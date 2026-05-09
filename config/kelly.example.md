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
