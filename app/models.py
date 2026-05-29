# =====================================================================
# 遊戲的「資料」與「對資料的小計算」都放在這裡。
#
# 整場遊戲只有一份資料，叫 state（state = 狀態）。
# state 就是一個大字典，裡面裝著：回合數、所有 POI、所有路線、歷史紀錄…
# 這個檔案不碰網路、不碰 Flask、不碰地圖，只負責「資料長什麼樣」跟簡單算數。
#
# 一個 POI（地點）長這樣（也是一個字典）：
#   {
#     "id": "node:123",        # 這個地點的唯一編號
#     "name": "新竹車站",
#     "lat": 24.80, "lon": 120.97,
#     "category": "railway", "poi_type": "station",
#     "score": 2,              # 佔領它可以得幾分
#     "owner": None,           # 屬於誰：None=中立、1=玩家1、2=玩家2
#     ...
#   }
# =====================================================================

import uuid
from datetime import datetime, timezone

from app.config import GAME_OPENING_MOVES_PER_PLAYER


# ---------------------------------------------------------------------
# 分數表：依照地點的種類決定它值幾分（3 分最高、1 分最低）
# ---------------------------------------------------------------------

def score_poi(category, poi_type):
    """給一個地點的種類，回傳它的分數（1、2 或 3）。"""
    # --- 3 分：博物館、大學、公園這類重要地標 ---
    if category == "historic":
        return 3
    if category == "tourism" and poi_type in ("museum", "attraction", "gallery", "zoo", "theme_park"):
        return 3
    if category == "amenity" and poi_type in ("university", "hospital", "theatre", "arts_centre"):
        return 3
    if category == "leisure" and poi_type in ("park", "stadium"):
        return 3

    # --- 2 分：餐廳、車站、商店這類中等地點 ---
    if category == "amenity" and poi_type in ("restaurant", "cafe", "bar", "fast_food",
                                              "library", "school", "college",
                                              "place_of_worship", "marketplace"):
        return 2
    if category == "shop":
        return 2
    if category == "railway" and poi_type in ("station", "halt"):
        return 2
    if category == "public_transport":
        return 2
    if category == "tourism" and poi_type in ("hotel", "hostel", "viewpoint"):
        return 2
    if category == "leisure" and poi_type in ("garden", "playground", "sports_centre"):
        return 2

    # --- 其他通通 1 分 ---
    return 1


def mmss(duration_s):
    """把「秒數」拆成 (分, 秒)。例如 130 秒 → (2, 10)。"""
    total = int(duration_s)
    minutes = total // 60     # 整除取得分鐘
    seconds = total % 60      # 取餘數得到剩下的秒
    return minutes, seconds


# ---------------------------------------------------------------------
# 建立資料（回傳字典）
# ---------------------------------------------------------------------

def _now_iso():
    """現在的時間，轉成文字（存進存檔用）。"""
    return datetime.now(timezone.utc).isoformat()


def make_poi(poi_id, name, lat, lon, osm_type, osm_id, category, poi_type, raw):
    """組出一個「中立 POI」字典。Nominatim / Overpass 抓到地點時呼叫它。"""
    return {
        "id": poi_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "osm_type": osm_type,
        "osm_id": osm_id,
        "category": category,
        "poi_type": poi_type,
        "score": score_poi(category, poi_type),
        "owner": None,            # 一開始都是中立
        "discovered_turn": None,
        "placed_turn": None,
        "raw": raw,
    }


def new_game(max_turns=20):
    """開一場全新的遊戲，回傳一個空白的 state 字典。"""
    now = _now_iso()
    return {
        "game_id": "game_" + uuid.uuid4().hex,   # 隨機產生不重複的編號
        "turn_index": 0,                         # 第幾回合（從 0 開始算）
        "max_turns": max_turns,
        "players": {
            "1": {"id": 1, "name": "Player 1"},
            "2": {"id": 2, "name": "Player 2"},
        },
        "pois": [],       # 棋盤上所有地點（之後 Overpass 抓進來）
        "routes": [],     # 所有畫過的路線
        "moves": [],      # 每一手的歷史紀錄
        "created_at": now,
        "updated_at": now,
        "status": "active",   # active=進行中、finished=已結束
    }


# ---------------------------------------------------------------------
# 讀 state 的小工具（全部是「給 state，回傳算好的答案」）
# ---------------------------------------------------------------------

def current_player_id(state):
    """現在輪到誰？偶數回合是玩家1，奇數回合是玩家2。"""
    if state["turn_index"] % 2 == 0:
        return 1
    return 2


def opponent_id(player_id):
    """給一個玩家，回傳他的對手。"""
    if player_id == 1:
        return 2
    return 1


def is_finished(state):
    """遊戲結束了嗎？status 已標記結束，或回合數到上限都算結束。"""
    return state["status"] == "finished" or state["turn_index"] >= state["max_turns"]


def get_poi(state, poi_id):
    """用 id 找出某個 POI 字典；找不到回傳 None。"""
    for poi in state["pois"]:
        if poi["id"] == poi_id:
            return poi
    return None


def owned_pois(state, player_id):
    """回傳某玩家擁有的所有 POI。"""
    return [poi for poi in state["pois"] if poi["owner"] == player_id]


def neutral_pois(state):
    """回傳目前還是中立（沒人佔）的所有 POI。"""
    return [poi for poi in state["pois"] if poi["owner"] is None]


def scores(state):
    """即時算出兩位玩家的分數（把自己擁有的 POI 分數全加起來）。"""
    result = {1: 0, 2: 0}
    for poi in state["pois"]:
        if poi["owner"] == 1:
            result[1] += poi["score"]
        elif poi["owner"] == 2:
            result[2] += poi["score"]
    return result


def winner(state):
    """遊戲結束時誰贏？分數高的贏，一樣高就回 None（平手）。"""
    if not is_finished(state):
        return None
    sc = scores(state)
    if sc[1] > sc[2]:
        return 1
    if sc[2] > sc[1]:
        return 2
    return None


def player_move_count(state, player_id):
    """數一數某玩家到目前為止下了幾手。"""
    count = 0
    for move in state["moves"]:
        if move["player_id"] == player_id:
            count += 1
    return count


def in_opening_phase(state, player_id, opening_moves=GAME_OPENING_MOVES_PER_PLAYER):
    """這位玩家還在「開局佈子」階段嗎？（前幾手算開局）"""
    return player_move_count(state, player_id) < opening_moves


def last_two_moves_are_passes(state):
    """最後兩手是不是都「跳過」？（連兩次跳過要結束遊戲）"""
    if len(state["moves"]) < 2:
        return False
    last_two = state["moves"][-2:]
    return last_two[0]["move_kind"] == "pass" and last_two[1]["move_kind"] == "pass"


def merge_discovered_pois(state, pois):
    """把 Overpass 抓到的新 POI 加進棋盤。

    已經存在的 POI 不會被覆蓋（不然玩家翻過的面會被洗掉）。
    """
    existing_ids = [poi["id"] for poi in state["pois"]]
    for new_poi in pois:
        if new_poi["id"] not in existing_ids:
            new_poi["discovered_turn"] = state["turn_index"]
            new_poi["owner"] = None
            new_poi["placed_turn"] = None
            state["pois"].append(new_poi)
            existing_ids.append(new_poi["id"])
