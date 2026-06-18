"""Typer CLI for Kelly — group trip planner (Eurostar + Airbnb).

Every MCP tool in ``kelly.mcp_server`` has a matching subcommand here that calls
the *same* underlying service and prints the *same* JSON output. The CLI never
imports the ``mcp`` package, so it works without the ``mcp`` extra installed.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import typer

from kelly import __version__
from kelly.history_store import open_default_store
from kelly.md_config import StayRow, TrainRow, load_kelly_config
from kelly.settings import config_path as default_config_path

app = typer.Typer(no_args_is_help=True, add_completion=False)


# --- Splitwise group config (mirrors mcp_server) ----------------------------

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


def _cfg(p: str | None) -> Path:
    return Path(p).expanduser() if p else default_config_path()


@app.callback()
def _main() -> None:
    """Kelly — group trip planner: Eurostar trains + Airbnb stays from a Markdown config."""


@app.command()
def version() -> None:
    """Print version."""
    typer.echo(__version__)


# --- Planning ----------------------------------------------------------------


@app.command("config-show")
def config_show(
    config: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Print parsed config (trains + stays) as JSON."""
    path = config or default_config_path()
    if not path.is_file():
        typer.echo(f"Config not found: {path}", err=True)
        raise typer.Exit(code=1)
    cfg = load_kelly_config(path)
    summary = {
        "frontmatter": cfg.frontmatter.model_dump(mode="json"),
        "trains": [t.model_dump(mode="json", by_alias=True) for t in cfg.trains],
        "stays": [s.model_dump(mode="json") for s in cfg.stays],
    }
    typer.echo(json.dumps(summary, default=str, indent=2))


@app.command("load-config")
def load_config(
    config_path: str | None = typer.Option(None, "--config-path"),
) -> None:
    """Load and validate kelly.md; return JSON summary of trains and stays."""
    path = _cfg(config_path)
    if not path.is_file():
        print(json.dumps({"error": f"config not found: {path}"}))
        return
    cfg = load_kelly_config(path)
    print(
        json.dumps(
            {
                "frontmatter": cfg.frontmatter.model_dump(mode="json"),
                "trains": [t.model_dump(mode="json", by_alias=True) for t in cfg.trains],
                "stays": [s.model_dump(mode="json") for s in cfg.stays],
            },
            default=str,
        )
    )


