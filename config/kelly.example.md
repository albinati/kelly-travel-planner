---
currency: GBP
history_window_days: 90
---

# Example trip — Paris weekend (Eurostar + Airbnb)

A single trip is two `## Trains` rows (`<trip_id>-out`, `<trip_id>-back`) plus
one `## Stays` row sharing the trip id. `kelly plan paris-weekend` looks them
up by id, runs each search, and curates a shortlist.

## Trains

| id | operator | origin_city | destination_city | date_start | date_end | class | adults | seniors | teens | children_ages | target_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paris-weekend-out  | eurostar | LON | PAR | 2026-08-20 | 2026-08-22 | standard | 2 | 0 | 0 |    | 400 | Outbound, flexible by ±1 day |
| paris-weekend-back | eurostar | PAR | LON | 2026-08-24 | 2026-08-25 | standard | 2 | 0 | 0 |    | 400 | Return |

## Stays

| id | area | check_in | check_out | adults | children_ages | bedrooms_min | near | max_walk_to_transit_min | max_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| paris-weekend | Paris | 2026-08-20 | 2026-08-25 | 2 |    | 1 | Châtelet-Les Halles | 10 | 1200 | Whole-listing, central |

## Hosting

Optional — only for trips where visitors stay at the host's home (no hotel cost).
`kelly_estimate_hosting <hosting-id>` rolls food + transit + outings + matching daytrips into one number; tune the baseline columns to match your real household spend.

| id | trip_id | visitor_party | visitor_count | dates_start | dates_end | currency | host_baseline_food_per_week | host_baseline_dineout_per_outing | host_baseline_transport_per_day | planned_outings_count | buffer_per_person | max_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| relatives-visit | relatives-uk-spring | relatives | 2 | 2026-05-01 | 2026-05-10 | GBP | 80 | 40 | 8.50 | 2 | 50 | 900 | 10 nights; 1 weekend day-trip planned. |

## Daytrips

Optional — same-day or short overnight excursions attached to a hosting arc via `trip_id`.

| id | trip_id | destination | mode | date | pax | est_cost_per_pp | currency | includes_overnight | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| relatives-windsor | relatives-uk-spring | Windsor | train | 2026-05-05 | 2 | 40 | GBP | no | Off-peak return + castle entry. |
