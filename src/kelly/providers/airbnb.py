"""Airbnb search via pyairbnb — hits Airbnb's internal staysSearch GraphQL.

Free, no API key. Geocodes the kelly.md ``area`` string to a bbox via OSM
Nominatim, then queries one page (~18 listings) through pyairbnb's lower-level
``search.get`` (avoiding the upstream ``search_first_page`` bug that mis-shapes
the response).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

# pyairbnb is gated behind the `trips` extra. Import lazily inside search_airbnb
# so this module loads (for the dataclasses below) even when the extra is absent.


@dataclass
class AirbnbListing:
    id: str
    url: str | None
    title: str | None
    price_total: Decimal | None
    price_currency: str | None
    bedrooms: int | None
    beds: int | None
    max_guests: int | None
    rating: float | None
    neighborhood: str | None
    lat: float | None
    lng: float | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AirbnbSearchResult:
    listings: list[AirbnbListing]
    error: str | None = None
    note: str | None = None


def _geocode_bbox(area: str) -> tuple[float, float, float, float] | None:
    """Geocode *area* via OSM Nominatim → (sw_lat, sw_long, ne_lat, ne_long).

    Nominatim is free and unauthenticated but enforces a User-Agent and a rough
    1 req/sec cap; that's fine for ad-hoc planner runs.
    """
    headers = {"User-Agent": "kelly-travel-planner/0.1 (personal-use)"}
    try:
        with httpx.Client(timeout=15.0, headers=headers) as client:
            r = client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": area, "format": "json", "limit": 1},
            )
        r.raise_for_status()
        items = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not items:
        return None
    bb = items[0].get("boundingbox")
    if not bb or len(bb) != 4:
        return None
    try:
        south, north, west, east = (float(x) for x in bb)
    except (TypeError, ValueError):
        return None
    return (south, west, north, east)


def _to_decimal(v: object) -> Decimal | None:
    if v is None or v == "" or v == 0:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_float(v: object) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None


def _parse_listing(item: dict[str, Any]) -> AirbnbListing:
    """Map a pyairbnb-standardized search result dict to AirbnbListing."""
    room_id = str(item.get("room_id") or "")
    price_block = item.get("price") or {}
    total_block = price_block.get("total") or {}
    unit_block = price_block.get("unit") or {}
    total_amount = total_block.get("amount") or unit_block.get("amount")
    currency = total_block.get("currency_symbol") or unit_block.get("curency_symbol")
    coords = item.get("coordinates") or {}
    rating_block = item.get("rating") or {}
    return AirbnbListing(
        id=room_id,
        url=f"https://www.airbnb.com/rooms/{room_id}" if room_id else None,
        title=item.get("name") or item.get("title") or None,
        price_total=_to_decimal(total_amount),
        price_currency=str(currency) if currency else None,
        bedrooms=None,
        beds=None,
        max_guests=None,
        rating=_to_float(rating_block.get("value")),
        neighborhood=None,
        lat=_to_float(coords.get("latitude")),
        lng=_to_float(coords.get("longitud") or coords.get("longitude")),
        raw=item,
    )


def search_airbnb(
    *,
    location: str,
    check_in: date,
    check_out: date,
    adults: int,
    children_ages: list[int],
    bedrooms_min: int = 1,
    max_total: float | None = None,
    currency: str = "EUR",
    language: str = "en",
    zoom_value: int = 12,
    proxy_url: str = "",
) -> AirbnbSearchResult:
    """Search Airbnb whole-listing options for a group via pyairbnb.

    Airbnb age bands: <2 = infant, 2–12 = child, 13+ counts as adult — matched
    here against *children_ages*.
    """
    try:
        from pyairbnb import api as pyapi
        from pyairbnb import search as pysearch
        from pyairbnb import standardize as pystd
    except ImportError as e:
        return AirbnbSearchResult(
            listings=[], error=f"pyairbnb not installed (install kelly[trips]): {e}"
        )

    bbox = _geocode_bbox(location)
    if bbox is None:
        return AirbnbSearchResult(
            listings=[],
            error=f"could not geocode area {location!r} via OSM Nominatim",
        )
    sw_lat, sw_long, ne_lat, ne_long = bbox

    infants = sum(1 for a in children_ages if a < 2)
    kids = sum(1 for a in children_ages if 2 <= a <= 12)
    teens_or_older = sum(1 for a in children_ages if a > 12)

    try:
        api_key = pyapi.get(proxy_url)
        results_raw = pysearch.get(
            api_key,
            "",  # cursor — first page
            check_in.isoformat(),
            check_out.isoformat(),
            ne_lat,
            ne_long,
            sw_lat,
            sw_long,
            zoom_value,
            currency,
            "",  # place_type — empty = any
            0,  # price_min
            int(max_total) if max_total else 0,
            [],  # amenities
            False,  # free_cancellation
            adults + teens_or_older,
            kids,
            infants,
            bedrooms_min,
            0,  # min_beds
            0,  # min_bathrooms
            language,
            proxy_url,
            "",  # hash
        )
        results = pystd.from_search(results_raw)
    except Exception as e:  # noqa: BLE001 — pyairbnb raises a mix of types
        return AirbnbSearchResult([], error=f"{type(e).__name__}: {e}")

    listings = [_parse_listing(item) for item in results if isinstance(item, dict)]
    note = None
    if max_total is not None:
        listings = [
            ln for ln in listings
            if ln.price_total is None or ln.price_total <= Decimal(str(max_total))
        ]
    return AirbnbSearchResult(listings=listings, error=None, note=note)
