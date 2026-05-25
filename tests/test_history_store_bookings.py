"""Tests for the bookings table — append-only, latest-per-leg fetch."""

from __future__ import annotations

from kelly.history_store import BookingRecord, SqliteHistoryStore


def _make(leg: str, total: float, ref: str | None = None) -> BookingRecord:
    return BookingRecord(
        trip_id="paris-disney-2026-08",
        leg=leg,
        provider="manual",
        confirmation_ref=ref,
        total_amount=total,
        currency="GBP",
        paid_at="2026-04-01",
        paid_by="me",
        raw_json=None,
    )


def test_booking_roundtrip(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    store = SqliteHistoryStore(db)
    rec = _make("airbnb", 1219.14, "HMESRSXD98")
    row_id = store.record_booking(rec)
    assert row_id > 0
    fetched = store.fetch_bookings("paris-disney-2026-08")
    assert len(fetched) == 1
    assert fetched[0].leg == "airbnb"
    assert fetched[0].total_amount == 1219.14
    assert fetched[0].confirmation_ref == "HMESRSXD98"
    assert fetched[0].currency == "GBP"


def test_booking_latest_per_leg_wins(tmp_path) -> None:
    """Re-logging the same leg returns the latest row only by default."""
    db = tmp_path / "t.sqlite"
    store = SqliteHistoryStore(db)
    store.record_booking(_make("airbnb", 999.00, "OLD-REF"))
    store.record_booking(_make("airbnb", 1219.14, "HMESRSXD98"))
    fetched = store.fetch_bookings("paris-disney-2026-08")
    assert len(fetched) == 1
    assert fetched[0].total_amount == 1219.14
    assert fetched[0].confirmation_ref == "HMESRSXD98"


def test_booking_all_versions_preserves_history(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    store = SqliteHistoryStore(db)
    store.record_booking(_make("airbnb", 999.00, "OLD-REF"))
    store.record_booking(_make("airbnb", 1219.14, "HMESRSXD98"))
    all_versions = store.fetch_bookings("paris-disney-2026-08", all_versions=True)
    assert len(all_versions) == 2
    amounts = [r.total_amount for r in all_versions]
    assert amounts == [999.00, 1219.14]


def test_booking_multiple_legs(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    store = SqliteHistoryStore(db)
    store.record_booking(_make("airbnb", 1219.14))
    store.record_booking(_make("eurostar_out", 460.00))
    store.record_booking(_make("eurostar_back", 480.00))
    fetched = store.fetch_bookings("paris-disney-2026-08")
    by_leg = {r.leg: r.total_amount for r in fetched}
    assert by_leg == {"airbnb": 1219.14, "eurostar_out": 460.00, "eurostar_back": 480.00}


def test_currency_is_uppercased_on_insert(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    store = SqliteHistoryStore(db)
    rec = BookingRecord(
        trip_id="trip1",
        leg="airbnb",
        provider="airbnb",
        confirmation_ref=None,
        total_amount=100.0,
        currency="eur",  # lowercase on input
        paid_at=None,
    )
    store.record_booking(rec)
    fetched = store.fetch_bookings("trip1")
    assert fetched[0].currency == "EUR"


def test_other_trips_are_isolated(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    store = SqliteHistoryStore(db)
    store.record_booking(_make("airbnb", 1219.14))
    # Different trip_id, same leg
    other = BookingRecord(
        trip_id="other-trip",
        leg="airbnb",
        provider="airbnb",
        confirmation_ref=None,
        total_amount=500.00,
        currency="GBP",
        paid_at=None,
    )
    store.record_booking(other)
    assert len(store.fetch_bookings("paris-disney-2026-08")) == 1
    assert len(store.fetch_bookings("other-trip")) == 1
    assert store.fetch_bookings("other-trip")[0].total_amount == 500.00
