# =====================================================================
# OSRM：算「兩點之間的步行路線」與「要走幾秒」。
#
# 這是遊戲規則「步行超過 600 秒就無效」會用到的服務。
# 用一個 class（OsrmClient）包起來：建立物件時記住伺服器網址，
# 之後呼叫 .route(...) 方法就能算路線。
#
# 用 requests 連網（課堂教的套件）。原版的快取、SSL 設定都拿掉了。
# =====================================================================

import requests

from app.config import OSRM_BASE_URL, OSRM_PROFILE, REQUEST_TIMEOUT_SECONDS


class OsrmClient:
    def __init__(self, base_url=OSRM_BASE_URL, profile=OSRM_PROFILE,
                 timeout=REQUEST_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.profile = profile          # foot = 走路
        self.timeout = timeout

    def route(self, from_lat, from_lon, to_lat, to_lon):
        """跟 OSRM 要一條步行路線。

        成功 → 回傳字典：
            {"coordinates_lonlat": [[經度,緯度], ...],
             "distance_m": 距離公尺, "duration_s": 步行秒數}
        失敗（沒網路、找不到路…）→ raise Exception。
        """
        # OSRM 網址規定座標順序是「經度,緯度」
        url = (self.base_url + "/route/v1/" + self.profile + "/"
               + str(from_lon) + "," + str(from_lat) + ";"
               + str(to_lon) + "," + str(to_lat))
        params = {"overview": "full", "geometries": "geojson",
                  "steps": "false", "annotations": "false"}

        # try：嘗試連線並讀資料；except：出任何錯就丟一個清楚的訊息
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()     # HTTP 狀態若是錯誤(如 404)就在這裡出錯
            data = resp.json()          # 回應的 JSON → Python 字典
        except Exception as exc:
            raise Exception("OSRM 連線失敗：" + str(exc))

        if data.get("code") != "Ok":   # OSRM 用 "Ok" 代表成功
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
