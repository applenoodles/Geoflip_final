# =====================================================================
# Overpass：一次抓「某個中心點附近一堆地點(POI)」來組成棋盤。
#
# Nominatim 是「找一個中心點」，Overpass 則是「抓中心點周圍的一票地點」。
# 我們只挑有趣、適合當棋子的種類（餐廳、公園、車站、古蹟…）。
#
# 用一個 class（OverpassClient）包起來。下面那幾個底線開頭的函式
# 是「整理資料」用的小工具，class 的方法會呼叫它們。
# =====================================================================

import math

import requests

from app.config import OVERPASS_BASE_URL, OVERPASS_TIMEOUT_SECONDS
from app.models import Poi


# ---------------------------------------------------------------------
# 我們接受的地點種類。每組是 (大分類, 允許的小種類)。
# 小種類是 None 代表「任何小種類都接受」（只用在古蹟，且要求有名字）。
# ---------------------------------------------------------------------
_TAG_PROFILE = [
    ("amenity", ("cafe", "restaurant", "fast_food", "bar", "library",
                 "school", "college", "university", "hospital",
                 "theatre", "arts_centre", "place_of_worship", "marketplace")),
    ("tourism", ("museum", "attraction", "gallery", "hotel", "hostel",
                 "viewpoint", "zoo", "theme_park")),
    ("leisure", ("park", "garden", "playground", "sports_centre", "stadium")),
    ("shop", ("convenience", "supermarket", "books", "mall",
              "department_store", "bakery")),
    ("railway", ("station", "halt")),
    ("public_transport", ("station",)),
    ("historic", None),
]

# 找名字的順序（中文名優先）
_NAME_KEYS = ("name:zh-TW", "name:zh", "name", "name:en")

# 沒名字的地點，給一個中文種類名稱當顯示（不要顯示英文 tag）
_CN_TYPE_NAMES = {
    ("amenity", "cafe"): "咖啡廳", ("amenity", "restaurant"): "餐廳",
    ("amenity", "fast_food"): "速食店", ("amenity", "bar"): "酒吧",
    ("amenity", "library"): "圖書館", ("amenity", "school"): "學校",
    ("amenity", "college"): "學院", ("amenity", "university"): "大學",
    ("amenity", "hospital"): "醫院", ("amenity", "theatre"): "劇院",
    ("amenity", "arts_centre"): "藝文中心", ("amenity", "place_of_worship"): "宗教場所",
    ("amenity", "marketplace"): "市集",
    ("tourism", "museum"): "博物館", ("tourism", "attraction"): "景點",
    ("tourism", "gallery"): "美術館", ("tourism", "hotel"): "飯店",
    ("tourism", "hostel"): "青年旅館", ("tourism", "viewpoint"): "觀景點",
    ("tourism", "zoo"): "動物園", ("tourism", "theme_park"): "主題樂園",
    ("leisure", "park"): "公園", ("leisure", "garden"): "花園",
    ("leisure", "playground"): "兒童遊樂場", ("leisure", "sports_centre"): "運動中心",
    ("leisure", "stadium"): "體育場",
    ("shop", "convenience"): "便利商店", ("shop", "supermarket"): "超市",
    ("shop", "books"): "書店", ("shop", "mall"): "購物中心",
    ("shop", "department_store"): "百貨公司", ("shop", "bakery"): "麵包店",
    ("railway", "station"): "火車站", ("railway", "halt"): "招呼站",
    ("public_transport", "station"): "車站",
}

# 連小種類都查不到時，用大分類的中文名稱
_CN_CATEGORY_FALLBACK = {
    "amenity": "設施", "tourism": "觀光景點", "leisure": "休閒場所",
    "shop": "商店", "railway": "鐵路站", "public_transport": "交通站",
    "historic": "古蹟", "custom": "自訂點",
}

# raw 裡只保留這些 tag（免得存檔太肥）
_RAW_TAG_WHITELIST = (
    "name", "name:zh", "name:zh-TW", "name:en",
    "website", "wikidata", "wikipedia", "opening_hours", "phone",
)


def _pick_category(tags):
    """看這個地點符合哪個種類，回傳 (大分類, 小種類)；都不符合回 None。"""
    for category, allowed in _TAG_PROFILE:
        value = tags.get(category)
        if not value or not isinstance(value, str):
            continue
        if allowed is None:
            # 古蹟：任何小種類都收，但一定要有名字
            has_name = any(tags.get(k) for k in _NAME_KEYS)
            if not has_name:
                return None
            return category, value
        if value in allowed:
            return category, value
    return None


def _pick_name(tags, category, poi_type):
    """找地點的名字；沒名字就用中文種類名稱代替。"""
    for key in _NAME_KEYS:
        if tags.get(key):
            return tags[key]
    return (_CN_TYPE_NAMES.get((category, poi_type))
            or _CN_CATEGORY_FALLBACK.get(category)
            or "未命名地點")


