---
currency: USD
default_passenger_ids: []
history_window_days: 90
max_dates_per_watch_row: 14
travel_policy:
  max_stops: 2
  direct_only: false
  baggage: "1 checked included"
---

## Passengers

| id | label | type |
| --- | --- | --- |
| p1 | Adult | adult |
| p2 | Child | child |

## Planned watchlist

| id | origin_iata | destination_iata | date_start | date_end | cabin | target_price | target_miles | seats_aero_sources | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| summer-lis | JFK | LIS | 2026-07-01 | 2026-07-14 | economy | 650 |  | united,aeroplan | Summer Portugal |
| ski-gva | BOS | GVA | 2026-01-10 | 2026-01-17 | business | 2200 | 70000 | united | Ski week |

## Opportunities

| id | origin_airports | destination_airports | start_date | end_date | cabin | max_cash | max_miles | seats_aero_sources | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eu-business | JFK,EWR | CDG,AMS | 2026-05-01 | 2026-05-31 | business | 1800 |  | flyingblue,aeroplan | Europe business dump |

## Trains

| id | operator | origin_city | destination_city | date_start | date_end | class | adults | seniors | teens | children_ages | target_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| example-out | eurostar | LON | PAR | 2026-08-20 | 2026-08-22 | standard | 2 | 0 | 0 |  | 400 | Outbound Eurostar |

## Stays

| id | area | check_in | check_out | adults | children_ages | bedrooms_min | near | max_walk_to_transit_min | max_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| example | Paris | 2026-08-20 | 2026-08-25 | 2 |  | 1 | Châtelet-Les Halles | 10 | 1200 | Single couple example |
