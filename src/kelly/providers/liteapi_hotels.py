"""Hotel search via LiteAPI (Nuitée) — REST, no SDK dep.

LiteAPI aggregates inventory across major hotel suppliers (Booking, Expedia,
Hotelbeds, etc.) and exposes a clean REST API with star rating + user rating +
review count + photos baked into the response. Authentication is a single
header ``X-API-Key``. The sandbox key prefix is ``sand_``; production is
``prod_``.

Three endpoints are hit per search:
  1. ``GET /data/hotels`` — catalog (id, name, stars, rating, reviewCount, geo).
  2. ``POST /hotels/min-rates`` — minimum rate per hotelId for the requested
     dates + multi-room occupancy. Fast path used to short-list candidates.
  3. ``POST /hotels/rates`` — full rate breakdown for the top candidates,
     including ``taxesAndFees`` with ``included: false`` entries (typically the
     local tourist tax / taxe de séjour paid on arrival). Without this second
     call the shortlist undershoots the true all-in total by ~10-15% in EU
     cities, which is enough to flip apples-to-apples comparisons against
     Airbnb (where the displayed total is regulator-mandated all-in).

We deliberately roll a thin httpx wrapper instead of using the official
``liteapi-sdk`` PyPI package (last release 2024-09, stale upstream) — the REST
surface is small enough that owning the call sites is cheaper than tracking a
third-party SDK.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

# Map common country names that appear in kelly.md `area` fields to ISO-2.
# LiteAPI's /data/hotels filter requires ISO-2 country codes, so we parse the
# last comma-separated token of `area` and map it here. Unmapped countries
# surface as a clean error rather than a silent miss.
_COUNTRY_NAME_TO_ISO2: dict[str, str] = {
    "france": "FR",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "ireland": "IE",
    "spain": "ES",
    "portugal": "PT",
    "italy": "IT",
    "germany": "DE",
    "netherlands": "NL",
    "belgium": "BE",
    "switzerland": "CH",
    "austria": "AT",
    "czech republic": "CZ",
    "czechia": "CZ",
    "denmark": "DK",
    "sweden": "SE",
    "norway": "NO",
    "finland": "FI",
    "poland": "PL",
    "greece": "GR",
    "united states": "US",
    "usa": "US",
    "united states of america": "US",
    "canada": "CA",
    "mexico": "MX",
    "brazil": "BR",
    "argentina": "AR",
    "japan": "JP",
    "australia": "AU",
}


@dataclass
class HotelListing:
    """One curated hotel hit, normalised for downstream scoring + display.

    *price_total* mirrors the LiteAPI ``retailRate.total`` and includes the
    supplier service fee but **not** taxes flagged ``included: false`` in the
    ``taxesAndFees`` array (typically local tourist tax). *price_all_in* adds
    those — that's what to display against Airbnb totals for an honest
    comparison. *taxes_at_property* records the sum of pay-on-arrival taxes
    so the front can show the breakdown.
    """

    id: str
    name: str | None
    stars: float | None
    rating: float | None
    review_count: int | None
    price_total: Decimal | None
    price_all_in: Decimal | None
    taxes_at_property: Decimal | None
    suggested_price: Decimal | None
    currency: str | None
    address: str | None
    city: str | None
    country: str | None
    lat: float | None
    lng: float | None
    main_photo: str | None
    description: str | None
    facility_ids: list[int]
    board_type: str | None
    refundable: bool | None
    offer_id: str | None  # opaque; required for the LiteAPI prebook+book flow
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class HotelSearchResult:
    listings: list[HotelListing]
    error: str | None = None
    note: str | None = None


def _to_decimal(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_float(v: object) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: object) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_area(area: str) -> tuple[str, str] | None:
    """Parse a kelly.md area string ("Paris, France") → (city, ISO-2 country).

    Returns ``None`` if the country segment can't be mapped — the caller
    should surface this as a clean error so the user can fix the config.
    """
    if not area:
        return None
    parts = [p.strip() for p in area.split(",") if p.strip()]
    if len(parts) < 2:
        return None
    city = parts[0]
    country_name = parts[-1].lower()
    iso2 = _COUNTRY_NAME_TO_ISO2.get(country_name)
    if iso2 is None:
        return None
    return city, iso2


def _client(api_key: str, base_url: str, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers={
            "X-API-Key": api_key,
            "accept": "application/json",
            "content-type": "application/json",
        },
        timeout=timeout,
    )


def _list_hotels(
    client: httpx.Client,
    city_name: str,
    country_code: str,
    limit: int,
    min_rating: float | None,
) -> list[dict[str, Any]]:
    params: dict[str, str | int | float] = {
        "countryCode": country_code,
        "cityName": city_name,
        "limit": limit,
    }
    if min_rating is not None:
        params["minRating"] = min_rating
    r = client.get("/data/hotels", params=params)
    r.raise_for_status()
    return list(r.json().get("data") or [])


def _min_rates(
    client: httpx.Client,
    hotel_ids: list[str],
    check_in: date,
    check_out: date,
    occupancies: list[dict[str, Any]],
    currency: str,
    guest_nationality: str,
) -> list[dict[str, Any]]:
    payload = {
        "hotelIds": hotel_ids,
        "checkin": check_in.isoformat(),
        "checkout": check_out.isoformat(),
        "occupancies": occupancies,
        "currency": currency,
        "guestNationality": guest_nationality,
    }
    r = client.post("/hotels/min-rates", json=payload)
    r.raise_for_status()
    return list(r.json().get("data") or [])


def _full_rates(
    client: httpx.Client,
    hotel_ids: list[str],
    check_in: date,
    check_out: date,
    occupancies: list[dict[str, Any]],
    currency: str,
    guest_nationality: str,
    timeout: int = 20,
) -> dict[str, dict[str, Any]]:
    """Call ``/hotels/rates`` for *hotel_ids* and reduce to one entry per hotel.

    Returns ``{hotel_id: {"total": Decimal, "taxes_at_property": Decimal,
    "board_type": str|None, "refundable": bool|None, "rate_offer_id": str|None}}``.
    For each occupancy slot (room) we pick the cheapest rate; the per-hotel
    total is the sum across occupancy slots. Mirrors what the user would
    actually book.
    """
    if not hotel_ids:
        return {}
    payload = {
        "hotelIds": hotel_ids,
        "checkin": check_in.isoformat(),
        "checkout": check_out.isoformat(),
        "occupancies": occupancies,
        "currency": currency,
        "guestNationality": guest_nationality,
        "timeout": timeout,
    }
    r = client.post("/hotels/rates", json=payload)
    r.raise_for_status()
    entries = list(r.json().get("data") or [])
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        hid = entry.get("hotelId")
        if not hid:
            continue
        # Flatten every rate across all roomTypes, then group by occupancyNumber.
        all_rates: list[dict[str, Any]] = []
        for rt in entry.get("roomTypes") or []:
            for rate in rt.get("rates") or []:
                all_rates.append(rate)
        if not all_rates:
            continue
        per_occ: dict[int, dict[str, Any]] = {}
        for rate in all_rates:
            occ_num = rate.get("occupancyNumber")
            if occ_num is None:
                continue
            retail = (rate.get("retailRate") or {})
            total_arr = retail.get("total") or []
            if not total_arr:
                continue
            total_amt = _to_decimal((total_arr[0] or {}).get("amount"))
            if total_amt is None:
                continue
            cur = per_occ.get(occ_num)
            if cur is None or (cur.get("_total") or Decimal("inf")) > total_amt:
                # Sum the non-included taxes; included ones are already in total.
                pay_on_arrival = Decimal("0")
                for tax in retail.get("taxesAndFees") or []:
                    if not tax.get("included", True):
                        amt = _to_decimal(tax.get("amount"))
                        if amt is not None:
                            pay_on_arrival += amt
                per_occ[occ_num] = {
                    "_total": total_amt,
                    "_taxes": pay_on_arrival,
                    "board_type": rate.get("boardName") or rate.get("boardType"),
                    "refundable": _refundable_from_policy(
                        rate.get("cancellationPolicies")
                    ),
                }
        if not per_occ:
            continue
        total_sum = sum((r["_total"] for r in per_occ.values()), Decimal("0"))
        taxes_sum = sum((r["_taxes"] for r in per_occ.values()), Decimal("0"))
        # Board / refundable take the worst-case across rooms (so user isn't
        # surprised if one of the rooms has no breakfast or is non-refundable).
        boards = {r["board_type"] for r in per_occ.values() if r.get("board_type")}
        refundables = [r["refundable"] for r in per_occ.values()]
        refundable: bool | None
        if any(r is False for r in refundables):
            refundable = False
        elif all(r is True for r in refundables):
            refundable = True
        else:
            refundable = None
        out[hid] = {
            "total": total_sum,
            "taxes_at_property": taxes_sum,
            "board_type": ", ".join(sorted(boards)) if boards else None,
            "refundable": refundable,
        }
    return out


def _refundable_from_policy(policies: dict[str, Any] | None) -> bool | None:
    """Cheap heuristic — LiteAPI marks fully non-refundable rates with an
    empty/zero cancellation window. If we can't parse the structure cleanly,
    return None (unknown) rather than guessing wrong."""
    if not policies:
        return None
    infos = policies.get("cancelPolicyInfos") or []
    if not infos:
        return None
    # If any cancellation window exists with a non-100% charge, refundable.
    for info in infos:
        amount = info.get("amount")
        try:
            if amount is not None and float(amount) < 100:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _merge(
    hotels: list[dict[str, Any]],
    rates: list[dict[str, Any]],
    full_rate_by_id: dict[str, dict[str, Any]],
    currency: str,
) -> list[HotelListing]:
    """Join catalog (stars/rating/photos) + min-rates (price/offerId) + full
    rates (all-in price including pay-on-arrival taxes, board type, refundable).
    Hotels that didn't make the full-rates round-trip still get listed with
    ``price_all_in=None`` so the caller can distinguish missing-data from
    truly-tax-free.
    """
    by_id: dict[str, dict[str, Any]] = {h["id"]: h for h in hotels if h.get("id")}
    out: list[HotelListing] = []
    for rate in rates:
        hid = rate.get("hotelId")
        if not hid or hid not in by_id:
            continue
        h = by_id[hid]
        full = full_rate_by_id.get(hid) or {}
        base_total = _to_decimal(rate.get("price"))
        full_total = full.get("total") if isinstance(full.get("total"), Decimal) else None
        taxes_extra = full.get("taxes_at_property") if isinstance(
            full.get("taxes_at_property"), Decimal
        ) else None
        # Prefer the full-rates total when we have it (it's the same number but
        # computed by summing per-room cheapest); fall back to min-rates price.
        price_total = full_total if full_total is not None else base_total
        price_all_in = (
            (price_total + taxes_extra) if price_total is not None and taxes_extra is not None
            else None
        )
        out.append(
            HotelListing(
                id=str(hid),
                name=h.get("name"),
                stars=_to_float(h.get("stars")),
                rating=_to_float(h.get("rating")),
                review_count=_to_int(h.get("reviewCount")),
                price_total=price_total,
                price_all_in=price_all_in,
                taxes_at_property=taxes_extra,
                suggested_price=_to_decimal(rate.get("suggestedSellingPrice")),
                currency=currency,
                address=h.get("address"),
                city=h.get("city"),
                country=h.get("country"),
                lat=_to_float(h.get("latitude")),
                lng=_to_float(h.get("longitude")),
                main_photo=h.get("main_photo") or h.get("thumbnail"),
                description=h.get("hotelDescription"),
                facility_ids=list(h.get("facilityIds") or []),
                board_type=full.get("board_type"),
                refundable=full.get("refundable"),
                offer_id=rate.get("offerId"),
                raw={"hotel": h, "rate": rate, "full": full},
            )
        )
    return out


def search_hotels(
    *,
    location: str,
    check_in: date,
    check_out: date,
    occupancies: list[dict[str, Any]],
    min_stars: float | None = None,
    min_rating: float | None = None,
    max_total: float | None = None,
    currency: str = "GBP",
    guest_nationality: str = "GB",
    catalog_limit: int = 100,
    full_rate_top_n: int = 10,
    api_key: str | None = None,
    base_url: str | None = None,
) -> HotelSearchResult:
    """Search hotels via LiteAPI for a multi-room party.

    *occupancies* is a list of room specs in LiteAPI's shape, e.g.
    ``[{"adults": 2, "children": [5]}, {"adults": 3, "children": [3]}]``.
    *min_stars* filters the catalog client-side (LiteAPI's ``minRating``
    parameter operates on the average *user* rating, not the official star
    classification).
    """
    api_key = api_key or os.environ.get("LITEAPI_API_KEY")
    if not api_key:
        return HotelSearchResult(
            listings=[],
            error="LITEAPI_API_KEY not set — add it to .env to enable hotel search",
        )
    base_url = base_url or os.environ.get(
        "LITEAPI_BASE_URL", "https://api.liteapi.travel/v3.0"
    )

    parsed = parse_area(location)
    if parsed is None:
        return HotelSearchResult(
            listings=[],
            error=(
                f"could not parse country from area {location!r} — "
                "expected 'City, Country' (e.g. 'Paris, France')"
            ),
        )
    city_name, country_code = parsed

    try:
        with _client(api_key, base_url) as client:
            hotels = _list_hotels(
                client, city_name, country_code, catalog_limit, min_rating
            )
            if min_stars is not None:
                hotels = [
                    h for h in hotels
                    if _to_float(h.get("stars")) is not None
                    and (_to_float(h.get("stars")) or 0) >= min_stars
                ]
            if not hotels:
                return HotelSearchResult(
                    listings=[],
                    note=f"no hotels matched the catalog filter in {city_name}, {country_code}",
                )
            ids = [h["id"] for h in hotels if h.get("id")]
            rates = _min_rates(
                client,
                ids,
                check_in,
                check_out,
                occupancies,
                currency,
                guest_nationality,
            )
            # Top-N (cheapest by min-rates) get a second round-trip to /hotels/rates
            # for the all-in price breakdown. Anything outside the top-N still
            # appears but with ``price_all_in=None`` so the caller can tell.
            cheap_rates = sorted(
                (r for r in rates if r.get("price") is not None),
                key=lambda r: float(r["price"]),
            )[:full_rate_top_n]
            full_rate_ids = [r["hotelId"] for r in cheap_rates if r.get("hotelId")]
            full_rate_by_id = (
                _full_rates(
                    client,
                    full_rate_ids,
                    check_in,
                    check_out,
                    occupancies,
                    currency,
                    guest_nationality,
                )
                if full_rate_ids
                else {}
            )
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return HotelSearchResult(
            listings=[], error=f"LiteAPI HTTP {e.response.status_code}: {body}"
        )
    except httpx.HTTPError as e:
        return HotelSearchResult(listings=[], error=f"{type(e).__name__}: {e}")

    listings = _merge(hotels, rates, full_rate_by_id, currency)

    if max_total is not None:
        cap = Decimal(str(max_total))
        # Filter against the all-in total when we have it (honest budget check);
        # fall back to base price for hotels outside the top-N where we didn't
        # fetch tax breakdown.
        def _within_budget(ln: HotelListing) -> bool:
            ref = ln.price_all_in if ln.price_all_in is not None else ln.price_total
            return ref is None or ref <= cap

        listings = [ln for ln in listings if _within_budget(ln)]

    return HotelSearchResult(listings=listings)


def occupancies_from_pax(
    adults: int,
    children_ages: list[int],
    rooms: int,
) -> list[dict[str, Any]]:
    """Distribute a party across *rooms* in LiteAPI's occupancy shape.

    Hotel booking platforms treat anyone 13+ as an adult; lap-held infants are
    excluded entirely (most properties charge nothing and don't reserve a bed).
    Adults are distributed evenly; children are spread across rooms one-at-a-
    time so no room is left without a chaperone. This is a planning heuristic —
    the actual room assignment is the user's call at booking time.
    """
    rooms = max(1, rooms)
    real_adults = int(adults) + sum(1 for a in children_ages if a >= 13)
    real_children = [a for a in children_ages if 2 <= a < 13]
    # Infants (< 2) intentionally dropped — most hotels don't require declaring
    # them at booking, and including them risks pushing rooms over capacity.

    # Even adult split with remainder pushed to the first rooms.
    base, extra = divmod(real_adults, rooms)
    adults_per_room = [base + (1 if i < extra else 0) for i in range(rooms)]
    children_per_room: list[list[int]] = [[] for _ in range(rooms)]
    for i, age in enumerate(real_children):
        children_per_room[i % rooms].append(age)

    return [
        {"adults": max(1, adults_per_room[i]), "children": children_per_room[i]}
        for i in range(rooms)
    ]
