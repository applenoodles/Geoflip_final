# =====================================================================
# 用 Folium 畫互動地圖，回傳一整頁 HTML（主頁面用 <iframe> 把它嵌進來）。
#
# Folium 是把「Python 物件」轉成「Leaflet 網頁地圖」的套件。
# 我們在這裡：放上每個 POI 的圓點、畫最新一條路線、畫路線的走廊，
# 再把每個圓點點開後要顯示的 popup(小視窗) HTML 準備好。
#
# 這個檔案「只讀不寫」：它從不修改 state，所有改變都走 Flask 的表單送出。
#
# 座標提醒：Folium 要的是 [緯度, 經度]；OSRM 給的是 [經度, 緯度]，所以要對調。
# =====================================================================

from html import escape

import folium

from app.config import (
    DEFAULT_CENTER_LAT,
    DEFAULT_CENTER_LON,
    DEFAULT_ZOOM,
    GAME_OPENING_MOVES_PER_PLAYER,
)
from app.models import current_player_id, in_opening_phase, is_finished
from app.services.geometry import (
    build_meter_transformers,
    buffer_route_meters,
    route_to_meter_linestring,
)


# --- 顏色與大小設定 ---
_NEUTRAL_COLOR = "#8b93a1"                       # 中立 POI 的灰色
_PLAYER_COLORS = {1: "#2563eb", 2: "#ef4444"}    # 玩家1藍、玩家2紅
_SCORE_RADIUS = {1: 6, 2: 9, 3: 13}              # 分數越高圓點越大


def _owner_color(owner):
    """依擁有者回傳圓點顏色。"""
    if owner is None:
        return _NEUTRAL_COLOR
    return _PLAYER_COLORS.get(owner, _NEUTRAL_COLOR)


def _owner_label(owner):
    """擁有者的文字標籤。"""
    if owner is None:
        return "中立"
    return "Player " + str(owner)


def _score_radius(score):
    """依分數回傳圓點半徑。"""
    return _SCORE_RADIUS.get(score, 7)


# ---------------------------------------------------------------------
# popup（點開圓點後跳出的小視窗）HTML
# 注意：popup 裡「不可以」放 <script>，否則會把外層 Folium 的程式碼弄壞。
# ---------------------------------------------------------------------

def _popup_header(poi):
    """popup 最上面那塊：名字、分類、分數、擁有者。"""
    return (
        "<div style='font-weight:600;font-size:14px;margin-bottom:4px'>"
        + escape(poi["name"]) + "</div>"
        + "<div style='font-size:12px;color:#555;margin-bottom:6px'>"
        + escape(poi["category"]) + ":" + escape(poi["poi_type"])
        + " · 分數 " + str(poi["score"]) + " · "
        + _owner_label(poi["owner"]) + "</div>"
    )


def _opening_popup(poi):
    """開局階段、中立 POI 的 popup：一顆按鈕直接送出落子。"""
    return (
        _popup_header(poi)
        + '<form action="/move" method="post" target="_top" style="margin:0">'
        + '<input type="hidden" name="target_poi_id" value="' + escape(poi["id"]) + '">'
        + '<button type="submit" style="width:100%;padding:6px 10px;'
        + 'background:#16a34a;color:white;border:0;border-radius:4px;'
        + 'cursor:pointer">在此落子</button>'
        + '</form>'
    )


def _own_source_popup(poi, player_color):
    """正常回合、自己的 POI：一顆「選為起點」按鈕（只動 JS、不送表單）。"""
    poi_id_js = poi["id"].replace("'", "\\'")
    poi_name_js = poi["name"].replace("'", "\\'")
    return (
        _popup_header(poi)
        + '<button type="button" onclick="window.GeoflipSetSource('
        + "'" + poi_id_js + "', '" + poi_name_js + "'"
        + ')" style="width:100%;padding:6px 10px;background:' + player_color + ';'
        + 'color:white;border:0;border-radius:4px;cursor:pointer">'
        + '選為起點</button>'
    )


def _opponent_popup(poi):
    """正常回合、對手的 POI：不能選，只顯示說明。"""
    return (
        _popup_header(poi)
        + '<div style="color:#888;font-size:12px;font-style:italic">'
        + '對手的據點，不可選為起點</div>'
    )


