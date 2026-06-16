"""Unified multi-modal transport shape + a constraint-solver for itineraries.

This is the E3 layer: it normalises the two MVP search modes — BA award flights
(Avios, from ``award_flight_service``) and Eurostar trains (cash, from
``train_service``) — into a single :class:`TransportOption`, then pairs an
outbound option with a return option into ranked :class:`Itinerary` objects.

The solver reproduces, deterministically, the by-hand planning result:
Thursday/Friday-out → Sunday-return weekend pairs, with **open-jaw** support
(e.g. fly into Bari, fly home from Brindisi) because the outbound destination
and the return origin range over the destination set *independently*.

Ranking (``solve_pairings`` sort key), ascending — best first:

1. **All-Avios itineraries first, cheapest Avios.** A pairing where *both* legs
   are priced in Avios sorts ahead of any pairing that involves cash. Within
   the all-Avios group, fewer total Avios wins. Cash-mixed pairings get
   ``+inf`` in this slot so they always sort *after* every all-Avios pairing.
2. **Then by total cash, cheapest first** (``None`` totals sort last). This
   orders the cash itineraries among themselves; all-Avios itineraries share a
   sentinel so this slot doesn't reshuffle them.
3. **Then by combined travel time** (sum of both legs' ``duration_min``;
   unknown durations count as ``+inf``). Shorter total travel breaks ties.

``total_avios`` is set only when *both* legs carry ``price_avios``;
``total_cash`` only when *both* carry ``price_cash`` in the *same* currency
(mixed currency ⇒ ``None`` + a note left to the caller). A mixed avios+cash
pairing (one leg each) leaves both totals ``None`` — the caller decides how to
value it — but it still ranks by whatever single signal is available, after the
all-Avios group, by the cash leg's price then duration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# A sentinel ranking weight that sorts strictly after any real numeric value.
_INF = float("inf")


@dataclass
class TransportOption:
    """One bookable transport leg, normalised across modes (flight | train).

    Exactly one pricing channel is populated per leg in the MVP: award flights
    set ``price_avios`` (and leave ``price_cash``/``currency`` ``None``); Eurostar
    sets ``price_cash``/``currency`` (and leaves ``price_avios`` ``None``).
    """

    mode: str  # "flight" | "train"
    operator: str
    origin: str
    destination: str
    depart_dt: datetime | None
    arrive_dt: datetime | None
    duration_min: int | None
    changes: int
    price_cash: Decimal | None
    currency: str | None
    price_avios: int | None
    avios_taxes_note: str | None
    seats_available: int | None
    deep_link: str | None
    source: str


@dataclass
class Itinerary:
    """An outbound + return pairing with derived totals."""

    out: TransportOption
    back: TransportOption
    nights: int
    is_open_jaw: bool
    total_cash: Decimal | None
    total_avios: int | None
    total_currency: str | None


# --- Normalizers ------------------------------------------------------------


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # seats.aero sometimes returns a trailing 'Z'; normalise it.
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None


def avios_options_from_service(result: Any) -> list[TransportOption]:
    """Map an ``award_flight_service.search_avios`` result to TransportOptions.

    Accepts the ``AwardFlightSearchResult`` (it reads ``result.options``) or a
    bare list of ``AviosFlightOption``. Each option becomes a flight leg priced
    in Avios (``price_cash``/``currency`` left ``None``). ``depart_dt`` /
    ``arrive_dt`` come from the trips()-enriched ISO strings when present; if
    they're absent we fall back to midnight on ``fly_date`` so the day still
    participates in weekday/nights constraints.
    """
    options = getattr(result, "options", result) or []
    out: list[TransportOption] = []
    for o in options:
        depart_dt = _parse_iso_dt(getattr(o, "departs_at", None))
        arrive_dt = _parse_iso_dt(getattr(o, "arrives_at", None))
        fly_date = getattr(o, "fly_date", None)
        if depart_dt is None and isinstance(fly_date, date):
            depart_dt = datetime(fly_date.year, fly_date.month, fly_date.day)
        out.append(
            TransportOption(
                mode="flight",
                operator=getattr(o, "operating_airlines", "") or "BA",
                origin=o.origin,
                destination=o.destination,
                depart_dt=depart_dt,
                arrive_dt=arrive_dt,
                duration_min=getattr(o, "duration_min", None),
                changes=getattr(o, "stops", None) or 0,
                price_cash=None,
                currency=None,
                price_avios=getattr(o, "avios_cost", None),
                avios_taxes_note=getattr(o, "avios_taxes_note", None),
                seats_available=getattr(o, "seats", None),
                deep_link=None,
                source=getattr(o, "partner_source", "seats.aero"),
            )
        )
    return out


def eurostar_options_from_result(result: Any, fly_date: date) -> list[TransportOption]:
    """Map a Eurostar ``EurostarSearchResult`` to TransportOptions for one date.

    Eurostar journeys carry ``depart_time`` / ``arrive_time`` as ``"HH:MM"``
    strings; we combine each with *fly_date* to build ``depart_dt`` / ``arrive_dt``.
    Trains are cash-priced (``price_cash`` set, ``price_avios`` left ``None``);
    ``seats_available`` is unknown for Eurostar listings so it stays ``None``
    (which the solver treats as "keep").
    """
    journeys = getattr(result, "journeys", result) or []
    out: list[TransportOption] = []
    for j in journeys:
        depart_dt = _combine(fly_date, getattr(j, "depart_time", None))
        arrive_dt = _combine(fly_date, getattr(j, "arrive_time", None))
        # Eurostar origin/destination aren't on the journey row; derive from raw
        # if present, else leave empty (the caller's city pair is the source of
        # truth for solver origin/destination matching when it builds the pool).
        raw = getattr(j, "raw", None) or {}
        out.append(
            TransportOption(
                mode="train",
                operator=getattr(j, "operator", None) or "eurostar",
                origin=str(raw.get("origin", "")),
                destination=str(raw.get("destination", "")),
                depart_dt=depart_dt,
                arrive_dt=arrive_dt,
                duration_min=getattr(j, "duration_min", None),
                changes=getattr(j, "changes", None) or 0,
                price_cash=getattr(j, "total_price", None),
                currency=getattr(j, "price_currency", None),
                price_avios=None,
                avios_taxes_note=None,
                seats_available=None,
                deep_link=getattr(j, "deep_link", None),
                source="eurostar",
            )
        )
    return out


def _combine(fly_date: date, hhmm: str | None) -> datetime | None:
    """Combine an ``"HH:MM"`` string with *fly_date* into a datetime."""
    if not hhmm:
        return None
    parts = hhmm.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return datetime(fly_date.year, fly_date.month, fly_date.day, hour, minute)


# --- Solver -----------------------------------------------------------------


def _is_avios(opt: TransportOption) -> bool:
    return opt.price_avios is not None


def _totals(
    out: TransportOption, back: TransportOption
) -> tuple[Decimal | None, int | None, str | None]:
    """Compute (total_cash, total_avios, total_currency) per the documented rule."""
    total_avios: int | None = None
    if out.price_avios is not None and back.price_avios is not None:
        total_avios = out.price_avios + back.price_avios

    total_cash: Decimal | None = None
    total_currency: str | None = None
    if out.price_cash is not None and back.price_cash is not None:
        if out.currency == back.currency:
            total_cash = out.price_cash + back.price_cash
            total_currency = out.currency
        # mixed currency → leave total_cash None (caller must normalise via FX)

    return total_cash, total_avios, total_currency


def _sort_key(it: Itinerary) -> tuple[float, float, float]:
    """Ranking key — see the module docstring. Ascending = best first."""
    both_avios = _is_avios(it.out) and _is_avios(it.back)
    avios_rank = float(it.total_avios) if (both_avios and it.total_avios is not None) else _INF
    cash_rank = float(it.total_cash) if it.total_cash is not None else _INF
    dur_out = it.out.duration_min if it.out.duration_min is not None else None
    dur_back = it.back.duration_min if it.back.duration_min is not None else None
    if dur_out is None or dur_back is None:
        dur_rank = _INF
    else:
        dur_rank = float(dur_out + dur_back)
    return (avios_rank, cash_rank, dur_rank)


def _weekday_ok(opt: TransportOption, days: set[str]) -> bool:
    if not days:
        return True
    if opt.depart_dt is None:
        return False
    return opt.depart_dt.strftime("%a") in days


def _seats_ok(opt: TransportOption, min_seats: int) -> bool:
    # None = unknown ⇒ keep. Only drop when a known count is below the threshold.
    return not (opt.seats_available is not None and opt.seats_available < min_seats)


def solve_pairings(
    out_options: list[TransportOption],
    return_options: list[TransportOption],
    *,
    out_days: set[str],
    return_days: set[str],
    min_nights: int,
    max_nights: int,
    open_jaw: bool,
    min_seats: int,
) -> list[Itinerary]:
    """Pair outbound/return legs into ranked itineraries under constraints.

    * ``out_days`` / ``return_days``: sets of weekday short names ("Mon".."Sun").
      An option qualifies if ``depart_dt.strftime("%a")`` is in the set (empty
      set = no weekday constraint).
    * ``nights = (back.depart_dt.date() - out.depart_dt.date()).days``; kept iff
      ``min_nights <= nights <= max_nights`` **and** the return departs strictly
      after the outbound.
    * ``is_open_jaw = out.destination != back.origin``. When ``open_jaw`` is
      ``False`` we require ``out.destination == back.origin``.
    * ``min_seats``: drop a pairing if *either* leg has a known
      ``seats_available`` below the threshold (``None`` ⇒ unknown ⇒ keep).

    Returns the surviving itineraries sorted by :func:`_sort_key`.
    """
    itineraries: list[Itinerary] = []
    for out in out_options:
        if out.depart_dt is None:
            continue
        if not _weekday_ok(out, out_days):
            continue
        if not _seats_ok(out, min_seats):
            continue
        for back in return_options:
            if back.depart_dt is None:
                continue
            if not _weekday_ok(back, return_days):
                continue
            if not _seats_ok(back, min_seats):
                continue
            if back.depart_dt <= out.depart_dt:
                continue

            is_open_jaw = out.destination != back.origin
            if not open_jaw and is_open_jaw:
                continue

            nights = (back.depart_dt.date() - out.depart_dt.date()).days
            if nights < min_nights or nights > max_nights:
                continue

            total_cash, total_avios, total_currency = _totals(out, back)
            itineraries.append(
                Itinerary(
                    out=out,
                    back=back,
                    nights=nights,
                    is_open_jaw=is_open_jaw,
                    total_cash=total_cash,
                    total_avios=total_avios,
                    total_currency=total_currency,
                )
            )

    itineraries.sort(key=_sort_key)
    return itineraries


# --- JSON shaping -----------------------------------------------------------


def transport_option_to_jsonable(opt: TransportOption) -> dict[str, Any]:
    return {
        "mode": opt.mode,
        "operator": opt.operator,
        "origin": opt.origin,
        "destination": opt.destination,
        "depart_dt": opt.depart_dt.isoformat() if opt.depart_dt else None,
        "arrive_dt": opt.arrive_dt.isoformat() if opt.arrive_dt else None,
        "duration_min": opt.duration_min,
        "changes": opt.changes,
        "price_cash": str(opt.price_cash) if opt.price_cash is not None else None,
        "currency": opt.currency,
        "price_avios": opt.price_avios,
        "avios_taxes_note": opt.avios_taxes_note,
        "seats_available": opt.seats_available,
        "deep_link": opt.deep_link,
        "source": opt.source,
    }


def itinerary_to_jsonable(it: Itinerary) -> dict[str, Any]:
    return {
        "out": transport_option_to_jsonable(it.out),
        "back": transport_option_to_jsonable(it.back),
        "nights": it.nights,
        "is_open_jaw": it.is_open_jaw,
        "total_cash": str(it.total_cash) if it.total_cash is not None else None,
        "total_avios": it.total_avios,
        "total_currency": it.total_currency,
    }
