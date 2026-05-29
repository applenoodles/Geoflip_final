# =====================================================================
# Flask 網站：把玩家在瀏覽器的操作，接到規則引擎上。
#
# Flask 的用法：
#   app = Flask(__name__)          先做出一個網站物件
#   @app.route("/xxx")             「裝飾器」：把下面那個函式註冊成 /xxx 這個網址
#   def xxx(): ...                 有人連到 /xxx 時就會執行這個函式
#
# 做法：每次有請求就「讀存檔 → 算規則 → 寫存檔 → 轉址回首頁」。
# 不存任何記憶體狀態，重新整理也不會出錯。
# =====================================================================

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from app import config
from app.game.rules import apply_move, apply_pass
from app.map.render import render_map_html
from app.models import (
    current_player_id,
    in_opening_phase,
    is_finished,
    merge_discovered_pois,
    mmss,
    owned_pois,
    player_move_count,
    scores,
    winner,
)
from app.services.nominatim import search_locations
from app.services.overpass import fetch_board_pois
from app.state import load_state, reset_state, save_state


# 防呆：總回合數至少要能容納雙方的開局佈子
if config.GAME_MAX_TURNS < 2 * config.GAME_OPENING_MOVES_PER_PLAYER:
    raise ValueError("GAME_MAX_TURNS 太小，無法容納雙方的開局佈子")


app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = "geoflip-dev-secret"   # flash 訊息需要一把密鑰，作業用固定值即可


def _load():
    """讀目前存檔（包成一個小函式，因為每個路由都會用到）。"""
    return load_state(config.STATE_FILE, config.GAME_MAX_TURNS)


def _render_setup(setup_query="", setup_candidates=None, info_message=None):
    """畫「開始新棋盤」的設定頁。"""
    if setup_candidates is None:
        setup_candidates = []
    return render_template(
        "setup.html",
        setup_query=setup_query,
        setup_candidates=setup_candidates,
        info_message=info_message,
        default_radius_m=config.OVERPASS_RADIUS_M,
    )


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/")
def index():
    state = _load()

    # 還沒設定棋盤 → 直接帶玩家去設定頁
    if not state["pois"]:
        preserved_q = (request.args.get("q") or "").strip()
        return _render_setup(setup_query=preserved_q)

    opening_moves = config.GAME_OPENING_MOVES_PER_PLAYER
    cur = current_player_id(state)
    opening = in_opening_phase(state, cur, opening_moves)
    opening_remaining = max(0, opening_moves - player_move_count(state, cur))

    owned_counts = {1: len(owned_pois(state, 1)), 2: len(owned_pois(state, 2))}

    total_flips = 0
    for m in state["moves"]:
        total_flips += len(m["flipped_poi_ids"])
    total_routes = len(state["routes"])

    # 每次回合或路線變動就換一次網址，逼瀏覽器重新載入地圖 iframe
    map_iframe_src = "/map?v=" + str(state["turn_index"]) + "_" + str(len(state["moves"]))

    # 側邊欄的路線紀錄（最新的排最上面）
    routes_info = []
    for r in reversed(state["routes"]):
        mins, secs = mmss(r["duration_s"])
        routes_info.append({
            "turn": r["turn_index"] + 1,
            "player": r["player_id"],
            "distance_m": int(r["distance_m"]),
            "mins": mins,
            "secs": secs,
        })

    return render_template(
        "index.html",
        state=state,
        game_id=state["game_id"],
        current_player_id=cur,
        in_opening_phase=opening,
        opening_remaining=opening_remaining,
        opening_moves_per_player=opening_moves,
        scores=scores(state),
        winner=winner(state),
        status=state["status"],
        is_finished=is_finished(state),
        owned_counts=owned_counts,
        total_flips=total_flips,
        total_routes=total_routes,
        routes_info=routes_info,
        max_walk_seconds=int(config.GAME_MAX_WALK_SECONDS),
        buffer_normal_m=int(config.GAME_BUFFER_NORMAL_M),
        map_iframe_src=map_iframe_src,
    )


