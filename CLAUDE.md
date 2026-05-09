# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Kelly** is a travel-hacking assistant. It reads a household travel config from a single Markdown file (frontmatter + GFM tables), queries cash flight APIs (RapidAPI Google Flights by default, SerpApi as fallback) and award APIs (Seats.aero), persists observations to SQLite, and exposes everything as a **stdio MCP server** (`kelly-mcp`) plus a Typer CLI (`kelly`). It is designed for OpenClaw-style MCP hosts but also integrates with Claude Code / Cursor / OpenCode.

See [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) for the authoritative design doc.

## Commands

Install (Poetry, Python ^3.11):

```bash
poetry install --extras "mcp trips" --with dev            # core + MCP + non-flight (rail/stay) + dev tools
poetry install --extras mcp --with dev                    # flights/awards only (skip pyairbnb + patchright)
poetry install --extras "mcp trips analytics" --with dev  # also add statsmodels for forecasting
```

The fastest path on a fresh machine is `./scripts/setup-venv.sh` — it bootstraps Poetry 2.x into `.tools/venv-poetry/` if it isn't on PATH, creates `.venv/` in-project, seeds `.env` and `config/kelly.md` from examples, and defaults to `EXTRAS="mcp trips"`. Override with `EXTRAS="mcp" ./scripts/setup-venv.sh` for the flights-only build.

If `poetry install` complains about a stale `poetry.lock` after a pull, run `poetry lock` once then re-install.

Run:

```bash
poetry run kelly scan --config config/kelly.md           # planned watchlist scan
poetry run kelly opportunities --config config/kelly.md  # capped wishlist scan
poetry run kelly config-show -c config/kelly.md          # dump parsed config as JSON
poetry run kelly history-route JFK LIS 2026-07-04        # baseline stats from SQLite
poetry run kelly-mcp                                     # stdio MCP server
```

`--json` prints one JSON object per row; `--no-persist` skips SQLite writes.

Tests / lint:

```bash
poetry run pytest                                # all tests (pythonpath=src, testpaths=tests)
poetry run pytest tests/test_aggregator.py       # one file
poetry run pytest tests/test_aggregator.py::test_gated_pipeline_skips_heavy_without_opportunities -v
poetry run ruff check .
poetry run ruff format .
```

## Configuration & secrets

- **Secrets load from `.env` at project root**, resolved by `kelly.settings.find_project_root()` (walks upward for `pyproject.toml`, or reads `KELLY_PROJECT_ROOT`). This means an MCP host only needs to point `cwd` at this repo — it does **not** need to inject env vars. See `.env.example` for the full list (`RAPIDAPI_KEY`, `SERPAPI_API_KEY`, `SEATS_AERO_API_KEY`, plus optional RapidAPI host overrides).
- **Single RapidAPI key** (`RAPIDAPI_KEY`) powers all RapidAPI-backed providers; each listing can override its host/base/path via `RAPIDAPI_<NAME>_HOST` / `_BASE` / `_PATH`.
- **Cash backend selection** (`kelly.settings.cash_backend()`): `KELLY_CASH_BACKEND=rapidapi|serpapi` is explicit; otherwise defaults to `rapidapi` when `RAPIDAPI_KEY` is set, else `serpapi`.
- **kelly.md** is the household config: YAML frontmatter (currency, passengers defaults, `travel_policy`) + GFM tables under `## Passengers`, `## Planned watchlist`, `## Opportunities`, and — for the non-flight track — `## Trains` and `## Stays`. Parser is in `src/kelly/md_config.py`; it also handles comma-separated `passenger_ids` per row as per-row overrides. See `config/kelly.example.md`.
- **Never `git add .env`**; `.gitignore` covers `.env`/`.env.*` but not `.env.example`.

## Architecture (Macro → Mid → Micro)

The core idea is a cost-aware funnel so expensive per-itinerary endpoints run only on flagged dates.

```
macro price graph (1 req/month) → anomaly vs SQLite baseline → mid policy/watchlist → micro heavy quotes (capped)
```

Layer responsibilities — each MCP tool is a thin wrapper; real logic lives in services:

