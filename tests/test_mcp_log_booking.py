"""Tests for kelly_log_booking — thin wrapper around booking_service."""

from __future__ import annotations

import json

from kelly.history_store import open_default_store
from kelly.mcp_server import kelly_log_booking


def test_log_booking_persists_and_returns_record() -> None:
    out = json.loads(
        kelly_log_booking(
            trip_id="sample-trip",
            leg="airbnb",
            provider="airbnb",
            total_amount="1219.14",
            currency="GBP",
            confirmation_ref="REDACTED_CONF",
            paid_at="2026-04-15",
            paid_by="me",
        )
    )
    assert out["leg"] == "airbnb"
    assert out["total_amount"] == 1219.14
    assert out["currency"] == "GBP"
    assert out["confirmation_ref"] == "REDACTED_CONF"
    assert out["id"] > 0

    store = open_default_store()
    rows = store.fetch_bookings("sample-trip")
    legs = {r.leg: r for r in rows}
    assert "airbnb" in legs
    assert legs["airbnb"].total_amount == 1219.14


def test_log_booking_accepts_decimal_precise_string() -> None:
    """Total is a string to preserve precision; verify it survives."""
    out = json.loads(
        kelly_log_booking(
            trip_id="trip-precision",
            leg="eurostar_out",
            provider="eurostar",
            total_amount="123.45",
            currency="GBP",
        )
    )
    assert out["total_amount"] == 123.45


def test_log_booking_uppercases_currency() -> None:
    out = json.loads(
        kelly_log_booking(
            trip_id="trip-ccy",
            leg="airbnb",
            provider="airbnb",
            total_amount="100",
            currency="eur",
        )
    )
    assert out["currency"] == "EUR"