def _neutral_target_popup(poi):
    """正常回合、中立 POI 的 popup：連線目標的表單，預設停用（要先選起點）。"""
    form_id = "tgt_" + poi["id"].replace(":", "_").replace("/", "_")
    return (
        _popup_header(poi)
        + '<form id="' + form_id + '" action="/move" method="post" target="_top" '
        + 'style="margin:0">'
        + '<input type="hidden" name="target_poi_id" value="' + escape(poi["id"]) + '">'
        + '<input type="hidden" name="source_poi_id" value="">'
        + '<div class="geoflip-source-hint" style="font-size:12px;'
        + 'color:#666;margin-bottom:4px">尚未選擇起點</div>'
        + '<button type="submit" disabled '
        + 'style="width:100%;padding:6px 10px;background:#9ca3af;color:white;'
        + 'border:0;border-radius:4px;cursor:not-allowed">連線到此</button>'
        + '</form>'
    )


def _finished_popup(poi):
    """遊戲已結束時，所有 POI 的 popup。"""
    return _popup_header(poi) + '<div style="font-size:12px;color:#666">遊戲已結束</div>'


# ---------------------------------------------------------------------
# 算出「最新一條路線」的折線 + 走廊多邊形（地圖只畫最新這一條）
# ---------------------------------------------------------------------

def _latest_route_overlay(state):
    """回傳 (折線座標, 走廊邊框座標)，都是 Folium 要的 [緯度, 經度] 格式。

    只畫最新一條路線：歷史路線全畫上去會把地圖塞爆，
    玩家想看的就是「剛剛這手走哪、影響範圍多大」。
    （所有歷史路線還是有存在 state["routes"]，沒有丟掉。）
    """
    if not state["routes"]:
        return None
    route = state["routes"][-1]
    coords_lonlat = route["coordinates_lonlat"]
    if len(coords_lonlat) < 2:
        return None

    # 折線：把每個 [經度, 緯度] 對調成 [緯度, 經度]
    polyline_latlon = []
    for lon, lat in coords_lonlat:
        polyline_latlon.append([float(lat), float(lon)])

    # 走廊：換成公尺 → 加寬 → 取邊框的點 → 換回經緯度
    ref_lon = coords_lonlat[0][0]
    ref_lat = coords_lonlat[0][1]
    to_meters, to_wgs = build_meter_transformers(ref_lon, ref_lat)
    line_m = route_to_meter_linestring(coords_lonlat, to_meters)
    poly_m = buffer_route_meters(line_m, route["buffer_m"])

    if poly_m.is_empty:
        return polyline_latlon, []

    ring_latlon = []
    for x, y in poly_m.exterior.coords:    # 走廊邊框上的每個公尺座標點
        lon, lat = to_wgs.transform(x, y)  # 換回經緯度
        ring_latlon.append([float(lat), float(lon)])
    return polyline_latlon, ring_latlon


# ---------------------------------------------------------------------
# 嵌進地圖頁的 JavaScript（負責跨 iframe 的「選起點」互動）
# 這段是網頁互動，必須維持原樣，UI 行為才不會變。
# ---------------------------------------------------------------------

def _page_script(game_id, turn_index):
    return """
<script>
(function() {
  var KEY = "geoflip_src_GAMEID_TURNINDEX";
  var TOP = (function() { try { return window.top; } catch (e) { return window; } })();

  function getStore() {
    try { return TOP.localStorage; } catch (e) { return localStorage; }
  }
  function getCached() {
    try { var raw = getStore().getItem(KEY); return raw ? JSON.parse(raw) : null; }
    catch (e) { return null; }
  }
  function setCached(v) {
    try { getStore().setItem(KEY, JSON.stringify(v)); } catch (e) {}
  }
  function clearCached() {
    try { getStore().removeItem(KEY); } catch (e) {}
  }
  function notifyParent(payload) {
    try { TOP.postMessage({ type: "geoflip-source", payload: payload }, "*"); }
    catch (e) {}
  }

  function hydrateAll() {
    document.querySelectorAll('form[id^="tgt_"]').forEach(function(f) {
      window.GeoflipHydrateTargetForm(f.id);
    });
  }

  function findMap() {
    var found = null;
    Object.keys(window).forEach(function(name) {
      var m = window[name];
      if (name.indexOf("map_") === 0 && m && m.on) found = m;
    });
    return found;
  }

  window.GeoflipSetSource = function(poiId, poiName) {
    setCached({ id: poiId, name: poiName });
    notifyParent({ id: poiId, name: poiName });
    hydrateAll();
  };

  window.GeoflipClearSource = function() {
    clearCached();
    notifyParent(null);
    hydrateAll();
  };

  window.GeoflipHydrateTargetForm = function(formId) {
    var f = document.getElementById(formId);
    if (!f) return;
    var src = getCached();
    var srcInput = f.querySelector('input[name="source_poi_id"]');
    var btn = f.querySelector('button[type="submit"]');
    var hint = f.querySelector('.geoflip-source-hint');
    if (src && srcInput && btn) {
      if (srcInput.value !== src.id) srcInput.value = src.id;
      if (btn.disabled) btn.disabled = false;
      if (btn.style.background !== "rgb(22, 163, 74)") btn.style.background = "#16a34a";
      if (btn.style.cursor !== "pointer") btn.style.cursor = "pointer";
      var selectedText = "起點：" + src.name;
      if (hint && hint.textContent !== selectedText) hint.textContent = selectedText;
    } else if (srcInput && btn) {
      if (srcInput.value !== "") srcInput.value = "";
      if (!btn.disabled) btn.disabled = true;
      if (btn.style.background !== "rgb(156, 163, 175)") btn.style.background = "#9ca3af";
      if (btn.style.cursor !== "not-allowed") btn.style.cursor = "not-allowed";
      var emptyText = "尚未選擇起點";
      if (hint && hint.textContent !== emptyText) hint.textContent = emptyText;
    }
  };

  document.addEventListener("DOMContentLoaded", function() {
    var map = findMap();
    if (map) map.on("popupopen", hydrateAll);
    hydrateAll();
  });

  var cached = getCached();
  if (cached) notifyParent(cached);
})();
</script>
""".replace("GAMEID", game_id).replace("TURNINDEX", str(turn_index))


