"""Hotel search orchestration (LiteAPI — REST, key in env)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from math import ceil
from typing import Any

from kelly.history_store import HotelObservation, SqliteHistoryStore, hotel_key
from kelly.md_config import KellyConfig, StayRow
from kelly.providers.liteapi_hotels import (
    HotelSearchResult,
    occupancies_from_pax,
    search_hotels,
)


def search_hotel(
    cfg: KellyConfig,
    stay: StayRow,
    *,
    store: SqliteHistoryStore | None = None,
    persist: bool = True,
    min_stars: float | None = 3.0,
    rooms: int | None = None,
) -> HotelSearchResult:
    """Run a hotel search for *stay*.

    Room count defaults to ``⌈party_size / 4⌉`` — most hotels cap one room at
    4 occupants, and asking for one room per couple (which is what
    ``stay.bedrooms_min`` encodes for Airbnb whole-apartment searches) is
    typically wasteful for hotels. Pass an explicit *rooms* to override.
    """
    currency = cfg.frontmatter.currency or "GBP"

    ages = list(stay.children_ages or [])
    real_adults = int(stay.adults) + sum(1 for a in ages if a >= 13)
    real_children = sum(1 for a in ages if 2 <= a < 13)
    party_size = real_adults + real_children
    rooms = rooms if rooms is not None else max(1, ceil(party_size / 4))

    occupancies = occupancies_from_pax(stay.adults, ages, rooms=rooms)
    nationality = _nationality_for_currency(currency)

    res = search_hotels(
        location=stay.area,
        check_in=stay.check_in,
        check_out=stay.check_out,
        occupancies=occupancies,
        min_stars=min_stars,
        max_total=stay.max_total,
        currency=currency,
        guest_nationality=nationality,
    )

    if store is not None and persist and res.error is None:
        _persist_hotel_observation(store, stay, res, currency, rooms, min_stars)
    return res


def hotel_result_to_jsonable(res: HotelSearchResult) -> dict[str, Any]:
    return {
        "error": res.error,
        "note": res.note,
        "listings": [
            {
                **{k: v for k, v in asdict(listing).items() if k != "raw"},
                "price_total": str(listing.price_total) if listing.price_total is not None else None,
                "suggested_price": (
                    str(listing.suggested_price) if listing.suggested_price is not None else None
                ),
            }
            for listing in res.listings
        ],
    }


def _days_before(check_in: date) -> int:
    today = datetime.now(timezone.utc).date()
    return max(0, (check_in - today).days)


def _nationality_for_currency(currency: str) -> str:
    """Best-effort ISO-2 nationality for a currency, used by LiteAPI to
    surface VAT/locale-correct prices. Falls back to GB for unknown."""
    return {
        "GBP": "GB",
        "EUR": "FR",
        "USD": "US",
        "BRL": "BR",
        "CAD": "CA",
    }.get(currency.upper(), "GB")


def _persist_hotel_observation(
    store: SqliteHistoryStore,
    stay: StayRow,
    res: HotelSearchResult,
    currency: str,
    rooms: int,
    min_stars: float | None,
) -> None:
    ages = list(stay.children_ages or [])
    pax_adult_eq = stay.adults + sum(1 for a in ages if a >= 13)
    pax_child = sum(1 for a in ages if 2 <= a <= 12)
    pax_infant = sum(1 for a in ages if a < 2)

    # Persist the all-in baseline when available — that's what makes the
    # 90-day price history actually mean "what would I have paid."
    prices: list[Decimal] = [
        ln.price_all_in if ln.price_all_in is not None else ln.price_total
        for ln in res.listings
        if (ln.price_all_in if ln.price_all_in is not None else ln.price_total) is not None
    ]
    best = min(prices) if prices else None
    nights = max(0, (stay.check_out - stay.check_in).days)

    store.append_hotel(
        HotelObservation(
            trip_key=hotel_key(stay.area, stay.check_in, stay.check_out),
            trip_row_id=stay.id,
            area=stay.area,
            check_in=stay.check_in.isoformat(),
            check_out=stay.check_out.isoformat(),
            nights=nights,
            days_before_check_in=_days_before(stay.check_in),
            best_total_amount=float(best) if best is not None else None,
            currency=currency,
            listings_count=len(res.listings),
            rooms_count=rooms,
            min_stars=min_stars,
            pax_adult_eq=pax_adult_eq,
            pax_child=pax_child,
            pax_infant=pax_infant,
            raw_json=json.dumps({"note": res.note}, default=str) if res.note else None,
        )
    )
