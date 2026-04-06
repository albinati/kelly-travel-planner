from kelly.analytics import (
    cash_baseline,
    label_cash_vs_history,
    phase1_forecast_hint,
)


def test_cash_baseline() -> None:
    b = cash_baseline([100.0, 200.0, 300.0, 400.0, 500.0])
    assert b.n == 5
    assert b.median == 300.0
    assert b.p10 is not None and b.p90 is not None


def test_label_insufficient() -> None:
    b = cash_baseline([100.0, 110.0])
    assert label_cash_vs_history(105.0, b) == "insufficient_history"


def test_forecast_unknown() -> None:
    h = phase1_forecast_hint(100.0, [])
    assert h.cash_trend == "unknown"
