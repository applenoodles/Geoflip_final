"""Folium map rendering.

Read-only — never mutates state. All state changes go through Flask POSTs.

Coordinate conventions:
  - Folium markers / polylines: [lat, lon]
  - RouteRecord.coordinates_lonlat: GeoJSON [lon, lat] (flipped here for Folium)

Popup interactions:
  - Forms POST to /move (or /pass) with target="_top" so submission lands
    on the parent page, not inside the iframe.
  - Source-selection uses window.top.localStorage keyed by game_id + turn_index;
    a postMessage notifies the parent sidebar of the choice.
"""
from __future__ import annotations

from html import escape

import folium
from shapely.ops import transform as shapely_transform

from app.config import Config
from app.models import (
    OPENING_MOVES_PER_PLAYER,
    GameState,
    Poi,
)
from app.services.geometry import (
    build_meter_transformers,
    buffer_route_meters,
    route_to_meter_linestring,
)


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

_NEUTRAL_COLOR = "#8b93a1"
_PLAYER_COLORS: dict[int, str] = {1: "#2563eb", 2: "#ef4444"}
_SCORE_RADIUS: dict[int, int] = {1: 6, 2: 9, 3: 13}


def _owner_color(owner: int | None) -> str:
    if owner is None:
        return _NEUTRAL_COLOR
    return _PLAYER_COLORS.get(owner, _NEUTRAL_COLOR)


def _owner_label(owner: int | None) -> str:
    if owner is None:
        return "中立"
    return f"Player {owner}"


def _score_radius(score: int) -> int:
    return _SCORE_RADIUS.get(score, 7)


# ---------------------------------------------------------------------------
# Popup HTML
# ---------------------------------------------------------------------------

def _popup_header(poi: Poi) -> str:
    return (
        f"<div style='font-weight:600;font-size:14px;margin-bottom:4px'>"
        f"{escape(poi.name)}</div>"
        f"<div style='font-size:12px;color:#555;margin-bottom:6px'>"
        f"{escape(poi.category)}:{escape(poi.poi_type)} · 分數 {poi.score} · "
        f"{_owner_label(poi.owner)}</div>"
    )


def _opening_popup(poi: Poi) -> str:
    """開局階段中立 POI 的 popup：一個按鈕直接 POST /move（只帶 target_poi_id）。

    target="_top" 讓表單在父頁面送出，不會被困在 iframe 裡。
    """
    return (
        _popup_header(poi)
        + f'<form action="/move" method="post" target="_top" '
          f'style="margin:0">'
          f'<input type="hidden" name="target_poi_id" value="{escape(poi.id)}">'
          f'<button type="submit" style="width:100%;padding:6px 10px;'
          f'background:#16a34a;color:white;border:0;border-radius:4px;'
          f'cursor:pointer">在此落子</button>'
          f'</form>'
    )


def _own_source_popup(poi: Poi, player_color: str) -> str:
    """正常回合自己的 POI：選為起點按鈕（只動 JS、不送表單）。"""
    poi_id_js = poi.id.replace("'", "\\'")
    poi_name_js = poi.name.replace("'", "\\'")
    return (
        _popup_header(poi)
        + f'<button type="button" onclick="window.GeoflipSetSource('
          f"'{poi_id_js}', '{poi_name_js}'"
          f')" style="width:100%;padding:6px 10px;background:{player_color};'
          f'color:white;border:0;border-radius:4px;cursor:pointer">'
          f'選為起點</button>'
    )


def _opponent_popup(poi: Poi) -> str:
    return (
        _popup_header(poi)
        + '<div style="color:#888;font-size:12px;font-style:italic">'
          '對手的據點，不可選為起點</div>'
    )


def _neutral_target_popup(poi: Poi) -> str:
    """正常回合中立 POI 的 popup：target 表單，預設禁用。

    這裡刻意 *不* 內嵌 <script>：popup 內若放 </script>，瀏覽器會把外層 Folium
    的 <script> 區塊提前關閉，造成後續 JS 以純文字洩漏到 popup 顯示出來（前一版
    的 bug）。hydration 改由 _page_script 裡的 MutationObserver 統一處理。
    """
    form_id = f"tgt_{poi.id.replace(':', '_').replace('/', '_')}"
    return (
        _popup_header(poi)
        + f'<form id="{form_id}" action="/move" method="post" target="_top" '
          f'style="margin:0">'
          f'<input type="hidden" name="target_poi_id" value="{escape(poi.id)}">'
          f'<input type="hidden" name="source_poi_id" value="">'
          f'<div class="geoflip-source-hint" style="font-size:12px;'
          f'color:#666;margin-bottom:4px">尚未選擇起點</div>'
          f'<button type="submit" disabled '
          f'style="width:100%;padding:6px 10px;background:#9ca3af;color:white;'
          f'border:0;border-radius:4px;cursor:not-allowed">連線到此</button>'
          f'</form>'
    )


