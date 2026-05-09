"""Environment-backed settings — load .env from project root."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def find_project_root() -> Path:
    """Directory containing pyproject.toml, starting from this file."""
    env = os.environ.get("KELLY_PROJECT_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve().parent
    for p in [here, *here.parents]:
        if (p / "pyproject.toml").is_file():
            return p
    return here.parent


_PROJECT_ROOT = find_project_root()
load_dotenv(_PROJECT_ROOT / ".env", override=False)
load_dotenv(_PROJECT_ROOT / ".env.local", override=False)


def project_root() -> Path:
    return _PROJECT_ROOT


def config_path() -> Path:
    raw = os.environ.get("KELLY_CONFIG_PATH")
    if raw:
        return Path(raw).expanduser()
    return _PROJECT_ROOT / "config" / "kelly.md"


def data_dir() -> Path:
    raw = os.environ.get("KELLY_DATA_DIR")
    if raw:
        return Path(raw).expanduser()
    return _PROJECT_ROOT / "data"


def history_db_path() -> Path:
    return data_dir() / "kelly_history.sqlite"
