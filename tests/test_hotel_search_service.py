from unittest.mock import patch

from kelly.history_store import SqliteHistoryStore
from kelly.providers.serpapi_hotels import HotelOption, HotelSearchResult
from kelly.services.hotel_search_service import (
    hotel_search_result_to_jsonable,
    search_hotels,
)

# A canned fixture resembling parsed SerpApi google_hotels properties: two
# Copenhagen hotels, one cheaper total than the other (so we can assert sort).
_CPH_PRICEY = HotelOption(
    name="Hotel d'Angleterre",
    rate_per_night=420.0,
    total_rate=1260.0,
    currency="GBP",
    hotel_class=5.0,
    overall_rating=4.7,
    gps={"latitude": 55.68, "longitude": 12.58},
    amenities=["Spa", "Bar"],
    prices=[{"source": "Expedia", "rate_per_night": {"extracted_lowest": 420.0}}],
    link="https://example.com/angleterre",
    raw={},
)

_CPH_CHEAP = HotelOption(
    name="Wakeup Copenhagen",
    rate_per_night=95.0,
    total_rate=285.0,
    currency="GBP",
    hotel_class=3.0,
    overall_rating=4.2,
    gps={"latitude": 55.66, "longitude": 12.57},
    amenities=["Free Wi-Fi"],
    prices=[{"source": "Booking.com", "rate_per_night": {"extracted_lowest": 95.0}}],
    link="https://example.com/wakeup",
    raw={},
)

# Provider returns pricey-first; the service must sort cheapest-first.
_FIXTURE = HotelSearchResult(options=[_CPH_PRICEY, _CPH_CHEAP])


@patch("kelly.services.hotel_search_service.provider_search", return_value=_FIXTURE)
def test_forward_and_sorts_cheapest_first(mock_search):
    res = search_hotels("Copenhagen", "2026-10-24", "2026-10-27", adults=2, persist=False)
    assert res.error is None
    assert len(res.options) == 2
    # Cheapest total first.
    assert res.options[0].total_rate == 285.0
    assert res.options[0].name == "Wakeup Copenhagen"
    assert res.options[1].total_rate == 1260.0
    mock_search.assert_called_once()


@patch("kelly.services.hotel_search_service.provider_search", return_value=_FIXTURE)
def test_jsonable_shape(mock_search):
    res = search_hotels("Copenhagen", "2026-10-24", "2026-10-27", persist=False)
    payload = hotel_search_result_to_jsonable(res)
    assert payload["error"] is None
    assert len(payload["options"]) == 2
    o = payload["options"][0]
    assert o["name"] == "Wakeup Copenhagen"
    assert o["total_rate"] == "285.0"
    assert o["rate_per_night"] == "95.0"
    assert o["currency"] == "GBP"
    assert o["hotel_class"] == 3.0


@patch("kelly.services.hotel_search_service.provider_search", return_value=_FIXTURE)
def test_persist_writes_observation(mock_search, tmp_path):
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    res = search_hotels(
        "Copenhagen",
        "2026-10-24",
        "2026-10-27",
        adults=2,
        children=2,
        store=store,
        persist=True,
    )
    assert len(res.options) == 2
    import sqlite3

    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            "SELECT area, check_in, check_out, nights, best_total_amount, currency, "
            "listings_count, min_stars, pax_adult_eq, pax_child FROM hotel_observations"
        ).fetchall()
    # One observation row per search (cheapest total + min stars across the set).
    assert len(rows) == 1
    area, ci, co, nights, best_total, currency, listings, min_stars, adults, children = rows[0]
    assert area == "Copenhagen"
    assert ci == "2026-10-24"
    assert co == "2026-10-27"
    assert nights == 3
    assert best_total == 285.0
    assert currency == "GBP"
    assert listings == 2
    assert min_stars == 3.0
    assert adults == 2
    assert children == 2


@patch(
    "kelly.services.hotel_search_service.provider_search",
    return_value=HotelSearchResult(options=[], error="SERPAPI_API_KEY not set"),
)
def test_error_propagates_cleanly(mock_search):
    res = search_hotels("Copenhagen", "2026-10-24", "2026-10-27", persist=False)
    assert res.options == []
    assert "SERPAPI_API_KEY" in (res.error or "")
