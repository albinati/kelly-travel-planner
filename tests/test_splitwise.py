from decimal import Decimal
from unittest.mock import MagicMock

import httpx

from kelly.providers.splitwise import (
    SplitwiseError,
    SplitwiseExpense,
    create_expense,
    equal_couple_shares,
    find_group_by_name,
    get_current_user,
    list_groups,
)


def test_equal_couple_shares_clean_split() -> None:
    s = equal_couple_shares(Decimal("1219.14"), [1, 2])
    assert s == {1: Decimal("609.57"), 2: Decimal("609.57")}
    assert sum(s.values()) == Decimal("1219.14")


def test_equal_couple_shares_handles_penny_drift() -> None:
    # £100.00 / 3 = £33.333... which quantizes to £33.33; sum = £99.99.
    # The helper pushes the £0.01 drift onto the first user so Splitwise
    # accepts the payload (it validates sum == cost exactly).
    s = equal_couple_shares(Decimal("100.00"), [11, 22, 33])
    assert sum(s.values()) == Decimal("100.00")
    assert s[11] == Decimal("33.34")
    assert s[22] == Decimal("33.33")
    assert s[33] == Decimal("33.33")


def test_equal_couple_shares_empty_users() -> None:
    assert equal_couple_shares(Decimal("100"), []) == {}


def _mock_client_responding(payload: dict) -> MagicMock:
    """Return an httpx.Client-like mock whose .get/.post return *payload*."""
    client = MagicMock(spec=httpx.Client)
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    client.get.return_value = resp
    client.post.return_value = resp
    return client


def test_get_current_user_parses_response() -> None:
    client = _mock_client_responding(
        {
            "user": {
                "id": 42,
                "first_name": "Alice",
                "last_name": "Test",
                "email": "alice@example.com",
            }
        }
    )
    me = get_current_user(client)
    assert me.id == 42
    assert me.display_name == "Alice Test"
    assert me.email == "alice@example.com"


def test_list_groups_includes_members() -> None:
    client = _mock_client_responding(
        {
            "groups": [
                {
                    "id": 100,
                    "name": "Test Group",
                    "members": [
                        {"id": 1, "first_name": "Alice", "email": "alice@x.com"},
                        {"id": 2, "first_name": "Bob", "email": "bob@x.com"},
                    ],
                }
            ]
        }
    )
    groups = list_groups(client)
    assert len(groups) == 1
    g = groups[0]
    assert g.id == 100
    assert g.name == "Test Group"
    assert [m.id for m in g.members] == [1, 2]


def test_find_group_by_name_substring_case_insensitive() -> None:
    client = _mock_client_responding(
        {
            "groups": [
                {"id": 1, "name": "Test Group", "members": []},
                {"id": 2, "name": "Lisbon-Holiday", "members": []},
            ]
        }
    )
    assert find_group_by_name(client, "test").id == 1
    assert find_group_by_name(client, "LISBON").id == 2
    assert find_group_by_name(client, "berlin") is None


def test_create_expense_flattens_users_payload() -> None:
    client = _mock_client_responding(
        {
            "expenses": [
                {
                    "id": 999,
                    "description": "Airbnb",
                    "cost": "1219.14",
                    "currency_code": "GBP",
                    "group_id": 100,
                    "date": "2026-05-24T00:00:00Z",
                }
            ],
            "errors": {},
        }
    )
    exp = create_expense(
        client,
        group_id=100,
        description="Airbnb",
        cost=Decimal("1219.14"),
        currency_code="GBP",
        paid_by_user_id=1,
        shares={1: Decimal("609.57"), 2: Decimal("609.57")},
    )
    assert isinstance(exp, SplitwiseExpense)
    assert exp.id == 999
    assert exp.cost == Decimal("1219.14")

    # Verify the wire payload — Splitwise's flat users__N__field schema is
    # easy to break silently.
    sent = client.post.call_args.kwargs["json"]
    assert sent["cost"] == "1219.14"
    assert sent["group_id"] == 100
    assert sent["users__0__user_id"] == 1
    assert sent["users__0__paid_share"] == "1219.14"  # payer gets full
    assert sent["users__0__owed_share"] == "609.57"
    assert sent["users__1__user_id"] == 2
    assert sent["users__1__paid_share"] == "0.00"
    assert sent["users__1__owed_share"] == "609.57"


def test_create_expense_raises_on_splitwise_errors() -> None:
    client = _mock_client_responding(
        {"expenses": [], "errors": {"base": ["Sum of shares != cost"]}}
    )
    try:
        create_expense(
            client,
            group_id=100,
            description="bad",
            cost=Decimal("100"),
            currency_code="GBP",
            paid_by_user_id=1,
            shares={1: Decimal("50"), 2: Decimal("40")},  # sums to 90, not 100
        )
    except SplitwiseError as e:
        assert "Sum of shares" in str(e)
    else:
        raise AssertionError("expected SplitwiseError")
