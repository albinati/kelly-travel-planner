from datetime import date
from decimal import Decimal
from unittest.mock import patch

from kelly.providers.google_flights import PriceGraphDay, PriceGraphResult
from kelly.services.macro_service import macro_price_graph


@patch("kelly.services.macro_service.fetch_price_graph")
def test_macro_price_graph_normalizes_days(mock_fg) -> None:
    mock_fg.return_value = PriceGraphResult(
        origin_iata="JFK",
        destination_iata="LIS",
        window_start=date(2026, 12, 1),
        window_end=date(2026, 12, 31),
        cabin="economy",
        days=[
            PriceGraphDay(date=date(2026, 12, 5), indicative_price=Decimal("412"), currency="USD"),
        ],
        error=None,
        http_requests_used=1,
    )
    out = macro_price_graph(
        "fake-key",
        origin_iata="JFK",
        destination_iata="LIS",
        window_start=date(2026, 12, 1),
        window_end=date(2026, 12, 31),
    )
    assert out["http_requests_used"] == 1
    assert len(out["days"]) == 1
    assert out["days"][0]["date"] == "2026-12-05"
    assert out["days"][0]["indicative_price"] == "412"
