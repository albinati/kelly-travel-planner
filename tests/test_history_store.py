from datetime import date

from kelly.history_store import (
    Observation,
    SqliteHistoryStore,
    StayObservation,
    TrainObservation,
    route_key,
    stay_key,
    train_key,
)


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
            source_cash="serpapi",
            source_award=None,
            watchlist_row_id="t1",
            raw_json=None,
        )
    )
    assert oid > 0
    cash, miles = store.fetch_amounts_for_route(rk, window_days=365)
    assert cash == [500.0]
    assert miles == []


def test_train_observation_roundtrip(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    store = SqliteHistoryStore(db)
    tk = train_key("eurostar", "LON", "PAR", "standard", date(2026, 8, 20))
    a = TrainObservation(
        trip_key=tk,
        trip_row_id="trip1-out",
        operator="eurostar",
        origin_city="LON",
        destination_city="PAR",
        class_="standard",
        departure_date="2026-08-20",
        days_before_departure=100,
        best_per_adult_amount=145.0,
        currency="GBP",
        journey_count=4,
        pax_adult_eq=8,
        pax_child=2,
        pax_infant=0,
        raw_json=None,
    )
    b = TrainObservation(
        trip_key=tk,
        trip_row_id="trip1-out",
        operator="eurostar",
        origin_city="LON",
        destination_city="PAR",
        class_="standard",
        departure_date="2026-08-20",
        days_before_departure=99,
        best_per_adult_amount=130.0,
        currency="GBP",
        journey_count=5,
        pax_adult_eq=8,
        pax_child=2,
        pax_infant=0,
        raw_json=None,
    )
    assert store.append_train(a) > 0
    assert store.append_train(b) > 0
    amounts = store.fetch_train_amounts(tk, window_days=365)
    assert amounts == [145.0, 130.0]


def test_stay_observation_roundtrip(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    store = SqliteHistoryStore(db)
    sk = stay_key("Paris", date(2026, 8, 20), date(2026, 8, 24))
    obs = StayObservation(
        trip_key=sk,
        trip_row_id="trip1",
        area="Paris",
        check_in="2026-08-20",
        check_out="2026-08-24",
        nights=4,
        days_before_check_in=100,
        best_total_amount=1850.5,
        currency="EUR",
        listings_count=12,
        bedrooms_min=5,
        pax_adult_eq=7,
        pax_child=3,
        pax_infant=0,
        raw_json=None,
    )
    assert store.append_stay(obs) > 0
    amounts = store.fetch_stay_amounts(sk, window_days=365)
    assert amounts == [1850.5]


def test_train_and_stay_amounts_isolated(tmp_path) -> None:
    """Trains and stays share a DB but separate tables — they must not mix."""
    db = tmp_path / "t.sqlite"
    store = SqliteHistoryStore(db)
    tk = train_key("eurostar", "LON", "PAR", "standard", date(2026, 8, 20))
    sk = stay_key("Paris", date(2026, 8, 20), date(2026, 8, 24))
    store.append_train(
        TrainObservation(
            trip_key=tk,
            trip_row_id=None,
            operator="eurostar",
            origin_city="LON",
            destination_city="PAR",
            class_="standard",
            departure_date="2026-08-20",
            days_before_departure=10,
            best_per_adult_amount=200.0,
            currency="GBP",
            journey_count=1,
            pax_adult_eq=2,
            pax_child=0,
            pax_infant=0,
        )
    )
    assert store.fetch_stay_amounts(sk) == []
    assert store.fetch_train_amounts(tk) == [200.0]
