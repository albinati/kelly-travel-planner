"""Static British Airways Reward Flight Saver (RFS) pricing chart.

This module is the **single source of truth** for the Avios number Kelly
surfaces. seats.aero only tells us *that* a BA-operated award seat exists on a
date; the Avios price comes from BA's published Reward Flight Saver chart,
which is banded by **great-circle distance** and a peak/off-peak calendar.

The numbers below are BA's economy RFS bands (Avios per person, one way, on
short/medium-haul routes that qualify for RFS). Club Europe (business) is
approximated at ~1.6x the economy figure — BA does not publish a clean
distance chart for Club Europe RFS, so this is a *documented approximation*
flagged for confirmation on BA.com.

Peak vs off-peak follows BA's peak calendar. For the MVP we treat **all of
July and August 2026 as PEAK** — this is approximate (the real BA calendar has
specific peak date ranges) and the returned ``taxes_note`` says so. Always
confirm the exact figure on BA.com before booking.
"""

from __future__ import annotations

from datetime import date

# (max_distance_mi_inclusive, off_peak_avios, peak_avios) for economy RFS.
# A distance maps to the first band whose upper bound it does not exceed.
_ECONOMY_BANDS: list[tuple[int, int, int]] = [
    (650, 4000, 5750),  # Zone 1: <= 650 mi
    (1150, 5000, 6500),  # Zone 2: 651-1150 mi
    (2000, 6500, 7750),  # Zone 3: 1151-2000 mi
    (3000, 10000, 12500),  # Zone 4: 2001-3000 mi
]

# Club Europe (business) is approximated as this multiple of the economy band.
_BUSINESS_MULTIPLIER = 1.6


def _is_business(cabin: str) -> bool:
    return cabin.strip().lower() in {
        "business",
        "j",
        "club",
        "club_europe",
        "club europe",
    }


def is_peak(dt: date) -> bool:
    """BA peak-calendar check (MVP approximation).

    For the MVP, all of July & August 2026 are treated as PEAK. This is an
    approximation of BA's published peak calendar and must be confirmed on
    BA.com before booking.
    """
    return dt.year == 2026 and dt.month in (7, 8)


def _economy_band(distance_mi: int) -> tuple[int, int]:
    """Return (off_peak, peak) economy Avios for *distance_mi*.

    Distances beyond the top published band clamp to the largest band rather
    than erroring — RFS only covers short/medium-haul, so anything bigger is
    out-of-scope and the caller should treat it as a coarse upper bound.
    """
    for upper, off_peak, peak in _ECONOMY_BANDS:
        if distance_mi <= upper:
            return off_peak, peak
    _, off_peak, peak = _ECONOMY_BANDS[-1]
    return off_peak, peak


def avios_cost(distance_mi: int, cabin: str, dt: date) -> tuple[int, str]:
    """Avios per person, one way, for a BA Reward Flight Saver seat.

    Args:
        distance_mi: great-circle distance in statute miles.
        cabin: ``"economy"`` / ``"business"`` (and common synonyms).
        dt: travel date — selects the peak vs off-peak band.

    Returns ``(avios_per_person_one_way, taxes_note)``. The note flags that the
    figure is approximate and that taxes/fees are separate and must be confirmed
    on BA.com.
    """
    off_peak, peak = _economy_band(int(distance_mi))
    peak_flag = is_peak(dt)
    economy_avios = peak if peak_flag else off_peak

    if _is_business(cabin):
        avios = int(round(economy_avios * _BUSINESS_MULTIPLIER))
        cabin_note = "Club Europe ~1.6x economy (approx)"
    else:
        avios = economy_avios
        cabin_note = "economy RFS"

    season = "peak" if peak_flag else "off-peak"
    month_name = dt.strftime("%B")
    taxes_note = (
        f"approx; {cabin_note}; {season} ({month_name}); taxes/fees separate — confirm on BA.com"
    )
    return avios, taxes_note
