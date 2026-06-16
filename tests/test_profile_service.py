"""Tests for the profile + dream-box tables and their service layer.

Local SQLite via tmp_path, no network. The store is passed explicitly so we
never touch the default KELLY_DATA_DIR path.
"""

from __future__ import annotations

import json

from kelly.history_store import SqliteHistoryStore
from kelly.services import profile_service


# --- Profile (upsert by profile_id) ---------------------------------------


def test_profile_set_get_roundtrip(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    payload = json.dumps({"home_airports": ["LHR", "LGW"], "avios": "BA Amex"})
    out = profile_service.set_profile("me", "Luis", payload, store=store)
    assert out["profile_id"] == "me"
    assert out["name"] == "Luis"
    assert json.loads(out["payload_json"])["home_airports"] == ["LHR", "LGW"]
    assert out["updated_at"]  # stamped

    got = profile_service.get_profile("me", store=store)
    assert got["profile_id"] == "me"
    assert got["name"] == "Luis"
    assert json.loads(got["payload_json"])["avios"] == "BA Amex"


def test_profile_upsert_updates_not_duplicates(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    profile_service.set_profile("me", "Old Name", '{"v": 1}', store=store)
    profile_service.set_profile("me", "New Name", '{"v": 2}', store=store)

    got = profile_service.get_profile("me", store=store)
    assert got["name"] == "New Name"
    assert json.loads(got["payload_json"])["v"] == 2

    # Exactly one row in the table.
    import sqlite3

    with sqlite3.connect(store.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    assert count == 1


def test_profile_get_missing_returns_error(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    out = profile_service.get_profile("nobody", store=store)
    assert "error" in out


def test_profile_set_rejects_bad_json(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    out = profile_service.set_profile("me", "Luis", "{not json", store=store)
    assert "error" in out
    # Nothing was written.
    assert "error" in profile_service.get_profile("me", store=store)


def test_profile_empty_payload_is_allowed(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    out = profile_service.set_profile("me", "Luis", "", store=store)
    assert out["profile_id"] == "me"
    assert out["payload_json"] is None


# --- Dream box (add / list / remove) --------------------------------------


def test_dreambox_add_list_remove(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    created = profile_service.add_dream_trip(
        "Sicily kid-free weekend",
        destination="CTA",
        notes="Thu->Sun, Avios",
        profile_id="me",
        store=store,
    )
    assert created["id"] > 0
    assert created["title"] == "Sicily kid-free weekend"
    assert created["destination"] == "CTA"

    listed = profile_service.list_dream_trips(store=store)["dream_trips"]
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]

    removed = profile_service.remove_dream_trip(created["id"], store=store)
    assert removed == {"deleted": created["id"]}
    assert profile_service.list_dream_trips(store=store)["dream_trips"] == []


def test_dreambox_remove_missing_returns_error(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    out = profile_service.remove_dream_trip(999, store=store)
    assert "error" in out


def test_dreambox_list_filtered_by_profile(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    profile_service.add_dream_trip("Rhodes", profile_id="me", store=store)
    profile_service.add_dream_trip("Puglia", profile_id="me", store=store)
    profile_service.add_dream_trip("Other person's idea", profile_id="them", store=store)

    mine = profile_service.list_dream_trips("me", store=store)["dream_trips"]
    assert {t["title"] for t in mine} == {"Rhodes", "Puglia"}

    theirs = profile_service.list_dream_trips("them", store=store)["dream_trips"]
    assert len(theirs) == 1
    assert theirs[0]["title"] == "Other person's idea"

    all_trips = profile_service.list_dream_trips(store=store)["dream_trips"]
    assert len(all_trips) == 3


def test_dreambox_add_requires_title(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    out = profile_service.add_dream_trip("   ", store=store)
    assert "error" in out


def test_dreambox_add_rejects_bad_json(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    out = profile_service.add_dream_trip("Sicily", payload_json="{nope", store=store)
    assert "error" in out


# --- Persistence across reopening the store from the same path ------------


def test_persistence_across_reopen(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    store1 = SqliteHistoryStore(db)
    profile_service.set_profile("me", "Luis", '{"v": 1}', store=store1)
    dream = profile_service.add_dream_trip("Sicily", destination="CTA", store=store1)

    # Reopen from the same path — a fresh process would do exactly this.
    store2 = SqliteHistoryStore(db)
    got = profile_service.get_profile("me", store=store2)
    assert got["name"] == "Luis"
    assert json.loads(got["payload_json"])["v"] == 1

    trips = profile_service.list_dream_trips(store=store2)["dream_trips"]
    assert len(trips) == 1
    assert trips[0]["id"] == dream["id"]
    assert trips[0]["destination"] == "CTA"
