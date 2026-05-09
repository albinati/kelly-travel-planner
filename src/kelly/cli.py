"""Typer CLI for Kelly — group trip planner (Eurostar + Airbnb)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from kelly import __version__
from kelly.history_store import open_default_store
from kelly.md_config import load_kelly_config
from kelly.services.trip_planner import plan_trip
from kelly.settings import config_path

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def _main() -> None:
    """Kelly — group trip planner: Eurostar trains + Airbnb stays from a Markdown config."""


@app.command()
def version() -> None:
    """Print version."""
    typer.echo(__version__)


@app.command("config-show")
def config_show(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Print parsed config (trains + stays) as JSON."""
    path = config or config_path()
    if not path.is_file():
        typer.echo(f"Config not found: {path}", err=True)
        raise typer.Exit(code=1)
    cfg = load_kelly_config(path)
    summary = {
        "frontmatter": cfg.frontmatter.model_dump(mode="json"),
        "trains": [t.model_dump(mode="json", by_alias=True) for t in cfg.trains],
        "stays": [s.model_dump(mode="json") for s in cfg.stays],
    }
    typer.echo(json.dumps(summary, default=str, indent=2))


@app.command("plan")
def plan_cmd(
    trip_id: str = typer.Argument(..., help="Trip id declared in kelly.md (Trains/Stays sections)"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    no_persist: bool = typer.Option(False, "--no-persist", help="Do not write SQLite history"),
) -> None:
    """Plan a trip from kelly.md: trains (out/back) + stay. Prints JSON."""
    path = config or config_path()
    if not path.is_file():
        typer.echo(f"Config not found: {path}", err=True)
        raise typer.Exit(code=1)
    cfg = load_kelly_config(path)
    store = None if no_persist else open_default_store()
    result = plan_trip(cfg, trip_id, store=store, persist=not no_persist)
    typer.echo(json.dumps(result, default=str, indent=2))


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
