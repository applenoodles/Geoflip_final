# =====================================================================
# 遊戲的「資料」用 class（類別）來表示。
#
# 這個檔案定義兩個 class：
#   Poi       —— 一個地點（名字、座標、分數、屬於誰…）
#   GameState —— 整場遊戲的狀態（所有 POI、回合數、歷史紀錄…）
#
# 為什麼用 class？
#   class 就是「把『資料』和『操作這份資料的函式』綁在一起」。
#   例如 GameState 不只存著所有 POI，還附帶 .scores()（算分數）、
#   .current_player_id()（現在輪誰）這些方法，要用時直接 state.scores() 就好。
#
# 名詞：
#   - 用 class 做出來的東西叫「物件(object)」，例如 state = GameState(...)。
#   - class 裡的函式叫「方法(method)」，第一個參數固定是 self（代表物件自己）。
#   - __init__ 是「建構子」：每次 GameState(...) 建立物件時自動執行，負責把欄位裝好。
#
# 這個檔案不碰網路、不碰 Flask、不碰地圖，只負責「資料長什麼樣」跟簡單算數。
# =====================================================================

import uuid
from datetime import datetime, timezone

from app.config import GAME_OPENING_MOVES_PER_PLAYER


# ---------------------------------------------------------------------
# 分數表：依地點種類決定它值幾分（3 分最高、1 分最低）
# 這是一個普通函式，不屬於任何 class。
# ---------------------------------------------------------------------

def score_poi(category, poi_type):
    """給一個地點的種類，回傳它的分數（1、2 或 3）。"""
    if category == "historic":
        return 3
    if category == "tourism" and poi_type in ("museum", "attraction", "gallery", "zoo", "theme_park"):
        return 3
    if category == "amenity" and poi_type in ("university", "hospital", "theatre", "arts_centre"):
        return 3
    if category == "leisure" and poi_type in ("park", "stadium"):
        return 3

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

    return 1


def mmss(duration_s):
    """把「秒數」拆成 (分, 秒)。例如 130 秒 → (2, 10)。"""
    total = int(duration_s)
    return total // 60, total % 60


def _now_iso():
    """現在的時間轉成文字（存進存檔用）。"""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------
# Poi：一個地點
# ---------------------------------------------------------------------

