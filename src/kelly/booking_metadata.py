"""Static metadata for booked trip artifacts — locations, timezones, defaults.

The *money* side of bookings lives in the SQLite `bookings` table (via
`history_store.BookingRecord`). This module holds only the immutable
operational metadata that `kelly_booking_event_draft` needs to compose
Google Calendar event specs: addresses, timezones, default times, etc.

When a second trip arc appears, add a new entry keyed by `trip_id`.
"""

from __future__ import annotations

AIRBNB = {
    "trip_id": "paris-disney-2026-08",
    "leg": "airbnb",
    "summary": "Airbnb Paris — UrbanFlat 115 (HMESRSXD98)",
    "location": "218 Rue St Denis, 75002 Paris, France",
    "host": "Urban Flat",
    "check_in_date": "2026-08-18",
    "check_in_time": "16:00",  # "After 16:00" per the receipt
    "check_out_date": "2026-08-21",
    "check_out_time": "11:00",  # "By 11:00" per the receipt
    "confirmation_code": "HMESRSXD98",
    "guests": "up to 10",
    "url": "https://www.airbnb.co.uk/reservation/itinerary?code=HMESRSXD98",
}

EUROSTAR_LEGS = {
    "out": {
        "trip_id": "paris-disney-2026-08",
        "leg": "eurostar_out",
        "summary_fmt": "Eurostar LON→PAR ({depart}–{arrive})",
        "origin": "St Pancras International, London",
        "destination": "Gare du Nord, Paris",
        "date": "2026-08-18",
        "origin_tz": "Europe/London",
        "destination_tz": "Europe/Paris",
        "default_depart": "12:01",
        "default_arrive": "15:30",
    },
    "back": {
        "trip_id": "paris-disney-2026-08",
        "leg": "eurostar_back",
        "summary_fmt": "Eurostar PAR→LON ({depart}–{arrive})",
        "origin": "Gare du Nord, Paris",
        "destination": "St Pancras International, London",
        "date": "2026-08-21",
        "origin_tz": "Europe/Paris",
        "destination_tz": "Europe/London",
        # 20:02→21:30 is the actual booked time (ref TGDJKR) — the earlier
        # 16:30 slot we'd recommended sold out by the time of booking.
        "default_depart": "20:02",
        "default_arrive": "21:30",
    },
}

DISNEY = {
    "trip_id": "paris-disney-2026-08",
    "leg": "disney_tickets",
    "summary": "Disneyland Paris (day visit)",
    "location": "Marne-la-Vallée–Chessy, Disneyland Paris, 77777 Marne-la-Vallée, France",
    "tz": "Europe/Paris",
    "default_date": "2026-08-20",  # Thursday — middle of trip per the user's earlier roteiro
    "default_start_time": "09:00",
    "default_end_time": "22:00",
    "notes": (
        "Take RER A from Châtelet-Les Halles / Gare de Lyon / Nation directly to "
        "Marne-la-Vallée–Chessy (~40min). Park gates open earlier; closing varies."
    ),
}


_CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "BRL": "R$", "CAD": "C$"}


def format_money(amount: float, currency: str) -> str:
    """Format a numeric amount with the right currency symbol/code.

    Falls back to the ISO code if we don't have a symbol mapped — keeps the
    function total without pulling babel as a dependency.
    """
    symbol = _CURRENCY_SYMBOLS.get(currency.upper())
    if symbol:
        return f"{symbol}{amount:,.2f}"
    return f"{amount:,.2f} {currency.upper()}"
