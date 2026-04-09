"""Apply travel policy and match watchlist rows to candidates."""

from __future__ import annotations

from typing import Any

from kelly.md_config import KellyConfig, TravelPolicy


def _max_stops_from_itinerary(details: list[dict[str, Any]]) -> int:
    if not details:
        return 0
    return max(int(leg.get("stops") or 0) for leg in details)


def apply_travel_policy(
    candidates: list[dict[str, Any]],
    policy: TravelPolicy | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter *candidates* with optional ``itinerary_details`` (Kelly leg dicts)."""
    if policy is None:
        return list(candidates), []

    max_stops_allowed = 0 if policy.direct_only else policy.max_stops
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for c in candidates:
        details = c.get("itinerary_details") or []
        st = _max_stops_from_itinerary(details)
        if st > max_stops_allowed:
            rejected.append(
                {
                    **c,
                    "_policy_reject": f"stops {st} > max_stops {max_stops_allowed}",
                }
            )
        else:
            kept.append(c)

    return kept, rejected


def explain_rejection(candidate: dict[str, Any], policy: TravelPolicy | None) -> dict[str, Any]:
    if policy is None:
        return {"allowed": True, "reasons": []}
    max_stops_allowed = 0 if policy.direct_only else policy.max_stops
    details = candidate.get("itinerary_details") or []
    st = _max_stops_from_itinerary(details)
    reasons: list[str] = []
    if policy.direct_only and st > 0:
        reasons.append(f"direct_only requires 0 stops, got {st}")
    elif st > max_stops_allowed:
        reasons.append(f"max_stops={max_stops_allowed}, itinerary implies {st}")
    if policy.baggage.strip():
        reasons.append(f"baggage policy note not auto-verified: {policy.baggage!r}")
    return {"allowed": len(reasons) == 0, "reasons": reasons, "max_stops_seen": st}


def match_watchlist(
    cfg: KellyConfig,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    *candidates* need keys: origin_iata, destination_iata, departure_date (ISO), optional best_total_amount.
    """
    triggers: list[dict[str, Any]] = []
    for row in cfg.planned:
        for c in candidates:
            o = str(c.get("origin_iata", "")).upper()
            d = str(c.get("destination_iata", "")).upper()
            if o != row.origin_iata or d != row.destination_iata:
                continue
            dep_s = c.get("departure_date")
            if not dep_s:
                continue
            from datetime import date as date_cls

            try:
                dep = date_cls.fromisoformat(str(dep_s)[:10])
            except ValueError:
                continue
            if not (row.date_start <= dep <= row.date_end):
                continue
            price = c.get("best_total_amount")
            try:
                pf = float(price) if price is not None else None
            except (TypeError, ValueError):
                pf = None
            hit_price = row.target_price is not None and pf is not None and pf <= row.target_price
            triggers.append(
                {
                    "watchlist_row_id": row.id,
                    "candidate": c,
                    "trigger_target_price": hit_price,
                    "target_price": row.target_price,
                }
            )
    return triggers
