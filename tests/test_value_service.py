from decimal import Decimal
from unittest.mock import patch

from kelly.history_store import SqliteHistoryStore
from kelly.services.value_service import (
    check_award_coverage,
    compute_avios_value,
    set_points_balances,
)


def test_high_value_recommends_avios():
    # £480 cash, 30k Avios + £30 taxes → (480-30)/30000*100 = 1.5p... push above.
    res = compute_avios_value("480", "GBP", 25000, "30", "GBP", "GBP")
    # (480 - 30) / 25000 * 100 = 1.8p/Avios → avios.
    assert res["value_metrics"]["recommendation"] == "avios"
    assert Decimal(res["value_metrics"]["pence_per_avios"]) > Decimal("1.5")
    assert res["warnings"] == []


def test_low_value_recommends_cash():
    # £200 cash redeemed for a big pile of Avios → poor value.
    res = compute_avios_value("200", "GBP", 30000, "0", "GBP", "GBP")
    # 200 / 30000 * 100 = 0.667p/Avios → cash.
    assert res["value_metrics"]["recommendation"] == "cash"
    assert Decimal(res["value_metrics"]["pence_per_avios"]) < Decimal("1.0")


def test_equivalent_band():
    # ~1.2p/Avios → equivalent.
    res = compute_avios_value("360", "GBP", 30000, "0", "GBP", "GBP")
    assert res["value_metrics"]["recommendation"] == "equivalent"


def test_zero_avios_guard():
    res = compute_avios_value("480", "GBP", 0, "0", "GBP", "GBP")
    assert "error" in res


@patch("kelly.services.value_service.convert", return_value=Decimal("400"))
def test_fx_conversion_used_for_non_target_currency(mock_convert):
    # Cash in EUR; converted to GBP via the (mocked) FX service.
    res = compute_avios_value("480", "EUR", 25000, "0", "GBP", "GBP")
    assert mock_convert.called
    assert res["cash"]["amount_in_target"] == "400"


def test_coverage_covered():
    res = check_award_coverage(100000, avios=82000, hsbc_points=53000, hsbc_to_avios=2.0)
    # 53000 / 2 = 26500; total 108500 >= 100000.
    assert res["available"]["hsbc_as_avios"] == 26500
    assert res["available"]["total"] == 108500
    assert res["can_cover"] is True
    assert res["shortfall"] == 0


def test_coverage_short_replicates_brazil():
    # The real Brazil-Easter calc: 82k Avios + 53k HSBC @2:1 = 108.5k vs 308k.
    res = check_award_coverage(308000, avios=82000, hsbc_points=53000, hsbc_to_avios=2.0)
    assert res["available"]["hsbc_as_avios"] == 26500
    assert res["available"]["total"] == 108500
    assert res["can_cover"] is False
    assert res["shortfall"] == 199500


def test_coverage_reads_profile_balances(tmp_path):
    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    set_points_balances("albinati", 82000, 53000, 2.0, store=store)
    res = check_award_coverage(308000, profile_id="albinati", store=store)
    assert res["available"]["avios"] == 82000
    assert res["available"]["hsbc_points"] == 53000
    assert res["available"]["total"] == 108500
    assert res["shortfall"] == 199500


def test_set_points_balances_preserves_existing_payload(tmp_path):
    from kelly.services.profile_service import set_profile

    store = SqliteHistoryStore(tmp_path / "h.sqlite")
    set_profile("albinati", "Luis", '{"home_airports": ["LHR", "LGW"]}', store=store)
    stored = set_points_balances("albinati", 82000, 53000, 2.0, store=store)
    import json

    payload = json.loads(stored["payload_json"])
    # Existing payload preserved...
    assert payload["home_airports"] == ["LHR", "LGW"]
    # ...and the balances merged in.
    assert payload["points_balances"]["avios"] == 82000
    assert payload["points_balances"]["hsbc"] == 53000
    assert payload["points_balances"]["conversions"]["hsbc_to_avios"] == 2.0


def test_transfer_bonus_applied():
    # 53000 / 2 * 1.3 = 34450.
    res = check_award_coverage(
        100000, avios=0, hsbc_points=53000, hsbc_to_avios=2.0, transfer_bonus_pct=30
    )
    assert res["available"]["hsbc_as_avios"] == 34450
