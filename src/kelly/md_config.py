"""Load household travel config from Markdown (frontmatter + tables)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field, field_validator

# --- Models ---


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


class KellyFrontmatter(BaseModel):
    """YAML front matter defaults."""

    currency: str = "USD"
    default_passenger_ids: list[str] = Field(default_factory=list)
    history_window_days: int = 90
    max_dates_per_watch_row: int = 14


class KellyConfig(BaseModel):
    """Parsed kelly.md."""

    frontmatter: KellyFrontmatter
    passengers: list[PassengerRow]
    planned: list[PlannedWatchRow]
    opportunities: list[OpportunityRow] = Field(default_factory=list)
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

    passengers = [_row_to_passenger(r) for r in passengers_tbl if r.get("id")]
    planned = [_row_to_planned(r) for r in planned_tbl if r.get("id")]
    opportunities = [_row_to_opportunity(r) for r in opp_tbl if r.get("id")]

    return KellyConfig(
        frontmatter=frontmatter_model,
        passengers=passengers,
        planned=planned,
        opportunities=opportunities,
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
