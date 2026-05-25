"""Tests for kelly_booking_event_draft — composition + bookings-table lookup."""

from __future__ import annotations

import json

from kelly.mcp_server import kelly_booking_event_draft, kelly_log_booking


def test_airbnb_draft_carries_confirmation_and_full_window() -> None:
    data = json.loads(kelly_booking_event_draft("airbnb"))
    assert "REDACTED_CONF" in data["summary"]
    assert data["location"] == "REDACTED_ADDRESS"
    assert "REDACTED_CONF" in data["description"]
    # Check-in after 16:00 on 2026-08-18, checkout by 11:00 on 2026-08-21.
    assert data["start"]["dateTime"] == "2026-08-18T16:00:00"
    assert data["end"]["dateTime"] == "2026-08-21T11:00:00"
    assert data["start"]["timeZone"] == "Europe/Paris"


def test_eurostar_out_default_times_with_correct_timezones() -> None:
    data = json.loads(kelly_booking_event_draft("eurostar_out"))
    # Default 12:01→15:30 on 18 Aug.
    assert data["start"] == {"dateTime": "2026-08-18T12:01:00", "timeZone": "Europe/London"}
    assert data["end"] == {"dateTime": "2026-08-18T15:30:00", "timeZone": "Europe/Paris"}
    assert "LON" in data["summary"] and "PAR" in data["summary"]


def test_eurostar_back_default_matches_booking() -> None:
    # Default reflects the actual booked train (ref REDACTED_REF): 20:02→21:30 on 21 Aug.
    data = json.loads(kelly_booking_event_draft("eurostar_back"))
    assert data["start"] == {"dateTime": "2026-08-21T20:02:00", "timeZone": "Europe/Paris"}
    assert data["end"] == {"dateTime": "2026-08-21T21:30:00", "timeZone": "Europe/London"}
    assert "20:02" in data["summary"] and "21:30" in data["summary"]


def test_eurostar_back_accepts_depart_arrive_override() -> None:
    data = json.loads(
        kelly_booking_event_draft("eurostar_back", depart_time="20:02", arrive_time="21:30")
    )
    # User wanted a later return → custom times reflected in summary + start/end.
    assert "20:02" in data["summary"] and "21:30" in data["summary"]
    assert data["start"] == {"dateTime": "2026-08-21T20:02:00", "timeZone": "Europe/Paris"}
    assert data["end"] == {"dateTime": "2026-08-21T21:30:00", "timeZone": "Europe/London"}


def test_disney_draft_uses_mid_trip_default() -> None:
    data = json.loads(kelly_booking_event_draft("disney"))
    assert data["start"]["dateTime"].startswith("2026-08-20T09:00")
    assert data["end"]["dateTime"].startswith("2026-08-20T22:00")
    assert "Marne-la-Vall" in data["location"]
    assert "RER A" in data["description"]


def test_disney_draft_accepts_date_override() -> None:
    data = json.loads(kelly_booking_event_draft("disney", disney_date="2026-08-19"))
    assert data["start"]["dateTime"].startswith("2026-08-19T")
    assert data["end"]["dateTime"].startswith("2026-08-19T")


def test_unknown_booking_returns_error() -> None:
    data = json.loads(kelly_booking_event_draft("space_x"))
    assert "unknown booking" in data["error"]


def test_airbnb_draft_shows_not_yet_logged_when_no_booking() -> None:
    """With an empty bookings table, the description marks the total as missing."""
    data = json.loads(kelly_booking_event_draft("airbnb"))
    assert "(not yet logged)" in data["description"]


def test_airbnb_draft_pulls_total_from_bookings_table() -> None:
    """After logging the Airbnb booking, the calendar draft surfaces the total."""
    kelly_log_booking(
        trip_id="sample-trip",
        leg="airbnb",
        provider="airbnb",
        total_amount="1219.14",
        currency="GBP",
        confirmation_ref="REDACTED_CONF",
    )
    data = json.loads(kelly_booking_event_draft("airbnb"))
    assert "£1,219.14" in data["description"]


def test_eurostar_back_draft_pulls_total_from_bookings_table() -> None:
    """Eurostar back total flows from the bookings table at draft-time."""
    kelly_log_booking(
        trip_id="sample-trip",
        leg="eurostar_back",
        provider="eurostar",
        total_amount="480.50",
        currency="GBP",
        confirmation_ref="REDACTED_REF",
    )
    data = json.loads(kelly_booking_event_draft("eurostar_back"))
    assert "£480.50" in data["description"]
