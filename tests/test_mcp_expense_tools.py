"""Tests for kelly_log_expense + kelly_expense_balances MCP tools.

The tools wire several functions from kelly.providers.splitwise — we patch each
at the point of use (kelly.mcp_server) rather than at source, so the test
doesn't depend on httpx internals and stays small."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from kelly.mcp_server import kelly_expense_balances, kelly_log_expense
from kelly.providers.splitwise import SplitwiseError, SplitwiseExpense, SplitwiseUser


def _client_context_manager(client_mock: MagicMock) -> MagicMock:
    """Wrap *client_mock* as a context manager (httpx.Client supports `with`)."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=client_mock)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


_GROUP_ID = 12345
_GROUP_NAME = "Test Group"
_TRIP_PREFIX_VAL = "[test-trip] "


@pytest.fixture(autouse=True)
def _splitwise_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KELLY_SPLITWISE_GROUP_ID", str(_GROUP_ID))
    monkeypatch.setenv("KELLY_SPLITWISE_GROUP_NAME", _GROUP_NAME)
    monkeypatch.setenv("KELLY_TRIP_PREFIX", _TRIP_PREFIX_VAL.rstrip())


@patch("kelly.mcp_server.create_expense")
@patch("kelly.mcp_server.get_group")
@patch("kelly.mcp_server.get_current_user")
@patch("kelly.mcp_server.splitwise_client")
def test_log_expense_builds_correct_payload(
    mock_client_factory, mock_get_user, mock_get_group, mock_create
) -> None:
    mock_client_factory.return_value = _client_context_manager(MagicMock())
    mock_get_user.return_value = SplitwiseUser(
        id=111, first_name="Alice", last_name="Doe", email="alice@example.com"
    )
    mock_get_group.return_value = {
        "id": _GROUP_ID,
        "members": [{"id": 111}, {"id": 222}],
    }
    mock_create.return_value = SplitwiseExpense(
        id=999,
        description=f"{_TRIP_PREFIX_VAL}Dinner",
        cost=Decimal("25.00"),
        currency_code="GBP",
        group_id=_GROUP_ID,
        date=None,
    )

    raw = kelly_log_expense(description="Dinner at the bistro", amount="25.00")
    data = json.loads(raw)

    assert data["expense_id"] == 999
    assert data["group"] == _GROUP_NAME
    assert data["paid_by"] == "Alice Doe"
    # Equal split between 2 members: £12.50 each
    assert data["shares"]["111"] == "12.50"
    assert data["shares"]["222"] == "12.50"

    kwargs = mock_create.call_args.kwargs
    assert kwargs["group_id"] == _GROUP_ID
    assert kwargs["description"] == f"{_TRIP_PREFIX_VAL}Dinner at the bistro"
    assert kwargs["paid_by_user_id"] == 111
    assert kwargs["cost"] == "25.00"
    assert kwargs["currency_code"] == "GBP"


@patch("kelly.mcp_server.splitwise_client")
def test_log_expense_returns_error_without_api_key(mock_client_factory) -> None:
    mock_client_factory.side_effect = SplitwiseError("SPLITWISE_API_KEY not set")
    raw = kelly_log_expense(description="Dinner", amount="25.00")
    assert "SPLITWISE_API_KEY" in json.loads(raw)["error"]


def test_log_expense_rejects_paid_by_someone_else() -> None:
    raw = kelly_log_expense(description="Dinner", amount="25.00", paid_by_me=False)
    err = json.loads(raw)["error"]
    assert "paid_by_me=False" in err


def test_log_expense_requires_group_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KELLY_SPLITWISE_GROUP_ID", raising=False)
    raw = kelly_log_expense(description="Dinner", amount="25.00")
    assert "KELLY_SPLITWISE_GROUP_ID" in json.loads(raw)["error"]


@patch("kelly.mcp_server.get_group_balances")
@patch("kelly.mcp_server.get_current_user")
@patch("kelly.mcp_server.splitwise_client")
def test_balances_relativizes_perspective(
    mock_client_factory, mock_get_user, mock_balances
) -> None:
    mock_client_factory.return_value = _client_context_manager(MagicMock())
    mock_get_user.return_value = SplitwiseUser(
        id=111, first_name="Alice", last_name="Doe", email="alice@example.com"
    )
    mock_balances.return_value = {
        "group_id": _GROUP_ID,
        "name": _GROUP_NAME,
        "members": [
            {
                "id": 111,
                "name": "Alice Doe",
                "email": "alice@example.com",
                "balance": [{"currency_code": "GBP", "amount": "100.00"}],
            },
            {
                "id": 222,
                "name": "Bob Roe",
                "email": "bob@example.com",
                "balance": [{"currency_code": "GBP", "amount": "-100.00"}],
            },
        ],
        "simplified_debts": [{"from": 222, "to": 111, "amount": "100.00", "currency_code": "GBP"}],
    }

    raw = kelly_expense_balances()
    data = json.loads(raw)

    assert data["group"] == _GROUP_NAME
    assert data["me"] == "Alice Doe"
    assert data["your_perspective"] == [
        {"direction": "owed_to_you", "from": "Bob Roe", "amount": "100.00", "currency": "GBP"}
    ]


@patch("kelly.mcp_server.splitwise_client")
def test_balances_returns_error_without_api_key(mock_client_factory) -> None:
    mock_client_factory.side_effect = SplitwiseError("SPLITWISE_API_KEY not set")
    raw = kelly_expense_balances()
    assert "SPLITWISE_API_KEY" in json.loads(raw)["error"]


def test_balances_requires_group_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KELLY_SPLITWISE_GROUP_ID", raising=False)
    raw = kelly_expense_balances()
    assert "KELLY_SPLITWISE_GROUP_ID" in json.loads(raw)["error"]
