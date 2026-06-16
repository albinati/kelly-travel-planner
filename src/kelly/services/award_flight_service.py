"""Avios award-flight search orchestration.

Pipeline:
  1. Hit the seats.aero provider (``search_award``) for a route + date window.
  2. Filter to **BA-bookable** options: the availability comes from a oneworld
     partner program we can credit to BA (american/qantas/alaska) AND the
     operating-airline field for the requested cabin contains ``BA``.
  3. Price each surviving option with the static BA Reward Flight Saver chart
     (``ba_avios.avios_cost``) — the seats.aero partner mileage is *ignored* for
     pricing (it's a different program's currency).
  4. Optionally enrich the top options with real depart/arrive times via the
     provider ``trips()`` endpoint.
  5. Optionally persist each option to ``award_flight_observations``.

The Avios figure on every returned option comes **only** from the BA chart;
``partner_mileage_cost`` is retained purely for provenance and is never the
Avios price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from kelly.history_store import (
    AwardFlightObservation,
    SqliteHistoryStore,
)
from kelly.providers.seats_aero import (
    AwardOption,
    AwardTrip,
    search_award,
    trips,
)
from kelly.services import ba_avios

# Partner programs whose award space BA members can actually book via BA / Avios
# (oneworld + Avios-transfer partners). seats.aero ``Route.Source`` must be one
# of these for the option to be BA-bookable.
_BA_BOOKABLE_SOURCES = {"american", "qantas", "alaska"}
_BA_AIRLINE_CODE = "BA"


@dataclass
class AviosFlightOption:
    """One BA-operated award flight, priced in Avios via the BA RFS chart.

    ``partner_source`` / ``partner_mileage_cost`` are provenance only — the
    Avios price is ``avios_cost`` (from the BA chart), never the partner miles.
    """

    availability_id: str
    origin: str
    destination: str
    fly_date: date
    cabin: str
    operating_airlines: str
    seats: int
    distance_mi: int | None
    avios_cost: int | None
    avios_taxes_note: str | None
    partner_source: str
    partner_mileage_cost: int | None  # NOT Avios — provenance only
    # Best-effort enrichment from trips(); empty if not fetched or unavailable.
    departs_at: str | None = None
    arrives_at: str | None = None
    flight_numbers: str | None = None
    duration_min: int | None = None
    stops: int | None = None


@dataclass
class AwardFlightSearchResult:
    options: list[AviosFlightOption]
    error: str | None = None
    note: str | None = None


def _norm_cabin(cabin: str) -> str:
    return "business" if cabin.strip().lower() in {"business", "j", "club"} else "economy"


def _enrich_from_trip(opt: AviosFlightOption, trip: AwardTrip) -> None:
    opt.departs_at = trip.departs_at
    opt.arrives_at = trip.arrives_at
    opt.flight_numbers = trip.flight_numbers
    opt.duration_min = trip.total_duration_min
    opt.stops = trip.stops


def search_avios(
    origin_airports: list[str] | str,
    destination_airports: list[str] | str,
    date_window: tuple[date | str, date | str],
    pax: int,
    cabin: str = "economy",
    only_direct: bool = True,
    *,
    store: SqliteHistoryStore | None = None,
    persist: bool = True,
    enrich_top_n: int = 5,
) -> AwardFlightSearchResult:
    """Search BA-operated award availability + price it in Avios.

    *date_window* is ``(start, end)``. *pax* is informational here — we keep the
    raw remaining-seat count on each option so the caller can filter by party
    size; we never drop options for having "too few" seats (a partial party
    might still want to know).
    """
    cabin_n = _norm_cabin(cabin)
    start, end = date_window

    res = search_award(
        origin_airports,
        destination_airports,
        start,
        end,
        cabins="business" if cabin_n == "business" else "economy",
        only_direct=only_direct,
        sources=",".join(sorted(_BA_BOOKABLE_SOURCES)),
    )
    if res.error is not None:
        return AwardFlightSearchResult(options=[], error=res.error)

    ba_options: list[AviosFlightOption] = []
    for opt in res.options:
        if not _is_ba_bookable(opt, cabin_n):
            continue
        ba_options.append(_to_avios_option(opt, cabin_n))

    if not ba_options:
        return AwardFlightSearchResult(
            options=[],
            note="no BA-operated award availability matched in the window",
        )

    # Enrich the top-N (most seats first) with real flight times. Best-effort:
    # the provider returns [] on any error, so this never blocks the result.
    ba_options.sort(key=lambda o: o.seats, reverse=True)
    for opt in ba_options[: max(0, enrich_top_n)]:
        trip_legs = trips(opt.availability_id)
        ba_trip = _best_trip_for_cabin(trip_legs, cabin_n)
        if ba_trip is not None:
            _enrich_from_trip(opt, ba_trip)

    if store is not None and persist:
        for opt in ba_options:
            _persist(store, opt)

    return AwardFlightSearchResult(options=ba_options)


def _is_ba_bookable(opt: AwardOption, cabin: str) -> bool:
    if opt.source.strip().lower() not in _BA_BOOKABLE_SOURCES:
        return False
    if not opt.operated_by(_BA_AIRLINE_CODE, cabin):
        return False
    return opt.seats_for(cabin) > 0


def _to_avios_option(opt: AwardOption, cabin: str) -> AviosFlightOption:
    avios_cost: int | None = None
    taxes_note: str | None = None
    if opt.distance_mi is not None:
        avios_cost, taxes_note = ba_avios.avios_cost(opt.distance_mi, cabin, opt.fly_date)
    else:
        taxes_note = "distance unknown — cannot price Avios; confirm on BA.com"

    is_business = cabin == "business"
    return AviosFlightOption(
        availability_id=opt.availability_id,
        origin=opt.origin_airport,
        destination=opt.destination_airport,
        fly_date=opt.fly_date,
        cabin=cabin,
        operating_airlines=opt.airlines_for(cabin),
        seats=opt.seats_for(cabin),
        distance_mi=opt.distance_mi,
        avios_cost=avios_cost,
        avios_taxes_note=taxes_note,
        partner_source=opt.source,
        partner_mileage_cost=opt.j_mileage_cost if is_business else opt.y_mileage_cost,
    )


def _best_trip_for_cabin(legs: list[AwardTrip], cabin: str) -> AwardTrip | None:
    """Pick the trip leg matching the requested cabin, preferring fewest stops.
    Falls back to any leg if no cabin match (the field is informational)."""
    if not legs:
        return None
    wanted = "business" if cabin == "business" else "economy"

    def cabin_matches(t: AwardTrip) -> bool:
        c = (t.cabin or "").strip().lower()
        if wanted == "business":
            return c in {"business", "j", "club"}
        return c in {"economy", "y", "coach"}

    matches = [t for t in legs if cabin_matches(t)] or legs
    return min(matches, key=lambda t: (t.stops if t.stops is not None else 99))


def _persist(store: SqliteHistoryStore, opt: AviosFlightOption) -> None:
    store.append_award_flight(
        AwardFlightObservation(
            origin=opt.origin,
            destination=opt.destination,
            fly_date=opt.fly_date.isoformat(),
            cabin=opt.cabin,
            operating_airlines=opt.operating_airlines,
            seats=opt.seats,
            partner_source=opt.partner_source,
            partner_mileage_cost=opt.partner_mileage_cost,
            avios_cost=opt.avios_cost,
            availability_id=opt.availability_id,
            distance_mi=opt.distance_mi,
        )
    )


def award_flight_result_to_jsonable(res: AwardFlightSearchResult) -> dict[str, Any]:
    return {
        "error": res.error,
        "note": res.note,
        "options": [
            {
                "availability_id": o.availability_id,
                "origin": o.origin,
                "destination": o.destination,
                "fly_date": o.fly_date.isoformat(),
                "cabin": o.cabin,
                "operating_airlines": o.operating_airlines,
                "seats": o.seats,
                "distance_mi": o.distance_mi,
                "avios_cost": o.avios_cost,
                "avios_taxes_note": o.avios_taxes_note,
                "partner_source": o.partner_source,
                "partner_mileage_cost": o.partner_mileage_cost,
                "partner_mileage_note": "partner program miles — NOT the Avios price",
                "departs_at": o.departs_at,
                "arrives_at": o.arrives_at,
                "flight_numbers": o.flight_numbers,
                "duration_min": o.duration_min,
                "stops": o.stops,
            }
            for o in res.options
        ],
    }
