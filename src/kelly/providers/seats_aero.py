"""Award-flight availability via seats.aero — REST, no SDK dep.

seats.aero indexes *oneworld / partner award space* across loyalty programs
(American AAdvantage, Qantas, Alaska, …) and surfaces, per route+date, how many
saver award seats remain in each cabin and which airlines operate them. Kelly
uses it purely as an **availability radar**: we want to know *which BA-operated
flights actually have award seats on a given date*, then price those seats with
the static BA Reward Flight Saver chart (`services/ba_avios.py`).

IMPORTANT — the ``*MileageCostRaw`` fields in the response are the *partner
program's* miles (e.g. AAdvantage miles needed to book the same seat through
American). That is **not** the Avios price and must never be surfaced as such.
The only source of Avios for Kelly is the BA RFS table.

Authentication is a single header ``Partner-Authorization: <key>``. We deliberately
roll a thin httpx wrapper (the REST surface is tiny) and lazy-read the key + base
URL from the environment so the module imports cleanly with no key present.

Pacing: rapid bulk loops against the partner API returned HTTP 403 during manual
testing, so we back off exponentially on 403/429 and pace bulk ``trips()`` loops
at roughly 1 request/second.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

_DEFAULT_BASE_URL = "https://seats.aero/partnerapi"
_MAX_RETRIES = 3
_BACKOFF_STATUSES = {403, 429}


@dataclass
class AwardOption:
    """One seats.aero availability object, normalised for the BA-Avios radar.

    Each option corresponds to one ``data[]`` entry: a single route + date with
    the remaining-seat / operating-airline / partner-mileage breakdown for the
    economy (``y_*``) and business (``j_*``) cabins.

    ``y_mileage_cost`` / ``j_mileage_cost`` are the *partner program's* miles —
    kept only for provenance/debugging. They are NOT Avios; never display them
    as the Avios price.
    """

    availability_id: str
    origin_airport: str
    destination_airport: str
    fly_date: date
    source: str  # partner program: "american" | "qantas" | "alaska" | ...
    distance_mi: int | None
    # Economy
    y_seats: int
    y_airlines: str  # CSV of operating-airline codes, e.g. "BA" or "AZ, EN, LH"
    y_mileage_cost: int | None  # PARTNER miles, NOT Avios
    # Business
    j_seats: int
    j_airlines: str
    j_mileage_cost: int | None  # PARTNER miles, NOT Avios
    raw: dict[str, Any] = field(default_factory=dict)

    def airlines_for(self, cabin: str) -> str:
        return self.j_airlines if _is_business(cabin) else self.y_airlines

    def seats_for(self, cabin: str) -> int:
        return self.j_seats if _is_business(cabin) else self.y_seats

    def operated_by(self, airline_code: str, cabin: str) -> bool:
        """True if *airline_code* (e.g. "BA") is among the operating airlines
        for the requested cabin. Token match on the CSV, case-insensitive."""
        tokens = {t.strip().upper() for t in self.airlines_for(cabin).split(",") if t.strip()}
        return airline_code.strip().upper() in tokens


@dataclass
class AwardTrip:
    """One bookable itinerary leg from the ``/trips/{id}`` endpoint — used to
    attach real depart/arrive times + flight numbers to a top availability."""

    cabin: str | None
    flight_numbers: str | None
    departs_at: str | None  # ISO 8601 as returned
    arrives_at: str | None
    total_duration_min: int | None
    stops: int | None
    remaining_seats: int | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AwardSearchResult:
    options: list[AwardOption]
    error: str | None = None
    note: str | None = None


def _is_business(cabin: str) -> bool:
    return cabin.strip().lower() in {"business", "j", "club", "club_europe", "club europe"}


def _to_int(v: object) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _client(api_key: str, base_url: str, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers={
            "Partner-Authorization": api_key,
            "accept": "application/json",
        },
        timeout=timeout,
    )


def _get_with_backoff(
    client: httpx.Client,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    max_retries: int = _MAX_RETRIES,
) -> httpx.Response:
    """GET with exponential backoff on 403/429 (1s, 2s, 4s). Raises for other
    HTTP errors and re-raises the last 403/429 if all retries are exhausted."""
    delay = 1.0
    last_exc: httpx.HTTPStatusError | None = None
    for attempt in range(max_retries + 1):
        r = client.get(path, params=params)
        if r.status_code in _BACKOFF_STATUSES:
            last_exc = httpx.HTTPStatusError(
                f"rate-limited (HTTP {r.status_code})", request=r.request, response=r
            )
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise last_exc
        r.raise_for_status()
        return r
    # Unreachable, but keeps type-checkers happy.
    assert last_exc is not None
    raise last_exc


def _parse_option(entry: dict[str, Any]) -> AwardOption | None:
    avail_id = entry.get("ID")
    if not avail_id:
        return None
    route = entry.get("Route") or {}
    date_str = entry.get("Date")
    try:
        fly_date = date.fromisoformat(str(date_str)) if date_str else None
    except ValueError:
        fly_date = None
    if fly_date is None:
        return None
    return AwardOption(
        availability_id=str(avail_id),
        origin_airport=str(route.get("OriginAirport") or ""),
        destination_airport=str(route.get("DestinationAirport") or ""),
        fly_date=fly_date,
        source=str(route.get("Source") or entry.get("Source") or ""),
        distance_mi=_to_int(route.get("Distance")),
        y_seats=_to_int(entry.get("YDirectRemainingSeatsRaw")) or 0,
        y_airlines=str(entry.get("YDirectAirlines") or ""),
        y_mileage_cost=_to_int(entry.get("YDirectMileageCostRaw")),
        j_seats=_to_int(entry.get("JDirectRemainingSeatsRaw")) or 0,
        j_airlines=str(entry.get("JDirectAirlines") or ""),
        j_mileage_cost=_to_int(entry.get("JDirectMileageCostRaw")),
        raw=entry,
    )


def _parse_trip(entry: dict[str, Any]) -> AwardTrip:
    return AwardTrip(
        cabin=entry.get("Cabin"),
        flight_numbers=entry.get("FlightNumbers"),
        departs_at=entry.get("DepartsAt"),
        arrives_at=entry.get("ArrivesAt"),
        total_duration_min=_to_int(entry.get("TotalDuration")),
        stops=_to_int(entry.get("Stops")),
        remaining_seats=_to_int(entry.get("RemainingSeats")),
        raw=entry,
    )


def search_award(
    origin_airports: list[str] | str,
    destination_airports: list[str] | str,
    start_date: date | str,
    end_date: date | str,
    cabins: str = "economy,business",
    only_direct: bool = True,
    sources: str = "american,qantas,alaska",
    take: int = 1000,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> AwardSearchResult:
    """Search seats.aero partner award space for a route + date window.

    *origin_airports* / *destination_airports* accept either a list of IATA
    codes or a pre-joined comma string. Returns one :class:`AwardOption` per
    route+date in the response ``data[]``. The result carries a clean ``error``
    string (never raises to the caller) when the key is missing or the API errors.
    """
    api_key = api_key or os.environ.get("SEATSAERO_API_KEY")
    if not api_key:
        return AwardSearchResult(
            options=[],
            error="SEATSAERO_API_KEY not set — add it to .env to enable award-flight search",
        )
    base_url = base_url or os.environ.get("SEATSAERO_BASE_URL", _DEFAULT_BASE_URL)

    origin = _join_codes(origin_airports)
    destination = _join_codes(destination_airports)
    params: dict[str, Any] = {
        "origin_airport": origin,
        "destination_airport": destination,
        "start_date": _as_date_str(start_date),
        "end_date": _as_date_str(end_date),
        "cabins": cabins,
        "only_direct_flights": "true" if only_direct else "false",
        "sources": sources,
        "take": take,
    }

    try:
        with _client(api_key, base_url) as client:
            r = _get_with_backoff(client, "/search", params=params)
            data = list(r.json().get("data") or [])
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else "?"
        body = e.response.text[:300] if e.response is not None else ""
        return AwardSearchResult(options=[], error=f"seats.aero HTTP {status}: {body}")
    except httpx.HTTPError as e:
        return AwardSearchResult(options=[], error=f"{type(e).__name__}: {e}")

    options = [opt for entry in data if (opt := _parse_option(entry)) is not None]
    return AwardSearchResult(options=options)


def trips(
    availability_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> list[AwardTrip]:
    """Fetch bookable itineraries for one availability id.

    Returns ``[]`` (rather than raising) on missing key or HTTP error — callers
    treat trip enrichment as best-effort. Paces itself via the shared backoff
    helper so a bulk loop over many ids degrades gracefully under 403/429.
    """
    api_key = api_key or os.environ.get("SEATSAERO_API_KEY")
    if not api_key:
        return []
    base_url = base_url or os.environ.get("SEATSAERO_BASE_URL", _DEFAULT_BASE_URL)
    try:
        with _client(api_key, base_url) as client:
            r = _get_with_backoff(client, f"/trips/{availability_id}")
            data = list(r.json().get("data") or [])
    except httpx.HTTPError:
        return []
    return [_parse_trip(entry) for entry in data]


def _join_codes(codes: list[str] | str) -> str:
    if isinstance(codes, str):
        parts = [c.strip().upper() for c in codes.split(",") if c.strip()]
    else:
        parts = [c.strip().upper() for c in codes if c and c.strip()]
    return ",".join(parts)


def _as_date_str(d: date | str) -> str:
    if isinstance(d, date):
        return d.isoformat()
    return str(d)
