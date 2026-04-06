from datetime import date

from kelly.history_store import SqliteHistoryStore, Observation, route_key


def test_sqlite_roundtrip(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    store = SqliteHistoryStore(db)
    rk = route_key("JFK", "LIS", "economy", date(2026, 7, 1))
    oid = store.append(
        Observation(
            route_key=rk,
            origin_iata="JFK",
            destination_iata="LIS",
            cabin="economy",
            departure_date="2026-07-01",
            days_before_departure=100,
            best_cash_amount=500.0,
            cash_currency="USD",
            best_award_miles=None,
            award_program=None,
            award_taxes_cents=None,
            source_cash="duffel",
            source_award=None,
            watchlist_row_id="t1",
            raw_json=None,
        )
    )
    assert oid > 0
    cash, miles = store.fetch_amounts_for_route(rk, window_days=365)
    assert cash == [500.0]
    assert miles == []
