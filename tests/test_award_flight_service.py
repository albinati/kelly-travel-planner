from datetime import date
from unittest.mock import patch

from kelly.history_store import SqliteHistoryStore
from kelly.providers.seats_aero import AwardOption, AwardSearchResult, AwardTrip
from kelly.services.award_flight_service import (
    award_flight_result_to_jsonable,
    search_avios,
)

# A canned fixture resembling a real seats.aero /search response, already parsed
# into AwardOptions. Two routes to CTA in July 2026:
#   - LHR->CTA operated by BA, 9 economy seats  → must SURVIVE the BA filter.
#   - LGW->CTA operated by AZ (ITA Airways), 4 seats → must be FILTERED OUT.
_CTA_BA = AwardOption(
    availability_id="avail-ba-cta",
    origin_airport="LHR",
    destination_airport="CTA",
    fly_date=date(2026, 7, 16),  # a Thursday in peak July
    source="american",  # BA-bookable partner program
    distance_mi=1300,  # Zone 3
    y_seats=9,
    y_airlines="BA",
    y_mileage_cost=23000,  # AAdvantage miles — NOT Avios
    j_seats=2,
    j_airlines="BA",
    j_mileage_cost=57500,
    raw={"ID": "avail-ba-cta"},
)

_CTA_AZ = AwardOption(
    availability_id="avail-az-cta",
    origin_airport="LGW",
    destination_airport="CTA",
    fly_date=date(2026, 7, 16),
    source="qantas",
    distance_mi=1290,
    y_seats=4,
    y_airlines="AZ, EN",  # ITA + Air Dolomiti — not BA
    y_mileage_cost=20000,
    j_seats=0,
    j_airlines="",
    j_mileage_cost=None,
    raw={"ID": "avail-az-cta"},
)

_FIXTURE = AwardSearchResult(options=[_CTA_BA, _CTA_AZ])

_TRIP = AwardTrip(
    cabin="economy",
    flight_numbers="BA2616",
    departs_at="2026-07-16T08:25:00",
    arrives_at="2026-07-16T13:05:00",
    total_duration_min=220,
    stops=0,
    remaining_seats=9,
    raw={},
)


@patch("kelly.services.award_flight_service.trips", return_value=[_TRIP])
@patch("kelly.services.award_flight_service.search_award", return_value=_FIXTURE)
def test_ba_filter_keeps_only_ba_operated(mock_search, mock_trips):
    res = search_avios(
        ["LHR", "LGW", "LCY"],
        ["CTA"],
        (date(2026, 7, 1), date(2026, 7, 31)),
        pax=2,
        persist=False,
    )
    assert res.error is None
    assert len(res.options) == 1
    opt = res.options[0]
    assert opt.origin == "LHR"
    assert opt.operating_airlines == "BA"
    assert opt.seats == 9
    # The AZ option must have been dropped.
    assert all(o.partner_source == "american" for o in res.options)


@patch("kelly.services.award_flight_service.trips", return_value=[_TRIP])
@patch("kelly.services.award_flight_service.search_award", return_value=_FIXTURE)
def test_avios_cost_from_ba_chart_not_partner_mileage(mock_search, mock_trips):
    res = search_avios(
        ["LHR"],
        ["CTA"],
        (date(2026, 7, 1), date(2026, 7, 31)),
        pax=2,
        persist=False,
    )
    opt = res.options[0]
    # Zone 3 (1151-2000mi), peak July 2026 → 7750 Avios from the BA chart.
    assert opt.avios_cost == 7750
    # Must NOT be the partner program's mileage cost.
    assert opt.avios_cost != opt.partner_mileage_cost
    assert opt.partner_mileage_cost == 23000
    assert "BA.com" in (opt.avios_taxes_note or "")


@patch("kelly.services.award_flight_service.trips", return_value=[_TRIP])
@patch("kelly.services.award_flight_service.search_award", return_value=_FIXTURE)
def test_trip_enrichment_attaches_times(mock_search, mock_trips):
    res = search_avios(
        ["LHR"],
        ["CTA"],
        (date(2026, 7, 1), date(2026, 7, 31)),
        pax=2,
        persist=False,
    )
    opt = res.options[0]
    assert opt.departs_at == "2026-07-16T08:25:00"
    assert opt.arrives_at == "2026-07-16T13:05:00"
    assert opt.flight_numbers == "BA2616"
    assert opt.stops == 0


@patch("kelly.services.award_flight_service.trips", return_value=[_TRIP])
@patch("kelly.services.award_flight_service.search_award", return_value=_FIXTURE)
def test_jsonable_shape(mock_search, mock_trips):
    res = search_avios(
        ["LHR"],
        ["CTA"],
        (date(2026, 7, 1), date(2026, 7, 31)),
        pax=2,
        persist=False,
    )
    payload = award_flight_result_to_jsonable(res)
    assert payload["error"] is None
    assert len(payload["options"]) == 1
    o = payload["options"][0]
    assert o["avios_cost"] == 7750
    assert o["partner_mileage_cost"] == 23000
    assert "NOT the Avios price" in o["partner_mileage_note"]
    assert o["fly_date"] == "2026-07-16"
    assert o["seats"] == 9


@patch("kelly.services.award_flight_service.trips", return_value=[_TRIP])
@patch("kelly.services.award_flight_service.search_award", return_value=_FIXTURE)
def test_persist_writes_observation(mock_search, mock_trips, tmp_path):
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    res = search_avios(
        ["LHR"],
        ["CTA"],
        (date(2026, 7, 1), date(2026, 7, 31)),
        pax=2,
        store=store,
        persist=True,
    )
    assert len(res.options) == 1
    import sqlite3

    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT origin, destination, seats, avios_cost, partner_mileage_cost, "
            "partner_source FROM award_flight_observations"
        ).fetchall()
    assert len(rows) == 1
    origin, dest, seats, avios, partner_miles, source = rows[0]
    assert origin == "LHR"
    assert dest == "CTA"
    assert seats == 9
    assert avios == 7750
    assert partner_miles == 23000
    assert source == "american"


@patch(
    "kelly.services.award_flight_service.search_award",
    return_value=AwardSearchResult(options=[], error="SEATSAERO_API_KEY not set"),
)
def test_error_propagates_cleanly(mock_search):
    res = search_avios(
        ["LHR"], ["CTA"], (date(2026, 7, 1), date(2026, 7, 31)), pax=2, persist=False
    )
    assert res.options == []
    assert "SEATSAERO_API_KEY" in (res.error or "")
