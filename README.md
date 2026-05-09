# Kelly

Travel-hacking copilot for planned watchlists, opportunistic scans, and group trips. Reads household policy from a single Markdown file, runs a **macro → mid → micro** funnel of cheap-to-expensive APIs, persists observations to **SQLite** for baselines and anomaly labels, and exposes the whole toolkit as a **stdio MCP server** plus a Typer CLI.

[![python](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white)](pyproject.toml)
[![poetry](https://img.shields.io/badge/poetry-2.x-60A5FA?logo=poetry&logoColor=white)](pyproject.toml)
[![mcp](https://img.shields.io/badge/MCP-stdio-7C3AED)](https://modelcontextprotocol.io)
[![ruff](https://img.shields.io/badge/lint-ruff-FFE873)](https://docs.astral.sh/ruff/)
[![docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](Dockerfile)
[![tests](https://img.shields.io/badge/tests-28%20passing-22c55e)](tests/)

```text
   macro price graph  →  anomaly vs SQLite baseline  →  mid policy / watchlist  →  micro heavy quotes
   (1 req/month)         (cheap | normal | unknown)     (max_stops, baggage, …)    (capped at N/scan)
```

## Tracks

| Track    | What it does                              | Default backend                              | Persists to          | Poetry extra |
| -------- | ----------------------------------------- | -------------------------------------------- | -------------------- | ------------ |
| Flights  | Cash search + macro price graph           | RapidAPI Google Flights (SerpApi fallback)   | `observations`       | `mcp`        |
| Awards   | Cached award availability                 | Seats.aero Partner API                       | `observations`       | `mcp`        |
| Trains   | Eurostar fare scrape (free, no key)       | `patchright` stealth Chromium                | `train_observations` | `trips`      |
| Stays    | Whole-listing Airbnb search (free, no key)| `pyairbnb` against staysSearch GraphQL       | `stay_observations`  | `trips`      |

## Quick start

```bash
./scripts/setup-venv.sh                     # creates .venv, seeds .env / config/kelly.md
source .venv/bin/activate
$EDITOR .env                                # add RAPIDAPI_KEY, SERPAPI_API_KEY, SEATS_AERO_API_KEY
$EDITOR config/kelly.md                     # passengers, watchlist, opportunities, trains, stays
kelly scan -c config/kelly.md               # planned watchlist
kelly opportunities -c config/kelly.md      # capped wishlist
kelly plan summer-paris                     # group trip: trains + stay
kelly-mcp                                   # stdio MCP server
```

`scripts/setup-venv.sh` bootstraps Poetry 2.x into `.tools/venv-poetry` if it isn't on PATH, creates an in-project `.venv`, and seeds `.env` / `config/kelly.md` from the example files. Override the interpreter with `PYTHON=python3.11 ./scripts/setup-venv.sh`, the extras with `EXTRAS="mcp" ./scripts/setup-venv.sh` for a flights-only install.

## MCP tools

Tools are namespaced `kelly_<layer>_<action>`; resources expose the parsed config to the host. Each tool is a thin delegate — business logic lives in `services/*`.

<details>
<summary><b>23 tools + 2 resources</b></summary>

**Macro** (≤1 HTTP request per month in window):
- `kelly_macro_price_graph` · `kelly_macro_month_overview` · `kelly_macro_cheapest_destinations` · `kelly_macro_flexible_trip`

**Mid** (policy / watchlist / anomaly):
- `kelly_mid_apply_policy` · `kelly_mid_match_watchlist` · `kelly_mid_explain_rejection` · `kelly_mid_anomaly_scan`

**Micro — flights & hotels:**
- `kelly_micro_flight_quote` · `kelly_micro_hotel_search` · `kelly_micro_tripadvisor_context`

**Micro — trips track** (`[trips]` extra):
- `kelly_micro_eurostar_search` · `kelly_micro_airbnb_search` · `kelly_plan_trip`

**Pipeline / scans / awards:**
- `kelly_pipeline_graph_to_details` · `kelly_search_cash` · `kelly_search_awards` · `kelly_scan_watchlist` · `kelly_scan_opportunities`

**History & explanation:**
- `kelly_history_summary` · `kelly_forecast_hint` · `kelly_explain_deal` · `kelly_load_config`

**Resources:**
- `kelly://config` (Markdown) · `kelly://policy` (JSON)

</details>

See [docs/MCP.md](docs/MCP.md) for host wiring (Claude Code, Cursor, OpenCode, OpenClaw).

## Run in a container

```bash
make docker-build                          # mcp + trips, no browsers (~300MB)
make docker-build-flights                  # flights/awards only, leanest image
make docker-build-browsers                 # adds Chromium for Eurostar (~700MB)
make docker-mcp                            # stdio MCP — pipe JSON-RPC to stdin
make docker-run CMD='kelly scan -c config/kelly.md'
```

Or with `docker compose`:

```bash
docker compose build
docker compose run --rm kelly kelly --help
docker compose run --rm -T kelly                # stdio MCP
```

The image runs as a non-root user (uid/gid 1000), excludes `.env` and `data/` via `.dockerignore`, and expects `.env` at run time via `--env-file` / `env_file:`. `./config` is mounted read-only and `./data` writable so SQLite history survives image rebuilds. The `POETRY_EXTRAS` build arg picks the install profile; `INSTALL_BROWSERS=true` opts into patchright/Chromium for Eurostar scraping.

## OpenClaw on a VPS

One-liner install + MCP registration (see [docs/OPENCLAW-VPS.md](docs/OPENCLAW-VPS.md) for env/flags and the private-repo SSH path):

```bash
curl -fsSL --proto '=https' --tlsv1.2 \
  https://raw.githubusercontent.com/albinati/kelly-travel-planner/main/scripts/install-openclaw-kelly.sh | bash
```

## Configuration

- **`config/kelly.md`** — household config: YAML frontmatter (`currency`, `passengers` defaults, `travel_policy`) + GFM tables under `## Passengers`, `## Planned watchlist`, `## Opportunities`, `## Trains`, `## Stays`. Parser in `src/kelly/md_config.py`. See `config/kelly.example.md`.
- **`.env`** — secrets only (`RAPIDAPI_KEY`, `SERPAPI_API_KEY`, `SEATS_AERO_API_KEY`); auto-loaded from project root. `chmod 600`. `.env.*` is gitignored.
- **History** — `data/kelly_history.sqlite` by default (override with `KELLY_DATA_DIR`). Rolling baseline window (default 90 days, set via `frontmatter.history_window_days`).

## Architecture

A cost-aware funnel: cheap calendar/price-graph endpoints flag candidate dates, anomaly labels gate them against history, mid-layer policy filters apply household preferences, and the per-itinerary heavy endpoints run only on at most `KELLY_PIPELINE_MAX_HEAVY` (default 5, clamped 1–20) flagged dates per scan. Full design in [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md).

## Dev

```bash
make test           # pytest, all HTTP mocked
make lint           # ruff check
make format         # ruff format
```

Tests mock at the service boundary (`@patch("kelly.services.<x>.<provider>")`) — never hit live APIs. `pyproject.toml` sets `pythonpath = ["src"]`, so import as `from kelly....`.

## Issues & roadmap

Tagged by area — `area:flights`, `area:awards`, `area:trips`, `area:mcp`, `area:cli`, `area:config`, `area:history`, `area:infra`. Browse the [labels page](https://github.com/albinati/kelly-travel-planner/labels) or the open [enhancements](https://github.com/albinati/kelly-travel-planner/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement).

Heuristics (anomaly labels, forecast hints) are not financial advice — they're explicit, capped, and meant for planning, not booking decisions.
