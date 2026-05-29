"""JSON state store —— GeoFlip 的「資料庫」。

整場遊戲只有一個 GameState，存成一個 JSON 檔（data/state.json）。
寫檔用 tmp + replace 的 atomic write，避免寫到一半當機留下半套檔案。
JSON 壞掉時要明確 raise，絕不 silent reset，否則玩家的資料會神秘消失。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.models import GameState, PlayerState


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_game() -> GameState:
    now = _now_iso()
    return GameState(
        game_id="game_" + uuid.uuid4().hex,
        turn_index=0,
        max_turns=20,
        players={
            1: PlayerState(id=1, name="Player 1"),
            2: PlayerState(id=2, name="Player 2"),
        },
        pois=[],
        routes=[],
        moves=[],
        created_at=now,
        updated_at=now,
        status="active",
    )


class StateStore:
    def __init__(self, path: str | os.PathLike) -> None:
        self._path = Path(path)

    def load(self) -> GameState:
        if not self._path.exists():
            return new_game()
        raw = self._path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"State file {self._path} contains invalid JSON: {exc}"
            ) from exc
        return GameState.from_dict(data)

    def save(self, state: GameState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    def reset(self) -> None:
        if self._path.exists():
            self._path.unlink()
