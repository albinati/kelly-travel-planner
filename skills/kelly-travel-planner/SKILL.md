---
name: kelly-travel-planner
description: OpenClaw skill — natural interface to Kelly, the household trip planner. Kelly owns the kelly.md trip catalogue, scrapes Eurostar fares (headless Chromium), queries Airbnb (GraphQL) + LiteAPI hotels, runs a BA Avios award-flight radar (seats.aero), solves multi-modal itineraries under date/weekend/open-jaw constraints, compares options all-in across currencies, and persists searches + a traveller profile + trip-session dossiers to local SQLite. Use whenever the user wants to plan a trip — including from a fuzzy "somewhere nice, no fixed idea" brief — look up Eurostar/Avios/hotel options, build a budget comparison, or compare against a previous price.
metadata: {"openclaw": {"requires": {"command": ["kelly"]}, "primaryEnv": "KELLY_MCP_URL", "emoji": "🚆"}}
---

# Kelly — group trip planner

Kelly does two things:

1. **Declared trips** — Eurostar trains + Airbnb/LiteAPI stays declared once in `kelly.md` and shortlisted on demand (`kelly_plan_trip`). The original core.
2. **Open-ended planning** — turn a fuzzy "we want a weekend somewhere nice, no fixed idea" brief into ranked, bookable options: capture a profile, discover destinations, run the **BA Avios award-flight radar** (seats.aero), solve multi-modal itineraries under real constraints (weekend shape, nights, **open-jaw**, seats), and compare options **all-in** across currencies. See [Planning from a fuzzy idea](#planning-a-trip-from-a-fuzzy-idea--the-playbook).

No keys for Eurostar/Airbnb (`patchright` drives headless Chromium, `pyairbnb` hits Airbnb's GraphQL). Avios needs `SEATSAERO_API_KEY`; hotels need `LITEAPI_API_KEY` (sandbox key = test data — lean on Airbnb/web for real stay prices). Keys live only on the server host.

## How you reach Kelly — one shared HTTP MCP

Kelly runs as **one long-lived, bearer-guarded streamable-HTTP MCP server** on the Hetzner box (the HEM pattern). It is registered in `openclaw.json` as `kelly`; tool names appear with the `kelly_` prefix (`kelly_load_config`, `kelly_avios_search`, …). The **same** server serves every client — OpenClaw over `127.0.0.1`, and remote Claude Code over Tailscale — so **all of them share one SQLite store: one profile, one set of dossiers, one truth.**

This is deliberately **not** a per-session stdio server. The old stdio model spawned a `kelly-mcp` child per session and left orphans resident (~75 MB idle each, +500 MB mid Chromium scrape) — the **"ghost session"** leak. A single long-lived HTTP process makes that impossible: clients are HTTP connections, nothing is spawned per session, and one process owns the DB (no cross-process lock contention). Kelly has no background loop, so the only resident cost is the idle server (~75 MB) plus a transient spike during a scrape.

> **CLI fallback.** Every tool also has a shell mirror — `kelly <kebab-command>` (e.g. `kelly avios-search …`, `kelly session-list`) over the same `services/*`, byte-identical JSON. Use it for scripting/cron, or locally if the shared server is unreachable. It's an ephemeral subprocess (runs, prints JSON, exits).

## Three rules, in order

1. **Read the config before scraping.** Always call `kelly_load_config` first to see the declared trips. Eurostar searches launch a headless Chromium (~500 MB resident while it runs); never trigger one until you know what trip the user actually wants. Skim `kelly_load_config`, name the candidate `trip_id`, then scrape.
2. **One trip = one `kelly_plan_trip` call.** Don't fan out separate `kelly_eurostar_search` + `kelly_airbnb_search` calls to assemble what `kelly_plan_trip` already returns. It runs them in sequence so Chromium is launched and torn down cleanly. Parallel ad-hoc scrapes = parallel Chromium = OOM risk on a small VPS. (One shared server means *your* scrape and another client's can overlap — so still serialise; don't kick off two heavy calls at once.)
3. **`persist=False` for what-ifs.** The default writes to SQLite — that's the price-baseline log, not a scratchpad. For "what if we left a day later" exploration, pass `persist=False`.

For zero-cost orientation, read the `kelly://config` resource (the raw `kelly.md`) — no Chromium, no HTTP scrape.

---

## Tool catalogue

25 tools + one resource, in groups. Within a group, lighter tools first. (Each has a `kelly <kebab>` CLI mirror — shown in parentheses.)

**Declared-trip core** (the original Eurostar+stay flow):

| Tool (CLI mirror) | Cost | What it does |
|---|---|---|
| `kelly_load_config` (`kelly load-config`) | **none** (file read) | Parse `kelly.md` → JSON of `frontmatter`, `trains[]`, `stays[]`. Trip ids, dates, party sizes. Always start here. |
| `kelly_airbnb_search` (`kelly airbnb-search`) | low (HTTP) | One-shot Airbnb whole-listing search via pyairbnb. Geocodes the area via OSM Nominatim, ~18 listings. Cheap. |
| `kelly_eurostar_search` (`kelly eurostar-search`) | **high** (Chromium) | One-shot Eurostar fare scrape for a city pair + date window. Walks each date (capped 7). Use only for a pair not in `kelly.md`. |
| `kelly_plan_trip` (`kelly plan-trip`) | **high** (Chromium ×1–2) | Look up `<trip_id>-out`/`-back` (or single `<trip_id>`) trains + the `<trip_id>` stay, fan out, return a curated shortlist with booking URLs. Chromium once per direction, sequential. |

**Open-ended planning** (the fuzzy-idea flow — see the playbook):

| Tool (CLI mirror) | Cost | What it does |
|---|---|---|
| `kelly_avios_search` (`kelly avios-search`) | medium (HTTP, paced) | BA **award-flight radar** via seats.aero (`origin_airports, destination_airports, date_start, date_end, pax, cabin, only_direct`). Returns BA-operated award availability: **seats + real times + Avios cost** (BA Reward Flight Saver chart). `partner_mileage_cost` is a partner program's miles — **NOT** the Avios price; never quote it as Avios. Needs `SEATSAERO_API_KEY`. |
| `kelly_solve_itinerary` (`kelly solve-itinerary`) | medium | Constraint-solver: pairs out/return legs into ranked itineraries by weekday shape, nights, min-seats, and **open-jaw** (out lands at A, back departs from B — how Bari-in/Brindisi-out is found). `modes=avios,eurostar`. All-Avios first by Avios, then cash, ties by travel time. |
| `kelly_budget_compare` (`kelly budget-compare`) | low (FX) | Convert every line to one currency (ECB-cached) and rank options **all-in** with deltas. Stay lines MUST be all-in (`price_all_in`). Avios **points** are reported separately, never folded into cash (only the Avios cash taxes are). |

**Profile + dream-box** (reuse across trips):

| Tool (CLI mirror) | What it does |
|---|---|
| `kelly_profile_get` / `kelly_profile_set` (`kelly profile-get`/`profile-set`) | Read/upsert the traveller profile (who travels, home airports, food/relax prefs, exclusions, Avios-or-cash). **A profile already exists: `albinati`** — the family, Theo in a London state school → family trips pinned to half-term. Read it at the start of every plan. |
| `kelly_dreambox_add` / `kelly_dreambox_list` / `kelly_dreambox_remove` (`kelly dreambox-*`) | Park a destination that doesn't fit the window; recall it later. (Thin shim over trip-sessions with `status=idea`.) |

**Trip sessions / dossiers** (intent + dated option snapshots across `idea → active → shortlisted → booked → archived`):

| Tool (CLI mirror) | What it does |
|---|---|
| `kelly_session_list` (`kelly session-list`) | List dossiers newest-first (without snapshots). Filter `--profile-id`/`--status`. |
| `kelly_session_get` (`kelly session-get`) | One dossier **plus its dated option snapshots** (a price time-series). |
| `kelly_session_create` (`kelly session-create`) | Open a dossier — a search **intent** + lifecycle status. |
| `kelly_session_update` (`kelly session-update`) | Patch fields (blanks untouched); the usual way to move `status` (e.g. `booked`, `archived`). |
| `kelly_session_attach_option` (`kelly session-attach-option`) | Append a **dated** option snapshot (append-only) — `kind` ∈ `cash_flight\|avios\|train\|stay\|experience\|car\|local\|other`. Avios points stay a separate currency. Re-attaching builds price history. |
| `kelly_session_remove` (`kelly session-remove`) | Delete a dossier + its options. |

**Bookings, money, calendar:**

| Tool (CLI mirror) | What it does |
|---|---|
| `kelly_convert` (`kelly convert`) | ECB-cached FX converter (string amounts, Decimal-safe). |
| `kelly_log_booking` / `kelly_trip_summary` (`kelly log-booking`/`trip-summary`) | Log a booked artifact (+ money); roll all legs into one currency. |
| `kelly_booking_event_draft` (`kelly booking-event-draft`) | Build a Google Calendar event spec (enriched with the logged total). Kelly never calls Calendar — pass the spec to OpenClaw's Calendar tool. |
| `kelly_log_expense` / `kelly_expense_balances` (`kelly log-expense`/`expense-balances`) | Splitwise (needs Splitwise env). |
| `kelly_estimate_hosting` (`kelly estimate-hosting`) | Marginal cost of hosting a visiting party from `## Hosting` + `## Daytrips`. |

### `kelly://config` resource

The current `kelly.md` as Markdown — read for "what trips are declared?" with no parsing/provider calls.

---

## The standard workflow

```
1. kelly_load_config              ← cheap, no scrape; orient yourself
2. (optional) kelly_airbnb_search ← cheap; explore stays ad-hoc
3. kelly_plan_trip <trip_id>      ← single expensive call, all-in-one
4. summarize the shortlist        ← stop; do not re-call to "verify"
```

**Don't** call `kelly_eurostar_search` + `kelly_airbnb_search` separately when a `trip_id` exists — `kelly_plan_trip` does that and curates the result (central-Paris filter, 9-pax Eurostar cap → Groups Desk hint, deep-link URLs with the right pax mix). **Don't** issue parallel scrapes — each spawns its own Chromium. **Don't** loop `kelly_eurostar_search` over a wide range — it already walks every date in the window (capped 7).

---

## Planning a trip from a fuzzy idea — the playbook

When the user has no fixed destination, don't ask them to pick a city. Run this. Kelly owns the deterministic parts (availability, pairing, budget); **you** own the judgment (profiling, discovery, taste, the final checklist).

```
0. profile   → kelly_profile_get albinati   (capture once with profile-set if absent)
1. discover  → propose candidate destinations that fit the profile (incl. one "break the box")
2. radar     → kelly_avios_search   across candidate airports + the date window
3. solve     → kelly_solve_itinerary  weekend shape + nights + open_jaw + min_seats=pax
4. stays     → research per surviving option (web/Airbnb), ALWAYS all-in prices
5. compare   → kelly_budget_compare   rank options all-in, Avios shown separately
6. present   → 2–4 ranked options with honest trade-offs; recommend one
7. capture   → kelly_session_create / kelly_session_attach_option; book when ready
```

**Step 0 — profile.** Read `kelly_profile_get` for `albinati`. If absent, interview briefly and `kelly_profile_set`. **Family trips must avoid Theo's term time** — verify the half-term window before searching.

**Step 1 — discover, don't wait for a city.** Propose destinations that fit; include one "break the box" pick. If a great idea needs more nights than the window allows, **park it** with `kelly_dreambox_add`.

**Step 2 — the Avios radar is the scarce constraint.** Award seats, not hotels, are what's rare in peak season. Run `kelly_avios_search` first; it shrinks the field fast. **In UK half-term the *return* leg is the usual bottleneck** — outbound award space loads but the return often shows nothing. Check both directions before claiming a round-trip exists. `partner_mileage_cost` is a different program's miles — never quote it as Avios.

**Step 3 — solve under real constraints.** `kelly_solve_itinerary` with `open_jaw=True` finds routings a human misses. Be honest: in peak season a **Sunday-afternoon award return often doesn't exist** — the real choice is a night flight or the Monday. Say so.

**Step 4 — stays, all-in only.** Use the **all-in** price (cleaning/fees/tourist tax in), never a raw nightly or `price_total` — mixing the two has caused real four-figure errors. LiteAPI is sandbox (test data); trust Airbnb/web for real stay prices.

**Step 5 — compare all-in.** Feed options to `kelly_budget_compare` as line items. Avios points stay separate from the cash total (only Avios taxes are cash).

**Step 6 — present honestly.** 2–4 options, each with its all-in number, the flight shape (e.g. "Sun night return, home 00:05"), and the one real downside. Recommend one. Respect "max one booked thing" if the profile says relax.

**Step 7 — capture + book.** Dossier the run with `kelly_session_create` (the intent) + `kelly_session_attach_option` per option (dated snapshots → a re-quotable price history). Buy-it-all checklist in **booking order — scarcest award seat first**. Then `kelly_booking_event_draft` per leg, `kelly_log_booking` once booked, and `kelly_session_update` to `status=booked`.

Keep exploratory searches `persist=False`; only committed searches belong in the history baseline.

---

## Reading the result

`kelly_plan_trip` returns:

```json
{
  "trip_id": "...",
  "trains": { "outbound": {...}, "return": {...} },
  "stays":  { "primary":  {...} },
  "shortlist": {
    "eurostar_outbound": [...],
    "eurostar_return":   [...],
    "airbnb":            [...]
  },
  "missing": []
}
```

Each Eurostar entry includes `booking_groups`: a deep-link URL pre-filled with the pax mix (split into two when the party exceeds 9 — Eurostar's web cap); when `booking_groups.length > 1`, surface the Groups Desk hint. Each Airbnb entry has a `url` pre-filled with `check_in`/`check_out`/`adults`/`children`/`infants`. Surface any `error`/`note` on inner result blocks.

---

## What NOT to do

- **Don't** edit `config/kelly.md` through OpenClaw. It's the user's source of truth — surface what's there; new trips → tell them what row to add.
- **Don't** call any tool with persist on (the default) unless the search is intentional and the user is committed to a window.
- **Don't** retry a failed Eurostar scrape immediately. If `error: "patchright not installed"`, tell the user to run `patchright install chromium` on the server — don't loop.
- **Don't** dial Airbnb/Eurostar/seats.aero APIs directly. The MCP tools (or their CLI mirrors) are the only sanctioned surface; if a capability is missing, request a new tool.

---

## Failure modes to recognise

| Symptom | What it means | What to do |
|---|---|---|
| `error: "patchright not installed"` | The server hasn't run `patchright install chromium`. | Tell the user once; don't retry. |
| `error: "unknown origin/destination station code 'XYZ'"` | City code isn't in `_STATION_CODES`. | List the known codes (LON, PAR, BRU, AMS, LIL, MLV); ask the user to pick. |
| `note: "<date>: navigation failed"` | DataDome blocked or the page timed out. | Surface the note. A retry on the same call is fine; don't spawn a parallel one. |
| `error: "could not geocode area …"` | OSM Nominatim returned nothing. | Suggest a more standard spelling (e.g. "Paris, France"). |
| `note: "no BA-operated award availability matched in the window"` | seats.aero has no BA award space for that leg/window (common for half-term returns). | Say so; check the other direction / nearby dates; consider Avios-out + cash-return. |
| `missing: ["trains.outbound", ...]` | No row matches `<trip_id>-out`/`-back`/stay. | Show what's declared (`kelly_load_config`) and ask which `trip_id` they meant. |
| HTTP `401` from the MCP endpoint | Bearer token missing/wrong in the client config. | The server is fine; fix the `Authorization` bearer in `openclaw.json` / `.mcp.json`. |

---

## Operational notes

- **One shared long-lived server, no ghosts.** Kelly is a single bearer-guarded streamable-HTTP process (`kelly-mcp-http`) on the Hetzner host; OpenClaw + remote Claude Code are HTTP clients of it. No per-session children, so nothing can be orphaned. Idle ~75 MB; restart is one `systemctl restart kelly-mcp`.
- **Shared state = one SQLite, one writer.** History, the `albinati` profile, dream-box, and trip-session dossiers all live in the server's `KELLY_DATA_DIR/kelly_history.sqlite`. Because a single process owns it, every client sees the same data with no cross-process locking.
- **Chromium is the long pole.** Idle is near-zero; every Eurostar scrape spikes to ~500 MB+ for the duration of that one call and releases on exit. Serialise scrapes — one shared server means clients can collide; don't run two heavy calls at once.
- **Airbnb side is light.** `kelly_airbnb_search` is one HTTP round-trip + a geocode. Use freely.
- **Secrets stay on the server.** `SEATSAERO_API_KEY` / `LITEAPI_API_KEY` / Splitwise live in the server's `.env`; clients never need them.
- **CLI fallback is offline-safe.** If the shared server is down, the `kelly <command>` CLI runs locally against the local SQLite — ephemeral, no resident process. (Its data is separate from the server's unless you point it at the same DB.)
- **Currency is informational.** Eurostar returns per-region currency regardless of the `currency` field; trust `price_currency` on each journey.

---

## Quick reference: trip declaration

A trip in `kelly.md` is two `## Trains` rows (`<trip_id>-out` / `<trip_id>-back`) and one `## Stays` row sharing the trip id:

```markdown
| paris-weekend-out  | eurostar | LON | PAR | 2026-08-20 | 2026-08-22 | standard | 2 | 0 | 0 |  | 400 | Outbound, flexible ±1 day |
| paris-weekend-back | eurostar | PAR | LON | 2026-08-24 | 2026-08-25 | standard | 2 | 0 | 0 |  | 400 | Return |
| paris-weekend      | Paris    | 2026-08-20 | 2026-08-25 | 2 |  | 1 | Châtelet-Les Halles | 10 | 1200 | Whole-listing, central |
```

`<trip_id>-out`/`-back` (or a single `<trip_id>` for one-way) on the train side; `<trip_id>` on the stay side — the convention `kelly_plan_trip` looks up.

Eurostar fare bands (auto-mapped from `children_ages`): 0–3 infant (lap-held, free) · 4–11 child · 12–25 youth · 26–59 adult · 60+ senior. Airbnb bands (also automatic): <2 infant, 2–12 child, 13+ rolls into adults.
