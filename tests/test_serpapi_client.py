from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from kelly.serpapi_client import CashSearchResult, search_cash_best


@patch("kelly.serpapi_client.httpx.get")
def test_search_cash_best_picks_cheapest(mock_get: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "best_flights": [
            {"price": 600, "currency": "USD", "departure_token": "t600"},
        ],
        "other_flights": [
            {
                "price": "$450",
                "currency": "USD",
                "booking_token": "t450",
                "flights": [
                    {
                        "airline": "Budget Air",
                        "departure_time": "8:00 AM",
                        "arrival_time": "10:00 AM",
                        "duration": 120,
                    },
                    {
                        "airline": "Budget Hop",
                        "departure_time": "1:00 PM",
                        "arrival_time": "3:30 PM",
                        "duration": 150,
                    },
                ],
                "layovers": [{"duration": "3 hr"}],
            },
            {"price": 500, "currency": "EUR"},
        ],
    }
    mock_get.return_value = mock_resp

    res = search_cash_best(
        "key",
        origin_iata="jfk",
        destination_iata="lis",
        departure_date=date(2026, 7, 1),
        cabin="economy",
        passengers=[{"type": "adult"}],
        currency="USD",
    )

    assert res.error is None
    assert res.best_total_amount == Decimal("450")
    assert res.best_total_currency == "USD"
    assert res.best_offer_id == "t450"
    assert res.offer_count == 3
    assert len(res.itinerary_details) == 2
    assert res.itinerary_details[0]["airline"] == "Budget Air"
    assert res.itinerary_details[0]["stops"] == 1
    assert res.itinerary_details[0]["layover_after"] == "3 hr"
    assert res.itinerary_details[1]["layover_after"] is None
    call_kwargs = mock_get.call_args
    assert call_kwargs[0][0] == "https://serpapi.com/search.json"
    params = call_kwargs[1]["params"]
    assert params["departure_id"] == "JFK"
    assert params["arrival_id"] == "LIS"
    assert params["adults"] == 1


@patch("kelly.serpapi_client.httpx.get")
def test_search_cash_best_api_error(mock_get: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"error": "Invalid API key"}
    mock_get.return_value = mock_resp

    res = search_cash_best(
        "bad",
        origin_iata="JFK",
        destination_iata="LIS",
        departure_date=date(2026, 7, 1),
        cabin="business",
        passengers=[{"type": "adult"}, {"type": "child"}],
    )
    assert isinstance(res, CashSearchResult)
    assert res.best_total_amount is None
    assert res.error == "Invalid API key"
    params = mock_get.call_args[1]["params"]
    assert params["travel_class"] == 3
    assert params["children"] == 1
