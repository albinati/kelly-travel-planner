"""Trip-session (dossier) orchestration.

A *session* is the unit a user actually plans in: an **intent** (who / from / to /
dates / constraints) + the **dated option snapshots** it surfaced + a **lifecycle
status** (``idea → active → shortlisted → booked → archived``). A dream-box idea is
simply a session with ``status='idea'`` — see ``profile_service`` for those thin
shortcuts.

This layer mirrors ``profile_service``: it validates the JSON-string payloads the
MCP tools accept, opens the default store, and returns jsonable dicts. It never
raises — failures come back as ``{"error": ...}``.

**v1 stores snapshots verbatim** (no re-quote / price-diff yet). Option snapshots
are append-only with a ``captured_at`` timestamp, so the deferred diff layer can
read them as a price time-series without any schema change.
"""

from __future__ import annotations

from typing import Any

from kelly.history_store import (
    SessionOption,
    SqliteHistoryStore,
    TripSession,
)
from kelly.services.profile_service import _store, _validate_json

_VALID_STATUS = {"idea", "active", "shortlisted", "booked", "archived"}
_VALID_KIND = {
    "cash_flight",
    "avios",
    "train",
    "stay",
    "experience",
    "car",
    "local",
    "package",
    "other",
}


def _option_to_dict(o: SessionOption) -> dict[str, Any]:
    return {
        "id": o.id,
        "session_id": o.session_id,
        "kind": o.kind,
        "label": o.label,
        "price_amount": o.price_amount,
        "price_currency": o.price_currency,
        "avios_points": o.avios_points,
        "source": o.source,
        "payload_json": o.payload_json,
        "captured_at": o.captured_at,
    }


def _session_to_dict(s: TripSession, options: list[SessionOption] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": s.id,
        "profile_id": s.profile_id,
        "title": s.title,
        "destination": s.destination,
        "notes": s.notes,
        "status": s.status,
        "intent_json": s.intent_json,
        "payload_json": s.payload_json,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }
    if options is not None:
        d["options"] = [_option_to_dict(o) for o in options]
    return d


def _status_error(status: str) -> str:
    return f"invalid status: {status}; must be one of {sorted(_VALID_STATUS)}"


def create_session(
    title: str,
    destination: str = "",
    notes: str = "",
    profile_id: str = "",
    intent_json: str = "",
    payload_json: str = "",
    status: str = "active",
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Create a session; return the stored row. ``status`` defaults to ``active``
    (use ``idea`` to park it like a dream-box entry)."""
    if not title or not title.strip():
        return {"error": "title is required"}
    status = (status or "active").strip().lower()
    if status not in _VALID_STATUS:
        return {"error": _status_error(status)}
    for field, val in (("intent_json", intent_json), ("payload_json", payload_json)):
        err = _validate_json(val, field)
        if err is not None:
            return {"error": err}
    st = _store(store)
    new_id = st.add_session(
        TripSession(
            id=None,
            profile_id=profile_id.strip() or None,
            title=title.strip(),
            destination=destination.strip() or None,
            notes=notes.strip() or None,
            payload_json=payload_json.strip() or None,
            status=status,
            intent_json=intent_json.strip() or None,
        )
    )
    created = st.get_session(new_id)
    return _session_to_dict(created) if created else {"error": "session not stored"}


def get_session(
    session_id: int,
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Return the session plus its dated option snapshots, or an error if absent."""
    st = _store(store)
    s = st.get_session(session_id)
    if s is None:
        return {"error": f"session not found: {session_id}"}
    return _session_to_dict(s, st.list_session_options(session_id))


def list_sessions(
    profile_id: str = "",
    status: str = "",
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Sessions newest first; optionally filtered by ``profile_id`` / ``status``."""
    pid = profile_id.strip() or None
    status_filter = status.strip().lower() or None
    if status_filter is not None and status_filter not in _VALID_STATUS:
        return {"error": _status_error(status_filter)}
    sessions = _store(store).list_sessions(profile_id=pid, status=status_filter)
    return {"sessions": [_session_to_dict(s) for s in sessions]}


def update_session(
    session_id: int,
    status: str = "",
    title: str = "",
    notes: str = "",
    destination: str = "",
    intent_json: str = "",
    payload_json: str = "",
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Patch any provided field (blanks are left untouched) and re-stamp
    ``updated_at``. Returns the updated session (with options) or an error."""
    fields: dict[str, str] = {}
    if status and status.strip():
        s = status.strip().lower()
        if s not in _VALID_STATUS:
            return {"error": _status_error(s)}
        fields["status"] = s
    if title and title.strip():
        fields["title"] = title.strip()
    if notes and notes.strip():
        fields["notes"] = notes.strip()
    if destination and destination.strip():
        fields["destination"] = destination.strip()
    for field, val in (("intent_json", intent_json), ("payload_json", payload_json)):
        if val and val.strip():
            err = _validate_json(val, field)
            if err is not None:
                return {"error": err}
            fields[field] = val.strip()
    if not fields:
        return {"error": "nothing to update"}
    st = _store(store)
    if not st.update_session(session_id, **fields):
        return {"error": f"session not found: {session_id}"}
    updated = st.get_session(session_id)
    return (
        _session_to_dict(updated, st.list_session_options(session_id))
        if updated
        else {"error": f"session not found: {session_id}"}
    )


def delete_session(
    session_id: int,
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Delete a session and its option snapshots; ``{"deleted": id}`` or an error."""
    deleted = _store(store).delete_session(session_id)
    if not deleted:
        return {"error": f"session not found: {session_id}"}
    return {"deleted": session_id}


def attach_option(
    session_id: int,
    kind: str,
    label: str = "",
    amount: str | float = "",
    currency: str = "",
    avios_points: int = 0,
    source: str = "manual",
    payload_json: str = "",
    *,
    store: SqliteHistoryStore | None = None,
) -> dict[str, Any]:
    """Append a dated option snapshot to a session. ``amount`` is the cash price
    (optional — avios-only options leave it blank); ``avios_points`` is a separate
    currency and is never summed into cash. ``payload_json`` carries the full
    option blob (times, flight numbers, notes). Returns the created snapshot."""
    k = (kind or "").strip().lower()
    if k not in _VALID_KIND:
        return {"error": f"invalid kind: {kind}; must be one of {sorted(_VALID_KIND)}"}
    err = _validate_json(payload_json)
    if err is not None:
        return {"error": err}
    price_amount: float | None = None
    if amount not in (None, ""):
        try:
            price_amount = float(amount)
        except (TypeError, ValueError):
            return {"error": f"amount is not a number: {amount}"}
    st = _store(store)
    if st.get_session(session_id) is None:
        return {"error": f"session not found: {session_id}"}
    new_id = st.add_session_option(
        SessionOption(
            id=None,
            session_id=session_id,
            kind=k,
            label=label.strip() or None,
            price_amount=price_amount,
            price_currency=currency.strip().upper() or None,
            avios_points=int(avios_points) or None,
            source=source.strip() or None,
            payload_json=payload_json.strip() or None,
        )
    )
    created = next((o for o in st.list_session_options(session_id) if o.id == new_id), None)
    return _option_to_dict(created) if created else {"error": "option not stored"}
