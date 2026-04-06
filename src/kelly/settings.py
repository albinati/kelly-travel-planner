"""Environment-backed settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def duffel_token() -> str | None:
    return os.environ.get("DUFFEL_ACCESS_TOKEN") or os.environ.get("DUFFEL_API_KEY")


def seats_aero_key() -> str | None:
    return os.environ.get("SEATS_AERO_API_KEY")


def config_path() -> Path:
    return _path("KELLY_CONFIG_PATH", "config/kelly.md")


def data_dir() -> Path:
    return _path("KELLY_DATA_DIR", "data")


def history_db_path() -> Path:
    return data_dir() / "kelly_history.sqlite"


def toolkit_data_dir() -> Path | None:
    raw = os.environ.get("TRAVEL_HACKING_TOOLKIT_DATA")
    if raw:
        return Path(raw).expanduser()
    vendor = Path("vendor/travel-hacking-toolkit/data")
    if vendor.is_dir():
        return vendor
    return None
