# =====================================================================
# 遊戲規則引擎：整個遊戲的「裁判」。所有「這一手合不合法、會翻誰」都在這裡。
#
# 規則摘要：
#   1. 開局階段：每位玩家前 2 手只能在「中立 POI」佈子，不用起點、不畫路線、
#      不翻面，但會用掉一回合。
#   2. 正常回合：要選「起點(自己的 POI)」+「目標(中立 POI)」，
#      後端用 OSRM 算步行路線；步行超過 600 秒 → 無效。
#   3. 目標一定被翻成自己的。
#   4. 路線兩側 50 公尺的走廊：
#        - 走廊內若有任何「中立」POI → 路線被擋住，這手只翻目標。
#        - 走廊內沒有中立 POI → 走廊內所有「對手」POI 全部翻成自己的。
#        - 自己的 POI 不會擋路、也不會被翻。
#   5. 跳過(pass)：用掉一回合、不翻任何人；連續兩次跳過直接結束遊戲。
#
# 重要做法：先用「沒被改過的 state」做完所有檢查，全部通過後才
# deepcopy（整份複製）一份來改。這樣無效的一手絕對不會留下任何半套修改。
# =====================================================================

import uuid
from copy import deepcopy
from datetime import datetime, timezone

from app.config import (
    GAME_BUFFER_NORMAL_M,
    GAME_MAX_WALK_SECONDS,
    GAME_OPENING_MOVES_PER_PLAYER,
)
from app.models import (
    current_player_id,
    get_poi,
    in_opening_phase,
    is_finished,
    last_two_moves_are_passes,
    mmss,
    neutral_pois,
    opponent_id,
)
from app.services.geometry import (
    build_meter_transformers,
    buffer_route_meters,
    point_in_buffer,
    route_to_meter_linestring,
)
from app.services.osrm import osrm_route


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _invalid(state, message):
    """組出一個「無效」的結果（state 維持原樣，沒有任何改變）。"""
    return {
        "ok": False,
        "message": message,
        "state": state,
        "placed_poi_id": None,
        "flipped_poi_ids": [],
        "route_ids": [],
    }


def apply_move(state, target_poi_id, source_poi_id=None,
               max_walk_s=GAME_MAX_WALK_SECONDS,
               buffer_m=GAME_BUFFER_NORMAL_M,
               opening_moves=GAME_OPENING_MOVES_PER_PLAYER):
    """玩家落子的單一入口。回傳一個「結果字典」。

    開局階段只看 target_poi_id；正常回合則需要 source_poi_id。
    """
    # ---- 共同檢查（這個階段絕不改 state）----
    if is_finished(state):
        return _invalid(state, "遊戲已結束")

    cur = current_player_id(state)

    target = get_poi(state, target_poi_id)
    if target is None:
        return _invalid(state, "目標 POI 不存在")
    if target["owner"] is not None:
        return _invalid(state, "該地點已被擁有，不可選為目標")

    # ---- 開局階段 ----
    if in_opening_phase(state, cur, opening_moves):
        if source_poi_id:
            return _invalid(state, "開局階段不需要選擇起點")
        return _commit_opening(state, target)

    # ---- 正常回合 ----
    if not source_poi_id:
        return _invalid(state, "請先選擇自己的起點 POI（source）")

    source = get_poi(state, source_poi_id)
    if source is None:
        return _invalid(state, "起點 POI 不存在")
    if source["owner"] != cur:
        return _invalid(state, "起點必須是自己擁有的 POI")
    if source["id"] == target["id"]:
        return _invalid(state, "起點和目標不可相同")

    # 唯一一次呼叫 OSRM 的地方：算 起點 → 目標 的步行路線
    try:
        route_result = osrm_route(source["lat"], source["lon"],
                                  target["lat"], target["lon"])
    except Exception as exc:
        return _invalid(state, "找不到步行路線：" + str(exc))

    # 步行太久 → 這手無效
    if route_result["duration_s"] > max_walk_s:
        mins, secs = mmss(route_result["duration_s"])
        return _invalid(
            state,
            "步行 " + str(int(route_result["distance_m"])) + " 公尺 · "
            + str(mins) + " 分 " + str(secs) + " 秒，超過 "
            + str(int(max_walk_s)) + " 秒上限，這一手無效",
        )

    return _commit_normal(state, source, target, route_result, buffer_m)


def apply_pass(state):
    """跳過一回合。用掉一回合、不翻任何人。"""
    if is_finished(state):
        return _invalid(state, "遊戲已結束")

    cur = current_player_id(state)

    new_state = deepcopy(state)   # 整份複製一份再改，不動到原本的
    new_state["moves"].append({
        "turn_index": state["turn_index"],
        "player_id": cur,
        "move_kind": "pass",
        "placed_poi_id": None,
        "source_poi_id": None,
        "route_ids": [],
        "flipped_poi_ids": [],
    })
    new_state["turn_index"] += 1
    new_state["updated_at"] = _now_iso()
    _maybe_finish(new_state)

    return {
        "ok": True,
        "message": "OK",
        "state": new_state,
        "placed_poi_id": None,
        "flipped_poi_ids": [],
        "route_ids": [],
    }


