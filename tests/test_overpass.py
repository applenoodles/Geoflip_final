"""Tests for app/services/overpass.py — all HTTP mocked, no real network."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.services.overpass import (
    OverpassClient,
    OverpassError,
    _build_query,
    _normalize,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _make_client(**kwargs) -> OverpassClient:
    # min_spacing_m=0 disables the spatial spread filter so tests can assert
    # exact POI counts without worrying about coordinates being too close.
    defaults = dict(base_url="https://overpass.example.com", timeout_seconds=5.0, min_spacing_m=0.0)
    defaults.update(kwargs)
    return OverpassClient(**defaults)


def _node(**overrides) -> dict:
    base: dict = {
        "type": "node",
        "id": 111,
        "lat": 25.0330,
        "lon": 121.5654,
        "tags": {"amenity": "cafe", "name": "Test Cafe"},
    }
    base.update(overrides)
    return base


def _way(**overrides) -> dict:
    base: dict = {
        "type": "way",
        "id": 222,
        "center": {"lat": 25.0335, "lon": 121.5660},
        "tags": {"leisure": "park", "name": "Central Park"},
    }
    base.update(overrides)
    return base


def _relation(**overrides) -> dict:
    base: dict = {
        "type": "relation",
        "id": 333,
        "center": {"lat": 25.0340, "lon": 121.5670},
        "tags": {"tourism": "museum", "name": "City Museum"},
    }
    base.update(overrides)
    return base


def _setup_mock_http(mocker, json_data, status_code: int = 200) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data

    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=MagicMock(status_code=status_code),
        )
    else:
        mock_resp.raise_for_status.return_value = None

    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    inst.post.return_value = mock_resp
    return inst


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_normalize_node():
    poi = _normalize(_node())
    assert poi is not None
    assert poi.id == "node:111"
    assert poi.osm_type == "node"
    assert poi.osm_id == 111
    assert poi.lat == pytest.approx(25.0330)
    assert poi.lon == pytest.approx(121.5654)
    assert poi.category == "amenity"
    assert poi.poi_type == "cafe"
    assert poi.score == 2  # amenity:cafe → 2 pts
    assert poi.name == "Test Cafe"
    assert poi.owner is None
    assert poi.discovered_turn is None
    assert poi.placed_turn is None


def test_normalize_way_uses_center():
    poi = _normalize(_way())
    assert poi is not None
    assert poi.id == "way:222"
    assert poi.osm_type == "way"
    assert poi.osm_id == 222
    assert poi.lat == pytest.approx(25.0335)
    assert poi.lon == pytest.approx(121.5660)
    assert poi.category == "leisure"
    assert poi.poi_type == "park"
    assert poi.score == 3


def test_normalize_relation_uses_center():
    poi = _normalize(_relation())
    assert poi is not None
    assert poi.id == "relation:333"
    assert poi.osm_type == "relation"
    assert poi.category == "tourism"
    assert poi.poi_type == "museum"
    assert poi.score == 3


def test_normalize_way_missing_center_returns_none():
    elem = {"type": "way", "id": 1, "tags": {"amenity": "cafe", "name": "X"}}
    assert _normalize(elem) is None


def test_normalize_node_missing_coords_returns_none():
    elem = {"type": "node", "id": 1, "tags": {"amenity": "cafe", "name": "X"}}
    assert _normalize(elem) is None


def test_normalize_unsupported_amenity_returns_none():
    """`amenity=bench` is not in the supported profile."""
    elem = _node(tags={"amenity": "bench", "name": "Old Bench"})
    assert _normalize(elem) is None


def test_normalize_unsupported_category_returns_none():
    """`highway=*` is not part of the profile at all."""
    elem = _node(tags={"highway": "residential", "name": "Some Road"})
    assert _normalize(elem) is None


def test_normalize_public_transport_stop_position_returns_none():
    """`public_transport=stop_position` is intentionally excluded (low-interest)."""
    elem = _node(tags={"public_transport": "stop_position", "name": "Bus Stop"})
    assert _normalize(elem) is None


def test_normalize_public_transport_platform_returns_none():
    """`public_transport=platform` is intentionally excluded (low-interest)."""
    elem = _node(tags={"public_transport": "platform", "name": "Platform 3"})
    assert _normalize(elem) is None


def test_normalize_public_transport_station_kept():
    """`public_transport=station` remains in the profile."""
    poi = _normalize(_node(tags={"public_transport": "station", "name": "Bus Hub"}))
    assert poi is not None
    assert poi.category == "public_transport"
    assert poi.poi_type == "station"


def test_normalize_no_tags_returns_none():
    elem = {"type": "node", "id": 1, "lat": 0.0, "lon": 0.0}
    assert _normalize(elem) is None


def test_normalize_unknown_element_type_returns_none():
    elem = {"type": "changeset", "id": 1, "tags": {"amenity": "cafe", "name": "X"}}
    assert _normalize(elem) is None


def test_historic_requires_name():
    with_name = _node(id=10, tags={"historic": "castle", "name": "Old Castle"})
    without_name = _node(id=11, tags={"historic": "castle"})
    assert _normalize(with_name) is not None
    assert _normalize(without_name) is None


def test_historic_score_is_3():
    poi = _normalize(_node(tags={"historic": "monument", "name": "X"}))
    assert poi is not None
    assert poi.score == 3


# ---------------------------------------------------------------------------
# Name priority
# ---------------------------------------------------------------------------

def test_name_prefers_zh_tw():
    poi = _normalize(
        _node(
            tags={
                "amenity": "cafe",
                "name": "Generic Cafe",
                "name:zh-TW": "繁體咖啡",
                "name:zh": "咖啡",
            }
        )
    )
    assert poi.name == "繁體咖啡"


def test_name_falls_back_to_zh_then_name():
    poi = _normalize(
        _node(
            tags={
                "amenity": "cafe",
                "name": "Plain",
                "name:zh": "咖啡",
            }
        )
    )
    assert poi.name == "咖啡"


def test_name_falls_back_to_name_tag():
    poi = _normalize(_node(tags={"amenity": "cafe", "name": "Plain"}))
    assert poi.name == "Plain"


def test_nameless_poi_uses_chinese_type_label():
    """No name:* tag → friendly Chinese label, NOT raw `amenity:restaurant`."""
    poi = _normalize(_node(tags={"amenity": "restaurant"}))
    assert poi is not None
    assert poi.name == "餐廳"
    assert ":" not in poi.name  # no raw OSM tag leaks through


def test_nameless_sports_centre_label():
    poi = _normalize(_node(tags={"leisure": "sports_centre"}))
    assert poi is not None
    assert poi.name == "運動中心"


def test_nameless_unknown_subtype_falls_back_to_category_label():
    """Category is supported but specific poi_type isn't in the CN table —
    fall back to the category-level label rather than the raw tag string."""
    # `amenity:fast_food` is in CN table, but if we feed something exotic
    # within an allowed category (e.g. via a tag the profile happens to
    # accept), the helper should still pick a category fallback.
    from app.services.overpass import _pick_name
    name, has_real = _pick_name({}, "amenity", "unknown_subtype")
    assert has_real is False
    assert name == "設施"
    assert ":" not in name


# ---------------------------------------------------------------------------
# Raw whitelist
# ---------------------------------------------------------------------------

def test_raw_includes_whitelisted_tags():
    poi = _normalize(
        _node(
            tags={
                "amenity": "cafe",
                "name": "Test Cafe",
                "website": "https://example.com",
                "phone": "+886-3-1234567",
                "opening_hours": "Mo-Fr 08:00-18:00",
                "wikidata": "Q123",
            }
        )
    )
    assert poi.raw["name"] == "Test Cafe"
    assert poi.raw["website"] == "https://example.com"
    assert poi.raw["phone"] == "+886-3-1234567"
    assert poi.raw["opening_hours"] == "Mo-Fr 08:00-18:00"
    assert poi.raw["wikidata"] == "Q123"
    # The category tag is also preserved so callers know the OSM key.
    assert poi.raw["amenity"] == "cafe"


def test_raw_excludes_unknown_tags():
    poi = _normalize(
        _node(
            tags={
                "amenity": "cafe",
                "name": "Test Cafe",
                "addr:street": "Test Street",
                "brand": "Starbucks",
                "wheelchair": "yes",
            }
        )
    )
    assert "addr:street" not in poi.raw
    assert "brand" not in poi.raw
    assert "wheelchair" not in poi.raw


# ---------------------------------------------------------------------------
# fetch_board_pois — end-to-end with mocked HTTP
# ---------------------------------------------------------------------------

def test_fetch_returns_pois(mocker):
    _setup_mock_http(mocker, {"elements": [_node(), _way(), _relation()]})
    pois = _make_client().fetch_board_pois(25.0330, 121.5654, 900)
    assert len(pois) == 3
    assert {p.id for p in pois} == {"node:111", "way:222", "relation:333"}


def test_fetch_filters_unsupported_tags(mocker):
    _setup_mock_http(
        mocker,
        {
            "elements": [
                _node(id=1, tags={"amenity": "cafe", "name": "Good"}),
                _node(id=2, tags={"amenity": "bench", "name": "Bad"}),
                _node(id=3, tags={"barrier": "fence"}),
                _node(id=4, tags={"shop": "supermarket", "name": "Mart"}),
            ]
        },
    )
    pois = _make_client().fetch_board_pois(25.033, 121.565, 900)
    names = sorted(p.name for p in pois)
    assert names == ["Good", "Mart"]


def test_fetch_deduplicates_same_id(mocker):
    _setup_mock_http(
        mocker,
        {
            "elements": [
                _node(id=1, tags={"amenity": "cafe", "name": "First"}),
                _node(id=1, tags={"amenity": "cafe", "name": "Duplicate"}),
            ]
        },
    )
    pois = _make_client().fetch_board_pois(25.033, 121.565, 900)
    assert len(pois) == 1
    assert pois[0].name == "First"


def test_fetch_filters_stop_position_and_platform(mocker):
    """End-to-end: stop_position / platform elements never reach the board."""
    _setup_mock_http(
        mocker,
        {
            "elements": [
                _node(id=10, tags={"public_transport": "stop_position", "name": "Stop"}),
                _node(id=11, tags={"public_transport": "platform", "name": "Plat"}),
                _node(id=12, tags={"public_transport": "station", "name": "Hub"}),
                _node(id=13, tags={"amenity": "cafe", "name": "Cafe"}),
            ]
        },
    )
    pois = _make_client().fetch_board_pois(25.033, 121.565, 900)
    names = sorted(p.name for p in pois)
    assert names == ["Cafe", "Hub"]


def test_fetch_prefers_higher_score_when_over_limit(mocker):
    """When the response exceeds `limit`, higher-score POIs are kept first."""
    _setup_mock_http(
        mocker,
        {
            "elements": [
                # Score 2 (cafe) — five of them.
                _node(id=1, tags={"amenity": "cafe", "name": "Cafe 1"}),
                _node(id=2, tags={"amenity": "cafe", "name": "Cafe 2"}),
                _node(id=3, tags={"amenity": "cafe", "name": "Cafe 3"}),
                _node(id=4, tags={"amenity": "cafe", "name": "Cafe 4"}),
                _node(id=5, tags={"amenity": "cafe", "name": "Cafe 5"}),
                # Score 3 (museum/park) — should win the limit race.
                _node(id=6, tags={"tourism": "museum", "name": "Museum A"}),
                _node(id=7, tags={"leisure": "park", "name": "Park A"}),
            ]
        },
    )
    pois = _make_client().fetch_board_pois(25.033, 121.565, 900, limit=2)
    assert len(pois) == 2
    assert {p.name for p in pois} == {"Museum A", "Park A"}


def test_fetch_sort_is_deterministic(mocker):
    """Same input → same output ordering (no randomness)."""
    elements = [
        _node(id=3, tags={"amenity": "cafe", "name": "C"}),
        _node(id=1, tags={"tourism": "museum", "name": "M"}),
        _node(id=2, tags={"leisure": "park", "name": "P"}),
    ]
    _setup_mock_http(mocker, {"elements": elements})
    pois = _make_client().fetch_board_pois(25.033, 121.565, 900)
    # museum/park (score 3) before cafe (score 2); museum id < park id.
    assert [p.name for p in pois] == ["M", "P", "C"]


def test_fetch_respects_limit(mocker):
    elements = [
        _node(id=i, tags={"amenity": "cafe", "name": f"Cafe {i}"})
        for i in range(1, 11)
    ]
    _setup_mock_http(mocker, {"elements": elements})
    pois = _make_client().fetch_board_pois(25.033, 121.565, 900, limit=5)
    assert len(pois) == 5


def test_fetch_skips_non_dict_elements(mocker):
    _setup_mock_http(
        mocker,
        {
            "elements": [
                None,
                "garbage",
                _node(id=1, tags={"amenity": "cafe", "name": "Real"}),
            ]
        },
    )
    pois = _make_client().fetch_board_pois(25.033, 121.565, 900)
    assert len(pois) == 1
    assert pois[0].name == "Real"


def test_fetch_caches_repeated_calls(mocker):
    inst = _setup_mock_http(mocker, {"elements": [_node()]})
    client = _make_client()
    r1 = client.fetch_board_pois(25.033, 121.5654, 900)
    r2 = client.fetch_board_pois(25.033, 121.5654, 900)
    assert inst.post.call_count == 1
    assert r1 is r2


def test_fetch_cache_key_differs_by_radius(mocker):
    inst = _setup_mock_http(mocker, {"elements": [_node()]})
    client = _make_client()
    client.fetch_board_pois(25.033, 121.5654, 900)
    client.fetch_board_pois(25.033, 121.5654, 1500)
    assert inst.post.call_count == 2


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_timeout_raises_overpass_error(mocker):
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    inst.post.side_effect = httpx.ReadTimeout("timed out", request=MagicMock())
    with pytest.raises(OverpassError, match="暫時無法使用"):
        _make_client().fetch_board_pois(25.033, 121.565, 900)


def test_http_error_raises_overpass_error(mocker):
    _setup_mock_http(mocker, {}, status_code=429)
    with pytest.raises(OverpassError, match="HTTP error 429"):
        _make_client().fetch_board_pois(25.033, 121.565, 900)


def test_http_5xx_raises_overpass_error(mocker):
    _setup_mock_http(mocker, {}, status_code=504)
    with pytest.raises(OverpassError, match="HTTP error 504"):
        _make_client().fetch_board_pois(25.033, 121.565, 900)


def test_bad_json_raises_overpass_error(mocker):
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.side_effect = ValueError("invalid json")
    inst.post.return_value = mock_resp
    with pytest.raises(OverpassError, match="parse error"):
        _make_client().fetch_board_pois(25.033, 121.565, 900)


def test_connect_error_raises_overpass_error(mocker):
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    inst.post.side_effect = httpx.ConnectError("refused", request=MagicMock())
    with pytest.raises(OverpassError, match="request error"):
        _make_client().fetch_board_pois(25.033, 121.565, 900)


def test_response_missing_elements_raises(mocker):
    _setup_mock_http(mocker, {"version": 0.6})
    with pytest.raises(OverpassError, match="elements"):
        _make_client().fetch_board_pois(25.033, 121.565, 900)


def test_response_non_object_raises(mocker):
    _setup_mock_http(mocker, ["not", "an", "object"])
    with pytest.raises(OverpassError, match="JSON object"):
        _make_client().fetch_board_pois(25.033, 121.565, 900)


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

def test_query_includes_around_radius_lat_lon():
    q = _build_query(25.033, 121.5654, 900, 60)
    assert "around:900,25.033,121.5654" in q


def test_query_includes_out_center():
    q = _build_query(25.0, 121.0, 800, 60)
    assert "out center" in q


def test_query_includes_all_supported_categories():
    q = _build_query(25.0, 121.0, 800, 60)
    for category in (
        "amenity", "tourism", "leisure", "shop",
        "railway", "public_transport", "historic",
    ):
        assert f'"{category}"' in q


def test_query_includes_supported_type_values():
    q = _build_query(25.0, 121.0, 800, 60)
    # A few representative supported type values from each category.
    for value in ("cafe", "museum", "park", "supermarket", "station"):
        assert value in q


def test_query_historic_requires_name_tag():
    q = _build_query(25.0, 121.0, 800, 60)
    assert '"historic"]["name"]' in q


def test_query_radius_is_rounded_integer():
    q = _build_query(25.0, 121.0, 899.6, 60)
    assert "around:900," in q


def test_query_excludes_public_transport_stop_position_and_platform():
    """The QL must not ask Overpass for stop_position / platform values."""
    q = _build_query(25.0, 121.0, 800, 60)
    # Locate the public_transport line and check its allowed alternation.
    pt_lines = [line for line in q.splitlines() if '"public_transport"' in line]
    assert pt_lines, "public_transport line missing from query"
    joined = "\n".join(pt_lines)
    assert "stop_position" not in joined
    assert "platform" not in joined
    assert "station" in joined


def test_fetch_sends_query_to_interpreter_endpoint(mocker):
    inst = _setup_mock_http(mocker, {"elements": []})
    _make_client(base_url="https://overpass.example.com").fetch_board_pois(
        25.0, 121.0, 800
    )
    args, kwargs = inst.post.call_args
    url = args[0]
    assert url == "https://overpass.example.com/api/interpreter"
    sent_data = kwargs.get("data") or (args[1] if len(args) > 1 else None)
    assert sent_data is not None
    sent_query = sent_data["data"]
    assert "around:800,25.0,121.0" in sent_query
    assert "out center" in sent_query
