"""Tests for hosting_service.estimate_hosting — pure math + FX path."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from kelly.md_config import DaytripRow, HostingRow, KellyConfig, KellyFrontmatter
from kelly.services.hosting_service import estimate_hosting


def _cfg(
    hosting: list[HostingRow] | None = None,
    daytrips: list[DaytripRow] | None = None,
) -> KellyConfig:
    return KellyConfig(
        frontmatter=KellyFrontmatter(),
        trains=[],
        stays=[],
        hosting=hosting or [],
        daytrips=daytrips or [],
        raw_markdown="",
    )


def _parents_hosting() -> HostingRow:
    """Matches the actual scenario from the user's WhatsApp thread."""
    return HostingRow(
        id="parents-uk-hosting",
        trip_id="parents-uk-2026-08",
        visitor_party="parents",
        visitor_count=2,
        dates_start=date(2026, 8, 7),
        dates_end=date(2026, 8, 30),
        currency="GBP",
        host_baseline_food_per_week=95.0,
        host_baseline_dineout_per_outing=50.0,
        host_baseline_transport_per_day=8.50,
        planned_outings_count=3,
        buffer_per_person=80.0,
        max_total=2200.0,
        notes="24 nights hosting",
    )


def test_unknown_hosting_id_returns_error_with_available_ids() -> None:
    cfg = _cfg(hosting=[_parents_hosting()])
    result = estimate_hosting("does-not-exist", cfg=cfg)
    assert "error" in result
    assert result["available_ids"] == ["parents-uk-hosting"]


def test_estimate_includes_all_components(tmp_path) -> None:
    """No FX needed (same currency); check the formula outputs cleanly."""
    cfg = _cfg(hosting=[_parents_hosting()])
    result = estimate_hosting("parents-uk-hosting", cfg=cfg, host_household_size=3)
    components = result["components"]
    # weeks = 24/7 = 3.428... ; visitor_share = 2/3
    # food_delta = 95 * (2/3) * (24/7) ≈ 217.14
    assert Decimal(components["food_delta"]).quantize(Decimal("0.01")) == Decimal("217.14")
    # dineout_delta = 50 * (2/3) * 3 = 100.00
    assert Decimal(components["dineout_delta"]).quantize(Decimal("0.01")) == Decimal("100.00")
    # transit = 8.50 * 2 * 24 * 0.5 = 204.00
    assert Decimal(components["transit"]).quantize(Decimal("0.01")) == Decimal("204.00")
    # buffer = 80 * 2 = 160.00
    assert Decimal(components["buffer"]).quantize(Decimal("0.01")) == Decimal("160.00")
    # No daytrips registered
    assert Decimal(components["daytrips"]) == Decimal("0.00")


def test_estimate_includes_daytrips() -> None:
    cfg = _cfg(
        hosting=[_parents_hosting()],
        daytrips=[
            DaytripRow(
                id="bath",
                trip_id="parents-uk-2026-08",
                destination="Bath",
                mode="train",
                date=date(2026, 8, 22),
                pax=2,
                est_cost_per_pp=180.0,
                currency="GBP",
                includes_overnight=True,
            ),
            DaytripRow(
                id="tower",
                trip_id="parents-uk-2026-08",
                destination="Tower of London",
                mode="tube",
                date=date(2026, 8, 10),
                pax=2,
                est_cost_per_pp=35.0,
                currency="GBP",
            ),
        ],
    )
    result = estimate_hosting("parents-uk-hosting", cfg=cfg, host_household_size=3)
    components = result["components"]
    # Daytrips: 180*2 + 35*2 = 360 + 70 = 430
    assert Decimal(components["daytrips"]) == Decimal("430.00")
    assert len(result["daytrips"]) == 2
    bath = next(d for d in result["daytrips"] if d["id"] == "bath")
    assert bath["original_amount"] == "360.00"
    assert bath["converted_amount"] == "360.00"


def test_unrelated_daytrips_are_ignored() -> None:
    cfg = _cfg(
        hosting=[_parents_hosting()],
        daytrips=[
            DaytripRow(
                id="other-bath",
                trip_id="some-other-trip",
                destination="Bath",
                mode="train",
                date=date(2026, 8, 22),
                pax=2,
                est_cost_per_pp=180.0,
                currency="GBP",
            ),
        ],
    )
    result = estimate_hosting("parents-uk-hosting", cfg=cfg, host_household_size=3)
    assert result["daytrips"] == []
    assert result["components"]["daytrips"] == "0.00"


def test_per_person_total() -> None:
    cfg = _cfg(hosting=[_parents_hosting()])
    result = estimate_hosting("parents-uk-hosting", cfg=cfg, host_household_size=3)
    estimate = Decimal(result["totals"]["estimate"])
    per_person = Decimal(result["totals"]["per_person"])
    # 2 visitors
    assert (estimate / 2).quantize(Decimal("0.01")) == per_person.quantize(Decimal("0.01"))


def test_over_max_warning_when_estimate_exceeds_cap() -> None:
    row = _parents_hosting()
    row = row.model_copy(update={"max_total": 100.0})  # impossibly low cap
    cfg = _cfg(hosting=[row])
    result = estimate_hosting("parents-uk-hosting", cfg=cfg, host_household_size=3)
    assert result["over_max"] is not None
    assert any("exceeds max_total" in w for w in result["warnings"])


def test_currency_conversion_into_brl() -> None:
    """Convert a £-denominated row into BRL; check FX is applied on each component."""
    cfg = _cfg(hosting=[_parents_hosting()])

    def fake_convert(amount, src, dst, *, store=None):
        # GBP → BRL at fixed test rate of 7.00
        amt = Decimal(str(amount))
        if src.upper() == dst.upper():
            return amt
        assert src.upper() == "GBP" and dst.upper() == "BRL"
        return amt * Decimal("7")

    with patch("kelly.services.hosting_service.fx_convert", side_effect=fake_convert):
        result = estimate_hosting(
            "parents-uk-hosting", cfg=cfg, host_household_size=3, currency="BRL"
        )

    assert result["currency"] == "BRL"
    components = result["components"]
    # Untouched-in-GBP food was £217.14 → £1519.98 in BRL
    assert Decimal(components["food_delta"]).quantize(Decimal("0.01")) == Decimal("1520.00")
    # Whole estimate is 7x the GBP estimate
    gbp_result = estimate_hosting(
        "parents-uk-hosting", cfg=cfg, host_household_size=3, currency="GBP"
    )
    gbp_total = Decimal(gbp_result["totals"]["estimate"])
    brl_total = Decimal(result["totals"]["estimate"])
    assert (brl_total / gbp_total).quantize(Decimal("0.01")) == Decimal("7.00")


def test_fx_failure_warning_keeps_estimate_running() -> None:
    from kelly.services.fx_service import FxError

    cfg = _cfg(hosting=[_parents_hosting()])

    def boom(amount, src, dst, *, store=None):
        if src.upper() == dst.upper():
            return Decimal(str(amount))
        raise FxError("offline")

    with patch("kelly.services.hosting_service.fx_convert", side_effect=boom):
        result = estimate_hosting(
            "parents-uk-hosting", cfg=cfg, host_household_size=3, currency="BRL"
        )

    # 4 base components + max_total cap conversion → 5 warnings (no daytrips)
    assert len([w for w in result["warnings"] if "could not convert" in w]) == 5
    # Estimate still computed (using untouched GBP amounts as fallback)
    assert Decimal(result["totals"]["estimate"]) > Decimal("0")
