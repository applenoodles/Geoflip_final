"""GeoFlip 的資料模型。

這個檔案只定義「資料長什麼樣」與一些純粹的計算（分數、回合判斷）。
不碰 Flask、不碰 HTTP、不碰 Folium，所有 I/O 都由其他層處理。

對教授說明：GameState 是整場遊戲唯一的真相來源。Flask 每次請求會讀進 GameState、
丟給 RulesEngine 算出新 GameState，再由 StateStore 寫回 data/state.json。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PlayerId = Literal[1, 2]
GameStatus = Literal["active", "finished"]
# move_kind 用來分辨同一個 MoveRecord 結構承載的不同行為：
#   opening : 開局前 2 手佈子，免 source、不畫路線
#   route   : 正常回合成功但沒翻到對手
#   flip    : 正常回合成功且翻了 ≥ 1 個對手
#   pass    : 玩家跳過，消耗一回合
MoveKind = Literal["opening", "route", "flip", "pass"]

_VALID_STATUSES = {"active", "finished"}
_VALID_MOVE_KINDS = {"opening", "route", "flip", "pass"}

# 每位玩家開局先佈 2 顆棋子（黑白棋四子佈局的靈感）。
# 改這個數字會直接影響規則引擎與 UI 的提示文字。
OPENING_MOVES_PER_PLAYER = 2

# ---------------------------------------------------------------------------
# Score mapping
# ---------------------------------------------------------------------------

_SCORE_3_TOURISM = {"museum", "attraction", "gallery", "zoo", "theme_park"}
_SCORE_3_AMENITY = {"university", "hospital", "theatre", "arts_centre"}
_SCORE_3_LEISURE = {"park", "stadium"}

_SCORE_2_AMENITY = {
    "restaurant", "cafe", "bar", "fast_food", "library",
    "school", "college", "place_of_worship", "marketplace",
}
_SCORE_2_RAILWAY = {"station", "halt"}
_SCORE_2_TOURISM = {"hotel", "hostel", "viewpoint"}
_SCORE_2_LEISURE = {"garden", "playground", "sports_centre"}


def score_poi(category: str, poi_type: str) -> int:
    if category == "historic":
        return 3
    if category == "tourism" and poi_type in _SCORE_3_TOURISM:
        return 3
    if category == "amenity" and poi_type in _SCORE_3_AMENITY:
        return 3
    if category == "leisure" and poi_type in _SCORE_3_LEISURE:
        return 3

    if category == "amenity" and poi_type in _SCORE_2_AMENITY:
        return 2
    if category == "shop":
        return 2
    if category == "railway" and poi_type in _SCORE_2_RAILWAY:
        return 2
    if category == "public_transport":
        return 2
    if category == "tourism" and poi_type in _SCORE_2_TOURISM:
        return 2
    if category == "leisure" and poi_type in _SCORE_2_LEISURE:
        return 2

    return 1


def mmss(duration_s: float) -> tuple[int, int]:
    """Split a duration in seconds into (minutes, seconds)."""
    return divmod(int(duration_s), 60)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Poi:
    id: str
    name: str
    lat: float
    lon: float
    osm_type: str | None
    osm_id: int | None
    category: str
    poi_type: str
    score: int
    owner: PlayerId | None
    discovered_turn: int | None
    placed_turn: int | None
    raw: dict

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "osm_type": self.osm_type,
            "osm_id": self.osm_id,
            "category": self.category,
            "poi_type": self.poi_type,
            "score": self.score,
            "owner": self.owner,
            "discovered_turn": self.discovered_turn,
            "placed_turn": self.placed_turn,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Poi:
        return cls(
            id=d["id"],
            name=d["name"],
            lat=float(d["lat"]),
            lon=float(d["lon"]),
            osm_type=d.get("osm_type"),
            osm_id=d.get("osm_id"),
            category=d["category"],
            poi_type=d["poi_type"],
            score=int(d["score"]),
            owner=d.get("owner"),
            discovered_turn=d.get("discovered_turn"),
            placed_turn=d.get("placed_turn"),
            raw=d.get("raw", {}),
        )


@dataclass
class PlayerState:
    id: PlayerId
    name: str

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict) -> PlayerState:
        return cls(id=d["id"], name=d["name"])


@dataclass
class RouteRecord:
    id: str
    turn_index: int
    player_id: PlayerId
    from_poi_id: str
    to_poi_id: str
    coordinates_lonlat: list[list[float]]
    distance_m: float
    duration_s: float
    buffer_m: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "turn_index": self.turn_index,
            "player_id": self.player_id,
            "from_poi_id": self.from_poi_id,
            "to_poi_id": self.to_poi_id,
            "coordinates_lonlat": self.coordinates_lonlat,
            "distance_m": self.distance_m,
            "duration_s": self.duration_s,
            "buffer_m": self.buffer_m,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RouteRecord:
        return cls(
            id=d["id"],
            turn_index=int(d["turn_index"]),
            player_id=d["player_id"],
            from_poi_id=d["from_poi_id"],
            to_poi_id=d["to_poi_id"],
            coordinates_lonlat=d["coordinates_lonlat"],
            distance_m=float(d["distance_m"]),
            duration_s=float(d["duration_s"]),
            buffer_m=float(d["buffer_m"]),
        )


@dataclass
class MoveRecord:
    """一手玩家行動的歷史紀錄（不可變、用來追溯）。

    四種 move_kind 共用同一份結構，差別在哪些欄位有值：
      - "opening": 開局佈子；只有 placed_poi_id，沒有 source、沒有 route
      - "route":   正常回合成功，target 被搶下但沒翻到對手
      - "flip":    正常回合成功，target + 至少一個對手 POI 被翻面
      - "pass":    玩家跳過；所有 *_poi_id 都是 None
    """
    turn_index: int
    player_id: PlayerId
    move_kind: MoveKind
    placed_poi_id: str | None
    source_poi_id: str | None
    route_ids: list[str]
    flipped_poi_ids: list[str]

    def to_dict(self) -> dict:
        return {
            "turn_index": self.turn_index,
            "player_id": self.player_id,
            "move_kind": self.move_kind,
            "placed_poi_id": self.placed_poi_id,
            "source_poi_id": self.source_poi_id,
            "route_ids": list(self.route_ids),
            "flipped_poi_ids": list(self.flipped_poi_ids),
        }

    @classmethod
    def from_dict(cls, d: dict) -> MoveRecord:
        kind = d.get("move_kind", "opening")
        # 向後相容：改名前的 state.json 用 "seed"，自動映射到新名稱
        # 才不會讓使用者必須手動刪 data/state.json。
        if kind == "seed":
            kind = "opening"
        if kind not in _VALID_MOVE_KINDS:
            raise ValueError(f"Invalid MoveRecord.move_kind: {kind!r}")
        return cls(
            turn_index=int(d["turn_index"]),
            player_id=d["player_id"],
            move_kind=kind,  # type: ignore[arg-type]
            placed_poi_id=d.get("placed_poi_id"),
            source_poi_id=d.get("source_poi_id"),
            route_ids=list(d.get("route_ids", [])),
            flipped_poi_ids=list(d.get("flipped_poi_ids", [])),
        )


@dataclass
class RouteResult:
    coordinates_lonlat: list[list[float]]
    distance_m: float
    duration_s: float


@dataclass
class MoveResult:
    ok: bool
    message: str
    state: GameState
    placed_poi_id: str | None
    flipped_poi_ids: list[str]
    route_ids: list[str]


# ---------------------------------------------------------------------------
# GameState
# ---------------------------------------------------------------------------

@dataclass
class GameState:
    game_id: str
    turn_index: int
    max_turns: int
    players: dict[PlayerId, PlayerState]
    pois: list[Poi]
    routes: list[RouteRecord]
    moves: list[MoveRecord]
    created_at: str
    updated_at: str
    status: GameStatus

    # --- turn / finish ---

    def current_player_id(self) -> PlayerId:
        return 1 if self.turn_index % 2 == 0 else 2

    def is_finished(self) -> bool:
        return self.status == "finished" or self.turn_index >= self.max_turns

    # --- player helpers ---

    def opponent_id(self, player_id: PlayerId) -> PlayerId:
        return 2 if player_id == 1 else 1

    # --- POI queries ---

    def owned_pois(self, player_id: PlayerId) -> list[Poi]:
        return [p for p in self.pois if p.owner == player_id]

    def neutral_pois(self) -> list[Poi]:
        return [p for p in self.pois if p.owner is None]

    def get_poi(self, poi_id: str) -> Poi | None:
        for p in self.pois:
            if p.id == poi_id:
                return p
        return None

    # --- scoring ---

    def scores(self) -> dict[PlayerId, int]:
        # 即時依目前 POI owner 重算，不存 cached score；翻面後分數自然會跟上。
        result: dict[PlayerId, int] = {1: 0, 2: 0}
        for poi in self.pois:
            if poi.owner in result:
                result[poi.owner] += poi.score
        return result

    def winner(self) -> PlayerId | None:
        if not self.is_finished():
            return None
        sc = self.scores()
        if sc[1] > sc[2]:
            return 1
        if sc[2] > sc[1]:
            return 2
        return None

    # --- move history helpers ---

    def player_move_count(self, player_id: PlayerId) -> int:
        return sum(1 for m in self.moves if m.player_id == player_id)

    def in_opening_phase(
        self,
        player_id: PlayerId,
        opening_moves_per_player: int = OPENING_MOVES_PER_PLAYER,
    ) -> bool:
        return self.player_move_count(player_id) < opening_moves_per_player

    def last_two_moves_are_passes(self) -> bool:
        if len(self.moves) < 2:
            return False
        return all(m.move_kind == "pass" for m in self.moves[-2:])

    # --- merge ---

    def merge_discovered_pois(self, pois: list[Poi]) -> None:
        # Setup 階段把 Overpass 抓到的 POI 灌進來；舊 POI 的 owner / placed_turn
        # 絕對不能被新 fetch 覆蓋掉，否則翻面結果會被洗掉。
        existing_ids = {p.id for p in self.pois}
        for new_poi in pois:
            if new_poi.id not in existing_ids:
                new_poi.discovered_turn = self.turn_index
                new_poi.owner = None
                new_poi.placed_turn = None
                self.pois.append(new_poi)
                existing_ids.add(new_poi.id)

    # --- serialization ---

    def to_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "turn_index": self.turn_index,
            "max_turns": self.max_turns,
            "players": {str(k): v.to_dict() for k, v in self.players.items()},
            "pois": [p.to_dict() for p in self.pois],
            "routes": [r.to_dict() for r in self.routes],
            "moves": [m.to_dict() for m in self.moves],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GameState:
        status = d.get("status", "")
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid GameState status: {status!r}")

        players: dict[PlayerId, PlayerState] = {}
        for k, v in d["players"].items():
            pid: PlayerId = int(k)  # type: ignore[assignment]
            players[pid] = PlayerState.from_dict(v)

        return cls(
            game_id=d["game_id"],
            turn_index=int(d["turn_index"]),
            max_turns=int(d["max_turns"]),
            players=players,
            pois=[Poi.from_dict(p) for p in d.get("pois", [])],
            routes=[RouteRecord.from_dict(r) for r in d.get("routes", [])],
            moves=[MoveRecord.from_dict(m) for m in d.get("moves", [])],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            status=status,  # type: ignore[arg-type]
        )
