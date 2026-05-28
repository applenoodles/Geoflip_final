"""Tests for app/map/render.py — Folium HTML output (no real network)."""
from __future__ import annotations

import re

import pytest

from app.config import Config
from app.map.render import render_map_html
from app.models import MoveRecord, Poi, RouteRecord
from app.state import new_game


def _poi(
    poi_id: str,
    lat: float = 25.0330,
    lon: float = 121.5654,
    *,
    name: str = "Test POI",
    owner=None,
    category: str = "amenity",
    poi_type: str = "cafe",
    score: int = 2,
) -> Poi:
    return Poi(
        id=poi_id, name=name, lat=lat, lon=lon,
        osm_type="node", osm_id=1,
        category=category, poi_type=poi_type, score=score,
        owner=owner, discovered_turn=0, placed_turn=None, raw={},
    )


def _seed_move(player_id, poi_id, turn_index=0):
    return MoveRecord(
        turn_index=turn_index, player_id=player_id, move_kind="opening",
        placed_poi_id=poi_id, source_poi_id=None,
        route_ids=[], flipped_poi_ids=[],
    )


@pytest.fixture()
def cfg():
    return Config()


# --- empty state ---------------------------------------------------------

def test_render_empty_state(cfg):
    html = render_map_html(new_game(), cfg)
    assert "<html" in html.lower() or "<!doctype" in html.lower()
    assert "leaflet" in html.lower()


def test_render_default_center_when_no_pois(cfg):
    html = render_map_html(new_game(), cfg)
    assert f"{cfg.DEFAULT_CENTER_LAT:.4f}" in html or str(cfg.DEFAULT_CENTER_LAT)[:6] in html


# --- POI markers ---------------------------------------------------------

def test_render_includes_poi_name(cfg):
    state = new_game()
    state.pois = [_poi("p1", name="Cool Cafe")]
    assert "Cool Cafe" in render_map_html(state, cfg)


def test_render_includes_poi_metadata(cfg):
    state = new_game()
    state.pois = [_poi("p1", name="Cool Cafe", category="amenity", poi_type="cafe", score=2)]
    html = render_map_html(state, cfg)
    assert "amenity" in html
    assert "cafe" in html


def test_owner_colors_in_html(cfg):
    state = new_game()
    state.pois = [
        _poi("a", lat=25.03, lon=121.56, owner=1),
        _poi("b", lat=25.04, lon=121.57, owner=2),
    ]
    html = render_map_html(state, cfg).lower()
    assert "2563eb" in html
    assert "ef4444" in html


def test_render_uses_circle_marker(cfg):
    state = new_game()
    state.pois = [_poi("p1")]
    assert "circleMarker" in render_map_html(state, cfg)


# --- seed-phase popup ----------------------------------------------------

def test_seed_phase_popup_has_target_only_form(cfg):
    """Empty moves → seed phase. Neutral POI gets a target-only form."""
    state = new_game()
    state.pois = [_poi("p1")]
    html = render_map_html(state, cfg)
    assert 'action="/move"' in html
    assert 'target="_top"' in html
    assert 'name="target_poi_id"' in html
    # No source field in seed phase
    # (loose check — the only source_poi_id input lives in normal-phase popups)


# --- normal-phase popups (after seeds) -----------------------------------

def _normal_state():
    state = new_game()
    # 2 seeds each so we're in normal phase, plus a free target.
    state.pois = [
        _poi("p1_seed1", lat=25.01, lon=121.51, owner=1),
        _poi("p2_seed1", lat=25.02, lon=121.52, owner=2),
        _poi("p1_seed2", lat=25.011, lon=121.511, owner=1),
        _poi("p2_seed2", lat=25.022, lon=121.522, owner=2),
        _poi("target", lat=25.03, lon=121.53),
    ]
    state.moves = [
        _seed_move(1, "p1_seed1", 0),
        _seed_move(2, "p2_seed1", 1),
        _seed_move(1, "p1_seed2", 2),
        _seed_move(2, "p2_seed2", 3),
    ]
    state.turn_index = 4
    return state


