"""Deterministic multi-option **all-in** budget comparison.

Given N candidate trip options — each a set of cost line items (possibly in
mixed currencies) plus an optional Avios component — convert everything to a
single target currency via :func:`kelly.services.fx_service.convert`, sum the
**all-in** total per option, and rank them. This is the deterministic core of
the "£1,250 vs £1,630 vs £1,950" comparison we used to do by hand.

ALL-IN RULE (project lesson — see CLAUDE.md / cross-provider pricing)
---------------------------------------------------------------------
Comparisons MUST use the **all-in** stay price, never a raw ``price_total``.
Mixing a raw ``price_total`` from one provider with an all-in price from
another is apples-vs-bananas and has produced a real ~£422 error on a Paris
plan. This service therefore *requires* that every stay line item is already
all-in:

- For a line with ``kind == "stay"``: if ``price_all_in`` is present it is
  used and any separate ``price_total`` is **ignored**; otherwise ``amount``
  is used and treated as already all-in.
- Every other ``kind`` uses ``amount`` directly.

AVIOS RULE
----------
Avios points are a *separate currency*. We sum and report ``avios_points`` per
option (and a top-level total), but we DO NOT fold points into the cash
``all_in_total`` — only the Avios cash **taxes** are converted into the target
currency and added to ``all_in_total``. Comparing options whose Avios spend
differs is a points-vs-cash judgement left to the caller.

Decimals are kept throughout; rounding to 2dp happens only at the jsonable
boundary. Conversion failures never raise: the offending line is counted at 0
and a warning is emitted.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import httpx

from kelly.history_store import SqliteHistoryStore
from kelly.services.fx_service import FxError, convert

# Recognised line ``kind`` values for the by_kind breakdown. Unknown kinds are
# accepted but bucketed under "other" so a typo never silently drops money.
_KNOWN_KINDS: frozenset[str] = frozenset(
    {"transport", "stay", "experience", "car", "local", "package", "other"}
)

_CENTS = Decimal("0.01")


def _q2(value: Decimal) -> str:
    """Round a Decimal to 2dp half-up and render as a string (jsonable boundary)."""
    return str(value.quantize(_CENTS, rounding=ROUND_HALF_UP))


def _line_amount(line: dict) -> Decimal:
    """Resolve the amount to charge for a line, applying the all-in rule.

    For ``kind == "stay"`` we prefer ``price_all_in`` and ignore any
    ``price_total``; otherwise we use ``amount`` (treated as already all-in).
    """
    kind = str(line.get("kind", "other")).lower()
    if kind == "stay" and line.get("price_all_in") is not None:
        return Decimal(str(line["price_all_in"]))
    return Decimal(str(line["amount"]))


def _convert(
    amount: Decimal,
    from_ccy: str,
    target: str,
    *,
    store: SqliteHistoryStore | None,
    client: httpx.Client | None,
) -> Decimal:
    """Convert one amount into the target currency (identity when already there)."""
    if from_ccy.upper() == target:
        return amount
    return convert(amount, from_ccy, target, store=store, client=client)


def _compare_one(
    option: dict,
    *,
    target: str,
    store: SqliteHistoryStore | None,
    client: httpx.Client | None,
) -> dict[str, object]:
    """Build the per-option roll-up. Never raises; FX failures become warnings."""
    name = str(option.get("name", "(unnamed)"))
    warnings: list[str] = []
    by_kind: dict[str, Decimal] = {}
    all_in_total = Decimal("0")
    conversions: set[tuple[str, str]] = set()

    for line in option.get("lines", []):
        raw_kind = str(line.get("kind", "other")).lower()
        kind = raw_kind if raw_kind in _KNOWN_KINDS else "other"
        from_ccy = str(line.get("currency", target)).upper()
        label = str(line.get("label", "(line)"))
        try:
            amount = _line_amount(line)
        except (KeyError, TypeError, ValueError):
            warnings.append(f"option {name!r}: line {label!r} has no usable amount; counted at 0")
            continue
        try:
            converted = _convert(amount, from_ccy, target, store=store, client=client)
        except FxError as e:
            warnings.append(
                f"option {name!r}: FX failed for line {label!r} ({from_ccy}->{target}): "
                f"{e}; counted at 0"
            )
            converted = Decimal("0")
        else:
            if from_ccy != target:
                conversions.add((from_ccy, target))
        by_kind[kind] = by_kind.get(kind, Decimal("0")) + converted
        all_in_total += converted

    # Avios: points are summed but NOT converted to cash; only the taxes go in.
    avios_points = 0
    avios_taxes = Decimal("0")
    avios = option.get("avios")
    if avios:
        try:
            avios_points = int(avios.get("points", 0) or 0)
        except (TypeError, ValueError):
            warnings.append(f"option {name!r}: avios.points not an int; counted as 0 points")
            avios_points = 0
        taxes_raw = avios.get("taxes_amount")
        taxes_ccy = str(avios.get("taxes_currency", target)).upper()
        if taxes_raw is not None:
            try:
                taxes_amount = Decimal(str(taxes_raw))
            except (TypeError, ValueError):
                warnings.append(f"option {name!r}: avios.taxes_amount not numeric; counted at 0")
                taxes_amount = Decimal("0")
            try:
                avios_taxes = _convert(taxes_amount, taxes_ccy, target, store=store, client=client)
            except FxError as e:
                warnings.append(
                    f"option {name!r}: FX failed for avios taxes ({taxes_ccy}->{target}): "
                    f"{e}; counted at 0"
                )
                avios_taxes = Decimal("0")
            else:
                if taxes_ccy != target:
                    conversions.add((taxes_ccy, target))
            all_in_total += avios_taxes
            by_kind["transport"] = by_kind.get("transport", Decimal("0")) + avios_taxes

    return {
        "name": name,
        "all_in_total": all_in_total,  # Decimal, jsonable-rounded by caller
        "by_kind": by_kind,  # Decimal map, jsonable-rounded by caller
        "avios_points": avios_points,
        "avios_taxes": avios_taxes,  # Decimal, included in all_in_total
        "currency_conversions": sorted(f"{a}->{b}" for a, b in conversions),
        "warnings": warnings,
    }


def compare_options(
    options: list[dict],
    *,
    target_currency: str = "GBP",
    store: SqliteHistoryStore | None = None,
    client: httpx.Client | None = None,
) -> dict[str, object]:
    """Compare and rank N candidate options by their **all-in** total.

    Each option is::

        {
          "name": str,
          "lines": [
            {"label": str, "amount": str|number, "currency": str,
             "kind": "transport|stay|experience|car|local|other",
             "price_all_in"?: str|number}
          ],
          "avios"?: {"points": int, "taxes_amount": str|number,
                     "taxes_currency": str},
        }

    Stay lines must already be all-in; if a stay line carries ``price_all_in``
    it is used and any ``price_total`` is ignored (see module docstring).

    Returns a JSON-friendly dict::

        {
          "target_currency": str,
          "options": [ <per-option roll-up> ...],
          "ranked": [name, ...],            # ascending by all_in_total
          "cheapest": name | None,
          "deltas": {name: <all_in_total - cheapest>, ...},
        }

    Never raises: a per-line FX failure counts that line at 0 and appends a
    warning. ``avios_points`` are summed and reported but never folded into the
    cash ``all_in_total`` (only the converted Avios taxes are).
    """
    target = target_currency.upper()
    per_option = [_compare_one(opt, target=target, store=store, client=client) for opt in options]

    # Rank ascending by all_in_total; stable on insertion order for ties.
    ranked_entries = sorted(
        enumerate(per_option), key=lambda item: (item[1]["all_in_total"], item[0])
    )
    ranked_names = [entry["name"] for _, entry in ranked_entries]
    cheapest_name: str | None = None
    cheapest_total = Decimal("0")
    if ranked_entries:
        cheapest_entry = ranked_entries[0][1]
        cheapest_name = cheapest_entry["name"]
        cheapest_total = cheapest_entry["all_in_total"]

    deltas = {entry["name"]: _q2(entry["all_in_total"] - cheapest_total) for entry in per_option}

    jsonable_options = [
        {
            "name": entry["name"],
            "all_in_total": _q2(entry["all_in_total"]),
            "by_kind": {k: _q2(v) for k, v in sorted(entry["by_kind"].items())},
            "avios_points": entry["avios_points"],
            "avios_taxes": _q2(entry["avios_taxes"]),
            "currency_conversions": entry["currency_conversions"],
            "warnings": entry["warnings"],
        }
        for entry in per_option
    ]

    return {
        "target_currency": target,
        "options": jsonable_options,
        "ranked": ranked_names,
        "cheapest": cheapest_name,
        "deltas": deltas,
    }
