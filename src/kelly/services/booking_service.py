"""Thin wrapper over the `bookings` SQLite table.

Validates inputs (currency uppercased, amount → float), surfaces typed
helpers other services use (`booking_total_for_leg`, etc).
"""

from __future__ import annotations

from decimal import Decimal

from kelly.history_store import (
    BookingRecord,
    SqliteHistoryStore,
    open_default_store,
)


def log_booking(
    *,
    trip_id: str,
    leg: str,
    provider: str,
    total_amount: Decimal | float | str,
    currency: str,
    confirmation_ref: str | None = None,
    paid_at: str | None = None,
    paid_by: str = "me",
    raw_json: str | None = None,
    store: SqliteHistoryStore | None = None,
) -> tuple[int, BookingRecord]:
    """Persist a booking row. Returns (row_id, record_as_stored)."""
    amount = float(Decimal(str(total_amount)))
    rec = BookingRecord(
        trip_id=trip_id,
        leg=leg,
        provider=provider,
        confirmation_ref=confirmation_ref,
        total_amount=amount,
        currency=currency.upper(),
        paid_at=paid_at,
        paid_by=paid_by,
        raw_json=raw_json,
    )
    s = store or open_default_store()
    row_id = s.record_booking(rec)
    return row_id, rec


def bookings_for_trip(
    trip_id: str, *, store: SqliteHistoryStore | None = None
) -> list[BookingRecord]:
    s = store or open_default_store()
    return s.fetch_bookings(trip_id)


def booking_total_for_leg(
    trip_id: str, leg: str, *, store: SqliteHistoryStore | None = None
) -> tuple[float, str] | None:
    """Return (amount, currency) for the latest booking on this leg, or None."""
    for b in bookings_for_trip(trip_id, store=store):
        if b.leg == leg:
            return b.total_amount, b.currency
    return None
