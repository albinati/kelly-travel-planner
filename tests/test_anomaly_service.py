from kelly.services.anomaly_service import scan_price_graph_vs_history


def test_anomaly_unknown_without_history() -> None:
    rows = scan_price_graph_vs_history(
        [{"date": "2026-07-01", "indicative_price": 400}],
        origin_iata="JFK",
        destination_iata="LIS",
        cabin="economy",
        hist_amounts=[],
        target_price=None,
    )
    assert len(rows) == 1
    assert rows[0].label == "unknown"


def test_anomaly_opportunity_dip_and_target() -> None:
    # Enough history for p10; price well below p10
    hist = [800.0, 750.0, 700.0, 650.0, 600.0, 550.0, 500.0, 480.0, 460.0, 440.0]
    rows = scan_price_graph_vs_history(
        [{"date": "2026-07-10", "indicative_price": 300}],
        origin_iata="JFK",
        destination_iata="LIS",
        cabin="economy",
        hist_amounts=hist,
        target_price=350,
    )
    assert rows[0].label == "opportunity"
