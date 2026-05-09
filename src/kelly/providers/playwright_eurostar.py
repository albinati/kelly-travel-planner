"""Eurostar fare scraping via patchright (stealth Playwright fork).

Free, no API key, no Apify. Patchright launches a headless Chromium that gets
past Eurostar's DataDome on the search page; the journey results render with
``data-testid`` attributes that we extract.

Fare bands honoured by the URL params (Eurostar's own definitions):
    * infant  : 0–3   (lap-held, free, no seat) → ``infant``
    * child   : 4–11                              → ``child``
    * youth   : 12–25 (discounted)                → ``youth``
    * adult   : 26–59                             → ``adult``
    * senior  : 60+                               → ``senior``

The provider returns per-adult Lowest-fare prices per departure (Eurostar's UI
prices that way); the *total* trip cost depends on the fare class chosen, and
the browser flow does not include the per-pax breakdown without a click into
each card. Multiplying lowest-fare-per-adult × passenger count is a reasonable
ceiling estimate for planning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

# Station codes harvested from eurostar.com autocomplete (2026-05).
# Add more as needed; unknowns will be reported in the result error field.
_STATION_CODES: dict[str, str] = {
    "LON": "7015400",   # London St Pancras International
    "STP": "7015400",
    "PAR": "8727100",   # Paris Gare du Nord
    "PNO": "8727100",
    "MLV": "8711184",   # Disneyland Paris (Marne-la-Vallée Chessy)
    "DIS": "8711184",
    "BRU": "8814001",   # Brussels-Midi (best-effort; verify via autocomplete if needed)
    "AMS": "8400058",   # Amsterdam Centraal (best-effort)
    "LIL": "8722326",   # Lille Europe (best-effort)
}

_PRICE_RE = re.compile(r"([£€$])\s?(\d+(?:[.,]\d+)?)")
_DURATION_RE = re.compile(
    r"(\d+)\s*hours?\s*(?:(\d+)\s*minutes?)?", re.IGNORECASE
)
_CHANGES_RE = re.compile(r"(\d+|direct)\s*change", re.IGNORECASE)
_CLASS_RE = re.compile(r"Eurostar\s+(Standard|Plus|Premier)", re.IGNORECASE)


@dataclass
class EurostarJourney:
    depart_time: str | None
    arrive_time: str | None
    duration_min: int | None
    changes: int | None
    total_price: Decimal | None  # per-adult lowest-fare from listing
    price_currency: str | None
    class_: str | None  # Standard | Plus | Premier
    refundable: bool | None
    operator: str | None
    deep_link: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EurostarSearchResult:
    journeys: list[EurostarJourney]
    error: str | None = None
    note: str | None = None


def _resolve_station(code: str) -> str | None:
    """Map a kelly.md city code (e.g. ``LON``) to Eurostar station id, or
    return *code* unchanged if it already looks like a numeric station id."""
    s = code.strip().upper()
    if s.isdigit():
        return s
    return _STATION_CODES.get(s)


def classify_passengers(
    adults: int, seniors: int, teens: int, children_ages: list[int]
) -> dict[str, int]:
    """Map kelly.md TrainRow fields to Eurostar URL params.

    *teens* (kelly.md's column for 12–17) maps to Eurostar's ``youth`` band.
    """
    infants = sum(1 for a in children_ages if a < 4)
    child = sum(1 for a in children_ages if 4 <= a <= 11)
    youth_from_kids = sum(1 for a in children_ages if 12 <= a <= 25)
    adult_from_kids = sum(1 for a in children_ages if a >= 26)
    return {
        "adult": adults + adult_from_kids,
        "senior": seniors,
        "youth": teens + youth_from_kids,
        "child": child,
        "infant": infants,
    }


def _build_url(
    origin_id: str, destination_id: str, depart: date, pax: dict[str, int]
) -> str:
    parts = [
        f"adult={pax['adult']}",
        f"senior={pax['senior']}",
        f"youth={pax['youth']}",
        f"child={pax['child']}",
        f"infant={pax['infant']}",
        f"origin={origin_id}",
        f"destination={destination_id}",
        f"outbound={depart.isoformat()}",
    ]
    return "https://www.eurostar.com/search/uk-en?" + "&".join(parts)


def _to_decimal(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _parse_journey_text(text: str) -> dict[str, Any]:
    """Extract fields from the aggregate text of one ``search-results-outbound-item``.

    Format (observed 2026-05):
        "Journey details Departs: 07:04 Arrives: 12:09 Journey duration:
         4 hr hours 5 min minutes 1 change £95.26 per adult Lowest fare …"
    """
    out: dict[str, Any] = {
        "depart_time": None,
        "arrive_time": None,
        "duration_min": None,
        "changes": None,
        "total_price": None,
        "price_currency": None,
        "class_": None,
    }
    full_times = re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", text)
    if len(full_times) >= 2:
        out["depart_time"] = full_times[0]
        out["arrive_time"] = full_times[1]
    elif len(full_times) == 1:
        out["depart_time"] = full_times[0]

    m = _DURATION_RE.search(text)
    if m:
        h = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else 0
        out["duration_min"] = h * 60 + mins

    cm = _CHANGES_RE.search(text)
    if cm:
        token = cm.group(1).lower()
        out["changes"] = 0 if token == "direct" else int(token)
    elif "direct" in text.lower():
        out["changes"] = 0

    pm = _PRICE_RE.search(text)
    if pm:
        out["price_currency"] = pm.group(1)
        out["total_price"] = _to_decimal(pm.group(2))

    cl = _CLASS_RE.search(text)
    if cl:
        out["class_"] = cl.group(1).lower()
    elif "standard" in text.lower():
        out["class_"] = "standard"
    return out


def search_eurostar(
    *,
    origin_city: str,
    destination_city: str,
    date_start: date,
    date_end: date,
    adults: int,
    seniors: int = 0,
    teens: int = 0,
    children_ages: list[int] | None = None,
    class_: str = "standard",
    currency: str = "GBP",  # informational; UI returns per-region currency
    headless: bool = True,
    nav_timeout_ms: int = 45_000,
    results_timeout_ms: int = 30_000,
    max_days: int = 7,
) -> EurostarSearchResult:
    """Scrape Eurostar fares for each date in [date_start, date_end]."""
    try:
        from patchright.sync_api import sync_playwright
    except ImportError as e:
        return EurostarSearchResult(
            [], error=f"patchright not installed: {e}"
        )

    origin_id = _resolve_station(origin_city)
    dest_id = _resolve_station(destination_city)
    if not origin_id:
        return EurostarSearchResult(
            [], error=f"unknown origin station code {origin_city!r} (add to _STATION_CODES)"
        )
    if not dest_id:
        return EurostarSearchResult(
            [], error=f"unknown destination station code {destination_city!r} (add to _STATION_CODES)"
        )

    pax = classify_passengers(adults, seniors, teens, list(children_ages or []))
    pax_total = sum(pax.values())

    # Walk each date in window; cap so we don't spam the site.
    days: list[date] = []
    cur = date_start
    while cur <= date_end:
        days.append(cur)
        cur = date.fromordinal(cur.toordinal() + 1)
    if len(days) > max_days:
        days = days[:max_days]

    journeys: list[EurostarJourney] = []
    notes: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 900},
                locale="en-GB",
            )
            page = ctx.new_page()
            for day in days:
                url = _build_url(origin_id, dest_id, day, pax)
                try:
                    page.goto(url, timeout=nav_timeout_ms, wait_until="domcontentloaded")
                except Exception as e:  # noqa: BLE001
                    notes.append(f"{day.isoformat()}: navigation failed ({e})")
                    continue
                try:
                    page.wait_for_selector(
                        "[data-testid=search-results-outbound-item]",
                        timeout=results_timeout_ms,
                    )
                except Exception:
                    notes.append(f"{day.isoformat()}: no journey results rendered")
                    continue
                page.wait_for_timeout(1500)
                items = page.evaluate(
                    """() => {
                      const cards = [...document.querySelectorAll(
                        '[data-testid=search-results-outbound-item]'
                      )];
                      return cards.map(c => {
                        const tabs = [...c.querySelectorAll(
                          '[data-testid=search-results-outbound-tab]'
                        )];
                        const detail = c.querySelector(
                          '[data-testid=search-results-outbound-journey-details]'
                        );
                        return {
                          detail_text: detail ? detail.innerText : '',
                          fares: tabs.map(t => (t.innerText || '').replace(/\\s+/g,' ').trim()),
                        };
                      });
                    }"""
                )
                for card in items:
                    detail_text = card.get("detail_text") or ""
                    parsed_detail = _parse_journey_text(detail_text)
                    fares = card.get("fares") or []
                    if not fares:
                        # No fare tabs — record the journey without a price
                        journeys.append(
                            EurostarJourney(
                                depart_time=parsed_detail["depart_time"],
                                arrive_time=parsed_detail["arrive_time"],
                                duration_min=parsed_detail["duration_min"],
                                changes=parsed_detail["changes"],
                                total_price=None,
                                price_currency=None,
                                class_=None,
                                refundable=None,
                                operator="eurostar",
                                deep_link=url,
                                raw={"date": day.isoformat(), "detail": detail_text},
                            )
                        )
                        continue
                    for fare_text in fares:
                        parsed_fare = _parse_journey_text(fare_text)
                        journeys.append(
                            EurostarJourney(
                                depart_time=parsed_detail["depart_time"]
                                or parsed_fare["depart_time"],
                                arrive_time=parsed_detail["arrive_time"],
                                duration_min=parsed_detail["duration_min"]
                                or parsed_fare["duration_min"],
                                changes=parsed_detail["changes"]
                                if parsed_detail["changes"] is not None
                                else parsed_fare["changes"],
                                total_price=parsed_fare["total_price"],
                                price_currency=parsed_fare["price_currency"],
                                class_=parsed_fare["class_"],
                                refundable=None,
                                operator="eurostar",
                                deep_link=url,
                                raw={
                                    "date": day.isoformat(),
                                    "detail": detail_text,
                                    "fare": fare_text,
                                    "pax_total": pax_total,
                                },
                            )
                        )
        finally:
            browser.close()

    note = " | ".join(notes) if notes else None
    if pax_total >= 10:
        warn = (
            "Party of 10+: Eurostar Groups Desk usually handles bookings of "
            "this size; live web quotes may not be bookable in one transaction."
        )
        note = f"{note} | {warn}" if note else warn

    return EurostarSearchResult(journeys=journeys, error=None, note=note)
