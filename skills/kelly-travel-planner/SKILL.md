---
name: kelly-travel-planner
description: OpenClaw skill — natural interface to Kelly, the household trip planner. Kelly owns the kelly.md trip catalogue, scrapes Eurostar fares (headless Chromium), queries Airbnb (GraphQL) + LiteAPI hotels, runs a BA Avios award-flight radar (seats.aero), solves multi-modal itineraries under date/weekend/open-jaw constraints, compares options all-in across currencies, and persists searches + a traveller profile + a dream-box of parked trips to local SQLite. Use whenever the user wants to plan a trip — including from a fuzzy "somewhere nice, no fixed idea" brief — look up Eurostar/Avios/hotel options, build a budget comparison, or compare against a previous price.
metadata: {"openclaw": {"requires": {"command": ["kelly-mcp"]}, "primaryEnv": "KELLY_CONFIG_PATH", "emoji": "🚆"}}
---

# Kelly — group trip planner

Kelly does two things:

1. **Declared trips** — Eurostar trains + Airbnb/LiteAPI stays declared once in `kelly.md` and shortlisted on demand (`kelly_plan_trip`). The original core.
2. **Open-ended planning** — turn a fuzzy "we want a weekend somewhere nice, no fixed idea" brief into ranked, bookable options: capture a profile, discover destinations, run the **BA Avios award-flight radar** (seats.aero), solve multi-modal itineraries under real constraints (weekend shape, nights, **open-jaw**, seats), and compare options **all-in** across currencies. See [Planning from a fuzzy idea](#planning-a-trip-from-a-fuzzy-idea--the-playbook).

No keys for Eurostar/Airbnb (`patchright` drives headless Chromium, `pyairbnb` hits Airbnb's GraphQL). Avios needs `SEATSAERO_API_KEY`; hotels need `LITEAPI_API_KEY` (sandbox key = test data — lean on Airbnb/web for real stay prices).

You reach Kelly through one MCP server, registered in `openclaw.json` as `kelly` (stdio transport). Tool names appear with the `kelly_` prefix.

## Three rules, in order

1. **Read the config before scraping.** Always call `kelly_load_config` first to see the declared trips. Eurostar searches launch a headless Chromium (~500MB resident while it runs); never trigger one until you know what trip the user actually wants. Skim `kelly_load_config` output, name the candidate `trip_id`, then scrape.
2. **One trip = one `kelly_plan_trip` call.** Don't fan out separate `kelly_eurostar_search` + `kelly_airbnb_search` invocations to assemble what `kelly_plan_trip` already returns. The orchestrator runs them in sequence within a single MCP call so Chromium is launched and torn down cleanly. Parallel ad-hoc calls = parallel Chromium = OOM risk on small VPS plans.
3. **`persist=False` for what-ifs.** The default is `persist=True` and writes to SQLite (`KELLY_DATA_DIR/kelly_history.sqlite`) — that's the price-baseline log, not a scratchpad. For "what if we left a day later" or "show me an alternate city" exploration, pass `persist=False` so the history table only records intentional searches.

For zero-cost orientation, pull `kelly://config` (the raw `kelly.md`) — no Chromium, no HTTP, just the Markdown.

---

## Tool catalogue

19 tools + one resource, in groups. Within a group, lighter tools first.

**Declared-trip core** (the original Eurostar+stay flow):

| Tool | Cost | What it does |
|---|---|---|
| `kelly_load_config` | **none** (file read) | Parse `kelly.md` → JSON of `frontmatter`, `trains[]`, `stays[]`. Returns trip ids, dates, party sizes. Always start here. |
| `kelly_airbnb_search` | low (HTTP only) | One-shot Airbnb whole-listing search via pyairbnb. Geocodes the area string via OSM Nominatim, returns ~18 listings. Cheap and fast. |
| `kelly_eurostar_search` | **high** (Chromium) | One-shot Eurostar fare scrape for a city pair + date window via patchright. Spawns a headless Chromium that walks each requested date (capped at 7). Use only when the user wants a city pair that isn't already in `kelly.md`. |
| `kelly_plan_trip` | **high** (Chromium ×1–2) | Look up `<trip_id>-out` / `<trip_id>-back` (or single `<trip_id>`) trains and the `<trip_id>` stay, fan them out, return a curated shortlist with booking URLs. Chromium launches once for outbound and once for return — sequentially, not in parallel. |

**Open-ended planning** (the fuzzy-idea flow — see the playbook below):

| Tool | Cost | What it does |
|---|---|---|
| `kelly_avios_search(origin_airports, destination_airports, date_start, date_end, pax, cabin="economy", only_direct=True)` | medium (HTTP, paced) | BA **award-flight radar** via seats.aero. CSV airport lists. Returns BA-operated award availability: **seats + real depart/arrive times + Avios cost** (from the BA Reward Flight Saver chart). The `partner_mileage_cost` field is the partner program's miles and is **NOT** the Avios price — never quote it as Avios. Backs off on HTTP 403/429; don't hammer it in tight loops. Needs `SEATSAERO_API_KEY`. |
| `kelly_solve_itinerary(origin_airports, destination_airports, date_start, date_end, pax, out_days="Thu,Fri", return_days="Sun", min_nights=2, max_nights=3, open_jaw=True, min_seats=2, modes="avios")` | medium | Constraint-solver: pairs outbound/return legs into ranked itineraries honouring weekday shape, nights, min-seats, and **open-jaw** (out lands at A, back departs from B — this is how Bari-in/Brindisi-out is found). Ranking: cheapest all-Avios first, then cash, ties by shortest total travel time. |
| `kelly_budget_compare(options_json, target_currency="GBP")` | low (FX only) | Convert every line item to one currency (ECB-cached FX) and rank options by **all-in** total with deltas vs the cheapest. Stay lines MUST be all-in (`price_all_in`). Avios **points** are reported separately, never folded into the cash total (only the Avios cash taxes are). |

**Profile + dream-box** (reuse across trips):

| Tool | What it does |
|---|---|
| `kelly_profile_get(profile_id)` / `kelly_profile_set(profile_id, name, payload_json)` | Read/upsert the traveller profile (who travels, home airports, food/relax prefs, hard exclusions, Avios-or-cash). Read it at the start of every plan; capture it once if absent. |
| `kelly_dreambox_add(...)` / `kelly_dreambox_list(profile_id?)` / `kelly_dreambox_remove(id)` | Park a great destination that doesn't fit the current window (e.g. needs more nights) instead of forcing it; recall parked ideas next time. |

**Bookings, money, calendar:**

| Tool | What it does |
|---|---|
| `kelly_convert(amount, from_ccy, to_ccy, as_of?)` | ECB-cached FX converter (string amounts, Decimal-safe). |
| `kelly_log_booking(...)` / `kelly_trip_summary(trip_id, currency)` | Log a booked artifact (+ its money); roll all legs into one currency. |
| `kelly_booking_event_draft(...)` | Build a Google Calendar event spec (enriched with the logged total). Kelly never calls Calendar itself — pass the spec to the agent's Calendar MCP tool. |
| `kelly_log_expense(...)` / `kelly_expense_balances()` | Splitwise: log a shared expense / read balances (needs Splitwise env). |
| `kelly_estimate_hosting(...)` | Marginal cost of hosting a visiting party from `## Hosting` + `## Daytrips`. |

### `kelly://config` resource

The current `kelly.md` as Markdown. Read this when the user asks "what trips are declared?" — no parsing, no provider calls. Strictly cheaper than `kelly_load_config` if you only need the human-readable view.

---

## The standard workflow

The shape of every interaction, in order:

```
1. kelly_load_config              ← cheap, no scrape; orient yourself
2. (optional) kelly_airbnb_search ← cheap; explore stay options ad-hoc
3. kelly_plan_trip <trip_id>      ← single expensive call, all-in-one
4. summarize the shortlist        ← stop; do not re-call to "verify"
```

**Don't** call `kelly_eurostar_search` and `kelly_airbnb_search` separately when a `trip_id` exists for it — `kelly_plan_trip` already does that and curates the result (central-Paris filter, 9-pax Eurostar cap → Groups Desk hint, deep-link URLs pre-filled with the right pax mix).

**Don't** issue parallel scrape calls. Each `kelly_eurostar_search` (and each direction inside `kelly_plan_trip`) spawns its own Chromium. On a 4 GB VPS, two concurrent scrapes can OOM the host.

**Don't** loop `kelly_eurostar_search` over a wide date range. The provider already walks every date in `[date_start, date_end]` (capped at 7); call it once with the right window, not seven times with single dates.

---

## Planning a trip from a fuzzy idea — the playbook

When the user has no fixed destination ("a weekend somewhere nice, just us"), don't ask them to pick a city. Run this flow. Kelly owns the deterministic parts (availability, pairing, budget); **you** own the judgment (profiling, discovery, taste, the final checklist).

```
0. profile   → kelly_profile_get   (capture once with kelly_profile_set if absent)
1. discover  → propose candidate destinations that fit the profile (incl. one "break the box")
2. radar     → kelly_avios_search   across candidate airports + the date window
3. solve     → kelly_solve_itinerary  weekend shape + nights + open_jaw + min_seats=pax
4. stays     → research per surviving option (web/Airbnb), ALWAYS all-in prices
5. compare   → kelly_budget_compare   rank options all-in, Avios shown separately
6. present   → 2–4 ranked options with honest trade-offs; recommend one
7. book      → buy-it-all checklist in booking order; then booking_event_draft + log_booking
```

**Step 0 — profile.** Read `kelly_profile_get`. If empty, do a short interview and `kelly_profile_set`: who travels (+ any dietary/comfort constraints), home airports, the date window and weekend shape (Thu/Fri-out → Sun/Mon-back, N nights), a **hard budget**, food/relax priorities, hard exclusions, and **Avios or cash**. The profile is reused next time — don't re-interview.

**Step 1 — discover, don't wait for a city.** From the profile, propose a handful of destinations that fit (food, sea, history, calm, value). Include one non-obvious "break the box" pick. If a great idea needs more nights than the window allows, **park it** with `kelly_dreambox_add` rather than forcing it.

**Step 2 — the Avios radar is the scarce constraint.** Award seats, not hotels, are what's rare in peak season. Run `kelly_avios_search` over the candidate airports first; it shrinks the field fast. The Avios number comes from the BA chart — the `partner_mileage_cost` is a different program's miles, never quote it as Avios. The tool paces itself; don't fire it in tight loops.

**Step 3 — solve under real constraints.** `kelly_solve_itinerary` with `open_jaw=True` finds routings a human misses (fly into one airport, out of another). Be honest about the trade-offs it surfaces: in peak season a **Sunday-afternoon award return often doesn't exist** — the real choice is a night flight home or taking the Monday. Say so plainly.

**Step 4 — stays, all-in only.** Research stays per surviving option. Use the **all-in** price (cleaning/fees/tourist tax included), never a raw nightly or `price_total` — mixing the two has produced real four-figure errors. The LiteAPI hotel channel is sandbox (test data); trust Airbnb/web for real stay prices.

**Step 5 — compare all-in.** Feed the options to `kelly_budget_compare` as line items (transport, stay, experience, car, local). It converts currencies and ranks by all-in total with deltas. Avios points stay separate from the cash total (only the Avios taxes are cash).

**Step 6 — present honestly.** 2–4 options, each with its all-in number, the flight shape (e.g. "Sun night return, home 00:05"), and the one real downside. Recommend one and say why. Respect "max one booked thing" if the profile says relax.

**Step 7 — book.** Produce a buy-it-all checklist in **booking order — scarcest award seat first** (a 3-seat return goes before the hotel). Give real links. Then `kelly_booking_event_draft` per leg for the calendar, and `kelly_log_booking` once they actually book so `kelly_trip_summary` stays truthful.

Keep exploratory searches `persist=False`; only intentional, committed searches belong in the history baseline.

---

## Reading the result

`kelly_plan_trip` returns:

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
- **Don't** call any tool with `persist=True` (the default) unless the search is intentional and the user is committed to a date window. History is for trend analysis, not noise.
- **Don't** retry a failed Eurostar search immediately. `error` is surfaced cleanly on the result; if patchright/Chromium isn't installed (`error: "patchright not installed"`), tell the user to run `patchright install chromium` on the VPS — don't loop.
- **Don't** dial Airbnb or Eurostar APIs directly. The MCP surface is the only sanctioned channel; if a capability is missing, request a new tool rather than improvising.

---

## Failure modes to recognise

| Symptom | What it means | What to do |
|---|---|---|
| `error: "patchright not installed"` | The VPS hasn't run `patchright install chromium`. | Tell the user once; don't retry. |
| `error: "unknown origin/destination station code 'XYZ'"` | City code isn't in `_STATION_CODES`. | List the known codes (LON, PAR, BRU, AMS, LIL, MLV) from the result; suggest the user pick one. |
| `note: "<date>: navigation failed"` | DataDome blocked or the page timed out. | Surface the note. A retry on the same call is fine; don't spawn a parallel one. |
| `error: "could not geocode area …"` | OSM Nominatim returned nothing. | Tell the user the area string didn't resolve; suggest a more standard spelling (e.g. "Paris, France"). |
| `missing: ["trains.outbound", ...]` | No row matches `<trip_id>-out` (or `-back`, or stay). | Show the user what's declared (`kelly_load_config`) and ask which `trip_id` they meant. |

---

## Operational notes

- **stdio transport, single agent.** Kelly currently ships only stdio MCP. OpenClaw spawns one `kelly-mcp` process per session. If the agent appears to fan out the same tool concurrently, that's a hosting-layer issue — the tool itself runs serially. Flag it; don't try to work around it.
- **Chromium is the long pole.** Lazy imports keep idle RAM small (~80 MB Python+MCP), but every Eurostar scrape spikes to ~500 MB+ for the duration of the search and releases on exit. On a 2 GB VPS, queue scrapes — don't parallelise.
- **Airbnb side is light.** `kelly_airbnb_search` is a single HTTP round-trip + a Nominatim geocode. Use it freely.
- **History queries.** Not yet exposed as tools; the SQLite file is at `KELLY_DATA_DIR/kelly_history.sqlite` for direct inspection if the user needs trend data. Anomaly labelling on top of the history isn't implemented.
- **Currency is informational.** Eurostar's UI returns per-region currency regardless of the `currency` field; trust the `price_currency` on each journey, not the requested one.

---

## Quick reference: trip declaration

A trip in `kelly.md` is two `## Trains` rows (`<trip_id>-out` / `<trip_id>-back`) and one `## Stays` row sharing the trip id:

```markdown
| paris-weekend-out  | eurostar | LON | PAR | 2026-08-20 | 2026-08-22 | standard | 2 | 0 | 0 |  | 400 | Outbound, flexible ±1 day |
| paris-weekend-back | eurostar | PAR | LON | 2026-08-24 | 2026-08-25 | standard | 2 | 0 | 0 |  | 400 | Return |
| paris-weekend      | Paris    | 2026-08-20 | 2026-08-25 | 2 |  | 1 | Châtelet-Les Halles | 10 | 1200 | Whole-listing, central |
```

`<trip_id>-out` and `<trip_id>-back` (or a single `<trip_id>` for one-way) on the train side; `<trip_id>` on the stay side. That's the convention `kelly_plan_trip` looks up.

Eurostar fare bands (mapped automatically from `children_ages`):
- 0–3 → infant (lap-held, free)
- 4–11 → child
- 12–25 → youth (discounted)
- 26–59 → adult
- 60+ → senior

Airbnb age bands (also automatic): <2 = infant, 2–12 = child, 13+ rolls into adults.
