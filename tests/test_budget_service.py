"""Tests for budget_service.compare_options — mocked ECB feed, real SQLite.

Reuses the fx_service test pattern: a real ``SqliteHistoryStore(tmp_path)`` plus
an ``httpx.MockTransport`` client returning the same ECB fixture, injected via
``store=``/``client=``. No network.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx

from kelly.history_store import SqliteHistoryStore
from kelly.services.budget_service import compare_options
from kelly.services.fx_service import ECB_DAILY_URL

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ecb_eurofxref_daily.xml"


def _fixture_xml() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def _mock_client() -> httpx.Client:
    """httpx client whose transport returns the fixture XML for the ECB URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ECB_DAILY_URL
        return httpx.Response(200, text=_fixture_xml())

    return httpx.Client(transport=httpx.MockTransport(handler))


def _boom_client() -> httpx.Client:
    """httpx client that simulates being offline (no cache to fall back on)."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated offline")

    return httpx.Client(transport=httpx.MockTransport(boom))


def test_single_currency_sum_rank_deltas(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    options = [
        {
            "name": "Cheap",
            "lines": [
                {"label": "Eurostar", "amount": "180", "currency": "GBP", "kind": "transport"},
                {"label": "Airbnb", "amount": "600", "currency": "GBP", "kind": "stay"},
            ],
        },
        {
            "name": "Mid",
            "lines": [
                {"label": "Eurostar", "amount": "180", "currency": "GBP", "kind": "transport"},
                {"label": "Hotel", "amount": "1000", "currency": "GBP", "kind": "stay"},
            ],
        },
        {
            "name": "Pricey",
            "lines": [
                {"label": "Flights", "amount": "400", "currency": "GBP", "kind": "transport"},
                {"label": "Resort", "amount": "1200", "currency": "GBP", "kind": "stay"},
            ],
        },
    ]
    # Same currency throughout — no network needed.
    with _boom_client() as c:
        res = compare_options(options, target_currency="GBP", store=store, client=c)

    totals = {o["name"]: o["all_in_total"] for o in res["options"]}
    assert totals["Cheap"] == "780.00"
    assert totals["Mid"] == "1180.00"
    assert totals["Pricey"] == "1600.00"
    assert res["ranked"] == ["Cheap", "Mid", "Pricey"]
    assert res["cheapest"] == "Cheap"
    assert res["deltas"] == {"Cheap": "0.00", "Mid": "400.00", "Pricey": "820.00"}
    # by_kind split is preserved.
    cheap = next(o for o in res["options"] if o["name"] == "Cheap")
    assert cheap["by_kind"] == {"stay": "600.00", "transport": "180.00"}
    assert all(o["warnings"] == [] for o in res["options"])


def test_mixed_currency_converted(tmp_path) -> None:
    """A EUR line in a GBP comparison converts via the ECB fixture (0.85)."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    options = [
        {
            "name": "Euro trip",
            "lines": [
                {"label": "Eurostar", "amount": "180", "currency": "GBP", "kind": "transport"},
                # 1000 EUR * 0.85 = 850 GBP
                {"label": "Airbnb", "amount": "1000", "currency": "EUR", "kind": "stay"},
            ],
        },
    ]
    with _mock_client() as c:
        res = compare_options(options, target_currency="GBP", store=store, client=c)

    opt = res["options"][0]
    assert opt["all_in_total"] == "1030.00"  # 180 + 850
    assert opt["by_kind"]["stay"] == "850.00"
    assert opt["currency_conversions"] == ["EUR->GBP"]
    assert opt["warnings"] == []


def test_price_all_in_rule_overrides_price_total(tmp_path) -> None:
    """A stay line with both fields must use price_all_in, ignoring price_total."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    options = [
        {
            "name": "All-in honoured",
            "lines": [
                {
                    "label": "Airbnb (cleaning+fees)",
                    "amount": "999",  # should be ignored too (price_all_in wins)
                    "currency": "GBP",
                    "kind": "stay",
                    "price_total": "600",  # raw — MUST be ignored
                    "price_all_in": "742",  # all-in — MUST be used
                },
            ],
        },
    ]
    with _boom_client() as c:
        res = compare_options(options, target_currency="GBP", store=store, client=c)

    opt = res["options"][0]
    assert opt["all_in_total"] == "742.00"
    assert opt["by_kind"]["stay"] == "742.00"


def test_stay_without_price_all_in_uses_amount(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    options = [
        {
            "name": "Bare stay",
            "lines": [
                {"label": "Hotel", "amount": "500", "currency": "GBP", "kind": "stay"},
            ],
        },
    ]
    with _boom_client() as c:
        res = compare_options(options, target_currency="GBP", store=store, client=c)
    assert res["options"][0]["all_in_total"] == "500.00"


def test_avios_points_separate_taxes_in_total(tmp_path) -> None:
    """Avios points are reported but NOT cash; Avios taxes ARE in the total."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    options = [
        {
            "name": "Avios option",
            "lines": [
                {"label": "Airbnb", "amount": "600", "currency": "GBP", "kind": "stay"},
            ],
            "avios": {"points": 30000, "taxes_amount": "120", "taxes_currency": "GBP"},
        },
    ]
    with _boom_client() as c:
        res = compare_options(options, target_currency="GBP", store=store, client=c)

    opt = res["options"][0]
    assert opt["avios_points"] == 30000
    assert opt["avios_taxes"] == "120.00"
    # all_in = 600 stay + 120 avios taxes; points are NOT folded into cash.
    assert opt["all_in_total"] == "720.00"
    # taxes land in the transport bucket.
    assert opt["by_kind"]["transport"] == "120.00"