| Layer | Code | What it does |
|-------|------|--------------|
| **Macro** | `services/macro_service.py`, `providers/google_flights.py` (`price graph`), `providers/kiwi.py` | Calendar/price-graph discovery; one HTTP request per month in window. |
| **Anomaly** | `services/anomaly_service.py` | Labels each calendar day (`normal` / `cheap_vs_history` / `opportunity` / `unknown` / `no_price`) against SQLite baseline (`p10` by default, controlled by `KELLY_ANOMALY_USE_P10` and `KELLY_ANOMALY_MIN_SAMPLES`). |
| **Mid** | `services/mid_service.py` | Applies `travel_policy` (max_stops/direct_only/baggage) from kelly.md frontmatter; matches candidates to watchlist rows; explains rejection. |
| **Micro** | `services/micro_service.py`, `providers/google_flights.py` (heavy search), `serpapi_client.py`, `providers/booking.py`, `providers/tripadvisor.py` | Dated cash quotes and hotel/context lookups. |
| **Aggregator** | `services/aggregator.py` | `run_gated_pipeline` + `gated_pipeline_from_env`: macro → anomaly → micro, with `KELLY_PIPELINE_MAX_HEAVY` cap (default 5, clamped to 1–20). Backs the `kelly_pipeline_graph_to_details` MCP tool. |
| **Awards** | `seats_aero_client.py` | Seats.aero cached-search (not RapidAPI). |

Orchestration for CLI/non-pipeline MCP tools goes through `orchestrator.py` (`scan_planned_watchlist`, `scan_opportunities`), which combines cash + awards + history + analytics and optionally appends `Observation` rows via `history_store.SqliteHistoryStore`.

HTTP: RapidAPI-backed providers go through `kelly.http.rapidapi_base.RapidAPIClient` (injects `X-RapidAPI-Key` / `X-RapidAPI-Host`). The non-flight track does **not** go through HTTP wrappers — `pyairbnb` hits Airbnb's internal GraphQL directly and `patchright` drives a stealth Chromium against eurostar.com. **No MCP inside Kelly** — outbound data access is HTTP / scraper only. Use `httpx` (already a dep) for new HTTP-shaped providers.

**Non-flight track (`[trips]` extra)** — parallel to the RapidAPI flight track, for group trips that aren't flight-shaped:

| Area | Code |
|------|------|
| Providers | `providers/airbnb.py` (uses `pyairbnb`), `providers/playwright_eurostar.py` (uses `patchright`) |
| Services | `services/stay_service.py`, `services/train_service.py`, `services/trip_planner.py` |
| Config | `TrainRow` / `StayRow` in `md_config.py`, sections `## Trains` / `## Stays` |
| MCP | `kelly_micro_eurostar_search`, `kelly_micro_airbnb_search`, `kelly_plan_trip` |
| CLI | `kelly plan <trip_id>` |
| Extra | `kelly[trips]` — `pyairbnb` (Airbnb GraphQL) + `patchright` (stealth Playwright fork). Browsers via `patchright install chromium` or `INSTALL_BROWSERS=true` in Docker. |

`pyairbnb` and `patchright` are imported lazily inside the provider call sites: when the `trips` extra is missing, `search_airbnb` / `search_eurostar` return a result with a clean `error` string instead of failing module load. That keeps the flight-only install path lean.

`kelly_plan_trip(trip_id)` convention: looks up trains `<trip_id>-out` and `<trip_id>-back` (or single `<trip_id>`) plus stay `<trip_id>`. Eurostar age bands applied in `playwright_eurostar`: 0–3 infant, 4–11 child, 12–25 youth, 26–59 adult, 60+ senior; per-adult lowest-fare prices are returned (multiplying by passenger count is a planning ceiling, not the booked total).

History: `data/kelly_history.sqlite` (override with `KELLY_DATA_DIR`). Baselines use a rolling window (default 90 days, `frontmatter.history_window_days`). Route key = `"{ORIGIN}|{DEST}|{cabin}|{YYYY-MM-DD}"`.

## MCP server

`src/kelly/mcp_server.py` defines all `@mcp.tool()` and `@mcp.resource(...)` functions against a single `FastMCP` instance. Tool naming convention: `kelly_<layer>_<action>` (e.g. `kelly_macro_price_graph`, `kelly_mid_anomaly_scan`, `kelly_micro_flight_quote`, `kelly_pipeline_graph_to_details`). Resources: `kelly://config` (markdown), `kelly://policy` (JSON). Keep tools as thin delegates to services — business logic belongs in `services/*`.

Tools return JSON strings (use `json.dumps(..., default=str)` because outputs often contain `Decimal` and `date`).

## Testing notes

- `pyproject.toml` sets `pythonpath = ["src"]`, so import as `from kelly....` (no `src.` prefix).
- HTTP is always mocked in tests (`unittest.mock.patch` on the service-level function, e.g. `@patch("kelly.services.aggregator.macro_price_graph")`). Follow this pattern — never hit real providers from tests.
- When touching provider JSON handling, add a mocked fixture rather than relying on live API schema.

## Conventions

- Python ^3.11; Ruff line-length 100, target-version `py311`.
- Pydantic v2 models for config (`md_config.py`) — normalize IATA to upper, cabin to `{economy, premium_economy, business, first}`.
- Prefer `Decimal` for prices in provider code; convert to `float` only at the SQLite boundary.
- Heuristics (anomaly labels, forecast hints) are not financial advice — keep them explicit and cap-limited.
