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


def train_key(
    operator: str,
    origin_city: str,
    destination_city: str,
    class_: str,
    departure_date: date,
) -> str:
    return (
        f"{operator.lower()}|{origin_city.upper()}|{destination_city.upper()}"
        f"|{class_.lower()}|{departure_date.isoformat()}"
    )


def stay_key(area: str, check_in: date, check_out: date) -> str:
    return f"{area.strip().lower()}|{check_in.isoformat()}|{check_out.isoformat()}"


@dataclass
class TrainObservation:
    """One per train search call — captures the cheapest per-adult fare seen
    for a given operator + route + class + departure_date."""

    trip_key: str
    trip_row_id: str | None
    operator: str
    origin_city: str
    destination_city: str
    class_: str
    departure_date: str
    days_before_departure: int
    best_per_adult_amount: float | None
    currency: str | None
    journey_count: int
    pax_adult_eq: int  # adults + seniors + teens (treated as adult-priced)
    pax_child: int
    pax_infant: int
    raw_json: str | None = None


@dataclass
class StayObservation:
    """One per stay search call — captures the cheapest total seen for a given
    area + check-in + check-out, plus how many listings were returned."""

    trip_key: str
    trip_row_id: str | None
    area: str
    check_in: str
    check_out: str
    nights: int
    days_before_check_in: int
    best_total_amount: float | None
    currency: str | None
    listings_count: int
    bedrooms_min: int | None
    pax_adult_eq: int  # adults + 13+
    pax_child: int
    pax_infant: int
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

-- Non-flight track (`[trips]` extra). Separate tables so route-key semantics
-- and baseline computation don't collide with flights.
CREATE TABLE IF NOT EXISTS train_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    trip_key TEXT NOT NULL,
    trip_row_id TEXT,
    operator TEXT NOT NULL,
    origin_city TEXT NOT NULL,
    destination_city TEXT NOT NULL,
    class_ TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    days_before_departure INTEGER NOT NULL,
    best_per_adult_amount REAL,
    currency TEXT,
    journey_count INTEGER NOT NULL,
    pax_adult_eq INTEGER NOT NULL,
    pax_child INTEGER NOT NULL,
    pax_infant INTEGER NOT NULL,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_train_trip_time
    ON train_observations(trip_key, observed_at);

CREATE TABLE IF NOT EXISTS stay_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    trip_key TEXT NOT NULL,
    trip_row_id TEXT,
    area TEXT NOT NULL,
    check_in TEXT NOT NULL,
    check_out TEXT NOT NULL,
    nights INTEGER NOT NULL,
    days_before_check_in INTEGER NOT NULL,
    best_total_amount REAL,
    currency TEXT,
    listings_count INTEGER NOT NULL,
    bedrooms_min INTEGER,
    pax_adult_eq INTEGER NOT NULL,
    pax_child INTEGER NOT NULL,
    pax_infant INTEGER NOT NULL,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_stay_trip_time
    ON stay_observations(trip_key, observed_at);
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

    # ----- Non-flight track ----------------------------------------------------

    def append_train(self, obs: TrainObservation) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cols = [
            "observed_at",
            "trip_key",
            "trip_row_id",
            "operator",
            "origin_city",
            "destination_city",
            "class_",
            "departure_date",
            "days_before_departure",
            "best_per_adult_amount",
            "currency",
            "journey_count",
            "pax_adult_eq",
            "pax_child",
            "pax_infant",
            "raw_json",
        ]
        row = {**asdict(obs), "observed_at": now}
        values = [row[c] for c in cols]
        placeholders = ",".join("?" * len(cols))
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"INSERT INTO train_observations ({','.join(cols)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def append_stay(self, obs: StayObservation) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cols = [
            "observed_at",
            "trip_key",
            "trip_row_id",
            "area",
            "check_in",
            "check_out",
            "nights",
            "days_before_check_in",
            "best_total_amount",
            "currency",
            "listings_count",
            "bedrooms_min",
            "pax_adult_eq",
            "pax_child",
            "pax_infant",
            "raw_json",
        ]
        row = {**asdict(obs), "observed_at": now}
        values = [row[c] for c in cols]
        placeholders = ",".join("?" * len(cols))
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"INSERT INTO stay_observations ({','.join(cols)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def fetch_train_amounts(
        self, key: str, *, window_days: int = 90
    ) -> list[float]:
        cutoff = datetime.now(timezone.utc).timestamp() - window_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        out: list[float] = []
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                SELECT best_per_adult_amount FROM train_observations
                WHERE trip_key = ? AND observed_at >= ?
                  AND best_per_adult_amount IS NOT NULL
                ORDER BY observed_at ASC
                """,
                (key, cutoff_iso),
            )
            out = [float(r[0]) for r in cur]
        return out

    def fetch_stay_amounts(
        self, key: str, *, window_days: int = 90
    ) -> list[float]:
        cutoff = datetime.now(timezone.utc).timestamp() - window_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        out: list[float] = []
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                SELECT best_total_amount FROM stay_observations
                WHERE trip_key = ? AND observed_at >= ?
                  AND best_total_amount IS NOT NULL
                ORDER BY observed_at ASC
                """,
                (key, cutoff_iso),
            )
            out = [float(r[0]) for r in cur]
        return out