def _finished_popup(poi: Poi) -> str:
    return _popup_header(poi) + '<div style="font-size:12px;color:#666">遊戲已結束</div>'


# ---------------------------------------------------------------------------
# Buffer polygon → WGS84 ring for Folium
# ---------------------------------------------------------------------------

def _latest_route_overlay(state: GameState) -> tuple[list[list[float]], list[list[float]]] | None:
    """只回傳「最新」一條路線的 polyline + buffer polygon。

    為什麼只畫最新一條：歷史路線堆疊起來會把地圖塞爆，且舊路線對「當下該怎麼下」
    沒幫助 —— 玩家想知道的就是「剛剛這手走了哪條路、影響範圍多大」。
    所有歷史 RouteRecord 仍保留在 state.routes 裡（檔案不丟資料）。
    """
    if not state.routes:
        return None
    route = state.routes[-1]
    coords_lonlat = route.coordinates_lonlat
    if len(coords_lonlat) < 2:
        return None

    polyline_latlon = [[float(lat), float(lon)] for lon, lat in coords_lonlat]

    # Buffer: project → buffer in meters → project back to WGS84.
    ref_lon, ref_lat = coords_lonlat[0][0], coords_lonlat[0][1]
    to_m, to_wgs = build_meter_transformers(ref_lon, ref_lat)
    line_m = route_to_meter_linestring(coords_lonlat, to_m)
    poly_m = buffer_route_meters(line_m, route.buffer_m)
    poly_wgs = shapely_transform(lambda x, y, z=None: to_wgs.transform(x, y), poly_m)

    if poly_wgs.is_empty:
        return polyline_latlon, []
    # poly_wgs.exterior.coords yields (lon, lat). Flip for Folium.
    ring_latlon = [[float(lat), float(lon)] for lon, lat in poly_wgs.exterior.coords]
    return polyline_latlon, ring_latlon


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------

def _page_script(game_id: str, turn_index: int) -> str:
    """Return iframe JS for source selection and target popup hydration."""
    return f"""
<script>
(function() {{
  var KEY = "geoflip_src_{game_id}_{turn_index}";
  // 視角記憶用 game_id（不綁 turn）：同一盤每手之間都記得使用者的縮放/平移，
  // 開新局換 game_id 後自動失效，不會殘留上一盤的視角。
  var VIEW_KEY = "geoflip_view_{game_id}";
  var TOP = (function() {{ try {{ return window.top; }} catch (e) {{ return window; }} }})();

  function getStore() {{
    try {{ return TOP.localStorage; }} catch (e) {{ return localStorage; }}
  }}
  function getCached() {{
    try {{ var raw = getStore().getItem(KEY); return raw ? JSON.parse(raw) : null; }}
    catch (e) {{ return null; }}
  }}
  function setCached(v) {{
    try {{ getStore().setItem(KEY, JSON.stringify(v)); }} catch (e) {{}}
  }}
  function clearCached() {{
    try {{ getStore().removeItem(KEY); }} catch (e) {{}}
  }}
  function notifyParent(payload) {{
    try {{ TOP.postMessage({{ type: "geoflip-source", payload: payload }}, "*"); }}
    catch (e) {{}}
  }}

  function hydrateAll() {{
    document.querySelectorAll('form[id^="tgt_"]').forEach(function(f) {{
      window.GeoflipHydrateTargetForm(f.id);
    }});
  }}

  function findMap() {{
    var found = null;
    Object.keys(window).forEach(function(name) {{
      var m = window[name];
      if (name.indexOf("map_") === 0 && m && m.on && m.setView) found = m;
    }});
    return found;
  }}

  function saveView(map) {{
    try {{
      var c = map.getCenter();
      getStore().setItem(VIEW_KEY, JSON.stringify(
        {{ lat: c.lat, lng: c.lng, zoom: map.getZoom() }}
      ));
    }} catch (e) {{}}
  }}

  function restoreView(map) {{
    try {{
      var raw = getStore().getItem(VIEW_KEY);
      if (!raw) return;
      var v = JSON.parse(raw);
      if (v && isFinite(v.lat) && isFinite(v.lng) && isFinite(v.zoom)) {{
        map.setView([v.lat, v.lng], v.zoom, {{ animate: false }});
      }}
    }} catch (e) {{}}
  }}

  window.GeoflipSetSource = function(poiId, poiName) {{
    setCached({{ id: poiId, name: poiName }});
    notifyParent({{ id: poiId, name: poiName }});
    hydrateAll();
  }};

  window.GeoflipClearSource = function() {{
    clearCached();
    notifyParent(null);
    hydrateAll();
  }};

  window.GeoflipHydrateTargetForm = function(formId) {{
    var f = document.getElementById(formId);
    if (!f) return;
    var src = getCached();
    var srcInput = f.querySelector('input[name="source_poi_id"]');
    var btn = f.querySelector('button[type="submit"]');
    var hint = f.querySelector('.geoflip-source-hint');
    if (src && srcInput && btn) {{
      if (srcInput.value !== src.id) srcInput.value = src.id;
      if (btn.disabled) btn.disabled = false;
      if (btn.style.background !== "rgb(22, 163, 74)") btn.style.background = "#16a34a";
      if (btn.style.cursor !== "pointer") btn.style.cursor = "pointer";
      var selectedText = "起點：" + src.name;
      if (hint && hint.textContent !== selectedText) hint.textContent = selectedText;
    }} else if (srcInput && btn) {{
      if (srcInput.value !== "") srcInput.value = "";
      if (!btn.disabled) btn.disabled = true;
      if (btn.style.background !== "rgb(156, 163, 175)") btn.style.background = "#9ca3af";
      if (btn.style.cursor !== "not-allowed") btn.style.cursor = "not-allowed";
      var emptyText = "尚未選擇起點";
      if (hint && hint.textContent !== emptyText) hint.textContent = emptyText;
    }}
  }};

  document.addEventListener("DOMContentLoaded", function() {{
    var map = findMap();
    if (map) {{
      map.on("popupopen", hydrateAll);
      // 先還原上一手的視角（蓋掉 Folium 的 fit_bounds），再掛存檔監聽，
      // 避免還原當下的 moveend 把預設視角寫回去。
      restoreView(map);
      map.on("moveend", function() {{ saveView(map); }});
      map.on("zoomend", function() {{ saveView(map); }});
    }}
    hydrateAll();
  }});

  var cached = getCached();
  if (cached) notifyParent(cached);
}})();
</script>
"""


