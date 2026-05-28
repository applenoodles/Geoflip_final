"""Projection + buffer helpers.

All distance work happens in projected meter coordinates (UTM).
WGS84 ↔ UTM transformers ALWAYS use `always_xy=True` so the API is (lon, lat) → (x, y).
"""
from __future__ import annotations

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point, Polygon


# ---------------------------------------------------------------------------
# CRS selection
# ---------------------------------------------------------------------------

def choose_metric_crs(lon: float, lat: float) -> CRS:
    """
    Return a UTM CRS suited for buffering near (lon, lat).

    UTM zone = floor((lon + 180) / 6) + 1, clamped to [1, 60].
    North hemisphere: EPSG 326{zone:02d}; South: EPSG 327{zone:02d}.
    """
    zone = int((lon + 180.0) // 6) + 1
    if zone < 1:
        zone = 1
    elif zone > 60:
        zone = 60

    if lat >= 0:
        epsg = 32600 + zone
    else:
        epsg = 32700 + zone

    return CRS.from_epsg(epsg)


# ---------------------------------------------------------------------------
# Transformers
# ---------------------------------------------------------------------------

def build_meter_transformers(
    reference_lon: float,
    reference_lat: float,
) -> tuple[Transformer, Transformer]:
    """
    Build (to_meters, to_wgs84) Transformer pair anchored near a reference point.

    Both transformers use `always_xy=True` so the calling convention is:
        to_meters.transform(lon, lat) -> (x_m, y_m)
        to_wgs84.transform(x_m, y_m)  -> (lon, lat)
    """
    metric_crs = choose_metric_crs(reference_lon, reference_lat)
    wgs84 = CRS.from_epsg(4326)

    to_meters = Transformer.from_crs(wgs84, metric_crs, always_xy=True)
    to_wgs84 = Transformer.from_crs(metric_crs, wgs84, always_xy=True)
    return to_meters, to_wgs84


# ---------------------------------------------------------------------------
# Route / buffer helpers
# ---------------------------------------------------------------------------

def route_to_meter_linestring(
    coordinates_lonlat: list[list[float]],
    to_meters: Transformer,
) -> LineString:
    """
    Project a GeoJSON-style [[lon, lat], ...] path to meter (x, y) and return a LineString.
    Requires >= 2 coordinate pairs.
    """
    if len(coordinates_lonlat) < 2:
        raise ValueError("LineString requires at least 2 coordinates")

    points_xy: list[tuple[float, float]] = []
    for pair in coordinates_lonlat:
        lon, lat = float(pair[0]), float(pair[1])
        x, y = to_meters.transform(lon, lat)
        points_xy.append((x, y))
    return LineString(points_xy)


def buffer_route_meters(line_meters: LineString, buffer_m: float) -> Polygon:
    """Buffer a projected line by `buffer_m` METERS — never call on WGS84 geometry."""
    if buffer_m <= 0:
        raise ValueError("buffer_m must be positive")
    return line_meters.buffer(buffer_m)


def point_in_buffer(
    poi_lat: float,
    poi_lon: float,
    buffer_polygon: Polygon,
    to_meters: Transformer,
) -> bool:
    """
    True if the POI is inside (or exactly on the boundary of) the buffer polygon.

    Uses `covers` so boundary points are inclusive and not flaky.
    """
    x, y = to_meters.transform(poi_lon, poi_lat)
    return buffer_polygon.covers(Point(x, y))
