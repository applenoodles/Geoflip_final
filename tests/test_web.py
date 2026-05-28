"""Tests for app/web.py — Flask routes wired with fake services."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.config import Config
from app.models import MoveRecord, Poi, RouteRecord, RouteResult
from app.services.nominatim import LocationCandidate, NominatimError
from app.services.overpass import OverpassError
from app.state import StateStore, new_game
from app.web import create_app


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class FakeNominatim:
    locations: list[LocationCandidate] = field(default_factory=list)
    location_error: Exception | None = None
    location_calls: list[tuple] = field(default_factory=list)

    def search_locations(self, query: str, limit: int = 5) -> list[LocationCandidate]:
        self.location_calls.append((query, limit))
        if self.location_error is not None:
            raise self.location_error
        return list(self.locations)


@dataclass
class FakeOverpass:
    pois: list[Poi] = field(default_factory=list)
    error: Exception | None = None
    calls: list[tuple] = field(default_factory=list)

    def fetch_board_pois(self, center_lat, center_lon, radius_m, limit=60):
        self.calls.append((center_lat, center_lon, radius_m, limit))
        if self.error is not None:
            raise self.error
        return list(self.pois)


@dataclass
class FakeOsrm:
    routes: dict = field(default_factory=dict)
    calls: list[tuple] = field(default_factory=list)

    def route(self, from_lat, from_lon, to_lat, to_lon) -> RouteResult:
        self.calls.append((from_lat, from_lon, to_lat, to_lon))
        key = (round(from_lat, 6), round(from_lon, 6), round(to_lat, 6), round(to_lon, 6))
        if key in self.routes:
            return self.routes[key]
        raise RuntimeError(f"unexpected route call {key}")


def _make_poi(poi_id: str, lat: float = 25.0330, lon: float = 121.5654, **kw) -> Poi:
    defaults = dict(
        name=f"POI {poi_id}",
        osm_type="node",
        osm_id=abs(hash(poi_id)) & 0xFFFFFFFF,
        category="amenity",
        poi_type="cafe",
        score=2,
        owner=None,
        discovered_turn=None,
        placed_turn=None,
        raw={},
    )
    defaults.update(kw)
    return Poi(id=poi_id, lat=lat, lon=lon, **defaults)


def _seed_move(player_id, poi_id, turn_index=0):
    return MoveRecord(
        turn_index=turn_index, player_id=player_id, move_kind="opening",
        placed_poi_id=poi_id, source_poi_id=None,
        route_ids=[], flipped_poi_ids=[],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def deps(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    config = Config()
    config.OVERPASS_MIN_POIS = 2
    config.OVERPASS_MAX_POIS = 36
    return {
        "state_store": state_store,
        "nominatim": FakeNominatim(),
        "osrm": FakeOsrm(),
        "overpass": FakeOverpass(),
        "config": config,
    }


@pytest.fixture()
def app(deps):
    app = create_app(
        config=deps["config"],
        state_store=deps["state_store"],
        nominatim_client=deps["nominatim"],
        osrm_client=deps["osrm"],
        overpass_client=deps["overpass"],
    )
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

def test_index_empty_state_renders_setup(client, deps):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b'action="/setup/search"' in resp.data
    assert b"<iframe" not in resp.data


def test_index_with_board_renders_game(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<iframe" in resp.data
    assert b'action="/setup/search"' not in resp.data


def test_index_seed_phase_banner_visible(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)
    resp = client.get("/")
    assert "開局階段".encode("utf-8") in resp.data


def test_index_normal_phase_shows_source_status_and_pass(client, deps):
    state = new_game()
    state.pois = [
        _make_poi("a", lat=25.01, lon=121.51, owner=1),
        _make_poi("b", lat=25.02, lon=121.52, owner=2),
        _make_poi("c", lat=25.03, lon=121.53, owner=1),
        _make_poi("d", lat=25.04, lon=121.54, owner=2),
        _make_poi("e", lat=25.05, lon=121.55),
    ]
    state.moves = [
        _seed_move(1, "a", 0),
        _seed_move(2, "b", 1),
        _seed_move(1, "c", 2),
        _seed_move(2, "d", 3),
    ]
    state.turn_index = 4
    deps["state_store"].save(state)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "連線回合".encode("utf-8") in resp.data
    assert b'id="source-status"' in resp.data
    assert b'action="/pass"' in resp.data


def test_sidebar_has_no_flag_form(client, deps):
    """Flag UI lives in map popups (the iframe), never in sidebar HTML."""
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)
    resp = client.get("/")
    assert b'name="target_poi_id"' not in resp.data
    assert b'name="source_poi_id"' not in resp.data


# ---------------------------------------------------------------------------
# POST /move — seed phase
# ---------------------------------------------------------------------------

def test_move_seed_success_saves_state(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.post("/move", data={"target_poi_id": "p1"})
    assert resp.status_code == 302

    saved = deps["state_store"].load()
    assert saved.turn_index == 1
    assert saved.get_poi("p1").owner == 1
    assert saved.moves[-1].move_kind == "opening"
    assert deps["osrm"].calls == []  # seed doesn't call OSRM


def test_move_invalid_does_not_save(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1", owner=2)]
    deps["state_store"].save(state)

    client.post("/move", data={"target_poi_id": "p1"})
    after = deps["state_store"].load()
    assert after.turn_index == 0
    assert after.get_poi("p1").owner == 2


def test_move_missing_target_redirects_with_flash(client, deps):
    resp = client.post("/move", data={})
    assert resp.status_code == 302


def test_move_does_not_accept_latlon_from_form(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1", lat=25.0330, lon=121.5654)]
    deps["state_store"].save(state)

    client.post("/move", data={
        "target_poi_id": "p1", "lat": "0.0", "lon": "0.0",
    })
    after = deps["state_store"].load()
    p = after.get_poi("p1")
    assert p.lat == 25.0330
    assert p.lon == 121.5654


# ---------------------------------------------------------------------------
# POST /move — normal phase
# ---------------------------------------------------------------------------

def test_move_normal_with_source_and_target(client, deps):
    # Past seed phase: P1 and P2 each have placed 2 seeds.
    state = new_game()
    state.pois = [
        _make_poi("a", lat=25.0330, lon=121.5654, owner=1),
        _make_poi("b", lat=25.0500, lon=121.5800, owner=2),
        _make_poi("c", lat=25.0335, lon=121.5660, owner=1),
        _make_poi("d", lat=25.0505, lon=121.5805, owner=2),
        _make_poi("target", lat=25.0340, lon=121.5670),
    ]
    state.moves = [
        _seed_move(1, "a", 0), _seed_move(2, "b", 1),
        _seed_move(1, "c", 2), _seed_move(2, "d", 3),
    ]
    state.turn_index = 4
    deps["state_store"].save(state)

    a = state.get_poi("a")
    target = state.get_poi("target")
    deps["osrm"].routes[
        (round(a.lat, 6), round(a.lon, 6), round(target.lat, 6), round(target.lon, 6))
    ] = RouteResult(
        coordinates_lonlat=[[a.lon, a.lat], [target.lon, target.lat]],
        distance_m=100.0, duration_s=120.0,
    )

    resp = client.post("/move", data={
        "target_poi_id": "target", "source_poi_id": "a",
    })
    assert resp.status_code == 302

    after = deps["state_store"].load()
    assert after.get_poi("target").owner == 1
    assert after.turn_index == 5
    assert after.moves[-1].source_poi_id == "a"
    assert after.moves[-1].placed_poi_id == "target"
    assert len(after.routes) == 1


def test_move_normal_without_source_invalid(client, deps):
    state = new_game()
    state.pois = [
        _make_poi("a", lat=25.0330, lon=121.5654, owner=1),
        _make_poi("b", lat=25.0500, lon=121.5800, owner=2),
        _make_poi("c", lat=25.0335, lon=121.5660, owner=1),
        _make_poi("d", lat=25.0505, lon=121.5805, owner=2),
        _make_poi("target", lat=25.0340, lon=121.5670),
    ]
    state.moves = [
        _seed_move(1, "a", 0), _seed_move(2, "b", 1),
        _seed_move(1, "c", 2), _seed_move(2, "d", 3),
    ]
    state.turn_index = 4
    deps["state_store"].save(state)

    client.post("/move", data={"target_poi_id": "target"})
    after = deps["state_store"].load()
    assert after.turn_index == 4
    assert after.get_poi("target").owner is None


# ---------------------------------------------------------------------------
# POST /pass
# ---------------------------------------------------------------------------

def test_pass_consumes_turn(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)

    resp = client.post("/pass")
    assert resp.status_code == 302
    after = deps["state_store"].load()
    assert after.turn_index == 1
    assert after.moves[-1].move_kind == "pass"


# ---------------------------------------------------------------------------
# POST /new-game
# ---------------------------------------------------------------------------

def test_new_game_resets_state(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1", owner=1)]
    state.turn_index = 5
    deps["state_store"].save(state)

    resp = client.post("/new-game", follow_redirects=True)
    assert resp.status_code == 200
    assert b'action="/setup/search"' in resp.data

    fresh = deps["state_store"].load()
    assert fresh.turn_index == 0
    assert fresh.pois == []


# ---------------------------------------------------------------------------
# GET /map
# ---------------------------------------------------------------------------

def test_create_app_rejects_max_turns_below_opening_total():
    config = Config(GAME_MAX_TURNS=3, GAME_OPENING_MOVES_PER_PLAYER=2)
    with pytest.raises(ValueError, match="GAME_MAX_TURNS"):
        create_app(config=config)


def test_create_app_accepts_max_turns_at_opening_total():
    config = Config(GAME_MAX_TURNS=4, GAME_OPENING_MOVES_PER_PLAYER=2)
    create_app(config=config)


def test_map_returns_html(client):
    resp = client.get("/map")
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()


def test_map_page_includes_source_helper_script(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)
    resp = client.get("/map")
    assert b"GeoflipSetSource" in resp.data
    assert b"GeoflipHydrateTargetForm" in resp.data


# ---------------------------------------------------------------------------
# GET /api/state
# ---------------------------------------------------------------------------

def test_api_state_returns_json(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)
    resp = client.get("/api/state")
    data = resp.get_json()
    assert data["status"] == "active"
    assert data["turn_index"] == 0
    assert any(p["id"] == "p1" for p in data["pois"])


def test_api_state_fresh_when_no_file(client, deps):
    data = client.get("/api/state").get_json()
    assert data["turn_index"] == 0
    assert data["status"] == "active"


# ---------------------------------------------------------------------------
# Setup flow
# ---------------------------------------------------------------------------

def test_setup_search_calls_nominatim_search_locations(client, deps):
    deps["nominatim"].locations = [
        LocationCandidate(
            display_name="新竹車站, 新竹市",
            lat=24.8019, lon=120.9716,
            osm_type="node", osm_id=42,
            category="railway", poi_type="station",
        ),
    ]
    resp = client.post("/setup/search", data={"q": "新竹車站"})
    assert resp.status_code == 200
    assert deps["nominatim"].location_calls == [("新竹車站", 5)]
    assert b'action="/setup/start"' in resp.data
    assert "新竹車站".encode("utf-8") in resp.data


def test_setup_search_empty_query_flashes(client, deps):
    resp = client.post("/setup/search", data={"q": "   "})
    assert resp.status_code == 200
    assert deps["nominatim"].location_calls == []
    assert "請輸入起始地點關鍵字".encode("utf-8") in resp.data


def test_setup_search_no_results_info(client, deps):
    resp = client.post("/setup/search", data={"q": "asdfqwerty"})
    assert "找不到符合的地點".encode("utf-8") in resp.data


def test_setup_search_nominatim_error_flash(client, deps):
    deps["nominatim"].location_error = NominatimError("nominatim 503")
    resp = client.post("/setup/search", data={"q": "anywhere"})
    assert "搜尋失敗".encode("utf-8") in resp.data


def _board_pois(n):
    return [
        _make_poi(f"b{i}", lat=25.0 + i * 0.0001, lon=121.5 + i * 0.0001)
        for i in range(n)
    ]


def test_setup_start_fetches_overpass_and_populates_state(client, deps):
    deps["overpass"].pois = _board_pois(5)
    resp = client.post("/setup/start", data={
        "lat": "24.8019", "lon": "120.9716",
        "display_name": "新竹車站", "radius_m": "900",
    })
    assert resp.status_code == 302
    call = deps["overpass"].calls[0]
    assert call[:3] == (24.8019, 120.9716, 900.0)

    state = deps["state_store"].load()
    assert len(state.pois) == 5
    assert state.turn_index == 0


def test_setup_start_too_few_pois(client, deps):
    deps["config"].OVERPASS_MIN_POIS = 5
    deps["overpass"].pois = _board_pois(2)
    client.post("/setup/start", data={
        "lat": "24.8", "lon": "120.97", "display_name": "X",
    })
    state = deps["state_store"].load()
    assert state.pois == []


def test_setup_start_overpass_error_keeps_state_empty(client, deps):
    deps["overpass"].error = OverpassError("overpass down")
    client.post("/setup/start", data={
        "lat": "24.8", "lon": "120.97", "display_name": "X",
    })
    state = deps["state_store"].load()
    assert state.pois == []


def test_setup_start_invalid_coords(client, deps):
    client.post("/setup/start", data={"lat": "nope", "lon": "abc"})
    assert deps["overpass"].calls == []


# ---------------------------------------------------------------------------
# Flash auto-dismiss markers
# ---------------------------------------------------------------------------

def test_index_has_flash_auto_dismiss_script(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)
    resp = client.get("/")
    assert b"data-auto-dismiss" in resp.data


def test_error_flash_is_not_auto_dismissed(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)
    resp = client.post("/move", data={}, follow_redirects=True)
    assert b'class="flash error"' in resp.data
    assert b'class="flash error" data-auto-dismiss' not in resp.data


# ---------------------------------------------------------------------------
# End-of-game summary
# ---------------------------------------------------------------------------

def test_summary_absent_when_active(client, deps):
    state = new_game()
    state.pois = [_make_poi("p1")]
    deps["state_store"].save(state)
    resp = client.get("/")
    assert "對局總結".encode("utf-8") not in resp.data


def test_summary_when_finished(client, deps):
    state = new_game()
    state.pois = [
        _make_poi("a", owner=1, lat=25.01, lon=121.51, score=3),
        _make_poi("b", owner=2, lat=25.02, lon=121.52, score=1),
    ]
    state.status = "finished"
    state.moves = [_seed_move(1, "a", 0), _seed_move(2, "b", 1)]
    deps["state_store"].save(state)

    resp = client.get("/")
    body = resp.data
    assert "對局總結".encode("utf-8") in body
    assert "Player 1 勝利".encode("utf-8") in body


def test_summary_shows_tie(client, deps):
    state = new_game()
    state.pois = [
        _make_poi("a", owner=1, lat=25.01, lon=121.51),
        _make_poi("b", owner=2, lat=25.02, lon=121.52),
    ]
    state.status = "finished"
    state.moves = [_seed_move(1, "a", 0), _seed_move(2, "b", 1)]
    deps["state_store"].save(state)
    resp = client.get("/")
    assert "平手".encode("utf-8") in resp.data


# ---------------------------------------------------------------------------
# DI defaults
# ---------------------------------------------------------------------------

def test_create_app_no_args_works(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    from importlib import reload
    import app.config as cfg_mod
    reload(cfg_mod)
    from app.web import create_app as ca
    app = ca()
    assert app is not None
    with app.test_client() as c:
        assert c.get("/health").status_code == 200
