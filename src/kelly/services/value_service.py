"""Deterministic value engine: cash-vs-Avios + points coverage (no external API).

Two pure-logic helpers that replace the hand calculations we kept redoing while
planning real trips:

``compute_avios_value`` — given the *cash* price of a fare and the *Avios* (+ cash
taxes) it would cost on points, work out **pence per Avios** and a plain
recommendation (cash / avios / equivalent). The Avios number is a direct input
(the real ba.com price), never the short-haul RFS estimate, which misled us.

``check_award_coverage`` — given how many Avios a redemption needs, work out
whether the traveller can cover it from their Avios balance plus convertible
HSBC points (default 2:1), and the shortfall if not. Balances come either from
explicit args or from a stored profile's ``payload_json["points_balances"]``.

Money is handled with ``Decimal`` throughout; we convert to the target currency
via :func:`kelly.services.fx_service.convert`. FX failures are caught and
surfaced as warnings rather than raised.
"""

from __future__ import annotations

import json
from decimal import Decimal
from math import floor
from typing import Any

import httpx

from kelly.history_store import SqliteHistoryStore
from kelly.services.fx_service import FxError, convert
from kelly.services.profile_service import get_profile

# pence-per-Avios recommendation thresholds. Redemptions worth < 1.0p/Avios are
# poor value (you're effectively buying Avios above their typical earn cost) →
# pay cash and keep the points. Above ~1.5p/Avios the redemption beats the cash
# price comfortably → use Avios. In between, it's a wash ("equivalent").
_CASH_THRESHOLD_PENCE = Decimal("1.0")
_AVIOS_THRESHOLD_PENCE = Decimal("1.5")

# Default HSBC Reward points → Avios conversion ratio (points per 1 Avios).
_DEFAULT_HSBC_TO_AVIOS = 2.0


def _convert(
    amount: Decimal,
    from_ccy: str,
    to_ccy: str,
    *,
    store: SqliteHistoryStore | None,
    client: httpx.Client | None,
    warnings: list[str],
) -> Decimal:
    """Convert *amount* to *to_ccy*; on FX failure append a warning and return the
    amount unchanged (best-effort — never raises)."""
    if from_ccy.upper() == to_ccy.upper():
        return amount
    try:
        return convert(amount, from_ccy, to_ccy, store=store, client=client)
    except (FxError, httpx.HTTPError) as e:
        warnings.append(f"FX {from_ccy.upper()}→{to_ccy.upper()} failed ({e}); used raw amount")
        return amount


