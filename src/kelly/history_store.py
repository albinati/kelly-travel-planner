"""Append-only SQLite history for trip price observations (trains + stays)."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path


def days_before_departure(departure_date: date, observed_at: datetime | None = None) -> int:
    obs = observed_at or datetime.now(timezone.utc)
    dep = datetime(departure_date.year, departure_date.month, departure_date.day, tzinfo=timezone.utc)
    delta = dep.date() - obs.date()
    return max(0, delta.days)


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


_SCHEMA = """
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
            return [float(r[0]) for r in cur]

    def fetch_stay_amounts(
        self, key: str, *, window_days: int = 90
    ) -> list[float]:
        cutoff = datetime.now(timezone.utc).timestamp() - window_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
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
            return [float(r[0]) for r in cur]


def open_default_store() -> SqliteHistoryStore:
    """Open the default SqliteHistoryStore at KELLY_DATA_DIR/kelly_history.sqlite."""
    from kelly.settings import history_db_path

    return SqliteHistoryStore(history_db_path())
