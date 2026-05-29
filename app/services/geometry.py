# =====================================================================
# 幾何計算：把路線「加寬成一條 50 公尺的走廊」，再判斷哪些 POI 落在裡面。
#
# 為什麼需要這個檔案？
#   地圖上的座標是「經緯度（度）」，但「度」沒辦法直接拿來量公尺
#   （1 度經度在赤道≈111 公里，在台灣又不一樣）。
#   所以要先用 pyproj 把經緯度換算成「公尺座標」，
#   在公尺世界裡用 Shapely 把線加寬 50 公尺，再判斷點在不在裡面。
#
# 兩個套件的角色：
#   pyproj  → 座標轉換（經緯度 ↔ 公尺）
#   shapely → 幾何運算（畫線、加寬成多邊形、判斷點是否在多邊形內）
# =====================================================================

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, Polygon


def choose_metric_crs(lon, lat):
    """挑一個適合這個地點的「公尺座標系」(UTM)。

    地球被切成 60 個直條（UTM zone），每條 6 度寬。
    依照經度算出落在第幾條，就用那條對應的公尺座標系，誤差最小。
    """
    zone = int((lon + 180.0) // 6) + 1   # 算出在第幾個直條
    if zone < 1:
        zone = 1
    if zone > 60:
        zone = 60

    if lat >= 0:
        epsg = 32600 + zone   # 北半球
    else:
        epsg = 32700 + zone   # 南半球
    return CRS.from_epsg(epsg)


def build_meter_transformers(reference_lon, reference_lat):
    """做出兩個「座標轉換器」：一個把經緯度轉成公尺，一個轉回來。

    用法：
        to_meters.transform(lon, lat) -> (x公尺, y公尺)
        to_wgs84.transform(x, y)      -> (lon, lat)
    always_xy=True 是固定要加的，確保順序都是 (經度, 緯度)。
    """
    metric_crs = choose_metric_crs(reference_lon, reference_lat)
    wgs84 = CRS.from_epsg(4326)   # 4326 = 一般地圖用的經緯度座標系

    to_meters = Transformer.from_crs(wgs84, metric_crs, always_xy=True)
    to_wgs84 = Transformer.from_crs(metric_crs, wgs84, always_xy=True)
    return to_meters, to_wgs84


def route_to_meter_linestring(coordinates_lonlat, to_meters):
    """把 OSRM 給的路線（一串 [經度, 緯度] 點）轉成公尺座標的一條線。"""
    if len(coordinates_lonlat) < 2:
        raise ValueError("一條線至少要有 2 個點")

    points_xy = []
    for pair in coordinates_lonlat:
        lon = float(pair[0])
        lat = float(pair[1])
        x, y = to_meters.transform(lon, lat)   # 經緯度 → 公尺
        points_xy.append((x, y))
    return LineString(points_xy)   # 用這些點組成一條線


def buffer_route_meters(line_meters, buffer_m):
    """把一條線往兩側「加寬」buffer_m 公尺，變成一個多邊形（走廊）。"""
    if buffer_m <= 0:
        raise ValueError("寬度必須大於 0")
    return line_meters.buffer(buffer_m)   # shapely 的 buffer 就是加寬


def point_in_buffer(poi_lat, poi_lon, buffer_polygon, to_meters):
    """判斷某個 POI 是否落在走廊（多邊形）裡面（含邊界）。"""
    x, y = to_meters.transform(poi_lon, poi_lat)   # 先把 POI 也換成公尺
    return buffer_polygon.covers(Point(x, y))      # covers：在裡面或正好在邊上都算
