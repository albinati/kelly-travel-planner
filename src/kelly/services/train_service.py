"""Rail search orchestration (Eurostar via patchright — stealth headless browser)."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Any

from kelly.history_store import (
    SqliteHistoryStore,
    TrainObservation,
    days_before_departure,
    train_key,
)
from kelly.md_config import KellyConfig, TrainRow
from kelly.providers.playwright_eurostar import EurostarSearchResult, search_eurostar


def search_train(
    cfg: KellyConfig,
    train: TrainRow,
    *,
    store: SqliteHistoryStore | None = None,
    persist: bool = True,
) -> EurostarSearchResult:
    currency = cfg.frontmatter.currency or "GBP"
    res = search_eurostar(
        origin_city=train.origin_city,
        destination_city=train.destination_city,
        date_start=train.date_start,
        date_end=train.date_end,
        adults=train.adults,
        seniors=train.seniors,
        teens=train.teens,
        children_ages=train.children_ages,
        class_=train.class_,
        currency=currency,
    )
    if store is not None and persist and res.error is None and res.journeys:
        _persist_train_observations(store, train, res, currency)
    return res


def train_result_to_jsonable(res: EurostarSearchResult) -> dict[str, Any]:
    return {
        "error": res.error,
        "note": res.note,
        "journeys": [
            {
                **{k: v for k, v in asdict(j).items() if k != "raw"},
                "total_price": str(j.total_price) if j.total_price is not None else None,
            }
            for j in res.journeys
        ],
    }


def _persist_train_observations(
    store: SqliteHistoryStore,
    train: TrainRow,
    res: EurostarSearchResult,
    currency: str,
) -> None:
    """Append one row per departure date with the cheapest per-adult fare seen.

    Eurostar searches scan a window of dates; we record one observation per
    requested date so the trip_key remains stable for trend analysis.
    """
    ages = list(train.children_ages or [])
    pax_adult_eq = train.adults + train.seniors + train.teens + sum(1 for a in ages if a >= 12)
    pax_child = sum(1 for a in ages if 4 <= a <= 11)
    pax_infant = sum(1 for a in ages if a < 4)

    by_date: dict[str, list[Decimal]] = defaultdict(list)
    journey_count: dict[str, int] = defaultdict(int)
    for j in res.journeys:
        d = (j.raw or {}).get("date")
        if not isinstance(d, str):
            continue
        journey_count[d] += 1
        if j.total_price is not None:
            by_date[d].append(j.total_price)
    if not journey_count:
        return

    for d_iso, count in journey_count.items():
        try:
            dep = date.fromisoformat(d_iso)
        except ValueError:
            continue
        prices = by_date.get(d_iso) or []
        best = min(prices) if prices else None
        store.append_train(
            TrainObservation(
                trip_key=train_key(
                    train.operator, train.origin_city, train.destination_city, train.class_, dep
                ),
                trip_row_id=train.id,
                operator=train.operator,
                origin_city=train.origin_city,
                destination_city=train.destination_city,
                class_=train.class_,
                departure_date=d_iso,
                days_before_departure=days_before_departure(dep),
                best_per_adult_amount=float(best) if best is not None else None,
                currency=currency,
                journey_count=count,
                pax_adult_eq=pax_adult_eq,
                pax_child=pax_child,
                pax_infant=pax_infant,
                raw_json=json.dumps({"note": res.note}, default=str) if res.note else None,
            )
        )
