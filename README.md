# GeoFlip

雙人 hotseat 地圖棋盤遊戲。兩位玩家在同一台電腦輪流操作——在真實地圖上佈子、用 OSRM 算實際步行路線連點、把對手沿途的據點翻成自己的。回合用完後分數高者勝。

> 「黑白棋 + 真實步行路徑」：先佈開局子，之後連連看搶地盤。

---

## 玩法

**開局階段**（每人前 2 手）：直接點任一中立 POI 佈子，不畫路線、不翻面，一手一回合。

**連線回合**：
1. 點自己的 POI 選為**起點**，再點中立 POI 選為**目標**。
2. 後端用 OSRM 算步行路線；**超過上限秒數（預設 600s）→ 此手無效**。
3. 合法時**目標一定翻成你的**，並沿路線畫影響範圍（預設 50m）：
   - 範圍內有任何中立 POI → 路線被阻斷，只翻目標。
   - 範圍內沒有中立 POI → 範圍內對手 POI 全翻給你。
   - 自己的 POI 不阻斷、不會被反翻。

**結束**：回合數用完 / 沒中立 POI 可下 / 連續兩次跳過。比分數，相同平手。非法落子完全不改變狀態。

---

## 安裝與啟動

需要 Python 3.11+。

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows（macOS/Linux: source .venv/bin/activate）
pip install -e ".[dev]"

cp .env.example .env            # 編輯 NOMINATIM_USER_AGENT / NOMINATIM_EMAIL 成你自己的

python -m app.web               # 啟動，開 http://127.0.0.1:5000/
```

測試：`python -m pytest`（HTTP 全 mock，不打真網路）。

---

## 可調參數

全部在 `app/config.py`（改引號裡的值或設同名環境變數），改完**重啟 server**：

| 變數 | 預設 | 用途 |
|---|---|---|
| `GAME_MAX_TURNS` | 20 | 總回合數（只對新開的局生效，須 ≥ 2×開局子數，否則啟動報錯） |
| `GAME_OPENING_MOVES_PER_PLAYER` | 2 | 每人開局佈子數 |
| `GAME_MAX_WALK_SECONDS` | 600 | 連線步行秒數上限，越小越難 |
| `GAME_BUFFER_NORMAL_M` | 50 | 路線影響範圍寬度（公尺） |
| `OVERPASS_RADIUS_M` / `MIN_SPACING_M` | 500 / 30 | 抓 POI 的半徑與最小間距 |
| `OVERPASS_MIN_POIS` / `MAX_POIS` | 18 / 36 | 棋盤 POI 數量範圍 |
| `NOMINATIM_USER_AGENT` / `EMAIL` | — | **Nominatim 必需**，換成你自己的真實聯絡資訊 |
| `OSRM_BASE_URL` / `OSRM_PROFILE` | 公開步行 demo | profile 須為 `foot` |
| `DEFAULT_CENTER_LAT/LON/ZOOM` | 台北 | 初始地圖視角 |
| `STATE_FILE` | data/state.json | JSON 存檔路徑 |

> 地圖視角會記在瀏覽器 localStorage，每手 reload 後維持你的縮放/平移，不跳回預設。

---

## 作業需求對應

| 需求 | 實作 |
|---|---|
| Nominatim 找候選地點 | `app/services/nominatim.py`（setup 搜尋遊戲中心） |
| OSRM 算真實步行時間 | `app/services/osrm.py` + 上限秒數規則 |
| Folium 互動地圖 | `app/map/render.py`，主頁 `<iframe src="/map">` 嵌入 |
| Shapely + pyproj 做 buffer | `app/services/geometry.py`，先投影到公尺再 `.buffer()` |
| Overpass 抓棋盤 POI（補充，不取代 Nominatim） | `app/services/overpass.py` |

---

## 架構

```
app/
  config.py       Config dataclass（讀 env / 預設值）
  models.py       Poi / RouteRecord / MoveRecord / GameState 與 score_poi()
  state.py        new_game() + StateStore（atomic JSON 讀寫）
  web.py          Flask app factory + 所有 routes
  game/rules.py   RulesEngine（純規則，不碰 I/O）
  services/       nominatim / overpass / osrm / geometry
  map/render.py   render_map_html() — Folium 輸出
  templates/ static/
tests/            全部 mock 外部 API
```

---

## Troubleshooting

- **Nominatim 403/429**：`NOMINATIM_USER_AGENT` 必須是真實聯絡資訊、間隔 ≥ 1s。
- **OSRM 找不到路線**：公開 demo 不穩，重試或自架 `foot` profile。
- **State file invalid JSON**：程式不 silent reset，手動修或 `rm data/state.json`，或在遊戲中按「新開局」。
- **地圖/popup 沒更新**：iframe 已自動 cache-bust，極端情況 Ctrl+Shift+R。

## 已知限制

本機 hotseat，無帳號/連線多人；公開 Nominatim/OSRM/Overpass 不穩且限流，重度使用請自架；棋盤 POI 在 setup 鎖定，遊戲中不再新增。
