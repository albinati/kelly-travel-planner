"""Environment-backed settings — load .env from project root (no OpenClaw env injection required)."""

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


def _path(name: str, default: str) -> Path:
    raw = os.environ.get(name)
    if raw:
        return Path(raw).expanduser()
    return (_PROJECT_ROOT / default).resolve()


def serpapi_api_key() -> str | None:
    return os.environ.get("SERPAPI_API_KEY")


def rapidapi_key() -> str | None:
    return os.environ.get("RAPIDAPI_KEY")


def seats_aero_key() -> str | None:
    return os.environ.get("SEATS_AERO_API_KEY")


def cash_backend() -> str:
    """serpapi | rapidapi — default rapidapi when RAPIDAPI_KEY set, else serpapi."""
    explicit = (os.environ.get("KELLY_CASH_BACKEND") or "").strip().lower()
    if explicit in ("serpapi", "rapidapi"):
        return explicit
    if rapidapi_key():
        return "rapidapi"
    return "serpapi"


def rapidapi_google_flights_host() -> str:
    return os.environ.get(
        "RAPIDAPI_GOOGLE_FLIGHTS_HOST",
        "google-flights-data.p.rapidapi.com",
    ).strip()


def rapidapi_google_flights_base_url() -> str:
    return os.environ.get(
        "RAPIDAPI_GOOGLE_FLIGHTS_BASE",
        "https://google-flights-data.p.rapidapi.com",
    ).rstrip("/")


def rapidapi_google_flights_calendar_path() -> str:
    """GET path for one-call monthly / range calendar (provider-specific)."""
    return os.environ.get(
        "RAPIDAPI_GOOGLE_FLIGHTS_CALENDAR_PATH",
        "/flights/get-cheapest-flights-by-month",
    ).strip() or "/flights/get-cheapest-flights-by-month"


def rapidapi_kiwi_host() -> str:
    return os.environ.get(
        "RAPIDAPI_KIWI_HOST",
        "kiwi-com-cheap-flights.p.rapidapi.com",
    ).strip()


def rapidapi_booking_host() -> str | None:
    h = os.environ.get("RAPIDAPI_BOOKING_HOST", "").strip()
    return h or None


def rapidapi_tripadvisor_host() -> str | None:
    h = os.environ.get("RAPIDAPI_TRIPADVISOR_HOST", "").strip()
    return h or None


def anomaly_opportunity_below_p10() -> bool:
    return os.environ.get("KELLY_ANOMALY_USE_P10", "true").lower() in ("1", "true", "yes")


def anomaly_min_hist_samples() -> int:
    return int(os.environ.get("KELLY_ANOMALY_MIN_SAMPLES", "3"))


def pipeline_max_heavy_calls() -> int:
    return max(1, min(20, int(os.environ.get("KELLY_PIPELINE_MAX_HEAVY", "5"))))


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


def toolkit_data_dir() -> Path | None:
    raw = os.environ.get("TRAVEL_HACKING_TOOLKIT_DATA")
    if raw:
        return Path(raw).expanduser()
    vendor = _PROJECT_ROOT / "vendor" / "travel-hacking-toolkit" / "data"
    if vendor.is_dir():
        return vendor
    return None
