"""Estimate the marginal cost of hosting a visiting party.

Reads a ``HostingRow`` plus any matching ``DaytripRow``s sharing its
``trip_id`` and produces a per-component breakdown:

  food_delta    = food_per_week * (visitors / host_household_size) * weeks
  dineout_delta = dineout_per_outing * (visitors / host_household_size) * outings
  transit       = transport_per_day * visitors * weeks * ACTIVE_DAY_FRACTION
  daytrips      = Σ (daytrip.est_cost_per_pp * daytrip.pax)  [currency-converted]
  buffer        = buffer_per_person * visitors
  total         = sum of the above

The model is intentionally simple: every number above is something the user
typed into kelly.md, so tuning the estimate means editing the row, not the
formula. ``ACTIVE_DAY_FRACTION`` (0.5 by default) is the only knob the
caller can override at the function boundary — it captures "what share of
the hosting window do visitors actually leave the house on transit".
"""

from __future__ import annotations

from decimal import Decimal

from kelly.history_store import SqliteHistoryStore
from kelly.md_config import DaytripRow, HostingRow, KellyConfig
from kelly.services.fx_service import FxError
from kelly.services.fx_service import convert as fx_convert

ACTIVE_DAY_FRACTION = Decimal("0.5")


def _convert(
    amount: Decimal,
    src: str,
    dst: str,
    *,
    store: SqliteHistoryStore | None,
    warnings: list[str],
    component_label: str,
) -> Decimal:
    """Wrap fx_service.convert with soft-fail; record warning if it bombs."""
    if src.upper() == dst.upper():
        return amount
    try:
        return fx_convert(amount, src, dst, store=store)
    except FxError as e:
        warnings.append(
            f"could not convert {component_label} ({src}→{dst}): {e}; using untouched amount"
        )
        return amount


def _hosting_window_days(row: HostingRow) -> int:
    return (row.dates_end - row.dates_start).days + 1


def _daytrips_for_trip(cfg: KellyConfig, trip_id: str) -> list[DaytripRow]:
    return [d for d in cfg.daytrips if d.trip_id == trip_id]


def estimate_hosting(
    hosting_id: str,
    *,
    cfg: KellyConfig,
    host_household_size: int = 2,
    currency: str | None = None,
    store: SqliteHistoryStore | None = None,
) -> dict[str, object]:
    """Estimate the visitor-attributable cost of hosting *hosting_id*.

    Pass ``host_household_size`` (default 2) so the food/dineout
    proportional split is right; pass ``currency`` to convert the result
    into that target (defaults to the row's own currency).
    """
    if host_household_size < 1:
        host_household_size = 1

    row = next((h for h in cfg.hosting if h.id == hosting_id), None)
    if row is None:
        return {
            "error": f"no hosting row with id {hosting_id!r}",
            "available_ids": [h.id for h in cfg.hosting],
        }

    base_ccy = row.currency
    target_ccy = (currency or base_ccy).upper()
    warnings: list[str] = []

    visitors = Decimal(row.visitor_count)
    household = Decimal(host_household_size)
    days = Decimal(_hosting_window_days(row))
    weeks = days / Decimal("7")
    visitor_share = visitors / household

    food_per_week = Decimal(str(row.host_baseline_food_per_week))
    dineout_per_outing = Decimal(str(row.host_baseline_dineout_per_outing))
    transit_per_day = Decimal(str(row.host_baseline_transport_per_day))
    buffer_pp = Decimal(str(row.buffer_per_person))
    outings = Decimal(row.planned_outings_count)

    food_delta = food_per_week * visitor_share * weeks
    dineout_delta = dineout_per_outing * visitor_share * outings
    transit_total = transit_per_day * visitors * days * ACTIVE_DAY_FRACTION
    buffer_total = buffer_pp * visitors

    food_delta = _convert(
        food_delta, base_ccy, target_ccy, store=store, warnings=warnings, component_label="food"
    )
    dineout_delta = _convert(
        dineout_delta,
        base_ccy,
        target_ccy,
        store=store,
        warnings=warnings,
        component_label="dineout",
    )
    transit_total = _convert(
        transit_total,
        base_ccy,
        target_ccy,
        store=store,
        warnings=warnings,
        component_label="transit",
    )
    buffer_total = _convert(
        buffer_total,
        base_ccy,
        target_ccy,
        store=store,
        warnings=warnings,
        component_label="buffer",
    )

    daytrips: list[dict[str, object]] = []
    daytrips_total = Decimal("0")
    for dt in _daytrips_for_trip(cfg, row.trip_id):
        amount = Decimal(str(dt.est_cost_per_pp)) * Decimal(dt.pax)
        converted = _convert(
            amount,
            dt.currency,
            target_ccy,
            store=store,
            warnings=warnings,
            component_label=f"daytrip:{dt.id}",
        )
        daytrips.append(
            {
                "id": dt.id,
                "destination": dt.destination,
                "date": dt.date.isoformat(),
                "pax": dt.pax,
                "original_amount": f"{amount:.2f}",
                "original_currency": dt.currency,
                "converted_amount": f"{converted:.2f}",
                "target_currency": target_ccy,
                "includes_overnight": dt.includes_overnight,
            }
        )
        daytrips_total += converted

    total = food_delta + dineout_delta + transit_total + buffer_total + daytrips_total

    over_max = None
    if row.max_total is not None:
        cap = Decimal(str(row.max_total))
        cap = _convert(
            cap,
            base_ccy,
            target_ccy,
            store=store,
            warnings=warnings,
            component_label="max_total",
        )
        if total > cap:
            over_max = f"{total - cap:.2f}"
            warnings.append(f"estimate exceeds max_total by {over_max} {target_ccy}")

    return {
        "hosting_id": row.id,
        "trip_id": row.trip_id,
        "visitor_party": row.visitor_party,
        "visitor_count": row.visitor_count,
        "host_household_size": host_household_size,
        "dates": {"start": row.dates_start.isoformat(), "end": row.dates_end.isoformat()},
        "window_days": int(days),
        "currency": target_ccy,
        "components": {
            "food_delta": f"{food_delta:.2f}",
            "dineout_delta": f"{dineout_delta:.2f}",
            "transit": f"{transit_total:.2f}",
            "daytrips": f"{daytrips_total:.2f}",
            "buffer": f"{buffer_total:.2f}",
        },
        "daytrips": daytrips,
        "totals": {
            "estimate": f"{total:.2f}",
            "per_person": f"{(total / visitors):.2f}",
        },
        "max_total": f"{row.max_total:.2f}" if row.max_total is not None else None,
        "over_max": over_max,
        "warnings": warnings,
    }
