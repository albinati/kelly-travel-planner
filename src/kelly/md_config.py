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


class HostingRow(BaseModel):
    """A visiting party staying at the user's home — used to estimate the
    marginal household cost of hosting them (food delta, transit, outings,
    optional weekend trip, buffer). ``trip_id`` ties this to a parent trip
    arc shared with related DaytripRow rows."""

    id: str
    trip_id: str
    visitor_party: str  # free-form label (e.g. "parents", "sister-family")
    visitor_count: int = Field(..., ge=1, le=20)
    dates_start: date
    dates_end: date
    currency: str = "GBP"
    host_baseline_food_per_week: float = Field(default=0.0, ge=0)
    host_baseline_dineout_per_outing: float = Field(default=0.0, ge=0)
    host_baseline_transport_per_day: float = Field(default=0.0, ge=0)
    planned_outings_count: int = Field(default=0, ge=0)
    buffer_per_person: float = Field(default=0.0, ge=0)
    max_total: float | None = None
    notes: str = ""

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return str(v).strip().upper()


class DaytripRow(BaseModel):
    """A daytrip or short overnight excursion attached to a hosting/trip arc.
    Pure storage right now — eventually feeds a daytrip cost estimator."""

    id: str
    trip_id: str
    destination: str
    mode: str = "train"  # train|car|coach|tube|other
    date: date
    pax: int = Field(default=1, ge=1, le=30)
    est_cost_per_pp: float = Field(default=0.0, ge=0)
    currency: str = "GBP"
    includes_overnight: bool = False
    notes: str = ""

    @field_validator("currency", mode="before")
    @classmethod
    def upper_currency(cls, v: str) -> str:
        return str(v).strip().upper()

    @field_validator("mode", mode="before")
    @classmethod
    def lower_mode(cls, v: str) -> str:
        return str(v).strip().lower() or "train"


class KellyFrontmatter(BaseModel):
    """YAML front matter defaults."""

    currency: str = "GBP"
    history_window_days: int = 90


class KellyConfig(BaseModel):
    """Parsed kelly.md."""

    frontmatter: KellyFrontmatter
    trains: list[TrainRow] = Field(default_factory=list)
    stays: list[StayRow] = Field(default_factory=list)
    hosting: list[HostingRow] = Field(default_factory=list)
    daytrips: list[DaytripRow] = Field(default_factory=list)
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


def _parse_bool(s: str | None) -> bool:
    if s is None:
        return False
    return s.strip().lower() in {"yes", "true", "y", "1", "x", "✓"}


def _opt_float(v: str | None) -> float | None:
    if v is None or not v.strip():
        return None
    return float(v)


def _row_to_hosting(d: dict[str, str]) -> HostingRow:
    return HostingRow(
        id=d.get("id", "").strip(),
        trip_id=d.get("trip_id", "").strip(),
        visitor_party=d.get("visitor_party", "").strip(),
        visitor_count=int(d["visitor_count"]) if d.get("visitor_count", "").strip() else 1,
        dates_start=_parse_date(d["dates_start"]),
        dates_end=_parse_date(d["dates_end"]),
        currency=d.get("currency", "GBP") or "GBP",
        host_baseline_food_per_week=float(d.get("host_baseline_food_per_week") or 0),
        host_baseline_dineout_per_outing=float(d.get("host_baseline_dineout_per_outing") or 0),
        host_baseline_transport_per_day=float(d.get("host_baseline_transport_per_day") or 0),
        planned_outings_count=(
            int(d["planned_outings_count"]) if d.get("planned_outings_count", "").strip() else 0
        ),
        buffer_per_person=float(d.get("buffer_per_person") or 0),
        max_total=_opt_float(d.get("max_total")),
        notes=d.get("notes", "") or "",
    )


def _row_to_daytrip(d: dict[str, str]) -> DaytripRow:
    return DaytripRow(
        id=d.get("id", "").strip(),
        trip_id=d.get("trip_id", "").strip(),
        destination=d.get("destination", "").strip(),
        mode=d.get("mode", "train") or "train",
        date=_parse_date(d["date"]),
        pax=int(d["pax"]) if d.get("pax", "").strip() else 1,
        est_cost_per_pp=float(d.get("est_cost_per_pp") or 0),
        currency=d.get("currency", "GBP") or "GBP",
        includes_overnight=_parse_bool(d.get("includes_overnight")),
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
    hosting_tbl = parse_markdown_table(_section_body(content, "Hosting"))
    daytrips_tbl = parse_markdown_table(_section_body(content, "Daytrips"))

    trains = [_row_to_train(r) for r in trains_tbl if r.get("id")]
    stays = [_row_to_stay(r) for r in stays_tbl if r.get("id")]
    hosting = [_row_to_hosting(r) for r in hosting_tbl if r.get("id")]
    daytrips = [_row_to_daytrip(r) for r in daytrips_tbl if r.get("id")]

    return KellyConfig(
        frontmatter=frontmatter_model,
        trains=trains,
        stays=stays,
        hosting=hosting,
        daytrips=daytrips,
        raw_markdown=raw,
    )
