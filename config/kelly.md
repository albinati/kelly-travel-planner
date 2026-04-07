---
currency: USD
default_passenger_ids: [luis, karol, theo, maria_alice]
history_window_days: 90
max_dates_per_watch_row: 14
---

## Passengers

| id | given_name | family_name | type | dob | passport_number | loyalty_ba |
| --- | --- | --- | --- | --- | --- | --- |
| luis | Luis | Albinati | adult | 1982-10-21 | YF380578 | 07707680 |
| karol | Karoline | Coelho | adult | 1987-05-12 | FX194920 | 07679717 |
| theo | Kiddoro | Albinati | child | 2022-03-13 | GJ466935 | 07743014 |
| maria_alice | REDACTED_NAME | Albinati | child | 2023-06-23 | GL017545 | 07670361 |
| sogra | Sandra Regina | Murno Coelho | adult | 1958-01-01 | | |

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