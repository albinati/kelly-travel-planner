"""FX conversion using the ECB daily-rates feed with local SQLite cache.

ECB publishes EUR-base rates as a small XML feed (no auth required):
    https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml

We cache every parsed row in ``fx_rates`` and derive cross-rates at lookup
time (X→Y = (1 / EUR_X) * EUR_Y). Conversion failures surface a single
``FxError`` so callers can soft-fail and continue.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx

from kelly.history_store import SqliteHistoryStore, open_default_store

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_SOURCE = "ecb"
# Per-wall-day "we already fetched the feed" marker. Stored under its own
# source so it never shows up in latest_fx_as_of / rate lookups for ECB_SOURCE.
ECB_FETCH_MARKER = "ecb_fetch_marker"

# ECB XML uses these namespaces; ElementTree needs them spelled out.
_NS = {
    "ex": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref",
    "gesmes": "http://www.gesmes.org/xml/2002-08-01",
}


class FxError(Exception):
    """Raised when an FX lookup cannot be satisfied (no cache + no network)."""


def fetch_ecb_rates(*, client: httpx.Client | None = None) -> tuple[str, dict[str, Decimal]]:
    """Fetch the ECB daily feed. Returns ``(as_of_iso_date, {ccy: rate})``.

    EUR is added explicitly with rate ``Decimal("1")`` so downstream
    derivation never special-cases it.
    """
    owns_client = client is None
    c = client or httpx.Client(timeout=10.0)
    try:
        resp = c.get(ECB_DAILY_URL)
        resp.raise_for_status()
        xml_text = resp.text
    finally:
        if owns_client:
            c.close()

    root = ET.fromstring(xml_text)
    # Path: gesmes:Envelope / ex:Cube / ex:Cube[@time] / ex:Cube[@currency,@rate]
    outer = root.find(".//ex:Cube", _NS)
    if outer is None:
        raise FxError("ECB feed has no <Cube> element")
    dated = outer.find("ex:Cube", _NS)
    if dated is None or "time" not in dated.attrib:
        raise FxError("ECB feed has no dated <Cube time=...> child")
    as_of = dated.attrib["time"]
    rates: dict[str, Decimal] = {"EUR": Decimal("1")}
    for cube in dated.findall("ex:Cube", _NS):
        ccy = cube.attrib.get("currency", "").upper()
        rate = cube.attrib.get("rate")
        if ccy and rate is not None:
            rates[ccy] = Decimal(rate)
    return as_of, rates


def _persist_rates(store: SqliteHistoryStore, as_of: str, rates: dict[str, Decimal]) -> None:
    for ccy, rate in rates.items():
        store.record_fx_rate(
            as_of=as_of,
            base_ccy="EUR",
            quote_ccy=ccy,
            rate=float(rate),
            source=ECB_SOURCE,
        )


def _eur_rate_for(store: SqliteHistoryStore, ccy: str, as_of: str) -> Decimal | None:
    """Return the EUR→ccy rate cached for that date, or None."""
    if ccy.upper() == "EUR":
        return Decimal("1")
    raw = store.fetch_fx_rate(as_of=as_of, base_ccy="EUR", quote_ccy=ccy, source=ECB_SOURCE)
    return Decimal(str(raw)) if raw is not None else None


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _ensure_fresh(store: SqliteHistoryStore, *, client: httpx.Client | None = None) -> str:
    """Fetch today's ECB rates if missing; return the latest cached as_of date."""
    today = _today_iso()
    if (
        store.fetch_fx_rate(as_of=today, base_ccy="EUR", quote_ccy="USD", source=ECB_SOURCE)
        is not None
    ):
        return today
    # We may have already fetched the feed today even though its as_of is an
    # earlier business day — ECB only publishes on weekdays, so on weekends/
    # holidays the feed's as_of < today. Without this guard the freshness check
    # above never matches and we refetch on every convert() call. The marker is
    # stored under its own source so it never pollutes latest_fx_as_of / lookups.
    if (
        store.fetch_fx_rate(
            as_of=today, base_ccy="EUR", quote_ccy="EUR", source=ECB_FETCH_MARKER
        )
        is not None
    ):
        cached = store.latest_fx_as_of(source=ECB_SOURCE)
        if cached:
            return cached
    try:
        as_of, rates = fetch_ecb_rates(client=client)
    except (httpx.HTTPError, FxError):
        fallback = store.latest_fx_as_of(source=ECB_SOURCE)
        if fallback:
            return fallback
        raise FxError("ECB feed unavailable and no cached rates — cannot convert") from None
    _persist_rates(store, as_of, rates)
    # Record that we fetched today's feed so same-day calls reuse the cache.
    store.record_fx_rate(
        as_of=today, base_ccy="EUR", quote_ccy="EUR", rate=1.0, source=ECB_FETCH_MARKER
    )
    return as_of


def convert(
    amount: Decimal | float | str,
    from_ccy: str,
    to_ccy: str,
    *,
    as_of: str | date | None = None,
    store: SqliteHistoryStore | None = None,
    client: httpx.Client | None = None,
) -> Decimal:
    """Convert *amount* from *from_ccy* to *to_ccy*.

    ``as_of`` defaults to today (UTC). If today's rates aren't cached and the
    network is reachable, the ECB feed is fetched and persisted. If the
    network fails, the most recent cached date is used as a fallback;
    ``FxError`` is raised only when no cache exists.
    """
    amt = Decimal(str(amount))
    src = from_ccy.upper()
    dst = to_ccy.upper()
    if src == dst:
        return amt

    s = store or open_default_store()
    target_date = (
        as_of.isoformat() if isinstance(as_of, date) else (as_of or _ensure_fresh(s, client=client))
    )

    # If user passed an explicit as_of and we don't have it cached, try to
    # backfill from the live feed (which always covers "today") and fall
    # back to whatever's cached.
    eur_to_src = _eur_rate_for(s, src, target_date)
    eur_to_dst = _eur_rate_for(s, dst, target_date)
    if eur_to_src is None or eur_to_dst is None:
        target_date = _ensure_fresh(s, client=client)
        eur_to_src = _eur_rate_for(s, src, target_date)
        eur_to_dst = _eur_rate_for(s, dst, target_date)
    if eur_to_src is None or eur_to_dst is None:
        raise FxError(f"no cached EUR rate for {src!r} or {dst!r} on {target_date}")
    return (amt / eur_to_src) * eur_to_dst


def fx_quote(
    from_ccy: str,
    to_ccy: str,
    *,
    as_of: str | date | None = None,
    store: SqliteHistoryStore | None = None,
    client: httpx.Client | None = None,
) -> dict[str, object]:
    """Return the rate (1 unit from_ccy → to_ccy) with metadata, never raises."""
    try:
        rate = convert(Decimal("1"), from_ccy, to_ccy, as_of=as_of, store=store, client=client)
    except FxError as e:
        return {"error": str(e), "from_ccy": from_ccy.upper(), "to_ccy": to_ccy.upper()}
    s = store or open_default_store()
    resolved_as_of = (
        as_of.isoformat()
        if isinstance(as_of, date)
        else (as_of or s.latest_fx_as_of(source=ECB_SOURCE) or "")
    )
    return {
        "from_ccy": from_ccy.upper(),
        "to_ccy": to_ccy.upper(),
        "rate": str(rate),
        "as_of": resolved_as_of,
        "source": ECB_SOURCE,
    }
