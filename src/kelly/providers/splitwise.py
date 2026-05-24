"""Splitwise integration — log shared trip expenses + read group balances.

Unlike the search providers (Airbnb, Eurostar, LiteAPI), Splitwise is a
write-target: Kelly creates expenses on the user's behalf and reads back
balances to drive the settle-up flow. REST API at
https://secure.splitwise.com/api/v3.0, authenticated by a personal API key
(self-issued at https://secure.splitwise.com/apps).

The official ``splitwise`` PyPI SDK exists but pulls OAuth1 deps for a 4-call
surface; we roll httpx directly. Same lazy-env pattern as the other providers
so the module loads cleanly when ``SPLITWISE_API_KEY`` is absent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://secure.splitwise.com/api/v3.0"


@dataclass
class SplitwiseUser:
    id: int
    first_name: str | None
    last_name: str | None
    email: str | None

    @property
    def display_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p) or (
            self.email or f"user_{self.id}"
        )


@dataclass
class SplitwiseGroup:
    id: int
    name: str
    members: list[SplitwiseUser] = field(default_factory=list)


@dataclass
class SplitwiseExpense:
    id: int
    description: str
    cost: Decimal
    currency_code: str
    group_id: int | None
    date: str | None
    raw: dict[str, Any] = field(default_factory=dict)


class SplitwiseError(RuntimeError):
    pass


def _client(
    api_key: str | None = None, base_url: str | None = None, timeout: float = 30.0
) -> httpx.Client:
    key = api_key or os.environ.get("SPLITWISE_API_KEY")
    if not key:
        raise SplitwiseError(
            "SPLITWISE_API_KEY not set — add it to .env to enable Splitwise sync"
        )
    return httpx.Client(
        base_url=base_url or os.environ.get("SPLITWISE_BASE_URL", DEFAULT_BASE_URL),
        headers={
            "Authorization": f"Bearer {key}",
            "accept": "application/json",
            "content-type": "application/json",
        },
        timeout=timeout,
    )


def _user_from_dict(d: dict[str, Any]) -> SplitwiseUser:
    return SplitwiseUser(
        id=int(d["id"]),
        first_name=d.get("first_name"),
        last_name=d.get("last_name"),
        email=d.get("email"),
    )


def get_current_user(client: httpx.Client) -> SplitwiseUser:
    r = client.get("/get_current_user")
    r.raise_for_status()
    return _user_from_dict(r.json()["user"])


def list_groups(client: httpx.Client) -> list[SplitwiseGroup]:
    r = client.get("/get_groups")
    r.raise_for_status()
    out: list[SplitwiseGroup] = []
    for g in r.json().get("groups") or []:
        members = [_user_from_dict(m) for m in (g.get("members") or [])]
        out.append(SplitwiseGroup(id=int(g["id"]), name=g.get("name") or "", members=members))
    return out


def find_group_by_name(client: httpx.Client, name: str) -> SplitwiseGroup | None:
    """Case-insensitive substring match. Useful when the user remembers the
    group name fuzzy ("family-london" matches "Family-London2026")."""
    needle = name.strip().lower()
    for g in list_groups(client):
        if needle in (g.name or "").lower():
            return g
    return None


def get_group(client: httpx.Client, group_id: int) -> dict[str, Any]:
    """Return the raw ``/get_group/{id}`` payload — members include per-currency
    balances and the response includes the simplified-debt graph. The MCP tool
    layer relativizes against the current user; this stays close to the wire."""
    r = client.get(f"/get_group/{group_id}")
    r.raise_for_status()
    return dict(r.json().get("group") or {})


def get_group_balances(client: httpx.Client, group_id: int) -> dict[str, Any]:
    """Return ``{members: [...], simplified_debts: [...]}`` for *group_id*.

    Members carry their per-currency balance:
        [{"id", "name", "email", "balance": [{"currency_code", "amount"}, ...]}]
    Positive *amount* = others owe this member; negative = this member owes.

    *simplified_debts* is Splitwise's minimal transfer set:
        [{"from": user_id, "to": user_id, "amount": "609.57", "currency_code": "GBP"}, ...]

    Both views are returned because they're useful for different prompts —
    "who owes me?" vs "what's the settle-up plan?".
    """
    g = get_group(client, group_id)
    members: list[dict[str, Any]] = []
    for m in g.get("members") or []:
        u = _user_from_dict(m)
        members.append(
            {
                "id": u.id,
                "name": u.display_name,
                "email": u.email,
                "balance": list(m.get("balance") or []),
            }
        )
    return {
        "group_id": int(g.get("id") or group_id),
        "name": g.get("name") or "",
        "members": members,
        "simplified_debts": [dict(d) for d in (g.get("simplified_debts") or [])],
    }


def create_expense(
    client: httpx.Client,
    *,
    group_id: int,
    description: str,
    cost: Decimal | float | str,
    currency_code: str,
    paid_by_user_id: int,
    shares: dict[int, Decimal | float | str],
    details: str | None = None,
    expense_date: str | None = None,
    category_id: int | None = None,
) -> SplitwiseExpense:
    """Create a shared expense.

    *shares* maps user_id → owed_share. The payer's ``paid_share`` is set to
    the full cost, everyone else's to 0. Sum of owed_share must equal cost
    (Splitwise enforces this — off-by-a-penny errors come back as 400).
    """
    cost_d = Decimal(str(cost))
    payload: dict[str, Any] = {
        "cost": f"{cost_d:.2f}",
        "currency_code": currency_code,
        "description": description,
        "group_id": group_id,
    }
    if details:
        payload["details"] = details
    if expense_date:
        payload["date"] = expense_date
    if category_id is not None:
        payload["category_id"] = category_id

    # Splitwise expects each user as users__N__field — flattened keys, not array.
    for i, (uid, owed) in enumerate(shares.items()):
        paid = cost_d if uid == paid_by_user_id else Decimal("0")
        owed_d = Decimal(str(owed))
        payload[f"users__{i}__user_id"] = uid
        payload[f"users__{i}__paid_share"] = f"{paid:.2f}"
        payload[f"users__{i}__owed_share"] = f"{owed_d:.2f}"

    r = client.post("/create_expense", json=payload)
    r.raise_for_status()
    body = r.json()
    errors = body.get("errors") or {}
    if errors and any(errors.values()):
        raise SplitwiseError(f"Splitwise rejected expense: {errors}")
    expenses = body.get("expenses") or []
    if not expenses:
        raise SplitwiseError(f"Splitwise returned no expense object: {body}")
    e = expenses[0]
    return SplitwiseExpense(
        id=int(e["id"]),
        description=e.get("description") or "",
        cost=Decimal(str(e.get("cost") or "0")),
        currency_code=e.get("currency_code") or currency_code,
        group_id=(int(e["group_id"]) if e.get("group_id") else None),
        date=e.get("date"),
        raw=e,
    )


def equal_couple_shares(
    cost: Decimal | float | str,
    user_ids: list[int],
) -> dict[int, Decimal]:
    """Split *cost* equally across *user_ids*, fixing any 1-cent rounding drift
    by pushing the remainder onto the first user.

    Splitwise validates ``sum(owed_share) == cost`` — naïve `cost / N`
    rounding breaks that for amounts like £1219.14 / 2 = £609.57 (clean here
    but not always). This helper guarantees the constraint holds.
    """
    cost_d = Decimal(str(cost)).quantize(Decimal("0.01"))
    n = len(user_ids)
    if n <= 0:
        return {}
    base = (cost_d / n).quantize(Decimal("0.01"))
    shares = {uid: base for uid in user_ids}
    drift = cost_d - base * n
    if drift != 0:
        shares[user_ids[0]] = base + drift
    return shares
