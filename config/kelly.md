---
currency: USD
default_passenger_ids: [luis, karol, theo, maria_alice]
history_window_days: 90
max_dates_per_watch_row: 14
---

## Passengers

<!--
Eurostar fare bands at travel time (Aug 2026): infant <4 (lap, free), child 4–11,
youth 12–25, adult 26–59, senior 60+. Kid's DOB makes him 4y at Aug 2026 (turns 5
March 2027); user notes "5" — for fare purposes both 4 and 5 fall in the same
child band so cost is unaffected. REDACTED_NAME will be 3y → lap infant (free).
DOBs for nicole / rafael / sogro / bro_in_law / sister are placeholders pending
real data — fix before any flight booking that requires DOB.
-->

| id | given_name | family_name | type | dob | passport_number | loyalty_ba |
| --- | --- | --- | --- | --- | --- | --- |
| luis | Luis | Albinati | adult | 1982-10-21 | YF380578 | 07707680 |
| karol | Karoline | Coelho | adult | 1987-05-12 | FX194920 | 07679717 |
| sister | (sister) | Albinati | adult | | | |
| bro_in_law | (brother-in-law) | | adult | | | |
| sogra | Sandra Regina | Murno Coelho | senior | 1958-01-01 | | |
| sogro | (father-in-law / parent 65+) | | senior | | | |
| rafael | Rafael | | youth | 2011-01-01 | | |
| theo | Kiddoro | Albinati | child | 2022-03-13 | GJ466935 | 07743014 |
| nicole | Nicole | | child | 2022-01-01 | | |
| maria_alice | REDACTED_NAME | Albinati | infant | 2023-06-23 | GL017545 | 07670361 |

## Planned watchlist

| id | origin_iata | destination_iata | date_start | date_end | cabin | target_price | target_miles | seats_aero_sources | notes | passenger_ids |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| br-outbound | LHR | GRU | 2026-12-18 | 2026-12-23 | economy | 1500 | | ba,latam | Ida pro BR no fim do ano | luis,karol,theo,maria_alice |
| br-inbound | GRU | LHR | 2027-01-01 | 2027-01-05 | economy | 1500 | | ba,latam | Volta antes das aulas | luis,karol,theo,maria_alice |
| sogra-ida | GRU | LHR | 2026-06-20 | 2026-06-25 | economy | 800 | | ba,latam | Ida da Sogra | sogra |
| sogra-volta | LHR | GRU | 2026-07-15 | 2026-07-20 | economy | 800 | | ba,latam | Volta da Sogra | sogra |

## Opportunities

| id | origin_airports | destination_airports | start_date | end_date | cabin | max_cash | max_miles | seats_aero_sources | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eu-business | LHR,LCY | CDG,AMS,MAD,BCN | 2026-05-01 | 2026-05-31 | business | 500 |  | ba,iberia | Escapadas rápidas (exemplo) |

## Trains

| id | operator | origin_city | destination_city | date_start | date_end | class | adults | seniors | teens | children_ages | target_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sample-trip-out | eurostar | LON | PAR | 2026-08-19 | 2026-08-19 | standard | 4 | 2 | 1 | 3,4,5 | 1200 | Outbound Wed afternoon — cheaper than Thu morning |
| sample-trip-back | eurostar | PAR | LON | 2026-08-23 | 2026-08-23 | standard | 4 | 2 | 1 | 3,4,5 | 1200 | Return Sun morning — 4-night trip (3 Paris days + 1 Disney) |

## Stays

| id | area | check_in | check_out | adults | children_ages | bedrooms_min | near | max_walk_to_transit_min | max_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sample-trip | Paris, France | 2026-08-19 | 2026-08-23 | 7 | 3,4,5 | 5 | Châtelet-Les Halles,Gare de Lyon,Nation,Marne-la-Vallée–Chessy | 10 | 3000 | 4 nights, whole apartment for 10 (Airbnb counts adults=7 since 15y is adult-band there); prefer arrondissements 1/2/3/4/9/11/12 with RER A access to Disneyland; step-free for 65+ parents |