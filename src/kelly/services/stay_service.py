"""Accommodation search orchestration (Airbnb via pyairbnb — free, no key)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from kelly.history_store import SqliteHistoryStore, StayObservation, stay_key
from kelly.md_config import KellyConfig, StayRow
from kelly.providers.airbnb import AirbnbSearchResult, search_airbnb


def search_stay(
    cfg: KellyConfig,
    stay: StayRow,
    *,
    store: SqliteHistoryStore | None = None,
    persist: bool = True,
) -> AirbnbSearchResult:
    currency = cfg.frontmatter.currency or "EUR"
    res = search_airbnb(
        location=stay.area,
        check_in=stay.check_in,
        check_out=stay.check_out,
        adults=stay.adults,
        children_ages=stay.children_ages,
        bedrooms_min=stay.bedrooms_min,
        max_total=stay.max_total,
        currency=currency,
    )
    if store is not None and persist and res.error is None:
        _persist_stay_observation(store, stay, res, currency)
    return res


def stay_result_to_jsonable(res: AirbnbSearchResult) -> dict[str, Any]:
    return {
        "error": res.error,
        "note": res.note,
        "listings": [
            {
                **{k: v for k, v in asdict(listing).items() if k != "raw"},
                "price_total": str(listing.price_total) if listing.price_total is not None else None,
            }
            for listing in res.listings
        ],
    }


def _days_before(check_in: date) -> int:
    today = datetime.now(timezone.utc).date()
    return max(0, (check_in - today).days)


def _persist_stay_observation(
    store: SqliteHistoryStore,
    stay: StayRow,
    res: AirbnbSearchResult,
    currency: str,
) -> None:
    """Append one row per stay search — the cheapest total across returned listings."""
    ages = list(stay.children_ages or [])
    pax_adult_eq = stay.adults + sum(1 for a in ages if a >= 13)
    pax_child = sum(1 for a in ages if 2 <= a <= 12)
    pax_infant = sum(1 for a in ages if a < 2)

    prices: list[Decimal] = [
        ln.price_total for ln in res.listings if ln.price_total is not None
    ]
    best = min(prices) if prices else None
    nights = max(0, (stay.check_out - stay.check_in).days)

    store.append_stay(
        StayObservation(
            trip_key=stay_key(stay.area, stay.check_in, stay.check_out),
            trip_row_id=stay.id,
            area=stay.area,
            check_in=stay.check_in.isoformat(),
            check_out=stay.check_out.isoformat(),
            nights=nights,
            days_before_check_in=_days_before(stay.check_in),
            best_total_amount=float(best) if best is not None else None,
            currency=currency,
            listings_count=len(res.listings),
            bedrooms_min=stay.bedrooms_min,
            pax_adult_eq=pax_adult_eq,
            pax_child=pax_child,
            pax_infant=pax_infant,
            raw_json=json.dumps({"note": res.note}, default=str) if res.note else None,
        )
    )
