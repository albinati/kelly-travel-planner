"""Google Flights–style cash search via RapidAPI (google-flights-data host)."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from kelly import settings
from kelly.cash_types import CashSearchResult
from kelly.http.rapidapi_base import RapidAPIClient


@dataclass
class PriceGraphDay:
    date: date
    indicative_price: Decimal | None
    currency: str | None = None


@dataclass
class PriceGraphResult:
    origin_iata: str
    destination_iata: str
    window_start: date
    window_end: date
    cabin: str
    days: list[PriceGraphDay]
    error: str | None = None
    http_requests_used: int = 0


def _normalize_cabin(cabin: str) -> str:
    return cabin.strip().lower().replace(" ", "_")


def _cabin_class(cabin: str) -> int:
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
    if value is None or isinstance(value, bool):
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
    for key in ("error", "detail", "message"):
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


def _parse_flexible_date(v: object) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _merge_calendar_into(
    acc: dict[date, PriceGraphDay],
    payload: dict[str, Any],
    *,
    default_currency: str,
) -> None:
    """Best-effort extraction of (date, price) pairs from arbitrary provider JSON."""

    def consider(d: date, price: Decimal | None, cur: str | None) -> None:
        if price is None:
            return
        cur_u = (cur or default_currency).upper() if cur else None
        prev = acc.get(d)
        if prev is None or (
            prev.indicative_price is not None and price < prev.indicative_price
        ) or prev.indicative_price is None:
            acc[d] = PriceGraphDay(date=d, indicative_price=price, currency=cur_u)

    def walk(o: object) -> None:
        if isinstance(o, dict):
            dl: date | None = None
            for dk in ("date", "departureDate", "day", "formattedDate", "departure_date"):
                if dk in o:
                    dl = _parse_flexible_date(o.get(dk))
                    if dl:
                        break
            pl: Decimal | None = None
            cur_hint: str | None = None
            for pk in ("price", "cheapest", "lowestPrice", "minPrice", "amount", "value"):
                if pk in o:
                    pl = _parse_price(o.get(pk))
                    if pl is not None:
                        break
            for ck in ("currency", "priceCurrency"):
                if isinstance(o.get(ck), str) and o[ck].strip():
                    cur_hint = o[ck].strip()
                    break
            if dl and pl is not None:
                consider(dl, pl, cur_hint)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(payload)


def _months_in_window(start: date, end: date) -> list[tuple[int, int]]:
    if end < start:
        return []
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while True:
        out.append((y, m))
        if y == end.year and m == end.month:
            break
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    return out


def fetch_price_graph(
    api_key: str,
    *,
    origin_iata: str,
    destination_iata: str,
    window_start: date,
    window_end: date,
    cabin: str = "economy",
    currency: str = "USD",
    client: RapidAPIClient | None = None,
) -> PriceGraphResult:
    """One HTTP request per calendar month touched by [window_start, window_end]."""
    origin_iata = origin_iata.upper().strip()
    destination_iata = destination_iata.upper().strip()
    cabin_n = _normalize_cabin(cabin)
    cur = (currency or "USD").strip().upper() or "USD"
    c = client or RapidAPIClient(
        host=settings.rapidapi_google_flights_host(),
        base_url=settings.rapidapi_google_flights_base_url(),
        api_key=api_key,
    )
    path = settings.rapidapi_google_flights_calendar_path()
    acc: dict[date, PriceGraphDay] = {}
    requests = 0
    last_err: str | None = None

    for y, m in _months_in_window(window_start, window_end):
        params: dict[str, Any] = {
            "departureId": origin_iata,
            "arrivalId": destination_iata,
            "year": y,
            "month": m,
            "currency": cur,
            "cabinClass": _cabin_class(cabin_n),
        }
        try:
            payload = c.get(path, params=params)
            requests += 1
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            break
        err = _response_error(payload)
        if err:
            last_err = err
            break
        root = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(root, dict):
            root = payload
        _merge_calendar_into(acc, root, default_currency=cur)

    days = [acc[d] for d in sorted(acc.keys()) if window_start <= d <= window_end]
    if not days and last_err:
        return PriceGraphResult(
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            window_start=window_start,
            window_end=window_end,
            cabin=cabin_n,
            days=[],
            error=last_err,
            http_requests_used=requests,
        )
    if not days and not last_err:
        return PriceGraphResult(
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            window_start=window_start,
            window_end=window_end,
            cabin=cabin_n,
            days=[],
            error=(
                "No calendar prices parsed — check RAPIDAPI_GOOGLE_FLIGHTS_CALENDAR_PATH "
                "and response shape, or use search-oneway for a single date."
            ),
            http_requests_used=requests,
        )
    return PriceGraphResult(
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        window_start=window_start,
        window_end=window_end,
        cabin=cabin_n,
        days=days,
        error=None,
        http_requests_used=requests,
    )


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
    client: RapidAPIClient | None = None,
) -> CashSearchResult:
    del max_connections
    origin_iata = origin_iata.upper().strip()
    destination_iata = destination_iata.upper().strip()
    cabin_n = _normalize_cabin(cabin)
    counts = _passenger_counts(passengers)
    cur = (currency or "USD").strip().upper() or "USD"

    c = client or RapidAPIClient(
        host=settings.rapidapi_google_flights_host(),
        base_url=settings.rapidapi_google_flights_base_url(),
        api_key=api_key,
        timeout_s=timeout_s,
    )

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

    try:
        payload = c.get(path, params=params)
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


def month_overview(
    api_key: str,
    *,
    origin_iata: str,
    destination_iata: str,
    year_month: str,
    cabin: str = "economy",
    currency: str = "USD",
    client: RapidAPIClient | None = None,
) -> dict[str, Any]:
    """Cheapest day and histogram buckets for a calendar month (uses price graph)."""
    y_str, m_str = year_month.split("-", 1)
    y, m = int(y_str), int(m_str)
    start = date(y, m, 1)
    end = date(y, m, calendar.monthrange(y, m)[1])
    g = fetch_price_graph(
        api_key,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        window_start=start,
        window_end=end,
        cabin=cabin,
        currency=currency,
        client=client,
    )
    prices = [float(d.indicative_price) for d in g.days if d.indicative_price is not None]
    cheapest = min(g.days, key=lambda d: (d.indicative_price or Decimal("999999999"), d.date)) if g.days else None
    return {
        "year_month": year_month,
        "origin_iata": g.origin_iata,
        "destination_iata": g.destination_iata,
        "error": g.error,
        "http_requests_used": g.http_requests_used,
        "day_count": len(g.days),
        "cheapest_date": cheapest.date.isoformat() if cheapest else None,
        "cheapest_price": str(cheapest.indicative_price) if cheapest and cheapest.indicative_price else None,
        "cheapest_currency": cheapest.currency if cheapest else None,
        "min_price": min(prices) if prices else None,
        "max_price": max(prices) if prices else None,
        "median_price": sorted(prices)[len(prices) // 2] if prices else None,
    }