@app.route("/move", methods=["POST"])
def move():
    target_poi_id = (request.form.get("target_poi_id") or "").strip()
    source_poi_id = (request.form.get("source_poi_id") or "").strip() or None

    if not target_poi_id:
        flash("缺少 target_poi_id", "error")
        return redirect(url_for("index"))

    state = _load()
    result = apply_move(state, target_poi_id, source_poi_id)

    if result["ok"]:
        # 只有合法的一手才存檔；無效的一手完全不碰存檔
        save_state(config.STATE_FILE, result["state"])

        flipped_n = len(result["flipped_poi_ids"])
        dist_info = ""
        if result["route_ids"] and result["state"]["routes"]:
            r0 = result["state"]["routes"][-1]
            mins, secs = mmss(r0["duration_s"])
            dist_info = "（步行 " + str(int(r0["distance_m"])) + " 公尺 · " \
                        + str(mins) + " 分 " + str(secs) + " 秒）"

        if flipped_n:
            flash("成功" + dist_info + "，翻面 " + str(flipped_n) + " 個 POI", "success")
        elif dist_info:
            flash("成功" + dist_info + "（路線被中立點阻斷，只翻 target）", "success")
        else:
            flash("開局佈子成功", "success")
    else:
        flash(result["message"], "error")

    return redirect(url_for("index"))


@app.route("/pass", methods=["POST"])
def pass_turn():
    state = _load()
    result = apply_pass(state)
    if result["ok"]:
        save_state(config.STATE_FILE, result["state"])
        flash("跳過一回合", "success")
    else:
        flash(result["message"], "error")
    return redirect(url_for("index"))


@app.route("/new-game", methods=["POST"])
def new_game_route():
    reset_state(config.STATE_FILE)
    flash("新遊戲開始", "success")
    return redirect(url_for("index"))


@app.route("/setup/search", methods=["POST"])
def setup_search():
    query = (request.form.get("q") or "").strip()
    if not query:
        flash("請輸入起始地點關鍵字", "error")
        return _render_setup()

    try:
        candidates = search_locations(query, limit=5)
    except Exception as exc:
        flash(str(exc), "error")
        return _render_setup(setup_query=query)

    info_message = None
    if not candidates:
        info_message = "找不到符合的地點，請換關鍵字或換城市名稱"

    return _render_setup(
        setup_query=query,
        setup_candidates=candidates,
        info_message=info_message,
    )


@app.route("/setup/start", methods=["POST"])
def setup_start():
    display_name = (request.form.get("display_name") or "").strip()
    setup_query = (request.form.get("setup_query") or "").strip()
    preserved_q = setup_query or display_name

    def _back_to_setup():
        if preserved_q:
            return redirect(url_for("index", q=preserved_q))
        return redirect(url_for("index"))

    # 解析中心點座標
    try:
        lat = float(request.form.get("lat", ""))
        lon = float(request.form.get("lon", ""))
    except (TypeError, ValueError):
        flash("起始地點座標格式錯誤", "error")
        return _back_to_setup()

    # 解析抓取半徑（沒填或填錯就用預設）
    radius_raw = (request.form.get("radius_m") or "").strip()
    try:
        radius_m = float(radius_raw) if radius_raw else config.OVERPASS_RADIUS_M
    except ValueError:
        radius_m = config.OVERPASS_RADIUS_M

    # 用 Overpass 抓附近的 POI
    try:
        pois = fetch_board_pois(
            lat, lon, radius_m,
            config.OVERPASS_MIN_SPACING_M,
            limit=config.OVERPASS_MAX_POIS,
        )
    except Exception:
        flash(
            "地圖資料暫時無法載入，OpenStreetMap 伺服器忙碌中。"
            "請稍後重試，或改搜尋其他地點。",
            "error",
        )
        return _back_to_setup()

    if len(pois) < config.OVERPASS_MIN_POIS:
        flash(
            "附近可用 POI 太少（" + str(len(pois)) + " 個，至少需要 "
            + str(config.OVERPASS_MIN_POIS) + " 個），請換地點或調整範圍",
            "error",
        )
        return _back_to_setup()

    # 開一場新局，把抓到的 POI 灌進棋盤
    reset_state(config.STATE_FILE)
    state = _load()
    merge_discovered_pois(state, pois)
    save_state(config.STATE_FILE, state)

    if display_name:
        flash("已建立棋盤：" + display_name + "（" + str(len(pois)) + " 個 POI）", "success")
    else:
        flash("已建立棋盤（" + str(len(pois)) + " 個 POI）", "success")
    return redirect(url_for("index"))


@app.route("/map")
def map_view():
    # 單獨回傳 Folium 的完整 HTML，首頁用 <iframe> 嵌入
    state = _load()
    return render_map_html(state)


@app.route("/api/state")
def api_state():
    return jsonify(_load())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
