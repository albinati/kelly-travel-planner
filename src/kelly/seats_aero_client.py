"""Seats.aero Partner API (award availability)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

PARTNER_BASE = "https://seats.aero/partnerapi"


@dataclass
class AwardSearchResult:
    origin_airport: str
    destination_airport: str
    start_date: date
    end_date: date
    cabin: str
    raw_data: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    best_miles: int | None = None
    best_source: str | None = None
    best_date: str | None = None


def _cabin_param(cabin: str) -> str:
    c = cabin.strip().lower().replace(" ", "_")
    m = {
        "economy": "economy",
        "premium_economy": "premium",
        "business": "business",
        "first": "first",
    }
    return m.get(c, "economy")


def _miles_field_for_cabin(cabin_param: str) -> str:
    return {
        "economy": "YMileageCost",
        "premium": "WMileageCost",
        "business": "JMileageCost",
        "first": "FMileageCost",
    }.get(cabin_param, "YMileageCost")


def _available_field_for_cabin(cabin_param: str) -> str:
    return {
        "economy": "YAvailable",
        "premium": "WAvailable",
        "business": "JAvailable",
        "first": "FAvailable",
    }.get(cabin_param, "YAvailable")


def search_cached(
    api_key: str,
    *,
    origin_airport: str,
    destination_airport: str,
    start_date: date,
    end_date: date,
    cabin: str = "economy",
    sources: str | None = None,
    take: int = 100,
    timeout: float = 60.0,
) -> AwardSearchResult:
    """GET /partnerapi/search — cached award availability."""
    cabin_p = _cabin_param(cabin)
    params: dict[str, str | int] = {
        "origin_airport": origin_airport.upper().strip(),
        "destination_airport": destination_airport.upper().strip(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "cabins": cabin_p,
        "take": take,
    }
    if sources:
        params["sources"] = sources

    headers = {"Partner-Authorization": api_key}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{PARTNER_BASE}/search", params=params, headers=headers)
            r.raise_for_status()
            body = r.json()
    except Exception as e:  # noqa: BLE001
        return AwardSearchResult(
            origin_airport=str(params["origin_airport"]),
            destination_airport=str(params["destination_airport"]),
            start_date=start_date,
            end_date=end_date,
            cabin=cabin_p,
            error=str(e),
        )

    rows = body.get("data") or body if isinstance(body, list) else []
    if not isinstance(rows, list):
        rows = []

    miles_key = _miles_field_for_cabin(
        "premium" if params.get("cabins") == "premium" else cabin_p
    )
    avail_key = _available_field_for_cabin(
        "premium" if params.get("cabins") == "premium" else cabin_p
    )

    best_miles: int | None = None
    best_source: str | None = None
    best_date: str | None = None
    for item in rows:
        if not isinstance(item, dict):
            continue
        if not item.get(avail_key):
            continue
        raw_m = item.get(miles_key)
        if raw_m is None or raw_m == "":
            continue
        try:
            mval = int(str(raw_m).replace(",", ""))
        except ValueError:
            continue
        if best_miles is None or mval < best_miles:
            best_miles = mval
            best_source = str(item.get("Source", "") or "")
            best_date = str(item.get("Date", "") or "")

    return AwardSearchResult(
        origin_airport=str(params["origin_airport"]),
        destination_airport=str(params["destination_airport"]),
        start_date=start_date,
        end_date=end_date,
        cabin=str(params.get("cabins", cabin_p)),
        raw_data=rows[:50],
        best_miles=best_miles,
        best_source=best_source,
        best_date=best_date,
        error=None,
    )


def fetch_trips(api_key: str, availability_id: str, timeout: float = 60.0) -> dict[str, Any]:
    """GET /partnerapi/trips/{id}"""
    headers = {"Partner-Authorization": api_key}
    with httpx.Client(timeout=timeout) as client:
        r = client.get(f"{PARTNER_BASE}/trips/{availability_id}", headers=headers)
        r.raise_for_status()
        return r.json()
