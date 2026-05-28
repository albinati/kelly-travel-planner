from datetime import date
from decimal import Decimal
from unittest.mock import patch

from kelly.md_config import KellyConfig, KellyFrontmatter, StayRow
from kelly.providers.liteapi_hotels import (
    HotelListing,
    HotelSearchResult,
    occupancies_from_pax,
    parse_area,
)
from kelly.services.hotel_service import hotel_result_to_jsonable, search_hotel


def _cfg() -> KellyConfig:
    return KellyConfig(
        frontmatter=KellyFrontmatter(currency="GBP"),
        trains=[],
        stays=[],
    )


def _row(area: str = "Paris, France") -> StayRow:
    return StayRow(
        id="sample-trip",
        area=area,
        check_in=date(2026, 8, 18),
        check_out=date(2026, 8, 21),
        adults=7,
        children_ages=[3, 4, 5],
        bedrooms_min=3,
        near=["Châtelet-Les Halles"],
        max_total=4500,
    )


def _listing(price: str, id_: str = "lp1") -> HotelListing:
    return HotelListing(
        id=id_,
        name=f"Hotel {id_}",
        stars=4.0,
        rating=8.5,
        review_count=120,
        price_total=Decimal(price),
        price_all_in=Decimal(price) + Decimal("80"),  # simulate £80 in pay-on-arrival tax
        taxes_at_property=Decimal("80"),
        suggested_price=Decimal(price),
        currency="GBP",
        address="1 Rue de Test",
        city="Paris",
        country="FR",
        lat=48.86,
        lng=2.35,
        main_photo=None,
        description=None,
        facility_ids=[],
        board_type="Room Only",
        refundable=True,
        offer_id="offer_" + id_,
    )


@patch("kelly.services.hotel_service.search_hotels")
def test_search_hotel_forwards_pax_and_dates(mock_search) -> None:
    mock_search.return_value = HotelSearchResult(listings=[], error=None)
    search_hotel(_cfg(), _row())
    kwargs = mock_search.call_args.kwargs
    assert kwargs["location"] == "Paris, France"
    assert kwargs["check_in"] == date(2026, 8, 18)
    assert kwargs["check_out"] == date(2026, 8, 21)
    assert kwargs["currency"] == "GBP"
    assert kwargs["guest_nationality"] == "GB"
    assert kwargs["max_total"] == 4500
    # bedrooms_min=3 → 3 rooms
    assert len(kwargs["occupancies"]) == 3
    # 7 adults distributed across 3 rooms: 3, 2, 2
    adults_per_room = sorted([o["adults"] for o in kwargs["occupancies"]])
    assert adults_per_room == [2, 2, 3]
    # 3 children spread one per room
    children_per_room = sorted([len(o["children"]) for o in kwargs["occupancies"]])
    assert children_per_room == [1, 1, 1]


@patch("kelly.services.hotel_service.search_hotels")
def test_search_hotel_persists_cheapest(mock_search, tmp_path) -> None:
    from kelly.history_store import SqliteHistoryStore, hotel_key

    listings = [_listing("3200.00", "lp_cheap"), _listing("4100.00", "lp_pricey")]
    mock_search.return_value = HotelSearchResult(listings=listings, error=None)
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    search_hotel(_cfg(), _row(), store=store, persist=True)

    hk = hotel_key("Paris, France", date(2026, 8, 18), date(2026, 8, 21))
    # _listing() simulates £80 of pay-on-arrival tax → all-in = base + 80
    assert store.fetch_hotel_amounts(hk) == [3280.0]


@patch("kelly.services.hotel_service.search_hotels")
def test_search_hotel_skips_persist_on_error(mock_search, tmp_path) -> None:
    from kelly.history_store import SqliteHistoryStore, hotel_key

    mock_search.return_value = HotelSearchResult(listings=[], error="LITEAPI_API_KEY not set")
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    search_hotel(_cfg(), _row(), store=store, persist=True)
    assert (
        store.fetch_hotel_amounts(hotel_key("Paris, France", date(2026, 8, 18), date(2026, 8, 21)))
        == []
    )


def test_hotel_result_to_jsonable_serializes_decimal() -> None:
    res = HotelSearchResult(listings=[_listing("4200.00")])
    out = hotel_result_to_jsonable(res)
    assert out["listings"][0]["price_total"] == "4200.00"
    assert out["listings"][0]["stars"] == 4.0
    assert out["listings"][0]["offer_id"] == "offer_lp1"
    assert "raw" not in out["listings"][0]


def test_parse_area_known_country() -> None:
    assert parse_area("Paris, France") == ("Paris", "FR")
    assert parse_area("London, United Kingdom") == ("London", "GB")
    assert parse_area("New York, USA") == ("New York", "US")


def test_parse_area_unknown_country_returns_none() -> None:
    assert parse_area("Paris, Atlantis") is None
    assert parse_area("Paris") is None
    assert parse_area("") is None


def test_occupancies_from_pax_distributes_evenly() -> None:
    # 4 adults + 2 seniors + 1 teen + 3 children (3,4,5) = 7 adults + 3 children, 3 rooms
    occ = occupancies_from_pax(adults=7, children_ages=[3, 4, 5], rooms=3)
    assert len(occ) == 3
    assert sum(o["adults"] for o in occ) == 7
    assert sum(len(o["children"]) for o in occ) == 3
    # No room left without an adult
    assert all(o["adults"] >= 1 for o in occ)


def test_occupancies_drops_infants() -> None:
    occ = occupancies_from_pax(adults=2, children_ages=[1, 5], rooms=1)
    # infant (<2) excluded, child age 5 stays
    assert occ == [{"adults": 2, "children": [5]}]


def test_occupancies_promotes_teens_to_adult() -> None:
    occ = occupancies_from_pax(adults=2, children_ages=[15], rooms=1)
    # 15yo rolls into adults; total adults = 3, children = []
    assert occ == [{"adults": 3, "children": []}]
