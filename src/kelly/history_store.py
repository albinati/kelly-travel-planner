"""Append-only SQLite history for trip price observations (trains + stays)."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path


def days_before_departure(departure_date: date, observed_at: datetime | None = None) -> int:
    obs = observed_at or datetime.now(timezone.utc)
    dep = datetime(
        departure_date.year, departure_date.month, departure_date.day, tzinfo=timezone.utc
    )
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


def hotel_key(area: str, check_in: date, check_out: date) -> str:
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


@dataclass
class BookingRecord:
    """One booked artifact (train/stay/ticket) with its money + provenance.
    Append-only: a corrected total is a new row, not an update — `fetch_bookings`
    returns the latest per `(trip_id, leg)` unless `all_versions=True`."""

    trip_id: str
    leg: str  # "airbnb" | "eurostar_out" | "eurostar_back" | "disney_tickets" | ...
    provider: str  # "airbnb" | "eurostar" | "disneyland_paris" | "liteapi" | "manual"
    confirmation_ref: str | None
    total_amount: float
    currency: str  # ISO 4217 uppercase
    paid_at: str | None  # ISO date
    paid_by: str = "me"  # free-form for now; promoted to enum when ## Groups exists
    raw_json: str | None = None


@dataclass
class HotelObservation:
    """One per hotel search call (LiteAPI) — same shape as StayObservation but
    *rooms_count* in place of bedrooms_min (hotels book rooms, not properties).
    Kept in its own table because price semantics differ: hotel totals sum
    multi-room rates, Airbnb totals are whole-listing.
    """

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
    rooms_count: int | None
    min_stars: float | None
    pax_adult_eq: int
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

CREATE TABLE IF NOT EXISTS hotel_observations (
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
    rooms_count INTEGER,
    min_stars REAL,
    pax_adult_eq INTEGER NOT NULL,
    pax_child INTEGER NOT NULL,
    pax_infant INTEGER NOT NULL,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_hotel_trip_time
    ON hotel_observations(trip_key, observed_at);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    leg TEXT NOT NULL,
    provider TEXT NOT NULL,
    confirmation_ref TEXT,
    total_amount REAL NOT NULL,
    currency TEXT NOT NULL,
    paid_at TEXT,
    paid_by TEXT NOT NULL DEFAULT 'me',
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_bookings_trip
    ON bookings(trip_id, leg, recorded_at);

CREATE TABLE IF NOT EXISTS fx_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of TEXT NOT NULL,
    base_ccy TEXT NOT NULL,
    quote_ccy TEXT NOT NULL,
    rate REAL NOT NULL,
    fetched_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'ecb',
    UNIQUE(as_of, base_ccy, quote_ccy, source)
);
CREATE INDEX IF NOT EXISTS idx_fx_lookup
    ON fx_rates(as_of, base_ccy, quote_ccy);
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

    def append_hotel(self, obs: HotelObservation) -> int:
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
            "rooms_count",
            "min_stars",
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
                f"INSERT INTO hotel_observations ({','.join(cols)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def fetch_train_amounts(self, key: str, *, window_days: int = 90) -> list[float]:
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

    def fetch_stay_amounts(self, key: str, *, window_days: int = 90) -> list[float]:
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

    def record_booking(self, rec: BookingRecord) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cols = [
            "recorded_at",
            "trip_id",
            "leg",
            "provider",
            "confirmation_ref",
            "total_amount",
            "currency",
            "paid_at",
            "paid_by",
            "raw_json",
        ]
        row = {**asdict(rec), "recorded_at": now}
        row["currency"] = (row["currency"] or "").upper()
        values = [row[c] for c in cols]
        placeholders = ",".join("?" * len(cols))
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"INSERT INTO bookings ({','.join(cols)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def fetch_bookings(self, trip_id: str, *, all_versions: bool = False) -> list[BookingRecord]:
        """Latest booking per (trip_id, leg) by default. Pass all_versions=True
        to get the full append-only history for that trip."""
        if all_versions:
            query = """
                SELECT trip_id, leg, provider, confirmation_ref, total_amount,
                       currency, paid_at, paid_by, raw_json
                FROM bookings
                WHERE trip_id = ?
                ORDER BY leg ASC, recorded_at ASC
            """
        else:
            # Window function (SQLite 3.25+) — keep one row per leg, the latest.
            query = """
                SELECT trip_id, leg, provider, confirmation_ref, total_amount,
                       currency, paid_at, paid_by, raw_json
                FROM (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY leg ORDER BY recorded_at DESC
                           ) AS rn
                    FROM bookings
                    WHERE trip_id = ?
                )
                WHERE rn = 1
                ORDER BY leg ASC
            """
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(query, (trip_id,))
            return [
                BookingRecord(
                    trip_id=r[0],
                    leg=r[1],
                    provider=r[2],
                    confirmation_ref=r[3],
                    total_amount=float(r[4]),
                    currency=r[5],
                    paid_at=r[6],
                    paid_by=r[7],
                    raw_json=r[8],
                )
                for r in cur
            ]

    def record_fx_rate(
        self,
        *,
        as_of: str,
        base_ccy: str,
        quote_ccy: str,
        rate: float,
        source: str = "ecb",
    ) -> int:
        """Persist one EUR-base FX row. Idempotent via UNIQUE(as_of, base, quote, source)."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO fx_rates
                    (as_of, base_ccy, quote_ccy, rate, fetched_at, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (as_of, base_ccy.upper(), quote_ccy.upper(), float(rate), now, source),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def fetch_fx_rate(
        self,
        *,
        as_of: str,
        base_ccy: str,
        quote_ccy: str,
        source: str = "ecb",
    ) -> float | None:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                SELECT rate FROM fx_rates
                WHERE as_of = ? AND base_ccy = ? AND quote_ccy = ? AND source = ?
                ORDER BY fetched_at DESC LIMIT 1
                """,
                (as_of, base_ccy.upper(), quote_ccy.upper(), source),
            )
            row = cur.fetchone()
            return float(row[0]) if row else None

    def latest_fx_as_of(self, *, source: str = "ecb") -> str | None:
        """Most recent `as_of` we have any rate row for. Used to fall back when
        today's rates haven't been fetched yet but yesterday's are usable."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT MAX(as_of) FROM fx_rates WHERE source = ?",
                (source,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None

    def fetch_hotel_amounts(self, key: str, *, window_days: int = 90) -> list[float]:
        cutoff = datetime.now(timezone.utc).timestamp() - window_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                SELECT best_total_amount FROM hotel_observations
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
