# =====================================================================
# Nominatim：用「文字關鍵字」搜尋地點，例如輸入「新竹車站」找出它在哪。
#
# 在 GeoFlip 裡只用在「開局選起始地點」：
# 玩家打關鍵字 → 我們給幾個候選地點 → 選一個當棋盤中心。
#
# 用一個 class（NominatimClient）包起來，建立物件時記住伺服器網址、
# User-Agent、聯絡信箱（這是 Nominatim 服務條款要求的）。
# =====================================================================

import requests

from app.config import (
    NOMINATIM_BASE_URL,
    NOMINATIM_EMAIL,
    NOMINATIM_USER_AGENT,
    REQUEST_TIMEOUT_SECONDS,
)


def _short_label(category, poi_type):
    """組出像 "railway:station" 的簡短分類標籤（給畫面顯示）。"""
    if category and poi_type:
        return category + ":" + poi_type
    return category or poi_type or ""


def _country_display(country_code, country_label):
    """國家顯示名稱：優先完整名稱，沒有就用國碼。"""
    if country_label:
        return country_label
    if country_code:
        return country_code.upper()
    return ""


class NominatimClient:
    def __init__(self, base_url=NOMINATIM_BASE_URL,
                 user_agent=NOMINATIM_USER_AGENT, email=NOMINATIM_EMAIL,
                 timeout=REQUEST_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.email = email
        self.timeout = timeout

    def search_locations(self, query, limit=5):
        """用關鍵字搜尋起始地點，回傳候選清單（每個是字典）。

        每個候選有：display_name、lat、lon、short_label、country_display。
        搜尋失敗 → raise Exception。
        """
        headers = {
            "User-Agent": self.user_agent,        # Nominatim 規定要附來源
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }
        params = {
            "format": "jsonv2", "q": query, "limit": limit,
            "addressdetails": 1, "dedupe": 1,
            "email": self.email, "countrycodes": "tw",   # 只搜台灣
        }

        try:
            resp = requests.get(self.base_url + "/search", params=params,
                                headers=headers, timeout=self.timeout, verify=False)
            resp.raise_for_status()
            results = resp.json()      # 一串搜尋結果（每個是字典）
        except Exception as exc:
            raise Exception("搜尋失敗：" + str(exc))

        candidates = []
        seen = []      # 用來去掉重複

        for result in results:
            if not result.get("lat") or not result.get("lon"):
                continue   # 沒座標的跳過
            lat = float(result["lat"])
            lon = float(result["lon"])
            display_name = result.get("display_name") or result.get("name") or "Unknown"

            address = result.get("address") or {}
            country_code = str(address.get("country_code") or "").strip().lower()
            country_label = str(address.get("country") or "").strip()

            # 去重複：同一個 osm 物件只留一個
            osm_type = result.get("osm_type")
            osm_id = result.get("osm_id")
            dedup_key = (osm_type, osm_id) if (osm_type and osm_id is not None) else display_name
            if dedup_key in seen:
                continue
            seen.append(dedup_key)

            candidates.append({
                "display_name": display_name, "lat": lat, "lon": lon,
                "short_label": _short_label(result.get("class", ""), result.get("type", "")),
                "country_display": _country_display(country_code, country_label),
            })

        # params 已寫死 countrycodes=tw（只搜台灣），不必再額外排序國家
        return candidates
