"""Tests for the E3 normalizers and the ``kelly_solve_itinerary`` MCP tool.

Provider calls are mocked at the service boundary (``search_avios``) — never
live. We assert: the Eurostar HH:MM + date normalizer, the Avios normalizer,
and that the tool returns ranked itineraries that include the open-jaw pairing.
"""

import json
from datetime import date, datetime
from unittest.mock import patch

from kelly.providers.playwright_eurostar import EurostarJourney, EurostarSearchResult
from kelly.services.award_flight_service import AviosFlightOption, AwardFlightSearchResult
from kelly.services.transport_service import (
    avios_options_from_service,
    eurostar_options_from_result,
)


# --- normalizer: Eurostar HH:MM + date → datetime ---------------------------


def test_eurostar_normalizer_combines_hhmm_with_date():
    j = EurostarJourney(
        depart_time="07:04",
        arrive_time="12:09",
        duration_min=305,
        changes=1,
        total_price=None,
        price_currency="£",
        class_="standard",
        refundable=None,
        operator="eurostar",
        deep_link="https://eurostar.example",
        raw={"date": "2026-07-17"},
    )
    from decimal import Decimal

    j.total_price = Decimal("95.26")
    res = EurostarSearchResult(journeys=[j])
    opts = eurostar_options_from_result(res, date(2026, 7, 17))
    assert len(opts) == 1
    opt = opts[0]
    assert opt.mode == "train"
    assert opt.depart_dt == datetime(2026, 7, 17, 7, 4)
    assert opt.arrive_dt == datetime(2026, 7, 17, 12, 9)
    assert opt.price_cash == Decimal("95.26")
    assert opt.currency == "£"
    assert opt.price_avios is None
    assert opt.seats_available is None  # unknown for Eurostar
    assert opt.changes == 1


def test_eurostar_normalizer_handles_missing_time():
    j = EurostarJourney(
        depart_time=None,
        arrive_time=None,
        duration_min=None,
        changes=None,
        total_price=None,
        price_currency=None,
        class_=None,
        refundable=None,
        operator="eurostar",
        deep_link=None,
        raw={},
    )
    opts = eurostar_options_from_result(EurostarSearchResult(journeys=[j]), date(2026, 7, 17))
    assert opts[0].depart_dt is None
    assert opts[0].changes == 0  # None coerced to 0


# --- normalizer: Avios service result → TransportOption ---------------------


def _avios_opt(origin, dest, fly_date, avios, seats, departs_at=None, arrives_at=None):
    return AviosFlightOption(
        availability_id=f"{origin}{dest}",
        origin=origin,
        destination=dest,
        fly_date=fly_date,
        cabin="economy",
        operating_airlines="BA",
        seats=seats,
        distance_mi=1300,
        avios_cost=avios,
        avios_taxes_note="approx; confirm on BA.com",
        partner_source="american",
        partner_mileage_cost=23000,
        departs_at=departs_at,
        arrives_at=arrives_at,
        flight_numbers="BA2616",
        duration_min=220,
        stops=0,
    )


def test_avios_normalizer_maps_fields():
    res = AwardFlightSearchResult(
        options=[
            _avios_opt(
                "LHR",
                "CTA",
                date(2026, 7, 16),
                7750,
                9,
                departs_at="2026-07-16T08:25:00",
                arrives_at="2026-07-16T13:05:00",
            )
        ]
    )
    opts = avios_options_from_service(res)
    assert len(opts) == 1
    o = opts[0]
    assert o.mode == "flight"
    assert o.price_avios == 7750
    assert o.price_cash is None
    assert o.seats_available == 9
    assert o.depart_dt == datetime(2026, 7, 16, 8, 25)
    assert o.arrive_dt == datetime(2026, 7, 16, 13, 5)
    assert o.duration_min == 220
    assert o.source == "american"


def test_avios_normalizer_falls_back_to_fly_date_midnight():
    res = AwardFlightSearchResult(
        options=[_avios_opt("LHR", "CTA", date(2026, 7, 16), 7750, 9)]  # no times
    )
    opts = avios_options_from_service(res)
    assert opts[0].depart_dt == datetime(2026, 7, 16, 0, 0)


# --- the MCP tool with a mocked search_avios fixture ------------------------


def test_solve_itinerary_tool_returns_ranked_open_jaw():
    # OUT pool: Bari (Thu) + Catania (Thu). RETURN pool: Brindisi (Sun) + Catania (Sun).
    # Mocked search_avios returns OUT then RETURN on successive calls.
    out_fixture = AwardFlightSearchResult(
        options=[
            _avios_opt(
                "LGW",
                "BRI",
                date(2026, 7, 16),
                6500,
                4,  # Bari, Thu, cheaper
                departs_at="2026-07-16T08:00:00",
                arrives_at="2026-07-16T11:00:00",
            ),
            _avios_opt(
                "LGW",
                "CTA",
                date(2026, 7, 16),
                7750,
                4,  # Catania, Thu
                departs_at="2026-07-16T09:00:00",
                arrives_at="2026-07-16T12:00:00",
            ),
        ]
    )
    return_fixture = AwardFlightSearchResult(
        options=[
            _avios_opt(
                "BDS",
                "LGW",
                date(2026, 7, 19),
                6500,
                4,  # Brindisi, Sun
                departs_at="2026-07-19T18:00:00",
                arrives_at="2026-07-19T21:00:00",
            ),
            _avios_opt(
                "CTA",
                "LGW",
                date(2026, 7, 19),
                7750,
                4,  # Catania, Sun
                departs_at="2026-07-19T19:00:00",
                arrives_at="2026-07-19T22:00:00",
            ),
        ]
    )

    with patch("kelly.mcp_server.search_avios", side_effect=[out_fixture, return_fixture]):
        from kelly.mcp_server import kelly_solve_itinerary

        raw = kelly_solve_itinerary(
            origin_airports="LGW",
            destination_airports="BRI,CTA,BDS",
            date_start="2026-07-15",
            date_end="2026-07-20",
            pax=2,
            persist=False,
        )
    payload = json.loads(raw)
    assert "error" not in payload
    its = payload["itineraries"]
    assert payload["count"] >= 1
    # The cheapest is the Bari-in / Brindisi-out open-jaw at 13000 Avios.
    best = its[0]
    assert best["total_avios"] == 13000
    assert best["is_open_jaw"] is True
    assert best["out"]["destination"] == "BRI"
    assert best["back"]["origin"] == "BDS"
    # All itineraries are nights in [2,3] and Thu→Sun.
    for it in its:
        assert 2 <= it["nights"] <= 3


def test_solve_itinerary_tool_propagates_key_error():
    err = AwardFlightSearchResult(options=[], error="SEATSAERO_API_KEY not set")
    with patch("kelly.mcp_server.search_avios", return_value=err):
        from kelly.mcp_server import kelly_solve_itinerary

        raw = kelly_solve_itinerary(
            origin_airports="LGW",
            destination_airports="CTA",
            date_start="2026-07-15",
            date_end="2026-07-20",
            pax=2,
            persist=False,
        )
    assert json.loads(raw)["error"] == "SEATSAERO_API_KEY not set"
