from datetime import date
from unittest.mock import patch

from kelly.md_config import KellyConfig, KellyFrontmatter, TrainRow
from kelly.providers.playwright_eurostar import EurostarJourney, EurostarSearchResult
from kelly.services.train_service import search_train, train_result_to_jsonable


def _cfg() -> KellyConfig:
    return KellyConfig(
        frontmatter=KellyFrontmatter(currency="GBP"),
        passengers=[],
        planned=[],
    )


def _row() -> TrainRow:
    return TrainRow(
        id="trip1-out",
        operator="eurostar",
        origin_city="LON",
        destination_city="PAR",
        date_start=date(2026, 8, 20),
        date_end=date(2026, 8, 22),
        adults=6,
        seniors=2,
        teens=1,
        children_ages=[3, 4, 5],
        **{"class": "standard"},
    )


@patch("kelly.services.train_service.search_eurostar")
def test_search_train_passes_group_counts(mock_search) -> None:
    mock_search.return_value = EurostarSearchResult(journeys=[], error=None)
    search_train(_cfg(), _row())
    kwargs = mock_search.call_args.kwargs
    assert kwargs["origin_city"] == "LON"
    assert kwargs["destination_city"] == "PAR"
    assert kwargs["adults"] == 6
    assert kwargs["seniors"] == 2
    assert kwargs["teens"] == 1
    assert kwargs["children_ages"] == [3, 4, 5]
    assert kwargs["class_"] == "standard"
    assert kwargs["currency"] == "GBP"


@patch("kelly.services.train_service.search_eurostar")
def test_search_train_persists_cheapest_per_date(mock_search, tmp_path) -> None:
    from decimal import Decimal

    from kelly.history_store import SqliteHistoryStore, train_key
    from kelly.providers.playwright_eurostar import EurostarJourney

    journeys = [
        EurostarJourney(
            depart_time="09:00", arrive_time="11:30", duration_min=150, changes=0,
            total_price=Decimal("145.00"), price_currency="GBP", class_="standard",
            refundable=False, operator="eurostar", deep_link="x",
            raw={"date": "2026-08-20"},
        ),
        EurostarJourney(
            depart_time="13:00", arrive_time="15:30", duration_min=150, changes=0,
            total_price=Decimal("99.00"), price_currency="GBP", class_="standard",
            refundable=False, operator="eurostar", deep_link="x",
            raw={"date": "2026-08-20"},
        ),
        EurostarJourney(
            depart_time="09:00", arrive_time="11:30", duration_min=150, changes=0,
            total_price=Decimal("210.00"), price_currency="GBP", class_="standard",
            refundable=False, operator="eurostar", deep_link="x",
            raw={"date": "2026-08-22"},
        ),
    ]
    mock_search.return_value = EurostarSearchResult(journeys=journeys, error=None)
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    search_train(_cfg(), _row(), store=store, persist=True)

    aug20 = train_key("eurostar", "LON", "PAR", "standard", date(2026, 8, 20))
    aug22 = train_key("eurostar", "LON", "PAR", "standard", date(2026, 8, 22))
    assert store.fetch_train_amounts(aug20) == [99.0]
    assert store.fetch_train_amounts(aug22) == [210.0]


@patch("kelly.services.train_service.search_eurostar")
def test_search_train_skip_persist_on_error(mock_search, tmp_path) -> None:
    from kelly.history_store import SqliteHistoryStore, train_key

    mock_search.return_value = EurostarSearchResult(journeys=[], error="patchright not installed")
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    search_train(_cfg(), _row(), store=store, persist=True)
    aug20 = train_key("eurostar", "LON", "PAR", "standard", date(2026, 8, 20))
    assert store.fetch_train_amounts(aug20) == []


def test_train_result_to_jsonable() -> None:
    from decimal import Decimal

    res = EurostarSearchResult(
        journeys=[
            EurostarJourney(
                depart_time="09:00",
                arrive_time="11:30",
                duration_min=150,
                changes=0,
                total_price=Decimal("1425.00"),
                price_currency="GBP",
                class_="standard",
                refundable=False,
                operator="eurostar",
                deep_link="https://eurostar.com/foo",
            )
        ],
        note="Party of 10+: Eurostar Groups Desk usually handles this via phone/email; "
        "live scraper quotes may be incomplete.",
    )
    out = train_result_to_jsonable(res)
    assert out["journeys"][0]["total_price"] == "1425.00"
    assert out["note"].startswith("Party of 10+")
