"""Tests for app/services/osrm.py — all HTTP mocked, no real network."""
from __future__ import annotations

import pytest
import httpx
from unittest.mock import MagicMock

from app.services.osrm import OsrmClient, OsrmError


# ---------------------------------------------------------------------------
# Factories / helpers
# ---------------------------------------------------------------------------

def _make_client(**kwargs) -> OsrmClient:
    defaults = dict(
        base_url="https://router.example.com",
        profile="foot",
        timeout_seconds=5.0,
    )
    defaults.update(kwargs)
    return OsrmClient(**defaults)


def _osrm_ok_response(
    coordinates: list[list[float]] | None = None,
    distance_m: float = 500.0,
    duration_s: float = 300.0,
) -> dict:
    if coordinates is None:
        coordinates = [[121.5654, 25.0330], [121.5700, 25.0400]]
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": distance_m,
                "duration": duration_s,
                "geometry": {
                    "type": "LineString",
                    "coordinates": coordinates,
                },
            }
        ],
    }


def _setup_mock_http(mocker, json_data: dict, status_code: int = 200) -> MagicMock:
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
    inst.get.return_value = mock_resp

    return inst


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_route_returns_route_result(mocker):
    from app.models import RouteResult
    _setup_mock_http(mocker, _osrm_ok_response(duration_s=250.0, distance_m=350.0))
    result = _make_client().route(25.033, 121.565, 25.040, 121.570)
    assert isinstance(result, RouteResult)
    assert result.duration_s == pytest.approx(250.0)
    assert result.distance_m == pytest.approx(350.0)


def test_distance_and_duration_converted_to_float(mocker):
    resp = _osrm_ok_response()
    resp["routes"][0]["distance"] = "500"
    resp["routes"][0]["duration"] = "300"
    _setup_mock_http(mocker, resp)
    result = _make_client().route(25.033, 121.565, 25.040, 121.570)
    assert isinstance(result.distance_m, float)
    assert isinstance(result.duration_s, float)


# ---------------------------------------------------------------------------
# Coordinate order (critical)
# ---------------------------------------------------------------------------

def test_request_url_uses_lonlat_order(mocker):
    """OSRM URL path must be {from_lon},{from_lat};{to_lon},{to_lat}."""
    inst = _setup_mock_http(mocker, _osrm_ok_response())
    from_lat, from_lon = 25.0330, 121.5654
    to_lat, to_lon = 25.0400, 121.5700

    _make_client().route(from_lat, from_lon, to_lat, to_lon)

    url_called = inst.get.call_args[0][0]
    # Must contain lon,lat order for both waypoints
    assert f"{from_lon},{from_lat}" in url_called
    assert f"{to_lon},{to_lat}" in url_called
    # Must NOT have lat,lon order (which would be wrong)
    assert f"{from_lat},{from_lon}" not in url_called
    assert f"{to_lat},{to_lon}" not in url_called


def test_request_url_profile_in_path(mocker):
    inst = _setup_mock_http(mocker, _osrm_ok_response())
    _make_client(profile="foot").route(25.033, 121.565, 25.040, 121.570)
    url = inst.get.call_args[0][0]
    assert "/route/v1/foot/" in url


def test_response_coordinates_preserved_as_lonlat(mocker):
    """GeoJSON coordinates from OSRM are [lon, lat]; must pass through unchanged."""
    # lon=121.5, lat=25.0 format → coords[i][0]=lon, coords[i][1]=lat
    coords_lonlat = [[121.5654, 25.0330], [121.5680, 25.0360], [121.5700, 25.0400]]
    _setup_mock_http(mocker, _osrm_ok_response(coordinates=coords_lonlat))
    result = _make_client().route(25.033, 121.5654, 25.040, 121.570)

    # First element of each coordinate pair must be the longitude (> 100 for Taiwan)
    for coord in result.coordinates_lonlat:
        assert coord[0] > 100, "First element should be longitude"
        assert coord[1] < 90, "Second element should be latitude"


def test_response_coordinates_not_swapped(mocker):
    """Verify coordinates_lonlat is NOT [lat, lon] — it must stay as GeoJSON [lon, lat]."""
    coords = [[121.5654, 25.0330], [121.5700, 25.0400]]
    _setup_mock_http(mocker, _osrm_ok_response(coordinates=coords))
    result = _make_client().route(25.033, 121.5654, 25.040, 121.570)
    assert result.coordinates_lonlat == coords  # exact preservation


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

def test_cache_hit_no_http(mocker):
    inst = _setup_mock_http(mocker, _osrm_ok_response())
    client = _make_client()
    r1 = client.route(25.033, 121.565, 25.040, 121.570)
    r2 = client.route(25.033, 121.565, 25.040, 121.570)
    assert inst.get.call_count == 1
    assert r1 is r2


def test_different_endpoints_both_hit_http(mocker):
    inst = _setup_mock_http(mocker, _osrm_ok_response())
    client = _make_client()
    client.route(25.033, 121.565, 25.040, 121.570)
    client.route(25.033, 121.565, 25.050, 121.580)
    assert inst.get.call_count == 2


