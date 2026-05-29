# AGENTS.md

This file provides guidance to Codex (codex.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable + dev deps)
pip install -e ".[dev]"

# Run all tests
python -m pytest

# Run a single test file / test
python -m pytest tests/test_rules.py -v
python -m pytest tests/test_rules.py::test_blocker_neutral_in_buffer_only_target_flips -v

# Syntax-check the whole app package
python -m compileall app

# Start the dev server
python -m app.web          # or: flask --app app.web run --debug
```

## Architecture

GeoFlip is a 2-player hotseat board game played on a real-world map. The backend
is a stateless Flask app that reads/writes a single JSON file
(`data/state.json`) on every request. No database, no session state, no sync.

### Request flow

```
Browser form POST /move (target_poi_id [, source_poi_id])
  → app/web.py  create_app()                # loads state from StateStore
  → app/game/rules.py  RulesEngine          # validates + mutates (deepcopy-then-commit)
  → app/services/osrm.py                    # only called in normal-phase moves
  → app/state.py  StateStore.save()         # atomic tmp→replace write
  → redirect GET /
  → app/map/render.py  render_map_html()    # Folium HTML returned at GET /map (iframe)
```

### Module responsibilities

| Module | Role |
|---|---|
| `app/models.py` | Dataclasses + `score_poi()`. No I/O, no external imports. Defines `MoveKind = "opening"|"route"|"flip"|"pass"` and `OPENING_MOVES_PER_PLAYER` (module default; runtime value comes from `Config`). |
| `app/state.py` | `new_game(max_turns)` factory; `StateStore(path, max_turns)` (load/save/reset). Bad JSON raises `ValueError` — never silent-reset. |
| `app/config.py` | `Config` dataclass; all values from env vars with defaults. Game knobs: `GAME_MAX_TURNS` (20), `GAME_OPENING_MOVES_PER_PLAYER` (2), `GAME_MAX_WALK_SECONDS` (600), `GAME_BUFFER_NORMAL_M` (50). |
| `app/web.py` | `create_app(config, state_store, nominatim_client, osrm_client, overpass_client, rules_engine)` — all deps injectable. |
| `app/game/rules.py` | `RulesEngine.apply_move()` + `apply_pass()` — pure logic; OSRM via a `RoutingService` protocol. |
| `app/services/nominatim.py` | `NominatimClient.search_locations()` — setup-time *location* search (board center pick), not POI candidates. |
| `app/services/overpass.py` | `OverpassClient.fetch_board_pois()` — fetches 18~36 POIs around the chosen center; only called during setup. |
| `app/services/osrm.py` | `OsrmClient.route()` — walking route between two points. |
| `app/services/geometry.py` | pyproj transformers + Shapely buffer helpers. |
| `app/map/render.py` | `render_map_html(state, config) -> str` — Folium only. Popup HTML must NOT contain `<script>` (breaks Folium's outer script block). |

### Game rules (final — must not regress)

1. **2 players, `GAME_MAX_TURNS` turns total** (default 20, set in `Config`; stored per-game in `state.max_turns` at `new_game()`). Even turns → P1, odd turns → P2.
2. **Opening phase**: each player's first `GAME_OPENING_MOVES_PER_PLAYER` (default 2) moves are `move_kind="opening"`. Only a neutral target POI; no source, no OSRM call, no buffer, no flip. Each consumes one turn. `create_app()` rejects `GAME_MAX_TURNS < 2 × opening moves` at startup.
3. **Normal moves** (after opening): require both `source_poi_id` (own POI) and `target_poi_id` (neutral POI). Server calls OSRM walking route source→target. If `duration_s > GAME_MAX_WALK_SECONDS` (default 600) → invalid (no state change).
4. **Target always flips** to the current player on a valid normal move.
5. **Blocker buffer rule** (the 50 m buffer around the route, excluding source/target):
   - If **any** neutral POI lies in the buffer → route is "blocked", only the target flips. `move_kind="route"`.
   - Otherwise → all opponent POIs in the buffer flip. `move_kind="flip"`.
   - Own POIs in the buffer never block and never re-flip.
6. **Pass** (`apply_pass()`): consumes a turn, adds a `move_kind="pass"` record, no owner change. Two consecutive passes end the game.
7. **End conditions**: `turn_index >= max_turns` OR no neutral POIs left OR two consecutive passes.
8. **Invalid move is a complete no-op**: no `turn_index` change, no owner change, no `RouteRecord`. `apply_move` / `apply_pass` deepcopy state before any mutation.
9. **`scores()` is always live** from current `poi.owner` — never cached.

### Grading-requirement compliance (do not regress)

1. **Nominatim** is used at setup to find candidate *locations* (board center). `search_locations()` only.
2. **OSRM** computes real walking time. The 600 s rule uses `OsrmClient.route()`.
3. **Folium** produces the interactive Leaflet map; embedded in the main page via `<iframe src="/map">`.
4. **Shapely + pyproj** do the buffer. Always project to metres before `.buffer()` (`always_xy=True`).
5. **Overpass** is a *supplementary* source for the board POIs (not a replacement for Nominatim — Nominatim still picks the center).

### Coordinate conventions

| Context | Order |
|---|---|
| `Poi.lat` / `Poi.lon` | stored separately |
| Folium `Marker` / `PolyLine` / `Polygon` | `[lat, lon]` |
| OSRM request URL | `lon,lat` |
| OSRM GeoJSON response | `[lon, lat]` |
| `RouteRecord.coordinates_lonlat` | `[lon, lat]` |
| Shapely geometry | `(lon, lat)` — **project to metres before buffering** |
| pyproj `Transformer` | always `always_xy=True` |

### Testing rules

- Tests must **never** call real Nominatim / OSRM / Overpass — mock all HTTP.
- Use `monkeypatch` to control time in rate-limiter tests (no `sleep`).
- Use pyproj to generate metre-level fixture offsets; never guess distances in degrees.
- `pytest testpaths` is set to `tests/`; no configuration needed beyond `pip install -e ".[dev]"`.

### Map popup gotcha

`app/map/render.py` popup HTML **must not contain `<script>`** — a `</script>`
inside the popup string closes Folium's outer `<script>` block early and leaks
the rest of the page's JS as visible text in the popup. Any JS that needs to
run when a popup opens belongs in `_page_script()`, hooked into the map's
`popupopen` event already set up in the `DOMContentLoaded` handler.

`_page_script()` also persists the map view (center + zoom) to
`localStorage["geoflip_view_<game_id>"]` on `moveend`/`zoomend`, and restores it
on load (overriding Folium's `fit_bounds`) so the view doesn't jump on every
iframe reload. Keyed by `game_id` so a new game resets the view.

### ID conventions

- `GameState.game_id` = `"game_" + uuid.uuid4().hex`
- `RouteRecord.id` = `"route_" + uuid.uuid4().hex` (created inside `_commit_normal()` at commit time)

### `Poi.raw` whitelist

Nominatim normalization keeps only these fields in `Poi.raw` (no `geojson`,
`boundingbox`, `icon`, full `addressdetails`):

```
display_name, name, class, type, osm_type, osm_id, importance
address.{country_code, city, town, village, suburb, neighbourhood, road, house_number, postcode}
extratags.{website, wikidata, wikipedia, opening_hours, phone}
```

Overpass normalization keeps only the curated tags listed in
`_RAW_TAG_WHITELIST` in `app/services/overpass.py`.
