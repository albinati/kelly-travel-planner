# Kelly

Group trip planner: declare a trip in a Markdown file, run one command, get a curated **Eurostar + Airbnb** shortlist with bookable links and party-size warnings. No API keys required — `pyairbnb` hits Airbnb's internal staysSearch GraphQL and `patchright` drives a stealth Chromium against eurostar.com.

[![python](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white)](pyproject.toml)
[![poetry](https://img.shields.io/badge/poetry-2.x-60A5FA?logo=poetry&logoColor=white)](pyproject.toml)
[![mcp](https://img.shields.io/badge/MCP-stdio-7C3AED)](https://modelcontextprotocol.io)
[![ruff](https://img.shields.io/badge/lint-ruff-FFE873)](https://docs.astral.sh/ruff/)
[![docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](Dockerfile)
[![tests](https://img.shields.io/badge/tests-16%20passing-22c55e)](tests/)

```text
   kelly.md  →  trains <trip>-out / <trip>-back  +  stay <trip>  →  Eurostar + Airbnb searches  →  shortlist
                  (Pydantic-validated GFM tables)                   (no API keys)               (with booking URLs)
```

## What it does

- Parses a household-scale `kelly.md` config (YAML frontmatter + `## Trains` / `## Stays` GFM tables).
- For each `trip_id` it fans out searches: `<trip_id>-out` / `<trip_id>-back` Eurostar legs plus the `<trip_id>` Airbnb stay.
- Curates a shortlist: cheapest journeys per direction, central-Paris filter for Paris stays, splits parties of 10+ into multiple Eurostar booking groups (the website caps at 9 paying pax).
- Persists every search to a local SQLite file (`train_observations`, `stay_observations`) so you can build a per-trip price baseline over time.
- Exposes the whole toolkit as a **stdio MCP server** plus a Typer CLI.

## Quick start

```bash
./scripts/setup-venv.sh                     # creates .venv (auto-bootstraps Poetry 2.x), seeds .env / config/kelly.md
source .venv/bin/activate
patchright install chromium                 # one-time, for Eurostar scraping
$EDITOR config/kelly.md                     # declare your trip(s)
kelly plan paris-weekend                    # group trip: trains (out/back) + stay
kelly-mcp                                   # stdio MCP server for Claude / Cursor / OpenCode
```

`./scripts/setup-venv.sh` bootstraps Poetry 2.x into `.tools/venv-poetry` if it isn't on PATH, creates an in-project `.venv`, and seeds `.env` / `config/kelly.md` from the example files. Override the interpreter with `PYTHON=python3.11 ./scripts/setup-venv.sh`.

## MCP tools

Tool calls are thin delegates — business logic lives in `services/*`.

| Tool | What it does |
| --- | --- |
| `kelly_load_config` | Parse `kelly.md` → JSON of frontmatter + trains + stays |
| `kelly_eurostar_search` | One-shot Eurostar fare scrape (any city pair Kelly knows) |
| `kelly_airbnb_search` | One-shot Airbnb whole-listing search for an area + dates |
| `kelly_plan_trip` | Look up `<trip_id>-out`/`<trip_id>-back`/`<trip_id>` and curate a shortlist |
| `kelly://config` | Resource: the current `kelly.md` as Markdown |

See [docs/MCP.md](docs/MCP.md) for host wiring (Claude Code, Cursor, OpenCode, OpenClaw).

## Config

Trips are pairs of `## Trains` rows (`<trip_id>-out`, `<trip_id>-back`) plus a `## Stays` row sharing the trip id. Eurostar fare bands are honoured by the URL: 0–3 infant (lap, free), 4–11 child, 12–25 youth, 26–59 adult, 60+ senior.

```markdown
---
currency: GBP
history_window_days: 90
---

## Trains

| id | operator | origin_city | destination_city | date_start | date_end | class | adults | seniors | teens | children_ages | target_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paris-weekend-out  | eurostar | LON | PAR | 2026-08-20 | 2026-08-22 | standard | 2 | 0 | 0 |    | 400 | Outbound, flexible ±1 day |
| paris-weekend-back | eurostar | PAR | LON | 2026-08-24 | 2026-08-25 | standard | 2 | 0 | 0 |    | 400 | Return |

## Stays

| id | area | check_in | check_out | adults | children_ages | bedrooms_min | near | max_walk_to_transit_min | max_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paris-weekend | Paris | 2026-08-20 | 2026-08-25 | 2 |    | 1 | Châtelet-Les Halles | 10 | 1200 | Whole-listing, central |
```

Full example: [config/kelly.example.md](config/kelly.example.md). Schema in `src/kelly/md_config.py`.

## Run in a container

```bash
make docker-build                          # default image (~700MB with Chromium for Eurostar)
make docker-build-flights                  # leaner; skips Chromium (Eurostar searches will error cleanly)
make docker-mcp                            # stdio MCP — pipe JSON-RPC to stdin
make docker-run CMD='kelly plan paris-weekend'
```

Or with `docker compose`:

```bash
docker compose build
docker compose run --rm kelly kelly plan paris-weekend
docker compose run --rm -T kelly                # stdio MCP
```

The image runs as a non-root user (uid/gid 1000), excludes `.env` and `data/` via `.dockerignore`, and bind-mounts `./config` read-only and `./data` writable so SQLite history survives image rebuilds. `INSTALL_BROWSERS=false` builds without Chromium for a lean image when you don't need Eurostar.

## Storage

- **`data/kelly_history.sqlite`** — append-only `train_observations` and `stay_observations` (cheapest fare/total + listings count + party size + days-to-go). Override location with `KELLY_DATA_DIR`.
- **History window** — `frontmatter.history_window_days` (default 90) controls the rolling window for baseline queries.

## Dev

```bash
make test           # pytest, all HTTP/scraper calls mocked
make lint           # ruff check
make format         # ruff format
```

Tests mock at the service boundary (`@patch("kelly.services.<x>.<provider>")`) — never hit live scrapers. `pyproject.toml` sets `pythonpath = ["src"]`, so import as `from kelly....`.

## Issues & roadmap

Browse by area: [`area:trips`](https://github.com/albinati/kelly-travel-planner/labels/area%3Atrips), [`area:mcp`](https://github.com/albinati/kelly-travel-planner/labels/area%3Amcp), [`area:cli`](https://github.com/albinati/kelly-travel-planner/labels/area%3Acli), [`area:config`](https://github.com/albinati/kelly-travel-planner/labels/area%3Aconfig), [`area:history`](https://github.com/albinati/kelly-travel-planner/labels/area%3Ahistory), [`area:infra`](https://github.com/albinati/kelly-travel-planner/labels/area%3Ainfra). Or jump to all [open enhancements](https://github.com/albinati/kelly-travel-planner/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement).

Heuristics like "central-Paris filter" and "lowest-fare ceiling estimate" are explicit and capped — they're planning hints, not booking decisions. Eurostar's website caps a single transaction at 9 paying passengers; for parties of 10+ Kelly emits Groups Desk hints in the shortlist.
