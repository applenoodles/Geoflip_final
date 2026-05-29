# =====================================================================
# 「存檔」相關：把整場遊戲的 state 字典存成一個 JSON 檔，或讀回來。
#
# JSON 是一種文字格式，長得跟 Python 的字典/串列幾乎一樣，
# 所以我們的 state（一個大字典）可以很自然地存成 JSON 檔。
#
# 原本這裡用了 class 跟「先寫暫存檔再換掉」的防當機寫法，
# 現在改成最單純的：要存就直接寫檔、要讀就直接讀檔。
# =====================================================================

import json
import os

from app.models import new_game


def load_state(path, max_turns=20):
    """讀取存檔。

    如果存檔還不存在（第一次玩、或剛開新局），就回傳一場全新遊戲。
    """
    if not os.path.exists(path):
        return new_game(max_turns)

    # with open(...) 會自動幫我們把檔案開好、用完關掉
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)   # 把 JSON 文字轉回 Python 字典


def save_state(path, state):
    """把目前的 state 存回檔案。"""
    # 確定資料夾存在（例如 data/ 第一次還沒被建出來）
    folder = os.path.dirname(path)
    if folder and not os.path.exists(folder):
        os.makedirs(folder)

    with open(path, "w", encoding="utf-8") as f:
        # ensure_ascii=False 讓中文正常顯示；indent=2 讓檔案排版好看
        json.dump(state, f, ensure_ascii=False, indent=2)


def reset_state(path):
    """刪掉存檔（按「新開局」時用）。"""
    if os.path.exists(path):
        os.remove(path)
