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
from kelly.services.stay_service import search_stay, stay_result_to_jsonable
from kelly.services.train_service import search_train, train_result_to_jsonable
from kelly.services.trip_planner import plan_trip
from kelly.settings import config_path as default_config_path

# --- Expense + calendar MVP constants ---------------------------------------
# Single Splitwise group for the family's 2026 trip arc (Luis + Patricia).
# Hardcoded by design — when the next trip arc starts we'll either bump this
# or finally do the ExpenseProvider abstraction (deferred per the plan).
_DEFAULT_GROUP_ID = 97871346  # Family-London2026
_DEFAULT_GROUP_NAME = "Family-London2026"
_TRIP_PREFIX = "[paris-disney-2026-08] "

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
    """Log a shared expense to the family Splitwise group (Family-London2026),
    splitting equally between all members.

    The trip prefix ``[paris-disney-2026-08]`` is prepended automatically — the
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
    """Read current balances in the family Splitwise group (Family-London2026).

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
    # agent can phrase it naturally ("Patricia owes you £X" vs "You owe £X").
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
            me_view.append({"direction": "owed_to_you", "from": other, "amount": amount, "currency": cur})

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


# --- Calendar event drafts --------------------------------------------------
# Hardcoded booking data extracted from the user's Gmail Airbnb confirmation
# (thread 19e5abed75c15397). Lives here instead of kelly.md because the kelly.md
# config models the *search* (party, dates, area), not the booked artifact.
_AIRBNB_BOOKING = {
    "summary": "Airbnb Paris — UrbanFlat 115 (HMESRSXD98)",
    "location": "218 Rue St Denis, 75002 Paris, France",
    "host": "Urban Flat",
    "check_in_date": "2026-08-18",
    "check_in_time": "16:00",  # "After 16:00" per the receipt
    "check_out_date": "2026-08-21",
    "check_out_time": "11:00",  # "By 11:00" per the receipt
    "confirmation_code": "HMESRSXD98",
    "guests": "up to 10",
    "total_paid": "£1,219.14",
    "url": "https://www.airbnb.co.uk/reservation/itinerary?code=HMESRSXD98",
}

_EUROSTAR_LEGS = {
    "out": {
        "summary_fmt": "Eurostar LON→PAR ({depart}–{arrive})",
        "origin": "St Pancras International, London",
        "destination": "Gare du Nord, Paris",
        "date": "2026-08-18",
        "origin_tz": "Europe/London",
        "destination_tz": "Europe/Paris",
        "default_depart": "12:01",
        "default_arrive": "15:30",
    },
    "back": {
        "summary_fmt": "Eurostar PAR→LON ({depart}–{arrive})",
        "origin": "Gare du Nord, Paris",
        "destination": "St Pancras International, London",
        "date": "2026-08-21",
        "origin_tz": "Europe/Paris",
        "destination_tz": "Europe/London",
        "default_depart": "16:30",
        "default_arrive": "18:00",
    },
}

_DISNEY_BOOKING = {
    "summary": "Disneyland Paris (day visit)",
    "location": "Marne-la-Vallée–Chessy, Disneyland Paris, 77777 Marne-la-Vallée, France",
    "tz": "Europe/Paris",
    "default_date": "2026-08-20",  # Thursday — middle of trip per the user's earlier roteiro
    "default_start_time": "09:00",
    "default_end_time": "22:00",
    "notes": (
        "Take RER A from Châtelet-Les Halles / Gare de Lyon / Nation directly to "
        "Marne-la-Vallée–Chessy (~40min). Park gates open earlier; closing varies."
    ),
}


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
      - ``"eurostar_back"`` — return Eurostar leg. Same overrides (default 16:30→18:00).
      - ``"disney"`` — Disney day visit. Pass *disney_date* (YYYY-MM-DD) to override
        the default 2026-08-20.

    Returns JSON: ``{summary, location, description, start: {dateTime, timeZone},
                     end: {dateTime, timeZone}}``.
    """
    b = booking.strip().lower()
    if b == "airbnb":
        a = _AIRBNB_BOOKING
        desc = (
            f"Host: {a['host']}\n"
            f"Confirmation code: {a['confirmation_code']}\n"
            f"Guests: {a['guests']}\n"
            f"Total paid: {a['total_paid']}\n"
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
        leg = _EUROSTAR_LEGS["out" if b == "eurostar_out" else "back"]
        dep = depart_time or leg["default_depart"]
        arr = arrive_time or leg["default_arrive"]
        return json.dumps(
            {
                "summary": leg["summary_fmt"].format(depart=dep, arrive=arr),
                "location": leg["origin"],
                "description": (
                    f"Eurostar standard class.\n"
                    f"Depart {leg['origin']} at {dep} ({leg['origin_tz']}).\n"
                    f"Arrive {leg['destination']} at {arr} ({leg['destination_tz']}).\n"
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
        d = _DISNEY_BOOKING
        the_date = disney_date or d["default_date"]
        return json.dumps(
            {
                "summary": d["summary"],
                "location": d["location"],
                "description": d["notes"],
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