class Poi:
    def __init__(self, id, name, lat, lon, osm_type, osm_id,
                 category, poi_type, score,
                 owner=None, discovered_turn=None, placed_turn=None, raw=None):
        # self.xxx = xxx 的意思是：把傳進來的值，存到「這個物件」身上。
        self.id = id
        self.name = name
        self.lat = lat
        self.lon = lon
        self.osm_type = osm_type
        self.osm_id = osm_id
        self.category = category
        self.poi_type = poi_type
        self.score = score
        self.owner = owner                  # None=中立、1=玩家1、2=玩家2
        self.discovered_turn = discovered_turn
        self.placed_turn = placed_turn
        self.raw = raw if raw is not None else {}

    @classmethod
    def from_api(cls, id, name, lat, lon, osm_type, osm_id, category, poi_type, raw):
        """從 API 抓到的資料做出一個「中立 Poi」，分數自動算好。
        @classmethod 代表這是「用整個類別呼叫」的方法：Poi.from_api(...)。"""
        return cls(id, name, lat, lon, osm_type, osm_id,
                   category, poi_type, score_poi(category, poi_type),
                   owner=None, raw=raw)

    def to_dict(self):
        """把這個 Poi 物件變成普通字典（要存成 JSON 時用）。"""
        return {
            "id": self.id, "name": self.name, "lat": self.lat, "lon": self.lon,
            "osm_type": self.osm_type, "osm_id": self.osm_id,
            "category": self.category, "poi_type": self.poi_type, "score": self.score,
            "owner": self.owner, "discovered_turn": self.discovered_turn,
            "placed_turn": self.placed_turn, "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, d):
        """從字典（讀存檔得到的）做回一個 Poi 物件。"""
        return cls(
            id=d["id"], name=d["name"], lat=float(d["lat"]), lon=float(d["lon"]),
            osm_type=d.get("osm_type"), osm_id=d.get("osm_id"),
            category=d["category"], poi_type=d["poi_type"], score=int(d["score"]),
            owner=d.get("owner"), discovered_turn=d.get("discovered_turn"),
            placed_turn=d.get("placed_turn"), raw=d.get("raw", {}),
        )


# ---------------------------------------------------------------------
# GameState：整場遊戲的狀態
# 路線(routes)和歷史(moves)只是純紀錄，直接用「字典的串列」存，不另外做 class。
# ---------------------------------------------------------------------

class GameState:
    def __init__(self, game_id, turn_index, max_turns, players,
                 pois, routes, moves, created_at, updated_at, status):
        self.game_id = game_id
        self.turn_index = turn_index        # 第幾回合（從 0 算）
        self.max_turns = max_turns
        self.players = players
        self.pois = pois                    # list of Poi 物件
        self.routes = routes                # list of dict
        self.moves = moves                  # list of dict
        self.created_at = created_at
        self.updated_at = updated_at
        self.status = status                # "active" 或 "finished"

    @classmethod
    def new_game(cls, max_turns=20):
        """開一場全新的遊戲（空白棋盤）。"""
        now = _now_iso()
        return cls(
            game_id="game_" + uuid.uuid4().hex,   # 隨機產生不重複的編號
            turn_index=0, max_turns=max_turns,
            players={"1": {"id": 1, "name": "Player 1"},
                     "2": {"id": 2, "name": "Player 2"}},
            pois=[], routes=[], moves=[],
            created_at=now, updated_at=now, status="active",
        )

    # ----- 回合 / 結束 -----

    def current_player_id(self):
        """現在輪到誰？偶數回合 P1、奇數回合 P2。"""
        return 1 if self.turn_index % 2 == 0 else 2

    def is_finished(self):
        """遊戲結束了嗎？"""
        return self.status == "finished" or self.turn_index >= self.max_turns

    # ----- 找 POI -----

    def get_poi(self, poi_id):
        """用 id 找出某個 Poi 物件；找不到回 None。"""
        for poi in self.pois:
            if poi.id == poi_id:
                return poi
        return None

    def owned_pois(self, player_id):
        """某玩家擁有的所有 POI。"""
        return [poi for poi in self.pois if poi.owner == player_id]

    def neutral_pois(self):
        """目前還是中立的所有 POI。"""
        return [poi for poi in self.pois if poi.owner is None]

    # ----- 計分 -----

    def scores(self):
        """即時把兩位玩家擁有的 POI 分數加總。"""
        result = {1: 0, 2: 0}
        for poi in self.pois:
            if poi.owner == 1:
                result[1] += poi.score
            elif poi.owner == 2:
                result[2] += poi.score
        return result

    def winner(self):
        """結束時誰贏？分數高的贏，一樣高回 None（平手）。"""
        if not self.is_finished():
            return None
        sc = self.scores()
        if sc[1] > sc[2]:
            return 1
        if sc[2] > sc[1]:
            return 2
        return None

    # ----- 歷史紀錄 -----

    def player_move_count(self, player_id):
        """某玩家到目前下了幾手。"""
        return len([m for m in self.moves if m["player_id"] == player_id])

    def in_opening_phase(self, player_id, opening_moves=GAME_OPENING_MOVES_PER_PLAYER):
        """這位玩家還在開局佈子階段嗎？"""
        return self.player_move_count(player_id) < opening_moves

    def last_two_moves_are_passes(self):
        """最後兩手是不是都「跳過」？"""
        if len(self.moves) < 2:
            return False
        last_two = self.moves[-2:]
        return last_two[0]["move_kind"] == "pass" and last_two[1]["move_kind"] == "pass"

    # ----- 把 Overpass 抓到的 POI 灌進棋盤 -----

    def merge_discovered_pois(self, pois):
        """加入新 POI；已存在的不覆蓋（不然翻過的面會被洗掉）。"""
        existing_ids = [poi.id for poi in self.pois]
        for new_poi in pois:
            if new_poi.id not in existing_ids:
                new_poi.discovered_turn = self.turn_index
                new_poi.owner = None
                new_poi.placed_turn = None
                self.pois.append(new_poi)
                existing_ids.append(new_poi.id)

    # ----- 存檔 / 讀檔用的轉換 -----

    def to_dict(self):
        """把整個 GameState 變成普通字典（要存成 JSON 時用）。"""
        return {
            "game_id": self.game_id, "turn_index": self.turn_index,
            "max_turns": self.max_turns, "players": self.players,
            "pois": [poi.to_dict() for poi in self.pois],   # 每個 Poi 都轉成字典
            "routes": self.routes, "moves": self.moves,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d):
        """從字典（讀存檔得到的）做回一個 GameState 物件。"""
        return cls(
            game_id=d["game_id"], turn_index=int(d["turn_index"]),
            max_turns=int(d["max_turns"]), players=d["players"],
            pois=[Poi.from_dict(p) for p in d.get("pois", [])],   # 每個字典都做回 Poi
            routes=d.get("routes", []), moves=d.get("moves", []),
            created_at=d["created_at"], updated_at=d["updated_at"],
            status=d["status"],
        )
