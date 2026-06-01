"""
跑一次真實的 Nominatim + Overpass，走完開局四手，
把結果存成 data/demo_state.json。
用法：python make_demo_state.py
"""

import json
import os

from app import config
from app.game.rules import RulesEngine
from app.models import GameState
from app.services.nominatim import NominatimClient
from app.services.overpass import OverpassClient


def main():
    # 1. 用 Nominatim 找清大新竹的座標
    nominatim = NominatimClient()
    print("搜尋地點中...")
    candidates = nominatim.search_locations("國立清華大學 新竹", limit=3)
    if not candidates:
        print("找不到地點，換個關鍵字試試")
        return

    best = candidates[0]
    lat = float(best["lat"])
    lon = float(best["lon"])
    print(f"使用：{best['display_name']}")
    print(f"座標：{lat}, {lon}")

    # 2. 用 Overpass 抓附近 POI
    overpass = OverpassClient(min_spacing_m=config.OVERPASS_MIN_SPACING_M)
    print("抓取 POI 中（可能需要幾秒）...")
    pois = overpass.fetch_board_pois(lat, lon, config.OVERPASS_RADIUS_M, limit=config.OVERPASS_MAX_POIS)
    print(f"抓到 {len(pois)} 個 POI")

    if len(pois) < config.OVERPASS_MIN_POIS:
        print(f"POI 太少（至少需要 {config.OVERPASS_MIN_POIS} 個），換地點試試")
        return

    # 3. 建立新遊戲，把 POI 灌進去
    state = GameState.new_game(config.GAME_MAX_TURNS)
    state.merge_discovered_pois(pois)

    # 4. 走完開局四手（P1兩手、P2兩手）
    #    開局不呼叫 OSRM，傳 None 進去也沒問題
    engine = RulesEngine()
    neutral = state.neutral_pois()

    # P1 第一手（選第 0 個中立點）
    result = engine.apply_move(state, neutral[0].id, None)
    state = result["state"]
    print(f"P1 佈子 1：{neutral[0].name}")

    # P2 第一手（選最後一個，離 P1 遠一點）
    neutral = state.neutral_pois()
    result = engine.apply_move(state, neutral[-1].id, None)
    state = result["state"]
    print(f"P2 佈子 1：{neutral[-1].name}")

    # P1 第二手（選第 1 個）
    neutral = state.neutral_pois()
    result = engine.apply_move(state, neutral[1].id, None)
    state = result["state"]
    print(f"P1 佈子 2：{neutral[1].name}")

    # P2 第二手（選倒數第二個）
    neutral = state.neutral_pois()
    result = engine.apply_move(state, neutral[-2].id, None)
    state = result["state"]
    print(f"P2 佈子 2：{neutral[-2].name}")

    # 5. 存檔
    os.makedirs("data", exist_ok=True)
    out_path = "data/demo_state.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)

    print(f"\n[OK] 已存到 {out_path}")
    print(f"目前回合：{state.turn_index}（開局已結束，輪到正常回合）")
    print(f"P1 擁有：{[p.name for p in state.owned_pois(1)]}")
    print(f"P2 擁有：{[p.name for p in state.owned_pois(2)]}")
    print(f"中立 POI：{len(state.neutral_pois())} 個")


if __name__ == "__main__":
    main()
