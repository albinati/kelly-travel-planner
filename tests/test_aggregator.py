from datetime import date
from unittest.mock import MagicMock, patch

from kelly.services.aggregator import run_gated_pipeline


@patch("kelly.services.aggregator.fetch_flight_quote")
@patch("kelly.services.aggregator.macro_price_graph")
def test_gated_pipeline_skips_heavy_without_opportunities(mock_macro, mock_micro) -> None:
    mock_macro.return_value = {
        "origin_iata": "JFK",
        "destination_iata": "LIS",
        "window_start": "2026-12-01",
        "window_end": "2026-12-07",
        "cabin": "economy",
        "error": None,
        "http_requests_used": 1,
        "days": [{"date": "2026-12-03", "indicative_price": "500", "currency": "USD"}],
    }
    store = MagicMock()
    store.fetch_amounts_for_route.return_value = ([], [])
    out = run_gated_pipeline(
        graph_api_key="g",
        micro_cash_key="m",
        micro_backend="rapidapi",
        origin_iata="JFK",
        destination_iata="LIS",
        window_start=date(2026, 12, 1),
        window_end=date(2026, 12, 7),
        cabin="economy",
        currency="USD",
        passengers=[{"type": "adult"}],
        store=store,
        policy=None,
        target_price=None,
    )
    assert out["heavy_calls_used"] == 0
    assert mock_micro.call_count == 0
