"""Cash flight search via RapidAPI google-flights-data."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

RAPIDAPI_GOOGLE_FLIGHTS_BASE = "https://google-flights-data.p.rapidapi.com"
RAPIDAPI_HOST = "google-flights-data.p.rapidapi.com"


@dataclass
class CashSearchResult:
    departure_date: date
    origin_iata: str
    destination_iata: str
    cabin: str
    best_offer_id: str | None
    best_total_amount: Decimal | None
    best_total_currency: str | None
    offer_count: int
    error: str | None = None
    return_date: date | None = None
    itinerary_details: list[dict[str, Any]] = field(default_factory=list)


def _normalize_cabin(cabin: str) -> str:
    return cabin.strip().lower().replace(" ", "_")


def _cabin_class(cabin: str) -> int:
    """API cabinClass: 1=economy … 4=first."""
    m = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}
    return m.get(_normalize_cabin(cabin), 1)


def _passenger_counts(passengers: list[dict[str, str]]) -> dict[str, int]:
    adults = children = infants_in_seat = infants_on_lap = 0
    for p in passengers:
        t = (p.get("type") or "adult").strip().lower()
        if t == "adult":
            adults += 1
        elif t == "child":
            children += 1
        elif t in ("infant_without_seat", "infant_on_lap"):
            infants_on_lap += 1
        elif t in ("infant", "infant_with_seat", "infant_in_seat"):
            infants_in_seat += 1
        else:
            adults += 1
    if adults < 1 and (children + infants_in_seat + infants_on_lap) > 0:
        adults = 1
    return {
        "adults": max(1, adults),
        "children": children,
        "infants_in_seat": infants_in_seat,
        "infants_on_lap": infants_on_lap,
    }


def _parse_price(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = str(value).strip()
    cleaned = re.sub(r"[^\d.]", "", s)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _flight_currency(entry: dict[str, Any], fallback: str) -> str:
    for key in ("currency", "totalCurrency", "priceCurrency"):
        cur = entry.get(key)
        if isinstance(cur, str) and cur.strip():
            return cur.strip().upper()
    return fallback.upper()


def _segment_list_from_part(part: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("itinerary", "itineraryDetails", "segments", "legs", "flights"):
        raw = part.get(key)
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
    return []


def _collect_segments_from_entry(entry: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    """Return (segments, total_stops hint from provider) for one priced offer."""
    stops_hint: int | None = None
    if isinstance(entry.get("stops"), int):
        stops_hint = entry["stops"]
    top_segments = _segment_list_from_part(entry)
    if top_segments:
        inferred = max(0, len(top_segments) - 1)
        return top_segments, stops_hint if stops_hint is not None else inferred

    segs: list[dict[str, Any]] = []
    for block_key in (
        "outbound",
        "outboundFlight",
        "inbound",
        "inboundFlight",
        "return",
        "returnFlight",
    ):
        part = entry.get(block_key)
        if isinstance(part, dict):
            segs.extend(_segment_list_from_part(part))
    if segs:
        inferred = max(0, len(segs) - 1)
        return segs, stops_hint if stops_hint is not None else inferred
    return [], stops_hint


def itinerary_details_from_rapid_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Build itinerary leg dicts from one topFlights / otherFlights item."""
    segments, offer_stops = _collect_segments_from_entry(entry)
    if not segments:
        return []
    stops_val = offer_stops if offer_stops is not None else max(0, len(segments) - 1)
    out: list[dict[str, Any]] = []
    for seg in segments:
        airline = (
            seg.get("airlineName")
            or seg.get("airline")
            or seg.get("carrier")
            or seg.get("airlineCode")
        )
        if airline is not None:
            airline = str(airline).strip() or None
        dep = seg.get("departureTime") or seg.get("departure_time")
        arr = seg.get("arrivalTime") or seg.get("arrival_time")
        dur = seg.get("durationMinutes")
        if dur is None:
            dur = seg.get("duration")
            if isinstance(dur, str) and dur.isdigit():
                dur = int(dur)
        out.append(
            {
                "airlineName": airline,
                "departureTime": None if dep is None else str(dep).strip() or None,
                "arrivalTime": None if arr is None else str(arr).strip() or None,
                "durationMinutes": dur,
                "stops": stops_val,
            }
        )
    return out