def test_avios_taxes_in_foreign_currency_converted(tmp_path) -> None:
    """Avios taxes in EUR convert to the target currency before being added."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    options = [
        {
            "name": "EUR avios taxes",
            "lines": [
                {"label": "Airbnb", "amount": "600", "currency": "GBP", "kind": "stay"},
            ],
            # 100 EUR * 0.85 = 85 GBP
            "avios": {"points": 25000, "taxes_amount": "100", "taxes_currency": "EUR"},
        },
    ]
    with _mock_client() as c:
        res = compare_options(options, target_currency="GBP", store=store, client=c)

    opt = res["options"][0]
    assert opt["avios_points"] == 25000
    assert opt["avios_taxes"] == "85.00"
    assert opt["all_in_total"] == "685.00"
    assert "EUR->GBP" in opt["currency_conversions"]


def test_fx_failure_on_one_line_warns_and_counts_zero(tmp_path) -> None:
    """No cache + offline → that line counts at 0 with a warning, never raises."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    options = [
        {
            "name": "Partial FX failure",
            "lines": [
                {"label": "Eurostar", "amount": "180", "currency": "GBP", "kind": "transport"},
                # BRL with no cache + offline client → FxError → counted at 0
                {"label": "Local", "amount": "500", "currency": "BRL", "kind": "local"},
            ],
        },
    ]
    with _boom_client() as c:
        res = compare_options(options, target_currency="GBP", store=store, client=c)

    opt = res["options"][0]
    # Only the GBP line counted; BRL line at 0.
    assert opt["all_in_total"] == "180.00"
    assert opt["by_kind"]["transport"] == "180.00"
    assert opt["by_kind"]["local"] == "0.00"
    assert len(opt["warnings"]) == 1
    assert "FX failed" in opt["warnings"][0]
    assert "BRL->GBP" in opt["warnings"][0]


def test_cheapest_ranked_deltas_with_mixed_currencies(tmp_path) -> None:
    """End-to-end ranking across mixed currencies + Avios — the by-hand flow."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    options = [
        {
            "name": "Sicily",
            "lines": [
                {"label": "Stay", "amount": "800", "currency": "EUR", "kind": "stay"},  # 680 GBP
                {"label": "Car", "amount": "100", "currency": "EUR", "kind": "car"},  # 85 GBP
            ],
            "avios": {"points": 30000, "taxes_amount": "120", "taxes_currency": "GBP"},
        },
        {
            "name": "Rhodes",
            "lines": [
                {"label": "Stay", "amount": "700", "currency": "GBP", "kind": "stay"},
                {"label": "Flights", "amount": "300", "currency": "GBP", "kind": "transport"},
            ],
        },
        {
            "name": "Puglia",
            "lines": [
                {"label": "Stay", "amount": "1500", "currency": "EUR", "kind": "stay"},  # 1275 GBP
            ],
            "avios": {"points": 26000, "taxes_amount": "100", "taxes_currency": "GBP"},
        },
    ]
    with _mock_client() as c:
        res = compare_options(options, target_currency="GBP", store=store, client=c)

    totals = {o["name"]: Decimal(o["all_in_total"]) for o in res["options"]}
    # Sicily: 680 + 85 + 120 = 885
    assert totals["Sicily"] == Decimal("885.00")
    # Rhodes: 700 + 300 = 1000
    assert totals["Rhodes"] == Decimal("1000.00")
    # Puglia: 1275 + 100 = 1375
    assert totals["Puglia"] == Decimal("1375.00")

    assert res["ranked"] == ["Sicily", "Rhodes", "Puglia"]
    assert res["cheapest"] == "Sicily"
    assert res["deltas"] == {
        "Sicily": "0.00",
        "Rhodes": "115.00",
        "Puglia": "490.00",
    }


def test_empty_options_list(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    res = compare_options([], target_currency="GBP", store=store)
    assert res["ranked"] == []
    assert res["cheapest"] is None
    assert res["deltas"] == {}
    assert res["options"] == []
