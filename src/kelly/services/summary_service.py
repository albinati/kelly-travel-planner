"""Trip cost rollup: aggregate logged bookings into a single target currency.

Reads the `bookings` table (latest-per-leg), normalises every leg via
``fx_service.convert`` to the requested currency, and groups by category
(trains / stays / tickets / other) plus a flat trip total. Each leg surfaces
both its original amount and the converted amount so the caller can spot FX
artifacts. Per-leg conversion failures are warnings, not hard errors.
"""

from __future__ import annotations

from decimal import Decimal

from kelly.history_store import SqliteHistoryStore
from kelly.services.booking_service import bookings_for_trip
from kelly.services.fx_service import FxError, convert, fx_quote

# Map each booking `leg` to a category for the by_category roll-up.
# Anything not matched lands in "other".
_LEG_PREFIX_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("eurostar", "trains"),
    ("train", "trains"),
    ("airbnb", "stays"),
    ("hotel", "stays"),
    ("disney", "tickets"),
    ("tickets", "tickets"),
)


def _category_for_leg(leg: str) -> str:
    lower = leg.lower()
    for prefix, cat in _LEG_PREFIX_CATEGORIES:
        if lower.startswith(prefix):
            return cat
    return "other"


def summarize_trip(
    trip_id: str,
    currency: str = "GBP",
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, object]:
    """Aggregate all logged bookings for `trip_id` into `currency`.

    Returns a JSON-friendly dict; never raises. Conversion errors per leg
    are appended to ``warnings`` and that leg's converted amount becomes
    ``None``.
    """
    target = currency.upper()
    bookings = bookings_for_trip(trip_id, store=store)
    warnings: list[str] = []
    by_leg: list[dict[str, object]] = []
    by_category: dict[str, Decimal] = {}
    trip_total = Decimal("0")

    for b in bookings:
        original_amount = Decimal(str(b.total_amount))
        original_ccy = (b.currency or "").upper()
        leg_entry: dict[str, object] = {
            "leg": b.leg,
            "provider": b.provider,
            "confirmation_ref": b.confirmation_ref,
            "original_amount": f"{original_amount:.2f}",
            "original_currency": original_ccy,
            "paid_at": b.paid_at,
            "paid_by": b.paid_by,
        }
        try:
            converted = convert(
                original_amount,
                original_ccy,
                target,
                as_of=b.paid_at,
                store=store,
            )
        except FxError as e:
            warnings.append(f"could not convert {b.leg} ({original_ccy}→{target}): {e}")
            leg_entry["converted_amount"] = None
            leg_entry["target_currency"] = target
            by_leg.append(leg_entry)
            continue
        leg_entry["converted_amount"] = f"{converted:.2f}"
        leg_entry["target_currency"] = target
        cat = _category_for_leg(b.leg)
        by_category[cat] = by_category.get(cat, Decimal("0")) + converted
        trip_total += converted
        by_leg.append(leg_entry)

    quote = fx_quote("EUR", target, store=store)
    fx_as_of = quote.get("as_of", "") if "error" not in quote else None

    return {
        "trip_id": trip_id,
        "currency": target,
        "fx_as_of": fx_as_of,
        "by_leg": by_leg,
        "by_category": {k: f"{v:.2f}" for k, v in sorted(by_category.items())},
        "totals": {"trip_total": f"{trip_total:.2f}"},
        "warnings": warnings,
        "booking_count": len(bookings),
    }
