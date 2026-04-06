"""Append-only SQLite history for price/award observations."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable


def route_key(
    origin_iata: str,
    dest_iata: str,
    cabin: str,
    departure_date: date,
) -> str:
    return f"{origin_iata.upper()}|{dest_iata.upper()}|{cabin.lower()}|{departure_date.isoformat()}"


def days_before_departure(departure_date: date, observed_at: datetime | None = None) -> int:
    obs = observed_at or datetime.now(timezone.utc)
    dep = datetime(departure_date.year, departure_date.month, departure_date.day, tzinfo=timezone.utc)
    delta = dep.date() - obs.date()
    return max(0, delta.days)


@dataclass
class Observation:
    route_key: str
    origin_iata: str
    destination_iata: str
    cabin: str
    departure_date: str
    days_before_departure: int
    best_cash_amount: float | None
    cash_currency: str | None
    best_award_miles: int | None
    award_program: str | None
    award_taxes_cents: int | None
    source_cash: str | None
    source_award: str | None
    watchlist_row_id: str | None
    raw_json: str | None = None


@runtime_checkable
class HistoryStore(Protocol):
    def append(self, obs: Observation) -> int: ...
    def fetch_amounts_for_route(
        self,
        key: str,
        *,
        window_days: int = 90,
    ) -> tuple[list[float], list[int]]: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    route_key TEXT NOT NULL,
    origin_iata TEXT NOT NULL,
    destination_iata TEXT NOT NULL,
    cabin TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    days_before_departure INTEGER NOT NULL,
    best_cash_amount REAL,
    cash_currency TEXT,
    best_award_miles INTEGER,
    award_program TEXT,
    award_taxes_cents INTEGER,
    source_cash TEXT,
    source_award TEXT,
    watchlist_row_id TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_route_time ON observations(route_key, observed_at);
"""


class SqliteHistoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(_SCHEMA)

    def append(self, obs: Observation) -> int:
        now = datetime.now(timezone.utc).isoformat()
        row = {
            **asdict(obs),
            "observed_at": now,
        }
        cols = [
            "observed_at",
            "route_key",
            "origin_iata",
            "destination_iata",
            "cabin",
            "departure_date",
            "days_before_departure",
            "best_cash_amount",
            "cash_currency",
            "best_award_miles",
            "award_program",
            "award_taxes_cents",
            "source_cash",
            "source_award",
            "watchlist_row_id",
            "raw_json",
        ]
        placeholders = ",".join("?" * len(cols))
        values = [row[c] for c in cols]
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"INSERT INTO observations ({','.join(cols)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def fetch_amounts_for_route(
        self,
        key: str,
        *,
        window_days: int = 90,
    ) -> tuple[list[float], list[int]]:
        """Return parallel lists of historical cash amounts and award miles (non-null only)."""
        cutoff = datetime.now(timezone.utc).timestamp() - window_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        cash_out: list[float] = []
        miles_out: list[int] = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT best_cash_amount, best_award_miles FROM observations
                WHERE route_key = ? AND observed_at >= ?
                ORDER BY observed_at ASC
                """,
                (key, cutoff_iso),
            )
            for r in cur:
                if r["best_cash_amount"] is not None:
                    cash_out.append(float(r["best_cash_amount"]))
                if r["best_award_miles"] is not None:
                    miles_out.append(int(r["best_award_miles"]))
        return cash_out, miles_out
