"""Shared cash search result shape (SerpApi + RapidAPI Google Flights)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class CashSearchResult:
    departure_date: date
    origin_iata: str
    destination_iata: str
    cabin: str
    best_offer_id: str | None
    best_total_amount: Decimal | None
    best_total_currency: str | None
    offer_count: int
    error: str | None = None
    return_date: date | None = None
    itinerary_details: list[dict[str, Any]] = field(default_factory=list)
