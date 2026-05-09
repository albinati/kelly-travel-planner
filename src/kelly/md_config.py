"""Load household trip config from Markdown (frontmatter + Trains/Stays tables)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field, field_validator

# --- Models ---


_TRAIN_OPERATORS = {"eurostar"}
_TRAIN_CLASSES = {"standard", "standard_premier", "business_premier"}


def _parse_ages(v: object) -> list[int]:
    """Parse comma-separated or list-of-ints field to list[int] (ages)."""
    if v is None:
        return []
    if isinstance(v, list):
        out: list[int] = []
        for x in v:
            s = str(x).strip()
            if not s:
                continue
            out.append(int(s))
        return out
    s = str(v).strip()
    if not s:
        return []
    return [int(p.strip()) for p in s.split(",") if p.strip()]


def _parse_landmarks(v: object) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


class TrainRow(BaseModel):
    """One rail leg to search (e.g. Eurostar London↔Paris)."""

    id: str
    operator: str = "eurostar"
    origin_city: str = Field(..., min_length=2, max_length=6)
    destination_city: str = Field(..., min_length=2, max_length=6)
    date_start: date
    date_end: date
    class_: str = Field(default="standard", alias="class")
    adults: int = Field(default=1, ge=0, le=30)
    seniors: int = Field(default=0, ge=0, le=30)
    teens: int = Field(default=0, ge=0, le=30)
    children_ages: list[int] = Field(default_factory=list)
    target_total: float | None = None
    notes: str = ""

    model_config = {"populate_by_name": True}

    @field_validator("origin_city", "destination_city", mode="before")
    @classmethod
    def upper_city(cls, v: str) -> str:
        return str(v).strip().upper()

    @field_validator("operator", mode="before")
    @classmethod
    def normalize_operator(cls, v: str) -> str:
        s = str(v).strip().lower()
        if s not in _TRAIN_OPERATORS:
            raise ValueError(f"operator must be one of {_TRAIN_OPERATORS}, got {v!r}")
        return s

    @field_validator("class_", mode="before")
    @classmethod
    def normalize_class(cls, v: str) -> str:
        s = str(v).strip().lower().replace(" ", "_")
        if s not in _TRAIN_CLASSES:
            raise ValueError(f"class must be one of {_TRAIN_CLASSES}, got {v!r}")
        return s

    @field_validator("children_ages", mode="before")
    @classmethod
    def parse_children_ages(cls, v: object) -> list[int]:
        return _parse_ages(v)


class StayRow(BaseModel):
    """One accommodation search (e.g. Paris whole-apartment for a group)."""

    id: str
    area: str
    check_in: date
    check_out: date
    adults: int = Field(default=1, ge=0, le=30)
    children_ages: list[int] = Field(default_factory=list)
    bedrooms_min: int = Field(default=1, ge=0, le=20)
    near: list[str] = Field(default_factory=list)
    max_walk_to_transit_min: int | None = None
    max_total: float | None = None
    notes: str = ""

    @field_validator("children_ages", mode="before")
    @classmethod
    def parse_children_ages(cls, v: object) -> list[int]:
        return _parse_ages(v)

    @field_validator("near", mode="before")
    @classmethod
    def parse_near(cls, v: object) -> list[str]:
        return _parse_landmarks(v)


class KellyFrontmatter(BaseModel):
    """YAML front matter defaults."""

    currency: str = "GBP"
    history_window_days: int = 90


class KellyConfig(BaseModel):
    """Parsed kelly.md."""

    frontmatter: KellyFrontmatter
    trains: list[TrainRow] = Field(default_factory=list)
    stays: list[StayRow] = Field(default_factory=list)
    raw_markdown: str = ""


# --- Table parsing ---


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if not line.startswith("|"):
        return []
    parts = [p.strip() for p in line.split("|")]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _is_separator_row(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(re.match(r"^:?-{3,}:?$", c.strip()) for c in cells if c.strip())


def parse_markdown_table(text: str) -> list[dict[str, str]]:
    """Parse first GFM-style pipe table in *text* into list of row dicts."""
    lines = text.splitlines()
    rows: list[list[str]] = []
    headers: list[str] | None = None
    for line in lines:
        if "|" not in line or not line.strip().startswith("|"):
            if rows and headers:
                break
            continue
        cells = _split_row(line)
        if not cells:
            continue
        if headers is None:
            headers = [c.lower().replace(" ", "_") for c in cells]
            continue
        if _is_separator_row(cells):
            continue
        if len(cells) != len(headers):
            while len(cells) < len(headers):
                cells.append("")
            cells = cells[: len(headers)]
        rows.append(dict(zip(headers, cells, strict=True)))
    return rows


def _parse_date(s: str) -> date:
    return date.fromisoformat(s.strip())


def _row_to_train(d: dict[str, str]) -> TrainRow:
    return TrainRow(
        id=d.get("id", "").strip(),
        operator=d.get("operator", "eurostar") or "eurostar",
        origin_city=d["origin_city"],
        destination_city=d["destination_city"],
        date_start=_parse_date(d["date_start"]),
        date_end=_parse_date(d["date_end"]),
        **{"class": (d.get("class", "standard") or "standard")},
        adults=int(d["adults"]) if d.get("adults", "").strip() else 1,
        seniors=int(d["seniors"]) if d.get("seniors", "").strip() else 0,
        teens=int(d["teens"]) if d.get("teens", "").strip() else 0,
        children_ages=d.get("children_ages") or "",
        target_total=float(d["target_total"]) if d.get("target_total", "").strip() else None,
        notes=d.get("notes", "") or "",
    )


def _row_to_stay(d: dict[str, str]) -> StayRow:
    return StayRow(
        id=d.get("id", "").strip(),
        area=d.get("area", "").strip(),
        check_in=_parse_date(d["check_in"]),
        check_out=_parse_date(d["check_out"]),
        adults=int(d["adults"]) if d.get("adults", "").strip() else 1,
        children_ages=d.get("children_ages") or "",
        bedrooms_min=int(d["bedrooms_min"]) if d.get("bedrooms_min", "").strip() else 1,
        near=d.get("near") or "",
        max_walk_to_transit_min=(
            int(d["max_walk_to_transit_min"])
            if d.get("max_walk_to_transit_min", "").strip()
            else None
        ),
        max_total=float(d["max_total"]) if d.get("max_total", "").strip() else None,
        notes=d.get("notes", "") or "",
    )


def _section_body(full_md: str, heading: str) -> str:
    """Return text from `## heading` until next `## ` or EOF."""
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = pattern.search(full_md)
    if not m:
        return ""
    start = m.end()
    rest = full_md[start:]
    next_h = re.search(r"^##\s+", rest, re.MULTILINE)
    if next_h:
        return rest[: next_h.start()]
    return rest


def load_kelly_config(path: str | Path) -> KellyConfig:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    fm_data: dict[str, Any] = dict(post.metadata) if post.metadata else {}
    frontmatter_model = KellyFrontmatter.model_validate(fm_data)

    content = post.content or ""
    trains_tbl = parse_markdown_table(_section_body(content, "Trains"))
    stays_tbl = parse_markdown_table(_section_body(content, "Stays"))

    trains = [_row_to_train(r) for r in trains_tbl if r.get("id")]
    stays = [_row_to_stay(r) for r in stays_tbl if r.get("id")]

    return KellyConfig(
        frontmatter=frontmatter_model,
        trains=trains,
        stays=stays,
        raw_markdown=raw,
    )
