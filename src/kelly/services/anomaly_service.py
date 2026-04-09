"""Label calendar price points vs SQLite baseline (no extra provider calls)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from kelly.analytics import cash_baseline
from kelly import settings

Label = Literal["normal", "cheap_vs_history", "opportunity", "unknown", "no_price"]


@dataclass
class DayAnomaly:
    date: date
    indicative_price: float | None
    label: Label
    reason: str


def scan_price_graph_vs_history(
    graph_days: list[dict[str, Any]],
    *,
    origin_iata: str,
    destination_iata: str,
    cabin: str,
    hist_amounts: list[float],
    target_price: float | None = None,
) -> list[DayAnomaly]:
    """
    *graph_days*: items with keys ``date`` (ISO), ``indicative_price`` (number or string).

    *origin_iata* / *destination_iata* / *cabin* reserved for future route-specific baselines.
    """
    _ = origin_iata, destination_iata, cabin
    cb = cash_baseline(hist_amounts)
    min_n = settings.anomaly_min_hist_samples()
    use_p10 = settings.anomaly_opportunity_below_p10()
    out: list[DayAnomaly] = []

    for row in graph_days:
        ds = row.get("date")
        if isinstance(ds, date):
            d = ds
        else:
            try:
                d = date.fromisoformat(str(ds)[:10])
            except ValueError:
                continue
        raw_p = row.get("indicative_price")
        if raw_p is None:
            out.append(DayAnomaly(d, None, "no_price", "missing price"))
            continue
        try:
            price = float(Decimal(str(raw_p)))
        except Exception:
            out.append(DayAnomaly(d, None, "unknown", "unparseable price"))
            continue

        if cb.n < min_n or cb.median is None:
            hist_tag = "unknown"
            hist_reason = f"insufficient_history n={cb.n} need>={min_n}"
        elif use_p10 and cb.p10 is not None and price <= float(cb.p10):
            hist_tag = "cheap_vs_history"
            hist_reason = f"price {price} <= historical p10 {cb.p10:.2f}"
        elif not use_p10 and price <= float(cb.median) * 0.9:
            hist_tag = "cheap_vs_history"
            hist_reason = f"price {price} <= 90% of median {cb.median:.2f}"
        else:
            hist_tag = "normal"
            hist_reason = f"vs median {cb.median:.2f}" if cb.median is not None else "no median"

        under_target = target_price is not None and price <= target_price
        dip = hist_tag == "cheap_vs_history"

        if dip and (target_price is None or under_target):
            label: Label = "opportunity"
            reason = hist_reason + ("; under family target" if under_target else "")
        elif dip:
            label = "cheap_vs_history"
            reason = hist_reason + f" (above target {target_price})"
        elif under_target:
            label = "cheap_vs_history"
            reason = f"under target_price {target_price} (not a historical dip)"
        elif hist_tag == "unknown":
            label = "unknown"
            reason = hist_reason
        else:
            label = "normal"
            reason = hist_reason

        out.append(DayAnomaly(d, price, label, reason))

    return out