def render_map_html(state: GameState, config: Config) -> str:
    if state.pois:
        center_lat = sum(p.lat for p in state.pois) / len(state.pois)
        center_lon = sum(p.lon for p in state.pois) / len(state.pois)
    else:
        center_lat = config.DEFAULT_CENTER_LAT
        center_lon = config.DEFAULT_CENTER_LON

    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=config.DEFAULT_ZOOM,
        tiles="CartoDB voyager",
        control_scale=True,
    )

    finished = state.is_finished()
    current_player_id = state.current_player_id()
    in_opening_phase = (
        not finished
        and state.in_opening_phase(current_player_id, OPENING_MOVES_PER_PLAYER)
    )
    player_color = _PLAYER_COLORS.get(current_player_id, _NEUTRAL_COLOR)


    # --- Latest route + buffer ---
    overlay = _latest_route_overlay(state)
    if overlay is not None:
        polyline_latlon, buffer_ring = overlay
        latest_route = state.routes[-1]
        route_color = _PLAYER_COLORS.get(latest_route.player_id, _NEUTRAL_COLOR)

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

    # --- POI markers ---
    for poi in state.pois:
        color = _owner_color(poi.owner)

        if finished:
            popup_html = _finished_popup(poi)
        elif in_opening_phase:
            if poi.owner is None:
                popup_html = _opening_popup(poi)
            else:
                popup_html = _popup_header(poi)
        else:
            if poi.owner == current_player_id:
                popup_html = _own_source_popup(poi, player_color)
            elif poi.owner is None:
                popup_html = _neutral_target_popup(poi)
            else:
                popup_html = _opponent_popup(poi)

        folium.CircleMarker(
            location=[poi.lat, poi.lon],
            radius=_score_radius(poi.score),
            color=color,
            weight=2,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            popup=folium.Popup(popup_html, max_width=280),
            tooltip=escape(poi.name),
        ).add_to(fmap)

    # --- Fit bounds to POIs ---
    if state.pois:
        bounds = [
            [min(p.lat for p in state.pois), min(p.lon for p in state.pois)],
            [max(p.lat for p in state.pois), max(p.lon for p in state.pois)],
        ]
        fmap.fit_bounds(bounds, padding=(20, 20))

    # --- Inject our page-level JS into the HTML ---
    html = fmap.get_root().render()
    script = _page_script(state.game_id, state.turn_index)
    if "</body>" in html:
        html = html.replace("</body>", script + "</body>")
    else:
        html += script
    return html
