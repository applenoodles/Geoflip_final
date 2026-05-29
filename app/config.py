from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_float(name: str, default: str) -> float:
    return float(os.getenv(name, default))


def _env_int(name: str, default: str) -> int:
    return int(os.getenv(name, default))


@dataclass
class Config:
    # =======================================================================
    # 調參數區 —— 想改遊戲行為，改下面每行引號裡的數字就好。
    # 改完記得：① 重啟 server　② 開新局（舊棋盤的路線是存檔的，不會變）
    #
    #   GAME_MAX_TURNS         : 整場總回合數（只對「新開的局」生效）
    #   GAME_OPENING_MOVES_PER_PLAYER : 每位玩家開局先佈幾子
    #   GAME_MAX_WALK_SECONDS  : 新旗要在己方旗子步行幾秒內才能連（越小越難）
    #   GAME_BUFFER_NORMAL_M   : 路線走廊寬度（公尺）
    #   OVERPASS_MIN_SPACING_M : POI 之間最小間距（避免擠成一坨）
    #   OVERPASS_RADIUS_M      : 抓 POI 的半徑（公尺）
    #   OSRM_BASE_URL          : 路由服務（routed-foot=步行；router.project-osrm=車程）
    # =======================================================================
    DEFAULT_CENTER_LAT: float = field(
        default_factory=lambda: _env_float("DEFAULT_CENTER_LAT", "25.0330")
    )
    DEFAULT_CENTER_LON: float = field(
        default_factory=lambda: _env_float("DEFAULT_CENTER_LON", "121.5654")
    )
    DEFAULT_ZOOM: int = field(
        default_factory=lambda: _env_int("DEFAULT_ZOOM", "15")
    )

    NOMINATIM_USER_AGENT: str = field(
        default_factory=lambda: _env_str(
            "NOMINATIM_USER_AGENT",
            "geoflip-coursework/0.1 (geoflip@example.com)",
        )
    )
    NOMINATIM_EMAIL: str = field(
        default_factory=lambda: _env_str("NOMINATIM_EMAIL", "geoflip@example.com")
    )
    NOMINATIM_BASE_URL: str = field(
        default_factory=lambda: _env_str(
            "NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org"
        )
    )
    NOMINATIM_MIN_INTERVAL_SECONDS: float = field(
        default_factory=lambda: _env_float("NOMINATIM_MIN_INTERVAL_SECONDS", "1.0")
    )

    OSRM_BASE_URL: str = field(
        default_factory=lambda: _env_str(
            "OSRM_BASE_URL", "https://routing.openstreetmap.de/routed-foot"
        )
    )
    OSRM_PROFILE: str = field(
        default_factory=lambda: _env_str("OSRM_PROFILE", "foot")
    )

    OVERPASS_BASE_URL: str = field(
        default_factory=lambda: _env_str(
            "OVERPASS_BASE_URL", "https://overpass-api.de"
        )
    )
    OVERPASS_RADIUS_M: float = field(
        default_factory=lambda: _env_float("OVERPASS_RADIUS_M", "500")
    )
    OVERPASS_MIN_POIS: int = field(
        default_factory=lambda: _env_int("OVERPASS_MIN_POIS", "18")
    )
    OVERPASS_MAX_POIS: int = field(
        default_factory=lambda: _env_int("OVERPASS_MAX_POIS", "36")
    )
    OVERPASS_TIMEOUT_SECONDS: float = field(
        default_factory=lambda: _env_float("OVERPASS_TIMEOUT_SECONDS", "25")
    )
    OVERPASS_MIN_SPACING_M: float = field(
        default_factory=lambda: _env_float("OVERPASS_MIN_SPACING_M", "30")
    )

    GAME_MAX_WALK_SECONDS: float = field(
        default_factory=lambda: _env_float("GAME_MAX_WALK_SECONDS", "600")
    )
    GAME_BUFFER_NORMAL_M: float = field(
        default_factory=lambda: _env_float("GAME_BUFFER_NORMAL_M", "50")
    )
    GAME_MAX_TURNS: int = field(
        default_factory=lambda: _env_int("GAME_MAX_TURNS", "20")
    )
    GAME_OPENING_MOVES_PER_PLAYER: int = field(
        default_factory=lambda: _env_int("GAME_OPENING_MOVES_PER_PLAYER", "2")
    )

    STATE_FILE: str = field(
        default_factory=lambda: _env_str("STATE_FILE", "data/state.json")
    )
    REQUEST_TIMEOUT_SECONDS: float = field(
        default_factory=lambda: _env_float("REQUEST_TIMEOUT_SECONDS", "10")
    )
