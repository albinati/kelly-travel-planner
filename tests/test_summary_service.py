"""Tests for summary_service.summarize_trip — bookings + FX rollup."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from kelly.history_store import BookingRecord, SqliteHistoryStore
from kelly.mcp_server import kelly_trip_summary
from kelly.services.summary_service import _category_for_leg, summarize_trip


def _seed_bookings(store: SqliteHistoryStore) -> None:
    """The Paris-Disney trip as it actually looks today."""
    legs = [
        BookingRecord(
            trip_id="sample-trip",
            leg="airbnb",
            provider="airbnb",
            confirmation_ref="REDACTED_CONF",
            total_amount=1219.14,
            currency="GBP",
            paid_at="2026-04-15",
        ),
        BookingRecord(
            trip_id="sample-trip",
            leg="eurostar_out",
            provider="eurostar",
            confirmation_ref="REDACTED_REF",
            total_amount=460.00,
            currency="GBP",
            paid_at="2026-05-20",
        ),
        BookingRecord(
            trip_id="sample-trip",
            leg="eurostar_back",
            provider="eurostar",
            confirmation_ref="REDACTED_REF",
            total_amount=480.50,
            currency="GBP",
            paid_at="2026-05-20",
        ),
        BookingRecord(
            trip_id="sample-trip",
            leg="disney_tickets",
            provider="disneyland_paris",
            confirmation_ref=None,
            total_amount=420.00,
            currency="EUR",
            paid_at="2026-05-15",
        ),
    ]
    for rec in legs:
        store.record_booking(rec)


def test_category_classification() -> None:
    assert _category_for_leg("eurostar_out") == "trains"
    assert _category_for_leg("eurostar_back") == "trains"
    assert _category_for_leg("airbnb") == "stays"
    assert _category_for_leg("hotel_paris") == "stays"
    assert _category_for_leg("disney_tickets") == "tickets"
    assert _category_for_leg("tickets_louvre") == "tickets"
    assert _category_for_leg("rer_passes") == "other"


def test_summarize_trip_no_bookings_returns_zeros(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    summary = summarize_trip("empty-trip", currency="GBP", store=store)
    assert summary["currency"] == "GBP"
    assert summary["by_leg"] == []
    assert summary["totals"]["trip_total"] == "0.00"
    assert summary["warnings"] == []
    assert summary["booking_count"] == 0


def test_summarize_trip_single_currency_no_fx_needed(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    store.record_booking(
        BookingRecord(
            trip_id="t1",
            leg="airbnb",
            provider="airbnb",
            confirmation_ref="X",
            total_amount=1219.14,
            currency="GBP",
            paid_at="2026-04-15",
        )
    )
    summary = summarize_trip("t1", currency="GBP", store=store)
    assert summary["totals"]["trip_total"] == "1219.14"
    assert summary["by_category"] == {"stays": "1219.14"}
    assert len(summary["by_leg"]) == 1
    assert summary["by_leg"][0]["converted_amount"] == "1219.14"
    assert summary["by_leg"][0]["original_currency"] == "GBP"
    assert summary["warnings"] == []


def test_summarize_trip_mixed_currencies_with_fx(tmp_path) -> None:
    """Trains+stay in GBP, Disney in EUR; rollup in GBP needs one conversion."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    _seed_bookings(store)

    # Mock fx_service.convert: GBP→GBP identity, EUR→GBP at 0.85 (matches our
    # fixture rate).
    def fake_convert(amount, src, dst, *, as_of=None, store=None):
        amount = Decimal(str(amount))
        src, dst = src.upper(), dst.upper()
        if src == dst:
            return amount
        if src == "EUR" and dst == "GBP":
            return amount * Decimal("0.85")
        raise AssertionError(f"unexpected conversion {src}->{dst}")

    with patch("kelly.services.summary_service.convert", side_effect=fake_convert):
        with patch(
            "kelly.services.summary_service.fx_quote",
            return_value={"as_of": "2026-05-25", "rate": "0.85"},
        ):
            summary = summarize_trip("sample-trip", currency="GBP", store=store)

    # GBP legs untouched: 1219.14 + 460.00 + 480.50 = 2159.64
    # EUR Disney: 420.00 * 0.85 = 357.00
    # Total: 2516.64
    assert summary["totals"]["trip_total"] == "2516.64"
    assert summary["by_category"] == {
        "stays": "1219.14",
        "trains": "940.50",
        "tickets": "357.00",
    }
    assert summary["currency"] == "GBP"
    assert summary["fx_as_of"] == "2026-05-25"
    # Disney row carries both the EUR original and the GBP target conversion.
    disney_row = next(r for r in summary["by_leg"] if r["leg"] == "disney_tickets")
    assert disney_row["original_amount"] == "420.00"
    assert disney_row["original_currency"] == "EUR"
    assert disney_row["converted_amount"] == "357.00"
    assert disney_row["target_currency"] == "GBP"
    assert summary["warnings"] == []


def test_summarize_trip_fx_failure_becomes_warning(tmp_path) -> None:
    """A failed conversion warns + leaves converted_amount=None but doesn't crash."""
    from kelly.services.fx_service import FxError

    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    store.record_booking(
        BookingRecord(
            trip_id="t1",
            leg="airbnb",
            provider="airbnb",
            confirmation_ref="X",
            total_amount=100.0,
            currency="GBP",
            paid_at="2026-04-15",
        )
    )
    store.record_booking(
        BookingRecord(
            trip_id="t1",
            leg="disney_tickets",
            provider="disneyland_paris",
            confirmation_ref=None,
            total_amount=50.0,
            currency="EUR",
            paid_at="2026-04-15",
        )
    )

    def fake_convert(amount, src, dst, *, as_of=None, store=None):
        if src.upper() == dst.upper():
            return Decimal(str(amount))
        raise FxError("no rates cached and offline")

    with patch("kelly.services.summary_service.convert", side_effect=fake_convert):
        with patch(
            "kelly.services.summary_service.fx_quote",
            return_value={"error": "offline"},
        ):
            summary = summarize_trip("t1", currency="GBP", store=store)

    # GBP leg counted, EUR leg failed and went to warnings.
    assert summary["totals"]["trip_total"] == "100.00"
    assert len(summary["warnings"]) == 1
    assert "could not convert disney_tickets" in summary["warnings"][0]
    disney_row = next(r for r in summary["by_leg"] if r["leg"] == "disney_tickets")
    assert disney_row["converted_amount"] is None


def test_mcp_trip_summary_returns_json() -> None:
    """The MCP wrapper just JSON-dumps summarize_trip."""
    # Conftest already isolates KELLY_DATA_DIR; seed via the MCP wrapper to
    # mirror the runtime call shape, then summarize via the MCP wrapper too.
    from kelly.mcp_server import kelly_log_booking

    kelly_log_booking(
        trip_id="t1",
        leg="airbnb",
        provider="airbnb",
        total_amount="500",
        currency="GBP",
        confirmation_ref="X",
    )
    payload = json.loads(kelly_trip_summary("t1", currency="GBP"))
    assert payload["trip_id"] == "t1"
    assert payload["totals"]["trip_total"] == "500.00"
    assert payload["currency"] == "GBP"
