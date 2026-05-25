"""MCP server (stdio) for Kelly — group trip planning over Eurostar + Airbnb.

Install with `poetry install --extras mcp`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as e:
    raise SystemExit(
        "The `mcp` package is required. Install with: poetry install --extras mcp"
    ) from e

from kelly.booking_metadata import AIRBNB, DISNEY, EUROSTAR_LEGS, format_money
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
from kelly.services.booking_service import booking_total_for_leg, log_booking
from kelly.services.fx_service import FxError, convert as fx_convert, fx_quote
from kelly.services.hosting_service import estimate_hosting
from kelly.services.summary_service import summarize_trip
from kelly.services.stay_service import search_stay, stay_result_to_jsonable
from kelly.services.train_service import search_train, train_result_to_jsonable
from kelly.services.trip_planner import plan_trip
from kelly.settings import config_path as default_config_path

# --- Expense + calendar MVP constants ---------------------------------------
# Single Splitwise group for the family's 2026 trip arc (Luis + Test User).
# Hardcoded by design — when the next trip arc starts we'll either bump this
# or finally do the ExpenseProvider abstraction (deferred per the plan).
_DEFAULT_GROUP_ID = 0  # example-group
_DEFAULT_GROUP_NAME = "example-group"
_TRIP_PREFIX = "[sample-trip] "

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
def kelly_log_expense(
    description: str,
    amount: str,
    currency: str = "GBP",
    paid_by_me: bool = True,
) -> str:
    """Log a shared expense to the family Splitwise group (example-group),
    splitting equally between all members.

    The trip prefix ``[sample-trip]`` is prepended automatically — the
    *description* should be the receipt-level detail ("Dinner at Bistrot Paul
    Bert", "RER A tickets, family of 10"). Amount is a string to avoid float
    precision issues; pass it like ``"25.00"``.

    Returns JSON with the created expense id, currency, total, and per-user
    share. Returns ``{"error": "..."}`` if SPLITWISE_API_KEY is not configured.
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
    try:
        with splitwise_client() as client:
            me = get_current_user(client)
            group = get_group(client, _DEFAULT_GROUP_ID)
            member_ids = [int(m["id"]) for m in (group.get("members") or [])]
            if not member_ids:
                return json.dumps({"error": f"group {_DEFAULT_GROUP_ID} has no members"})
            shares = equal_couple_shares(amount, member_ids)
            full_desc = f"{_TRIP_PREFIX}{description}"
            expense = create_expense(
                client,
                group_id=_DEFAULT_GROUP_ID,
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
            "group": _DEFAULT_GROUP_NAME,
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
    """Read current balances in the family Splitwise group (example-group).

    Returns JSON with each member's per-currency balance and Splitwise's
    minimum-transfer settle-up plan (``simplified_debts``). Positive balances
    mean the member is owed money; negative means they owe.

    Returns ``{"error": "..."}`` if SPLITWISE_API_KEY is not configured.
    """
    try:
        with splitwise_client() as client:
            me = get_current_user(client)
            balances = get_group_balances(client, _DEFAULT_GROUP_ID)
    except SplitwiseError as e:
        return json.dumps({"error": str(e)})

    # Relativize the simplified_debts for the calling user's perspective so the
    # agent can phrase it naturally ("Test User owes you £X" vs "You owe £X").
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
            "group": _DEFAULT_GROUP_NAME,
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

    Example: ``kelly_trip_summary("sample-trip", currency="BRL")``
    """
    return json.dumps(summarize_trip(trip_id, currency=currency), default=str)


# --- Bookings + calendar event drafts ---------------------------------------
# Static operational metadata (locations, timezones, default times) lives in
# `kelly.booking_metadata`. The *money* lives in the SQLite `bookings` table
# and is fetched at draft-time so confirmations stay in sync after correction.


def _total_paid_str(trip_id: str, leg: str) -> str:
    pair = booking_total_for_leg(trip_id, leg)
    if pair is None:
        return "(not yet logged)"
    amount, currency = pair
    return format_money(amount, currency)


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
    booking: str,
    depart_time: str | None = None,
    arrive_time: str | None = None,
    disney_date: str | None = None,
) -> str:
    """Return a Google Calendar event spec for a Paris-Disney 2026-08 booking.

    The agent passes this spec to a Google Calendar MCP tool (e.g.
    ``mcp__claude_ai_Google_Calendar__create_event``) to actually create the
    event — Kelly never touches Google's API directly.

    booking:
      - ``"airbnb"`` — Airbnb stay, full check-in→checkout span.
      - ``"eurostar_out"`` — outbound Eurostar leg. Pass *depart_time* and
        *arrive_time* (HH:MM) to override the planner's recommendation (12:01→15:30).
      - ``"eurostar_back"`` — return Eurostar leg. Same overrides (default 20:02→21:30).
      - ``"disney"`` — Disney day visit. Pass *disney_date* (YYYY-MM-DD) to override
        the default 2026-08-20.

    The ``Total paid:`` line in the description is read from the bookings table
    at runtime; if no booking has been logged yet, it shows "(not yet logged)".

    Returns JSON: ``{summary, location, description, start: {dateTime, timeZone},
                     end: {dateTime, timeZone}}``.
    """
    b = booking.strip().lower()
    if b == "airbnb":
        a = AIRBNB
        total = _total_paid_str(a["trip_id"], a["leg"])
        desc = (
            f"Host: {a['host']}\n"
            f"Confirmation code: {a['confirmation_code']}\n"
            f"Guests: {a['guests']}\n"
            f"Total paid: {total}\n"
            f"Reservation: {a['url']}\n\n"
            f"Check-in after {a['check_in_time']}; checkout by {a['check_out_time']}."
        )
        return json.dumps(
            {
                "summary": a["summary"],
                "location": a["location"],
                "description": desc,
                "start": {
                    "dateTime": f"{a['check_in_date']}T{a['check_in_time']}:00",
                    "timeZone": "Europe/Paris",
                },
                "end": {
                    "dateTime": f"{a['check_out_date']}T{a['check_out_time']}:00",
                    "timeZone": "Europe/Paris",
                },
            },
            default=str,
        )

    if b in ("eurostar_out", "eurostar_back"):
        leg = EUROSTAR_LEGS["out" if b == "eurostar_out" else "back"]
        dep = depart_time or leg["default_depart"]
        arr = arrive_time or leg["default_arrive"]
        total = _total_paid_str(leg["trip_id"], leg["leg"])
        return json.dumps(
            {
                "summary": leg["summary_fmt"].format(depart=dep, arrive=arr),
                "location": leg["origin"],
                "description": (
                    f"Eurostar standard class.\n"
                    f"Depart {leg['origin']} at {dep} ({leg['origin_tz']}).\n"
                    f"Arrive {leg['destination']} at {arr} ({leg['destination_tz']}).\n"
                    f"Total paid: {total}\n"
                    f"Party of 10 — split into 2 booking groups (5+4+1 lap infant)."
                ),
                "start": {
                    "dateTime": f"{leg['date']}T{dep}:00",
                    "timeZone": leg["origin_tz"],
                },
                "end": {
                    "dateTime": f"{leg['date']}T{arr}:00",
                    "timeZone": leg["destination_tz"],
                },
            },
            default=str,
        )

    if b == "disney":
        d = DISNEY
        the_date = disney_date or d["default_date"]
        total = _total_paid_str(d["trip_id"], d["leg"])
        return json.dumps(
            {
                "summary": d["summary"],
                "location": d["location"],
                "description": f"{d['notes']}\n\nTotal paid: {total}",
                "start": {
                    "dateTime": f"{the_date}T{d['default_start_time']}:00",
                    "timeZone": d["tz"],
                },
                "end": {
                    "dateTime": f"{the_date}T{d['default_end_time']}:00",
                    "timeZone": d["tz"],
                },
            },
            default=str,
        )

    return json.dumps(
        {
            "error": (
                f"unknown booking {booking!r}. Use: 'airbnb', 'eurostar_out', "
                "'eurostar_back', or 'disney'."
            )
        }
    )


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
