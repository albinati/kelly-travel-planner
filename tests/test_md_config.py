from datetime import date
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


def test_load_example_config() -> None:
    src = Path(__file__).resolve().parents[1] / "config" / "kelly.example.md"
    cfg = load_kelly_config(src)
    assert cfg.frontmatter.currency == "GBP"
    assert any(t.id == "paris-weekend-out" for t in cfg.trains)
    assert any(t.id == "paris-weekend-back" for t in cfg.trains)
    assert any(s.id == "paris-weekend" for s in cfg.stays)


def test_unknown_sections_are_ignored(tmp_path: Path) -> None:
    """Legacy sections (Passengers, Planned watchlist, Opportunities) parse without error."""
    md = """---
currency: GBP
---
## Passengers
| id | label | type |
| --- | --- | --- |
| p1 | A | adult |

## Planned watchlist
| id | origin_iata | destination_iata | date_start | date_end | cabin | notes |
| --- | --- | --- | --- | --- | --- | --- |
| r1 | JFK | LIS | 2026-07-01 | 2026-07-02 | economy | x |

## Trains
| id | operator | origin_city | destination_city | date_start | date_end | class | adults | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| t1 | eurostar | LON | PAR | 2026-08-20 | 2026-08-22 | standard | 2 | live |
"""
    p = tmp_path / "k.md"
    p.write_text(md, encoding="utf-8")
    cfg = load_kelly_config(p)
    assert len(cfg.trains) == 1
    assert cfg.trains[0].id == "t1"
    assert cfg.trains[0].date_start == date(2026, 8, 20)
    assert cfg.stays == []
