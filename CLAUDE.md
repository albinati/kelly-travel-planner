# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Kelly** is a group trip planner: declare trips in a Markdown config, run one command, get a curated **Eurostar + Airbnb + Hotel** shortlist with bookable links. Airbnb (key-free) via `pyairbnb`, Eurostar (key-free) via `patchright`, hotels via **LiteAPI** (REST, key in `.env` — sandbox prefix `sand_`, production `prod_`). Persists searches to local SQLite for per-trip price baselines. Exposes a stdio MCP server (`kelly-mcp`) plus a Typer CLI (`kelly`).

See [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) for the design doc.

## Commands

Install (Poetry 2.x, Python ^3.11):

```bash
./scripts/setup-venv.sh                                 # easy path: creates .venv, seeds .env / config/kelly.md
poetry install --extras mcp --with dev                  # explicit path
patchright install chromium                             # one-time, needed for Eurostar
```

If `poetry install` complains about a stale `poetry.lock` after a pull, run `poetry lock` once and re-install.

Run:

```bash
poetry run kelly plan <trip_id> --config config/kelly.md
poetry run kelly config-show -c config/kelly.md
poetry run kelly-mcp                                    # stdio MCP server
```

`--no-persist` skips the SQLite write on `kelly plan`.

Tests / lint:

```bash
poetry run pytest                                # all tests (pythonpath=src, testpaths=tests)
poetry run pytest tests/test_train_service.py    # one file
poetry run ruff check .
poetry run ruff format .
```

## Configuration & secrets