def compute_avios_value(
    cash_amount: Decimal | float | str,
    cash_currency: str,
    avios_points: int,
    avios_taxes_amount: Decimal | float | str = 0,
    avios_taxes_currency: str = "GBP",
    target_currency: str = "GBP",
    *,
    store: SqliteHistoryStore | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Compute the cash value of an Avios redemption and recommend cash vs Avios.

    ``pence_per_avios = (cash_in_target - taxes_in_target) / avios_points * 100``.
    The recommendation is ``"cash"`` below 1.0p/Avios, ``"avios"`` above
    ~1.5p/Avios, and ``"equivalent"`` in between (see module thresholds).

    Returns ``{cash, avios, value_metrics:{pence_per_avios, recommendation},
    warnings}``. Guards ``avios_points <= 0`` (returns an ``error``).
    """
    warnings: list[str] = []
    target = target_currency.upper()

    if avios_points <= 0:
        return {"error": "avios_points must be > 0 to compute pence-per-Avios"}

    cash = Decimal(str(cash_amount))
    taxes = Decimal(str(avios_taxes_amount))

    cash_in_target = _convert(
        cash, cash_currency, target, store=store, client=client, warnings=warnings
    )
    taxes_in_target = _convert(
        taxes, avios_taxes_currency, target, store=store, client=client, warnings=warnings
    )

    # Value the points get you = cash you'd otherwise pay, net of the cash taxes
    # you still pay on the award. In pence, per Avios.
    net_value = cash_in_target - taxes_in_target
    pence_per_avios = (net_value / Decimal(avios_points)) * Decimal(100)

    if pence_per_avios < _CASH_THRESHOLD_PENCE:
        recommendation = "cash"
    elif pence_per_avios > _AVIOS_THRESHOLD_PENCE:
        recommendation = "avios"
    else:
        recommendation = "equivalent"

    return {
        "cash": {
            "amount": str(cash),
            "currency": cash_currency.upper(),
            "amount_in_target": str(cash_in_target),
            "target_currency": target,
        },
        "avios": {
            "points": avios_points,
            "taxes_amount": str(taxes),
            "taxes_currency": avios_taxes_currency.upper(),
            "taxes_in_target": str(taxes_in_target),
        },
        "value_metrics": {
            "pence_per_avios": str(pence_per_avios.quantize(Decimal("0.01"))),
            "recommendation": recommendation,
        },
        "warnings": warnings,
    }


def _balances_from_profile(
    profile_id: str,
    *,
    store: SqliteHistoryStore | None,
) -> dict[str, Any]:
    """Read ``payload_json["points_balances"]`` from a stored profile, or {}."""
    prof = get_profile(profile_id, store=store)
    if "error" in prof:
        return {}
    raw = prof.get("payload_json")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    balances = payload.get("points_balances")
    return balances if isinstance(balances, dict) else {}


def check_award_coverage(
    avios_needed: int,
    *,
    profile_id: str | None = None,
    avios: int = 0,
    hsbc_points: int = 0,
    hsbc_to_avios: float = _DEFAULT_HSBC_TO_AVIOS,
    transfer_bonus_pct: float = 0,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Check whether *avios_needed* can be covered from Avios + convertible HSBC.

    If ``profile_id`` is given, balances are read from the profile's
    ``payload_json["points_balances"]`` (``avios``, ``hsbc``, and
    ``conversions.hsbc_to_avios``); explicit args override / fill any gaps.

    ``hsbc_as_avios = floor(hsbc_points / hsbc_to_avios * (1 + transfer_bonus_pct/100))``.
    ``total = avios + hsbc_as_avios``. Returns ``{avios_needed, available:{...},
    can_cover, shortfall}``.
    """
    if profile_id:
        stored = _balances_from_profile(profile_id, store=store)
        if stored:
            if not avios:
                avios = int(stored.get("avios") or 0)
            if not hsbc_points:
                hsbc_points = int(stored.get("hsbc") or 0)
            conversions = stored.get("conversions") or {}
            if hsbc_to_avios == _DEFAULT_HSBC_TO_AVIOS and conversions.get("hsbc_to_avios"):
                hsbc_to_avios = float(conversions["hsbc_to_avios"])

    if hsbc_to_avios <= 0:
        hsbc_as_avios = 0
    else:
        hsbc_as_avios = floor(hsbc_points / hsbc_to_avios * (1 + transfer_bonus_pct / 100))

    total = avios + hsbc_as_avios
    can_cover = total >= avios_needed
    shortfall = max(0, avios_needed - total)

    return {
        "avios_needed": avios_needed,
        "available": {
            "avios": avios,
            "hsbc_points": hsbc_points,
            "hsbc_to_avios": hsbc_to_avios,
            "transfer_bonus_pct": transfer_bonus_pct,
            "hsbc_as_avios": hsbc_as_avios,
            "total": total,
        },
        "can_cover": can_cover,
        "shortfall": shortfall,
    }


def set_points_balances(
    profile_id: str,
    avios: int,
    hsbc: int = 0,
    hsbc_to_avios: float = _DEFAULT_HSBC_TO_AVIOS,
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Merge a ``points_balances`` block into the profile's ``payload_json``.

    Reads the existing profile (preserving the rest of its payload), sets
    ``points_balances = {avios, hsbc, conversions:{hsbc_to_avios}, updated_at}``,
    and saves via ``profile_service.set_profile``. Returns the stored profile or
    ``{"error": ...}``.
    """
    from datetime import datetime, timezone

    from kelly.services.profile_service import set_profile

    if not profile_id or not profile_id.strip():
        return {"error": "profile_id is required"}

    prof = get_profile(profile_id, store=store)
    name = prof.get("name") if "error" not in prof else None
    payload: dict[str, Any] = {}
    if "error" not in prof and prof.get("payload_json"):
        try:
            loaded = json.loads(prof["payload_json"])
            if isinstance(loaded, dict):
                payload = loaded
        except (json.JSONDecodeError, TypeError):
            payload = {}

    payload["points_balances"] = {
        "avios": int(avios),
        "hsbc": int(hsbc),
        "conversions": {"hsbc_to_avios": float(hsbc_to_avios)},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    return set_profile(
        profile_id,
        name or "",
        json.dumps(payload),
        store=store,
    )