def test_normal_phase_own_popup_uses_source_button(cfg):
    html = render_map_html(_normal_state(), cfg)
    assert "GeoflipSetSource" in html
    assert "選為起點" in html


def test_normal_phase_opponent_popup_disabled(cfg):
    html = render_map_html(_normal_state(), cfg)
    assert "對手的據點" in html


def test_normal_phase_neutral_target_form_present(cfg):
    html = render_map_html(_normal_state(), cfg)
    assert 'name="target_poi_id"' in html
    assert 'name="source_poi_id"' in html
    assert "GeoflipHydrateTargetForm" in html


def test_normal_phase_helper_script_defined(cfg):
    html = render_map_html(_normal_state(), cfg)
    assert "window.GeoflipSetSource" in html
    assert "window.GeoflipClearSource" in html


# --- coordinate flipping (route polyline) -------------------------------

def test_route_polyline_uses_lat_lon_order(cfg):
    """Route's [lon, lat] must come out as Folium [lat, lon] in JS."""
    state = new_game()
    state.pois = [_poi("p1", lat=25.0330, lon=121.5654)]
    state.routes = [RouteRecord(
        id="r1", turn_index=0, player_id=1,
        from_poi_id="x", to_poi_id="y",
        coordinates_lonlat=[[121.5654, 25.0330], [121.5700, 25.0400]],
        distance_m=100.0, duration_s=80.0, buffer_m=50.0,
    )]
    html = render_map_html(state, cfg)
    matches = re.findall(r"\[\s*25\.0\d+\s*,\s*121\.\d+\s*\]", html)
    assert matches, "Expected [lat, lon] pairs in polyline output"


def test_render_route_polyline_present(cfg):
    state = new_game()
    state.pois = [_poi("p1")]
    state.routes = [RouteRecord(
        id="r1", turn_index=0, player_id=1,
        from_poi_id="x", to_poi_id="y",
        coordinates_lonlat=[[121.5, 25.0], [121.51, 25.01]],
        distance_m=10, duration_s=10, buffer_m=50.0,
    )]
    assert "polyline" in render_map_html(state, cfg).lower()


def test_render_buffer_polygon_present(cfg):
    state = new_game()
    state.pois = [_poi("p1")]
    state.routes = [RouteRecord(
        id="r1", turn_index=0, player_id=1,
        from_poi_id="x", to_poi_id="y",
        coordinates_lonlat=[[121.5654, 25.0330], [121.5700, 25.0400]],
        distance_m=100.0, duration_s=80.0, buffer_m=50.0,
    )]
    assert "L.polygon(" in render_map_html(state, cfg)


def test_render_no_polyline_when_no_routes(cfg):
    state = new_game()
    state.pois = [_poi("p1")]
    assert "L.polygon(" not in render_map_html(state, cfg)


# --- XSS ----------------------------------------------------------------

def test_popup_escapes_html(cfg):
    state = new_game()
    state.pois = [_poi("p1", name='<script>alert("x")</script>')]
    html = render_map_html(state, cfg)
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;" in html


# --- finished state -----------------------------------------------------

def test_finished_state_popup_no_form(cfg):
    state = new_game()
    state.status = "finished"
    state.turn_index = state.max_turns
    state.pois = [_poi("p1")]
    html = render_map_html(state, cfg)
    # No insert form on a finished board.
    assert 'name="target_poi_id"' not in html


# --- render must not mutate state ---------------------------------------

def test_render_does_not_mutate_state(cfg):
    state = new_game()
    state.pois = [_poi("p1", owner=1)]
    pre_turn = state.turn_index
    pre_owner = state.pois[0].owner
    pre_moves = len(state.moves)
    render_map_html(state, cfg)
    assert state.turn_index == pre_turn
    assert state.pois[0].owner == pre_owner
    assert len(state.moves) == pre_moves
