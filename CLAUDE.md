# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> This project is a first-year student's coursework, written at the level their course
> actually taught: **plain OOP** (`class` + `__init__` + methods + `@classmethod`),
> `requests`, `with open`, `json`, list comprehensions, `try/except`, lambda, Flask,
> Folium. The student must be able to explain every line.
> Do NOT introduce things beyond that level: **no `@dataclass`, no `Protocol`, no
> dependency-injection frameworks, no type hints, no `httpx`, no caching/rate-limiting/
> atomic-write/SSL-truststore layers.** Keep classes hand-written and simple.
> `程式講解.md` is the line-by-line Chinese walkthrough (incl. an OOP-from-zero section)
> that ships with the code.

## Commands

```bash
# Install
pip install -e .

# Syntax-check the whole app package
python -m compileall app

# Start the dev server
python -m app.web          # http://127.0.0.1:5000

# Run the lightweight rule checks (offline; fakes OSRM). All should PASS.
python check_rules.py
```

The original 250-test pytest suite was removed during simplification. In its place
`check_rules.py` is a single plain-Python script (no pytest/fixtures) with ~12 key
rule checks. It passes a `FakeOsrm` object (one with a `.route()` method) into
`RulesEngine.apply_move`, so it runs offline. Add checks there rather than
reintroducing pytest.

## Architecture

GeoFlip is a 2-player hotseat board game played on a real-world map. The backend
is a stateless Flask app that reads/writes a single JSON file
(`data/state.json`) on every request. No database, no session state, no sync.

The whole game state is one `GameState` object. POIs are `Poi` objects; routes and
moves are plain dicts inside the state. The game logic is "change each POI's `owner`
attribute, then sum scores."

### Request flow

```
Browser form POST /move (target_poi_id [, source_poi_id])
  → app/web.py        @app.route("/move")              # store.load() -> GameState
  → app/game/rules.py RulesEngine.apply_move(state, target, osrm, source_poi_id=...)
  → app/services/osrm.py OsrmClient.route(...)          # only called in normal-phase moves
  → app/state.py      StateStore.save(state)            # state.to_dict() -> json.dump
  → redirect GET /
  → app/map/render.py render_map_html(state)            # Folium HTML returned at GET /map (iframe)
```

`web.py` instantiates the shared objects once at import: `store = StateStore(...)`,
`osrm = OsrmClient()`, `nominatim = NominatimClient()`, `overpass = OverpassClient(...)`,
`engine = RulesEngine()`. No app factory, no DI container — just direct instantiation.
The OSRM client is **passed into** `apply_move` (so tests pass a fake); everything else
is module-level.

### Module responsibilities

