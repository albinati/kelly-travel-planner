from decimal import Decimal
from unittest.mock import patch

from kelly.history_store import SqliteHistoryStore
from kelly.providers.serpapi_flights import CashFlightOption, CashFlightSearchResult
from kelly.services.cash_flight_service import (
    cash_flight_result_to_jsonable,
    search_cash_flights,
)

# A canned fixture resembling a parsed SerpApi google_flights response: two
# LHR->CPH options, one cheaper than the other (so we can assert sort order).
_CPH_CHEAP = CashFlightOption(
    id="LHR-CPH-2026-10-24-0",
    origin="LHR",
    destination="CPH",
    departure_date="2026-10-24",
    departure_time="2026-10-24 09:15",
    arrival_time="2026-10-24 12:05",
    airline="SAS",
    price_amount=Decimal("142"),
    price_currency="GBP",
    cabin="economy",
    stops=0,
    duration_min=110,
    raw={},
)

_CPH_PRICEY = CashFlightOption(
    id="LHR-CPH-2026-10-24-1",
    origin="LHR",
    destination="CPH",
    departure_date="2026-10-24",
    departure_time="2026-10-24 18:40",
    arrival_time="2026-10-24 23:55",
    airline="BA",
    price_amount=Decimal("268"),
    price_currency="GBP",
    cabin="economy",
    stops=1,
    duration_min=195,
    raw={},
)

# Provider returns pricey-first; the service must sort cheapest-first.
_FIXTURE = CashFlightSearchResult(options=[_CPH_PRICEY, _CPH_CHEAP])


@patch("kelly.services.cash_flight_service.provider_search", return_value=_FIXTURE)
def test_forward_and_sorts_cheapest_first(mock_search):
    res = search_cash_flights("LHR", "CPH", "2026-10-24", adults=2, persist=False)
    assert res.error is None
    assert len(res.options) == 2
    # Cheapest first.
    assert res.options[0].price_amount == Decimal("142")
    assert res.options[0].airline == "SAS"
    assert res.options[1].price_amount == Decimal("268")
    # The provider was called once per origin/dest pair.
    mock_search.assert_called_once()


@patch("kelly.services.cash_flight_service.provider_search", return_value=_FIXTURE)
def test_jsonable_shape(mock_search):
    res = search_cash_flights("LHR", "CPH", "2026-10-24", persist=False)
    payload = cash_flight_result_to_jsonable(res)
    assert payload["error"] is None
    assert len(payload["options"]) == 2
    o = payload["options"][0]
    assert o["price_amount"] == "142"
    assert o["price_currency"] == "GBP"
    assert o["origin"] == "LHR"
    assert o["destination"] == "CPH"
    assert o["stops"] == 0


@patch("kelly.services.cash_flight_service.provider_search", return_value=_FIXTURE)
def test_persist_writes_observation(mock_search, tmp_path):
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    res = search_cash_flights("LHR", "CPH", "2026-10-24", store=store, persist=True)
    assert len(res.options) == 2
    import sqlite3

    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT origin, destination, airline, price_amount, price_currency, "
            "cabin, stops, duration_min FROM cash_flight_observations "
            "ORDER BY price_amount ASC"
        ).fetchall()
    assert len(rows) == 2
    origin, dest, airline, price, currency, cabin, stops, dur = rows[0]
    assert origin == "LHR"
    assert dest == "CPH"
    assert airline == "SAS"
    assert price == 142.0
    assert currency == "GBP"
    assert cabin == "economy"
    assert stops == 0
    assert dur == 110


@patch("kelly.services.cash_flight_service.provider_search")
def test_iterates_every_pair(mock_search):
    mock_search.return_value = CashFlightSearchResult(options=[_CPH_CHEAP])
    search_cash_flights("LHR,LGW", "CPH", "2026-10-24", persist=False)
    # 2 origins x 1 dest = 2 provider calls.
    assert mock_search.call_count == 2


@patch(
    "kelly.services.cash_flight_service.provider_search",
    return_value=CashFlightSearchResult(options=[], error="SERPAPI_API_KEY not set"),
)
def test_error_propagates_cleanly(mock_search):
    res = search_cash_flights("LHR", "CPH", "2026-10-24", persist=False)
    assert res.options == []
    assert "SERPAPI_API_KEY" in (res.error or "")
