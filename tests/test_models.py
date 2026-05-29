"""Tests for app/models.py — domain model + GameState helpers."""
import pytest

from app.models import (
    OPENING_MOVES_PER_PLAYER,
    GameState,
    MoveRecord,
    Poi,
    RouteRecord,
    score_poi,
)
from app.state import new_game


def _make_poi(
    poi_id: str = "p1",
    name: str = "Test POI",
    lat: float = 25.033,
    lon: float = 121.565,
    category: str = "amenity",
    poi_type: str = "cafe",
    score: int = 2,
    owner=None,
    discovered_turn: int | None = 0,
    placed_turn: int | None = None,
) -> Poi:
    return Poi(
        id=poi_id,
        name=name,
        lat=lat,
        lon=lon,
        osm_type="node",
        osm_id=123,
        category=category,
        poi_type=poi_type,
        score=score,
        owner=owner,
        discovered_turn=discovered_turn,
        placed_turn=placed_turn,
        raw={"display_name": name},
    )


def _seed_move(player_id, placed_poi_id, turn_index=0) -> MoveRecord:
    return MoveRecord(
        turn_index=turn_index,
        player_id=player_id,
        move_kind="opening",
        placed_poi_id=placed_poi_id,
        source_poi_id=None,
        route_ids=[],
        flipped_poi_ids=[],
    )


def _pass_move(player_id, turn_index=0) -> MoveRecord:
    return MoveRecord(
        turn_index=turn_index,
        player_id=player_id,
        move_kind="pass",
        placed_poi_id=None,
        source_poi_id=None,
        route_ids=[],
        flipped_poi_ids=[],
    )


# --- new_game basics -------------------------------------------------------

def test_new_game_current_player_is_1():
    assert new_game().current_player_id() == 1


def test_new_game_game_id_prefix():
    assert new_game().game_id.startswith("game_")


def test_new_game_status_active():
    state = new_game()
    assert state.status == "active"
    assert not state.is_finished()


def test_new_game_12_max_turns():
    assert new_game().max_turns == 12


# --- finished / current player --------------------------------------------

def test_is_finished_after_max_turns():
    state = new_game()
    state.turn_index = state.max_turns
    assert state.is_finished()


def test_current_player_odd_turn_is_2():
    state = new_game()
    state.turn_index = 1
    assert state.current_player_id() == 2


def test_current_player_even_turn_is_1():
    state = new_game()
    state.turn_index = 4
    assert state.current_player_id() == 1


# --- score_poi -------------------------------------------------------------

def test_score_poi_historic_is_3():
    assert score_poi("historic", "castle") == 3


def test_score_poi_amenity_2_types():
    assert score_poi("amenity", "cafe") == 2
    assert score_poi("amenity", "restaurant") == 2


def test_score_poi_shop_is_2():
    assert score_poi("shop", "bakery") == 2


def test_score_poi_other_is_1():
    assert score_poi("amenity", "parking") == 1
    assert score_poi("unknown", "anything") == 1


# --- scoring / winner ------------------------------------------------------

def test_scores_empty_state():
    assert new_game().scores() == {1: 0, 2: 0}


def test_scores_from_owner():
    state = new_game()
    state.pois = [
        _make_poi("a", score=3, owner=1),
        _make_poi("b", score=2, owner=2),
        _make_poi("c", score=1, owner=None),
        _make_poi("d", score=3, owner=1),
    ]
    assert state.scores() == {1: 6, 2: 2}


def test_scores_update_after_flip():
    state = new_game()
    poi = _make_poi("a", score=3, owner=1)
    state.pois = [poi]
    assert state.scores()[1] == 3
    poi.owner = 2
    assert state.scores() == {1: 0, 2: 3}


def test_winner_none_when_active():
    assert new_game().winner() is None


def test_winner_player1_wins():
    state = new_game()
    state.turn_index = state.max_turns
    state.status = "finished"
    state.pois = [_make_poi("a", score=3, owner=1), _make_poi("b", score=1, owner=2)]
    assert state.winner() == 1


def test_winner_tie_returns_none():
    state = new_game()
    state.turn_index = state.max_turns
    state.status = "finished"
    state.pois = [_make_poi("a", score=2, owner=1), _make_poi("b", score=2, owner=2)]
    assert state.winner() is None


# --- player_move_count / in_opening_phase / last_two_moves_are_passes ---------

def test_player_move_count_counts_only_that_player():
    state = new_game()
    state.moves = [
        _seed_move(1, "a", 0),
        _seed_move(2, "b", 1),
        _seed_move(1, "c", 2),
    ]
    assert state.player_move_count(1) == 2
    assert state.player_move_count(2) == 1


