# GeoFlip

雙人 hotseat 地圖棋盤遊戲。兩位玩家在同一台電腦同一個瀏覽器輪流操作 ——
在真實地圖上佈子、用 OSRM 算實際步行路線連點、把對手沿途的據點翻成自己的。
12 回合後分數高者勝。

> 「黑白棋 + 真實步行路徑」：先在地圖上佈下開局子，之後連連看搶地盤。

---

## 遊戲規則

### 開局階段（前 4 手）
每位玩家前 **2 手** 是開局佈子：
- 直接點地圖上任何中立 POI 即可佈子。
- 不需要呼叫 OSRM、不畫路線、不翻面。
- 一手消耗一回合。

設計理由：給雙方各兩個起手地盤，後手不會被先手直接壓制。

### 連線回合（第 5 手起）
進入正常回合，每一手：
1. 先點自己的 POI 選為 **起點 (source)**。
2. 再點中立 POI 選為 **目標 (target)**。
3. 後端呼叫 OSRM 算 source → target 真實步行路線。
4. 路線 **步行時間 > 600 秒 → 此手無效**，回合不前進。
5. 路線合法時：
   - **目標 POI 一定變成你的**。
   - 沿路線畫 **50m 影響範圍**：
     - 範圍內若有任何中立 POI → 路線被阻斷，只翻目標。
     - 範圍內沒有中立 POI → 範圍內所有對手 POI 全部翻給你。
   - 自己的 POI 不阻斷、不會被反向翻面。

### 跳過 / 結束
- 找不到合法路線時可以 **跳過本回合**。
- 連續兩次跳過 → 立即結束遊戲。
- 沒有中立 POI 可下 → 結束遊戲。
- 回合數達 12 → 結束遊戲。
- 結束後比分數，相同則平手。

### 無效動作 = 完全不變
非法落子不會前進回合、不會改 owner、不會留下路線紀錄。

---

## 作業需求對應

| 需求 | 對應實作 |
|---|---|
| **Nominatim API** 找候選地點 | `app/services/nominatim.py`，setup 階段搜尋遊戲中心位置 |
| **OSRM API** 計算真實步行時間 | `app/services/osrm.py` + 規則中的 600 秒上限 |
| **Folium** 產生互動式 Leaflet 地圖 | `app/map/render.py` 回傳完整 Folium HTML，主頁用 `<iframe src="/map">` 嵌入 |
| **Shapely + pyproj** 做路線 buffer | `app/services/geometry.py`，always_xy=True，先投影到公尺再 `.buffer()` |
| Overpass API 抓棋盤 POI（不取代 Nominatim） | `app/services/overpass.py`，setup 階段抓中心附近 18~36 個 POI |

---

## 安裝

需要 Python 3.11+。

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

pip install -e ".[dev]"
```

## 設定

```bash
cp .env.example .env
# 編輯 .env，至少要把 NOMINATIM_USER_AGENT 與 NOMINATIM_EMAIL
# 設成你自己的（Nominatim 服務條款要求）
```

| 變數 | 用途 |
|---|---|
| `DEFAULT_CENTER_LAT` / `DEFAULT_CENTER_LON` / `DEFAULT_ZOOM` | 初始地圖視角 |
| `NOMINATIM_USER_AGENT` / `NOMINATIM_EMAIL` | **Nominatim 必需** — 換成你自己的真實 email |
| `NOMINATIM_BASE_URL` | 自架 Nominatim 時改 |
| `NOMINATIM_MIN_INTERVAL_SECONDS` | 兩次 cache miss 之間的最小間隔 |
| `OSRM_BASE_URL` / `OSRM_PROFILE` | 預設用公開 demo，profile 必須是 `foot` |
| `OVERPASS_BASE_URL` / `OVERPASS_RADIUS_M` / `OVERPASS_MIN_POIS` / `OVERPASS_MAX_POIS` | 開局時抓 POI 的設定 |
| `GAME_MAX_WALK_SECONDS` / `GAME_BUFFER_NORMAL_M` | 600 秒上限與 50m buffer |
| `STATE_FILE` | JSON 存檔路徑 |

## 執行

```bash
python -m app.web
# 或
flask --app app.web run --debug
```

打開 <http://127.0.0.1:5000/>。

## 測試

```bash
python -m pytest           # 全套測試，HTTP 全 mock，不打真網路
python -m compileall app   # 整個 package 語法檢查
```

---

## 手動 playtest 流程

1. `python -m app.web` 啟動。
2. 在 setup 頁搜尋一個地點（例：`新竹車站`），挑一個候選 → 系統用 Overpass 抓附近 POI 建立棋盤。
3. **P1 開局子 #1**：點地圖上任一中立 POI，按「在此落子」。
4. **P2 開局子 #1**：換 P2 任意中立 POI。
5. **P1 開局子 #2** → **P2 開局子 #2**（共 4 手開局）。
6. **連線回合**：P1 點自己的 POI 按「選為起點」，再點中立 POI 按「連線到此」。
   - 路線 ≤ 600 秒會成功，地圖上畫出 polyline + 50m 範圍。
   - 範圍內對手 POI 會翻成 P1 的（若沒被中立阻斷）。
7. 試一個 > 600 秒的目標 → 應該被拒絕、不消耗回合。
8. 玩到結束，sidebar 顯示勝者 / 平手與最終分數。

---

## 架構

```
app/
  config.py         讀 env / 預設值的 Config dataclass
  models.py         Poi / RouteRecord / MoveRecord / GameState 與 score_poi()
  state.py          new_game() + StateStore（atomic JSON 讀寫）
  web.py            Flask app factory + 所有 routes
  game/rules.py     RulesEngine（純規則，不碰 I/O）
  services/
    nominatim.py    地點搜尋（rate-limit + cache）
    overpass.py     棋盤 POI 抓取
    osrm.py         步行路線
    geometry.py     pyproj 投影 + Shapely buffer
  map/render.py     render_map_html() — Folium 輸出
  templates/        sidebar + iframe
  static/           CSS

tests/              全部 mock 過外部 API
```

### 最重要的五個檔案
1. **`models.py`** — 資料長什麼樣
2. **`state.py`** — 資料怎麼存
3. **`rules.py`** — 規則怎麼判
4. **`web.py`** — 玩家動作怎麼進來
5. **`render.py`** — 地圖怎麼畫

---

## Troubleshooting

**`Nominatim 403 / 429`** — 公開 Nominatim 限流很嚴格：
- `NOMINATIM_USER_AGENT` 必須是真實聯絡資訊。
- `NOMINATIM_MIN_INTERVAL_SECONDS=1.0` 以上。

**`OSRM HTTP error / 找不到步行路線`** — `router.project-osrm.org` 公開 demo 不穩定，重試或自架 `foot` profile 的 OSRM。

**`State file ... contains invalid JSON`** — 程式不會 silent reset 壞檔；手動修 JSON 或 `rm data/state.json`，或在正常遊戲中按「新開局」清掉。

**popup 顯示 JS 原始碼** — 已修，若仍看到請 hard reload（Ctrl+Shift+R）清 iframe cache。

**地圖沒更新** — iframe URL 帶 `?v=<turn>_<moves>` 已自動 cache-bust；極端情況 Ctrl+Shift+R。

---

## 已知限制

- 本機 hotseat 遊戲，無帳號、無 WebSocket、無線上多人。同一 server 多瀏覽器會搶同一個 JSON 檔。
- 公開 Nominatim / OSRM / Overpass 服務不穩 + 限流，要重度使用請自架。
- 棋盤 POI 在 setup 時就鎖定，遊戲中不再新增。
