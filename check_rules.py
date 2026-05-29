# =====================================================================
# 規則檢查腳本（精簡版，約 12 個重點檢查）
#
# 怎麼跑：   python check_rules.py
# 它會印出每一條 PASS / FAIL，最後印通過幾項。全部 PASS 就代表規則沒跑掉。
#
# 重點：這支「不連網路」。它做一個「假的 OSRM 物件」(FakeOsrm)，
#       它的 .route() 回傳固定路線，傳給規則引擎用，所以測試又快又穩。
#       （這也示範了 OOP：引擎只要拿到一個「會 .route()」的物件就能用，
#         不管那是真的 OsrmClient 還是這個假的。）
# =====================================================================

import sys
sys.stdout.reconfigure(encoding="utf-8")   # 讓 Windows 終端機能正確顯示中文

from app.models import GameState, Poi
from app.game.rules import RulesEngine
from app.services.geometry import build_meter_transformers

engine = RulesEngine()   # 用預設規則參數（600 秒、50 公尺、開局 2 子、阻斷門檻 2）


# --- 小工具：用 pyproj 算出「離目標 N 公尺」的精確座標 ---
T_LON, T_LAT = 120.97, 24.80
to_m, to_wgs = build_meter_transformers(T_LON, T_LAT)
tx, ty = to_m.transform(T_LON, T_LAT)


def at(dx, dy):
    """回傳一個 (經度, 緯度)，它離目標 (dx, dy) 公尺。"""
    return to_wgs.transform(tx + dx, ty + dy)


def poi_at(pid, dx, dy, owner, category="amenity", poi_type="cafe"):
    """做一個位在 (dx, dy) 公尺、屬於 owner 的 Poi 物件。"""
    lon, lat = at(dx, dy)
    p = Poi.from_api(pid, pid, lat, lon, "node", 1, category, poi_type, {})
    p.owner = owner
    return p


class FakeOsrm:
    """假的 OSRM：.route() 回傳一條從起點到終點的直線、固定秒數。"""
    def __init__(self, duration_s):
        self.duration_s = duration_s

    def route(self, from_lat, from_lon, to_lat, to_lon):
        return {
            "coordinates_lonlat": [[from_lon, from_lat], [to_lon, to_lat]],
            "distance_m": 400.0,
            "duration_s": self.duration_s,
        }


def normal_phase_state():
    """做一個「已過開局階段」的 state（雙方各下了 2 手，現在輪 P1）。"""
    s = GameState.new_game(max_turns=20)
    s.turn_index = 4   # 偶數 → 現在輪 P1
    for i in range(4):  # 補 4 筆開局紀錄，讓 player_move_count 認定已過開局
        s.moves.append({
            "turn_index": i, "player_id": (1 if i % 2 == 0 else 2),
            "move_kind": "opening", "placed_poi_id": "x", "source_poi_id": None,
            "route_ids": [], "flipped_poi_ids": [],
        })
    return s


# --- 計分小工具 ---
_results = []
def check(name, condition):
    _results.append(condition)
    print(("PASS" if condition else "FAIL"), "-", name)


# =====================================================================
# 開始檢查
# =====================================================================

# 規則2：開局階段 — 不用起點、目標翻成自己的、回合+1
s = GameState.new_game(max_turns=20)
s.pois = [poi_at("a", 0, 0, None), poi_at("b", 100, 0, None)]
res = engine.apply_move(s, "a", FakeOsrm(300))     # P1 開局，只給目標
check("開局：成功且目標翻給 P1", res["ok"] and res["state"].get_poi("a").owner == 1)
check("開局：回合數 +1", res["state"].turn_index == 1)
res = engine.apply_move(s, "b", FakeOsrm(300), source_poi_id="a")   # 開局硬給起點
check("開局：給了起點要被拒絕", not res["ok"])

