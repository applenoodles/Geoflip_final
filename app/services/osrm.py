# =====================================================================
# OSRM：算「兩點之間的步行路線」與「要走幾秒」。
#
# 這是遊戲規則「步行超過 600 秒就無效」會用到的服務。
# 我們把起點、終點的經緯度組成一個網址丟給 OSRM，
# 它回我們一條路線（一串座標）以及距離、時間。
#
# 原本這裡是一個 class，還有快取、SSL 設定。現在只留一支函式。
# =====================================================================

import httpx

from app.config import OSRM_BASE_URL, OSRM_PROFILE, REQUEST_TIMEOUT_SECONDS


def osrm_route(from_lat, from_lon, to_lat, to_lon):
    """跟 OSRM 要一條步行路線。

    成功 → 回傳一個字典：
        {"coordinates_lonlat": [[經度, 緯度], ...],
         "distance_m": 距離公尺,
         "duration_s": 步行秒數}
    失敗（沒網路、找不到路、逾時…）→ raise 一個 Exception，
    呼叫它的規則引擎會把這一手當成無效。
    """
    # OSRM 的網址規定座標順序是「經度,緯度」
    url = (
        OSRM_BASE_URL.rstrip("/")
        + "/route/v1/" + OSRM_PROFILE + "/"
        + str(from_lon) + "," + str(from_lat) + ";"
        + str(to_lon) + "," + str(to_lat)
    )
    params = {
        "overview": "full",       # 要完整的路線座標
        "geometries": "geojson",  # 座標格式
        "steps": "false",
        "annotations": "false",
    }

    # try：嘗試連線並讀資料；except：只要出任何錯就改成丟一個清楚的訊息
    try:
        resp = httpx.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()   # 如果 HTTP 狀態是錯誤(如 404)就會在這裡出錯
        data = resp.json()        # 把回應的 JSON 轉成 Python 字典
    except Exception as exc:
        raise Exception("OSRM 連線失敗：" + str(exc))

    # OSRM 回應裡會有一個 code，"Ok" 才代表成功
    if data.get("code") != "Ok":
        raise Exception("找不到步行路線")

    routes = data.get("routes", [])
    if not routes:
        raise Exception("找不到步行路線")

    route = routes[0]
    coordinates = route.get("geometry", {}).get("coordinates", [])
    if len(coordinates) < 2:
        raise Exception("OSRM 回傳的路線點太少")

    return {
        # OSRM 給的座標已經是 [經度, 緯度]，直接沿用
        "coordinates_lonlat": coordinates,
        "distance_m": float(route["distance"]),
        "duration_s": float(route["duration"]),
    }