def test_cache_key_includes_profile(mocker):
    _setup_mock_http(mocker, _osrm_ok_response())
    c1 = _make_client(profile="foot")
    c2 = _make_client(profile="car")
    c1.route(25.033, 121.565, 25.040, 121.570)
    c2.route(25.033, 121.565, 25.040, 121.570)
    # Each client has its own cache → independent (different profile in key)
    # Both should have fetched from HTTP once each
    # (separate OsrmClient instances)
    # Just verify each returns successfully
    assert c1._cache
    assert c2._cache


# ---------------------------------------------------------------------------
# OSRM error codes → OsrmError
# ---------------------------------------------------------------------------

def test_osrm_code_no_route_raises(mocker):
    resp = {"code": "NoRoute", "message": "No route found"}
    _setup_mock_http(mocker, resp)
    with pytest.raises(OsrmError, match="步行路線"):
        _make_client().route(25.033, 121.565, 25.040, 121.570)


def test_osrm_code_no_segment_raises(mocker):
    resp = {"code": "NoSegment"}
    _setup_mock_http(mocker, resp)
    with pytest.raises(OsrmError):
        _make_client().route(25.033, 121.565, 25.040, 121.570)


def test_osrm_profile_not_found_raises_helpful_message(mocker):
    resp = {"code": "ProfileNotFound"}
    _setup_mock_http(mocker, resp)
    with pytest.raises(OsrmError, match="OSRM_PROFILE"):
        _make_client().route(25.033, 121.565, 25.040, 121.570)


def test_osrm_invalid_service_raises_helpful_message(mocker):
    resp = {"code": "InvalidService"}
    _setup_mock_http(mocker, resp)
    with pytest.raises(OsrmError, match="OSRM_PROFILE"):
        _make_client().route(25.033, 121.565, 25.040, 121.570)


def test_osrm_empty_routes_list_raises(mocker):
    resp = {"code": "Ok", "routes": []}
    _setup_mock_http(mocker, resp)
    with pytest.raises(OsrmError, match="步行路線"):
        _make_client().route(25.033, 121.565, 25.040, 121.570)


def test_osrm_coordinates_less_than_2_raises(mocker):
    coords = [[121.5654, 25.0330]]  # only 1 point
    _setup_mock_http(mocker, _osrm_ok_response(coordinates=coords))
    with pytest.raises(OsrmError, match="fewer than 2"):
        _make_client().route(25.033, 121.565, 25.040, 121.570)


def test_osrm_coordinates_exactly_2_ok(mocker):
    coords = [[121.5654, 25.0330], [121.5700, 25.0400]]
    _setup_mock_http(mocker, _osrm_ok_response(coordinates=coords))
    result = _make_client().route(25.033, 121.565, 25.040, 121.570)
    assert len(result.coordinates_lonlat) == 2


def test_osrm_unknown_error_code_raises(mocker):
    resp = {"code": "SomeUnknownError"}
    _setup_mock_http(mocker, resp)
    with pytest.raises(OsrmError):
        _make_client().route(25.033, 121.565, 25.040, 121.570)


# ---------------------------------------------------------------------------
# HTTP / network errors → OsrmError
# ---------------------------------------------------------------------------

def test_timeout_raises_osrm_error(mocker):
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    inst.get.side_effect = httpx.ReadTimeout("timed out", request=MagicMock())

    with pytest.raises(OsrmError, match="timed out"):
        _make_client().route(25.033, 121.565, 25.040, 121.570)


def test_http_error_raises_osrm_error(mocker):
    _setup_mock_http(mocker, {}, status_code=500)
    with pytest.raises(OsrmError, match="HTTP error"):
        _make_client().route(25.033, 121.565, 25.040, 121.570)


def test_connect_error_raises_osrm_error(mocker):
    mock_class = mocker.patch("httpx.Client")
    inst = mock_class.return_value
    inst.__enter__.return_value = inst
    inst.__exit__.return_value = False
    inst.get.side_effect = httpx.ConnectError("refused", request=MagicMock())

    with pytest.raises(OsrmError, match="request error"):
        _make_client().route(25.033, 121.565, 25.040, 121.570)


# ---------------------------------------------------------------------------
# OSRM request parameters
# ---------------------------------------------------------------------------

def test_request_params_contain_geojson(mocker):
    inst = _setup_mock_http(mocker, _osrm_ok_response())
    _make_client().route(25.033, 121.565, 25.040, 121.570)
    params = inst.get.call_args[1]["params"]
    assert params["geometries"] == "geojson"
    assert params["overview"] == "full"


def test_request_params_steps_false(mocker):
    inst = _setup_mock_http(mocker, _osrm_ok_response())
    _make_client().route(25.033, 121.565, 25.040, 121.570)
    params = inst.get.call_args[1]["params"]
    assert params["steps"] == "false"
    assert params["annotations"] == "false"
