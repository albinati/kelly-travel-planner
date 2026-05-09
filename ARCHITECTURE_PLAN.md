# Kelly — architecture

A small, focused tool for planning **group trips** (Eurostar trains + Airbnb stays) from a single Markdown config. Earlier versions tried to be a general flight/awards aggregator; that scope was cut in 0.2.0 — see `git log` if you want the history.

## 0. Why

Group trips don't fit a flight aggregator. The hard parts are:

- **Group passenger composition** — Eurostar fare bands (`infant 0–3 / child 4–11 / youth 12–25 / adult / senior`) are different from Airbnb's (`<2 / 2–12 / 13+`); a 10-person family of mixed ages is annoying to coordinate by hand.
- **9-pax Eurostar cap** — single-transaction limit on the website. Parties of 10+ have to either route through the Groups Desk or split the booking.
- **Whole-listing filters** — Airbnb's UI buries bedrooms-min, walkable-to-transit, and central-area requirements; what you actually want is "5BR within 10 min of Châtelet, total ≤ €3000".

Kelly takes those constraints from a Markdown table and runs the searches.

## 1. Pipeline

```
config/kelly.md
    ├── parsed by md_config.py (Pydantic v2 models, GFM tables)
    │
    ▼
trip_planner.plan_trip(trip_id)
    ├── search_train(<trip_id>-out)   → playwright_eurostar (patchright Chromium)
    ├── search_train(<trip_id>-back)  → playwright_eurostar
    ├── search_stay(<trip_id>)        → providers/airbnb (pyairbnb GraphQL)
    │
    ├── persist train_observations / stay_observations  (SqliteHistoryStore)
    │
    └── shortlist:
        ├── trains: cheapest journey per leg + per-pax-mix booking groups
        └── stays:  central-area filter, bedrooms_min, max_total, deep-link URL
```

## 2. Modules

```
src/kelly/
├── __init__.py             — version
├── settings.py             — env-backed paths (KELLY_CONFIG_PATH, KELLY_DATA_DIR), .env loader
├── md_config.py            — TrainRow / StayRow / KellyFrontmatter / KellyConfig + parser
├── history_store.py        — SqliteHistoryStore + TrainObservation / StayObservation
├── providers/
│   ├── airbnb.py           — pyairbnb wrapper, OSM Nominatim geocoding for `area`
│   └── playwright_eurostar.py — patchright scraper, station code map, age-band classifier
├── services/
│   ├── train_service.py    — orchestrates eurostar search + history append
│   ├── stay_service.py     — orchestrates airbnb search + history append
│   └── trip_planner.py     — fans out by trip_id, curates shortlist, builds booking URLs
├── mcp_server.py           — FastMCP stdio server (4 tools + 1 resource)
└── cli.py                  — Typer CLI (`kelly plan`, `kelly config-show`, `kelly version`)
```

## 3. External dependencies

| Lib | Purpose | Why this lib |
|-----|---------|--------------|
| `pyairbnb` | Airbnb staysSearch GraphQL | Free, no key. Uses the lower-level `search.get` path because `search_first_page` mis-shapes responses upstream. |
| `patchright` | Stealth Playwright fork | Eurostar protects the search page with DataDome; stock Playwright gets blocked. Patchright bypasses it. |
| `httpx` | OSM Nominatim geocoding | Already a Pydantic transitive; no need for `requests`. |
| `frontmatter` | YAML frontmatter parsing | Single-purpose, no surprises. |
| `pydantic` v2 | Config models | Strict validation on city codes, fare classes, age lists. |
| `typer` | CLI | Generates `--help` and types arguments cleanly. |
| `mcp` (optional) | stdio MCP server | Behind the `[mcp]` extra. |

`pyairbnb` and `patchright` are imported lazily inside the provider call sites so the module graph still loads if a user installs only `[mcp]` (e.g. for tests or for inspecting the config shape).

## 4. History

Two tables, both keyed by trip_key (string concatenation; see `train_key()` / `stay_key()`):

```sql
train_observations(trip_key, departure_date, best_per_adult_amount,
                   currency, journey_count, pax_adult_eq, pax_child, pax_infant, …)
stay_observations (trip_key, check_in, check_out, best_total_amount,
                   currency, listings_count, bedrooms_min, pax_*, …)
```

One row per `search_train` call **per requested date** (Eurostar searches scan a window), one row per `search_stay` call. Anomaly labelling on top of these isn't implemented — meaningful baselines need more samples than we have right now.

## 5. MCP server

Tools are intentionally thin:

| Tool | Layer call |
|------|------------|
| `kelly_load_config` | `md_config.load_kelly_config` → JSON |
| `kelly_eurostar_search` | builds an ad-hoc `TrainRow` → `train_service.search_train` |
| `kelly_airbnb_search`   | builds an ad-hoc `StayRow`  → `stay_service.search_stay`  |
| `kelly_plan_trip` | `trip_planner.plan_trip(trip_id)` |

Resource `kelly://config` returns the raw `kelly.md`. No HTTP transport — stdio only.

## 6. Run paths

- **`./scripts/setup-venv.sh`** — bootstraps Poetry 2.x into `.tools/venv-poetry` if missing, creates an in-project `.venv`, seeds `.env` and `config/kelly.md`. Defaults to `--extras mcp`.
- **`make docker-build` / `docker-mcp`** — multi-stage non-root image; `INSTALL_BROWSERS=true` (default) bakes Chromium for Eurostar, `INSTALL_BROWSERS=false` for a flights-style lean image with Eurostar disabled at run time.
- **OpenClaw on a VPS** — see [docs/OPENCLAW-VPS.md](docs/OPENCLAW-VPS.md) for the `curl | bash` install + MCP registration.

## 7. Security

There's nothing to leak: no API keys are required. Still, never `git add .env` (gitignored) or check in `data/*.sqlite` (gitignored). The Docker image runs as uid/gid 1000 with `tini` for signal handling.

## 8. What's deferred

- Anomaly / "is this a good price?" labelling for trains and stays. Needs more data than the few samples a casual user has after a couple of weeks.
- Other train operators (TGV INOUI, OUIGO, Trainline aggregator). Schema shape would be similar; provider switch needs an `operator: trenitalia | tgv | …` enum extension.
- Booking automation — out of scope; Kelly produces deep links, the user clicks them.
