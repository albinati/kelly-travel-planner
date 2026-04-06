"""Historical baselines and phase-1 buy/wait heuristics."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Literal

Label = Literal["low", "typical", "high", "insufficient_history"]


@dataclass
class CashBaseline:
    n: int
    median: float | None
    p10: float | None
    p90: float | None
    min_v: float | None
    max_v: float | None


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def cash_baseline(amounts: list[float]) -> CashBaseline:
    vals = sorted(a for a in amounts if a is not None and a > 0)
    n = len(vals)
    if n == 0:
        return CashBaseline(0, None, None, None, None, None)
    return CashBaseline(
        n=n,
        median=float(statistics.median(vals)),
        p10=_percentile(vals, 0.10),
        p90=_percentile(vals, 0.90),
        min_v=vals[0],
        max_v=vals[-1],
    )


def label_cash_vs_history(amount: float | None, baseline: CashBaseline) -> Label:
    if amount is None or baseline.n < 3 or baseline.p10 is None or baseline.p90 is None:
        return "insufficient_history"
    if amount <= baseline.p10:
        return "low"
    if amount >= baseline.p90:
        return "high"
    return "typical"


def label_miles_vs_history(miles: int | None, historical: list[int]) -> Label:
    if miles is None or len(historical) < 3:
        return "insufficient_history"
    s = sorted(historical)
    p10 = _percentile([float(x) for x in s], 0.10)
    p90 = _percentile([float(x) for x in s], 0.90)
    if p10 is None or p90 is None:
        return "insufficient_history"
    m = float(miles)
    if m <= p10:
        return "low"
    if m >= p90:
        return "high"
    return "typical"


@dataclass
class ForecastHint:
    """Heuristic only — not financial advice."""

    cash_trend: str  # cheaper | flatter | pricier | unknown
    buy_hint_cash: str
    disclaimer: str = "Heuristic from your local history only; fares change constantly."


def phase1_forecast_hint(
    current_cash: float | None,
    historical_cash: list[float],
) -> ForecastHint:
    if current_cash is None or len(historical_cash) < 2:
        return ForecastHint(
            cash_trend="unknown",
            buy_hint_cash="Not enough stored cash quotes to compare.",
        )
    recent = historical_cash[-5:]
    older = historical_cash[:-5] if len(historical_cash) > 5 else historical_cash[:1]
    med_recent = statistics.median(recent)
    med_older = statistics.median(older) if older else med_recent
    if med_recent < med_older * 0.97:
        trend = "cheaper"
        hint = "Recent snapshots trend cheaper than earlier ones — still verify live before booking."
    elif med_recent > med_older * 1.03:
        trend = "pricier"
        hint = "Recent snapshots trend higher than earlier — if below your target, consider acting."
    else:
        trend = "flatter"
        hint = "Recent snapshots are flat vs earlier window — use your target and gut feel."

    if current_cash is not None and historical_cash:
        b = cash_baseline(historical_cash[:-1] if len(historical_cash) > 1 else historical_cash)
        if b.n >= 3 and b.p10 is not None and current_cash <= b.p10:
            hint = "Current cash is at or below your historical p10 for this key — relatively strong vs your own data."

    return ForecastHint(cash_trend=trend, buy_hint_cash=hint)
