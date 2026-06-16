"""Traveller profile + dream-box orchestration (single-user).

Thin layer over ``SqliteHistoryStore``: validates the JSON-string payloads the
MCP tools accept, opens the default store, and returns jsonable dicts. The
profile is a free-form blob (travellers, food/relax prefs, hard exclusions, home
origin airports, Avios notes); the dream-box parks future-trip ideas. Keeping the
shape free-form is intentional — judgment about *what* goes in the profile lives
in the ``/kelly-plan`` skill, not here.
"""

from __future__ import annotations

import json
from typing import Any

from kelly.history_store import (
    DreamTrip,
    Profile,
    SqliteHistoryStore,
    open_default_store,
)


def _store(store: SqliteHistoryStore | None) -> SqliteHistoryStore:
    return store if store is not None else open_default_store()


def _validate_json(payload_json: str, field: str = "payload_json") -> str | None:
    """Return a clean error string if ``payload_json`` is non-empty and not valid
    JSON; ``None`` if it parses (or is empty, which we treat as "unset")."""
    if not payload_json or not payload_json.strip():
        return None
    try:
        json.loads(payload_json)
    except (json.JSONDecodeError, TypeError) as e:
        return f"{field} is not valid JSON: {e}"
    return None


def _profile_to_dict(p: Profile) -> dict[str, Any]:
    return {
        "profile_id": p.profile_id,
        "name": p.name,
        "payload_json": p.payload_json,
        "updated_at": p.updated_at,
    }


def _dream_to_dict(t: DreamTrip) -> dict[str, Any]:
    return {
        "id": t.id,
        "profile_id": t.profile_id,
        "title": t.title,
        "destination": t.destination,
        "notes": t.notes,
        "payload_json": t.payload_json,
        "created_at": t.created_at,
    }


def set_profile(
    profile_id: str,
    name: str,
    payload_json: str = "",
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Upsert the profile keyed by ``profile_id``; return the stored row."""
    if not profile_id or not profile_id.strip():
        return {"error": "profile_id is required"}
    err = _validate_json(payload_json)
    if err is not None:
        return {"error": err}
    st = _store(store)
    st.upsert_profile(
        Profile(
            profile_id=profile_id.strip(),
            name=name or None,
            payload_json=payload_json.strip() or None,
        )
    )
    stored = st.get_profile(profile_id.strip())
    return _profile_to_dict(stored) if stored else {"error": "profile not stored"}


def get_profile(
    profile_id: str,
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Return the profile, or ``{"error": ...}`` if absent."""
    if not profile_id or not profile_id.strip():
        return {"error": "profile_id is required"}
    stored = _store(store).get_profile(profile_id.strip())
    if stored is None:
        return {"error": f"profile not found: {profile_id}"}
    return _profile_to_dict(stored)


def add_dream_trip(
    title: str,
    destination: str = "",
    notes: str = "",
    profile_id: str = "",
    payload_json: str = "",
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Park a future-trip idea in the dream-box; return the created row."""
    if not title or not title.strip():
        return {"error": "title is required"}
    err = _validate_json(payload_json)
    if err is not None:
        return {"error": err}
    st = _store(store)
    new_id = st.add_dream_trip(
        DreamTrip(
            id=None,
            profile_id=profile_id.strip() or None,
            title=title.strip(),
            destination=destination.strip() or None,
            notes=notes.strip() or None,
            payload_json=payload_json.strip() or None,
        )
    )
    created = next((t for t in st.list_dream_trips() if t.id == new_id), None)
    return _dream_to_dict(created) if created else {"error": "dream trip not stored"}


def list_dream_trips(
    profile_id: str = "",
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """All parked trips (newest first); optionally filtered by ``profile_id``."""
    pid = profile_id.strip() or None
    trips = _store(store).list_dream_trips(pid)
    return {"dream_trips": [_dream_to_dict(t) for t in trips]}


def remove_dream_trip(
    dream_id: int,
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Delete a parked trip by id; ``{"deleted": id}`` or an error if absent."""
    deleted = _store(store).delete_dream_trip(dream_id)
    if not deleted:
        return {"error": f"dream trip not found: {dream_id}"}
    return {"deleted": dream_id}
