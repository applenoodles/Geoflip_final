# =====================================================================
# Nominatim：用「文字關鍵字」搜尋地點，例如輸入「新竹車站」找出它在哪。
#
# 在 GeoFlip 裡，它只用在「開局選起始地點」：
# 玩家打關鍵字 → 我們給幾個候選地點 → 選一個當棋盤中心。
#
# 原本這裡是一個 class（含快取、限速、SSL）。現在只留一支函式，
# 回傳一個「候選地點清單」，清單裡每個元素都是普通字典。
# =====================================================================

import httpx

from app.config import (
    NOMINATIM_BASE_URL,
    NOMINATIM_EMAIL,
    NOMINATIM_USER_AGENT,
    REQUEST_TIMEOUT_SECONDS,
)


def _short_label(category, poi_type):
    """組出像 "railway:station" 的簡短分類標籤（給畫面顯示用）。"""
    if category and poi_type:
        return category + ":" + poi_type
    return category or poi_type or ""


def _country_display(country_code, country_label):
    """國家顯示名稱：優先用完整名稱，沒有就用國碼。"""
    if country_label:
        return country_label
    if country_code:
        return country_code.upper()
    return ""


def search_locations(query, limit=5):
    """用關鍵字搜尋起始地點，回傳候選清單（每個是一個字典）。

    每個候選字典有：display_name(地名)、lat、lon、short_label、country_display。
    搜尋失敗 → raise Exception。
    """
    headers = {
        "User-Agent": NOMINATIM_USER_AGENT,        # Nominatim 規定要附上來源
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }
    params = {
        "format": "jsonv2",
        "q": query,
        "limit": limit,
        "addressdetails": 1,
        "dedupe": 1,
        "email": NOMINATIM_EMAIL,
        "countrycodes": "tw",   # 只搜尋台灣的地點
    }

    try:
        resp = httpx.get(
            NOMINATIM_BASE_URL.rstrip("/") + "/search",
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        results = resp.json()   # 一串搜尋結果（每個是字典）
    except Exception as exc:
        raise Exception("搜尋失敗：" + str(exc))

    candidates = []
    seen = []   # 用來去掉重複的地點

    for result in results:
        # 沒有經緯度的結果直接跳過
        if not result.get("lat") or not result.get("lon"):
            continue
        lat = float(result["lat"])
        lon = float(result["lon"])

        display_name = result.get("display_name") or result.get("name") or "Unknown"

        # 從地址裡挖出國碼跟國名（可能沒有）
        address = result.get("address") or {}
        country_code = str(address.get("country_code") or "").strip().lower()
        country_label = str(address.get("country") or "").strip()

        # 去重複：同一個 osm 物件只留一個
        osm_type = result.get("osm_type")
        osm_id = result.get("osm_id")
        if osm_type and osm_id is not None:
            dedup_key = (osm_type, osm_id)
        else:
            dedup_key = display_name
        if dedup_key in seen:
            continue
        seen.append(dedup_key)

        candidates.append({
            "display_name": display_name,
            "lat": lat,
            "lon": lon,
            "short_label": _short_label(result.get("class", ""), result.get("type", "")),
            "country_display": _country_display(country_code, country_label),
            "country_code": country_code,
        })

    # 台灣的結果排前面（其他國家的仍保留在後面）
    taiwan = [c for c in candidates if c["country_code"] == "tw"]
    others = [c for c in candidates if c["country_code"] != "tw"]
    return taiwan + others
