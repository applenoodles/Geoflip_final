# GeoFlip

雙人 hotseat 地圖棋盤遊戲。兩位玩家在同一台電腦輪流操作——在真實地圖上佈子、用 OSRM 算實際步行路線連點、把對手沿途的據點翻成自己的。回合用完後分數高者勝。

> 「黑白棋 + 真實步行路徑」：先佈開局子，之後連連看搶地盤。

> 想看「每一行在做什麼」的逐檔說明，請看 [`程式講解.md`](程式講解.md)。

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
pip install -e .             # 安裝套件（只需做一次）

python -m app.web           # 啟動，開 http://127.0.0.1:5000/

python check_rules.py        # （可選）跑規則檢查，全部 PASS 代表規則正常
```

> 在學校網路若遇到 SSL 憑證錯誤，見最下方 Troubleshooting。

---

## 可調參數

全部在 [`app/config.py`](app/config.py)，就是一堆普通常數，改數字即可，改完**重啟 server**：

| 變數 | 預設 | 用途 |
|---|---|---|
| `GAME_MAX_TURNS` | 20 | 總回合數（只對新開的局生效，須 ≥ 2×開局子數，否則啟動報錯） |
| `GAME_OPENING_MOVES_PER_PLAYER` | 2 | 每人開局佈子數 |
| `GAME_MAX_WALK_SECONDS` | 600 | 連線步行秒數上限，越小越難 |
| `GAME_BUFFER_NORMAL_M` | 50 | 路線影響範圍寬度（公尺） |
| `OVERPASS_RADIUS_M` / `MIN_SPACING_M` | 500 / 30 | 抓 POI 的半徑與最小間距 |
| `OVERPASS_MIN_POIS` / `MAX_POIS` | 18 / 36 | 棋盤 POI 數量範圍 |
| `NOMINATIM_USER_AGENT` / `EMAIL` | — | Nominatim 建議填真實聯絡資訊 |
| `OSRM_BASE_URL` / `OSRM_PROFILE` | 公開步行 demo | profile 須為 `foot` |
| `DEFAULT_CENTER_LAT/LON/ZOOM` | 台北 | 初始地圖視角 |
| `STATE_FILE` | data/state.json | JSON 存檔路徑 |

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

後端是一個 **stateless Flask app**：每次請求都「讀 JSON 存檔 → 算 → 寫回」。
沒有資料庫、沒有記憶體狀態。整場遊戲是一個 `GameState` 物件，存成 `data/state.json`。
程式用課堂教的 OOP 寫（`class` + `__init__` + 方法），連網用 `requests`。逐檔教學見 [`程式講解.md`](程式講解.md)。

```
app/
  config.py       設定值（一堆普通常數）
  models.py       class Poi、class GameState（資料 + 方法：scores / current_player_id…）
  state.py        class StateStore（load / save / reset，JSON 讀寫）
  web.py          Flask 路由（@app.route 把網址綁到函式）
  game/rules.py   class RulesEngine（apply_move / apply_pass，回傳結果字典）
  services/       class NominatimClient / OverpassClient / OsrmClient（requests）+ geometry（Shapely+pyproj 函式）
  map/render.py   render_map_html() — Folium 輸出
  templates/ static/
check_rules.py    精簡規則檢查（離線、約 12 項）
```

一次落子的資料流：

```
瀏覽器 POST /move → web.py → RulesEngine.apply_move(...)（中途呼叫 OsrmClient.route()）
   → StateStore.save() → 轉址 GET / → web.py 重畫，地圖由 map/render.py 用 Folium 產生
```

---

## Troubleshooting

- **Nominatim 搜尋回 403 Forbidden**：Nominatim 規定 `User-Agent` 必須能識別你的程式並附「真實」聯絡信箱
  （用 `example.com` 之類的假信箱會被擋）。改 `app/config.py` 的 `NOMINATIM_USER_AGENT` / `NOMINATIM_EMAIL`
  成你自己的學號/信箱即可。
- **SSL 憑證錯誤（學校網路常見）**：學校網路攔截造成。臨時解法：在 `app/services/` 內
  `requests.get(...)` / `requests.post(...)` 那幾行加上 `verify=False`（關閉憑證檢查，僅作業用）。
- **OSRM 找不到路線**：公開 demo 不穩，重試或換地點。
- **Overpass「附近 POI 太少」**：換人口密集一點的地點，或把 `OVERPASS_RADIUS_M` 調大。
- **想重來**：遊戲中按「新開局」，或直接刪掉 `data/state.json`。

## 已知限制

本機 hotseat，無帳號/連線多人；公開 Nominatim/OSRM/Overpass 不穩且限流；棋盤 POI 在 setup 鎖定，遊戲中不再新增。
