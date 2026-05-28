"""Tests for app/state.py — new_game(), StateStore load/save/reset."""
import json
import pytest
from pathlib import Path

from app.state import StateStore, new_game
from app.models import Poi


def _make_poi(poi_id: str, owner=None) -> Poi:
    return Poi(
        id=poi_id,
        name="Test",
        lat=25.033,
        lon=121.565,
        osm_type="node",
        osm_id=1,
        category="amenity",
        poi_type="cafe",
        score=2,
        owner=owner,
        discovered_turn=0,
        placed_turn=None,
        raw={},
    )


# ---------------------------------------------------------------------------
# new_game()
# ---------------------------------------------------------------------------

def test_new_game_returns_game_state():
    from app.models import GameState
    state = new_game()
    assert isinstance(state, GameState)


def test_new_game_current_player_is_1():
    state = new_game()
    assert state.current_player_id() == 1


def test_new_game_game_id_starts_with_game():
    state = new_game()
    assert state.game_id.startswith("game_")


def test_new_game_ids_are_unique():
    ids = {new_game().game_id for _ in range(20)}
    assert len(ids) == 20


def test_new_game_status_is_active():
    state = new_game()
    assert state.status == "active"


def test_new_game_turn_index_zero():
    state = new_game()
    assert state.turn_index == 0


# ---------------------------------------------------------------------------
# StateStore — load
# ---------------------------------------------------------------------------

def test_statestore_load_creates_new_game_if_not_exists(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = store.load()
    assert state.status == "active"
    assert state.turn_index == 0


def test_statestore_load_returns_new_game_each_time_when_missing(tmp_path):
    store = StateStore(tmp_path / "state.json")
    s1 = store.load()
    s2 = store.load()
    # Both should be valid; IDs differ because new_game() generates fresh UUIDs
    assert s1.game_id.startswith("game_")
    assert s2.game_id.startswith("game_")


# ---------------------------------------------------------------------------
# StateStore — save / load round-trip
# ---------------------------------------------------------------------------

def test_statestore_save_and_load_roundtrip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = new_game()
    state.pois = [_make_poi("p1", owner=1)]
    state.turn_index = 3
    store.save(state)

    loaded = store.load()
    assert loaded.game_id == state.game_id
    assert loaded.turn_index == 3
    assert len(loaded.pois) == 1
    assert loaded.pois[0].owner == 1


def test_statestore_save_is_atomic(tmp_path):
    """Atomic write: no .tmp file left behind after save."""
    store = StateStore(tmp_path / "state.json")
    store.save(new_game())
    tmp_file = tmp_path / "state.tmp"
    assert not tmp_file.exists()


def test_statestore_save_creates_parent_dirs(tmp_path):
    store = StateStore(tmp_path / "subdir" / "deep" / "state.json")
    store.save(new_game())
    assert (tmp_path / "subdir" / "deep" / "state.json").exists()


# ---------------------------------------------------------------------------
# StateStore — reset
# ---------------------------------------------------------------------------

def test_statestore_reset_removes_file(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save(new_game())
    store.reset()
    assert not (tmp_path / "state.json").exists()


def test_statestore_reset_then_load_gives_fresh_game(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = new_game()
    state.turn_index = 5
    store.save(state)
    store.reset()
    fresh = store.load()
    assert fresh.turn_index == 0


def test_statestore_reset_when_no_file_is_noop(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.reset()  # should not raise


# ---------------------------------------------------------------------------
# StateStore — bad JSON raises
# ---------------------------------------------------------------------------

def test_statestore_bad_json_raises(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not valid json }", encoding="utf-8")
    store = StateStore(path)
    with pytest.raises(ValueError, match="invalid JSON"):
        store.load()


def test_statestore_bad_status_raises(tmp_path):
    path = tmp_path / "state.json"
    state = new_game()
    d = state.to_dict()
    d["status"] = "corrupted"
    path.write_text(json.dumps(d), encoding="utf-8")
    store = StateStore(path)
    with pytest.raises(ValueError):
        store.load()


def test_statestore_bad_json_does_not_silent_reset(tmp_path):
    """A bad state file must raise, never silently return a new game."""
    path = tmp_path / "state.json"
    path.write_text("GARBAGE", encoding="utf-8")
    store = StateStore(path)
    raised = False
    try:
        store.load()
    except ValueError:
        raised = True
    assert raised, "Bad JSON must raise ValueError, not silently return new_game()"
