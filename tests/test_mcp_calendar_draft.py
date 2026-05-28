"""Tests for kelly_booking_event_draft — generic spec + bookings-table lookup."""

from __future__ import annotations

import json

from kelly.mcp_server import kelly_booking_event_draft, kelly_log_booking


def _draft(**overrides):
    base = dict(
        trip_id="trip-x",
        leg="stay",
        summary="Stay",
        location="Somewhere",
        start_datetime="2030-01-01T16:00",
        end_datetime="2030-01-04T11:00",
        start_timezone="Europe/Paris",
    )
    base.update(overrides)
    return json.loads(kelly_booking_event_draft(**base))


def test_draft_returns_spec_with_appended_total_marker() -> None:
    data = _draft()
    assert data["summary"] == "Stay"
    assert data["location"] == "Somewhere"
    assert data["start"] == {"dateTime": "2030-01-01T16:00:00", "timeZone": "Europe/Paris"}
    assert data["end"] == {"dateTime": "2030-01-04T11:00:00", "timeZone": "Europe/Paris"}
    # With nothing logged in bookings, the description carries the placeholder.
    assert data["description"] == "Total paid: (not yet logged)"


def test_draft_uses_end_timezone_when_supplied() -> None:
    data = _draft(start_timezone="Europe/London", end_timezone="Europe/Paris")
    assert data["start"]["timeZone"] == "Europe/London"
    assert data["end"]["timeZone"] == "Europe/Paris"


def test_draft_falls_back_to_start_timezone_when_end_missing() -> None:
    data = _draft(start_timezone="Europe/London")
    assert data["end"]["timeZone"] == "Europe/London"


def test_draft_appends_total_to_existing_description() -> None:
    data = _draft(description="Some details here.")
    assert data["description"].startswith("Some details here.")
    assert data["description"].endswith("Total paid: (not yet logged)")


def test_draft_pulls_total_from_bookings_table() -> None:
    kelly_log_booking(
        trip_id="trip-y",
        leg="stay",
        provider="airbnb",
        total_amount="1234.56",
        currency="GBP",
    )
    data = _draft(trip_id="trip-y", leg="stay")
    assert "£1,234.56" in data["description"]


def test_draft_formats_currency_with_iso_fallback_for_unknown_symbol() -> None:
    kelly_log_booking(
        trip_id="trip-z",
        leg="stay",
        provider="hotel",
        total_amount="999.00",
        currency="CHF",
    )
    data = _draft(trip_id="trip-z", leg="stay")
    assert "999.00 CHF" in data["description"]
