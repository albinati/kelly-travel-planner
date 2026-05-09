"""Load household travel config from Markdown (frontmatter + tables)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field, field_validator

# --- Models ---


class TravelPolicy(BaseModel):
    """Optional constraints from kelly.md front matter (``travel_policy``)."""

    max_stops: int = Field(default=2, ge=0, le=10)
    direct_only: bool = False
    baggage: str = Field(default="", description="Free-text baggage expectation")


class PassengerRow(BaseModel):
    """One row from the Passengers table."""

    id: str = Field(..., description="Stable id, e.g. p1")
    label: str = ""
    type: str = Field(
        default="adult",
        description="Passenger type for cash search counts: adult, child, infant_without_seat",
    )


class PlannedWatchRow(BaseModel):
    """One planned route to monitor."""

    id: str
    origin_iata: str = Field(..., min_length=3, max_length=3)
    destination_iata: str = Field(..., min_length=3, max_length=3)
    date_start: date
    date_end: date
    cabin: str = "economy"
    target_price: float | None = None
    target_miles: int | None = None
    passenger_ids: list[str] | None = Field(
        default=None,
        description="Comma-separated in MD; overrides frontmatter default_passenger_ids.",
    )
    seats_aero_sources: str | None = Field(
        default=None,
        description="Comma-separated Seats.aero source ids, e.g. united,aeroplan",
    )
    notes: str = ""

    @field_validator("origin_iata", "destination_iata", mode="before")
    @classmethod
    def upper_iata(cls, v: str) -> str:
        return str(v).strip().upper()

    @field_validator("cabin", mode="before")
    @classmethod
    def normalize_cabin(cls, v: str) -> str:
        s = str(v).strip().lower().replace(" ", "_")
        allowed = {"economy", "premium_economy", "business", "first"}
        if s not in allowed:
            raise ValueError(f"cabin must be one of {allowed}, got {v!r}")
        return s

    @field_validator("passenger_ids", mode="before")
    @classmethod
    def parse_passenger_ids(cls, v: object) -> list[str] | None:
        if v is None:
            return None
        if isinstance(v, list):
            out = [str(x).strip() for x in v if str(x).strip()]
            return out or None
        s = str(v).strip()
        if not s:
            return None
        out = [p.strip() for p in s.split(",") if p.strip()]
        return out or None


class OpportunityRow(BaseModel):
    """Broader award/cash discovery rules (optional section)."""

    id: str
    origin_airports: str = Field(..., description="Comma-separated IATA codes")
    destination_airports: str
    start_date: date
    end_date: date
    cabin: str = "economy"
    max_cash: float | None = None
    max_miles: int | None = None
    passenger_ids: list[str] | None = Field(
        default=None,
        description="Comma-separated in MD; overrides frontmatter default_passenger_ids.",
    )
    seats_aero_sources: str | None = None
    notes: str = ""

    @field_validator("cabin", mode="before")
    @classmethod
    def normalize_cabin(cls, v: str) -> str:
        s = str(v).strip().lower().replace(" ", "_")
        allowed = {"economy", "premium_economy", "business", "first"}
        if s not in allowed:
            raise ValueError(f"cabin must be one of {allowed}, got {v!r}")
        return s

    @field_validator("passenger_ids", mode="before")
    @classmethod
    def parse_passenger_ids(cls, v: object) -> list[str] | None:
        if v is None:
            return None
        if isinstance(v, list):
            out = [str(x).strip() for x in v if str(x).strip()]
            return out or None
        s = str(v).strip()
        if not s:
            return None
        out = [p.strip() for p in s.split(",") if p.strip()]
        return out or None


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

    currency: str = "USD"
    default_passenger_ids: list[str] = Field(default_factory=list)
    history_window_days: int = 90
    max_dates_per_watch_row: int = 14
    travel_policy: TravelPolicy | None = None


class KellyConfig(BaseModel):
    """Parsed kelly.md."""

    frontmatter: KellyFrontmatter
    passengers: list[PassengerRow]
    planned: list[PlannedWatchRow]
    opportunities: list[OpportunityRow] = Field(default_factory=list)
    trains: list[TrainRow] = Field(default_factory=list)
    stays: list[StayRow] = Field(default_factory=list)
    raw_markdown: str = ""


# --- Table parsing ---


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if not line.startswith("|"):
        return []
    parts = [p.strip() for p in line.split("|")]
    # leading/trailing empty from split
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
            # pad or trim
            while len(cells) < len(headers):
                cells.append("")
            cells = cells[: len(headers)]
        rows.append(dict(zip(headers, cells, strict=True)))
    return rows


def _parse_date(s: str) -> date:
    s = s.strip()
    return date.fromisoformat(s)


def _row_to_planned(d: dict[str, str]) -> PlannedWatchRow:
    return PlannedWatchRow(
        id=d.get("id", "").strip(),
        origin_iata=d["origin_iata"],
        destination_iata=d["destination_iata"],
        date_start=_parse_date(d["date_start"]),
        date_end=_parse_date(d["date_end"]),
        cabin=d.get("cabin", "economy") or "economy",
        target_price=float(d["target_price"]) if d.get("target_price", "").strip() else None,
        target_miles=int(d["target_miles"]) if d.get("target_miles", "").strip() else None,
        passenger_ids=d.get("passenger_ids") or None,
        seats_aero_sources=d.get("seats_aero_sources") or None,
        notes=d.get("notes", "") or "",
    )


def _row_to_passenger(d: dict[str, str]) -> PassengerRow:
    return PassengerRow(
        id=d["id"].strip(),
        label=d.get("label", "") or "",
        type=(d.get("type", "adult") or "adult").strip().lower(),
    )


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


def _row_to_opportunity(d: dict[str, str]) -> OpportunityRow:
    return OpportunityRow(
        id=d.get("id", "").strip(),
        origin_airports=d["origin_airports"].strip(),
        destination_airports=d["destination_airports"].strip(),
        start_date=_parse_date(d["start_date"]),
        end_date=_parse_date(d["end_date"]),
        cabin=d.get("cabin", "economy") or "economy",
        max_cash=float(d["max_cash"]) if d.get("max_cash", "").strip() else None,
        max_miles=int(d["max_miles"]) if d.get("max_miles", "").strip() else None,
        passenger_ids=d.get("passenger_ids") or None,
        seats_aero_sources=d.get("seats_aero_sources") or None,
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
    passengers_tbl = parse_markdown_table(_section_body(content, "Passengers"))
    planned_tbl = parse_markdown_table(_section_body(content, "Planned watchlist"))
    opp_tbl = parse_markdown_table(_section_body(content, "Opportunities"))
    trains_tbl = parse_markdown_table(_section_body(content, "Trains"))
    stays_tbl = parse_markdown_table(_section_body(content, "Stays"))

    passengers = [_row_to_passenger(r) for r in passengers_tbl if r.get("id")]
    planned = [_row_to_planned(r) for r in planned_tbl if r.get("id")]
    opportunities = [_row_to_opportunity(r) for r in opp_tbl if r.get("id")]
    trains = [_row_to_train(r) for r in trains_tbl if r.get("id")]
    stays = [_row_to_stay(r) for r in stays_tbl if r.get("id")]

    return KellyConfig(
        frontmatter=frontmatter_model,
        passengers=passengers,
        planned=planned,
        opportunities=opportunities,
        trains=trains,
        stays=stays,
        raw_markdown=raw,
    )


def passengers_for_cash(
    cfg: KellyConfig,
    *,
    passenger_ids_override: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build passenger type dicts for cash APIs.

    Uses *passenger_ids_override* when set (per watchlist/opportunity row); otherwise
    ``default_passenger_ids`` from front matter; when that is empty, all passengers.
    """
    ids = (
        passenger_ids_override
        if passenger_ids_override is not None
        else cfg.frontmatter.default_passenger_ids
    )
    rows = [p for p in cfg.passengers if not ids or p.id in ids]
    if not rows:
        rows = cfg.passengers
    return [{"type": p.type} for p in rows]
