from datetime import date

from kelly.services.ba_avios import avios_cost, is_peak


def test_zone_boundaries_off_peak():
    off_peak = date(2026, 3, 10)  # March = off-peak in the MVP calendar
    assert avios_cost(650, "economy", off_peak)[0] == 4000  # top of Zone 1
    assert avios_cost(651, "economy", off_peak)[0] == 5000  # bottom of Zone 2
    assert avios_cost(1150, "economy", off_peak)[0] == 5000  # top of Zone 2
    assert avios_cost(1151, "economy", off_peak)[0] == 6500  # bottom of Zone 3
    assert avios_cost(2000, "economy", off_peak)[0] == 6500  # top of Zone 3
    assert avios_cost(2001, "economy", off_peak)[0] == 10000  # bottom of Zone 4
    assert avios_cost(3000, "economy", off_peak)[0] == 10000  # top of Zone 4


def test_zone_boundaries_peak():
    peak = date(2026, 7, 15)
    assert avios_cost(650, "economy", peak)[0] == 5750
    assert avios_cost(1150, "economy", peak)[0] == 6500
    assert avios_cost(2000, "economy", peak)[0] == 7750
    assert avios_cost(3000, "economy", peak)[0] == 12500


def test_july_and_august_are_peak():
    assert is_peak(date(2026, 7, 1)) is True
    assert is_peak(date(2026, 7, 31)) is True
    assert is_peak(date(2026, 8, 15)) is True
    assert is_peak(date(2026, 6, 30)) is False
    assert is_peak(date(2026, 9, 1)) is False
    # Approximation is year-scoped — other years' July is not auto-peak here.
    assert is_peak(date(2025, 7, 15)) is False


def test_peak_selection_flips_july():
    # Same distance, off-peak vs peak month → different Avios.
    off = avios_cost(1000, "economy", date(2026, 3, 10))[0]
    peak = avios_cost(1000, "economy", date(2026, 7, 10))[0]
    assert off == 5000
    assert peak == 6500
    assert peak > off


def test_business_is_approx_1_6x_economy():
    eco, _ = avios_cost(1000, "economy", date(2026, 7, 10))
    biz, note = avios_cost(1000, "business", date(2026, 7, 10))
    assert biz == round(eco * 1.6)
    assert "Club Europe" in note


def test_taxes_note_flags_peak_and_confirmation():
    _, note = avios_cost(800, "economy", date(2026, 7, 10))
    assert "peak" in note
    assert "July" in note
    assert "BA.com" in note


def test_oversize_distance_clamps_to_top_band():
    # RFS only covers short/medium-haul; anything bigger clamps to Zone 4.
    assert avios_cost(9999, "economy", date(2026, 3, 10))[0] == 10000
