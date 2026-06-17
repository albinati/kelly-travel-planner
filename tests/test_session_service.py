import json

from kelly.history_store import SqliteHistoryStore
from kelly.services import session_service


def test_session_full_lifecycle(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")

    created = session_service.create_session(
        "Stanford reunion",
        destination="SFO",
        notes="GSB Ignite 6-9 Aug",
        profile_id="me",
        intent_json=json.dumps({"from": "LHR", "to": "SFO", "pax": 1}),
        store=store,
    )
    assert created["id"] > 0
    assert created["status"] == "active"  # default
    assert created["title"] == "Stanford reunion"
    assert json.loads(created["intent_json"])["to"] == "SFO"
    sid = created["id"]

    # get returns the session plus an (empty) options list
    got = session_service.get_session(sid, store=store)
    assert got["id"] == sid
    assert got["options"] == []

    # attach two dated snapshots: a cash flight and an avios-only option
    cash = session_service.attach_option(
        sid,
        "cash_flight",
        label="Virgin VS19",
        amount="860",
        currency="gbp",
        source="serpapi",
        payload_json=json.dumps({"flight": "VS19"}),
        store=store,
    )
    assert cash["id"] > 0
    assert cash["price_amount"] == 860.0
    assert cash["price_currency"] == "GBP"  # upper-cased
    assert cash["captured_at"]  # timestamped

    avios = session_service.attach_option(
        sid,
        "avios",
        label="BA286 Club World",
        avios_points=20000,
        source="seats_aero",
        store=store,
    )
    assert avios["price_amount"] is None  # avios-only
    assert avios["avios_points"] == 20000

    got = session_service.get_session(sid, store=store)
    assert len(got["options"]) == 2

    # move it along the lifecycle
    updated = session_service.update_session(sid, status="archived", store=store)
    assert updated["status"] == "archived"
    assert updated["updated_at"]

    # delete removes the session and its options
    assert session_service.delete_session(sid, store=store) == {"deleted": sid}
    assert "error" in session_service.get_session(sid, store=store)


def test_status_filter_and_validation(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    session_service.create_session("active one", status="active", store=store)
    session_service.create_session("parked idea", status="idea", store=store)

    listed = session_service.list_sessions(store=store)["sessions"]
    assert len(listed) == 2

    only_ideas = session_service.list_sessions(status="idea", store=store)["sessions"]
    assert len(only_ideas) == 1
    assert only_ideas[0]["title"] == "parked idea"

    assert "error" in session_service.create_session("x", status="bogus", store=store)
    assert "error" in session_service.list_sessions(status="bogus", store=store)


def test_invalid_inputs(tmp_path) -> None:
    store = SqliteHistoryStore(tmp_path / "t.sqlite")
    assert "error" in session_service.create_session("", store=store)  # title required
    assert "error" in session_service.create_session("t", intent_json="{not json", store=store)

    s = session_service.create_session("t", store=store)
    sid = s["id"]
    assert "error" in session_service.attach_option(sid, "spaceship", store=store)  # bad kind
    assert "error" in session_service.attach_option(sid, "stay", amount="not-a-number", store=store)
    assert "error" in session_service.attach_option(999999, "stay", store=store)  # no session
    assert "error" in session_service.update_session(sid, store=store)  # nothing to update
    assert "error" in session_service.update_session(999999, status="booked", store=store)


def test_persistence_across_reopen(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    store1 = SqliteHistoryStore(db)
    created = session_service.create_session("trip", status="shortlisted", store=store1)
    session_service.attach_option(
        created["id"], "train", amount="180", currency="GBP", store=store1
    )

    store2 = SqliteHistoryStore(db)  # reopen same path
    got = session_service.get_session(created["id"], store=store2)
    assert got["status"] == "shortlisted"
    assert len(got["options"]) == 1
    assert got["options"][0]["kind"] == "train"
