# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> This project was deliberately simplified for a first-year student to be able to
> read and explain every line. It uses **only basic Python**: dicts, lists,
> functions, if/for/while. There are **no classes, no dataclasses, no Protocols,
> no dependency injection, no decorators of our own** (only the framework-required
> `@app.route`). Keep it that way — do not "improve" it back into OOP. See
> `程式講解.md` for the line-by-line Chinese walkthrough that ships with the code.

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
`check_rules.py` is a single plain-Python script (no pytest/fixtures) with ~12
key rule checks; it monkeypatches `app.game.rules.osrm_route` with a fake route so
it runs offline. Add checks there rather than reintroducing pytest.

## Architecture

GeoFlip is a 2-player hotseat board game played on a real-world map. The backend
is a stateless Flask app that reads/writes a single JSON file
(`data/state.json`) on every request. No database, no session state, no sync.

**The entire game is one dict called `state`.** Everything operates on that dict
and the lists/dicts inside it. The whole game logic is "change each POI's
`owner` field, then sum scores."

### Request flow

```
Browser form POST /move (target_poi_id [, source_poi_id])
  → app/web.py        @app.route("/move")        # load_state()
  → app/game/rules.py apply_move(state, ...)      # validates + mutates (deepcopy-then-commit)
  → app/services/osrm.py osrm_route(...)          # only called in normal-phase moves
  → app/state.py      save_state(path, state)     # plain json.dump
  → redirect GET /
  → app/map/render.py render_map_html(state)      # Folium HTML returned at GET /map (iframe)
```

### Module responsibilities

| Module | Role |
|---|---|
| `app/config.py` | Plain module-level constants only (no class, no env vars). Game knobs: `GAME_MAX_TURNS` (20), `GAME_OPENING_MOVES_PER_PLAYER` (2), `GAME_MAX_WALK_SECONDS` (600), `GAME_BUFFER_NORMAL_M` (50). |
| `app/models.py` | Builds/reads the `state` dict via plain functions: `score_poi()`, `make_poi()`, `new_game()`, and helpers like `current_player_id()`, `get_poi()`, `scores()`, `neutral_pois()`, `in_opening_phase()`. No I/O. |
| `app/state.py` | `load_state(path, max_turns)` / `save_state(path, state)` / `reset_state(path)` using `open()` + `json`. Missing file → `new_game()`. |
| `app/web.py` | Module-level `app = Flask(__name__)`; routes via `@app.route`. No factory, no DI. Calls the plain functions directly. |
| `app/game/rules.py` | `apply_move()` / `apply_pass()` — plain functions returning a **result dict** `{"ok", "message", "state", "placed_poi_id", "flipped_poi_ids", "route_ids"}`. Calls `osrm_route` directly. |
| `app/services/nominatim.py` | `search_locations(query, limit)` — returns a list of plain candidate dicts (setup-time location search for the board center). |
| `app/services/overpass.py` | `fetch_board_pois(lat, lon, radius_m, min_spacing_m, limit)` — returns a list of POI dicts around the chosen center; only called during setup. |
| `app/services/osrm.py` | `osrm_route(from_lat, from_lon, to_lat, to_lon)` — walking route; returns `{"coordinates_lonlat", "distance_m", "duration_s"}` or raises. |
| `app/services/geometry.py` | pyproj transformers + Shapely buffer helpers (plain functions). |
| `app/map/render.py` | `render_map_html(state) -> str` — Folium only. Popup HTML must NOT contain `<script>` (breaks Folium's outer script block). |

Services read their settings (base URLs, timeouts) directly from `app.config`.
Caching, rate-limiting, retries, atomic writes, and the SSL/truststore layer were
all removed for simplicity.

### Game rules (final — must not regress)

1. **2 players, `GAME_MAX_TURNS` turns total** (default 20, stored per-game in `state["max_turns"]`). Even `turn_index` → P1, odd → P2.
2. **Opening phase**: each player's first `GAME_OPENING_MOVES_PER_PLAYER` (default 2) moves are `move_kind="opening"`. Only a neutral target POI; no source, no OSRM call, no buffer, no flip. Each consumes one turn. `web.py` rejects `GAME_MAX_TURNS < 2 × opening moves` at import.
3. **Normal moves** (after opening): require both `source_poi_id` (own POI) and `target_poi_id` (neutral POI). Server calls OSRM walking route source→target. If `duration_s > GAME_MAX_WALK_SECONDS` (default 600) → invalid (no state change).
4. **Target always flips** to the current player on a valid normal move.
5. **Blocker buffer rule** (the 50 m buffer around the route, excluding source/target):
   - If **any** neutral POI lies in the buffer → route is "blocked", only the target flips. `move_kind="route"`.
   - Otherwise → all opponent POIs in the buffer flip. `move_kind="flip"`.
   - Own POIs in the buffer never block and never re-flip.
6. **Pass** (`apply_pass()`): consumes a turn, adds a `move_kind="pass"` record, no owner change. Two consecutive passes end the game.
7. **End conditions**: `turn_index >= max_turns` OR no neutral POIs left OR two consecutive passes.
8. **Invalid move is a complete no-op**: no `turn_index` change, no owner change, no route record. `apply_move` / `apply_pass` deepcopy state before any mutation.
9. **`scores()` is always live** from current `poi["owner"]` — never cached.

### Grading-requirement compliance (do not regress)

1. **Nominatim** is used at setup to find candidate *locations* (board center). `search_locations()` only.
2. **OSRM** computes real walking time. The 600 s rule uses `osrm_route()`.
3. **Folium** produces the interactive Leaflet map; embedded in the main page via `<iframe src="/map">`.
4. **Shapely + pyproj** do the buffer. Always project to metres before `.buffer()` (`always_xy=True`).
5. **Overpass** is a *supplementary* source for the board POIs (Nominatim still picks the center).

### Coordinate conventions

| Context | Order |
|---|---|
| `poi["lat"]` / `poi["lon"]` | stored separately |
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

- `state["game_id"]` = `"game_" + uuid.uuid4().hex`
- route `id` = `"route_" + uuid.uuid4().hex` (created inside `_commit_normal()`)
