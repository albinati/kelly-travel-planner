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


def test_load_example_config(tmp_path: Path) -> None:
    src = Path(__file__).resolve().parents[1] / "config" / "kelly.example.md"
    cfg = load_kelly_config(src)
    assert cfg.frontmatter.currency == "USD"
    assert len(cfg.passengers) >= 1
    assert any(p.id == "p1" for p in cfg.passengers)
    assert any(r.id == "summer-lis" for r in cfg.planned)
    assert any(o.id == "eu-business" for o in cfg.opportunities)
