# =====================================================================
# 全部的「設定值」都放在這裡，就是一堆普通變數。
# 想改遊戲行為，直接改下面的數字就好（改完要重開 server、重開一局）。
#
# 原本這個檔案用 class + 環境變數，現在通通拿掉，
# 因為我們不需要「從外面改設定」這種功能，寫成普通變數最好讀。
# =====================================================================

# --- 地圖一開始的中心點（找不到 POI 時才會用到的備案）---
DEFAULT_CENTER_LAT = 25.0330       # 預設緯度（台北 101 附近）
DEFAULT_CENTER_LON = 121.5654      # 預設經度
DEFAULT_ZOOM = 15                  # 地圖預設放大層級

# --- Nominatim：開局搜尋「起始地點」用的服務 ---
# 注意：Nominatim 規定 User-Agent 必須能識別你的程式並附上「真實」聯絡信箱，
# 否則會回 403 Forbidden（用 example.com 之類的假信箱會被擋）。
# 換成你自己的學號/信箱即可。
NOMINATIM_BASE_URL = "https://nominatim.openstreetmap.org"
NOMINATIM_USER_AGENT = "GeoFlip-NTHU-coursework/1.0 (85145lin@gmail.com)"
NOMINATIM_EMAIL = "85145lin@gmail.com"

# --- OSRM：計算「步行路線 / 步行時間」的服務 ---
OSRM_BASE_URL = "https://routing.openstreetmap.de/routed-foot"
OSRM_PROFILE = "foot"             # foot = 走路

# --- Overpass：抓棋盤上一堆 POI（地點）的服務 ---
OVERPASS_BASE_URL = "https://overpass-api.de"
OVERPASS_RADIUS_M = 500           # 以起始點為中心，抓多大範圍的 POI（公尺）
OVERPASS_MIN_POIS = 20            # 至少要抓到幾個 POI 才能開局
OVERPASS_MAX_POIS = 36            # 最多保留幾個 POI
OVERPASS_TIMEOUT_SECONDS = 25     # 等 Overpass 回應的秒數上限
OVERPASS_MIN_SPACING_M = 30       # 兩個 POI 至少相隔幾公尺（避免擠成一坨）

# --- 遊戲規則的數字 ---
GAME_MAX_TURNS = 20               # 整場總共幾回合
GAME_OPENING_MOVES_PER_PLAYER = 2 # 每位玩家開局先佈幾顆子
GAME_MAX_WALK_SECONDS = 600       # 連線時步行超過幾秒就算無效（10 分鐘）
GAME_BUFFER_NORMAL_M = 50         # 路線兩側「影響範圍」的寬度（公尺）
# 走廊內要「幾個（含）以上」中立 POI 才會把路線擋住（只翻目標、不翻對手）。
#   1 = 原本規則（只要有 1 個中立點就被擋，最難翻面）
#   2 = 預設（要 2 個以上才被擋，翻面變常見、比較有拉扯感）
#   越大 → 越難被擋 → 越容易一路翻對手
GAME_BLOCK_NEUTRAL_COUNT = 2

# --- 其他 ---
STATE_FILE = "data/state.json"    # 整場遊戲存檔的位置
REQUEST_TIMEOUT_SECONDS = 10      # 對外連網的等待秒數上限
