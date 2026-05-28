"""Tests for app/game/rules.py — RulesEngine: seed / normal / pass / blocker buffer."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.game.rules import BUFFER_NORMAL_M, RulesEngine
from app.models import OPENING_MOVES_PER_PLAYER, Poi, RouteResult
from app.services.geometry import build_meter_transformers
from app.state import new_game


# ---------------------------------------------------------------------------
# Fixture helpers — generate WGS84 coords from METER offsets via pyproj
# ---------------------------------------------------------------------------

BASE_LAT = 25.0330
BASE_LON = 121.5654
_TO_M, _TO_WGS = build_meter_transformers(BASE_LON, BASE_LAT)
_BASE_X, _BASE_Y = _TO_M.transform(BASE_LON, BASE_LAT)


def lonlat_at_offset(east_m: float, north_m: float) -> tuple[float, float]:
    lon, lat = _TO_WGS.transform(_BASE_X + east_m, _BASE_Y + north_m)
    return lon, lat


def make_poi(
    poi_id: str,
    east_m: float = 0.0,
    north_m: float = 0.0,
    *,
    category: str = "amenity",
    poi_type: str = "cafe",
    owner=None,
    score: int = 2,
    name: str | None = None,
) -> Poi:
    lon, lat = lonlat_at_offset(east_m, north_m)
    return Poi(
        id=poi_id,
        name=name or poi_id,
        lat=lat,
        lon=lon,
        osm_type="node",
        osm_id=hash(poi_id) & 0xFFFFFFFF,
        category=category,
        poi_type=poi_type,
        score=score,
        owner=owner,
        discovered_turn=0,
        placed_turn=None,
        raw={},
    )


def route_lonlat_between(a: Poi, b: Poi, n_points: int = 5) -> list[list[float]]:
    ax, ay = _TO_M.transform(a.lon, a.lat)
    bx, by = _TO_M.transform(b.lon, b.lat)
    coords: list[list[float]] = []
    for i in range(n_points):
        t = i / (n_points - 1)
        lon, lat = _TO_WGS.transform(ax + (bx - ax) * t, ay + (by - ay) * t)
        coords.append([lon, lat])
    return coords


@dataclass
class FakeRoutingService:
    """Lookup-table routing service. Returns RouteResult / raises configured exc."""
    responses: dict = field(default_factory=dict)
    call_log: list[tuple] = field(default_factory=list)

    def _key(self, fl, fn, tl, tn):
        return (round(fl, 7), round(fn, 7), round(tl, 7), round(tn, 7))

    def add(self, src: Poi, tgt: Poi, duration_s: float, distance_m=None, n_points=5):
        coords = route_lonlat_between(src, tgt, n_points=n_points)
        if distance_m is None:
            distance_m = duration_s * 1.4
        rr = RouteResult(coords, distance_m, duration_s)
        self.responses[self._key(src.lat, src.lon, tgt.lat, tgt.lon)] = rr
        return rr

    def add_failure(self, src: Poi, tgt: Poi, exc: Exception):
        self.responses[self._key(src.lat, src.lon, tgt.lat, tgt.lon)] = exc

    def route(self, fl, fn, tl, tn) -> RouteResult:
        self.call_log.append((fl, fn, tl, tn))
        key = self._key(fl, fn, tl, tn)
        if key not in self.responses:
            raise RuntimeError(f"unexpected route call {key}")
        val = self.responses[key]
        if isinstance(val, Exception):
            raise val
        return val


def _state_with(*pois: Poi):
    state = new_game()
    state.pois = list(pois)
    return state


def _seed_to_normal_phase(engine, state):
    """Drive both players through their seed placements so the next call is a
    normal move for P1. Caller supplies enough neutral POIs (seeds_p1_*, seeds_p2_*)."""
    for i in range(OPENING_MOVES_PER_PLAYER):
        for player_offset in (1, 2):
            poi_id = f"seed_p{player_offset}_{i}"
            result = engine.apply_move(state, poi_id, FakeRoutingService())
            assert result.ok, f"seed {poi_id} failed: {result.message}"
            state = result.state
    return state


# ---------------------------------------------------------------------------
# Seed phase
# ---------------------------------------------------------------------------

def test_seed_first_move_is_seed_kind():
    state = _state_with(make_poi("a", east_m=0))
    res = RulesEngine().apply_move(state, "a", FakeRoutingService())
    assert res.ok
    assert res.state.get_poi("a").owner == 1
    assert res.state.turn_index == 1
    assert res.state.routes == []
    assert res.state.moves[-1].move_kind == "opening"
    assert res.state.moves[-1].source_poi_id is None
    assert res.flipped_poi_ids == []


def test_seed_does_not_call_osrm():
    state = _state_with(make_poi("a"))
    routing = FakeRoutingService()
    res = RulesEngine().apply_move(state, "a", routing)
    assert res.ok
    assert routing.call_log == []


def test_seed_cannot_be_on_owned_poi():
    state = _state_with(make_poi("a", owner=2))
    res = RulesEngine().apply_move(state, "a", FakeRoutingService())
    assert not res.ok
    assert "擁有" in res.message


def test_seed_phase_rejects_source_argument():
    s1 = make_poi("s1")
    state = _state_with(s1, make_poi("a", east_m=10))
    res = RulesEngine().apply_move(state, "a", FakeRoutingService(), source_poi_id="s1")
    assert not res.ok
    assert "開局" in res.message or "source" in res.message.lower()


# ---------------------------------------------------------------------------
# Transition from seed → normal
# ---------------------------------------------------------------------------

def test_after_seed_phase_normal_move_requires_source():
    state = _state_with(
        make_poi("seed_p1_0", east_m=0),
        make_poi("seed_p2_0", east_m=5000),
        make_poi("seed_p1_1", east_m=10),
        make_poi("seed_p2_1", east_m=5010),
        make_poi("target", east_m=200),
    )
    engine = RulesEngine()
    state = _seed_to_normal_phase(engine, state)

    # Now P1's first normal move. Omitting source should fail.
    res = engine.apply_move(state, "target", FakeRoutingService())
    assert not res.ok
    assert "起點" in res.message or "source" in res.message.lower()


def test_normal_move_source_must_be_owned_by_current_player():
    state = _state_with(
        make_poi("seed_p1_0", east_m=0),
        make_poi("seed_p2_0", east_m=5000),
        make_poi("seed_p1_1", east_m=10),
        make_poi("seed_p2_1", east_m=5010),
        make_poi("target", east_m=200),
    )
    engine = RulesEngine()
    state = _seed_to_normal_phase(engine, state)
    # P1 tries to use a P2 seed as source.
    res = engine.apply_move(
        state, "target", FakeRoutingService(), source_poi_id="seed_p2_0"
    )
    assert not res.ok


def test_normal_move_target_must_be_neutral():
    state = _state_with(
        make_poi("seed_p1_0", east_m=0),
        make_poi("seed_p2_0", east_m=5000),
        make_poi("seed_p1_1", east_m=10),
        make_poi("seed_p2_1", east_m=5010),
        make_poi("spare", east_m=400),  # keep at least one neutral after seeds
    )
    engine = RulesEngine()
    state = _seed_to_normal_phase(engine, state)
    res = engine.apply_move(
        state, "seed_p2_0", FakeRoutingService(), source_poi_id="seed_p1_0"
    )
    assert not res.ok
    assert "擁有" in res.message


def test_normal_move_source_target_must_differ():
    state = _state_with(
        make_poi("seed_p1_0", east_m=0),
        make_poi("seed_p2_0", east_m=5000),
        make_poi("seed_p1_1", east_m=10),
        make_poi("seed_p2_1", east_m=5010),
        make_poi("spare", east_m=400),
    )
    engine = RulesEngine()
    state = _seed_to_normal_phase(engine, state)
    res = engine.apply_move(
        state, "seed_p1_0", FakeRoutingService(), source_poi_id="seed_p1_0"
    )
    assert not res.ok


# ---------------------------------------------------------------------------
# Normal move — OSRM duration limits + target flip
# ---------------------------------------------------------------------------

def _normal_state(*extra_pois: Poi):
    pois = [
        make_poi("seed_p1_0", east_m=0),
        make_poi("seed_p2_0", east_m=5000),
        make_poi("seed_p1_1", east_m=10),
        make_poi("seed_p2_1", east_m=5010),
        *extra_pois,
    ]
    state = _state_with(*pois)
    engine = RulesEngine()
    state = _seed_to_normal_phase(engine, state)
    return state, engine


def test_normal_move_over_600s_invalid():
    state, engine = _normal_state(make_poi("target", east_m=300))
    routing = FakeRoutingService()
    routing.add(state.get_poi("seed_p1_0"), state.get_poi("target"), duration_s=601.0)
    res = engine.apply_move(
        state, "target", routing, source_poi_id="seed_p1_0"
    )
    assert not res.ok


def test_normal_move_within_600s_target_flips():
    state, engine = _normal_state(make_poi("target", east_m=300))
    routing = FakeRoutingService()
    routing.add(state.get_poi("seed_p1_0"), state.get_poi("target"), duration_s=200.0)
    res = engine.apply_move(state, "target", routing, source_poi_id="seed_p1_0")
    assert res.ok
    assert res.state.get_poi("target").owner == 1
    assert len(res.state.routes) == 1
    assert res.state.routes[-1].buffer_m == BUFFER_NORMAL_M


def test_invalid_normal_move_does_not_mutate_state():
    state, engine = _normal_state(make_poi("target", east_m=300))
    snap_turn = state.turn_index
    snap_owners = {p.id: p.owner for p in state.pois}
    snap_routes = len(state.routes)
    snap_moves = len(state.moves)

    routing = FakeRoutingService()
    routing.add(state.get_poi("seed_p1_0"), state.get_poi("target"), duration_s=999.0)
    res = engine.apply_move(state, "target", routing, source_poi_id="seed_p1_0")
    assert not res.ok
    assert state.turn_index == snap_turn
    assert {p.id: p.owner for p in state.pois} == snap_owners
    assert len(state.routes) == snap_routes
    assert len(state.moves) == snap_moves


# ---------------------------------------------------------------------------
# Blocker buffer rule
# ---------------------------------------------------------------------------

def test_blocker_no_neutral_in_buffer_flips_opponent():
    """Buffer has only opponent + own POIs → opponent ones flip."""
    # Route: seed_p1_0 (east_m=0) → target (east_m=300). All extras on/near
    # the line are P2-owned at 30m north (inside 50m buffer).
    opp_near = make_poi("opp_near", east_m=150, north_m=30, owner=2)
    opp_far = make_poi("opp_far", east_m=150, north_m=200, owner=2)  # outside buffer
    state, engine = _normal_state(
        make_poi("target", east_m=300), opp_near, opp_far
    )
    routing = FakeRoutingService()
    routing.add(state.get_poi("seed_p1_0"), state.get_poi("target"), duration_s=250.0)
    res = engine.apply_move(state, "target", routing, source_poi_id="seed_p1_0")
    assert res.ok
    assert set(res.flipped_poi_ids) == {"opp_near"}
    assert res.state.get_poi("opp_near").owner == 1
    assert res.state.get_poi("opp_far").owner == 2
    assert res.state.moves[-1].move_kind == "flip"


def test_blocker_neutral_in_buffer_only_target_flips():
    """A neutral POI sitting in the buffer blocks all opponent flips."""
    blocker = make_poi("blocker", east_m=150, north_m=20, owner=None)
    opp = make_poi("opp", east_m=200, north_m=10, owner=2)
    state, engine = _normal_state(
        make_poi("target", east_m=300), blocker, opp
    )
    routing = FakeRoutingService()
    routing.add(state.get_poi("seed_p1_0"), state.get_poi("target"), duration_s=250.0)
    res = engine.apply_move(state, "target", routing, source_poi_id="seed_p1_0")
    assert res.ok
    assert res.flipped_poi_ids == []
    assert res.state.get_poi("target").owner == 1  # target still flips
    assert res.state.get_poi("opp").owner == 2  # opponent not flipped
    assert res.state.get_poi("blocker").owner is None  # blocker stays neutral
    assert res.state.moves[-1].move_kind == "route"


def test_own_poi_in_buffer_does_not_block():
    """An own POI sitting between source and target should not block."""
    own = make_poi("own", east_m=150, north_m=20, owner=1)
    opp = make_poi("opp", east_m=200, north_m=10, owner=2)
    state, engine = _normal_state(
        make_poi("target", east_m=300), own, opp
    )
    routing = FakeRoutingService()
    routing.add(state.get_poi("seed_p1_0"), state.get_poi("target"), duration_s=250.0)
    res = engine.apply_move(state, "target", routing, source_poi_id="seed_p1_0")
    assert res.ok
    assert "opp" in res.flipped_poi_ids
    assert res.state.get_poi("own").owner == 1  # unchanged


def test_source_and_target_excluded_from_buffer_scan():
    """Buffer scan should not iterate over source / target themselves."""
    state, engine = _normal_state(make_poi("target", east_m=100))
    routing = FakeRoutingService()
    routing.add(state.get_poi("seed_p1_0"), state.get_poi("target"), duration_s=100.0)
    res = engine.apply_move(state, "target", routing, source_poi_id="seed_p1_0")
    assert res.ok
    # source must still be owned by player 1 (it's the seed), target now owned by 1.
    assert res.state.get_poi("seed_p1_0").owner == 1
    assert res.state.get_poi("target").owner == 1
    assert res.state.moves[-1].source_poi_id == "seed_p1_0"
    assert res.state.moves[-1].placed_poi_id == "target"


# ---------------------------------------------------------------------------
# Pass
# ---------------------------------------------------------------------------

def test_pass_consumes_turn_and_records_kind():
    state = _state_with(make_poi("a"))
    engine = RulesEngine()
    res = engine.apply_pass(state)
    assert res.ok
    assert res.state.turn_index == 1
    assert res.state.moves[-1].move_kind == "pass"
    assert res.state.moves[-1].placed_poi_id is None
    assert res.state.moves[-1].source_poi_id is None
    assert res.state.routes == []


def test_two_consecutive_passes_end_game():
    state = _state_with(make_poi("a"), make_poi("b", east_m=50))
    engine = RulesEngine()
    state = engine.apply_pass(state).state
    res = engine.apply_pass(state)
    assert res.ok
    assert res.state.status == "finished"


def test_pass_on_finished_invalid():
    state = _state_with(make_poi("a"))
    state.status = "finished"
    res = RulesEngine().apply_pass(state)
    assert not res.ok


# ---------------------------------------------------------------------------
# Game end conditions
# ---------------------------------------------------------------------------

def test_no_neutral_pois_finishes_game():
    """If a move leaves zero neutral POIs, the game ends."""
    # Setup: 4 seeds (2 per player) + 1 final neutral target. After P1 captures
    # it, the game should auto-finish (no neutrals left, max_turns not reached).
    state = _state_with(
        make_poi("seed_p1_0", east_m=0),
        make_poi("seed_p2_0", east_m=5000),
        make_poi("seed_p1_1", east_m=10),
        make_poi("seed_p2_1", east_m=5010),
        make_poi("last", east_m=100),
    )
    engine = RulesEngine()
    state = _seed_to_normal_phase(engine, state)
    routing = FakeRoutingService()
    routing.add(state.get_poi("seed_p1_0"), state.get_poi("last"), duration_s=80.0)
    res = engine.apply_move(state, "last", routing, source_poi_id="seed_p1_0")
    assert res.ok
    assert res.state.status == "finished"


def test_move_after_finished_invalid():
    state = _state_with(make_poi("a"))
    state.status = "finished"
    res = RulesEngine().apply_move(state, "a", FakeRoutingService())
    assert not res.ok
    assert "結束" in res.message


# ---------------------------------------------------------------------------
# RouteRecord invariants
# ---------------------------------------------------------------------------

def test_seed_move_creates_no_route_record():
    state = _state_with(make_poi("a"))
    res = RulesEngine().apply_move(state, "a", FakeRoutingService())
    assert res.ok
    assert res.state.routes == []
    assert res.state.moves[-1].route_ids == []


def test_normal_move_route_record_id_format_and_count():
    state, engine = _normal_state(make_poi("target", east_m=300))
    routing = FakeRoutingService()
    routing.add(state.get_poi("seed_p1_0"), state.get_poi("target"), duration_s=200.0)
    res = engine.apply_move(state, "target", routing, source_poi_id="seed_p1_0")
    assert res.ok
    assert len(res.state.routes) == 1
    assert res.state.routes[-1].id.startswith("route_")
    assert len(res.state.moves[-1].route_ids) == 1


def test_normal_move_kind_is_route_when_no_flip():
    """No opponent POI near the route → move_kind = 'route' not 'flip'."""
    state, engine = _normal_state(make_poi("target", east_m=300))
    routing = FakeRoutingService()
    routing.add(state.get_poi("seed_p1_0"), state.get_poi("target"), duration_s=200.0)
    res = engine.apply_move(state, "target", routing, source_poi_id="seed_p1_0")
    assert res.ok
    assert res.state.moves[-1].move_kind == "route"
