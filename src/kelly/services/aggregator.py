"""Gated pipeline: price graph → anomaly → optional heavy micro quotes."""

from __future__ import annotations

from datetime import date
from typing import Any

from kelly import settings
from kelly.history_store import HistoryStore, route_key
from kelly.md_config import TravelPolicy
from kelly.services.anomaly_service import scan_price_graph_vs_history
from kelly.services.macro_service import macro_price_graph
from kelly.services.mid_service import apply_travel_policy, explain_rejection
from kelly.services.micro_service import default_cash_key_and_backend, fetch_flight_quote


def run_gated_pipeline(
    *,
    graph_api_key: str,
    micro_cash_key: str,
    micro_backend: str,
    origin_iata: str,
    destination_iata: str,
    window_start: date,
    window_end: date,
    cabin: str,
    currency: str,
    passengers: list[dict[str, str]],
    store: HistoryStore | None,
    policy: TravelPolicy | None = None,
    target_price: float | None = None,
    window_days: int = 90,
    max_heavy: int | None = None,
) -> dict[str, Any]:
    """
    1) Macro price graph (one HTTP per month in window).
    2) Anomaly labels vs SQLite baseline for a representative route_key (first graph day or window_start).
    3) Heavy search only for ``opportunity`` dates, capped.
    """
    cap = max_heavy if max_heavy is not None else settings.pipeline_max_heavy_calls()
    graph = macro_price_graph(
        graph_api_key,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        window_start=window_start,
        window_end=window_end,
        cabin=cabin,
        currency=currency,
    )
    if graph.get("error") and not graph.get("days"):
        return {
            "graph": graph,
            "anomalies": [],
            "heavy_calls_used": 0,
            "micro_results": [],
            "error": graph.get("error"),
        }

    hist_amounts: list[float] = []
    if store and graph.get("days"):
        sample_date = date.fromisoformat(graph["days"][0]["date"])
        rk = route_key(origin_iata, destination_iata, cabin, sample_date)
        hist_amounts, _ = store.fetch_amounts_for_route(rk, window_days=window_days)

    anoms = scan_price_graph_vs_history(
        graph.get("days") or [],
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        cabin=cabin,
        hist_amounts=hist_amounts,
        target_price=target_price,
    )
    flagged_dates = [a.date for a in anoms if a.label == "opportunity"]

    micro_results: list[dict[str, Any]] = []
    heavy = 0
    for d in flagged_dates[:cap]:
        q = fetch_flight_quote(
            cash_key=micro_cash_key,
            cash_backend=micro_backend,
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            departure_date=d,
            cabin=cabin,
            passengers=passengers,
            currency=currency,
        )
        heavy += 1
        cand = {
            "departure_date": d.isoformat(),
            "origin_iata": origin_iata,
            "destination_iata": destination_iata,
            "best_total_amount": str(q.best_total_amount) if q.best_total_amount else None,
            "best_total_currency": q.best_total_currency,
            "best_offer_id": q.best_offer_id,
            "offer_count": q.offer_count,
            "error": q.error,
            "itinerary_details": q.itinerary_details,
        }
        kept, _rej = apply_travel_policy([cand], policy)
        expl = explain_rejection(cand, policy)
        micro_results.append(
            {
                "quote": cand,
                "policy_allowed": len(kept) > 0,
                "policy_explain": expl,
            }
        )

    return {
        "graph": graph,
        "anomalies": [
            {
                "date": x.date.isoformat(),
                "indicative_price": x.indicative_price,
                "label": x.label,
                "reason": x.reason,
            }
            for x in anoms
        ],
        "anomaly_meta": {
            "hist_samples": len(hist_amounts),
            "flagged_opportunity_dates": [x.isoformat() for x in flagged_dates],
        },
        "heavy_calls_used": heavy,
        "micro_results": micro_results,
        "micro_cash_backend": micro_backend,
    }


def gated_pipeline_from_env(
    *,
    origin_iata: str,
    destination_iata: str,
    window_start: date,
    window_end: date,
    cabin: str,
    currency: str,
    passengers: list[dict[str, str]],
    store: HistoryStore | None,
    policy: TravelPolicy | None = None,
    target_price: float | None = None,
) -> dict[str, Any]:
    graph_key = settings.rapidapi_key()
    micro_key, micro_backend = default_cash_key_and_backend()
    if not graph_key:
        return {
            "error": "RAPIDAPI_KEY required for price-graph step (macro layer)",
            "graph": {},
            "anomalies": [],
            "heavy_calls_used": 0,
            "micro_results": [],
        }
    if not micro_key:
        return {
            "error": "No micro cash key: set RAPIDAPI_KEY and/or SERPAPI_API_KEY for heavy quotes",
            "graph": {},
            "anomalies": [],
            "heavy_calls_used": 0,
            "micro_results": [],
        }
    return run_gated_pipeline(
        graph_api_key=graph_key,
        micro_cash_key=micro_key,
        micro_backend=micro_backend,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        window_start=window_start,
        window_end=window_end,
        cabin=cabin,
        currency=currency,
        passengers=passengers,
        store=store,
        policy=policy,
        target_price=target_price,
    )
