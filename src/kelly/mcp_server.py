"""MCP server (stdio) for Kelly — install with `poetry install --extras mcp`."""

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

from kelly.md_config import load_kelly_config
from kelly.orchestrator import (
    history_summary_for_route,
    load_config_summary,
    open_default_store,
    scan_opportunities,
    scan_planned_watchlist,
    scan_row_to_jsonable,
)
from kelly.settings import (
    config_path as default_config_path,
    duffel_token,
    seats_aero_key,
    toolkit_data_dir,
)
from kelly.duffel_client import search_cash_best
from kelly.seats_aero_client import search_cached
from kelly.md_config import passengers_for_duffel
from kelly.analytics import cash_baseline, phase1_forecast_hint
from kelly.history_store import route_key
from kelly.toolkit_data import ToolkitData, worth_summary

mcp = FastMCP(
    "Kelly",
    instructions=(
        "Kelly finds cash (Duffel) and award (Seats.aero) options, compares to your "
        "Markdown watchlist, and uses local SQLite history for typical/high/low context. "
        "Heuristics are not financial advice."
    ),
)


def _cfg(p: str | None) -> Path:
    return Path(p).expanduser() if p else default_config_path()


@mcp.tool()
def kelly_load_config(config_path: str | None = None) -> str:
    """Load and validate kelly.md; return JSON summary of passengers, planned rows, opportunities."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    return json.dumps(load_config_summary(cfg), default=str)


@mcp.tool()
def kelly_scan_opportunities(config_path: str | None = None, persist: bool = True) -> str:
    """Scan ## Opportunities (capped O-D pairs and dates per pair to limit API usage)."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    store = open_default_store() if persist else None
    toolkit = ToolkitData(toolkit_data_dir())
    rows = scan_opportunities(
        cfg,
        duffel_token=duffel_token(),
        seats_key=seats_aero_key(),
        store=store,
        toolkit=toolkit,
        persist=persist,
    )
    return json.dumps([scan_row_to_jsonable(r) for r in rows], default=str)


@mcp.tool()
def kelly_scan_watchlist(config_path: str | None = None, persist: bool = True) -> str:
    """Run planned watchlist scan (Duffel + Seats.aero); optionally persist to SQLite."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    store = open_default_store() if persist else None
    toolkit = ToolkitData(toolkit_data_dir())
    rows = scan_planned_watchlist(
        cfg,
        duffel_token=duffel_token(),
        seats_key=seats_aero_key(),
        store=store,
        toolkit=toolkit,
        persist=persist,
    )
    return json.dumps([scan_row_to_jsonable(r) for r in rows], default=str)


@mcp.tool()
def kelly_search_cash(
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    cabin: str = "economy",
    config_path: str | None = None,
) -> str:
    """Ad-hoc Duffel cash search using passenger types from kelly.md (YYYY-MM-DD)."""
    token = duffel_token()
    if not token:
        return json.dumps({"error": "DUFFEL_ACCESS_TOKEN not set"})
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    pax = passengers_for_duffel(cfg)
    if not pax:
        return json.dumps({"error": "No passengers defined in config"})
    dep = date.fromisoformat(departure_date)
    res = search_cash_best(
        token,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        departure_date=dep,
        cabin=cabin,
        passengers=pax,
    )
    return json.dumps(
        {
            "best_total_amount": str(res.best_total_amount) if res.best_total_amount else None,
            "best_total_currency": res.best_total_currency,
            "best_offer_id": res.best_offer_id,
            "offer_count": res.offer_count,
            "error": res.error,
        },
        default=str,
    )


@mcp.tool()
def kelly_search_awards(
    origin_airport: str,
    destination_airport: str,
    start_date: str,
    end_date: str,
    cabin: str = "economy",
    sources: str | None = None,
) -> str:
    """Ad-hoc Seats.aero cached search (YYYY-MM-DD)."""
    key = seats_aero_key()
    if not key:
        return json.dumps({"error": "SEATS_AERO_API_KEY not set"})
    res = search_cached(
        key,
        origin_airport=origin_airport,
        destination_airport=destination_airport,
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date),
        cabin=cabin,
        sources=sources,
    )
    return json.dumps(
        {
            "best_miles": res.best_miles,
            "best_source": res.best_source,
            "best_date": res.best_date,
            "result_count": len(res.raw_data),
            "error": res.error,
        },
        default=str,
    )


@mcp.tool()
def kelly_history_summary(
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    cabin: str = "economy",
    window_days: int = 90,
) -> str:
    """Distribution stats from local SQLite for a route + departure date."""
    store = open_default_store()
    summary = history_summary_for_route(
        store,
        origin_iata,
        destination_iata,
        cabin,
        date.fromisoformat(departure_date),
        window_days=window_days,
    )
    return json.dumps(summary, default=str)


@mcp.tool()
def kelly_forecast_hint(
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    cabin: str = "economy",
    current_cash: float | None = None,
    window_days: int = 90,
) -> str:
    """Phase-1 heuristic buy/wait hint from your stored cash series for this route key."""
    from kelly.history_store import route_key

    store = open_default_store()
    rk = route_key(origin_iata, destination_iata, cabin, date.fromisoformat(departure_date))
    hist_cash, _ = store.fetch_amounts_for_route(rk, window_days=window_days)
    hint = phase1_forecast_hint(current_cash, hist_cash)
    return json.dumps({"route_key": rk, **hint.__dict__}, default=str)


@mcp.tool()
def kelly_explain_deal(
    cash_amount: float | None = None,
    cash_currency: str | None = None,
    award_miles: int | None = None,
    award_program: str | None = None,
    origin_iata: str | None = None,
    destination_iata: str | None = None,
    departure_date: str | None = None,
    cabin: str = "economy",
) -> str:
    """Short worth note: toolkit valuation hint + optional history context."""
    tk = ToolkitData(toolkit_data_dir())
    parts: list[str] = []
    w = worth_summary(tk, award_program)
    if w:
        parts.append(w)
    if (
        origin_iata
        and destination_iata
        and departure_date
        and cash_amount is not None
    ):
        store = open_default_store()
        rk = route_key(origin_iata, destination_iata, cabin, date.fromisoformat(departure_date))
        hist_cash, _ = store.fetch_amounts_for_route(rk, window_days=90)
        cb = cash_baseline(hist_cash)
        parts.append(
            f"Local history: n={cb.n} median={cb.median} p10={cb.p10} p90={cb.p90} for {rk}"
        )
        parts.append(
            f"Your quote {cash_amount} {cash_currency or ''} vs median — compare manually."
        )
    if award_miles is not None:
        parts.append(f"Award: {award_miles} miles ({award_program or 'unknown program'}).")
    if not parts:
        parts.append("No toolkit data path and no route context — set TRAVEL_HACKING_TOOLKIT_DATA or pass route + cash.")
    return json.dumps({"notes": parts}, default=str)


@mcp.resource("kelly://config", mime_type="text/markdown")
def kelly_config_resource() -> str:
    """Read-only Markdown config (default path)."""
    path = default_config_path()
    if not path.is_file():
        return f"(missing config file: {path})"
    return path.read_text(encoding="utf-8")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
