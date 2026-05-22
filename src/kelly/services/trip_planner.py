"""Combine train + stay searches for a single trip_id and curate a shortlist."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from kelly.history_store import SqliteHistoryStore
from kelly.md_config import KellyConfig, TrainRow
from kelly.providers.airbnb import AirbnbListing, AirbnbSearchResult
from kelly.providers.liteapi_hotels import HotelListing, HotelSearchResult
from kelly.providers.playwright_eurostar import (
    EurostarJourney,
    EurostarSearchResult,
    classify_passengers,
)
from kelly.services.hotel_service import hotel_result_to_jsonable, search_hotel
from kelly.services.stay_service import search_stay, stay_result_to_jsonable
from kelly.services.train_service import search_train, train_result_to_jsonable

# Eurostar's website caps a single booking transaction at 9 paying passengers
# (lap-held infants don't count). Larger parties either route through the
# Eurostar Groups Desk or split into multiple web bookings.
_EUROSTAR_WEB_BOOKING_MAX = 9
_PAX_KINDS = ("adult", "senior", "youth", "child", "infant")

# Central Paris bounding box (arrondissements 1–4, 8–11 + Île-de-la-Cité).
# Listings outside this box are dropped from the shortlist — typically labelled
# "near Paris" / suburban properties that fail the user's "reasonable location"
# bar (RER A access, walkable to metro).
_CENTRAL_PARIS_BBOX = {
    "min_lat": 48.835,
    "max_lat": 48.890,
    "min_lng": 2.300,
    "max_lng": 2.410,
}


def _coords_in_central_paris(listing: AirbnbListing) -> bool:
    if listing.lat is None or listing.lng is None:
        return False
    return (
        _CENTRAL_PARIS_BBOX["min_lat"] <= listing.lat <= _CENTRAL_PARIS_BBOX["max_lat"]
        and _CENTRAL_PARIS_BBOX["min_lng"] <= listing.lng <= _CENTRAL_PARIS_BBOX["max_lng"]
    )


def _hotel_in_central_paris(h: HotelListing) -> bool:
    if h.lat is None or h.lng is None:
        return False
    return (
        _CENTRAL_PARIS_BBOX["min_lat"] <= h.lat <= _CENTRAL_PARIS_BBOX["max_lat"]
        and _CENTRAL_PARIS_BBOX["min_lng"] <= h.lng <= _CENTRAL_PARIS_BBOX["max_lng"]
    )


def _airbnb_book_url(
    listing_id: str, check_in: str, check_out: str, adults: int, children: int, infants: int
) -> str:
    """Pre-filled Airbnb room URL — lands on the room with dates and pax loaded."""
    return (
        f"https://www.airbnb.com/rooms/{listing_id}"
        f"?check_in={check_in}&check_out={check_out}"
        f"&adults={adults}&children={children}&infants={infants}"
    )


def _normalize_inverse(value: float | None, lo: float, hi: float) -> float:
    """Map *value* in [lo, hi] to [0, 1] where lo→1 and hi→0 (cheaper is better).
    Missing values → 0.5 neutral so they neither over- nor under-perform.
    """
    if value is None or hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (hi - value) / (hi - lo)))


def _normalize(value: float | None, lo: float, hi: float) -> float:
    if value is None or hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _google_hotels_url(name: str | None, city: str | None) -> str | None:
    """Google Hotels search deep-link — bridge until the Kelly UI ships the
    LiteAPI prebook+book flow (v2 plan). Opens with the hotel name pre-queried
    so the user can verify availability + sanity-check the price."""
    if not name:
        return None
    from urllib.parse import quote_plus

    q = f"{name} {city}" if city else name
    return f"https://www.google.com/travel/search?q={quote_plus(q)}"


def _hotel_shortlist(
    res: HotelSearchResult,
    *,
    require_central: bool,
    max_total: float | None,
    take: int = 5,
) -> list[dict[str, Any]]:
    """Rank hotels by a composite score: cheaper + more stars + higher user
    rating + more reviews. Factor weights are explicit so the front (v2) can
    surface a "why this ranks first" breakdown next to each card.
    """
    cands: list[HotelListing] = list(res.listings)
    if require_central:
        cands = [h for h in cands if _hotel_in_central_paris(h)]

    # Use the all-in price (incl. pay-on-arrival taxes) when available — this
    # is what we compare against Airbnb's regulated total. Hotels without a
    # full-rate hit fall back to the base price.
    def _ref_price(h: HotelListing) -> Decimal | None:
        return h.price_all_in if h.price_all_in is not None else h.price_total

    if max_total is not None:
        cap = Decimal(str(max_total))
        cands = [h for h in cands if _ref_price(h) is None or (_ref_price(h) or cap) <= cap]
    if not cands:
        return []

    priced = [h for h in cands if _ref_price(h) is not None]
    if priced:
        prices = [float(_ref_price(h) or 0) for h in priced]
        p_lo, p_hi = min(prices), max(prices)
    else:
        p_lo, p_hi = 0.0, 1.0

    rated = [h.rating for h in cands if h.rating is not None]
    r_lo, r_hi = (min(rated), max(rated)) if rated else (0.0, 10.0)

    reviewed = [h.review_count for h in cands if h.review_count is not None]
    rc_lo, rc_hi = (min(reviewed), max(reviewed)) if reviewed else (0, 1)
    # log-scale review count so 50 vs 500 reviews isn't a 10x signal
    from math import log10

    weights = {"price": 0.40, "stars": 0.25, "rating": 0.20, "reviews": 0.15}

    scored: list[tuple[float, dict[str, float], HotelListing]] = []
    for h in cands:
        ref = _ref_price(h)
        price_n = _normalize_inverse(
            float(ref) if ref is not None else None, p_lo, p_hi
        )
        stars_n = _normalize(h.stars, 1.0, 5.0)
        rating_n = _normalize(h.rating, r_lo, r_hi) if rated else 0.5
        if h.review_count is not None and rc_hi > 0:
            reviews_n = _normalize(
                log10(max(1, h.review_count)),
                log10(max(1, rc_lo)),
                log10(max(1, rc_hi)),
            )
        else:
            reviews_n = 0.5

        factors = {
            "price": round(price_n, 3),
            "stars": round(stars_n, 3),
            "rating": round(rating_n, 3),
            "reviews": round(reviews_n, 3),
        }
        score = (
            weights["price"] * price_n
            + weights["stars"] * stars_n
            + weights["rating"] * rating_n
            + weights["reviews"] * reviews_n
        )
        scored.append((score, factors, h))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    for score, factors, h in scored[:take]:
        out.append(
            {
                "id": h.id,
                "name": h.name,
                "stars": h.stars,
                "rating": h.rating,
                "review_count": h.review_count,
                "price_total": str(h.price_total) if h.price_total is not None else None,
                "price_all_in": str(h.price_all_in) if h.price_all_in is not None else None,
                "taxes_at_property": (
                    str(h.taxes_at_property) if h.taxes_at_property is not None else None
                ),
                "suggested_price": (
                    str(h.suggested_price) if h.suggested_price is not None else None
                ),
                "currency": h.currency,
                "address": h.address,
                "city": h.city,
                "lat": h.lat,
                "lng": h.lng,
                "main_photo": h.main_photo,
                "board_type": h.board_type,
                "refundable": h.refundable,
                "offer_id": h.offer_id,
                "search_url": _google_hotels_url(h.name, h.city),
                "score": round(score, 4),
                "factors": factors,
            }
        )
    return out


def _airbnb_shortlist(
    res: AirbnbSearchResult,
    *,
    require_central: bool,
    bedrooms_min: int | None,
    max_total: float | None,
    check_in: str,
    check_out: str,
    adults: int,
    children: int,
    infants: int,
    take: int = 5,
) -> list[dict[str, Any]]:
    """Filter + rank Airbnb listings; return jsonable shortlist entries.

    Sorts by price asc with a small rating tiebreaker. Drops anything above
    *max_total* and (when *require_central*) anything outside central Paris.
    """
    cands: list[AirbnbListing] = list(res.listings)
    if require_central:
        cands = [ln for ln in cands if _coords_in_central_paris(ln)]
    if max_total is not None:
        cap = Decimal(str(max_total))
        cands = [ln for ln in cands if ln.price_total is None or ln.price_total <= cap]

    def sort_key(ln: AirbnbListing) -> tuple[Decimal, float]:
        price = ln.price_total if ln.price_total is not None else Decimal("99999999")
        # Higher rating = better; negate so asc sort prefers it after price.
        rating = -(ln.rating or 0.0)
        return (price, rating)

    cands.sort(key=sort_key)
    out: list[dict[str, Any]] = []
    for ln in cands[:take]:
        booking_url = (
            _airbnb_book_url(ln.id, check_in, check_out, adults, children, infants)
            if ln.id
            else ln.url
        )
        out.append(
            {
                "title": ln.title,
                "url": booking_url,
                "price_total": str(ln.price_total) if ln.price_total is not None else None,
                "currency": ln.price_currency,
                "rating": ln.rating,
                "lat": ln.lat,
                "lng": ln.lng,
                "id": ln.id,
            }
        )
    return out


def _split_pax_mix(pax: dict[str, int]) -> list[dict[str, int]]:
    """Split a pax mix into balanced booking groups for the Eurostar web form.

    Eurostar policy treats lap-held infants as not counting against the
    9-paying-passenger booking cap, but the *web form itself* counts every
    declared passenger (incl. infants), so a party where ``total >= 10`` is
    blocked from a single web transaction. We split on total, not paying, to
    match what the form actually accepts. Odd counts alternate between groups
    so adults/seniors/kids stay roughly balanced (chaperone capacity per
    group).
    """
    total = sum(pax.get(k, 0) for k in _PAX_KINDS)
    if total <= _EUROSTAR_WEB_BOOKING_MAX:
        return [dict(pax)]

    a: dict[str, int] = {k: 0 for k in _PAX_KINDS}
    b: dict[str, int] = {k: 0 for k in _PAX_KINDS}
    flip = True
    for kind in _PAX_KINDS:
        n = pax.get(kind, 0)
        half, rem = divmod(n, 2)
        a[kind] = half
        b[kind] = half
        if rem:
            (a if flip else b)[kind] += 1
            flip = not flip
    return [a, b]


def _replace_pax_in_url(deep_link: str, pax: dict[str, int]) -> str:
    """Return *deep_link* with its adult/senior/youth/child/infant params
    replaced by *pax*; preserve everything else (origin, destination, outbound)."""
    parsed = urlparse(deep_link)
    qs = [(k, v) for k, v in parse_qsl(parsed.query) if k not in _PAX_KINDS]
    for k in _PAX_KINDS:
        if pax.get(k, 0) > 0:
            qs.append((k, str(pax[k])))
    return urlunparse(parsed._replace(query=urlencode(qs)))


def _booking_groups_for(deep_link: str, pax: dict[str, int]) -> list[dict[str, Any]]:
    """One entry per split group: {pax: ..., paying: int, url: str}."""
    groups = _split_pax_mix(pax)
    out: list[dict[str, Any]] = []
    for g in groups:
        paying = sum(g.get(k, 0) for k in ("adult", "senior", "youth", "child"))
        out.append(
            {
                "pax": {k: g[k] for k in _PAX_KINDS if g.get(k, 0) > 0},
                "paying": paying,
                "url": _replace_pax_in_url(deep_link, g),
            }
        )
    return out


def _eurostar_shortlist(
    res: EurostarSearchResult,
    *,
    pax: dict[str, int] | None = None,
    fare_class: str = "standard",
    take_per_date: int = 6,
) -> list[dict[str, Any]]:
    """Per (date, departure_time) pick the cheapest fare in *fare_class*.

    Each `EurostarJourney` carries a date inside `raw['date']`. The provider
    emits one journey-row per fare-class-per-train, so we group by
    (date, depart_time) and select the row whose ``class_`` matches.
    """
    grouped: dict[tuple[str, str | None], list[EurostarJourney]] = {}
    for j in res.journeys:
        d = (j.raw or {}).get("date") or ""
        grouped.setdefault((d, j.depart_time), []).append(j)

    picks: list[dict[str, Any]] = []
    for (d, dt), bucket in grouped.items():
        # Pick the journey for the requested fare class; fall back to cheapest.
        target = next(
            (j for j in bucket if (j.class_ or "").lower() == fare_class.lower()),
            None,
        )
        if target is None:
            priced = [j for j in bucket if j.total_price is not None]
            if not priced:
                continue
            target = min(priced, key=lambda j: j.total_price or Decimal("999999"))

        entry: dict[str, Any] = {
            "date": d,
            "depart": target.depart_time,
            "arrive": target.arrive_time,
            "duration_min": target.duration_min,
            "changes": target.changes,
            "class": target.class_,
            "price_per_adult": (
                str(target.total_price) if target.total_price is not None else None
            ),
            "currency": target.price_currency,
            "deep_link": target.deep_link,
        }
        if pax is not None and target.deep_link:
            entry["booking_groups"] = _booking_groups_for(target.deep_link, pax)
        picks.append(entry)

    # Sort by date, then depart time; trim per date.
    picks.sort(key=lambda p: (p["date"], p["depart"] or ""))
    by_date: dict[str, list[dict[str, Any]]] = {}
    for p in picks:
        by_date.setdefault(p["date"], []).append(p)
    out: list[dict[str, Any]] = []
    for d, lst in sorted(by_date.items()):
        out.extend(lst[:take_per_date])
    return out


def plan_trip(
    cfg: KellyConfig,
    trip_id: str,
    *,
    store: SqliteHistoryStore | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Look up trains `<trip_id>-out` / `<trip_id>-back` and stay `<trip_id>`,
    run searches, and return both raw results and a curated shortlist.

    When *store* is provided and *persist* is True, each train and stay search
    appends to the corresponding history table (see history_store.py).
    """
    out = next((t for t in cfg.trains if t.id == f"{trip_id}-out"), None)
    back = next((t for t in cfg.trains if t.id == f"{trip_id}-back"), None)
    single = next((t for t in cfg.trains if t.id == trip_id), None) if not (out or back) else None
    stay = next((s for s in cfg.stays if s.id == trip_id), None)

    trains: dict[str, Any] = {}
    train_results: dict[str, tuple[EurostarSearchResult, TrainRow]] = {}
    for label, row in (("outbound", out), ("return", back), ("single", single)):
        if row is None:
            continue
        r = search_train(cfg, row, store=store, persist=persist)
        train_results[label] = (r, row)
        trains[label] = {
            "row": row.model_dump(by_alias=True, mode="json"),
            "result": train_result_to_jsonable(r),
        }

    stays: dict[str, Any] = {}
    stay_result: AirbnbSearchResult | None = None
    hotel_result: HotelSearchResult | None = None
    if stay is not None:
        stay_result = search_stay(cfg, stay, store=store, persist=persist)
        stays["primary"] = {
            "row": stay.model_dump(mode="json"),
            "result": stay_result_to_jsonable(stay_result),
        }
        # Hotel fan-out via LiteAPI runs alongside Airbnb so the shortlist can
        # surface both options for the user to compare. Errors (missing API
        # key, unmappable country) come back inside the result as ``error``
        # and are exposed below in the JSON payload — the Airbnb side keeps
        # working regardless.
        hotel_result = search_hotel(cfg, stay, store=store, persist=persist)
        stays["hotel"] = {
            "row": stay.model_dump(mode="json"),
            "result": hotel_result_to_jsonable(hotel_result),
        }

    # Curate
    shortlist: dict[str, Any] = {}
    if stay_result is not None and stay is not None:
        # Treat 'paris' or 'paris-' anywhere in the area or trip_id as a hint
        # that the central-Paris filter is meaningful.
        require_central = "paris" in (stay.area or "").lower() or "paris" in trip_id.lower()
        # Mirror the providers/airbnb.py age bucketing so the booking URL matches
        # what was searched: <2 = infant, 2–12 = child, 13+ rolls into adults.
        ages = list(stay.children_ages or [])
        infants = sum(1 for a in ages if a < 2)
        children = sum(1 for a in ages if 2 <= a <= 12)
        teens_or_older = sum(1 for a in ages if a > 12)
        shortlist["airbnb"] = _airbnb_shortlist(
            stay_result,
            require_central=require_central,
            bedrooms_min=stay.bedrooms_min,
            max_total=stay.max_total,
            check_in=stay.check_in.isoformat(),
            check_out=stay.check_out.isoformat(),
            adults=stay.adults + teens_or_older,
            children=children,
            infants=infants,
        )
        if hotel_result is not None:
            shortlist["hotels"] = _hotel_shortlist(
                hotel_result,
                require_central=require_central,
                max_total=stay.max_total,
            )
    for label, (r, row) in train_results.items():
        pax = classify_passengers(
            row.adults, row.seniors, row.teens, list(row.children_ages or [])
        )
        shortlist[f"eurostar_{label}"] = _eurostar_shortlist(r, pax=pax)

    missing = [
        name
        for name, ok in (
            ("trains.outbound", out is not None),
            ("trains.return", back is not None),
            ("trains.single", single is not None),
            ("stay", stay is not None),
        )
        if not ok
    ]
    return {
        "trip_id": trip_id,
        "trains": trains,
        "stays": stays,
        "shortlist": shortlist,
        "missing": missing,
    }
