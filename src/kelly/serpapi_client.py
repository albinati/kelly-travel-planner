"""Cash flight search via SerpApi (Google Flights engine)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

SERPAPI_SEARCH_JSON = "https://serpapi.com/search.json"


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
    # Per-leg segments for the selected (cheapest) SerpApi offer.
    itinerary_details: list[dict[str, Any]] = field(default_factory=list)


def _normalize_cabin(cabin: str) -> str:
    return cabin.strip().lower().replace(" ", "_")


def _travel_class(cabin: str) -> int:
    """SerpApi travel_class: 1–4 (economy … first)."""
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
    cur = entry.get("currency")
    if isinstance(cur, str) and cur.strip():
        return cur.strip().upper()
    return fallback.upper()


def _format_layover_after(layovers: list[Any], segment_index: int) -> Any:
    if segment_index >= len(layovers):
        return None
    lo = layovers[segment_index]
    if isinstance(lo, dict):
        for k in ("duration", "wait_time", "time", "name", "id"):
            v = lo.get(k)
            if v is not None and str(v).strip():
                return v
        return lo
    return lo


def itinerary_details_from_flight_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Build itinerary leg dicts from one SerpApi `best_flights` / `other_flights` item."""
    raw = entry.get("flights")
    if not isinstance(raw, list):
        return []
    layovers_raw = entry.get("layovers")
    layovers_list: list[Any] = layovers_raw if isinstance(layovers_raw, list) else []
    stops = max(0, len(raw) - 1)
    out: list[dict[str, Any]] = []
    for i, seg in enumerate(raw):
        if not isinstance(seg, dict):
            continue
        airline = seg.get("airline")
        if airline is not None:
            airline = str(airline).strip() or None
        dep = seg.get("departure_time")
        arr = seg.get("arrival_time")
        duration = seg.get("duration")
        layover_after = (
            _format_layover_after(layovers_list, i) if i < stops else None
        )
        out.append(
            {
                "airline": airline,
                "departure_time": None if dep is None else str(dep).strip() or None,
                "arrival_time": None if arr is None else str(arr).strip() or None,
                "duration": duration,
                "stops": stops,
                "layover_after": layover_after,
            }
        )
    return out


def _collect_priced_offers(
    data: dict[str, Any], currency_hint: str
) -> list[tuple[Decimal, str, dict[str, Any]]]:
    out: list[tuple[Decimal, str, dict[str, Any]]] = []
    for key in ("best_flights", "other_flights"):
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
    max_connections: int = 1,
    timeout_s: float = 60.0,
) -> CashSearchResult:
    """One-way Google Flights via SerpApi; cheapest option by parsed price."""
    origin_iata = origin_iata.upper().strip()
    destination_iata = destination_iata.upper().strip()
    cabin_n = _normalize_cabin(cabin)
    counts = _passenger_counts(passengers)
    cur = (currency or "USD").strip().upper() or "USD"

    stops = min(max(0, max_connections), 2)

    params: dict[str, str | int] = {
        "engine": "google_flights",
        "api_key": api_key,
        "departure_id": origin_iata,
        "arrival_id": destination_iata,
        "outbound_date": departure_date.isoformat(),
        "type": 2,
        "travel_class": _travel_class(cabin_n),
        "currency": cur,
        "adults": counts["adults"],
        "children": counts["children"],
        "infants_in_seat": counts["infants_in_seat"],
        "infants_on_lap": counts["infants_on_lap"],
        "stops": stops,
    }

    try:
        resp = httpx.get(SERPAPI_SEARCH_JSON, params=params, timeout=timeout_s)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — return surface errors
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
            itinerary_details=[],
        )

    err = data.get("error")
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
            error=str(err),
            itinerary_details=[],
        )

    priced = _collect_priced_offers(data, cur)
    if not priced:
        n_offers = sum(
            len(data.get(k) or [])
            for k in ("best_flights", "other_flights")
        )
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
            itinerary_details=[],
        )

    def sort_key(t: tuple[Decimal, str, dict[str, Any]]) -> tuple[Decimal, str]:
        return (t[0], t[1])

    best_amt, best_cur, best_entry = min(priced, key=sort_key)
    token = best_entry.get("departure_token") or best_entry.get("booking_token")
    if isinstance(token, str) and token.strip():
        offer_id = token.strip()
    else:
        offer_id = None

    n_offers = sum(len(data.get(k) or []) for k in ("best_flights", "other_flights"))
    legs = itinerary_details_from_flight_entry(best_entry)

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
        itinerary_details=legs,
    )
