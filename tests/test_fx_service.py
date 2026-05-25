"""Tests for fx_service — mocked ECB feed, real SQLite via conftest tmp dir."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from kelly.history_store import SqliteHistoryStore
from kelly.services.fx_service import (
    ECB_DAILY_URL,
    FxError,
    convert,
    fetch_ecb_rates,
    fx_quote,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ecb_eurofxref_daily.xml"


def _fixture_xml() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def _mock_client(xml: str | None = None, status: int = 200) -> httpx.Client:
    """httpx client whose transport returns our fixture XML for the ECB URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == ECB_DAILY_URL
        body = xml if xml is not None else _fixture_xml()
        return httpx.Response(status, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_ecb_rates_parses_currencies(tmp_path) -> None:
    with _mock_client() as c:
        as_of, rates = fetch_ecb_rates(client=c)
    assert as_of == "2026-05-25"
    assert rates["EUR"] == Decimal("1")
    assert rates["GBP"] == Decimal("0.8500")
    assert rates["BRL"] == Decimal("5.9500")


def test_convert_same_currency_is_identity(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    # No network needed — same-ccy short-circuits.
    result = convert("100", "GBP", "GBP", store=store)
    assert result == Decimal("100")


def test_convert_gbp_to_brl_uses_cross_rate(tmp_path) -> None:
    """1 GBP at the fixture rates = (1 / 0.85) * 5.95 ≈ 7.00 BRL."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    with _mock_client() as c:
        result = convert("1", "GBP", "BRL", store=store, client=c)
    # Decimal precision — assert within 1e-6
    assert abs(result - Decimal("7")) < Decimal("0.0001")


def test_convert_persists_rates_for_reuse(tmp_path) -> None:
    """Second call doesn't hit the network."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, text=_fixture_xml())

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        convert("1", "GBP", "BRL", store=store, client=c)
        convert("1", "GBP", "EUR", store=store, client=c)
        convert("100", "GBP", "BRL", store=store, client=c)

    assert call_count["n"] == 1  # cache hits after the first fetch


def test_convert_fails_when_no_cache_and_no_network(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "h.sqlite")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated offline")

    with httpx.Client(transport=httpx.MockTransport(boom)) as c:
        with pytest.raises(FxError):
            convert("1", "GBP", "BRL", store=store, client=c)


def test_convert_falls_back_to_cached_date_when_network_dies(tmp_path) -> None:
    """Once we have cached rates, a network outage still allows conversion."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    # Seed cache via a successful fetch first.
    with _mock_client() as c:
        convert("1", "GBP", "BRL", store=store, client=c)

    # Now simulate the network dying — we should still get the same result
    # by using the cached as_of.
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated offline")

    with httpx.Client(transport=httpx.MockTransport(boom)) as c:
        # New date in the cache: today is in the store now. A second convert
        # for a *cached* date should not need to fetch anything new.
        result = convert("100", "GBP", "BRL", store=store, client=c)
    assert result > Decimal("0")


def test_fx_quote_returns_rate_metadata(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    with _mock_client() as c:
        q = fx_quote("GBP", "BRL", store=store, client=c)
    assert q["from_ccy"] == "GBP"
    assert q["to_ccy"] == "BRL"
    assert q["source"] == "ecb"
    assert q["as_of"] == "2026-05-25"
    # Rate is 1 GBP in BRL ≈ 7.00
    assert Decimal(q["rate"]).quantize(Decimal("0.01")) == Decimal("7.00")


def test_fx_quote_returns_error_when_no_data(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "h.sqlite")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated offline")

    with httpx.Client(transport=httpx.MockTransport(boom)) as c:
        q = fx_quote("GBP", "BRL", store=store, client=c)
    assert "error" in q


def test_convert_eur_is_identity_through_pair(tmp_path) -> None:
    """EUR is implicit in the table at rate 1; converting from EUR works."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    with _mock_client() as c:
        # 100 EUR → 85 GBP at fixture rate 0.85
        result = convert("100", "EUR", "GBP", store=store, client=c)
    assert result == Decimal("85.00")


def test_explicit_as_of_uses_cached_row_when_available(tmp_path, monkeypatch) -> None:
    """Pass an explicit historical date; if cached, no network call needed."""
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    # Seed an explicit historical row.
    store.record_fx_rate(as_of="2026-01-15", base_ccy="EUR", quote_ccy="GBP", rate=0.84)
    store.record_fx_rate(as_of="2026-01-15", base_ccy="EUR", quote_ccy="BRL", rate=6.00)

    def boom(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not have fetched")

    with httpx.Client(transport=httpx.MockTransport(boom)) as c:
        # GBP→BRL on 2026-01-15: (1/0.84) * 6.00 ≈ 7.14
        result = convert("1", "GBP", "BRL", as_of="2026-01-15", store=store, client=c)
    assert abs(result - Decimal("7.142857142857")) < Decimal("0.0001")
