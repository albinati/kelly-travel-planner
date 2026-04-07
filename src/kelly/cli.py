"""Typer CLI for Kelly."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from kelly import __version__
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
    config_path,
    seats_aero_key,
    serpapi_api_key,
    toolkit_data_dir,
)
from kelly.toolkit_data import ToolkitData

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def _main() -> None:
    """Kelly — travel hacking from Markdown + SerpApi + Seats.aero."""


@app.command()
def version() -> None:
    """Print version."""
    typer.echo(__version__)


@app.command("scan")
def scan_cmd(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to kelly.md",
    ),
    no_persist: bool = typer.Option(False, "--no-persist", help="Do not write SQLite history"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON lines"),
) -> None:
    """Scan planned watchlist and print results."""
    path = config or config_path()
    if not path.is_file():
        typer.echo(f"Config not found: {path}", err=True)
        raise typer.Exit(code=1)

    cfg = load_kelly_config(path)
    token = serpapi_api_key()
    seats = seats_aero_key()
    if not token:
        typer.echo("Warning: SERPAPI_API_KEY not set — skipping cash search.", err=True)
    if not seats:
        typer.echo("Warning: SEATS_AERO_API_KEY not set — skipping award search.", err=True)

    store = None if no_persist else open_default_store()
    toolkit = ToolkitData(toolkit_data_dir())

    rows = scan_planned_watchlist(
        cfg,
        serpapi_key=token,
        seats_key=seats,
        store=store,
        toolkit=toolkit,
        persist=not no_persist,
    )

    if json_out:
        for r in rows:
            typer.echo(json.dumps(scan_row_to_jsonable(r), default=str))
    else:
        for r in rows:
            line = (
                f"{r.watchlist_row_id} {r.departure_date} {r.origin_iata}-{r.destination_iata} "
                f"cash={r.cash.best_total_amount if r.cash and r.cash.best_total_amount else '—'} "
                f"awards={r.awards.best_miles if r.awards else '—'} "
                f"trigger_p={r.triggered_price} trigger_m={r.triggered_miles} "
                f"vs_hist_cash={r.cash_baseline_label}"
            )
            typer.echo(line)


@app.command("opportunities")
def opportunities_cmd(
    config: Path | None = typer.Option(None, "--config", "-c"),
    no_persist: bool = typer.Option(False, "--no-persist"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Scan opportunity / wishlist rows (capped)."""
    path = config or config_path()
    if not path.is_file():
        typer.echo(f"Config not found: {path}", err=True)
        raise typer.Exit(code=1)
    cfg = load_kelly_config(path)
    if not serpapi_api_key():
        typer.echo("Warning: SERPAPI_API_KEY not set — skipping cash search.", err=True)
    store = None if no_persist else open_default_store()
    toolkit = ToolkitData(toolkit_data_dir())
    rows = scan_opportunities(
        cfg,
        serpapi_key=serpapi_api_key(),
        seats_key=seats_aero_key(),
        store=store,
        toolkit=toolkit,
        persist=not no_persist,
    )
    if json_out:
        for r in rows:
            typer.echo(json.dumps(scan_row_to_jsonable(r), default=str))
    else:
        for r in rows:
            typer.echo(
                f"{r.watchlist_row_id} {r.departure_date} {r.origin_iata}-{r.destination_iata} "
                f"cash={r.cash.best_total_amount if r.cash and r.cash.best_total_amount else '—'}"
            )


@app.command("config-show")
def config_show(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Print parsed config summary as JSON."""
    path = config or config_path()
    if not path.is_file():
        typer.echo(f"Config not found: {path}", err=True)
        raise typer.Exit(code=1)
    cfg = load_kelly_config(path)
    typer.echo(json.dumps(load_config_summary(cfg), default=str, indent=2))


@app.command("history-route")
def history_route(
    origin: str = typer.Argument(...),
    dest: str = typer.Argument(...),
    departure: str = typer.Argument(..., help="YYYY-MM-DD"),
    cabin: str = typer.Option("economy", "--cabin"),
) -> None:
    """Show baseline stats for a route key from SQLite history."""
    from datetime import date

    dep = date.fromisoformat(departure)
    store = open_default_store()
    summary = history_summary_for_route(
        store,
        origin.upper(),
        dest.upper(),
        cabin,
        dep,
    )
    typer.echo(json.dumps(summary, indent=2, default=str))


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
