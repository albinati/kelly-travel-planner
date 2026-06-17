---
name: kelly-travel-planner
description: OpenClaw skill — natural interface to Kelly, the household trip planner. Kelly owns the kelly.md trip catalogue, scrapes Eurostar fares (headless Chromium), queries Airbnb (GraphQL) + LiteAPI hotels, runs a BA Avios award-flight radar (seats.aero), solves multi-modal itineraries under date/weekend/open-jaw constraints, compares options all-in across currencies, and persists searches + a traveller profile + trip-session dossiers to local SQLite. Use whenever the user wants to plan a trip — including from a fuzzy "somewhere nice, no fixed idea" brief — look up Eurostar/Avios/hotel options, build a budget comparison, or compare against a previous price.
metadata: {"openclaw": {"requires": {"command": ["kelly"]}, "primaryEnv": "KELLY_CONFIG_PATH", "emoji": "🚆"}}
---

# Kelly — group trip planner

Kelly does two things:

1. **Declared trips** — Eurostar trains + Airbnb/LiteAPI stays declared once in `kelly.md` and shortlisted on demand (`kelly plan-trip`). The original core.
2. **Open-ended planning** — turn a fuzzy "we want a weekend somewhere nice, no fixed idea" brief into ranked, bookable options: capture a profile, discover destinations, run the **BA Avios award-flight radar** (seats.aero), solve multi-modal itineraries under real constraints (weekend shape, nights, **open-jaw**, seats), and compare options **all-in** across currencies. See [Planning from a fuzzy idea](#planning-a-trip-from-a-fuzzy-idea--the-playbook).

No keys for Eurostar/Airbnb (`patchright` drives headless Chromium, `pyairbnb` hits Airbnb's GraphQL). Avios needs `SEATSAERO_API_KEY`; hotels need `LITEAPI_API_KEY` (sandbox key = test data — lean on Airbnb/web for real stay prices).

## How you drive Kelly — the CLI, not a server

You reach Kelly by running its **`kelly` CLI** in the shell. **Kelly is CLI-first on purpose: there is no resident server.** Each `kelly <command>` is an ephemeral subprocess — it starts, does one job, prints a JSON result to stdout, and **exits**. Memory is held only for the duration of the call and reclaimed by the OS on exit.

This is the deliberate fix for the **"ghost session" memory problem**: a per-session MCP server left orphaned `kelly-mcp` processes resident (each ~75 MB idle, +500 MB if a Chromium scrape was mid-flight), and they stacked up across sessions until the VPS thrashed. The CLI model makes that **structurally impossible** — nothing stays resident, so nothing can be orphaned. The cost is a ~1 s cold start per call (Python re-imports); irrelevant at trip-planning cadence. All state lives in SQLite (`KELLY_DATA_DIR/kelly_history.sqlite`), so ephemeral calls lose nothing between invocations.

Every command prints a single JSON object/array to stdout — parse that. Errors are surfaced as `{"error": "..."}` JSON or a clean message, not exceptions to retry blindly.

> Run `kelly --help` to list commands, `kelly <command> --help` for its flags. The `mcp_server.py` is kept in-tree but its `kelly-mcp` entrypoint is **disabled** (see `pyproject.toml`) — do not try to launch it; use the CLI.

## Three rules, in order

1. **Read the config before scraping.** Always run `kelly load-config` first to see the declared trips. Eurostar searches launch a headless Chromium (~500 MB resident *while the command runs*, freed on exit); never trigger one until you know what trip the user actually wants. Skim `kelly load-config`, name the candidate `trip_id`, then scrape.
2. **One trip = one `kelly plan-trip` call.** Don't fan out separate `kelly eurostar-search` + `kelly airbnb-search` invocations to assemble what `kelly plan-trip` already returns. It runs them in sequence within one process so Chromium is launched and torn down cleanly. Parallel ad-hoc scrape commands = parallel Chromium = OOM risk on small VPS plans.
3. **`--no-persist` for what-ifs.** The default writes to SQLite — that's the price-baseline log, not a scratchpad. For "what if we left a day later" or "show me an alternate city" exploration, pass `--no-persist` so the history table only records intentional searches.

For zero-cost orientation, `kelly config-show` prints the parsed `kelly.md` — no Chromium, no HTTP.

---

## Command catalogue

25 commands over the same `services/*` the old MCP tools used, in groups. Within a group, lighter commands first.

**Declared-trip core** (the original Eurostar+stay flow):

| Command | Cost | What it does |
|---|---|---|
| `kelly load-config` | **none** (file read) | Parse `kelly.md` → JSON of `frontmatter`, `trains[]`, `stays[]`. Returns trip ids, dates, party sizes. Always start here. |
| `kelly config-show` | **none** | The parsed `kelly.md` as JSON — the cheap human-readable view. Use for "what trips are declared?". |
| `kelly airbnb-search` | low (HTTP only) | One-shot Airbnb whole-listing search via pyairbnb. Geocodes the area string via OSM Nominatim, returns ~18 listings. Cheap and fast. |
| `kelly eurostar-search` | **high** (Chromium) | One-shot Eurostar fare scrape for a city pair + date window via patchright. Spawns a headless Chromium that walks each requested date (capped at 7). Use only when the city pair isn't already in `kelly.md`. |
| `kelly plan-trip <trip_id>` | **high** (Chromium ×1–2) | Look up `<trip_id>-out` / `<trip_id>-back` (or single `<trip_id>`) trains and the `<trip_id>` stay, fan them out, return a curated shortlist with booking URLs. Chromium launches once for outbound and once for return — sequentially, not in parallel. (`kelly plan` is a legacy alias.) |

**Open-ended planning** (the fuzzy-idea flow — see the playbook below):

| Command | Cost | What it does |
|---|---|---|
| `kelly avios-search <origin_airports> <destination_airports> <date_start> <date_end> <pax>` | medium (HTTP, paced) | BA **award-flight radar** via seats.aero. The five positionals are required (airport lists are CSV, e.g. `LHR,LGW`); options: `--cabin economy`, `--only-direct/--no-only-direct`, `--persist/--no-persist`. Returns BA-operated award availability: **seats + real depart/arrive times + Avios cost** (from the BA Reward Flight Saver chart). The `partner_mileage_cost` field is the partner program's miles and is **NOT** the Avios price — never quote it as Avios. Backs off on HTTP 403/429; don't hammer it in tight loops. Needs `SEATSAERO_API_KEY`. |
| `kelly solve-itinerary <origin_airports> <destination_airports> <date_start> <date_end> <pax>` | medium | Constraint-solver: same five positionals as `avios-search`. Options shape the search: weekday shape (`--out-days`/`--return-days`), nights (`--min-nights`/`--max-nights`), `--min-seats`, **open-jaw** (`--open-jaw`, default on — out lands at A, back departs from B; this is how Bari-in/Brindisi-out is found), and `--modes avios,eurostar`. Ranking: cheapest all-Avios first, then cash, ties by shortest total travel time. |
| `kelly budget-compare '<options_json>'` | low (FX only) | `options_json` is a positional JSON string; `--target-currency` defaults GBP. Converts every line item to one currency (ECB-cached FX) and ranks options by **all-in** total with deltas vs the cheapest. Stay lines MUST be all-in (`price_all_in`). Avios **points** are reported separately, never folded into the cash total (only the Avios cash taxes are). |

**Profile + dream-box** (reuse across trips):

| Command | What it does |
|---|---|
| `kelly profile-get <profile_id>` / `kelly profile-set <profile_id> <name> --payload-json '<json>'` | Read/upsert the traveller profile (who travels, home airports, food/relax prefs, hard exclusions, Avios-or-cash). **A profile already exists: `albinati`** (the family — Theo in a London state school, so family trips are pinned to half-term). Read it at the start of every plan. |
| `kelly dreambox-add …` / `kelly dreambox-list [--profile-id …]` / `kelly dreambox-remove <id>` | Park a great destination that doesn't fit the current window instead of forcing it; recall parked ideas next time. (Thin shim over trip-sessions with `status=idea`.) |

**Trip sessions / dossiers** (an intent + dated option snapshots across a lifecycle `idea → active → shortlisted → booked → archived`):

| Command | What it does |
|---|---|
| `kelly session-list [--profile-id …] [--status …]` | List dossiers newest-first (without their option snapshots). |
| `kelly session-get <id>` | One dossier **plus its dated option snapshots** (a price time-series). |
| `kelly session-create <title> [--destination …] [--intent-json …] [--status active]` | Open a dossier — a search **intent** + a lifecycle status. |
| `kelly session-update <id> [--status …] [--notes …] [--intent-json …]` | Patch provided fields (blanks untouched); the usual way to move `status` along (e.g. `booked`, `archived`). |
| `kelly session-attach-option <session_id> <kind>` | Append a **dated** option snapshot (append-only). `kind` is positional ∈ `cash_flight\|avios\|train\|stay\|experience\|car\|local\|other`; options: `--amount … --currency …`, `--avios-points …`, `--label …`, `--source …`, `--payload-json …`. Re-attaching later builds a price history. Avios points are a separate currency, never folded into cash. |
| `kelly session-remove <id>` | Delete a dossier + its options. |

**Bookings, money, calendar:**

| Command | What it does |
|---|---|
| `kelly convert <amount> <from_ccy> <to_ccy> [--as-of …]` | ECB-cached FX converter (string amounts, Decimal-safe). |
| `kelly log-booking …` / `kelly trip-summary <trip_id> [--currency …]` | Log a booked artifact (+ its money); roll all legs into one currency. |
| `kelly booking-event-draft …` | Build a Google Calendar event spec (enriched with the logged total). Kelly never calls Calendar itself — pass the spec to OpenClaw's Calendar tool. |
| `kelly log-expense …` / `kelly expense-balances` | Splitwise: log a shared expense / read balances (needs Splitwise env). |
| `kelly estimate-hosting <hosting_id> …` | Marginal cost of hosting a visiting party from `## Hosting` + `## Daytrips`. |

---

## The standard workflow

The shape of every interaction, in order:

```
1. kelly load-config              ← cheap, no scrape; orient yourself
2. (optional) kelly airbnb-search ← cheap; explore stay options ad-hoc
3. kelly plan-trip <trip_id>      ← single expensive call, all-in-one
4. summarize the shortlist        ← stop; do not re-run to "verify"
```

**Don't** run `kelly eurostar-search` and `kelly airbnb-search` separately when a `trip_id` exists for it — `kelly plan-trip` already does that and curates the result (central-Paris filter, 9-pax Eurostar cap → Groups Desk hint, deep-link URLs pre-filled with the right pax mix).

**Don't** issue parallel scrape commands. Each `kelly eurostar-search` (and each direction inside `kelly plan-trip`) spawns its own Chromium. On a 4 GB VPS, two concurrent scrapes can OOM the host. Run them one at a time.

**Don't** loop `kelly eurostar-search` over a wide date range. The command already walks every date in `[date_start, date_end]` (capped at 7); run it once with the right window, not seven times.

---

## Planning a trip from a fuzzy idea — the playbook

When the user has no fixed destination ("a weekend somewhere nice, just us"), don't ask them to pick a city. Run this flow. Kelly owns the deterministic parts (availability, pairing, budget); **you** own the judgment (profiling, discovery, taste, the final checklist).

```
0. profile   → kelly profile-get albinati   (capture once with profile-set if absent)
1. discover  → propose candidate destinations that fit the profile (incl. one "break the box")
2. radar     → kelly avios-search   across candidate airports + the date window
3. solve     → kelly solve-itinerary  weekend shape + nights + open-jaw + min-seats=pax
4. stays     → research per surviving option (web/Airbnb), ALWAYS all-in prices
5. compare   → kelly budget-compare   rank options all-in, Avios shown separately
6. present   → 2–4 ranked options with honest trade-offs; recommend one
7. capture   → kelly session-create / session-attach-option to dossier the run; book when ready
```

**Step 0 — profile.** Run `kelly profile-get albinati`. If empty/missing, do a short interview and `kelly profile-set`: who travels (+ any dietary/comfort constraints), home airports, the date window and weekend shape, a **hard budget**, food/relax priorities, hard exclusions, and **Avios or cash**. The profile is reused next time — don't re-interview. **Family trips must avoid Theo's term time** — verify the half-term window before searching.

**Step 1 — discover, don't wait for a city.** From the profile, propose a handful of destinations that fit. Include one non-obvious "break the box" pick. If a great idea needs more nights than the window allows, **park it** with `kelly dreambox-add` rather than forcing it.

**Step 2 — the Avios radar is the scarce constraint.** Award seats, not hotels, are what's rare in peak season. Run `kelly avios-search` over the candidate airports first; it shrinks the field fast. **In UK half-term the *return* leg is the usual bottleneck** — outbound award space loads but the islands→London return often shows nothing. Check both directions before claiming a round-trip exists. The Avios number comes from the BA chart — `partner_mileage_cost` is a different program's miles, never quote it as Avios.

**Step 3 — solve under real constraints.** `kelly solve-itinerary --open-jaw` finds routings a human misses (fly into one airport, out of another). Be honest about trade-offs: in peak season a **Sunday-afternoon award return often doesn't exist** — the real choice is a night flight home or taking the Monday. Say so plainly.

**Step 4 — stays, all-in only.** Research stays per surviving option. Use the **all-in** price (cleaning/fees/tourist tax included), never a raw nightly or `price_total` — mixing the two has produced real four-figure errors. The LiteAPI hotel channel is sandbox (test data); trust Airbnb/web for real stay prices.

**Step 5 — compare all-in.** Feed the options to `kelly budget-compare` as line items (transport, stay, experience, car, local). It converts currencies and ranks by all-in total with deltas. Avios points stay separate from the cash total (only the Avios taxes are cash).

**Step 6 — present honestly.** 2–4 options, each with its all-in number, the flight shape (e.g. "Sun night return, home 00:05"), and the one real downside. Recommend one and say why. Respect "max one booked thing" if the profile says relax.

**Step 7 — capture + book.** Dossier the run with `kelly session-create` (the intent) + `kelly session-attach-option` per option (dated snapshots → a re-quotable price history). Produce a buy-it-all checklist in **booking order — scarcest award seat first** (a 3-seat return goes before the hotel). Then `kelly booking-event-draft` per leg for the calendar, and `kelly log-booking` once they actually book so `kelly trip-summary` stays truthful. Move the dossier to `status=booked` with `kelly session-update`.

Keep exploratory searches `--no-persist`; only intentional, committed searches belong in the history baseline.

---

## Reading the result

`kelly plan-trip` prints:

```json
{
  "trip_id": "...",
  "trains": { "outbound": {...}, "return": {...} },   // raw search results
  "stays":  { "primary":  {...} },
  "shortlist": {
    "eurostar_outbound": [...],   // cheapest journey per (date, depart_time)
    "eurostar_return":   [...],
    "airbnb":            [...]    // top 5 sorted by price asc, rating tiebreaker
  },
  "missing": []                   // labels for any rows that weren't found
}
```

Each Eurostar shortlist entry includes `booking_groups`: a deep-link URL pre-filled with the pax mix (split into two groups when the party exceeds 9 — Eurostar's web booking cap). When `booking_groups.length > 1`, surface the Groups Desk hint that the search note carries.

Each Airbnb shortlist entry includes a `url` already pre-filled with `check_in`, `check_out`, `adults`, `children`, `infants` so the user lands on the room with the right pax loaded.

When the result has `error` or `note` fields populated on the inner `result` blocks, surface them — they explain navigation failures, missing station codes, or party-size warnings.

---

## What NOT to do

- **Don't** edit `config/kelly.md` directly through OpenClaw. The file is the user's source of truth for declared trips; surface what's there and let the user edit it themselves. New trips → tell them what row to add.
- **Don't** run any command with persist on (the default) unless the search is intentional and the user is committed to a date window. History is for trend analysis, not noise.
- **Don't** retry a failed Eurostar scrape immediately. `error` is surfaced cleanly on the result; if patchright/Chromium isn't installed (`error: "patchright not installed"`), tell the user to run `patchright install chromium` on the VPS — don't loop.
- **Don't** try to launch `kelly-mcp` or dial Airbnb/Eurostar/seats.aero APIs directly. The `kelly` CLI is the only sanctioned surface; if a capability is missing, request a new subcommand rather than improvising.

---

## Failure modes to recognise

| Symptom | What it means | What to do |
|---|---|---|
| `error: "patchright not installed"` | The VPS hasn't run `patchright install chromium`. | Tell the user once; don't retry. |
| `error: "unknown origin/destination station code 'XYZ'"` | City code isn't in `_STATION_CODES`. | List the known codes (LON, PAR, BRU, AMS, LIL, MLV) from the result; suggest the user pick one. |
| `note: "<date>: navigation failed"` | DataDome blocked or the page timed out. | Surface the note. A retry on the same command is fine; don't spawn a parallel one. |
| `error: "could not geocode area …"` | OSM Nominatim returned nothing. | Tell the user the area string didn't resolve; suggest a more standard spelling (e.g. "Paris, France"). |
| `note: "no BA-operated award availability matched in the window"` | seats.aero has no BA award space for that leg/window (common for half-term returns). | Say so plainly; check the other direction / nearby dates; consider Avios-out + cash-return. |
| `missing: ["trains.outbound", ...]` | No row matches `<trip_id>-out` (or `-back`, or stay). | Show the user what's declared (`kelly load-config`) and ask which `trip_id` they meant. |
| `{"error": "..."}` mentioning a key | A keyed channel (Avios/LiteAPI/Splitwise) lacks its env var. | Report the missing key; the rest of Kelly still works without it. |

---

## Operational notes

- **Ephemeral, no resident process.** Every `kelly <command>` runs and exits — zero idle footprint, no `kelly-mcp` daemon, **no ghost sessions**. The only RAM cost is *during* a command. Cold start is ~1 s (lazy imports keep it small). This is why Kelly is CLI-first rather than an MCP server: it has no background loop (unlike HEM, which must be a daemon) and all its state is in SQLite, so a resident process buys nothing and risks the orphaned-process memory leak.
- **Chromium is the long pole.** Idle is near-zero; every Eurostar scrape spikes to ~500 MB+ for the duration of that one command and releases on exit. On a 2 GB VPS, run scrapes one at a time — don't kick off two `kelly eurostar-search` / `kelly plan-trip` commands concurrently.
- **Airbnb side is light.** `kelly airbnb-search` is a single HTTP round-trip + a Nominatim geocode. Use it freely.
- **State is the SQLite file.** History, the traveller profile, dream-box, and trip-session dossiers all live in `KELLY_DATA_DIR/kelly_history.sqlite`. Ephemeral CLI calls share it; nothing is lost between invocations. Direct inspection is fine if the user needs raw trend data.
- **Currency is informational.** Eurostar's UI returns per-region currency regardless of the `currency` field; trust the `price_currency` on each journey, not the requested one.

---

## Quick reference: trip declaration

A trip in `kelly.md` is two `## Trains` rows (`<trip_id>-out` / `<trip_id>-back`) and one `## Stays` row sharing the trip id:

```markdown
| paris-weekend-out  | eurostar | LON | PAR | 2026-08-20 | 2026-08-22 | standard | 2 | 0 | 0 |  | 400 | Outbound, flexible ±1 day |
| paris-weekend-back | eurostar | PAR | LON | 2026-08-24 | 2026-08-25 | standard | 2 | 0 | 0 |  | 400 | Return |
| paris-weekend      | Paris    | 2026-08-20 | 2026-08-25 | 2 |  | 1 | Châtelet-Les Halles | 10 | 1200 | Whole-listing, central |
```

`<trip_id>-out` and `<trip_id>-back` (or a single `<trip_id>` for one-way) on the train side; `<trip_id>` on the stay side. That's the convention `kelly plan-trip` looks up.

Eurostar fare bands (mapped automatically from `children_ages`):
- 0–3 → infant (lap-held, free)
- 4–11 → child
- 12–25 → youth (discounted)
- 26–59 → adult
- 60+ → senior

Airbnb age bands (also automatic): <2 = infant, 2–12 = child, 13+ rolls into adults.
