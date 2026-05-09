"""Combine train + stay searches for a single trip_id and curate a shortlist."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from kelly.history_store import SqliteHistoryStore
from kelly.md_config import KellyConfig, TrainRow
from kelly.providers.airbnb import AirbnbListing, AirbnbSearchResult
from kelly.providers.playwright_eurostar import (
    EurostarJourney,
    EurostarSearchResult,
    classify_passengers,
)
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


def _airbnb_book_url(
    listing_id: str, check_in: str, check_out: str, adults: int, children: int, infants: int
) -> str:
    """Pre-filled Airbnb room URL — lands on the room with dates and pax loaded."""
    return (
        f"https://www.airbnb.com/rooms/{listing_id}"
        f"?check_in={check_in}&check_out={check_out}"
        f"&adults={adults}&children={children}&infants={infants}"
    )


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
    if stay is not None:
        stay_result = search_stay(cfg, stay, store=store, persist=persist)
        stays["primary"] = {
            "row": stay.model_dump(mode="json"),
            "result": stay_result_to_jsonable(stay_result),
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
