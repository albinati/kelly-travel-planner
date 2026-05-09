# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Kelly** is a group trip planner: declare trips in a Markdown config, run one command, get a curated **Eurostar + Airbnb** shortlist with bookable links. No API keys: `pyairbnb` hits Airbnb's internal staysSearch GraphQL, `patchright` (stealth Playwright fork) drives a headless Chromium against eurostar.com. Persists searches to local SQLite for per-trip price baselines. Exposes a stdio MCP server (`kelly-mcp`) plus a Typer CLI (`kelly`).

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

- **No API keys required.** `pyairbnb` and `patchright` work key-free; everything else is local SQLite.
- **`.env` is optional** and is auto-loaded from the project root by `kelly.settings.find_project_root()` (walks upward for `pyproject.toml`, or reads `KELLY_PROJECT_ROOT`). Only override env vars are accepted; see `.env.example`.
- **kelly.md** is the trip config: YAML frontmatter (`currency`, `history_window_days`) + GFM tables under `## Trains` and `## Stays`. Parser is in `src/kelly/md_config.py`. See [`config/kelly.example.md`](config/kelly.example.md).
- Trip-id convention: trains live under `<trip_id>-out` and `<trip_id>-back` (or a single `<trip_id>` for one-way), the stay shares `<trip_id>`.

## Architecture

```
kelly.md  →  md_config (Pydantic)  →  trip_planner.plan_trip(trip_id)
                                          ├── train_service → playwright_eurostar (patchright)
                                          ├── stay_service  → providers/airbnb (pyairbnb)
                                          └── shortlist + history append (SqliteHistoryStore)
```

Layer responsibilities:

| Layer | Code | What it does |
|-------|------|--------------|
| **Config** | `md_config.py` | Pydantic v2 models (`TrainRow`, `StayRow`, `KellyFrontmatter`, `KellyConfig`); GFM table parser; section finder. |
| **Providers** | `providers/airbnb.py`, `providers/playwright_eurostar.py` | Direct external calls. Lazy-imports `pyairbnb` / `patchright` so the module loads even without browsers/deps; surfaces a clean `error` string on the result if so. |
| **Services** | `services/train_service.py`, `services/stay_service.py`, `services/trip_planner.py` | Orchestrate provider calls, persist to history, build the curated shortlist (Eurostar 9-pax cap → Groups Desk hint, Paris central bbox filter, deep-link URL pre-filling). |
| **History** | `history_store.py` | `SqliteHistoryStore` with `train_observations` and `stay_observations` tables; `train_key()` / `stay_key()` helpers; `open_default_store()` opens at `KELLY_DATA_DIR/kelly_history.sqlite`. |
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

`src/kelly/mcp_server.py` defines all `@mcp.tool()` and `@mcp.resource(...)` functions against a single `FastMCP` instance. **4 tools + 1 resource:** `kelly_load_config`, `kelly_eurostar_search`, `kelly_airbnb_search`, `kelly_plan_trip`, plus the `kelly://config` resource (the current `kelly.md` as Markdown). Tools accept `persist: bool = True`; pass `False` to skip the SQLite append.

## Testing notes

- `pyproject.toml` sets `pythonpath = ["src"]`, so import as `from kelly....` (no `src.` prefix).
- HTTP / scraper calls are always mocked at the service boundary: `@patch("kelly.services.train_service.search_eurostar")` and `@patch("kelly.services.stay_service.search_airbnb")`. Follow this pattern — never hit live providers from tests.
- When touching provider JSON handling, add a mocked fixture rather than relying on live API schema (Airbnb's GraphQL changes shape; Eurostar's DOM does too).

## Conventions

- Python ^3.11; Ruff line-length 100, target-version `py311`.
- Pydantic v2 models for config — normalize city codes to upper, class to `{standard, standard_premier, business_premier}`, operator to `{eurostar}`.
- Prefer `Decimal` for prices in provider code; convert to `float` only at the SQLite boundary.
- Heuristics (party-size warnings, central-area filters, target-total checks) are explicit and capped — they're planning hints, not booking decisions.
