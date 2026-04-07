"""Duffel (cash) flight search via duffel-api."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from duffel_api import Duffel

from kelly.settings import duffel_api_version

if TYPE_CHECKING:
    from duffel_api.models import Offer


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


def _cabin_for_duffel(cabin: str) -> str:
    return cabin.strip().lower().replace(" ", "_")


def search_cash_best(
    access_token: str,
    *,
    origin_iata: str,
    destination_iata: str,
    departure_date: date,
    cabin: str,
    passengers: list[dict[str, str]],
    max_connections: int = 1,
    api_version: str | None = None,
) -> CashSearchResult:
    """Create an offer request with return_offers and return cheapest offer by total_amount."""
    origin_iata = origin_iata.upper().strip()
    destination_iata = destination_iata.upper().strip()
    cabin_d = _cabin_for_duffel(cabin)
    ver = api_version if api_version is not None else duffel_api_version()
    client = Duffel(access_token=access_token, api_version=ver)
    try:
        offer_request = (
            client.offer_requests.create()
            .return_offers()
            .cabin_class(cabin_d)
            .passengers(passengers)
            .max_connections(max_connections)
            .slices(
                [
                    {
                        "origin": origin_iata,
                        "destination": destination_iata,
                        "departure_date": departure_date.isoformat(),
                    }
                ]
            )
            .execute()
        )
    except Exception as e:  # noqa: BLE001 — surface API errors to orchestrator
        return CashSearchResult(
            departure_date=departure_date,
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            cabin=cabin_d,
            best_offer_id=None,
            best_total_amount=None,
            best_total_currency=None,
            offer_count=0,
            error=str(e),
        )

    offers: list[Offer] = list(offer_request.offers or [])
    if not offers:
        return CashSearchResult(
            departure_date=departure_date,
            origin_iata=origin_iata,
            destination_iata=destination_iata,
            cabin=cabin_d,
            best_offer_id=None,
            best_total_amount=None,
            best_total_currency=None,
            offer_count=0,
            error=None,
        )

    def price_key(o: Offer) -> Decimal:
        try:
            return Decimal(o.total_amount)
        except Exception:
            return Decimal("9999999999")

    best = min(offers, key=price_key)
    return CashSearchResult(
        departure_date=departure_date,
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        cabin=cabin_d,
        best_offer_id=best.id,
        best_total_amount=Decimal(best.total_amount),
        best_total_currency=best.total_currency,
        offer_count=len(offers),
        error=None,
    )
