"""MCP server (stdio) for Kelly — group trip planning over Eurostar + Airbnb.

Install with `poetry install --extras mcp`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:
    raise SystemExit(
        "The `mcp` package is required. Install with: poetry install --extras mcp"
    ) from e

from kelly.history_store import open_default_store
from kelly.md_config import StayRow, TrainRow, load_kelly_config
from kelly.services.stay_service import search_stay, stay_result_to_jsonable
from kelly.services.train_service import search_train, train_result_to_jsonable
from kelly.services.trip_planner import plan_trip
from kelly.settings import config_path as default_config_path

mcp = FastMCP(
    "Kelly",
    instructions=(
        "Kelly plans group trips from a Markdown config — Eurostar trains plus Airbnb stays. "
        "No API keys required: pyairbnb hits Airbnb's internal staysSearch GraphQL and patchright "
        "drives a stealth Chromium against eurostar.com. Searches are persisted to a local SQLite "
        "file (KELLY_DATA_DIR) so we can build per-trip price baselines over time."
    ),
)


def _cfg(p: str | None) -> Path:
    return Path(p).expanduser() if p else default_config_path()


@mcp.tool()
def kelly_load_config(config_path: str | None = None) -> str:
    """Load and validate kelly.md; return JSON summary of trains and stays."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    return json.dumps(
        {
            "frontmatter": cfg.frontmatter.model_dump(mode="json"),
            "trains": [t.model_dump(mode="json", by_alias=True) for t in cfg.trains],
            "stays": [s.model_dump(mode="json") for s in cfg.stays],
        },
        default=str,
    )


@mcp.tool()
def kelly_eurostar_search(
    origin_city: str,
    destination_city: str,
    date_start: str,
    date_end: str,
    adults: int,
    seniors: int = 0,
    teens: int = 0,
    children_ages: str = "",
    class_: str = "standard",
    config_path: str | None = None,
    persist: bool = True,
) -> str:
    """One-shot Eurostar search (via patchright). children_ages: comma-separated ints (e.g. '3,4,5')."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    ages = [int(x.strip()) for x in children_ages.split(",") if x.strip()]
    row = TrainRow(
        id="__adhoc__",
        operator="eurostar",
        origin_city=origin_city,
        destination_city=destination_city,
        date_start=date.fromisoformat(date_start),
        date_end=date.fromisoformat(date_end),
        **{"class": class_},
        adults=adults,
        seniors=seniors,
        teens=teens,
        children_ages=ages,
    )
    store = open_default_store() if persist else None
    return json.dumps(
        train_result_to_jsonable(search_train(cfg, row, store=store, persist=persist)),
        default=str,
    )


@mcp.tool()
def kelly_airbnb_search(
    area: str,
    check_in: str,
    check_out: str,
    adults: int,
    children_ages: str = "",
    bedrooms_min: int = 1,
    near: str = "",
    max_total: float | None = None,
    config_path: str | None = None,
    persist: bool = True,
) -> str:
    """One-shot Airbnb search (via pyairbnb). children_ages / near: comma-separated strings."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    ages = [int(x.strip()) for x in children_ages.split(",") if x.strip()]
    near_list = [x.strip() for x in near.split(",") if x.strip()]
    row = StayRow(
        id="__adhoc__",
        area=area,
        check_in=date.fromisoformat(check_in),
        check_out=date.fromisoformat(check_out),
        adults=adults,
        children_ages=ages,
        bedrooms_min=bedrooms_min,
        near=near_list,
        max_total=max_total,
    )
    store = open_default_store() if persist else None
    return json.dumps(
        stay_result_to_jsonable(search_stay(cfg, row, store=store, persist=persist)),
        default=str,
    )


@mcp.tool()
def kelly_plan_trip(trip_id: str, config_path: str | None = None, persist: bool = True) -> str:
    """Plan a group trip declared in kelly.md (## Trains / ## Stays sections).

    Looks up trains `<trip_id>-out` and `<trip_id>-back` (or a single `<trip_id>` train)
    plus the stay with id `<trip_id>`; returns combined JSON with search results,
    a curated shortlist, and Eurostar-Groups Desk hints for parties of 10+.
    """
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    store = open_default_store() if persist else None
    return json.dumps(plan_trip(cfg, trip_id, store=store, persist=persist), default=str)


@mcp.resource("kelly://config", mime_type="text/markdown")
def kelly_config_resource() -> str:
    """The current kelly.md as Markdown."""
    path = default_config_path()
    if not path.is_file():
        return f"# kelly.md not found at {path}\n"
    return path.read_text(encoding="utf-8")


def main() -> None:
    """Entrypoint for `kelly-mcp` script — runs the stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
