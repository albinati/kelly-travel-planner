from pathlib import Path

from kelly.md_config import load_kelly_config


SAMPLE = """---
currency: EUR
default_passenger_ids: []
history_window_days: 90
max_dates_per_watch_row: 14
---

## Passengers

| id | label | type |
| --- | --- | --- |
| p1 | Adult | adult |

## Planned watchlist

| id | origin_iata | destination_iata | date_start | date_end | cabin | target_price | target_miles | seats_aero_sources | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Trains

| id | operator | origin_city | destination_city | date_start | date_end | class | adults | seniors | teens | children_ages | target_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| trip1-out | eurostar | LON | PAR | 2026-08-20 | 2026-08-22 | standard | 6 | 2 | 1 | 3,4,5 | 1800 | outbound |
| trip1-back | eurostar | PAR | LON | 2026-08-25 | 2026-08-27 | standard_premier | 6 | 2 | 1 | 3,4,5 | 2200 | return |

## Stays

| id | area | check_in | check_out | adults | children_ages | bedrooms_min | near | max_walk_to_transit_min | max_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| trip1 | Paris 1–4,9,11 | 2026-08-20 | 2026-08-25 | 6 | 3,4,5,15 | 5 | Châtelet-Les Halles,Gare de Lyon | 10 | 4500 | big group |
"""


def test_parses_trains_and_stays(tmp_path: Path) -> None:
    p = tmp_path / "kelly.md"
    p.write_text(SAMPLE, encoding="utf-8")
    cfg = load_kelly_config(p)

    assert [t.id for t in cfg.trains] == ["trip1-out", "trip1-back"]
    out = cfg.trains[0]
    assert out.origin_city == "LON"
    assert out.destination_city == "PAR"
    assert out.class_ == "standard"
    assert out.adults == 6 and out.seniors == 2 and out.teens == 1
    assert out.children_ages == [3, 4, 5]
    assert cfg.trains[1].class_ == "standard_premier"

    assert [s.id for s in cfg.stays] == ["trip1"]
    stay = cfg.stays[0]
    assert stay.bedrooms_min == 5
    assert stay.children_ages == [3, 4, 5, 15]
    assert stay.near == ["Châtelet-Les Halles", "Gare de Lyon"]
    assert stay.max_walk_to_transit_min == 10
    assert stay.max_total == 4500


def test_missing_sections_are_empty(tmp_path: Path) -> None:
    p = tmp_path / "kelly.md"
    p.write_text(
        """---
currency: USD
---

## Passengers

| id | label | type |
| --- | --- | --- |
| p1 | Adult | adult |
""",
        encoding="utf-8",
    )
    cfg = load_kelly_config(p)
    assert cfg.trains == []
    assert cfg.stays == []
    assert cfg.hosting == []
    assert cfg.daytrips == []


HOSTING_SAMPLE = """---
currency: GBP
---

## Hosting

| id | trip_id | visitor_party | visitor_count | dates_start | dates_end | currency | host_baseline_food_per_week | host_baseline_dineout_per_outing | host_baseline_transport_per_day | planned_outings_count | buffer_per_person | max_total | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| visitors-1 | hosting-trip | parents | 2 | 2026-08-07 | 2026-08-30 | GBP | 95 | 60 | 3.50 | 3 | 80 | 2200 | 24 nights hosting |
| visitors-2 | hosting-trip | sister-family | 4 | 2026-08-14 | 2026-08-30 | gbp | 95 | 60 | 3.50 | 2 | 80 | 2800 | overlap window |

## Daytrips

| id | trip_id | destination | mode | date | pax | est_cost_per_pp | currency | includes_overnight | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bath-weekend | hosting-trip | Bath | TRAIN | 2026-08-22 | 6 | 180 | GBP | yes | Sat-Sun GWR + B&B |
| tower-of-london | hosting-trip | London — Tower of London | tube | 2026-08-10 | 4 | 35 | GBP | no | family of 4 |
"""


def test_parses_hosting_section(tmp_path: Path) -> None:
    p = tmp_path / "kelly.md"
    p.write_text(HOSTING_SAMPLE, encoding="utf-8")
    cfg = load_kelly_config(p)

    assert [h.id for h in cfg.hosting] == ["visitors-1", "visitors-2"]
    parents = cfg.hosting[0]
    assert parents.trip_id == "hosting-trip"
    assert parents.visitor_party == "parents"
    assert parents.visitor_count == 2
    assert parents.host_baseline_food_per_week == 95.0
    assert parents.host_baseline_dineout_per_outing == 60.0
    assert parents.host_baseline_transport_per_day == 3.5
    assert parents.planned_outings_count == 3
    assert parents.buffer_per_person == 80.0
    assert parents.max_total == 2200.0
    assert parents.currency == "GBP"
    # Sister row had lowercase "gbp" — verify validator uppercases.
    assert cfg.hosting[1].currency == "GBP"
    assert cfg.hosting[1].visitor_count == 4


def test_parses_daytrips_section(tmp_path: Path) -> None:
    p = tmp_path / "kelly.md"
    p.write_text(HOSTING_SAMPLE, encoding="utf-8")
    cfg = load_kelly_config(p)

    assert [d.id for d in cfg.daytrips] == ["bath-weekend", "tower-of-london"]
    bath = cfg.daytrips[0]
    assert bath.destination == "Bath"
    assert bath.mode == "train"  # uppercased in source, normalized to lowercase
    assert bath.pax == 6
    assert bath.est_cost_per_pp == 180.0
    assert bath.includes_overnight is True
    assert bath.currency == "GBP"

    tower = cfg.daytrips[1]
    assert tower.mode == "tube"
    assert tower.includes_overnight is False
