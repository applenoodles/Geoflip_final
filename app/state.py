# =====================================================================
# 「存檔」用一個 class：StateStore（state store = 狀態倉庫）。
#
# 它記住「存檔檔案的路徑」，並提供三個方法：
#   load()  讀存檔，回傳一個 GameState 物件
#   save()  把一個 GameState 物件存成 JSON 檔
#   reset() 刪掉存檔（按「新開局」時用）
#
# JSON 是一種文字格式，長得跟 Python 的字典/串列幾乎一樣。
# GameState 物件先用 .to_dict() 變成字典，再用 json 存成文字。
# =====================================================================

import json
import os

from app.models import GameState


class StateStore:
    def __init__(self, path, max_turns=20):
        # 把設定存到物件身上，之後每個方法都能用 self.xxx 拿到
        self.path = path
        self.max_turns = max_turns

    def load(self):
        """讀存檔。檔案還不存在（第一次玩）就回傳一場全新遊戲。"""
        if not os.path.exists(self.path):
            return GameState.new_game(self.max_turns)
        with open(self.path, "r", encoding="utf-8") as f:   # 開檔，用完自動關
            data = json.load(f)                              # JSON 文字 → 字典
        return GameState.from_dict(data)                     # 字典 → GameState 物件

    def save(self, state):
        """把目前的 GameState 物件存回檔案。"""
        folder = os.path.dirname(self.path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)                              # 確保 data/ 資料夾存在
        with open(self.path, "w", encoding="utf-8") as f:
            # ensure_ascii=False 讓中文正常顯示；indent=2 讓檔案排版好看
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)

    def reset(self):
        """刪掉存檔。"""
        if os.path.exists(self.path):
            os.remove(self.path)
