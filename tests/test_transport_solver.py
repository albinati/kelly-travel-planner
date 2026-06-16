"""Unit tests for the E3 constraint-solver (``solve_pairings``).

All TransportOptions here are hand-built — the solver is pure and never touches
a provider. We cover: weekend-shape matching, return-day rejection, nights
window, min-seats filter, open-jaw (found when allowed, rejected when not), and
the documented ranking order (all-Avios cheapest first, then cash).
"""

from datetime import datetime
from decimal import Decimal

from kelly.services.transport_service import TransportOption, solve_pairings


def _flight(
    origin,
    destination,
    depart_dt,
    *,
    avios=None,
    seats=None,
    cash=None,
    currency=None,
    duration=120,
    arrive_dt=None,
):
    return TransportOption(
        mode="flight" if avios is not None else "train",
        operator="BA",
        origin=origin,
        destination=destination,
        depart_dt=depart_dt,
        arrive_dt=arrive_dt or depart_dt,
        duration_min=duration,
        changes=0,
        price_cash=Decimal(cash) if cash is not None else None,
        currency=currency,
        price_avios=avios,
        avios_taxes_note=None,
        seats_available=seats,
        deep_link=None,
        source="test",
    )


# A Thursday + Friday outbound, a Sunday return in July 2026.
THU = datetime(2026, 7, 16, 8, 0)  # Thu
FRI = datetime(2026, 7, 17, 8, 0)  # Fri
SUN = datetime(2026, 7, 19, 18, 0)  # Sun (3 nights from Thu, 2 from Fri)
MON = datetime(2026, 7, 20, 18, 0)  # Mon

DEFAULTS = dict(
    out_days={"Thu", "Fri"},
    return_days={"Sun"},
    min_nights=2,
    max_nights=3,
    open_jaw=True,
    min_seats=2,
)


def test_thu_to_sun_three_night_pass():
    out = [_flight("LHR", "CTA", THU, avios=7750, seats=4)]
    back = [_flight("CTA", "LHR", SUN, avios=7750, seats=4)]
    res = solve_pairings(out, back, **DEFAULTS)
    assert len(res) == 1
    it = res[0]
    assert it.nights == 3
    assert it.is_open_jaw is False
    assert it.total_avios == 15500


def test_monday_return_rejected_by_return_days():
    out = [_flight("LHR", "CTA", THU, avios=7750, seats=4)]
    back = [_flight("CTA", "LHR", MON, avios=7750, seats=4)]
    res = solve_pairings(out, back, **DEFAULTS)
    assert res == []


def test_nights_out_of_range_rejected():
    # Fri-out → Sun-return is only 2 nights (OK); force a 1-night window to reject.
    out = [_flight("LHR", "CTA", FRI, avios=7750, seats=4)]
    back = [_flight("CTA", "LHR", SUN, avios=7750, seats=4)]
    opts = {**DEFAULTS, "min_nights": 3, "max_nights": 3}
    res = solve_pairings(out, back, **opts)
    assert res == []  # only 2 nights, below min_nights=3


def test_min_seats_filter_drops_low_seat_leg():
    out = [_flight("LHR", "CTA", THU, avios=7750, seats=1)]  # only 1 seat
    back = [_flight("CTA", "LHR", SUN, avios=7750, seats=4)]
    res = solve_pairings(out, back, **DEFAULTS)
    assert res == []  # min_seats=2 drops the 1-seat outbound


def test_unknown_seats_kept():
    # seats_available=None (e.g. Eurostar) must NOT be dropped by min_seats.
    out = [_flight("LON", "PAR", THU, cash="100", currency="£", seats=None)]
    back = [_flight("PAR", "LON", SUN, cash="100", currency="£", seats=None)]
    res = solve_pairings(out, back, **DEFAULTS)
    assert len(res) == 1
    assert res[0].total_cash == Decimal("200")


def test_open_jaw_found_when_allowed():
    # Fly into Bari, fly home from Brindisi — a real open-jaw.
    out = [_flight("LGW", "BRI", THU, avios=6500, seats=4)]  # Bari
    back = [_flight("BDS", "LGW", SUN, avios=6500, seats=4)]  # Brindisi
    res = solve_pairings(out, back, **DEFAULTS)
    assert len(res) == 1
    assert res[0].is_open_jaw is True
    assert res[0].out.destination == "BRI"
    assert res[0].back.origin == "BDS"


def test_open_jaw_rejected_when_disallowed():
    out = [_flight("LGW", "BRI", THU, avios=6500, seats=4)]
    back = [_flight("BDS", "LGW", SUN, avios=6500, seats=4)]
    opts = {**DEFAULTS, "open_jaw": False}
    res = solve_pairings(out, back, **opts)
    assert res == []  # destination != origin and open_jaw off → rejected


def test_ranking_all_avios_cheapest_first_then_cash():
    # Three closed-jaw pairings to *distinct* destinations so only the intended
    # out↔back pairs are non-open-jaw:
    #   A: all-Avios 16000 (CTA), B: all-Avios 14000 (FCO), C: all-cash £300 (BCN).
    out = [
        _flight("LHR", "CTA", THU, avios=8000, seats=4, duration=120),  # A out
        _flight("LGW", "FCO", THU, avios=7000, seats=4, duration=120),  # B out
        _flight("STN", "BCN", THU, cash="150", currency="£", seats=None, duration=120),  # C out
    ]
    back = [
        _flight("CTA", "LHR", SUN, avios=8000, seats=4, duration=120),  # A back
        _flight("FCO", "LGW", SUN, avios=7000, seats=4, duration=120),  # B back
        _flight("BCN", "STN", SUN, cash="150", currency="£", seats=None, duration=120),  # C back
    ]
    res = solve_pairings(out, back, **DEFAULTS)
    closed = [it for it in res if not it.is_open_jaw]
    assert len(closed) == 3
    assert closed[0].total_avios == 14000  # cheapest Avios first
    assert closed[1].total_avios == 16000  # next Avios
    # The cash pairing must come after both all-Avios ones.
    cash_idx = next(i for i, it in enumerate(closed) if it.total_cash is not None)
    assert cash_idx == 2
    assert closed[cash_idx].total_cash == Decimal("300")


def test_ranking_ties_broken_by_duration():
    # Two all-Avios pairings with identical total Avios; shorter combined
    # duration must rank first.
    out = [
        _flight("LHR", "CTA", THU, avios=7000, seats=4, duration=120),  # fast
        _flight("LGW", "FCO", THU, avios=7000, seats=4, duration=300),  # slow
    ]
    back = [
        _flight("CTA", "LHR", SUN, avios=7000, seats=4, duration=120),  # fast
        _flight("FCO", "LGW", SUN, avios=7000, seats=4, duration=300),  # slow
    ]
    res = solve_pairings(out, back, **DEFAULTS)
    closed = [it for it in res if not it.is_open_jaw]
    assert closed[0].out.duration_min + closed[0].back.duration_min == 240
    assert closed[-1].out.duration_min + closed[-1].back.duration_min == 600


def test_return_must_be_after_outbound():
    # A return that departs before the outbound (same nominal weekday set) is
    # rejected even though it technically matches the day filter.
    out = [_flight("LHR", "CTA", SUN, avios=7750, seats=4)]
    back = [_flight("CTA", "LHR", THU, avios=7750, seats=4)]
    opts = {**DEFAULTS, "out_days": {"Sun"}, "return_days": {"Thu"}, "min_nights": 0}
    res = solve_pairings(out, back, **opts)
    assert res == []
