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
    cash_backend,
    config_path as default_config_path,
    rapidapi_key,
    seats_aero_key,
    toolkit_data_dir,
)
from kelly.seats_aero_client import search_cached
from kelly.md_config import passengers_for_cash
from kelly.analytics import cash_baseline, phase1_forecast_hint
from kelly.history_store import route_key
from kelly.toolkit_data import ToolkitData, worth_summary
from kelly.services.aggregator import gated_pipeline_from_env
from kelly.services.anomaly_service import scan_price_graph_vs_history
from kelly.services.macro_service import (
    macro_cheapest_destinations,
    macro_flexible_trip_placeholder,
    macro_month_overview,
    macro_price_graph,
)
from kelly.services.mid_service import apply_travel_policy, explain_rejection, match_watchlist
from kelly.services.micro_service import default_cash_key_and_backend, fetch_flight_quote
from kelly.services.stay_service import search_stay, stay_result_to_jsonable
from kelly.services.train_service import search_train, train_result_to_jsonable
from kelly.services.trip_planner import plan_trip
from kelly.md_config import StayRow, TrainRow
from kelly.providers.booking import hotel_search
from kelly.providers.tripadvisor import nearby_context

mcp = FastMCP(
    "Kelly",
    instructions=(
        "Kelly aggregates RapidAPI (Google Flights–style cash) and Seats.aero awards, with optional "
        "SerpApi cash when KELLY_CASH_BACKEND=serpapi. Macro tools use RAPIDAPI_KEY; load secrets from "
        "repo .env (project root). Heuristics are not financial advice."
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
    """Scan ## Opportunities (capped O-D pairs and dates per pair; cash via RapidAPI or SerpApi)."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    store = open_default_store() if persist else None
    toolkit = ToolkitData(toolkit_data_dir())
    rows = scan_opportunities(
        cfg,
        seats_key=seats_aero_key(),
        store=store,
        toolkit=toolkit,
        persist=persist,
    )
    return json.dumps([scan_row_to_jsonable(r) for r in rows], default=str)


@mcp.tool()
def kelly_scan_watchlist(config_path: str | None = None, persist: bool = True) -> str:
    """Run planned watchlist scan (cash + Seats.aero); optionally persist to SQLite."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    store = open_default_store() if persist else None
    toolkit = ToolkitData(toolkit_data_dir())
    rows = scan_planned_watchlist(
        cfg,
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
    """Ad-hoc cash search (RapidAPI or SerpApi per KELLY_CASH_BACKEND); passengers from kelly.md."""
    key, backend = default_cash_key_and_backend()
    if not key:
        return json.dumps({"error": "Set RAPIDAPI_KEY or SERPAPI_API_KEY in .env"})
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    pax = passengers_for_cash(cfg)
    if not pax:
        return json.dumps({"error": "No passengers defined in config"})
    dep = date.fromisoformat(departure_date)
    res = fetch_flight_quote(
        cash_key=key,
        backend=backend,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        departure_date=dep,
        cabin=cabin,
        passengers=pax,
        currency=cfg.frontmatter.currency,
    )
    return json.dumps(
        {
            "cash_backend": backend,
            "best_total_amount": str(res.best_total_amount) if res.best_total_amount else None,
            "best_total_currency": res.best_total_currency,
            "best_offer_id": res.best_offer_id,
            "offer_count": res.offer_count,
            "error": res.error,
            "itinerary_details": res.itinerary_details,
        },
        default=str,
    )


@mcp.tool()
def kelly_micro_flight_quote(
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    cabin: str = "economy",
    return_date: str | None = None,
    config_path: str | None = None,
) -> str:
    """Heavy dated flight quote (same backends as kelly_search_cash); optional return YYYY-MM-DD."""
    key, backend = default_cash_key_and_backend()
    if not key:
        return json.dumps({"error": "Set RAPIDAPI_KEY or SERPAPI_API_KEY in .env"})
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    pax = passengers_for_cash(cfg)
    if not pax:
        return json.dumps({"error": "No passengers defined in config"})
    ret = date.fromisoformat(return_date) if return_date else None
    res = fetch_flight_quote(
        cash_key=key,
        backend=backend,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        departure_date=date.fromisoformat(departure_date),
        cabin=cabin,
        passengers=pax,
        currency=cfg.frontmatter.currency,
        return_date=ret,
    )
    return json.dumps(
        {
            "cash_backend": backend,
            "best_total_amount": str(res.best_total_amount) if res.best_total_amount else None,
            "best_total_currency": res.best_total_currency,
            "best_offer_id": res.best_offer_id,
            "offer_count": res.offer_count,
            "error": res.error,
            "itinerary_details": res.itinerary_details,
        },
        default=str,
    )


@mcp.tool()
def kelly_macro_price_graph(
    origin_iata: str,
    destination_iata: str,
    window_start: str,
    window_end: str,
    cabin: str = "economy",
    config_path: str | None = None,
) -> str:
    """One calendar request per month in window — normalized date→price array (needs RAPIDAPI_KEY)."""
    token = rapidapi_key()
    if not token:
        return json.dumps({"error": "RAPIDAPI_KEY not set (macro uses RapidAPI only)"})
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    g = macro_price_graph(
        token,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        window_start=date.fromisoformat(window_start),
        window_end=date.fromisoformat(window_end),
        cabin=cabin,
        currency=cfg.frontmatter.currency,
    )
    return json.dumps(g, default=str)


@mcp.tool()
def kelly_macro_month_overview(
    origin_iata: str,
    destination_iata: str,
    year_month: str,
    cabin: str = "economy",
    config_path: str | None = None,
) -> str:
    """Cheapest day + simple stats for YYYY-MM (RapidAPI price graph)."""
    token = rapidapi_key()
    if not token:
        return json.dumps({"error": "RAPIDAPI_KEY not set"})
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    return json.dumps(
        macro_month_overview(
            token,
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            year_month=year_month,
            cabin=cabin,
            currency=cfg.frontmatter.currency,
        ),
        default=str,
    )


@mcp.tool()
def kelly_macro_cheapest_destinations(
    origin_iata: str,
    month: str | None = None,
    max_results: int = 10,
) -> str:
    """Kiwi-style destination discovery (stub until RAPIDAPI_KIWI_HOST path is implemented)."""
    token = rapidapi_key()
    if not token:
        return json.dumps({"error": "RAPIDAPI_KEY not set"})
    return json.dumps(
        macro_cheapest_destinations(token, origin_iata=origin_iata, month=month, max_results=max_results),
        default=str,
    )


@mcp.tool()
def kelly_macro_flexible_trip(
    origin_iata: str,
    destinations_csv: str,
    year_month: str,
) -> str:
    """Hint for flexible destination ranking (use price graph per destination for real data)."""
    return json.dumps(
        macro_flexible_trip_placeholder(
            origin_iata=origin_iata,
            destinations_csv=destinations_csv,
            year_month=year_month,
        ),
        default=str,
    )


@mcp.tool()
def kelly_mid_apply_policy(candidates_json: str, config_path: str | None = None) -> str:
    """Filter JSON list of candidates with itinerary_details using kelly.md travel_policy."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    pol = cfg.frontmatter.travel_policy
    try:
        candidates = json.loads(candidates_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"invalid JSON: {e}"})
    if not isinstance(candidates, list):
        return json.dumps({"error": "candidates_json must be a JSON array"})
    kept, rejected = apply_travel_policy(candidates, pol)
    return json.dumps({"kept": kept, "rejected": rejected, "policy": pol.model_dump() if pol else None}, default=str)


@mcp.tool()
def kelly_mid_match_watchlist(candidates_json: str, config_path: str | None = None) -> str:
    """Match macro/mid candidates to ## Planned watchlist rows (date window + target price)."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    try:
        candidates = json.loads(candidates_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"invalid JSON: {e}"})
    if not isinstance(candidates, list):
        return json.dumps({"error": "candidates_json must be a JSON array"})
    return json.dumps(match_watchlist(cfg, candidates), default=str)


@mcp.tool()
def kelly_mid_explain_rejection(candidate_json: str, config_path: str | None = None) -> str:
    """Structured reasons a candidate fails travel_policy."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    pol = cfg.frontmatter.travel_policy
    try:
        cand = json.loads(candidate_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"invalid JSON: {e}"})
    if not isinstance(cand, dict):
        return json.dumps({"error": "candidate_json must be a JSON object"})
    return json.dumps(explain_rejection(cand, pol), default=str)


@mcp.tool()
def kelly_mid_anomaly_scan(
    graph_days_json: str,
    origin_iata: str,
    destination_iata: str,
    cabin: str = "economy",
    target_price: float | None = None,
    reference_departure_date: str | None = None,
    window_days: int = 90,
) -> str:
    """Label each calendar day vs SQLite baseline (no heavy flight API). graph_days_json = kelly_macro_price_graph days."""
    try:
        days = json.loads(graph_days_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"invalid JSON: {e}"})
    if not isinstance(days, list):
        return json.dumps({"error": "expected JSON array"})
    store = open_default_store()
    ref = date.fromisoformat(reference_departure_date) if reference_departure_date else None
    if ref is None and days:
        try:
            ref = date.fromisoformat(str(days[0].get("date", ""))[:10])
        except ValueError:
            ref = date.today()
    if ref is None:
        ref = date.today()
    rk = route_key(origin_iata, destination_iata, cabin, ref)
    hist_cash, _ = store.fetch_amounts_for_route(rk, window_days=window_days)
    rows = scan_price_graph_vs_history(
        days,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        cabin=cabin,
        hist_amounts=hist_cash,
        target_price=target_price,
    )
    return json.dumps(
        [
            {
                "date": r.date.isoformat(),
                "indicative_price": r.indicative_price,
                "label": r.label,
                "reason": r.reason,
            }
            for r in rows
        ],
        default=str,
    )


@mcp.tool()
def kelly_pipeline_graph_to_details(
    origin_iata: str,
    destination_iata: str,
    window_start: str,
    window_end: str,
    cabin: str = "economy",
    target_price: float | None = None,
    config_path: str | None = None,
) -> str:
    """Price graph → anomaly vs SQLite + policy → heavy micro only for opportunity dates (capped)."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    pax = passengers_for_cash(cfg)
    if not pax:
        return json.dumps({"error": "No passengers defined in config"})
    store = open_default_store()
    out = gated_pipeline_from_env(
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        window_start=date.fromisoformat(window_start),
        window_end=date.fromisoformat(window_end),
        cabin=cabin,
        currency=cfg.frontmatter.currency,
        passengers=pax,
        store=store,
        policy=cfg.frontmatter.travel_policy,
        target_price=target_price,
    )
    out["cash_backend_effective"] = cash_backend()
    return json.dumps(out, default=str)


@mcp.tool()
def kelly_micro_hotel_search(
    city: str,
    check_in: str,
    check_out: str,
    adults: int = 2,
) -> str:
    """Hotel listings stub (set RAPIDAPI_BOOKING_HOST and implement provider)."""
    token = rapidapi_key()
    if not token:
        return json.dumps({"error": "RAPIDAPI_KEY not set"})
    return json.dumps(
        hotel_search(
            token,
            city=city,
            check_in=date.fromisoformat(check_in),
            check_out=date.fromisoformat(check_out),
            adults=adults,
        ),
        default=str,
    )


@mcp.tool()
def kelly_micro_tripadvisor_context(destination_name: str, limit: int = 5) -> str:
    """Trip context stub (set RAPIDAPI_TRIPADVISOR_HOST)."""
    token = rapidapi_key()
    if not token:
        return json.dumps({"error": "RAPIDAPI_KEY not set"})
    return json.dumps(nearby_context(token, destination_name=destination_name, limit=limit), default=str)


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


@mcp.tool()
def kelly_micro_eurostar_search(
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
def kelly_micro_airbnb_search(
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
    plus the stay with id `<trip_id>`; returns combined JSON with search results.
    """
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    store = open_default_store() if persist else None
    return json.dumps(plan_trip(cfg, trip_id, store=store, persist=persist), default=str)


@mcp.resource("kelly://config", mime_type="text/markdown")
def kelly_config_resource() -> str:
    """Read-only Markdown config (default path)."""
    path = default_config_path()
    if not path.is_file():
        return f"(missing config file: {path})"
    return path.read_text(encoding="utf-8")


@mcp.resource("kelly://policy", mime_type="application/json")
def kelly_policy_resource() -> str:
    """Travel policy from kelly.md front matter (JSON)."""
    path = default_config_path()
    if not path.is_file():
        return "{}"
    cfg = load_kelly_config(path)
    pol = cfg.frontmatter.travel_policy
    return json.dumps(pol.model_dump() if pol else {}, default=str)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