# ---------------------------------------------------------------------
# 真正改 state 的兩個函式（都先 deepcopy 再動手）
# ---------------------------------------------------------------------

def _commit_opening(state, target):
    """開局佈子：把目標變成自己的，記一筆，回合 +1。"""
    new_state = deepcopy(state)
    cur = current_player_id(new_state)

    new_target = get_poi(new_state, target["id"])
    new_target["owner"] = cur
    new_target["placed_turn"] = state["turn_index"]

    new_state["moves"].append({
        "turn_index": state["turn_index"],
        "player_id": cur,
        "move_kind": "opening",
        "placed_poi_id": new_target["id"],
        "source_poi_id": None,
        "route_ids": [],
        "flipped_poi_ids": [],
    })
    new_state["turn_index"] += 1
    new_state["updated_at"] = _now_iso()
    _maybe_finish(new_state)

    return {
        "ok": True,
        "message": "OK",
        "state": new_state,
        "placed_poi_id": new_target["id"],
        "flipped_poi_ids": [],
        "route_ids": [],
    }


def _commit_normal(state, source, target, route_result, buffer_m):
    """正常回合：翻目標、算走廊、依阻斷規則翻對手、存路線。"""
    new_state = deepcopy(state)
    cur = current_player_id(new_state)

    new_target = get_poi(new_state, target["id"])

    # 1. 目標一定翻成自己的
    new_target["owner"] = cur
    new_target["placed_turn"] = state["turn_index"]

    # 2. 算出路線兩側的 50 公尺走廊（要先換成公尺座標再加寬）
    to_meters, _ = build_meter_transformers(new_target["lon"], new_target["lat"])
    line_m = route_to_meter_linestring(route_result["coordinates_lonlat"], to_meters)
    buffer_poly = buffer_route_meters(line_m, buffer_m)

    # 找出落在走廊裡的 POI（起點、目標本身不算）
    exclude_ids = (source["id"], target["id"])
    in_buffer = []
    for poi in new_state["pois"]:
        if poi["id"] in exclude_ids:
            continue
        if point_in_buffer(poi["lat"], poi["lon"], buffer_poly, to_meters):
            in_buffer.append(poi)

    # 3. 阻斷規則：走廊內只要有一個「中立」POI，整條路線就算被擋住
    blocked = False
    for poi in in_buffer:
        if poi["owner"] is None:
            blocked = True

    flipped_poi_ids = []
    if not blocked:
        opp = opponent_id(cur)
        for poi in in_buffer:
            if poi["owner"] == opp:
                poi["owner"] = cur
                flipped_poi_ids.append(poi["id"])

    # 4. 不管有沒有翻到對手，正常回合都要存一筆路線（給地圖畫線、畫走廊）
    route_record = {
        "id": "route_" + uuid.uuid4().hex,
        "turn_index": state["turn_index"],
        "player_id": cur,
        "from_poi_id": source["id"],
        "to_poi_id": target["id"],
        "coordinates_lonlat": list(route_result["coordinates_lonlat"]),
        "distance_m": float(route_result["distance_m"]),
        "duration_s": float(route_result["duration_s"]),
        "buffer_m": float(buffer_m),
    }
    new_state["routes"].append(route_record)

    if flipped_poi_ids:
        move_kind = "flip"     # 有翻到對手
    else:
        move_kind = "route"    # 只翻了目標
    new_state["moves"].append({
        "turn_index": state["turn_index"],
        "player_id": cur,
        "move_kind": move_kind,
        "placed_poi_id": target["id"],
        "source_poi_id": source["id"],
        "route_ids": [route_record["id"]],
        "flipped_poi_ids": list(flipped_poi_ids),
    })
    new_state["turn_index"] += 1
    new_state["updated_at"] = _now_iso()
    _maybe_finish(new_state)

    return {
        "ok": True,
        "message": "OK",
        "state": new_state,
        "placed_poi_id": target["id"],
        "flipped_poi_ids": flipped_poi_ids,
        "route_ids": [route_record["id"]],
    }


def _maybe_finish(new_state):
    """檢查三個結束條件，符合就把遊戲標記成 finished。"""
    if new_state["turn_index"] >= new_state["max_turns"]:
        new_state["status"] = "finished"
        return
    if not neutral_pois(new_state):     # 沒有中立 POI 可下了
        new_state["status"] = "finished"
        return
    if last_two_moves_are_passes(new_state):   # 連續兩次跳過
        new_state["status"] = "finished"
