"""Flask 路由層 —— 把玩家的 HTTP 請求接到 RulesEngine 上。

設計重點：
  - create_app() 是 Flask app factory，所有依賴（state_store / nominatim /
    osrm / overpass / rules_engine）都可以被外部塞進來；測試時換成假物件即可。
  - /move 與 /pass 都只回 302 redirect；錯誤訊息用 flask flash 帶到 GET /，
    這樣即便重新整理也不會重送表單。
  - 前端永遠只能送 poi_id；伺服器絕不接受任意 lat/lon，避免玩家作弊。
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.config import Config
from app.game.rules import RulesEngine
from app.map.render import render_map_html
from app.models import mmss
from app.services.nominatim import NominatimClient, NominatimError
from app.services.osrm import OsrmClient
from app.services.overpass import OverpassClient, OverpassError
from app.state import StateStore

load_dotenv()

logger = logging.getLogger("geoflip.web")


def create_app(
    config: Config | None = None,
    state_store: StateStore | None = None,
    nominatim_client: NominatimClient | None = None,
    osrm_client: OsrmClient | None = None,
    overpass_client: OverpassClient | None = None,
    rules_engine: RulesEngine | None = None,
) -> Flask:
    """Flask app factory — all dependencies are injectable for tests."""
    if config is None:
        config = Config()

    # 防呆：總回合數至少要能容納雙方的開局佈子，否則開局還沒結束遊戲就到上限。
    opening_total = 2 * config.GAME_OPENING_MOVES_PER_PLAYER
    if config.GAME_MAX_TURNS < opening_total:
        raise ValueError(
            f"GAME_MAX_TURNS={config.GAME_MAX_TURNS} 太小："
            f"雙方開局共需 {opening_total} 回合"
            f"（2 玩家 × GAME_OPENING_MOVES_PER_PLAYER="
            f"{config.GAME_OPENING_MOVES_PER_PLAYER}），"
            f"請把 GAME_MAX_TURNS 設為 >= {opening_total}。"
        )
    if state_store is None:
        state_store = StateStore(config.STATE_FILE, max_turns=config.GAME_MAX_TURNS)
    if nominatim_client is None:
        nominatim_client = NominatimClient(
            base_url=config.NOMINATIM_BASE_URL,
            user_agent=config.NOMINATIM_USER_AGENT,
            email=config.NOMINATIM_EMAIL,
            timeout_seconds=config.REQUEST_TIMEOUT_SECONDS,
            min_interval_seconds=config.NOMINATIM_MIN_INTERVAL_SECONDS,
        )
    if osrm_client is None:
        osrm_client = OsrmClient(
            base_url=config.OSRM_BASE_URL,
            profile=config.OSRM_PROFILE,
            timeout_seconds=config.REQUEST_TIMEOUT_SECONDS,
        )
    if overpass_client is None:
        overpass_client = OverpassClient(
            base_url=config.OVERPASS_BASE_URL,
            timeout_seconds=config.OVERPASS_TIMEOUT_SECONDS,
            min_spacing_m=config.OVERPASS_MIN_SPACING_M,
        )
    if rules_engine is None:
        rules_engine = RulesEngine(
            max_walk_duration_s=config.GAME_MAX_WALK_SECONDS,
            buffer_normal_m=config.GAME_BUFFER_NORMAL_M,
            opening_moves_per_player=config.GAME_OPENING_MOVES_PER_PLAYER,
        )

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.getenv("SECRET_KEY", "geoflip-dev-secret")

    app.config["GEOFLIP_CONFIG"] = config
    app.config["GEOFLIP_STATE_STORE"] = state_store
    app.config["GEOFLIP_NOMINATIM"] = nominatim_client
    app.config["GEOFLIP_OSRM"] = osrm_client
    app.config["GEOFLIP_OVERPASS"] = overpass_client
    app.config["GEOFLIP_RULES"] = rules_engine

    # ------------------------------------------------------------------

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    def _render_setup(
        setup_query: str = "",
        setup_candidates=None,
        info_message: str | None = None,
    ):
        return render_template(
            "setup.html",
            setup_query=setup_query,
            setup_candidates=setup_candidates or [],
            info_message=info_message,
            default_radius_m=config.OVERPASS_RADIUS_M,
        )

    @app.get("/")
    def index():
        state = state_store.load()

        # 還沒設定棋盤（剛安裝或剛 /new-game）→ 直接導去 setup 頁面，
        # 不要讓玩家看到空地圖。preserved_q 讓回到 setup 時搜尋字串還在。
        if not state.pois:
            preserved_q = (request.args.get("q") or "").strip()
            return _render_setup(setup_query=preserved_q)

        opening_moves = config.GAME_OPENING_MOVES_PER_PLAYER
        current_player_id = state.current_player_id()
        in_opening_phase = state.in_opening_phase(
            current_player_id, opening_moves
        )
        opening_remaining = max(
            0, opening_moves - state.player_move_count(current_player_id)
        )

        scores = state.scores()
        owned_counts = {
            1: len(state.owned_pois(1)),
            2: len(state.owned_pois(2)),
        }
        total_flips = sum(len(m.flipped_poi_ids) for m in state.moves)
        total_routes = len(state.routes)
        map_iframe_src = f"/map?v={state.turn_index}_{len(state.moves)}"

        # Sidebar route log — latest first.
        routes_info = []
        for r in reversed(state.routes):
            mins, secs = mmss(r.duration_s)
            routes_info.append({
                "turn": r.turn_index + 1,
                "player": r.player_id,
                "distance_m": int(r.distance_m),
                "mins": mins,
                "secs": secs,
            })

        return render_template(
            "index.html",
            state=state,
            game_id=state.game_id,
            current_player_id=current_player_id,
            in_opening_phase=in_opening_phase,
            opening_remaining=opening_remaining,
            opening_moves_per_player=opening_moves,
            scores=scores,
            winner=state.winner(),
            status=state.status,
            is_finished=state.is_finished(),
            owned_counts=owned_counts,
            total_flips=total_flips,
            total_routes=total_routes,
            routes_info=routes_info,
            max_walk_seconds=int(config.GAME_MAX_WALK_SECONDS),
            buffer_normal_m=int(config.GAME_BUFFER_NORMAL_M),
            map_iframe_src=map_iframe_src,
        )

    @app.post("/move")
    def move():
        target_poi_id = (request.form.get("target_poi_id") or "").strip()
        source_poi_id = (request.form.get("source_poi_id") or "").strip() or None

        if not target_poi_id:
            flash("缺少 target_poi_id", "error")
            return redirect(url_for("index"))

        state = state_store.load()
        result = rules_engine.apply_move(
            state, target_poi_id, osrm_client, source_poi_id=source_poi_id
        )

        if result.ok:
            # 只有合法的 move 才存檔；無效動作完全不碰磁碟上的 state.json。
            state_store.save(result.state)
            flipped_n = len(result.flipped_poi_ids)
            dist_info = ""
            if result.route_ids and result.state.routes:
                r0 = result.state.routes[-1]
                mins, secs = mmss(r0.duration_s)
                dist_info = f"（步行 {int(r0.distance_m)} 公尺 · {mins} 分 {secs} 秒）"

            if flipped_n:
                flash(f"成功{dist_info}，翻面 {flipped_n} 個 POI", "success")
            elif dist_info:
                flash(f"成功{dist_info}（路線被中立點阻斷，只翻 target）", "success")
            else:
                flash("開局佈子成功", "success")
            logger.info(
                "move ok turn=%d player=%d target=%s source=%s flipped=%d",
                state.turn_index, state.current_player_id(),
                target_poi_id, source_poi_id, flipped_n,
            )
        else:
            logger.info(
                "move INVALID turn=%d player=%d target=%s source=%s reason=%s",
                state.turn_index, state.current_player_id(),
                target_poi_id, source_poi_id, result.message,
            )
            flash(result.message, "error")

        return redirect(url_for("index"))

    @app.post("/pass")
    def pass_turn():
        state = state_store.load()
        result = rules_engine.apply_pass(state)
        if result.ok:
            state_store.save(result.state)
            flash("跳過一回合", "success")
        else:
            flash(result.message, "error")
        return redirect(url_for("index"))

    @app.post("/new-game")
    def new_game_route():
        state_store.reset()
        flash("新遊戲開始", "success")
        return redirect(url_for("index"))

    @app.post("/setup/search")
    def setup_search():
        query = (request.form.get("q") or "").strip()
        if not query:
            flash("請輸入起始地點關鍵字", "error")
            return _render_setup()

        try:
            candidates = nominatim_client.search_locations(query, limit=5)
            logger.info("setup search q=%r → %d candidates", query, len(candidates))
        except NominatimError as exc:
            logger.warning("setup search q=%r failed: %s", query, exc)
            flash(f"搜尋失敗：{exc}", "error")
            return _render_setup(setup_query=query)

        info_message = None
        if not candidates:
            info_message = "找不到符合的地點，請換關鍵字或換城市名稱"

        return _render_setup(
            setup_query=query,
            setup_candidates=candidates,
            info_message=info_message,
        )

    @app.post("/setup/start")
    def setup_start():
        display_name = (request.form.get("display_name") or "").strip()
        setup_query = (request.form.get("setup_query") or "").strip()
        preserved_q = setup_query or display_name

        def _back_to_setup():
            if preserved_q:
                return redirect(url_for("index", q=preserved_q))
            return redirect(url_for("index"))

        try:
            lat = float(request.form.get("lat", ""))
            lon = float(request.form.get("lon", ""))
        except (TypeError, ValueError):
            flash("起始地點座標格式錯誤", "error")
            return _back_to_setup()

        radius_raw = (request.form.get("radius_m") or "").strip()
        try:
            radius_m = float(radius_raw) if radius_raw else config.OVERPASS_RADIUS_M
        except ValueError:
            radius_m = config.OVERPASS_RADIUS_M

        try:
            pois = overpass_client.fetch_board_pois(
                lat, lon, radius_m, limit=config.OVERPASS_MAX_POIS,
            )
        except OverpassError as exc:
            logger.warning("overpass fetch failed at (%s,%s): %s", lat, lon, exc)
            flash(
                "地圖資料暫時無法載入，OpenStreetMap 伺服器忙碌中。"
                "請稍後重試，或改搜尋其他地點。",
                "error",
            )
            return _back_to_setup()

        if len(pois) < config.OVERPASS_MIN_POIS:
            logger.info(
                "overpass too few POIs at (%s,%s) r=%sm → %d",
                lat, lon, radius_m, len(pois),
            )
            flash(
                f"附近可用 POI 太少（{len(pois)} 個，至少需要 "
                f"{config.OVERPASS_MIN_POIS} 個），請換地點或調整範圍",
                "error",
            )
            return _back_to_setup()

        state_store.reset()
        state = state_store.load()
        state.merge_discovered_pois(pois)
        state_store.save(state)

        logger.info(
            "setup start ok center=(%s,%s) r=%sm pois=%d label=%r",
            lat, lon, radius_m, len(pois), display_name,
        )
        if display_name:
            flash(f"已建立棋盤：{display_name}（{len(pois)} 個 POI）", "success")
        else:
            flash(f"已建立棋盤（{len(pois)} 個 POI）", "success")
        return redirect(url_for("index"))

    @app.get("/map")
    def map_view():
        # 獨立回 Folium 的完整 HTML，主頁用 <iframe> 嵌入。
        # 這樣 Folium 自己的 <html>/<head>/<body> 不會跟主頁衝突。
        state = state_store.load()
        return render_map_html(state, config)

    @app.get("/api/state")
    def api_state():
        state = state_store.load()
        return jsonify(state.to_dict())

    return app


def _configure_root_logging() -> None:
    if logging.getLogger().handlers:
        return
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


if __name__ == "__main__":
    _configure_root_logging()
    app = create_app()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    app.run(host=host, port=port, debug=debug)
