"""Tests for app/services/nominatim.py — all HTTP mocked, no real network."""
from __future__ import annotations

import pytest
import httpx
from unittest.mock import MagicMock

from app.services.nominatim import (
    LocationCandidate,
    NominatimClient,
    NominatimError,
    _build_raw,
    _normalize,
    _normalize_location,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _make_client(**kwargs) -> NominatimClient:
    defaults = dict(
        base_url="https://nominatim.example.com",
        user_agent="test-agent/1.0 (test@example.com)",
        email="test@example.com",
        timeout_seconds=5.0,
        min_interval_seconds=0.0,  # disabled in most tests
    )
    defaults.update(kwargs)
    return NominatimClient(**defaults)


def _nom_result(**overrides) -> dict:
    """Minimal valid Nominatim result dict."""
    base: dict = {
        "osm_type": "node",
        "osm_id": 123456,
        "lat": "25.0330",
        "lon": "121.5654",
        "class": "amenity",
        "type": "cafe",
        "display_name": "Test Cafe, Hsinchu, Taiwan",
        "name": "Test Cafe",
        "importance": 0.5,
        "address": {
            "country_code": "tw",
            "city": "Hsinchu City",
            "road": "Zhongzheng Road",
            "postcode": "300",
        },
        "extratags": {
            "website": "https://example.com",
            "phone": "+886-3-1234567",
            "opening_hours": "Mo-Fr 08:00-18:00",
        },
    }
    base.update(overrides)
    return base


def _setup_mock_http(mocker, json_data, status_code: int = 200) -> MagicMock:
    """
    Patch httpx.Client so that .get() returns json_data.
    Returns the mock *instance* (the thing returned by `with httpx.Client() as c:`).
    """
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
    mock_instance = mock_class.return_value
    mock_instance.__enter__.return_value = mock_instance
    mock_instance.__exit__.return_value = False
    mock_instance.get.return_value = mock_resp

    return mock_instance


# ---------------------------------------------------------------------------
# Normalization / Poi construction
# ---------------------------------------------------------------------------

def test_normalize_returns_poi(mocker):
    _setup_mock_http(mocker, [_nom_result()])
    pois = _make_client().search("cafe")
    assert len(pois) == 1
    poi = pois[0]
    assert poi.id == "node:123456"
    assert poi.name == "Test Cafe"
    assert poi.category == "amenity"
    assert poi.poi_type == "cafe"
    assert poi.score == 2  # amenity:cafe → 2 pts
    assert poi.owner is None


def test_lat_lon_string_converted_to_float(mocker):
    result = _nom_result(lat="25.0330", lon="121.5654")
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("cafe")[0]
    assert isinstance(poi.lat, float)
    assert isinstance(poi.lon, float)
    assert poi.lat == pytest.approx(25.033)
    assert poi.lon == pytest.approx(121.5654)


def test_poi_id_from_osm_type_and_osm_id(mocker):
    result = _nom_result(osm_type="way", osm_id=987)
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    assert poi.id == "way:987"


def test_poi_id_fallback_when_no_osm_info(mocker):
    result = _nom_result()
    del result["osm_type"]
    del result["osm_id"]
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    assert poi.id.startswith("hash:")
    assert len(poi.id) > 5  # non-empty hash


def test_fallback_id_is_stable():
    """Same lat/lon/name must always produce the same fallback id."""
    from app.services.nominatim import _fallback_id
    id1 = _fallback_id(25.033, 121.565, "Test")
    id2 = _fallback_id(25.033, 121.565, "Test")
    assert id1 == id2


def test_fallback_id_differs_for_different_inputs():
    from app.services.nominatim import _fallback_id
    assert _fallback_id(25.0, 121.0, "A") != _fallback_id(25.0, 121.0, "B")
    assert _fallback_id(25.0, 121.0, "A") != _fallback_id(25.1, 121.0, "A")


def test_name_prefers_namedetails(mocker):
    result = _nom_result(
        name="Short Name",
        namedetails={"name": "Preferred Name"},
    )
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    assert poi.name == "Preferred Name"


def test_name_falls_back_to_display_name(mocker):
    result = _nom_result()
    result.pop("name", None)
    result["display_name"] = "Display Name, City"
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    assert poi.name == "Display Name, City"


def test_score_3pts_for_historic(mocker):
    result = _nom_result(**{"class": "historic", "type": "castle"})
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    assert poi.score == 3


def test_score_3pts_for_tourism_museum(mocker):
    result = _nom_result(**{"class": "tourism", "type": "museum"})
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    assert poi.score == 3


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def test_filter_result_missing_lat(mocker):
    result = _nom_result()
    del result["lat"]
    _setup_mock_http(mocker, [result])
    assert _make_client().search("test") == []


def test_filter_result_missing_lon(mocker):
    result = _nom_result()
    del result["lon"]
    _setup_mock_http(mocker, [result])
    assert _make_client().search("test") == []


def test_filter_boundary_administrative(mocker):
    result = _nom_result(**{"class": "boundary", "type": "administrative"})
    _setup_mock_http(mocker, [result])
    assert _make_client().search("test") == []


def test_keep_boundary_national_park(mocker):
    result = _nom_result(**{"class": "boundary", "type": "national_park"})
    _setup_mock_http(mocker, [result])
    pois = _make_client().search("test")
    assert len(pois) == 1


# ---------------------------------------------------------------------------
# Duplicate ID deduplication
# ---------------------------------------------------------------------------

def test_duplicate_id_skipped(mocker):
    r1 = _nom_result(osm_type="node", osm_id=1, name="First")
    r2 = _nom_result(osm_type="node", osm_id=1, name="Duplicate")  # same id
    _setup_mock_http(mocker, [r1, r2])
    pois = _make_client().search("test")
    assert len(pois) == 1
    assert pois[0].name == "First"


def test_different_ids_both_returned(mocker):
    r1 = _nom_result(osm_id=1, name="A")
    r2 = _nom_result(osm_id=2, name="B")
    _setup_mock_http(mocker, [r1, r2])
    pois = _make_client().search("test")
    assert len(pois) == 2


# ---------------------------------------------------------------------------
# Raw whitelist
# ---------------------------------------------------------------------------

def test_raw_excludes_geojson(mocker):
    result = _nom_result()
    result["geojson"] = {"type": "Point", "coordinates": [121.5, 25.0]}
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    assert "geojson" not in poi.raw


def test_raw_excludes_boundingbox(mocker):
    result = _nom_result()
    result["boundingbox"] = ["25.0", "25.1", "121.5", "121.6"]
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    assert "boundingbox" not in poi.raw


def test_raw_excludes_icon(mocker):
    result = _nom_result()
    result["icon"] = "https://nominatim.example.com/images/mapicons/amenity_cafe.p.20.png"
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    assert "icon" not in poi.raw


def test_raw_includes_display_name(mocker):
    _setup_mock_http(mocker, [_nom_result()])
    poi = _make_client().search("test")[0]
    assert "display_name" in poi.raw


def test_raw_includes_whitelisted_address_fields(mocker):
    _setup_mock_http(mocker, [_nom_result()])
    poi = _make_client().search("test")[0]
    addr = poi.raw.get("address", {})
    assert "country_code" in addr
    assert "city" in addr
    assert "road" in addr


def test_raw_excludes_non_whitelisted_address_fields(mocker):
    result = _nom_result()
    result["address"]["state"] = "Hsinchu"  # not in whitelist
    result["address"]["county"] = "Hsinchu County"  # not in whitelist
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    addr = poi.raw.get("address", {})
    assert "state" not in addr
    assert "county" not in addr


def test_raw_includes_whitelisted_extratags(mocker):
    _setup_mock_http(mocker, [_nom_result()])
    poi = _make_client().search("test")[0]
    extra = poi.raw.get("extratags", {})
    assert "website" in extra
    assert "phone" in extra
    assert "opening_hours" in extra


def test_raw_excludes_non_whitelisted_extratags(mocker):
    result = _nom_result()
    result["extratags"]["brand"] = "Starbucks"  # not in whitelist
    _setup_mock_http(mocker, [result])
    poi = _make_client().search("test")[0]
    extra = poi.raw.get("extratags", {})
    assert "brand" not in extra


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

def test_cache_hit_no_http_request(mocker):
    mock_inst = _setup_mock_http(mocker, [_nom_result()])
    client = _make_client()

    result1 = client.search("cafe")
    result2 = client.search("cafe")  # same query → cache hit

    assert mock_inst.get.call_count == 1
    assert result1 is result2  # exact same list object


def test_different_queries_both_hit_http(mocker):
    mock_inst = _setup_mock_http(mocker, [_nom_result(osm_id=1)])
    client = _make_client()
    client.search("cafe")
    mock_inst.get.return_value.json.return_value = [_nom_result(osm_id=2)]
    client.search("restaurant")

    assert mock_inst.get.call_count == 2


def test_cache_key_includes_limit(mocker):
    """Different limits produce different cache entries → separate HTTP calls."""
    mock_inst = _setup_mock_http(mocker, [_nom_result(osm_id=1)])
    client = _make_client()
    client.search("cafe", limit=5)
    mock_inst.get.return_value.json.return_value = [_nom_result(osm_id=2)]
    client.search("cafe", limit=10)
    assert mock_inst.get.call_count == 2


# ---------------------------------------------------------------------------
# Rate limiter (monkeypatch time — no real sleep)
# ---------------------------------------------------------------------------

def test_rate_limiter_sleeps_between_cache_misses(mocker):
    """Second cache-miss call must sleep to honour min_interval_seconds."""
    # monotonic() call order:
    #   [start-req1, end-req1, start-req2, end-req2]
    # start-req1=10.0, last=0.0 → elapsed=10 ≥ 1.0 → no sleep
    # end-req1   → _last_request_time = 10.0
    # start-req2=10.3 → elapsed=0.3 < 1.0 → sleep(0.7)
    # end-req2   → _last_request_time = 101.3
    mono_values = [10.0, 10.0, 10.3, 101.3]
    mocker.patch("time.monotonic", side_effect=mono_values)
    sleep_mock = mocker.patch("time.sleep")

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    # Two different JSON responses for two different queries
    mock_resp.json.side_effect = [
        [_nom_result(osm_id=1, name="Cafe A")],
        [_nom_result(osm_id=2, name="Restaurant B")],
    ]
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    inst.get.return_value = mock_resp

    client = _make_client(min_interval_seconds=1.0)
    client.search("cafe")        # cache miss #1 – no sleep
    client.search("restaurant")  # cache miss #2 – should sleep

    sleep_mock.assert_called_once()
    assert abs(sleep_mock.call_args[0][0] - 0.7) < 0.01


def test_rate_limiter_no_sleep_on_cache_hit(mocker):
    """Cache hit must never trigger a sleep call."""
    sleep_mock = mocker.patch("time.sleep")
    mocker.patch("time.monotonic", return_value=0.1)  # always near 0 → would sleep

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = [_nom_result()]
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    inst.get.return_value = mock_resp

    client = _make_client(min_interval_seconds=1.0)
    client.search("cafe")  # cache miss (sleep may fire here)
    sleep_mock.reset_mock()
    client.search("cafe")  # cache hit — must NOT sleep

    sleep_mock.assert_not_called()


# ---------------------------------------------------------------------------
# User-Agent header
# ---------------------------------------------------------------------------

def test_user_agent_header_sent(mocker):
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = []
    inst.get.return_value = mock_resp

    ua = "geoflip-test/1.0 (test@example.com)"
    _make_client(user_agent=ua).search("test")

    call_kwargs = mock_class.call_args[1]
    assert "headers" in call_kwargs
    assert call_kwargs["headers"].get("User-Agent") == ua


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_timeout_raises_nominatim_error(mocker):
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    inst.get.side_effect = httpx.ReadTimeout("timed out", request=MagicMock())

    with pytest.raises(NominatimError, match="暫時無法使用"):
        _make_client().search("test")


def test_http_4xx_raises_nominatim_error(mocker):
    _setup_mock_http(mocker, [], status_code=429)
    with pytest.raises(NominatimError, match="NOMINATIM_USER_AGENT"):
        _make_client().search("test")


def test_http_5xx_raises_nominatim_error(mocker):
    _setup_mock_http(mocker, [], status_code=503)
    with pytest.raises(NominatimError, match="HTTP error"):
        _make_client().search("test")


def test_connect_error_raises_nominatim_error(mocker):
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    inst.get.side_effect = httpx.ConnectError("refused", request=MagicMock())

    with pytest.raises(NominatimError, match="request error"):
        _make_client().search("test")


# ---------------------------------------------------------------------------
# Viewbox parameter
# ---------------------------------------------------------------------------

def test_viewbox_params_sent_when_center_provided(mocker):
    mock_inst = _setup_mock_http(mocker, [])
    _make_client().search("cafe", center_lat=25.033, center_lon=121.565, search_km=1.0)

    params = mock_inst.get.call_args[1]["params"]
    assert "viewbox" in params
    assert "bounded" in params
    assert params["bounded"] == 1


def test_no_viewbox_without_center(mocker):
    mock_inst = _setup_mock_http(mocker, [])
    _make_client().search("cafe")

    params = mock_inst.get.call_args[1]["params"]
    assert "viewbox" not in params


# ---------------------------------------------------------------------------
# LocationCandidate / search_locations (setup flow)
# ---------------------------------------------------------------------------

def _loc_result(**overrides) -> dict:
    base: dict = {
        "osm_type": "node",
        "osm_id": 555,
        "lat": "24.8019",
        "lon": "120.9716",
        "class": "railway",
        "type": "station",
        "display_name": "新竹車站, 新竹市, 台灣",
        "name": "新竹車站",
    }
    base.update(overrides)
    return base


def test_normalize_location_returns_candidate():
    c = _normalize_location(_loc_result())
    assert isinstance(c, LocationCandidate)
    assert c.display_name == "新竹車站, 新竹市, 台灣"
    assert c.lat == pytest.approx(24.8019)
    assert c.lon == pytest.approx(120.9716)
    assert c.osm_type == "node"
    assert c.osm_id == 555
    assert c.category == "railway"
    assert c.poi_type == "station"


def test_normalize_location_keeps_administrative_boundary():
    """Admin boundaries are valid starting locations and must NOT be filtered."""
    c = _normalize_location(_loc_result(**{"class": "boundary", "type": "administrative"}))
    assert c is not None
    assert c.category == "boundary"
    assert c.poi_type == "administrative"


def test_normalize_location_missing_coords_returns_none():
    r = _loc_result()
    del r["lat"]
    assert _normalize_location(r) is None


def test_location_candidate_short_label():
    c = LocationCandidate(
        display_name="X", lat=0, lon=0, category="railway", poi_type="station",
    )
    assert c.short_label == "railway:station"


def test_search_locations_returns_candidates(mocker):
    _setup_mock_http(mocker, [_loc_result()])
    candidates = _make_client().search_locations("新竹車站")
    assert len(candidates) == 1
    assert candidates[0].display_name.startswith("新竹車站")


def test_search_locations_deduplicates_by_osm_id(mocker):
    r1 = _loc_result(osm_type="node", osm_id=1, display_name="A")
    r2 = _loc_result(osm_type="node", osm_id=1, display_name="Duplicate")
    _setup_mock_http(mocker, [r1, r2])
    candidates = _make_client().search_locations("test")
    assert len(candidates) == 1
    assert candidates[0].display_name == "A"


def test_search_locations_caches(mocker):
    inst = _setup_mock_http(mocker, [_loc_result()])
    client = _make_client()
    a = client.search_locations("test")
    b = client.search_locations("test")
    assert inst.get.call_count == 1
    assert a is b


def test_search_locations_timeout_raises(mocker):
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    inst.get.side_effect = httpx.ReadTimeout("t", request=MagicMock())
    with pytest.raises(NominatimError, match="暫時無法使用"):
        _make_client().search_locations("test")


def test_search_locations_http_error_raises(mocker):
    _setup_mock_http(mocker, [], status_code=503)
    with pytest.raises(NominatimError, match="HTTP error"):
        _make_client().search_locations("test")