def _offer_identity(entry: dict[str, Any]) -> str | None:
    for key in ("id", "offerId", "bookingToken", "token", "flightId"):
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _response_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("error", "detail"):
        v = payload.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("error",):
            v = data.get(key)
            if v is not None and str(v).strip():
                return str(v).strip()
    return None


def _collect_priced_offers(
    data: dict[str, Any], currency_hint: str
) -> list[tuple[Decimal, str, dict[str, Any]]]:
    out: list[tuple[Decimal, str, dict[str, Any]]] = []
    for key in ("topFlights", "otherFlights"):
        for entry in data.get(key) or []:
            if not isinstance(entry, dict):
                continue
            amt = _parse_price(entry.get("price"))
            if amt is None:
                continue
            cur = _flight_currency(entry, currency_hint)
            out.append((amt, cur, entry))
    return out


def search_cash_best(
    api_key: str,
    *,
    origin_iata: str,
    destination_iata: str,
    departure_date: date,
    cabin: str,
    passengers: list[dict[str, str]],
    currency: str = "USD",
    return_date: date | None = None,
    max_connections: int = 1,
    timeout_s: float = 60.0,
) -> CashSearchResult:
    """Google Flights via RapidAPI; cheapest option from topFlights / otherFlights."""
    del max_connections  # not used by this API surface
    origin_iata = origin_iata.upper().strip()
    destination_iata = destination_iata.upper().strip()
    cabin_n = _normalize_cabin(cabin)
    counts = _passenger_counts(passengers)
    cur = (currency or "USD").strip().upper() or "USD"

    if return_date is not None:
        path = "/flights/search-roundtrip"
        params: dict[str, str | int] = {
            "departureId": origin_iata,
            "arrivalId": destination_iata,
            "departureDate": departure_date.isoformat(),
            "returnDate": return_date.isoformat(),
            "adults": counts["adults"],
            "children": counts["children"],
            "cabinClass": _cabin_class(cabin_n),
            "currency": cur,
        }
    else:
        path = "/flights/search-oneway"
        params = {
            "departureId": origin_iata,
            "arrivalId": destination_iata,
            "departureDate": departure_date.isoformat(),
            "adults": counts["adults"],
            "children": counts["children"],
            "cabinClass": _cabin_class(cabin_n),
            "currency": cur,
        }

    url = f"{RAPIDAPI_GOOGLE_FLIGHTS_BASE}{path}"
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": RAPIDAPI_HOST,
    }

    empty = CashSearchResult(
        departure_date=departure_date,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        cabin=cabin_n,
        best_offer_id=None,
        best_total_amount=None,
        best_total_currency=None,
        offer_count=0,
        error=None,
        return_date=return_date,
        itinerary_details=[],
    )

    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=timeout_s)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:  # noqa: BLE001
        return CashSearchResult(
            departure_date=departure_date,
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            cabin=cabin_n,
            best_offer_id=None,
            best_total_amount=None,
            best_total_currency=None,
            offer_count=0,
            error=str(e),
            return_date=return_date,
            itinerary_details=[],
        )

    err = _response_error(payload)
    if err:
        return CashSearchResult(
            departure_date=departure_date,
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            cabin=cabin_n,
            best_offer_id=None,
            best_total_amount=None,
            best_total_currency=None,
            offer_count=0,
            error=err,
            return_date=return_date,
            itinerary_details=[],
        )

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = {}

    priced = _collect_priced_offers(data, cur)
    n_offers = sum(len(data.get(k) or []) for k in ("topFlights", "otherFlights"))

    if not priced:
        return CashSearchResult(
            departure_date=departure_date,
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            cabin=cabin_n,
            best_offer_id=None,
            best_total_amount=None,
            best_total_currency=None,
            offer_count=n_offers,
            error=None,
            return_date=return_date,
            itinerary_details=[],
        )

    def sort_key(t: tuple[Decimal, str, dict[str, Any]]) -> tuple[Decimal, str]:
        return (t[0], t[1])

    best_amt, best_cur, best_entry = min(priced, key=sort_key)
    offer_id = _offer_identity(best_entry)
    legs = itinerary_details_from_rapid_entry(best_entry)

    return CashSearchResult(
        departure_date=departure_date,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        cabin=cabin_n,
        best_offer_id=offer_id,
        best_total_amount=best_amt,
        best_total_currency=best_cur,
        offer_count=n_offers,
        error=None,
        return_date=return_date,
        itinerary_details=legs,
    )
