"""MCP server (stdio) for Kelly — group trip planning over Eurostar + Airbnb.

Install with `poetry install --extras mcp`.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:
    raise SystemExit(
        "The `mcp` package is required. Install with: poetry install --extras mcp"
    ) from e

from kelly.history_store import open_default_store
from kelly.md_config import StayRow, TrainRow, load_kelly_config
from kelly.providers.splitwise import (
    SplitwiseError,
    _client as splitwise_client,
    create_expense,
    equal_couple_shares,
    get_current_user,
    get_group,
    get_group_balances,
)
from kelly.services.award_flight_service import (
    award_flight_result_to_jsonable,
    search_avios,
)
from kelly.services.booking_service import booking_total_for_leg, log_booking
from kelly.services.budget_service import compare_options
from kelly.services.fx_service import FxError, convert as fx_convert, fx_quote
from kelly.services.hosting_service import estimate_hosting
from kelly.services.profile_service import (
    add_dream_trip as profile_add_dream_trip,
    get_profile as profile_get_profile,
    list_dream_trips as profile_list_dream_trips,
    remove_dream_trip as profile_remove_dream_trip,
    set_profile as profile_set_profile,
)
from kelly.services.session_service import (
    attach_option as session_attach_option,
    create_session as session_create,
    delete_session as session_delete,
    get_session as session_get,
    list_sessions as session_list,
    update_session as session_update,
)
from kelly.services.summary_service import summarize_trip
from kelly.services.stay_service import search_stay, stay_result_to_jsonable
from kelly.services.train_service import search_train, train_result_to_jsonable
from kelly.services.transport_service import (
    avios_options_from_service,
    eurostar_options_from_result,
    itinerary_to_jsonable,
    solve_pairings,
)
from kelly.services.trip_planner import plan_trip
from kelly.settings import config_path as default_config_path

# --- Splitwise group config -------------------------------------------------
# Read from env so trip-specific identifiers stay out of the repo. Set
# KELLY_SPLITWISE_GROUP_ID (required for expense tools), optionally
# KELLY_SPLITWISE_GROUP_NAME and KELLY_TRIP_PREFIX in .env.

_CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "BRL": "R$", "CAD": "C$"}


def _format_money(amount: float, currency: str) -> str:
    symbol = _CURRENCY_SYMBOLS.get(currency.upper())
    if symbol:
        return f"{symbol}{amount:,.2f}"
    return f"{amount:,.2f} {currency.upper()}"


def _splitwise_group_id() -> int | None:
    raw = os.getenv("KELLY_SPLITWISE_GROUP_ID")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _splitwise_group_name() -> str:
    return os.getenv("KELLY_SPLITWISE_GROUP_NAME", "group")


def _trip_prefix() -> str:
    raw = os.getenv("KELLY_TRIP_PREFIX", "")
    return f"{raw} " if raw and not raw.endswith(" ") else raw


mcp = FastMCP(
    "Kelly",
    instructions=(
        "Kelly plans group trips from a Markdown config — Eurostar trains plus Airbnb stays. "
        "No API keys required: pyairbnb hits Airbnb's internal staysSearch GraphQL and patchright "
        "drives a stealth Chromium against eurostar.com. Searches are persisted to a local SQLite "
        "file (KELLY_DATA_DIR) so we can build per-trip price baselines over time."
    ),
)


def _cfg(p: str | None) -> Path:
    return Path(p).expanduser() if p else default_config_path()


@mcp.tool()
def kelly_load_config(config_path: str | None = None) -> str:
    """Load and validate kelly.md; return JSON summary of trains and stays."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    return json.dumps(
        {
            "frontmatter": cfg.frontmatter.model_dump(mode="json"),
            "trains": [t.model_dump(mode="json", by_alias=True) for t in cfg.trains],
            "stays": [s.model_dump(mode="json") for s in cfg.stays],
        },
        default=str,
    )