- **One optional key:** `LITEAPI_API_KEY` (in `.env`) enables hotel search. Without it, the hotel shortlist returns an `error` string and Airbnb still runs. Eurostar + Airbnb are key-free.
- **`.env` is optional** and is auto-loaded from the project root by `kelly.settings.find_project_root()` (walks upward for `pyproject.toml`, or reads `KELLY_PROJECT_ROOT`). Only override env vars are accepted; see `.env.example`.
- **kelly.md** is the trip config: YAML frontmatter (`currency`, `history_window_days`) + GFM tables under `## Trains`, `## Stays`, `## Hosting`, `## Daytrips`. Parser is in `src/kelly/md_config.py`. See [`config/kelly.example.md`](config/kelly.example.md).
- Trip-id convention: trains live under `<trip_id>-out` and `<trip_id>-back` (or a single `<trip_id>` for one-way), the stay shares `<trip_id>`. For hosting-style arcs (visitors staying at the host's home), use `## Hosting` rows; `DaytripRow`s with the same `trip_id` attach excursions to that arc.

## Architecture

```
kelly.md  →  md_config (Pydantic)  →  trip_planner.plan_trip(trip_id)
                                          ├── train_service  → playwright_eurostar (patchright)
                                          ├── stay_service   → providers/airbnb (pyairbnb)
                                          ├── hotel_service  → providers/liteapi_hotels (REST)
                                          └── shortlist + history append (SqliteHistoryStore)
```

Layer responsibilities:

| Layer | Code | What it does |
|-------|------|--------------|
| **Config** | `md_config.py` | Pydantic v2 models (`TrainRow`, `StayRow`, `HostingRow`, `DaytripRow`, `KellyFrontmatter`, `KellyConfig`); GFM table parser; section finder. |
| **Providers** | `providers/airbnb.py`, `providers/playwright_eurostar.py`, `providers/liteapi_hotels.py`, `providers/splitwise.py` | Direct external calls. Lazy-imports / lazy-reads-env so the modules load even without browsers/deps/keys; each surfaces a clean `error` string on the result when prereqs are missing. |
| **Services** | `services/train_service.py`, `services/stay_service.py`, `services/hotel_service.py`, `services/trip_planner.py`, `services/booking_service.py`, `services/fx_service.py`, `services/summary_service.py`, `services/hosting_service.py` | Orchestrate provider calls, persist to history, build curated shortlists, log booked artifacts, normalise across currencies (ECB-cached), aggregate trip cost rollups, estimate hosting-window costs. |
| **History** | `history_store.py` | `SqliteHistoryStore` with `train_observations`, `stay_observations`, `hotel_observations`, `bookings`, and `fx_rates` tables; `train_key()` / `stay_key()` / `hotel_key()` helpers; `open_default_store()` opens at `KELLY_DATA_DIR/kelly_history.sqlite`. Bookings is latest-per-leg on fetch; `fx_rates` is UNIQUE on `(as_of, base, quote, source)` so refetch is idempotent. |
| **Metadata** | `booking_metadata.py` | Static operational metadata (locations, timezones, default times) for the calendar-draft tool. The *money* side of any booking lives in the `bookings` table — never in this module. |
| **MCP / CLI** | `mcp_server.py`, `cli.py` | Thin transport wrappers over `services/*`. Tools return JSON strings (use `json.dumps(..., default=str)` because outputs contain `Decimal` and `date`). |

### Eurostar fare bands

Honoured by the URL params in `playwright_eurostar.py`:

- 0–3 → **infant** (lap-held, free, no seat)
- 4–11 → **child**
- 12–25 → **youth** (discounted)
- 26–59 → **adult**
- 60+ → **senior**

`children_ages` on `TrainRow` are mapped to those bands automatically. `seniors` and `teens` columns are explicit counts (since they aren't always derivable from age).

### Airbnb age bands

`pyairbnb` uses different bands: <2 = infant, 2–12 = child, 13+ rolls into adults. `services/trip_planner.py` mirrors this when building the booking URL so the link lands with the same pax mix that was searched.

### History window

`frontmatter.history_window_days` (default 90) controls the cutoff for `fetch_train_amounts` / `fetch_stay_amounts`. Anomaly labelling for trains/stays is not implemented yet — different statistical shape than flight P10, deferred until we have enough samples to warrant it.

## MCP server

`src/kelly/mcp_server.py` defines all `@mcp.tool()` and `@mcp.resource(...)` functions against a single `FastMCP` instance. **11 tools + 1 resource:**

- **Planning** (4): `kelly_load_config`, `kelly_eurostar_search`, `kelly_airbnb_search`, `kelly_plan_trip` — accept `persist: bool = True`; pass `False` to skip the SQLite append.
- **Bookings + rollup** (3): `kelly_log_booking(trip_id, leg, provider, total_amount, currency, confirmation_ref, paid_at, paid_by)` writes an append-only row to the `bookings` table; `kelly_trip_summary(trip_id, currency="GBP")` aggregates all legs into a target currency; `kelly_convert(amount, from_ccy, to_ccy, as_of?)` is the ECB-cached FX converter both rely on.
- **Hosting** (1): `kelly_estimate_hosting(hosting_id, currency?, host_household_size=2, config_path?)` computes the marginal cost of hosting a visiting party from a `## Hosting` row + matching `## Daytrips`. Formula in `services/hosting_service.py`.
- **Splitwise** (2): `kelly_log_expense(description, amount, currency="GBP", paid_by_me=True)` and `kelly_expense_balances()` — group is hardcoded to `Family-London2026` (id `97871346`) and descriptions get auto-prefixed with `[paris-disney-2026-08]`. Both return `{"error": "..."}` JSON if `SPLITWISE_API_KEY` is absent.
- **Calendar drafts** (1): `kelly_booking_event_draft(booking, depart_time=None, arrive_time=None, disney_date=None)` — returns a Google Calendar event spec (`summary, location, description, start/end with timeZone`) for `"airbnb"`, `"eurostar_out"`, `"eurostar_back"`, or `"disney"`. The `Total paid:` line is read from the `bookings` table at runtime; shows "(not yet logged)" until you log. Kelly never calls the Google Calendar API itself — the agent passes the spec to its own Calendar MCP tool to create the event.
- **Resource:** `kelly://config` — the current `kelly.md` as Markdown.

## Testing notes

- `pyproject.toml` sets `pythonpath = ["src"]`, so import as `from kelly....` (no `src.` prefix).
- HTTP / scraper calls are always mocked at the service boundary: `@patch("kelly.services.train_service.search_eurostar")` and `@patch("kelly.services.stay_service.search_airbnb")`. Follow this pattern — never hit live providers from tests.
- When touching provider JSON handling, add a mocked fixture rather than relying on live API schema (Airbnb's GraphQL changes shape; Eurostar's DOM does too).

## Conventions

- Python ^3.11; Ruff line-length 100, target-version `py311`.
- Pydantic v2 models for config — normalize city codes to upper, class to `{standard, standard_premier, business_premier}`, operator to `{eurostar}`.
- Prefer `Decimal` for prices in provider code; convert to `float` only at the SQLite boundary.
- Heuristics (party-size warnings, central-area filters, target-total checks) are explicit and capped — they're planning hints, not booking decisions.
