from pathlib import Path

from kelly.md_config import load_kelly_config, parse_markdown_table


def test_parse_simple_table() -> None:
    md = """
| a | b |
| --- | --- |
| 1 | 2 |
"""
    rows = parse_markdown_table(md)
    assert rows == [{"a": "1", "b": "2"}]


def test_planned_row_passenger_ids_override(tmp_path: Path) -> None:
    md = """---
currency: USD
default_passenger_ids: [p1]
---
## Passengers
| id | label | type |
| --- | --- | --- |
| p1 | A | adult |
| p2 | B | child |
## Planned watchlist
| id | origin_iata | destination_iata | date_start | date_end | cabin | passenger_ids | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| r1 | JFK | LIS | 2026-07-01 | 2026-07-02 | economy | p2 | x |
"""
    p = tmp_path / "k.md"
    p.write_text(md, encoding="utf-8")
    from kelly.md_config import passengers_for_cash

    cfg = load_kelly_config(p)
    assert passengers_for_cash(cfg) == [{"type": "adult"}]
    row = next(r for r in cfg.planned if r.id == "r1")
    assert passengers_for_cash(cfg, passenger_ids_override=row.passenger_ids) == [
        {"type": "child"},
    ]


def test_travel_policy_frontmatter(tmp_path: Path) -> None:
    md = """---
currency: USD
travel_policy:
  max_stops: 0
  direct_only: true
  baggage: "carry-on only"
---
## Passengers
| id | label | type |
| --- | --- | --- |
| p1 | A | adult |
## Planned watchlist
| id | origin_iata | destination_iata | date_start | date_end | cabin | notes |
| --- | --- | --- | --- | --- | --- | --- |
| r1 | JFK | LIS | 2026-07-01 | 2026-07-02 | economy | x |
"""
    p = tmp_path / "k.md"
    p.write_text(md, encoding="utf-8")
    cfg = load_kelly_config(p)
    assert cfg.frontmatter.travel_policy is not None
    assert cfg.frontmatter.travel_policy.direct_only is True
    assert cfg.frontmatter.travel_policy.max_stops == 0


def test_load_example_config(tmp_path: Path) -> None:
    src = Path(__file__).resolve().parents[1] / "config" / "kelly.example.md"
    cfg = load_kelly_config(src)
    assert cfg.frontmatter.currency == "USD"
    assert len(cfg.passengers) >= 1
    assert any(p.id == "p1" for p in cfg.passengers)
    assert any(r.id == "summer-lis" for r in cfg.planned)
    assert any(o.id == "eu-business" for o in cfg.opportunities)
    assert cfg.frontmatter.travel_policy is not None
    assert cfg.frontmatter.travel_policy.max_stops == 2