@mcp.tool()
def kelly_eurostar_search(
    origin_city: str,
    destination_city: str,
    date_start: str,
    date_end: str,
    adults: int,
    seniors: int = 0,
    teens: int = 0,
    children_ages: str = "",
    class_: str = "standard",
    config_path: str | None = None,
    persist: bool = True,
) -> str:
    """One-shot Eurostar search (via patchright). children_ages: comma-separated ints (e.g. '3,4,5')."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    ages = [int(x.strip()) for x in children_ages.split(",") if x.strip()]
    row = TrainRow(
        id="__adhoc__",
        operator="eurostar",
        origin_city=origin_city,
        destination_city=destination_city,
        date_start=date.fromisoformat(date_start),
        date_end=date.fromisoformat(date_end),
        **{"class": class_},
        adults=adults,
        seniors=seniors,
        teens=teens,
        children_ages=ages,
    )
    store = open_default_store() if persist else None
    return json.dumps(
        train_result_to_jsonable(search_train(cfg, row, store=store, persist=persist)),
        default=str,
    )


@mcp.tool()
def kelly_airbnb_search(
    area: str,
    check_in: str,
    check_out: str,
    adults: int,
    children_ages: str = "",
    bedrooms_min: int = 1,
    near: str = "",
    max_total: float | None = None,
    config_path: str | None = None,
    persist: bool = True,
) -> str:
    """One-shot Airbnb search (via pyairbnb). children_ages / near: comma-separated strings."""
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    ages = [int(x.strip()) for x in children_ages.split(",") if x.strip()]
    near_list = [x.strip() for x in near.split(",") if x.strip()]
    row = StayRow(
        id="__adhoc__",
        area=area,
        check_in=date.fromisoformat(check_in),
        check_out=date.fromisoformat(check_out),
        adults=adults,
        children_ages=ages,
        bedrooms_min=bedrooms_min,
        near=near_list,
        max_total=max_total,
    )
    store = open_default_store() if persist else None
    return json.dumps(
        stay_result_to_jsonable(search_stay(cfg, row, store=store, persist=persist)),
        default=str,
    )


@mcp.tool()
def kelly_plan_trip(trip_id: str, config_path: str | None = None, persist: bool = True) -> str:
    """Plan a group trip declared in kelly.md (## Trains / ## Stays sections).

    Looks up trains `<trip_id>-out` and `<trip_id>-back` (or a single `<trip_id>` train)
    plus the stay with id `<trip_id>`; returns combined JSON with search results,
    a curated shortlist, and Eurostar-Groups Desk hints for parties of 10+.
    """
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    store = open_default_store() if persist else None
    return json.dumps(plan_trip(cfg, trip_id, store=store, persist=persist), default=str)


@mcp.tool()
def kelly_avios_search(
    origin_airports: str,
    destination_airports: str,
    date_start: str,
    date_end: str,
    pax: int,
    cabin: str = "economy",
    only_direct: bool = True,
    persist: bool = True,
) -> str:
    """Search BA-operated award-flight availability over a date window.

    Uses seats.aero as an availability *radar* (it indexes oneworld partner
    award space) and keeps only options that are BA-operated and bookable with
    Avios. For each it returns remaining seats, real depart/arrive times (when
    available), and the **Avios cost from BA's Reward Flight Saver chart** (banded
    by great-circle distance + a peak calendar).

    IMPORTANT: the ``partner_mileage_cost`` field is the partner program's miles
    (e.g. AAdvantage), NOT the Avios price. The Avios number is ``avios_cost``,
    sourced only from the BA chart; ``avios_taxes_note`` flags that it's approximate
    and that taxes/fees are separate — confirm on BA.com before booking.

    *origin_airports* / *destination_airports* are comma-separated IATA codes
    (e.g. "LHR,LGW,LCY" → "CTA"). Requires ``SEATSAERO_API_KEY`` in .env; returns
    ``{"error": "..."}`` JSON if it's missing.
    """
    origins = [c.strip() for c in origin_airports.split(",") if c.strip()]
    dests = [c.strip() for c in destination_airports.split(",") if c.strip()]
    store = open_default_store() if persist else None
    res = search_avios(
        origins,
        dests,
        (date.fromisoformat(date_start), date.fromisoformat(date_end)),
        pax=pax,
        cabin=cabin,
        only_direct=only_direct,
        store=store,
        persist=persist,
    )
    return json.dumps(award_flight_result_to_jsonable(res), default=str)


@mcp.tool()
def kelly_solve_itinerary(
    origin_airports: str,
    destination_airports: str,
    date_start: str,
    date_end: str,
    pax: int,
    out_days: str = "Thu,Fri",
    return_days: str = "Sun",
    min_nights: int = 2,
    max_nights: int = 3,
    open_jaw: bool = True,
    min_seats: int = 2,
    modes: str = "avios",
    persist: bool = True,
) -> str:
    """Search transport for a weekend window and return ranked itineraries.

    Pairs an outbound leg with a return leg under weekend-shape constraints —
    *out_days* / *return_days* are comma-separated weekday short names
    ("Thu,Fri" → "Sun"), nights kept within ``[min_nights, max_nights]``. Set
    ``open_jaw=True`` (default) to allow the return to depart from a *different*
    airport in ``destination_airports`` (this is how Bari-in / Brindisi-out is
    found): the outbound destination and the return origin range over the whole
    destination set independently.

    ``modes`` is a comma-separated list of ``"avios"`` (BA award flights via
    seats.aero + the BA RFS chart) and/or ``"eurostar"`` (cash trains). Avios
    contributes flight legs priced in Avios; Eurostar contributes cash train
    legs (best-effort — if the route isn't a Eurostar city pair it simply
    contributes nothing). ``min_seats`` drops a pairing only when a leg's *known*
    seat count is below the threshold (Eurostar seats are unknown ⇒ kept).

    Ranking (best first): all-Avios itineraries first by cheapest total Avios,
    then cash itineraries by cheapest total cash, ties broken by shortest combined
    travel time. The returned list is capped at the top 25 (``"capped"`` flags it).

    Returns JSON ``{itineraries: [...], count, capped, notes: [...]}`` or
    ``{"error": "..."}`` (e.g. ``SEATSAERO_API_KEY`` missing for avios mode).
    """
    origins = [c.strip() for c in origin_airports.split(",") if c.strip()]
    dests = [c.strip() for c in destination_airports.split(",") if c.strip()]
    out_day_set = {d.strip() for d in out_days.split(",") if d.strip()}
    return_day_set = {d.strip() for d in return_days.split(",") if d.strip()}
    mode_set = {m.strip().lower() for m in modes.split(",") if m.strip()}
    start = date.fromisoformat(date_start)
    end = date.fromisoformat(date_end)
    store = open_default_store() if persist else None

    out_pool = []
    return_pool = []
    notes: list[str] = []

    if "avios" in mode_set:
        out_res = search_avios(origins, dests, (start, end), pax=pax, store=store, persist=persist)
        back_res = search_avios(dests, origins, (start, end), pax=pax, store=store, persist=persist)
        if out_res.error:
            return json.dumps({"error": out_res.error})
        if back_res.error:
            return json.dumps({"error": back_res.error})
        out_pool += avios_options_from_service(out_res)
        return_pool += avios_options_from_service(back_res)
        for tag, r in (("out", out_res), ("back", back_res)):
            if r.note:
                notes.append(f"avios {tag}: {r.note}")

    if "eurostar" in mode_set:
        from kelly.providers.playwright_eurostar import search_eurostar

        # Best-effort: scan each origin→dest city pair across the window, one
        # date at a time so depart_dt is dated correctly. Unknown stations / no
        # results just contribute nothing (the provider returns a clean error).
        def _scan(o_codes: list[str], d_codes: list[str], pool: list) -> None:
            cur = start
            while cur <= end:
                for o in o_codes:
                    for d in d_codes:
                        res = search_eurostar(
                            origin_city=o,
                            destination_city=d,
                            date_start=cur,
                            date_end=cur,
                            adults=pax,
                        )
                        if res.error:
                            continue
                        for opt in eurostar_options_from_result(res, cur):
                            opt.origin = o
                            opt.destination = d
                            pool.append(opt)
                cur = date.fromordinal(cur.toordinal() + 1)

        _scan(origins, dests, out_pool)
        _scan(dests, origins, return_pool)

    itineraries = solve_pairings(
        out_pool,
        return_pool,
        out_days=out_day_set,
        return_days=return_day_set,
        min_nights=min_nights,
        max_nights=max_nights,
        open_jaw=open_jaw,
        min_seats=min_seats,
    )

    cap = 25
    capped = len(itineraries) > cap
    top = itineraries[:cap]
    return json.dumps(
        {
            "itineraries": [itinerary_to_jsonable(it) for it in top],
            "count": len(top),
            "capped": capped,
            "notes": notes,
        },
        default=str,
    )


@mcp.tool()
def kelly_log_expense(
    description: str,
    amount: str,
    currency: str = "GBP",
    paid_by_me: bool = True,
) -> str:
    """Log a shared expense to the configured Splitwise group, splitting equally
    between all members.

    The trip prefix from ``KELLY_TRIP_PREFIX`` (if set) is prepended to the
    *description*, which should be the receipt-level detail. Amount is a string
    to avoid float precision issues; pass it like ``"25.00"``.

    Returns JSON with the created expense id, currency, total, and per-user
    share. Returns ``{"error": "..."}`` if SPLITWISE_API_KEY or
    KELLY_SPLITWISE_GROUP_ID is not configured.
    """
    if not paid_by_me:
        return json.dumps(
            {
                "error": (
                    "paid_by_me=False not supported in MVP — the agent that called this "
                    "tool is currently mapped to the SPLITWISE_API_KEY owner. To log an "
                    "expense paid by someone else, ask that person to log it from their "
                    "own Splitwise."
                )
            }
        )
    group_id = _splitwise_group_id()
    if group_id is None:
        return json.dumps({"error": "KELLY_SPLITWISE_GROUP_ID is not set"})
    try:
        with splitwise_client() as client:
            me = get_current_user(client)
            group = get_group(client, group_id)
            member_ids = [int(m["id"]) for m in (group.get("members") or [])]
            if not member_ids:
                return json.dumps({"error": f"group {group_id} has no members"})
            shares = equal_couple_shares(amount, member_ids)
            full_desc = f"{_trip_prefix()}{description}"
            expense = create_expense(
                client,
                group_id=group_id,
                description=full_desc,
                cost=amount,
                currency_code=currency,
                paid_by_user_id=me.id,
                shares=shares,
            )
    except SplitwiseError as e:
        return json.dumps({"error": str(e)})

    return json.dumps(
        {
            "expense_id": expense.id,
            "group": _splitwise_group_name(),
            "description": expense.description,
            "cost": str(expense.cost),
            "currency": expense.currency_code,
            "paid_by": me.display_name,
            "shares": {str(uid): str(amt) for uid, amt in shares.items()},
        },
        default=str,
    )


@mcp.tool()
def kelly_expense_balances() -> str:
    """Read current balances in the configured Splitwise group.

    Returns JSON with each member's per-currency balance and Splitwise's
    minimum-transfer settle-up plan (``simplified_debts``). Positive balances
    mean the member is owed money; negative means they owe.

    Returns ``{"error": "..."}`` if SPLITWISE_API_KEY or
    KELLY_SPLITWISE_GROUP_ID is not configured.
    """
    group_id = _splitwise_group_id()
    if group_id is None:
        return json.dumps({"error": "KELLY_SPLITWISE_GROUP_ID is not set"})
    try:
        with splitwise_client() as client:
            me = get_current_user(client)
            balances = get_group_balances(client, group_id)
    except SplitwiseError as e:
        return json.dumps({"error": str(e)})

    # Relativize the simplified_debts for the calling user's perspective so the
    # agent can phrase it naturally ("X owes you £Y" vs "You owe X").
    members_by_id = {m["id"]: m for m in balances["members"]}
    me_view: list[dict[str, str]] = []
    for d in balances["simplified_debts"]:
        from_id = int(d["from"])
        to_id = int(d["to"])
        amount = d["amount"]
        cur = d["currency_code"]
        if from_id == me.id:
            other = members_by_id.get(to_id, {}).get("name", f"user_{to_id}")
            me_view.append({"direction": "you_owe", "to": other, "amount": amount, "currency": cur})
        elif to_id == me.id:
            other = members_by_id.get(from_id, {}).get("name", f"user_{from_id}")
            me_view.append(
                {"direction": "owed_to_you", "from": other, "amount": amount, "currency": cur}
            )

    return json.dumps(
        {
            "group": _splitwise_group_name(),
            "group_id": balances["group_id"],
            "me": me.display_name,
            "members": balances["members"],
            "simplified_debts": balances["simplified_debts"],
            "your_perspective": me_view,
        },
        default=str,
    )


# --- FX conversion ----------------------------------------------------------


@mcp.tool()
def kelly_convert(
    amount: str,
    from_ccy: str,
    to_ccy: str,
    as_of: str | None = None,
) -> str:
    """Convert *amount* from one ISO 4217 currency to another via cached ECB rates.

    ``amount`` is a string (pass ``"1219.14"``) to preserve Decimal precision.
    ``as_of`` is an optional ISO date — defaults to today UTC. If today's
    rates aren't cached locally, the ECB daily feed is fetched once and
    persisted; subsequent calls reuse the cache.

    Returns JSON ``{from_ccy, to_ccy, amount, converted, rate, as_of, source}``
    or ``{"error": "..."}`` if no cache + network failure prevents conversion.
    """
    try:
        converted = fx_convert(amount, from_ccy, to_ccy, as_of=as_of)
    except FxError as e:
        return json.dumps({"error": str(e)})
    quote = fx_quote(from_ccy, to_ccy, as_of=as_of)
    return json.dumps(
        {
            "amount": str(amount),
            "from_ccy": from_ccy.upper(),
            "to_ccy": to_ccy.upper(),
            "converted": f"{converted:.4f}",
            "rate": quote.get("rate"),
            "as_of": quote.get("as_of"),
            "source": quote.get("source"),
        },
        default=str,
    )


# --- Budget comparison ------------------------------------------------------


@mcp.tool()
def kelly_budget_compare(options_json: str, target_currency: str = "GBP") -> str:
    """Compare N candidate trip options by their **all-in** total and rank them.

    ``options_json`` is a JSON string of a list of options. Each option::

        {
          "name": "Option A",
          "lines": [
            {"label": "Eurostar", "amount": "180", "currency": "GBP",
             "kind": "transport"},
            {"label": "Airbnb 3n", "amount": "0", "currency": "EUR",
             "kind": "stay", "price_all_in": "640"}
          ],
          "avios": {"points": 30000, "taxes_amount": "120",
                    "taxes_currency": "GBP"}
        }

    Line ``kind`` is one of ``transport|stay|experience|car|local|other``.
    **All-in rule:** stay lines must already be all-in; when a stay line carries
    ``price_all_in`` it is used and any raw ``price_total`` is ignored (mixing a
    raw provider total with an all-in price is apples-vs-bananas).

    **Avios rule:** ``avios.points`` are summed and reported per option but never
    folded into the cash ``all_in_total`` — only the (converted) Avios cash
    taxes are added to the total.

    Returns JSON ``{target_currency, options[...], ranked, cheapest, deltas}``
    (ranked ascending by all_in_total; deltas vs the cheapest). Per-line FX
    failures count that line at 0 and surface a warning rather than raising.
    Returns ``{"error": "..."}`` if ``options_json`` doesn't parse as a list.
    """
    try:
        options = json.loads(options_json)
    except (json.JSONDecodeError, TypeError) as e:
        return json.dumps({"error": f"options_json is not valid JSON: {e}"})
    if not isinstance(options, list):
        return json.dumps({"error": "options_json must be a JSON array of options"})
    return json.dumps(compare_options(options, target_currency=target_currency), default=str)


# --- Hosting estimator ------------------------------------------------------


@mcp.tool()
def kelly_estimate_hosting(
    hosting_id: str,
    currency: str | None = None,
    host_household_size: int = 2,
    config_path: str | None = None,
) -> str:
    """Estimate the marginal cost of hosting the visiting party `hosting_id`.

    Reads `## Hosting` (and matching `## Daytrips`) from kelly.md, then
    computes food_delta + dineout_delta + transit + daytrips + buffer per
    the formulas documented in `hosting_service.estimate_hosting`.

    `host_household_size` is your own household count (default 2) — used
    for the proportional food/dineout split. `currency` defaults to the
    hosting row's own currency; pass a different one to convert.

    Returns JSON `{components, daytrips, totals, warnings, over_max?}` or
    `{"error": ..., "available_ids": [...]}` when the id isn't found.
    """
    path = _cfg(config_path)
    if not path.is_file():
        return json.dumps({"error": f"config not found: {path}"})
    cfg = load_kelly_config(path)
    return json.dumps(
        estimate_hosting(
            hosting_id,
            cfg=cfg,
            host_household_size=host_household_size,
            currency=currency,
        ),
        default=str,
    )


# --- Trip cost summary ------------------------------------------------------


@mcp.tool()
def kelly_trip_summary(trip_id: str, currency: str = "GBP") -> str:
    """Aggregate every logged booking for *trip_id* into *currency*.

    Reads the bookings table (latest-per-leg) and converts each amount via
    ``kelly_convert`` using ECB rates as_of the booking's ``paid_at`` date.

    Returns JSON ``{trip_id, currency, fx_as_of, by_leg: [...], by_category:
    {trains, stays, tickets, other}, totals: {trip_total}, warnings: [...]}``.
    Per-leg conversion failures land in ``warnings``; the whole tool never
    raises. ``by_leg`` rows always carry the original (untouched) amount in
    its original currency alongside the target conversion.

    Example: ``kelly_trip_summary("my-trip-id", currency="BRL")``
    """
    return json.dumps(summarize_trip(trip_id, currency=currency), default=str)


# --- Bookings + calendar event drafts ---------------------------------------
# The *money* lives in the SQLite `bookings` table and is fetched at
# draft-time so confirmations stay in sync after correction. Operational
# metadata (location, timezones, times) is supplied by the caller.


def _total_paid_str(trip_id: str, leg: str) -> str:
    pair = booking_total_for_leg(trip_id, leg)
    if pair is None:
        return "(not yet logged)"
    amount, currency = pair
    return _format_money(amount, currency)


@mcp.tool()
def kelly_log_booking(
    trip_id: str,
    leg: str,
    provider: str,
    total_amount: str,
    currency: str = "GBP",
    confirmation_ref: str | None = None,
    paid_at: str | None = None,
    paid_by: str = "me",
) -> str:
    """Log an actual booked artifact (with its money) to the bookings store.

    ``leg`` is a free-form identifier scoped to the trip — conventionally
    ``"airbnb"``, ``"eurostar_out"``, ``"eurostar_back"``, ``"disney_tickets"``.
    ``total_amount`` is a string to preserve Decimal precision (pass ``"1219.14"``).
    ``paid_at`` is an ISO date (YYYY-MM-DD); ``paid_by`` is a free-form label.

    Append-only: re-logging the same ``(trip_id, leg)`` adds a new row and the
    latest wins in fetches. Returns JSON with the persisted record.
    """
    try:
        row_id, rec = log_booking(
            trip_id=trip_id,
            leg=leg,
            provider=provider,
            total_amount=total_amount,
            currency=currency,
            confirmation_ref=confirmation_ref,
            paid_at=paid_at,
            paid_by=paid_by,
        )
    except Exception as e:
        return json.dumps({"error": f"failed to log booking: {e}"})
    return json.dumps(
        {
            "id": row_id,
            "trip_id": rec.trip_id,
            "leg": rec.leg,
            "provider": rec.provider,
            "confirmation_ref": rec.confirmation_ref,
            "total_amount": rec.total_amount,
            "currency": rec.currency,
            "paid_at": rec.paid_at,
            "paid_by": rec.paid_by,
        },
        default=str,
    )


@mcp.tool()
def kelly_booking_event_draft(
    trip_id: str,
    leg: str,
    summary: str,
    location: str,
    start_datetime: str,
    end_datetime: str,
    start_timezone: str,
    end_timezone: str | None = None,
    description: str = "",
) -> str:
    """Return a Google Calendar event spec, enriched with the booking total.

    The agent passes this spec to a Google Calendar MCP tool (e.g.
    ``mcp__claude_ai_Google_Calendar__create_event``) to actually create the
    event — Kelly never touches Google's API directly.

    Parameters carry all the operational metadata: ``summary`` (event title),
    ``location`` (free-form address), ``start_datetime`` / ``end_datetime``
    (ISO ``YYYY-MM-DDTHH:MM`` — seconds are appended), IANA timezones, and an
    optional ``description``. ``trip_id`` + ``leg`` are used to look up the
    booking's total in the bookings table; the ``Total paid:`` line is appended
    to the description automatically. If no booking has been logged yet, it
    shows ``"(not yet logged)"``.

    Returns JSON: ``{summary, location, description, start: {dateTime, timeZone},
                     end: {dateTime, timeZone}}``.
    """
    total = _total_paid_str(trip_id, leg)
    full_desc = (
        description.rstrip() + ("\n\n" if description.strip() else "") + f"Total paid: {total}"
    ).strip()
    end_tz = end_timezone or start_timezone
    return json.dumps(
        {
            "summary": summary,
            "location": location,
            "description": full_desc,
            "start": {"dateTime": f"{start_datetime}:00", "timeZone": start_timezone},
            "end": {"dateTime": f"{end_datetime}:00", "timeZone": end_tz},
        },
        default=str,
    )


@mcp.tool()
def kelly_profile_set(profile_id: str, name: str, payload_json: str = "") -> str:
    """Upsert the single-user traveller profile, keyed by ``profile_id``.

    ``payload_json`` is a free-form JSON string (travellers, food/relax prefs,
    hard exclusions, home origin airports, Avios notes) — stored verbatim. Saving
    the same ``profile_id`` again overwrites in place (no duplicates). Pass an
    empty string for ``payload_json`` to leave the blob unset. Returns the stored
    profile, or ``{"error": "..."}`` if ``payload_json`` isn't valid JSON.
    """
    return json.dumps(profile_set_profile(profile_id, name, payload_json), default=str)


@mcp.tool()
def kelly_profile_get(profile_id: str) -> str:
    """Return the stored traveller profile for ``profile_id``.

    Returns ``{"error": "..."}`` if no profile has been saved under that id.
    """
    return json.dumps(profile_get_profile(profile_id), default=str)


@mcp.tool()
def kelly_dreambox_add(
    title: str,
    destination: str = "",
    notes: str = "",
    profile_id: str = "",
    payload_json: str = "",
) -> str:
    """Park a future-trip idea in the dream-box; return the created row.

    Only ``title`` is required. ``destination`` / ``notes`` are free text;
    ``profile_id`` (optional) ties the idea to a profile; ``payload_json`` (a JSON
    string) carries any extra structured notes (budget hints, candidate dates,
    must-dos). Returns ``{"error": "..."}`` if ``payload_json`` isn't valid JSON.
    """
    return json.dumps(
        profile_add_dream_trip(title, destination, notes, profile_id, payload_json),
        default=str,
    )


@mcp.tool()
def kelly_dreambox_list(profile_id: str = "") -> str:
    """List all parked dream-box trips, newest first.

    Pass ``profile_id`` to filter to one profile's parked ideas; omit it for all.
    """
    return json.dumps(profile_list_dream_trips(profile_id), default=str)


@mcp.tool()
def kelly_dreambox_remove(id: int) -> str:
    """Delete a parked dream-box trip by its ``id``.

    Returns ``{"deleted": id}`` on success, or ``{"error": "..."}`` if no row
    with that id exists.
    """
    return json.dumps(profile_remove_dream_trip(id), default=str)


@mcp.tool()
def kelly_session_create(
    title: str,
    destination: str = "",
    notes: str = "",
    profile_id: str = "",
    intent_json: str = "",
    payload_json: str = "",
    status: str = "active",
) -> str:
    """Create a trip-planning session (dossier) and return the stored row.

    A session bundles a search **intent** with the dated option snapshots it
    surfaces, across a lifecycle: ``idea → active → shortlisted → booked →
    archived``. ``status`` defaults to ``active``; pass ``idea`` to park it like a
    dream-box entry. ``intent_json`` (who / from / to / dates / pax / constraints)
    and ``payload_json`` are free-form JSON strings stored verbatim; both are
    validated and return ``{"error": "..."}`` if they don't parse. Attach options
    later with ``kelly_session_attach_option``.
    """
    return json.dumps(
        session_create(title, destination, notes, profile_id, intent_json, payload_json, status),
        default=str,
    )


@mcp.tool()
def kelly_session_list(profile_id: str = "", status: str = "") -> str:
    """List trip sessions, newest first.

    Filter by ``profile_id`` and/or ``status`` (``idea|active|shortlisted|booked|
    archived``); omit both for all. Returns ``{"sessions": [...]}`` (without the
    per-session option snapshots — use ``kelly_session_get`` for those).
    """
    return json.dumps(session_list(profile_id, status), default=str)


@mcp.tool()
def kelly_session_get(id: int) -> str:
    """Return one session plus its dated option snapshots, or ``{"error": "..."}``.

    Each option carries a ``captured_at`` timestamp — a stored snapshot reflects
    what was true *then*, never guaranteed-current pricing.
    """
    return json.dumps(session_get(id), default=str)


@mcp.tool()
def kelly_session_update(
    id: int,
    status: str = "",
    title: str = "",
    notes: str = "",
    destination: str = "",
    intent_json: str = "",
    payload_json: str = "",
) -> str:
    """Patch a session (blank args are left untouched) and re-stamp ``updated_at``.

    Common use is moving ``status`` along the lifecycle (e.g. to ``archived`` or
    ``booked``). ``intent_json`` / ``payload_json`` are validated as JSON. Returns
    the updated session with its options, or ``{"error": "..."}``.
    """
    return json.dumps(
        session_update(id, status, title, notes, destination, intent_json, payload_json),
        default=str,
    )


@mcp.tool()
def kelly_session_attach_option(
    session_id: int,
    kind: str,
    label: str = "",
    amount: str = "",
    currency: str = "",
    avios_points: int = 0,
    source: str = "manual",
    payload_json: str = "",
) -> str:
    """Append a dated option snapshot to a session; return the created snapshot.

    ``kind`` ∈ ``cash_flight|avios|train|stay|experience|car|local|other``.
    ``amount`` (+ ``currency``) is the cash price — leave blank for avios-only
    options. ``avios_points`` is a **separate currency**, reported but never folded
    into cash. ``source`` records provenance (``serpapi|seats_aero|eurostar|
    manual|...``). ``payload_json`` carries the full option blob (times, flight
    numbers, the BA.com-confirm note, etc.). Snapshots are append-only and
    timestamped, so re-attaching later builds a price history.
    """
    return json.dumps(
        session_attach_option(
            session_id, kind, label, amount, currency, avios_points, source, payload_json
        ),
        default=str,
    )


@mcp.tool()
def kelly_session_remove(id: int) -> str:
    """Delete a session and all its option snapshots by ``id``.

    Returns ``{"deleted": id}`` on success, or ``{"error": "..."}`` if absent.
    """
    return json.dumps(session_delete(id), default=str)


@mcp.resource("kelly://config", mime_type="text/markdown")
def kelly_config_resource() -> str:
    """The current kelly.md as Markdown."""
    path = default_config_path()
    if not path.is_file():
        return f"# kelly.md not found at {path}\n"
    return path.read_text(encoding="utf-8")


def main() -> None:
    """Entrypoint for `kelly-mcp` script — runs the stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
