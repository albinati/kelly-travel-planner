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
| paris-disney-2026-08-out  | eurostar | LON | PAR | 2026-08-19 | 2026-08-19 | standard | 4 | 2 | 1 | 3,4,5 | 1200 | Outbound Wed afternoon — cheaper than Thu morning |
| paris-disney-2026-08-back | eurostar | PAR | LON | 2026-08-23 | 2026-08-23 | standard | 4 | 2 | 1 | 3,4,5 | 1200 | Return Sun morning — 4-night trip (3 Paris days + 1 Disney) |

## Stays

| id | area | check_in | check_out | adults | children_ages | bedrooms_min | near | max_walk_to_transit_min | max_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paris-disney-2026-08 | Paris, France | 2026-08-19 | 2026-08-23 | 7 | 3,4,5 | 5 | Châtelet-Les Halles,Gare de Lyon,Nation,Marne-la-Vallée–Chessy | 10 | 3000 | 4 nights, whole apartment for 10 (Airbnb counts adults=7 since 15y is adult-band there); prefer arrondissements 1/2/3/4/9/11/12 with RER A access to Disneyland; step-free for 65+ parents |