@app.command("eurostar-search")
def eurostar_search(
    origin_city: str = typer.Argument(...),
    destination_city: str = typer.Argument(...),
    date_start: str = typer.Argument(...),
    date_end: str = typer.Argument(...),
    adults: int = typer.Argument(...),
    seniors: int = typer.Option(0, "--seniors"),
    teens: int = typer.Option(0, "--teens"),
    children_ages: str = typer.Option("", "--children-ages"),
    class_: str = typer.Option("standard", "--class"),
    config_path: str | None = typer.Option(None, "--config-path"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
) -> None:
    """One-shot Eurostar search. children_ages: comma-separated ints (e.g. '3,4,5')."""
    from kelly.services.train_service import search_train, train_result_to_jsonable

    path = _cfg(config_path)
    if not path.is_file():
        print(json.dumps({"error": f"config not found: {path}"}))
        return
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
    print(
        json.dumps(
            train_result_to_jsonable(search_train(cfg, row, store=store, persist=persist)),
            default=str,
        )
    )


@app.command("airbnb-search")
def airbnb_search(
    area: str = typer.Argument(...),
    check_in: str = typer.Argument(...),
    check_out: str = typer.Argument(...),
    adults: int = typer.Argument(...),
    children_ages: str = typer.Option("", "--children-ages"),
    bedrooms_min: int = typer.Option(1, "--bedrooms-min"),
    near: str = typer.Option("", "--near"),
    max_total: float | None = typer.Option(None, "--max-total"),
    config_path: str | None = typer.Option(None, "--config-path"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
) -> None:
    """One-shot Airbnb search. children_ages / near: comma-separated strings."""
    from kelly.services.stay_service import search_stay, stay_result_to_jsonable

    path = _cfg(config_path)
    if not path.is_file():
        print(json.dumps({"error": f"config not found: {path}"}))
        return
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
    print(
        json.dumps(
            stay_result_to_jsonable(search_stay(cfg, row, store=store, persist=persist)),
            default=str,
        )
    )


@app.command("plan-trip")
def plan_trip_cmd(
    trip_id: str = typer.Argument(...),
    config_path: str | None = typer.Option(None, "--config-path"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
) -> None:
    """Plan a group trip declared in kelly.md (## Trains / ## Stays sections)."""
    from kelly.services.trip_planner import plan_trip

    path = _cfg(config_path)
    if not path.is_file():
        print(json.dumps({"error": f"config not found: {path}"}))
        return
    cfg = load_kelly_config(path)
    store = open_default_store() if persist else None
    print(json.dumps(plan_trip(cfg, trip_id, store=store, persist=persist), default=str))


@app.command("plan")
def plan_cmd(
    trip_id: str = typer.Argument(..., help="Trip id declared in kelly.md (Trains/Stays sections)"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    no_persist: bool = typer.Option(False, "--no-persist", help="Do not write SQLite history"),
) -> None:
    """Plan a trip from kelly.md: trains (out/back) + stay. Prints indented JSON.

    Legacy/ergonomic alias of ``plan-trip`` (indented output, --config/-c flag).
    ``plan-trip`` is the canonical command that mirrors the MCP tool exactly.
    """
    from kelly.services.trip_planner import plan_trip

    path = config or default_config_path()
    if not path.is_file():
        typer.echo(f"Config not found: {path}", err=True)
        raise typer.Exit(code=1)
    cfg = load_kelly_config(path)
    store = None if no_persist else open_default_store()
    result = plan_trip(cfg, trip_id, store=store, persist=not no_persist)
    typer.echo(json.dumps(result, default=str, indent=2))


# --- Award flights / itinerary solver ----------------------------------------


@app.command("avios-search")
def avios_search(
    origin_airports: str = typer.Argument(...),
    destination_airports: str = typer.Argument(...),
    date_start: str = typer.Argument(...),
    date_end: str = typer.Argument(...),
    pax: int = typer.Argument(...),
    cabin: str = typer.Option("economy", "--cabin"),
    only_direct: bool = typer.Option(True, "--only-direct/--no-only-direct"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
) -> None:
    """Search BA-operated award-flight availability over a date window."""
    from kelly.services.award_flight_service import award_flight_result_to_jsonable, search_avios

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
    print(json.dumps(award_flight_result_to_jsonable(res), default=str))


@app.command("cash-flight-search")
def cash_flight_search(
    origin_airports: str = typer.Argument(...),
    destination_airports: str = typer.Argument(...),
    outbound_date: str = typer.Argument(...),
    adults: int = typer.Option(2, "--adults"),
    children: int = typer.Option(0, "--children"),
    cabin: str = typer.Option("economy", "--cabin"),
    currency: str = typer.Option("GBP", "--currency"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
) -> None:
    """Search cash (paid) one-way flights for a route + date via SerpApi."""
    from kelly.services.cash_flight_service import (
        cash_flight_result_to_jsonable,
        search_cash_flights,
    )

    origins = [c.strip() for c in origin_airports.split(",") if c.strip()]
    dests = [c.strip() for c in destination_airports.split(",") if c.strip()]
    store = open_default_store() if persist else None
    res = search_cash_flights(
        origins,
        dests,
        outbound_date,
        adults=adults,
        children=children,
        cabin=cabin,
        currency=currency,
        store=store,
        persist=persist,
    )
    print(json.dumps(cash_flight_result_to_jsonable(res), default=str))


@app.command("solve-itinerary")
def solve_itinerary(
    origin_airports: str = typer.Argument(...),
    destination_airports: str = typer.Argument(...),
    date_start: str = typer.Argument(...),
    date_end: str = typer.Argument(...),
    pax: int = typer.Argument(...),
    out_days: str = typer.Option("Thu,Fri", "--out-days"),
    return_days: str = typer.Option("Sun", "--return-days"),
    min_nights: int = typer.Option(2, "--min-nights"),
    max_nights: int = typer.Option(3, "--max-nights"),
    open_jaw: bool = typer.Option(True, "--open-jaw/--no-open-jaw"),
    min_seats: int = typer.Option(2, "--min-seats"),
    modes: str = typer.Option("avios", "--modes"),
    persist: bool = typer.Option(True, "--persist/--no-persist"),
) -> None:
    """Search transport for a weekend window and return ranked itineraries."""
    from kelly.services.award_flight_service import search_avios
    from kelly.services.transport_service import (
        avios_options_from_service,
        eurostar_options_from_result,
        itinerary_to_jsonable,
        solve_pairings,
    )

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
            print(json.dumps({"error": out_res.error}))
            return
        if back_res.error:
            print(json.dumps({"error": back_res.error}))
            return
        out_pool += avios_options_from_service(out_res)
        return_pool += avios_options_from_service(back_res)
        for tag, r in (("out", out_res), ("back", back_res)):
            if r.note:
                notes.append(f"avios {tag}: {r.note}")

    if "eurostar" in mode_set:
        from kelly.providers.playwright_eurostar import search_eurostar

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
    print(
        json.dumps(
            {
                "itineraries": [itinerary_to_jsonable(it) for it in top],
                "count": len(top),
                "capped": capped,
                "notes": notes,
            },
            default=str,
        )
    )


# --- Splitwise ---------------------------------------------------------------


@app.command("log-expense")
def log_expense(
    description: str = typer.Argument(...),
    amount: str = typer.Argument(...),
    currency: str = typer.Option("GBP", "--currency"),
    paid_by_me: bool = typer.Option(True, "--paid-by-me/--no-paid-by-me"),
) -> None:
    """Log a shared expense to the configured Splitwise group, splitting equally."""
    from kelly.providers.splitwise import (
        SplitwiseError,
        _client as splitwise_client,
        create_expense,
        equal_couple_shares,
        get_current_user,
        get_group,
    )

    if not paid_by_me:
        print(
            json.dumps(
                {
                    "error": (
                        "paid_by_me=False not supported in MVP — the agent that called this "
                        "tool is currently mapped to the SPLITWISE_API_KEY owner. To log an "
                        "expense paid by someone else, ask that person to log it from their "
                        "own Splitwise."
                    )
                }
            )
        )
        return
    group_id = _splitwise_group_id()
    if group_id is None:
        print(json.dumps({"error": "KELLY_SPLITWISE_GROUP_ID is not set"}))
        return
    try:
        with splitwise_client() as client:
            me = get_current_user(client)
            group = get_group(client, group_id)
            member_ids = [int(m["id"]) for m in (group.get("members") or [])]
            if not member_ids:
                print(json.dumps({"error": f"group {group_id} has no members"}))
                return
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
        print(json.dumps({"error": str(e)}))
        return

    print(
        json.dumps(
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
    )


@app.command("expense-balances")
def expense_balances() -> None:
    """Read current balances in the configured Splitwise group."""
    from kelly.providers.splitwise import (
        SplitwiseError,
        _client as splitwise_client,
        get_current_user,
        get_group_balances,
    )

    group_id = _splitwise_group_id()
    if group_id is None:
        print(json.dumps({"error": "KELLY_SPLITWISE_GROUP_ID is not set"}))
        return
    try:
        with splitwise_client() as client:
            me = get_current_user(client)
            balances = get_group_balances(client, group_id)
    except SplitwiseError as e:
        print(json.dumps({"error": str(e)}))
        return

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

    print(
        json.dumps(
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
    )


# --- FX conversion -----------------------------------------------------------


@app.command("convert")
def convert(
    amount: str = typer.Argument(...),
    from_ccy: str = typer.Argument(...),
    to_ccy: str = typer.Argument(...),
    as_of: str | None = typer.Option(None, "--as-of"),
) -> None:
    """Convert *amount* from one ISO 4217 currency to another via cached ECB rates."""
    from kelly.services.fx_service import FxError, convert as fx_convert, fx_quote

    try:
        converted = fx_convert(amount, from_ccy, to_ccy, as_of=as_of)
    except FxError as e:
        print(json.dumps({"error": str(e)}))
        return
    quote = fx_quote(from_ccy, to_ccy, as_of=as_of)
    print(
        json.dumps(
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
    )


# --- Budget comparison -------------------------------------------------------


@app.command("budget-compare")
def budget_compare(
    options_json: str = typer.Argument(...),
    target_currency: str = typer.Option("GBP", "--target-currency"),
) -> None:
    """Compare N candidate trip options by their all-in total and rank them."""
    from kelly.services.budget_service import compare_options

    try:
        options = json.loads(options_json)
    except (json.JSONDecodeError, TypeError) as e:
        print(json.dumps({"error": f"options_json is not valid JSON: {e}"}))
        return
    if not isinstance(options, list):
        print(json.dumps({"error": "options_json must be a JSON array of options"}))
        return
    print(json.dumps(compare_options(options, target_currency=target_currency), default=str))


# --- Value engine (cash-vs-Avios + points coverage) --------------------------


@app.command("value-avios")
def value_avios(
    cash_amount: str = typer.Argument(...),
    cash_currency: str = typer.Argument(...),
    avios_points: int = typer.Argument(...),
    avios_taxes_amount: str = typer.Option("0", "--avios-taxes-amount"),
    avios_taxes_currency: str = typer.Option("GBP", "--avios-taxes-currency"),
    target_currency: str = typer.Option("GBP", "--target-currency"),
) -> None:
    """Compute pence-per-Avios for a redemption and recommend cash vs Avios."""
    from kelly.services.value_service import compute_avios_value

    print(
        json.dumps(
            compute_avios_value(
                cash_amount,
                cash_currency,
                avios_points,
                avios_taxes_amount,
                avios_taxes_currency,
                target_currency,
            ),
            default=str,
        )
    )


@app.command("check-award-coverage")
def check_award_coverage_cmd(
    avios_needed: int = typer.Argument(...),
    profile_id: str = typer.Option("", "--profile-id"),
    avios: int = typer.Option(0, "--avios"),
    hsbc_points: int = typer.Option(0, "--hsbc-points"),
    hsbc_to_avios: float = typer.Option(2.0, "--hsbc-to-avios"),
    transfer_bonus_pct: float = typer.Option(0, "--transfer-bonus-pct"),
) -> None:
    """Check whether an Avios redemption can be covered from your balances."""
    from kelly.services.value_service import check_award_coverage

    print(
        json.dumps(
            check_award_coverage(
                avios_needed,
                profile_id=profile_id or None,
                avios=avios,
                hsbc_points=hsbc_points,
                hsbc_to_avios=hsbc_to_avios,
                transfer_bonus_pct=transfer_bonus_pct,
            ),
            default=str,
        )
    )


@app.command("set-points-balances")
def set_points_balances_cmd(
    profile_id: str = typer.Argument(...),
    avios: int = typer.Option(0, "--avios"),
    hsbc: int = typer.Option(0, "--hsbc"),
    hsbc_to_avios: float = typer.Option(2.0, "--hsbc-to-avios"),
) -> None:
    """Store your points balances on the traveller profile."""
    from kelly.services.value_service import set_points_balances

    print(json.dumps(set_points_balances(profile_id, avios, hsbc, hsbc_to_avios), default=str))


# --- Hosting estimator -------------------------------------------------------


@app.command("estimate-hosting")
def estimate_hosting_cmd(
    hosting_id: str = typer.Argument(...),
    currency: str | None = typer.Option(None, "--currency"),
    host_household_size: int = typer.Option(2, "--host-household-size"),
    config_path: str | None = typer.Option(None, "--config-path"),
) -> None:
    """Estimate the marginal cost of hosting the visiting party `hosting_id`."""
    from kelly.services.hosting_service import estimate_hosting

    path = _cfg(config_path)
    if not path.is_file():
        print(json.dumps({"error": f"config not found: {path}"}))
        return
    cfg = load_kelly_config(path)
    print(
        json.dumps(
            estimate_hosting(
                hosting_id,
                cfg=cfg,
                host_household_size=host_household_size,
                currency=currency,
            ),
            default=str,
        )
    )


# --- Trip cost summary -------------------------------------------------------


@app.command("trip-summary")
def trip_summary(
    trip_id: str = typer.Argument(...),
    currency: str = typer.Option("GBP", "--currency"),
) -> None:
    """Aggregate every logged booking for *trip_id* into *currency*."""
    from kelly.services.summary_service import summarize_trip

    print(json.dumps(summarize_trip(trip_id, currency=currency), default=str))


# --- Bookings + calendar event drafts ----------------------------------------


def _total_paid_str(trip_id: str, leg: str) -> str:
    from kelly.services.booking_service import booking_total_for_leg

    pair = booking_total_for_leg(trip_id, leg)
    if pair is None:
        return "(not yet logged)"
    amount, currency = pair
    return _format_money(amount, currency)


@app.command("log-booking")
def log_booking_cmd(
    trip_id: str = typer.Argument(...),
    leg: str = typer.Argument(...),
    provider: str = typer.Argument(...),
    total_amount: str = typer.Argument(...),
    currency: str = typer.Option("GBP", "--currency"),
    confirmation_ref: str | None = typer.Option(None, "--confirmation-ref"),
    paid_at: str | None = typer.Option(None, "--paid-at"),
    paid_by: str = typer.Option("me", "--paid-by"),
) -> None:
    """Log an actual booked artifact (with its money) to the bookings store."""
    from kelly.services.booking_service import log_booking

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
        print(json.dumps({"error": f"failed to log booking: {e}"}))
        return
    print(
        json.dumps(
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
    )


@app.command("booking-event-draft")
def booking_event_draft(
    trip_id: str = typer.Argument(...),
    leg: str = typer.Argument(...),
    summary: str = typer.Argument(...),
    location: str = typer.Argument(...),
    start_datetime: str = typer.Argument(...),
    end_datetime: str = typer.Argument(...),
    start_timezone: str = typer.Argument(...),
    end_timezone: str | None = typer.Option(None, "--end-timezone"),
    description: str = typer.Option("", "--description"),
) -> None:
    """Return a Google Calendar event spec, enriched with the booking total."""
    total = _total_paid_str(trip_id, leg)
    full_desc = (
        description.rstrip() + ("\n\n" if description.strip() else "") + f"Total paid: {total}"
    ).strip()
    end_tz = end_timezone or start_timezone
    print(
        json.dumps(
            {
                "summary": summary,
                "location": location,
                "description": full_desc,
                "start": {"dateTime": f"{start_datetime}:00", "timeZone": start_timezone},
                "end": {"dateTime": f"{end_datetime}:00", "timeZone": end_tz},
            },
            default=str,
        )
    )


# --- Profile + dream-box -----------------------------------------------------


@app.command("profile-set")
def profile_set(
    profile_id: str = typer.Argument(...),
    name: str = typer.Argument(...),
    payload_json: str = typer.Option("", "--payload-json"),
) -> None:
    """Upsert the single-user traveller profile, keyed by ``profile_id``."""
    from kelly.services.profile_service import set_profile as profile_set_profile

    print(json.dumps(profile_set_profile(profile_id, name, payload_json), default=str))


@app.command("profile-get")
def profile_get(
    profile_id: str = typer.Argument(...),
) -> None:
    """Return the stored traveller profile for ``profile_id``."""
    from kelly.services.profile_service import get_profile as profile_get_profile

    print(json.dumps(profile_get_profile(profile_id), default=str))


@app.command("dreambox-add")
def dreambox_add(
    title: str = typer.Argument(...),
    destination: str = typer.Option("", "--destination"),
    notes: str = typer.Option("", "--notes"),
    profile_id: str = typer.Option("", "--profile-id"),
    payload_json: str = typer.Option("", "--payload-json"),
) -> None:
    """Park a future-trip idea in the dream-box; return the created row."""
    from kelly.services.profile_service import add_dream_trip as profile_add_dream_trip

    print(
        json.dumps(
            profile_add_dream_trip(title, destination, notes, profile_id, payload_json),
            default=str,
        )
    )


@app.command("dreambox-list")
def dreambox_list(
    profile_id: str = typer.Option("", "--profile-id"),
) -> None:
    """List all parked dream-box trips, newest first."""
    from kelly.services.profile_service import list_dream_trips as profile_list_dream_trips

    print(json.dumps(profile_list_dream_trips(profile_id), default=str))


@app.command("dreambox-remove")
def dreambox_remove(
    id: int = typer.Argument(...),
) -> None:
    """Delete a parked dream-box trip by its ``id``."""
    from kelly.services.profile_service import remove_dream_trip as profile_remove_dream_trip

    print(json.dumps(profile_remove_dream_trip(id), default=str))


# --- Trip sessions / dossiers ------------------------------------------------


@app.command("session-create")
def session_create_cmd(
    title: str = typer.Argument(...),
    destination: str = typer.Option("", "--destination"),
    notes: str = typer.Option("", "--notes"),
    profile_id: str = typer.Option("", "--profile-id"),
    intent_json: str = typer.Option("", "--intent-json"),
    payload_json: str = typer.Option("", "--payload-json"),
    status: str = typer.Option("active", "--status"),
) -> None:
    """Create a trip-planning session (dossier) and return the stored row."""
    from kelly.services.session_service import create_session as session_create

    print(
        json.dumps(
            session_create(
                title, destination, notes, profile_id, intent_json, payload_json, status
            ),
            default=str,
        )
    )


@app.command("session-list")
def session_list_cmd(
    profile_id: str = typer.Option("", "--profile-id"),
    status: str = typer.Option("", "--status"),
) -> None:
    """List trip sessions, newest first."""
    from kelly.services.session_service import list_sessions as session_list

    print(json.dumps(session_list(profile_id, status), default=str))


@app.command("session-get")
def session_get_cmd(
    id: int = typer.Argument(...),
) -> None:
    """Return one session plus its dated option snapshots."""
    from kelly.services.session_service import get_session as session_get

    print(json.dumps(session_get(id), default=str))


@app.command("session-update")
def session_update_cmd(
    id: int = typer.Argument(...),
    status: str = typer.Option("", "--status"),
    title: str = typer.Option("", "--title"),
    notes: str = typer.Option("", "--notes"),
    destination: str = typer.Option("", "--destination"),
    intent_json: str = typer.Option("", "--intent-json"),
    payload_json: str = typer.Option("", "--payload-json"),
) -> None:
    """Patch a session (blank args are left untouched) and re-stamp ``updated_at``."""
    from kelly.services.session_service import update_session as session_update

    print(
        json.dumps(
            session_update(id, status, title, notes, destination, intent_json, payload_json),
            default=str,
        )
    )


@app.command("session-attach-option")
def session_attach_option_cmd(
    session_id: int = typer.Argument(...),
    kind: str = typer.Argument(...),
    label: str = typer.Option("", "--label"),
    amount: str = typer.Option("", "--amount"),
    currency: str = typer.Option("", "--currency"),
    avios_points: int = typer.Option(0, "--avios-points"),
    source: str = typer.Option("manual", "--source"),
    payload_json: str = typer.Option("", "--payload-json"),
) -> None:
    """Append a dated option snapshot to a session; return the created snapshot."""
    from kelly.services.session_service import attach_option as session_attach_option

    print(
        json.dumps(
            session_attach_option(
                session_id, kind, label, amount, currency, avios_points, source, payload_json
            ),
            default=str,
        )
    )


@app.command("session-remove")
def session_remove_cmd(
    id: int = typer.Argument(...),
) -> None:
    """Delete a session and all its option snapshots by ``id``."""
    from kelly.services.session_service import delete_session as session_delete

    print(json.dumps(session_delete(id), default=str))


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