# ---------------------------------------------------------------------
# 主函式：把整張地圖畫出來，回傳 HTML 文字
# ---------------------------------------------------------------------

def render_map_html(state):
    # 地圖中心：取所有 POI 的平均位置；沒有 POI 才用預設中心
    if state["pois"]:
        center_lat = sum(p["lat"] for p in state["pois"]) / len(state["pois"])
        center_lon = sum(p["lon"] for p in state["pois"]) / len(state["pois"])
    else:
        center_lat = DEFAULT_CENTER_LAT
        center_lon = DEFAULT_CENTER_LON

    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=DEFAULT_ZOOM,
        tiles="CartoDB voyager",
        control_scale=True,
    )

    finished = is_finished(state)
    cur = current_player_id(state)
    opening = (not finished) and in_opening_phase(state, cur, GAME_OPENING_MOVES_PER_PLAYER)
    player_color = _PLAYER_COLORS.get(cur, _NEUTRAL_COLOR)

    # --- 畫最新一條路線 + 走廊 ---
    overlay = _latest_route_overlay(state)
    if overlay is not None:
        polyline_latlon, buffer_ring = overlay
        latest_route = state["routes"][-1]
        route_color = _PLAYER_COLORS.get(latest_route["player_id"], _NEUTRAL_COLOR)

        if buffer_ring:
            folium.Polygon(
                locations=buffer_ring,
                color=route_color,
                weight=1,
                fill=True,
                fill_color=route_color,
                fill_opacity=0.12,
            ).add_to(fmap)

        folium.PolyLine(
            locations=polyline_latlon,
            color=route_color,
            weight=4,
            opacity=0.85,
        ).add_to(fmap)

    # --- 畫每個 POI 的圓點 ---
    for poi in state["pois"]:
        color = _owner_color(poi["owner"])

        # 依「遊戲階段 + 這個 POI 屬於誰」決定 popup 內容
        if finished:
            popup_html = _finished_popup(poi)
        elif opening:
            if poi["owner"] is None:
                popup_html = _opening_popup(poi)
            else:
                popup_html = _popup_header(poi)
        else:
            if poi["owner"] == cur:
                popup_html = _own_source_popup(poi, player_color)
            elif poi["owner"] is None:
                popup_html = _neutral_target_popup(poi)
            else:
                popup_html = _opponent_popup(poi)

        folium.CircleMarker(
            location=[poi["lat"], poi["lon"]],
            radius=_score_radius(poi["score"]),
            color=color,
            weight=2,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=escape(poi["name"]),
        ).add_to(fmap)

    # --- 把地圖縮放到剛好框住所有 POI ---
    if state["pois"]:
        bounds = [
            [min(p["lat"] for p in state["pois"]), min(p["lon"] for p in state["pois"])],
            [max(p["lat"] for p in state["pois"]), max(p["lon"] for p in state["pois"])],
        ]
        fmap.fit_bounds(bounds, padding=(20, 20))

    # --- 把我們的 JavaScript 塞進 Folium 產生的 HTML 裡 ---
    html = fmap.get_root().render()
    script = _page_script(state["game_id"], state["turn_index"])
    if "</body>" in html:
        html = html.replace("</body>", script + "</body>")
    else:
        html = html + script
    return html
