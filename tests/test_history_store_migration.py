"""Migration safety: a legacy DB whose only saved-trips table is the old
``dream_box`` must be upgraded to ``trip_session`` non-destructively when opened."""

import sqlite3

from kelly.history_store import SqliteHistoryStore


def _build_legacy_db(path) -> None:
    """Recreate the pre-sessions ``dream_box`` schema + a couple of rows."""
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE dream_box (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT,
                title TEXT NOT NULL,
                destination TEXT,
                notes TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO dream_box (profile_id, title, destination, notes, payload_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("me", "Sicily kid-free", "CTA", "Avios", '{"v": 1}', "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO dream_box (profile_id, title, destination, notes, payload_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (None, "Lisbon weekend", "LIS", None, None, "2026-02-01T00:00:00+00:00"),
        )
        conn.commit()


def test_legacy_dream_box_is_migrated_in_place(tmp_path) -> None:
    db = tmp_path / "legacy.sqlite"
    _build_legacy_db(db)

    # Opening the store runs the migration.
    store = SqliteHistoryStore(db)

    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "trip_session" in tables
        assert "dream_box" not in tables  # renamed, not duplicated
        assert "session_option" in tables
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trip_session)")}
        assert {"status", "intent_json", "updated_at"} <= cols

    # Rows preserved, and now carry the default 'idea' status.
    sessions = store.list_sessions()
    assert {s.title for s in sessions} == {"Sicily kid-free", "Lisbon weekend"}
    assert all(s.status == "idea" for s in sessions)

    # The legacy dream-box view still works through the shims.
    ideas = store.list_dream_trips()
    assert len(ideas) == 2


def test_migration_is_idempotent(tmp_path) -> None:
    db = tmp_path / "legacy.sqlite"
    _build_legacy_db(db)
    SqliteHistoryStore(db)  # first open migrates
    store = SqliteHistoryStore(db)  # second open must be a no-op, not error
    assert len(store.list_sessions()) == 2


def test_fresh_db_has_sessions_schema(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "fresh.sqlite")
    assert store.list_sessions() == []
    with sqlite3.connect(store.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "trip_session" in tables and "session_option" in tables
        assert "dream_box" not in tables
