from datetime import date
from decimal import Decimal
from unittest.mock import patch

from kelly.md_config import KellyConfig, KellyFrontmatter, StayRow
from kelly.providers.airbnb import AirbnbListing, AirbnbSearchResult
from kelly.services.stay_service import search_stay, stay_result_to_jsonable


def _cfg() -> KellyConfig:
    return KellyConfig(
        frontmatter=KellyFrontmatter(currency="EUR"),
        passengers=[],
        planned=[],
    )


def _row() -> StayRow:
    return StayRow(
        id="trip1",
        area="Paris",
        check_in=date(2026, 8, 20),
        check_out=date(2026, 8, 25),
        adults=6,
        children_ages=[3, 4, 5, 15],
        bedrooms_min=5,
        near=["Châtelet-Les Halles"],
        max_total=4500,
    )


@patch("kelly.services.stay_service.search_airbnb")
def test_search_stay_forwards_all_constraints(mock_search) -> None:
    mock_search.return_value = AirbnbSearchResult(listings=[], error=None)
    search_stay(_cfg(), _row())
    kwargs = mock_search.call_args.kwargs
    assert kwargs["location"] == "Paris"
    assert kwargs["adults"] == 6
    assert kwargs["children_ages"] == [3, 4, 5, 15]
    assert kwargs["bedrooms_min"] == 5
    assert kwargs["max_total"] == 4500
    assert kwargs["currency"] == "EUR"


@patch("kelly.services.stay_service.search_airbnb")
def test_search_stay_persists_cheapest_listing(mock_search, tmp_path) -> None:
    from kelly.history_store import SqliteHistoryStore, stay_key

    listings = [
        AirbnbListing(
            id="cheap", url=None, title="cheap one",
            price_total=Decimal("1800.00"), price_currency="EUR",
            bedrooms=5, beds=7, max_guests=10, rating=4.5,
            neighborhood="Marais", lat=48.86, lng=2.35,
        ),
        AirbnbListing(
            id="pricey", url=None, title="pricey",
            price_total=Decimal("3200.00"), price_currency="EUR",
            bedrooms=5, beds=7, max_guests=10, rating=4.9,
            neighborhood="Marais", lat=48.86, lng=2.35,
        ),
    ]
    mock_search.return_value = AirbnbSearchResult(listings=listings, error=None)
    store = SqliteHistoryStore(tmp_path / "s.sqlite")
    search_stay(_cfg(), _row(), store=store, persist=True)

    sk = stay_key("Paris", date(2026, 8, 20), date(2026, 8, 25))
    assert store.fetch_stay_amounts(sk) == [1800.0]


@patch("kelly.services.stay_service.search_airbnb")
def test_search_stay_skips_persist_on_error(mock_search, tmp_path) -> None:
    from kelly.history_store import SqliteHistoryStore, stay_key

    mock_search.return_value = AirbnbSearchResult(listings=[], error="geocode failed")
    store = SqliteHistoryStore(tmp_path / "s.sqlite")
    search_stay(_cfg(), _row(), store=store, persist=True)
    assert store.fetch_stay_amounts(stay_key("Paris", date(2026, 8, 20), date(2026, 8, 25))) == []


def test_stay_result_to_jsonable_serializes_decimal() -> None:
    res = AirbnbSearchResult(
        listings=[
            AirbnbListing(
                id="abc123",
                url="https://airbnb.com/rooms/abc123",
                title="Huge Parisian Loft",
                price_total=Decimal("4200.00"),
                price_currency="EUR",
                bedrooms=5,
                beds=7,
                max_guests=10,
                rating=4.9,
                neighborhood="Marais",
                lat=48.858,
                lng=2.351,
            )
        ]
    )
    out = stay_result_to_jsonable(res)
    assert out["listings"][0]["price_total"] == "4200.00"
    assert out["listings"][0]["bedrooms"] == 5
    assert "raw" not in out["listings"][0]
