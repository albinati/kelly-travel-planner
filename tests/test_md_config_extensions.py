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
