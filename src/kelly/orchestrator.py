"""Wire config, APIs, history, and analytics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from kelly.analytics import (
    ForecastHint,
    cash_baseline,
    label_cash_vs_history,
    label_miles_vs_history,
    phase1_forecast_hint,
)
from kelly.cash_types import CashSearchResult
from kelly import settings as kelly_settings
from kelly.services.micro_service import fetch_flight_quote
from kelly.history_store import (
    HistoryStore,
    Observation,
    SqliteHistoryStore,
    days_before_departure,
    route_key,
)
from kelly.md_config import KellyConfig, passengers_for_cash
from kelly.seats_aero_client import AwardSearchResult, search_cached
from kelly.toolkit_data import ToolkitData, worth_summary


def iter_departure_dates(start: date, end: date, max_n: int) -> list[date]:
    total = (end - start).days + 1
    if total <= max_n:
        return [start + timedelta(days=i) for i in range(total)]
    step = max(1, total // max_n)
    out: list[date] = []
    d = start
    while d <= end and len(out) < max_n:
        out.append(d)
        d += timedelta(days=step)
    if end not in out and len(out) < max_n:
        out.append(end)
    return sorted(set(out))[:max_n]


@dataclass
class ScanRowResult:
    watchlist_row_id: str
    departure_date: date
    origin_iata: str
    destination_iata: str
    cabin: str
    cash: CashSearchResult | None
    awards: AwardSearchResult | None
    triggered_price: bool
    triggered_miles: bool
    cash_baseline_label: str
    miles_baseline_label: str
    forecast: ForecastHint | None
    worth_note: str | None
    persisted: bool


def _float_or_none(d: Decimal | None) -> float | None:
    if d is None:
        return None
    return float(d)


def _cash_key_and_backend() -> tuple[str | None, str]:
    b = kelly_settings.cash_backend()
    if b == "rapidapi":
        return kelly_settings.rapidapi_key(), "rapidapi"
    return kelly_settings.serpapi_api_key(), "serpapi"


def scan_planned_watchlist(
    cfg: KellyConfig,
    *,
    seats_key: str | None,
    store: HistoryStore | None,
    toolkit: ToolkitData | None,
    persist: bool = True,
) -> list[ScanRowResult]:
    """Run cash search (RapidAPI or SerpApi) + Seats.aero for each planned row and departure sample."""
    cash_key, cash_src = _cash_key_and_backend()
    window = cfg.frontmatter.history_window_days
    max_dates = cfg.frontmatter.max_dates_per_watch_row
    results: list[ScanRowResult] = []
    tk = toolkit or ToolkitData(None)

    for row in cfg.planned:
        pax = passengers_for_cash(cfg, passenger_ids_override=row.passenger_ids)
        for dep in iter_departure_dates(row.date_start, row.date_end, max_dates):
            rk = route_key(row.origin_iata, row.destination_iata, row.cabin, dep)
            d_before = days_before_departure(dep)

            cash_res: CashSearchResult | None = None
            if cash_key and pax:
                cash_res = fetch_flight_quote(
                    cash_key=cash_key,
                    backend=cash_src,
                    origin_iata=row.origin_iata,
                    destination_iata=row.destination_iata,
                    departure_date=dep,
                    cabin=row.cabin,
                    passengers=pax,
                    currency=cfg.frontmatter.currency,
                )

            award_res: AwardSearchResult | None = None
            if seats_key:
                award_res = search_cached(
                    seats_key,
                    origin_airport=row.origin_iata,
                    destination_airport=row.destination_iata,
                    start_date=dep,
                    end_date=dep,
                    cabin=row.cabin,
                    sources=row.seats_aero_sources,
                )

            cash_amount = _float_or_none(cash_res.best_total_amount) if cash_res else None
            hist_cash, hist_miles = (
                store.fetch_amounts_for_route(rk, window_days=window) if store else ([], [])
            )

            cb = cash_baseline(hist_cash)
            cash_label = label_cash_vs_history(cash_amount, cb)
            miles_label = label_miles_vs_history(
                award_res.best_miles if award_res else None,
                hist_miles,
            )

            trig_price = bool(
                row.target_price is not None
                and cash_amount is not None
                and cash_amount <= row.target_price
            )
            trig_miles = bool(
                row.target_miles is not None
                and award_res
                and award_res.best_miles is not None
                and award_res.best_miles <= row.target_miles
            )

            hist_for_forecast = hist_cash + ([cash_amount] if cash_amount is not None else [])
            forecast = phase1_forecast_hint(
                cash_amount,
                hist_for_forecast[:-1] if cash_amount is not None else hist_for_forecast,
            )

            wnote = worth_summary(tk, award_res.best_source if award_res else None)

            persisted = False
            if persist and store and (cash_amount is not None or (award_res and award_res.best_miles)):
                obs = Observation(
                    route_key=rk,
                    origin_iata=row.origin_iata,
                    destination_iata=row.destination_iata,
                    cabin=row.cabin,
                    departure_date=dep.isoformat(),
                    days_before_departure=d_before,
                    best_cash_amount=cash_amount,
                    cash_currency=cash_res.best_total_currency if cash_res else None,
                    best_award_miles=award_res.best_miles if award_res else None,
                    award_program=award_res.best_source if award_res else None,
                    award_taxes_cents=None,
                    source_cash=(cash_src if cash_res and not cash_res.error else None),
                    source_award="seats_aero" if award_res and not award_res.error else None,
                    watchlist_row_id=row.id,
                    raw_json=None,
                )
                store.append(obs)
                persisted = True

            results.append(
                ScanRowResult(
                    watchlist_row_id=row.id,
                    departure_date=dep,
                    origin_iata=row.origin_iata,
                    destination_iata=row.destination_iata,
                    cabin=row.cabin,
                    cash=cash_res,
                    awards=award_res,
                    triggered_price=trig_price,
                    triggered_miles=trig_miles,
                    cash_baseline_label=cash_label,
                    miles_baseline_label=miles_label,
                    forecast=forecast,
                    worth_note=wnote,
                    persisted=persisted,
                )
            )

    return results


def scan_row_to_jsonable(r: ScanRowResult) -> dict[str, Any]:
    out: dict[str, Any] = {
        "watchlist_row_id": r.watchlist_row_id,
        "departure_date": r.departure_date.isoformat(),
        "origin_iata": r.origin_iata,
        "destination_iata": r.destination_iata,
        "cabin": r.cabin,
        "triggered_price": r.triggered_price,
        "triggered_miles": r.triggered_miles,
        "cash_baseline_label": r.cash_baseline_label,
        "miles_baseline_label": r.miles_baseline_label,
        "persisted": r.persisted,
        "worth_note": r.worth_note,
    }
    if r.cash:
        out["cash"] = {
            "best_total_amount": str(r.cash.best_total_amount) if r.cash.best_total_amount else None,
            "best_total_currency": r.cash.best_total_currency,
            "best_offer_id": r.cash.best_offer_id,
            "offer_count": r.cash.offer_count,
            "error": r.cash.error,
            "itinerary_details": r.cash.itinerary_details,
        }
    if r.awards:
        out["awards"] = {
            "best_miles": r.awards.best_miles,
            "best_source": r.awards.best_source,
            "best_date": r.awards.best_date,
            "error": r.awards.error,
        }
    if r.forecast:
        out["forecast"] = asdict(r.forecast)
    return out


def load_config_summary(cfg: KellyConfig) -> dict[str, Any]:
    return {
        "passengers": [p.model_dump() for p in cfg.passengers],
        "planned_rows": [p.model_dump(mode="json") for p in cfg.planned],
        "opportunity_rows": [p.model_dump(mode="json") for p in cfg.opportunities],
        "trains": [t.model_dump(mode="json", by_alias=True) for t in cfg.trains],
        "stays": [s.model_dump(mode="json") for s in cfg.stays],
        "frontmatter": cfg.frontmatter.model_dump(mode="json"),
        "cash_backend": kelly_settings.cash_backend(),
    }


def history_summary_for_route(
    store: HistoryStore,
    origin_iata: str,
    dest_iata: str,
    cabin: str,
    departure_date: date,
    *,
    window_days: int = 90,
) -> dict[str, Any]:
    rk = route_key(origin_iata, dest_iata, cabin, departure_date)
    cash, miles = store.fetch_amounts_for_route(rk, window_days=window_days)
    cb = cash_baseline(cash)
    mb = cash_baseline([float(m) for m in miles])  # reuse numeric stats
    return {
        "route_key": rk,
        "window_days": window_days,
        "cash_samples": len(cash),
        "cash_median": cb.median,
        "cash_p10": cb.p10,
        "cash_p90": cb.p90,
        "miles_samples": len(miles),
        "miles_median": mb.median,
        "miles_p10": mb.p10,
        "miles_p90": mb.p90,
    }


def open_default_store() -> SqliteHistoryStore:
    from kelly.settings import history_db_path

    return SqliteHistoryStore(history_db_path())


def scan_opportunities(
    cfg: KellyConfig,
    *,
    seats_key: str | None,
    store: HistoryStore | None,
    toolkit: ToolkitData | None,
    persist: bool = True,
    max_origin_dest_pairs: int = 4,
    max_dates_per_pair: int = 3,
) -> list[ScanRowResult]:
    """Broad wishlist scans: Cartesian sample of airports (capped) × date samples (capped)."""
    cash_key, cash_src = _cash_key_and_backend()
    window = cfg.frontmatter.history_window_days
    results: list[ScanRowResult] = []
    tk = toolkit or ToolkitData(None)
    pair_budget = max_origin_dest_pairs

    for opp in cfg.opportunities:
        pax = passengers_for_cash(cfg, passenger_ids_override=opp.passenger_ids)
        origins = [x.strip().upper() for x in opp.origin_airports.split(",") if x.strip()]
        dests = [x.strip().upper() for x in opp.destination_airports.split(",") if x.strip()]
        for o in origins:
            for d in dests:
                if pair_budget <= 0:
                    return results
                pair_budget -= 1
                for dep in iter_departure_dates(
                    opp.start_date,
                    opp.end_date,
                    max_dates_per_pair,
                ):
                    rk = route_key(o, d, opp.cabin, dep)
                    d_before = days_before_departure(dep)

                    cash_res = None
                    if cash_key and pax:
                        cash_res = fetch_flight_quote(
                            cash_key=cash_key,
                            backend=cash_src,
                            origin_iata=o,
                            destination_iata=d,
                            departure_date=dep,
                            cabin=opp.cabin,
                            passengers=pax,
                            currency=cfg.frontmatter.currency,
                        )

                    award_res = None
                    if seats_key:
                        award_res = search_cached(
                            seats_key,
                            origin_airport=o,
                            destination_airport=d,
                            start_date=dep,
                            end_date=dep,
                            cabin=opp.cabin,
                            sources=opp.seats_aero_sources,
                        )

                    cash_amount = _float_or_none(cash_res.best_total_amount) if cash_res else None
                    hist_cash, hist_miles = (
                        store.fetch_amounts_for_route(rk, window_days=window) if store else ([], [])
                    )
                    cb = cash_baseline(hist_cash)
                    cash_label = label_cash_vs_history(cash_amount, cb)
                    miles_label = label_miles_vs_history(
                        award_res.best_miles if award_res else None,
                        hist_miles,
                    )

                    trig_price = bool(
                        opp.max_cash is not None
                        and cash_amount is not None
                        and cash_amount <= opp.max_cash
                    )
                    trig_miles = bool(
                        opp.max_miles is not None
                        and award_res
                        and award_res.best_miles is not None
                        and award_res.best_miles <= opp.max_miles
                    )

                    hist_for_forecast = hist_cash + ([cash_amount] if cash_amount is not None else [])
                    forecast = phase1_forecast_hint(
                        cash_amount,
                        hist_for_forecast[:-1] if cash_amount is not None else hist_for_forecast,
                    )
                    wnote = worth_summary(tk, award_res.best_source if award_res else None)

                    persisted = False
                    if persist and store and (cash_amount is not None or (award_res and award_res.best_miles)):
                        store.append(
                            Observation(
                                route_key=rk,
                                origin_iata=o,
                                destination_iata=d,
                                cabin=opp.cabin,
                                departure_date=dep.isoformat(),
                                days_before_departure=d_before,
                                best_cash_amount=cash_amount,
                                cash_currency=cash_res.best_total_currency if cash_res else None,
                                best_award_miles=award_res.best_miles if award_res else None,
                                award_program=award_res.best_source if award_res else None,
                                award_taxes_cents=None,
                                source_cash=(cash_src if cash_res and not cash_res.error else None),
                                source_award="seats_aero" if award_res and not award_res.error else None,
                                watchlist_row_id=f"opp:{opp.id}",
                                raw_json=None,
                            )
                        )
                        persisted = True

                    results.append(
                        ScanRowResult(
                            watchlist_row_id=f"opp:{opp.id}",
                            departure_date=dep,
                            origin_iata=o,
                            destination_iata=d,
                            cabin=opp.cabin,
                            cash=cash_res,
                            awards=award_res,
                            triggered_price=trig_price,
                            triggered_miles=trig_miles,
                            cash_baseline_label=cash_label,
                            miles_baseline_label=miles_label,
                            forecast=forecast,
                            worth_note=wnote,
                            persisted=persisted,
                        )
                    )

    return results