def test_in_opening_phase_true_before_pieces_placed():
    state = new_game()
    assert state.in_opening_phase(1)
    assert state.in_opening_phase(2)


def test_in_opening_phase_false_after_threshold():
    state = new_game()
    state.moves = [_seed_move(1, "a", 0), _seed_move(1, "c", 2)]
    assert not state.in_opening_phase(1)
    # P2 still in seed phase
    assert state.in_opening_phase(2)


def test_seed_moves_per_player_constant_is_2():
    assert OPENING_MOVES_PER_PLAYER == 2


def test_last_two_moves_are_passes_false_when_empty():
    assert not new_game().last_two_moves_are_passes()


def test_last_two_moves_are_passes_true_when_two_passes():
    state = new_game()
    state.moves = [_pass_move(1, 4), _pass_move(2, 5)]
    assert state.last_two_moves_are_passes()


def test_last_two_moves_are_passes_false_when_mixed():
    state = new_game()
    state.moves = [_pass_move(1, 4), _seed_move(2, "x", 5)]
    assert not state.last_two_moves_are_passes()


# --- POI helpers -----------------------------------------------------------

def test_opponent_id():
    s = new_game()
    assert s.opponent_id(1) == 2
    assert s.opponent_id(2) == 1


def test_neutral_pois():
    state = new_game()
    state.pois = [
        _make_poi("a", owner=None),
        _make_poi("b", owner=1),
        _make_poi("c", owner=None),
    ]
    assert {p.id for p in state.neutral_pois()} == {"a", "c"}


def test_get_poi_found_and_missing():
    state = new_game()
    poi = _make_poi("x")
    state.pois = [poi]
    assert state.get_poi("x") is poi
    assert state.get_poi("missing") is None


# --- merge_discovered_pois -------------------------------------------------

def test_merge_new_poi_sets_discovered_turn_and_clears_owner():
    state = new_game()
    state.turn_index = 3
    state.merge_discovered_pois([_make_poi("new", owner=1, placed_turn=9)])
    new = state.get_poi("new")
    assert new.discovered_turn == 3
    assert new.owner is None
    assert new.placed_turn is None


def test_merge_existing_poi_no_overwrite():
    state = new_game()
    state.pois = [_make_poi("p", owner=1, discovered_turn=0)]
    state.merge_discovered_pois([_make_poi("p", owner=None, discovered_turn=99)])
    p = state.get_poi("p")
    assert p.owner == 1
    assert p.discovered_turn == 0


def test_merge_does_not_duplicate():
    state = new_game()
    state.pois = [_make_poi("p")]
    state.merge_discovered_pois([_make_poi("p")])
    assert len(state.pois) == 1


# --- serialization round-trips --------------------------------------------

def test_poi_serialization_roundtrip():
    poi = _make_poi("p1", owner=2, discovered_turn=3, placed_turn=4)
    restored = Poi.from_dict(poi.to_dict())
    assert restored == poi


def test_gamestate_serialization_roundtrip():
    state = new_game()
    state.pois = [_make_poi("a", owner=1), _make_poi("b", owner=None)]
    state.moves = [_seed_move(1, "a", 0), _pass_move(2, 1)]
    restored = GameState.from_dict(state.to_dict())
    assert restored.game_id == state.game_id
    assert len(restored.pois) == 2
    assert len(restored.moves) == 2
    assert restored.moves[0].move_kind == "opening"
    assert restored.moves[1].move_kind == "pass"


def test_from_dict_bad_status_raises():
    state = new_game()
    d = state.to_dict()
    d["status"] = "unknown"
    with pytest.raises(ValueError):
        GameState.from_dict(d)


def test_from_dict_bad_move_kind_raises():
    state = new_game()
    state.moves = [_seed_move(1, "a", 0)]
    d = state.to_dict()
    d["moves"][0]["move_kind"] = "nope"
    with pytest.raises(ValueError):
        GameState.from_dict(d)


def test_move_record_pass_serializes_nones():
    m = _pass_move(2, 5)
    restored = MoveRecord.from_dict(m.to_dict())
    assert restored.move_kind == "pass"
    assert restored.placed_poi_id is None
    assert restored.source_poi_id is None


def test_route_record_serialization_roundtrip():
    rr = RouteRecord(
        id="route_abc",
        turn_index=2,
        player_id=1,
        from_poi_id="p1",
        to_poi_id="p2",
        coordinates_lonlat=[[121.5, 25.0], [121.6, 25.1]],
        distance_m=500.0,
        duration_s=300.0,
        buffer_m=50.0,
    )
    restored = RouteRecord.from_dict(rr.to_dict())
    assert restored == rr
