---
currency: GBP
history_window_days: 90
---

# Kelly trips

Trips are declared as `## Trains` rows (`<trip_id>-out`, `<trip_id>-back`)
plus a `## Stays` row sharing the trip id. Run `kelly plan <trip_id>` or call
`kelly_plan_trip` over MCP to fan out searches and curate a shortlist.

Eurostar fare bands honoured by the URL (Aug 2026): infant 0–3 (lap, free),
child 4–11, youth 12–25, adult 26–59, senior 60+. Theo turns 5 in March 2027
so on Aug-2026 travel he's 4 → child band; Maria Alice is 3 → lap infant.

## Trains

| id | operator | origin_city | destination_city | date_start | date_end | class | adults | seniors | teens | children_ages | target_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paris-disney-2026-08-out  | eurostar | LON | PAR | 2026-08-18 | 2026-08-18 | standard | 4 | 2 | 1 | 3,4,5 | 1200 | Outbound Tue afternoon — weekday slot inside the 17–27 Aug window |
| paris-disney-2026-08-back | eurostar | PAR | LON | 2026-08-21 | 2026-08-21 | standard | 4 | 2 | 1 | 3,4,5 | 1200 | Return Fri afternoon — 3-night trip (2 Paris days + 1 Disney) |

## Stays

| id | area | check_in | check_out | adults | children_ages | bedrooms_min | near | max_walk_to_transit_min | max_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paris-disney-2026-08 | Paris, France | 2026-08-18 | 2026-08-21 | 7 | 3,4,5 | 5 | Châtelet-Les Halles,Gare de Lyon,Nation,Marne-la-Vallée–Chessy | 10 | 2400 | 3 nights, whole apartment for 10 (Airbnb counts adults=7 since 15y is adult-band there); prefer arrondissements 1/2/3/4/9/11/12 with RER A access to Disneyland; step-free for 65+ parents |

## Hosting

Visitors staying at home in London — separate arc from Paris/Disney (which is a gift from the user + sister and lives in `## Trains` / `## Stays`). Numbers below are tuned to the user's actual household baseline so `kelly_estimate_hosting` returns realistic GBP totals.

| id | trip_id | visitor_party | visitor_count | dates_start | dates_end | currency | host_baseline_food_per_week | host_baseline_dineout_per_outing | host_baseline_transport_per_day | planned_outings_count | buffer_per_person | max_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| parents-uk-hosting | parents-uk-2026-08 | parents | 2 | 2026-08-07 | 2026-08-30 | GBP | 95 | 50 | 8.50 | 2 | 80 | 1700 | 24 nights total; first week mostly home recovering from jetlag, sightseeing concentrates post-Paris (22-30/8). TfL is the visitor tourist-mode cap (£8.50/day z1-2), not the host's £1.75 commuter pattern. |
| sister-uk-hosting  | parents-uk-2026-08 | sister-family | 4 | 2026-08-14 | 2026-08-30 | GBP | 95 | 50 | 8.50 | 2 | 60 | 2400 | 17-day overlap window; family-of-4 (2 adults + 2 kids). Same household baselines — `kelly_estimate_hosting` proportions automatically. |

## Daytrips

Same-day or short overnight excursions attached to the hosting arc. `est_cost_per_pp × pax` rolls into `kelly_estimate_hosting`'s daytrip line. Currency conversion happens at lookup if the call asks for a non-GBP target (e.g. BRL for the dad's FX planning).

| id | trip_id | destination | mode | date | pax | est_cost_per_pp | currency | includes_overnight | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| parents-uk-bath-weekend | parents-uk-2026-08 | Bath | train | 2026-08-22 | 2 | 300 | GBP | yes | Sat anchor; 2 nights B&B + GWR off-peak return + Roman Baths entry. With Senior Railcard the trains drop ~30% but the railcard pays back only at 1+ trips — recommend only if a 2nd day-trip is on the cards. |
| parents-uk-london-tower | parents-uk-2026-08 | London — Tower of London | tube | 2026-08-26 | 4 | 35 | GBP | no | Adult-rate; visit while sister's family is in town so the whole crew goes. |
| parents-uk-london-westminster | parents-uk-2026-08 | London — Westminster Abbey | tube | 2026-08-28 | 2 | 30 | GBP | no | Lighter cultural anchor for the parents-only days. |