| Module | Role |
|---|---|
| `app/config.py` | Plain module-level constants only. Game knobs: `GAME_MAX_TURNS` (20), `GAME_OPENING_MOVES_PER_PLAYER` (2), `GAME_MAX_WALK_SECONDS` (600), `GAME_BUFFER_NORMAL_M` (50), `GAME_BLOCK_NEUTRAL_COUNT` (2 — neutrals in the corridor needed to block a route). Also `NOMINATIM_USER_AGENT`/`EMAIL` (must be real/identifiable or Nominatim returns 403). |
| `app/models.py` | `class Poi` (fields + `to_dict`/`from_dict`/`from_api`) and `class GameState` (`new_game`, `current_player_id`, `get_poi`, `scores`, `neutral_pois`, `in_opening_phase`, `to_dict`/`from_dict`, …). Plus module functions `score_poi()`, `mmss()`. No I/O. |
| `app/state.py` | `class StateStore(path, max_turns)` with `load()` / `save(state)` / `reset()` using `open()` + `json`. Missing file → `GameState.new_game()`. |
| `app/web.py` | Module-level `app = Flask(__name__)`; routes via `@app.route`. Instantiates the shared objects; routes call their methods. |
| `app/game/rules.py` | `class RulesEngine(max_walk_s, buffer_m, opening_moves)` with `apply_move(state, target_id, osrm, source_poi_id=None)` / `apply_pass(state)`. Returns a **result dict** `{"ok","message","state","placed_poi_id","flipped_poi_ids","route_ids"}`. The `osrm` arg is any object with a `.route()` method. |
| `app/services/nominatim.py` | `class NominatimClient` — `search_locations(query, limit)` returns a list of candidate dicts (setup-time location search). Uses `requests`. |
| `app/services/overpass.py` | `class OverpassClient` — `fetch_board_pois(lat, lon, radius_m, limit)` returns a list of `Poi` objects around the center; setup only. Uses `requests`. |
| `app/services/osrm.py` | `class OsrmClient` — `route(from_lat, from_lon, to_lat, to_lon)` returns `{"coordinates_lonlat","distance_m","duration_s"}` or raises. Uses `requests`. |
| `app/services/geometry.py` | pyproj transformers + Shapely buffer helpers (plain functions — geometry needs no class). |
| `app/map/render.py` | `render_map_html(state) -> str` — Folium only (plain functions). Reads `Poi` objects (`poi.name`) and route dicts (`route["..."]`). Popup HTML must NOT contain `<script>` (breaks Folium's outer script block). |

Caching, rate-limiting, retries, atomic writes, and the SSL/truststore layer were
all removed for simplicity.

### Game rules (final — must not regress)

1. **2 players, `GAME_MAX_TURNS` turns total** (default 20, stored per-game in `state.max_turns`). Even `turn_index` → P1, odd → P2.
2. **Opening phase**: each player's first `GAME_OPENING_MOVES_PER_PLAYER` (default 2) moves are `move_kind="opening"`. Only a neutral target POI; no source, no OSRM call, no buffer, no flip. Each consumes one turn. `web.py` rejects `GAME_MAX_TURNS < 2 × opening moves` at import.
3. **Normal moves** (after opening): require both `source_poi_id` (own POI) and `target_poi_id` (neutral POI). Server calls OSRM walking route source→target. If `duration_s > GAME_MAX_WALK_SECONDS` (default 600) → invalid (no state change).
4. **Target always flips** to the current player on a valid normal move.
5. **Blocker buffer rule** (the 50 m buffer around the route, excluding source/target):
   - If **`GAME_BLOCK_NEUTRAL_COUNT` (default 2) or more** neutral POIs lie in the buffer → route is "blocked", only the target flips. `move_kind="route"`. (Set the knob to 1 for the original "any single neutral blocks" behavior; higher = flips happen more often / more tug-of-war.)
   - Otherwise → all opponent POIs in the buffer flip. `move_kind="flip"`.
   - Own POIs in the buffer never block and never re-flip.
6. **Pass** (`apply_pass()`): consumes a turn, adds a `move_kind="pass"` record, no owner change. Two consecutive passes end the game.
7. **End conditions**: `turn_index >= max_turns` OR no neutral POIs left OR two consecutive passes.
8. **Invalid move is a complete no-op**: no `turn_index` change, no owner change, no route record. `apply_move` / `apply_pass` deepcopy state before any mutation.
9. **`scores()` is always live** from current `poi.owner` — never cached.

### Grading-requirement compliance (do not regress)

1. **Nominatim** is used at setup to find candidate *locations* (board center). `search_locations()` only.
2. **OSRM** computes real walking time. The 600 s rule uses `OsrmClient.route()`.
3. **Folium** produces the interactive Leaflet map; embedded in the main page via `<iframe src="/map">`.
4. **Shapely + pyproj** do the buffer. Always project to metres before `.buffer()` (`always_xy=True`).
5. **Overpass** is a *supplementary* source for the board POIs (Nominatim still picks the center).

### Coordinate conventions

| Context | Order |
|---|---|
| `poi.lat` / `poi.lon` | stored separately on the `Poi` object |
| Folium `Marker` / `PolyLine` / `Polygon` | `[lat, lon]` |
| OSRM request URL | `lon,lat` |
| OSRM GeoJSON response | `[lon, lat]` |
| `route["coordinates_lonlat"]` | `[lon, lat]` |
| Shapely geometry | `(lon, lat)` — **project to metres before buffering** |
| pyproj `Transformer` | always `always_xy=True` |

### Map popup gotcha

`app/map/render.py` popup HTML **must not contain `<script>`** — a `</script>`
inside the popup string closes Folium's outer `<script>` block early and leaks
the rest of the page's JS as visible text in the popup. JS that needs to run when
a popup opens belongs in `_page_script()`, hooked into the map's `popupopen`
event in the `DOMContentLoaded` handler.

### ID conventions

- `state.game_id` = `"game_" + uuid.uuid4().hex`
- route `id` = `"route_" + uuid.uuid4().hex` (created inside `RulesEngine._commit_normal()`)