def _build_raw(tags, category):
    """只挑白名單裡的 tag 留下來。"""
    raw = {}
    for k in _RAW_TAG_WHITELIST:
        if k in tags:
            raw[k] = tags[k]
    if category in tags:
        raw[category] = tags[category]
    return raw


def _element_coords(element):
    """取出座標；point 直接有 lat/lon，面/線放在 center 裡。"""
    if element.get("type") == "node":
        lat, lon = element.get("lat"), element.get("lon")
    else:
        center = element.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def _normalize(element):
    """把 Overpass 的一筆資料轉成一個 Poi 物件；不合用回 None。"""
    if element.get("type") not in ("node", "way", "relation"):
        return None
    tags = element.get("tags")
    if not isinstance(tags, dict) or not tags:
        return None

    picked = _pick_category(tags)
    if picked is None:
        return None
    category, poi_type = picked

    coords = _element_coords(element)
    if coords is None:
        return None
    lat, lon = coords

    if element.get("id") is None:
        return None
    osm_id = int(element["id"])
    elem_type = element["type"]
    name = _pick_name(tags, category, poi_type)

    return Poi.from_api(
        id=elem_type + ":" + str(osm_id), name=name, lat=lat, lon=lon,
        osm_type=elem_type, osm_id=osm_id, category=category, poi_type=poi_type,
        raw=_build_raw(tags, category),
    )


def _too_close(poi, selected, min_spacing_m):
    """poi 是否離「已選的某個點」太近（小於 min_spacing_m 公尺）？"""
    for sel in selected:
        # 把經緯度差換算成大概的公尺差（小範圍內夠準）
        dlat = (poi.lat - sel.lat) * 111_000
        dlon = (poi.lon - sel.lon) * 111_000 * math.cos(math.radians(poi.lat))
        if math.sqrt(dlat ** 2 + dlon ** 2) < min_spacing_m:
            return True
    return False


def _spread_filter(pois, min_spacing_m):
    """只保留彼此距離夠遠的點（高分點先選，所以高分會贏）。"""
    selected = []
    for poi in pois:
        if not _too_close(poi, selected, min_spacing_m):
            selected.append(poi)
    return selected


def _build_query(center_lat, center_lon, radius_m, limit):
    """組出 Overpass 要的查詢字串（它有自己的查詢語法）。"""
    rad = int(round(radius_m))
    lines = []
    for category, allowed in _TAG_PROFILE:
        if allowed is None:
            lines.append('  nwr["' + category + '"]["name"]'
                         + "(around:" + str(rad) + "," + str(center_lat) + "," + str(center_lon) + ");")
        else:
            alt = "|".join(sorted(allowed))   # cafe|restaurant|...
            lines.append('  nwr["' + category + '"~"^(' + alt + ')$"]'
                         + "(around:" + str(rad) + "," + str(center_lat) + "," + str(center_lon) + ");")
    body = "\n".join(lines)
    return ("[out:json][timeout:25];\n(\n" + body + "\n);\nout center " + str(int(limit)) + ";\n")


class OverpassClient:
    def __init__(self, base_url=OVERPASS_BASE_URL,
                 timeout=OVERPASS_TIMEOUT_SECONDS, min_spacing_m=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.min_spacing_m = min_spacing_m

    def fetch_board_pois(self, center_lat, center_lon, radius_m, limit=60):
        """抓中心點附近的 POI，整理後回傳一個 Poi 物件清單。

        失敗（沒網路、逾時、回應壞掉…）→ raise Exception。
        """
        query = _build_query(center_lat, center_lon, radius_m, limit)
        url = self.base_url + "/api/interpreter"

        try:
            resp = requests.post(url, data={"data": query},
                                 headers={"User-Agent": "geoflip-coursework/0.1"},
                                 timeout=self.timeout, verify=False)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            raise Exception("Overpass 連線失敗：" + str(exc))

        elements = payload.get("elements")
        if not isinstance(elements, list):
            raise Exception("Overpass 回應格式不對")

        # 一筆筆整理成 Poi，順便去掉重複的 id
        pois = []
        seen_ids = []
        for element in elements:
            if not isinstance(element, dict):
                continue
            poi = _normalize(element)
            if poi is None or poi.id in seen_ids:
                continue
            seen_ids.append(poi.id)
            pois.append(poi)

        # 分數高的排前面（間距過濾時高分點會優先被留下）
        pois = sorted(pois, key=lambda poi: (-poi.score, poi.id))

        # 把太靠近的點去掉，讓棋盤分散
        pois = _spread_filter(pois, self.min_spacing_m)

        # 最多只留 limit 個
        return pois[:limit]