# 規則4 + 5a：用「門檻=1」的引擎（等同原始規則：1 個中立點就擋）
strict = RulesEngine(block_neutral_count=1)
s = normal_phase_state()
s.pois = [
    poi_at("source", 400, 0, 1),       # P1 的起點
    poi_at("target", 0, 0, None),      # 中立目標
    poi_at("opp", 200, 10, 2),         # 走廊內的對手
    poi_at("blocker", 200, 20, None),  # 走廊內的中立點
]
ns = strict.apply_move(s, "target", FakeOsrm(300), source_poi_id="source")["state"]
check("正常：目標一定翻給自己", ns.get_poi("target").owner == 1)
check("阻斷(門檻1)：1 個中立點就擋住，對手沒被翻", ns.get_poi("opp").owner == 2)
check("阻斷(門檻1)：這手算 route（沒翻到人）", ns.moves[-1]["move_kind"] == "route")

# 可調門檻：用「門檻=2」的引擎 → 1 個中立點擋不住、2 個才擋
loose = RulesEngine(block_neutral_count=2)
s = normal_phase_state()
s.pois = [
    poi_at("source", 400, 0, 1), poi_at("target", 0, 0, None),
    poi_at("opp", 200, 10, 2),
    poi_at("blocker", 200, 20, None),     # 只有 1 個中立點
]
ns = loose.apply_move(s, "target", FakeOsrm(300), source_poi_id="source")["state"]
check("門檻2：只有 1 個中立點 → 擋不住，對手被翻", ns.get_poi("opp").owner == 1)

s = normal_phase_state()
s.pois = [
    poi_at("source", 400, 0, 1), poi_at("target", 0, 0, None),
    poi_at("opp", 200, 10, 2),
    poi_at("blk1", 200, 20, None), poi_at("blk2", 150, -15, None),  # 2 個中立點
]
ns = loose.apply_move(s, "target", FakeOsrm(300), source_poi_id="source")["state"]
check("門檻2：2 個中立點 → 擋住，對手沒被翻", ns.get_poi("opp").owner == 2)

# 規則5b：走廊內沒有中立點 → 對手全翻；自己的點不會被翻
s = normal_phase_state()
s.pois = [
    poi_at("source", 400, 0, 1),
    poi_at("target", 0, 0, None),
    poi_at("opp", 200, 10, 2),     # 走廊內對手 → 應翻
    poi_at("mine", 100, 5, 1),     # 走廊內自己的點 → 不翻、也不擋
    poi_at("far", 0, 500, 2),      # 太遠，不在走廊 → 不翻
]
ns = engine.apply_move(s, "target", FakeOsrm(300), source_poi_id="source")["state"]
check("翻面：走廊內對手翻成 P1", ns.get_poi("opp").owner == 1)
check("翻面：自己的點維持不變", ns.get_poi("mine").owner == 1)
check("翻面：走廊外的對手不受影響", ns.get_poi("far").owner == 2)
check("翻面：這手算 flip（有翻到人）", ns.moves[-1]["move_kind"] == "flip")

# 規則3：步行超過 600 秒 → 無效，且完全不改變 state
s = normal_phase_state()
s.pois = [poi_at("source", 400, 0, 1), poi_at("target", 0, 0, None)]
res = engine.apply_move(s, "target", FakeOsrm(700), source_poi_id="source")
check("超時：被判無效", not res["ok"])
check("超時：回合數沒前進", s.turn_index == 4)
check("超時：目標仍是中立", s.get_poi("target").owner is None)

# 規則6/7：連續兩次跳過 → 遊戲結束
s = normal_phase_state()
s.pois = [poi_at("a", 0, 0, None)]
r1 = engine.apply_pass(s)
r2 = engine.apply_pass(r1["state"])
check("跳過：兩次跳過後遊戲結束", r2["state"].is_finished())

# 規則9：分數即時依擁有者計算
s = GameState.new_game(max_turns=20)
s.pois = [
    poi_at("a", 0, 0, 1, "historic", "monument"),  # 3 分
    poi_at("b", 100, 0, 1, "amenity", "cafe"),     # 2 分
    poi_at("c", 200, 0, 2, "historic", "castle"),  # 3 分
]
sc = s.scores()
check("計分：P1 = 5、P2 = 3", sc[1] == 5 and sc[2] == 3)


# =====================================================================
print()
print("通過 %d / %d 項" % (sum(_results), len(_results)))
