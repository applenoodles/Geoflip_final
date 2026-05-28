from __future__ import annotations

from html import unescape
import math
import re
from typing import Any

import httpx

from app.models import Poi, score_poi
from app.services.tls import build_ssl_context, is_certificate_verify_error


class OverpassError(Exception):
    """Raised when an Overpass API call fails for any reason."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


# ---------------------------------------------------------------------------
# Tag profile
# ---------------------------------------------------------------------------
#
# Ordered list of (category, allowed_types).
# * allowed_types is a frozenset → only those values count as supported.
# * allowed_types is None → any value is acceptable (used for `historic`);
#   for that case we additionally require a name tag, mirroring the QL.

_TAG_PROFILE: tuple[tuple[str, frozenset[str] | None], ...] = (
    (
        "amenity",
        frozenset(
            {
                "cafe", "restaurant", "fast_food", "bar", "library",
                "school", "college", "university", "hospital",
                "theatre", "arts_centre", "place_of_worship", "marketplace",
            }
        ),
    ),
    (
        "tourism",
        frozenset(
            {
                "museum", "attraction", "gallery", "hotel", "hostel",
                "viewpoint", "zoo", "theme_park",
            }
        ),
    ),
    (
        "leisure",
        frozenset({"park", "garden", "playground", "sports_centre", "stadium"}),
    ),
    (
        "shop",
        frozenset(
            {"convenience", "supermarket", "books", "mall", "department_store", "bakery"}
        ),
    ),
    ("railway", frozenset({"station", "halt"})),
    # Only `station` is kept here — `stop_position` and `platform` flood
    # dense areas with low-interest points that aren't fun to flag.
    ("public_transport", frozenset({"station"})),
    ("historic", None),
)


_NAME_KEYS_PRIORITY: tuple[str, ...] = ("name:zh-TW", "name:zh", "name", "name:en")


# Chinese fallback names for nameless POIs. Players see a friendly label
# ("餐廳", "運動中心") instead of the raw OSM tag (amenity:restaurant).
_CN_TYPE_NAMES: dict[tuple[str, str], str] = {
    ("amenity", "cafe"): "咖啡廳",
    ("amenity", "restaurant"): "餐廳",
    ("amenity", "fast_food"): "速食店",
    ("amenity", "bar"): "酒吧",
    ("amenity", "library"): "圖書館",
    ("amenity", "school"): "學校",
    ("amenity", "college"): "學院",
    ("amenity", "university"): "大學",
    ("amenity", "hospital"): "醫院",
    ("amenity", "theatre"): "劇院",
    ("amenity", "arts_centre"): "藝文中心",
    ("amenity", "place_of_worship"): "宗教場所",
    ("amenity", "marketplace"): "市集",
    ("tourism", "museum"): "博物館",
    ("tourism", "attraction"): "景點",
    ("tourism", "gallery"): "美術館",
    ("tourism", "hotel"): "飯店",
    ("tourism", "hostel"): "青年旅館",
    ("tourism", "viewpoint"): "觀景點",
    ("tourism", "zoo"): "動物園",
    ("tourism", "theme_park"): "主題樂園",
    ("leisure", "park"): "公園",
    ("leisure", "garden"): "花園",
    ("leisure", "playground"): "兒童遊樂場",
    ("leisure", "sports_centre"): "運動中心",
    ("leisure", "stadium"): "體育場",
    ("shop", "convenience"): "便利商店",
    ("shop", "supermarket"): "超市",
    ("shop", "books"): "書店",
    ("shop", "mall"): "購物中心",
    ("shop", "department_store"): "百貨公司",
    ("shop", "bakery"): "麵包店",
    ("railway", "station"): "火車站",
    ("railway", "halt"): "招呼站",
    ("public_transport", "station"): "車站",
}

_CN_CATEGORY_FALLBACK: dict[str, str] = {
    "amenity": "設施",
    "tourism": "觀光景點",
    "leisure": "休閒場所",
    "shop": "商店",
    "railway": "鐵路站",
    "public_transport": "交通站",
    "historic": "古蹟",
}

_RAW_TAG_WHITELIST = frozenset(
    {
        "name", "name:zh", "name:zh-TW", "name:en",
        "website", "wikidata", "wikipedia", "opening_hours", "phone",
    }
)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _pick_category(tags: dict) -> tuple[str, str] | None:
    """Return (category, poi_type) for the first matching profile tag, else None."""
    for category, allowed in _TAG_PROFILE:
        value = tags.get(category)
        if not value or not isinstance(value, str):
            continue
        if allowed is None:
            if not any(tags.get(k) for k in _NAME_KEYS_PRIORITY):
                return None
            return category, value
        if value in allowed:
            return category, value
    return None


def _pick_name(tags: dict, category: str, poi_type: str) -> tuple[str, bool]:
    """Return (name, has_real_name).

    No name tag → fall back to a Chinese type label (e.g. "餐廳") so the
    map doesn't show raw OSM strings like `amenity:restaurant`.
    """
    for key in _NAME_KEYS_PRIORITY:
        v = tags.get(key)
        if v:
            return v, True
    fallback = (
        _CN_TYPE_NAMES.get((category, poi_type))
        or _CN_CATEGORY_FALLBACK.get(category)
        or "未命名地點"
    )
    return fallback, False


def _build_raw(tags: dict, category: str) -> dict:
    raw: dict = {}
    for k in _RAW_TAG_WHITELIST:
        if k in tags:
            raw[k] = tags[k]
    if category in tags:
        raw[category] = tags[category]
    return raw


def _element_coords(element: dict) -> tuple[float, float] | None:
    if element.get("type") == "node":
        lat = element.get("lat")
        lon = element.get("lon")
    else:
        center = element.get("center") or {}
        lat = center.get("lat")
        lon = center.get("lon")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _normalize(element: dict) -> Poi | None:
    """
    Convert one Overpass element to a Poi.
    Returns None for unsupported / malformed elements.
    """
    elem_type = element.get("type")
    if elem_type not in {"node", "way", "relation"}:
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

    osm_id_raw = element.get("id")
    if osm_id_raw is None:
        return None
    try:
        osm_id = int(osm_id_raw)
    except (TypeError, ValueError):
        return None

    name, _has_real_name = _pick_name(tags, category, poi_type)

    return Poi(
        id=f"{elem_type}:{osm_id}",
        name=name,
        lat=lat,
        lon=lon,
        osm_type=elem_type,
        osm_id=osm_id,
        category=category,
        poi_type=poi_type,
        score=score_poi(category, poi_type),
        owner=None,
        discovered_turn=None,
        placed_turn=None,
        raw=_build_raw(tags, category),
    )


# ---------------------------------------------------------------------------
# Spatial spread filter
# ---------------------------------------------------------------------------

def _spread_filter(pois: list[Poi], min_spacing_m: float) -> list[Poi]:
    """Keep only POIs that are ≥ min_spacing_m from every already-selected POI.

    Iterates in the caller's order (score descending), so higher-value POIs
    always win when two candidates are too close to each other.
    Uses a flat-earth approximation — accurate enough within a 1 km radius.
    """
    selected: list[Poi] = []
    for poi in pois:
        for sel in selected:
            dlat = (poi.lat - sel.lat) * 111_000
            dlon = (poi.lon - sel.lon) * 111_000 * math.cos(math.radians(poi.lat))
            if math.sqrt(dlat ** 2 + dlon ** 2) < min_spacing_m:
                break
        else:
            selected.append(poi)
    return selected


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def _build_query(
    center_lat: float,
    center_lon: float,
    radius_m: float,
    limit: int,
) -> str:
    rad = int(round(radius_m))
    parts: list[str] = []
    for category, allowed in _TAG_PROFILE:
        if allowed is None:
            parts.append(
                f'  nwr["{category}"]["name"](around:{rad},{center_lat},{center_lon});'
            )
        else:
            alt = "|".join(sorted(allowed))
            parts.append(
                f'  nwr["{category}"~"^({alt})$"](around:{rad},{center_lat},{center_lon});'
            )
    body = "\n".join(parts)
    return (
        "[out:json][timeout:25];\n"
        "(\n"
        f"{body}\n"
        ");\n"
        f"out center {int(limit)};\n"
    )


def _response_error_summary(response: httpx.Response) -> str:
    """Return a compact plain-text Overpass error body, if one exists."""
    text = response.text
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = " ".join(text.split())
    return text[:300]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OverpassClient:
    """
    Thin wrapper around the Overpass API.

    Fetches a bounded set of POI candidates around a center coordinate,
    using a curated tag profile that maps to existing game scoring.
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 25.0,
        min_spacing_m: float = 80.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._min_spacing_m = min_spacing_m
        self._cache: dict[tuple[float, float, float, int], list[Poi]] = {}
        self._ssl_context = build_ssl_context()

    def _cache_key(
        self, center_lat: float, center_lon: float, radius_m: float, limit: int
    ) -> tuple[float, float, float, int]:
        # Round center to ~10m grid so nearby setups share a board fetch.
        return (
            round(center_lat, 4),
            round(center_lon, 4),
            float(radius_m),
            int(limit),
        )

    @staticmethod
    def _attempt_radii(radius_m: float) -> list[float]:
        """Original radius plus up to two smaller fallbacks (e.g. 900 → 600 → 450)."""
        radii: list[float] = [radius_m]
        for factor in (2.0 / 3.0, 0.5):
            smaller = radius_m * factor
            if smaller >= 300.0 and smaller < radii[-1] - 50.0:
                radii.append(smaller)
        return radii

    def fetch_board_pois(
        self,
        center_lat: float,
        center_lon: float,
        radius_m: float,
        limit: int = 60,
    ) -> list[Poi]:
        """
        Fetch POI candidates around (center_lat, center_lon) within radius_m.

        Returns a de-duplicated list of Poi objects (owner=None).
        Raises OverpassError on any network / parse failure.

        On transient failure (timeout, 5xx, network error) retries with
        progressively smaller radii. Non-transient failures (HTTP 4xx, bad
        payload, cert error) raise immediately.
        """
        key = self._cache_key(center_lat, center_lon, radius_m, limit)
        if key in self._cache:
            return self._cache[key]

        last_error: OverpassError | None = None
        for attempt_radius in self._attempt_radii(radius_m):
            try:
                pois = self._fetch_once(center_lat, center_lon, attempt_radius, limit)
            except OverpassError as exc:
                if not exc.transient:
                    raise
                last_error = exc
                continue
            self._cache[key] = pois
            return pois

        assert last_error is not None
        raise last_error

    def _fetch_once(
        self,
        center_lat: float,
        center_lon: float,
        radius_m: float,
        limit: int,
    ) -> list[Poi]:
        query = _build_query(center_lat, center_lon, radius_m, limit)
        url = f"{self._base_url}/api/interpreter"

        try:
            with httpx.Client(
                headers={"User-Agent": "geoflip-coursework/0.1"},
                timeout=self._timeout,
                verify=self._ssl_context,
            ) as client:
                resp = client.post(url, data={"data": query})
                resp.raise_for_status()
                payload: Any = resp.json()
        except httpx.TimeoutException as exc:
            raise OverpassError(
                "Overpass 服務暫時無法使用，請稍後再試", transient=True
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            detail = _response_error_summary(exc.response)
            message = f"Overpass HTTP error {status}"
            if detail:
                message = f"{message}: {detail}"
            is_transient = status in (500, 502, 503, 504)
            raise OverpassError(message, transient=is_transient) from exc
        except httpx.RequestError as exc:
            if is_certificate_verify_error(exc):
                raise OverpassError(
                    "SSL 憑證驗證失敗，請更新 certifi/truststore 或確認系統根憑證",
                    transient=False,
                ) from exc
            raise OverpassError(
                f"Overpass request error: {exc}", transient=True
            ) from exc
        except (ValueError, KeyError) as exc:
            raise OverpassError(
                f"Overpass response parse error: {exc}", transient=False
            ) from exc

        if not isinstance(payload, dict):
            raise OverpassError("Overpass response is not a JSON object")

        elements = payload.get("elements")
        if not isinstance(elements, list):
            raise OverpassError("Overpass response missing 'elements' list")

        pois: list[Poi] = []
        seen_ids: set[str] = set()
        for element in elements:
            if not isinstance(element, dict):
                continue
            poi = _normalize(element)
            if poi is None:
                continue
            if poi.id in seen_ids:
                continue
            seen_ids.add(poi.id)
            pois.append(poi)

        # Deterministic: higher-score POIs first (museum/park/historic before
        # cafe), id ascending as a stable tiebreak. No randomness.
        pois.sort(key=lambda p: (-p.score, p.id))

        # Drop POIs that are too close to a higher-scoring neighbour so the
        # board stays spread out and no area gives a last-move sweep advantage.
        pois = _spread_filter(pois, self._min_spacing_m)

        if len(pois) > limit:
            pois = pois[:limit]

        return pois
