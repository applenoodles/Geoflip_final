"""GeoFlip 核心規則引擎 —— 整個遊戲的判決中心。

設計原則：
  RulesEngine 只做純粹的規則判斷與計算。它不讀 Flask request、不寫 JSON、
  也不直接呼叫 OSRM HTTP，而是接受一個 routing_service 介面（依賴反轉），
  所以測試時可以塞 FakeRoutingService 完全離線。

遊戲規則（對應企畫書 §2.3 ~ §2.7）：

  1. 開局階段：每位玩家前 OPENING_MOVES_PER_PLAYER 手只能在中立 POI 佈子。
     開局子不需 source、不呼叫 OSRM、不畫路線、不翻面，但會消耗一回合。
  2. 正常回合：玩家送 source（自己擁有的 POI）+ target（中立 POI）。
     後端呼叫 OSRM 算實際步行路線；duration > 600s 視為無效。
  3. Target 一定會被搶到當前玩家手上（target 必翻）。
  4. 路線 50m buffer 的「阻斷規則」：
        - buffer 內若有任何「中立」POI（排除 source/target 本身）→
          視為路線被擋住，這一手只翻 target，不翻沿途對手。
        - buffer 內若沒有中立 POI → buffer 內所有對手 POI 全部翻面。
        - 自己的 POI 不阻斷、也不會被反向翻面。
  5. apply_pass() 跳過一回合不改 owner；連續兩次 pass 直接結束遊戲。

Transaction-like 設計（apply_move / apply_pass 共用）：
  - 先用「未改變的 state」做完所有 validation。
  - 全部 check 通過之後才 deepcopy 一份新 state 來改。
  - 無效動作絕不留下任何副作用（不前進回合、不寫 RouteRecord、不翻面）。
  這樣測試與除錯都很乾淨：在原 state 上看不到任何半套修改。
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Protocol

from app.models import (
    GameState,
    MoveRecord,
    MoveResult,
    Poi,
    RouteRecord,
    RouteResult,
    OPENING_MOVES_PER_PLAYER,
    mmss,
)
from app.services.geometry import (
    build_meter_transformers,
    buffer_route_meters,
    point_in_buffer,
    route_to_meter_linestring,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_WALK_DURATION_S: float = 600.0
BUFFER_NORMAL_M: float = 50.0


class RoutingService(Protocol):
    """OSRM 抽象介面 —— 任何提供 .route(...) 的物件都能塞進來。

    生產環境用 OsrmClient（真的打 OSRM），測試環境用 FakeRoutingService
    （查表 / 抛例外）。RulesEngine 不知道、也不需要知道差別。
    """
    def route(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
    ) -> RouteResult: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _invalid(state: GameState, message: str) -> MoveResult:
    return MoveResult(
        ok=False,
        message=message,
        state=state,
        placed_poi_id=None,
        flipped_poi_ids=[],
        route_ids=[],
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RulesEngine:
    """Pure game-rules engine — no I/O, no Flask, no Folium."""

    def __init__(
        self,
        max_walk_duration_s: float = MAX_WALK_DURATION_S,
        buffer_normal_m: float = BUFFER_NORMAL_M,
        opening_moves_per_player: int = OPENING_MOVES_PER_PLAYER,
    ) -> None:
        self._max_walk_duration_s = max_walk_duration_s
        self._buffer_normal_m = buffer_normal_m
        self._opening_moves_per_player = opening_moves_per_player

    # ------------------------------------------------------------------

    def apply_move(
        self,
        state: GameState,
        target_poi_id: str,
        routing_service: RoutingService,
        *,
        source_poi_id: str | None = None,
    ) -> MoveResult:
        """落子的單一入口。開局階段忽略 source_poi_id；正常回合則必須帶。"""
        # ---- 通用驗證階段（不可改動 state） ----

        if state.is_finished():
            return _invalid(state, "遊戲已結束")

        current_player_id = state.current_player_id()
        target = state.get_poi(target_poi_id)
        if target is None:
            return _invalid(state, "目標 POI 不存在")
        if target.owner is not None:
            return _invalid(state, "該地點已被擁有，不可選為目標")

        in_opening_phase = state.in_opening_phase(
            current_player_id, self._opening_moves_per_player
        )

        if in_opening_phase:
            # 開局階段：不允許傳 source（避免玩家 / 前端誤用）。
            if source_poi_id:
                return _invalid(state, "開局階段不需要選擇起點")
            return self._commit_opening(state, target)

        # ---- 正常回合 ----

        if not source_poi_id:
            return _invalid(state, "請先選擇自己的起點 POI（source）")

        source = state.get_poi(source_poi_id)
        if source is None:
            return _invalid(state, "起點 POI 不存在")
        if source.owner != current_player_id:
            return _invalid(state, "起點必須是自己擁有的 POI")
        if source.id == target.id:
            return _invalid(state, "起點和目標不可相同")

        try:
            # 唯一一次呼叫 OSRM 的地方：source → target 步行路線。
            route_result = routing_service.route(
                source.lat, source.lon, target.lat, target.lon
            )
        except Exception as exc:
            # OSRM 失敗 / 沒路 / timeout 都算這手無效，但不會 crash 整個請求。
            return _invalid(state, f"找不到步行路線：{exc}")

        if route_result.duration_s > self._max_walk_duration_s:
            mins, secs = mmss(route_result.duration_s)
            return _invalid(
                state,
                f"步行 {int(route_result.distance_m)} 公尺 · {mins} 分 {secs} 秒，"
                f"超過 {int(self._max_walk_duration_s)} 秒上限，這一手無效",
            )

        return self._commit_normal(state, source, target, route_result)

    # ------------------------------------------------------------------

    def apply_pass(self, state: GameState) -> MoveResult:
        if state.is_finished():
            return _invalid(state, "遊戲已結束")

        current_player_id = state.current_player_id()

        new_state = deepcopy(state)
        new_state.moves.append(
            MoveRecord(
                turn_index=state.turn_index,
                player_id=current_player_id,
                move_kind="pass",
                placed_poi_id=None,
                source_poi_id=None,
                route_ids=[],
                flipped_poi_ids=[],
            )
        )
        new_state.turn_index += 1
        new_state.updated_at = _now_iso()
        self._maybe_finish(new_state)

        return MoveResult(
            ok=True,
            message="OK",
            state=new_state,
            placed_poi_id=None,
            flipped_poi_ids=[],
            route_ids=[],
        )

    # ------------------------------------------------------------------
    # Commit helpers
    # ------------------------------------------------------------------

    def _commit_opening(self, state: GameState, target: Poi) -> MoveResult:
        new_state = deepcopy(state)
        new_target = new_state.get_poi(target.id)
        assert new_target is not None

        current_player_id = new_state.current_player_id()
        new_target.owner = current_player_id
        new_target.placed_turn = state.turn_index

        new_state.moves.append(
            MoveRecord(
                turn_index=state.turn_index,
                player_id=current_player_id,
                move_kind="opening",
                placed_poi_id=new_target.id,
                source_poi_id=None,
                route_ids=[],
                flipped_poi_ids=[],
            )
        )
        new_state.turn_index += 1
        new_state.updated_at = _now_iso()
        self._maybe_finish(new_state)

        return MoveResult(
            ok=True,
            message="OK",
            state=new_state,
            placed_poi_id=new_target.id,
            flipped_poi_ids=[],
            route_ids=[],
        )

    def _commit_normal(
        self,
        state: GameState,
        source: Poi,
        target: Poi,
        route_result: RouteResult,
    ) -> MoveResult:
        new_state = deepcopy(state)
        new_target = new_state.get_poi(target.id)
        assert new_target is not None
        current_player_id = new_state.current_player_id()

        # 1. Target 必翻 —— 規則保證玩家一定有收穫，避免「白走一趟」的挫折感。
        new_target.owner = current_player_id
        new_target.placed_turn = state.turn_index

        # 2. 建立路線 50m buffer（在公尺座標系做，degree 直接 buffer 會錯幾十公里）。
        #    pyproj 投影 → Shapely LineString.buffer → point-in-polygon 檢查。
        buffer_m = self._buffer_normal_m
        to_m, _ = build_meter_transformers(new_target.lon, new_target.lat)
        line_m = route_to_meter_linestring(route_result.coordinates_lonlat, to_m)
        buffer_poly = buffer_route_meters(line_m, buffer_m)

        # source / target 本身不要被掃描：source 已經屬於自己，target 一定會翻。
        exclude_ids = {source.id, target.id}
        in_buffer: list[Poi] = [
            p for p in new_state.pois
            if p.id not in exclude_ids
            and point_in_buffer(p.lat, p.lon, buffer_poly, to_m)
        ]

        # 3. 阻斷規則：buffer 內若有任何中立 POI，整條路線視為被擋住，這手只翻 target。
        #    這給玩家一個策略空間 —— 找一條「乾淨」的路徑才能連續吃掉沿途對手。
        blocked = any(p.owner is None for p in in_buffer)
        flipped_poi_ids: list[str] = []
        if not blocked:
            opponent_id = new_state.opponent_id(current_player_id)
            for poi in in_buffer:
                if poi.owner == opponent_id:
                    poi.owner = current_player_id
                    flipped_poi_ids.append(poi.id)

        # 4. 不論有沒有翻面，正常回合都要存 RouteRecord —— 給地圖畫 polyline / buffer。
        route_record = RouteRecord(
            id="route_" + uuid.uuid4().hex,
            turn_index=state.turn_index,
            player_id=current_player_id,
            from_poi_id=source.id,
            to_poi_id=target.id,
            coordinates_lonlat=list(route_result.coordinates_lonlat),
            distance_m=float(route_result.distance_m),
            duration_s=float(route_result.duration_s),
            buffer_m=float(buffer_m),
        )
        new_state.routes.append(route_record)

        move_kind = "flip" if flipped_poi_ids else "route"
        new_state.moves.append(
            MoveRecord(
                turn_index=state.turn_index,
                player_id=current_player_id,
                move_kind=move_kind,  # type: ignore[arg-type]
                placed_poi_id=target.id,
                source_poi_id=source.id,
                route_ids=[route_record.id],
                flipped_poi_ids=list(flipped_poi_ids),
            )
        )
        new_state.turn_index += 1
        new_state.updated_at = _now_iso()
        self._maybe_finish(new_state)

        return MoveResult(
            ok=True,
            message="OK",
            state=new_state,
            placed_poi_id=target.id,
            flipped_poi_ids=flipped_poi_ids,
            route_ids=[route_record.id],
        )

    # ------------------------------------------------------------------

    def _maybe_finish(self, new_state: GameState) -> None:
        """三個結束條件：回合到上限 / 沒中立 POI 可下 / 連續兩 pass。"""
        if new_state.turn_index >= new_state.max_turns:
            new_state.status = "finished"
            return
        if not new_state.neutral_pois():
            new_state.status = "finished"
            return
        if new_state.last_two_moves_are_passes():
            new_state.status = "finished"
