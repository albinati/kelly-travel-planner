"""Tests for kelly_booking_event_draft — pure data composition, no I/O."""

from __future__ import annotations

import json

from kelly.mcp_server import kelly_booking_event_draft


def test_airbnb_draft_carries_confirmation_and_full_window() -> None:
    data = json.loads(kelly_booking_event_draft("airbnb"))
    assert "HMESRSXD98" in data["summary"]
    assert data["location"] == "218 Rue St Denis, 75002 Paris, France"
    assert "HMESRSXD98" in data["description"]
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
    # Default reflects the actual booked train (ref TGDJKR): 20:02→21:30 on 21 Aug.
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
