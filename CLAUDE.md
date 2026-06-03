# CLAUDE.md

> **課程限制**：plain OOP only（`class` + `__init__` + methods + `@classmethod`）、
> `requests`、`with open`、`json`、list comprehensions、`try/except`、lambda、Flask、Folium。
> **禁止加入**：`@dataclass`、`Protocol`、type hints、`httpx`、DI frameworks、caching、
> rate-limiting、atomic writes。學生必須能解釋每一行。

## Commands

```bash
pip install -e .
python -m compileall app          # syntax check
python -m app.web                 # dev server → http://127.0.0.1:5000
python check_rules.py             # 18 offline rule checks (FakeOsrm). All must PASS.
python make_demo_state.py         # 呼叫真實 API 重產 data/demo_state.json（唯讀）
```

`check_rules.py` 取代舊的 250-test pytest suite，加新規則加在這裡。

## Architecture

Stateless Flask；所有狀態存在 `data/state.json`。`GameState` + `Poi` objects + plain-dict routes。
`/demo` 載入唯讀的 `data/demo_state.json` 並重置成新局。
三個 service client 都有 `verify=False`（校園/家庭網路 SSL 繞過，勿移除）。

## Game rules (must not regress)

1. **2 players, `GAME_MAX_TURNS` turns** (default 20). Even turn → P1, odd → P2.
2. **Opening phase**: 每人前 `GAME_OPENING_MOVES_PER_PLAYER`（default 2）步 = `move_kind="opening"`。只能選中立目標；無 source、無 OSRM、無 buffer、無翻轉。各消耗一回合。
3. **Normal moves**: 需 `source_poi_id`（自己）+ `target_poi_id`（中立）。OSRM 步行時間 > 600 s → 無效，狀態不變。
4. 目標永遠翻轉給當前玩家。
5. **Buffer rule**（路線 50 m buffer，排除 source/target）：
   - buffer 內中立 POI ≥ `GAME_BLOCK_NEUTRAL_COUNT`（default 2）→ 封鎖，只翻目標（`move_kind="route"`）。
   - 否則 → buffer 內所有對手 POI 翻轉（`move_kind="flip"`）。
   - 自己的 POI 不封鎖也不被翻。
6. **Pass**：消耗一回合，無 owner 改變。連續兩次 pass → 遊戲結束。**開局階段禁止**（後端拒絕；UI 也只在連線階段顯示跳過鈕）。
7. **結束**：`turn_index >= max_turns` 或無中立 POI 或連續兩次 pass。
8. **無效 move = 完全 no-op**：mutation 前 deepcopy，不改 turn/owner。
9. `scores()` 永遠從 `poi.owner` 即時算，不 cache。

## Grading compliance (do not regress)

1. Nominatim → 棋盤中心（`search_locations()` only）。
2. OSRM → 真實步行時間；600 s 規則用 `OsrmClient.route()`。
3. Folium → `<iframe src="/map">` 嵌入互動地圖。
4. Shapely + pyproj → buffer（先投影到公尺，`always_xy=True`）。
5. Overpass → 輔助 POI 來源（中心仍由 Nominatim 決定）。

## Coordinate conventions

| Context | Order |
|---|---|
| Folium `Marker` / `PolyLine` / `Polygon` | `[lat, lon]` |
| OSRM URL / GeoJSON / `route["coordinates_lonlat"]` | `lon, lat` |
| Shapely / pyproj | `(lon, lat)` — 先投影到公尺再 `.buffer()` |

## Map popup gotcha

Popup HTML **禁止含 `<script>`** — `</script>` 會提早關閉 Folium 外層 script block。
需要執行的 JS 放進 `_page_script()`，用 `popupopen` event hook。
