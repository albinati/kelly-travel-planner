"""Optional travel-hacking-toolkit JSON data (valuations, transfers, sweet spots)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class ToolkitData:
    """Lazy-loaded reference data from vendor/travel-hacking-toolkit/data."""

    def __init__(self, data_dir: str | Path | None) -> None:
        self.data_dir = Path(data_dir) if data_dir else None
        self._valuations: dict[str, Any] | None = None
        self._transfers: dict[str, Any] | None = None
        self._sweet_spots: list[Any] | None = None

    @property
    def available(self) -> bool:
        return self.data_dir is not None and self.data_dir.is_dir()

    def valuations(self) -> dict[str, Any] | None:
        if not self.available:
            return None
        if self._valuations is None:
            self._valuations = _load_json(self.data_dir / "points-valuations.json")  # type: ignore[union-attr]
        return self._valuations if isinstance(self._valuations, dict) else None

    def transfer_partners(self) -> dict[str, Any] | None:
        if not self.available:
            return None
        if self._transfers is None:
            self._transfers = _load_json(self.data_dir / "transfer-partners.json")  # type: ignore[union-attr]
        return self._transfers if isinstance(self._transfers, dict) else None

    def sweet_spots(self) -> list[Any] | None:
        if not self.available:
            return None
        if self._sweet_spots is None:
            raw = _load_json(self.data_dir / "sweet-spots.json")  # type: ignore[union-attr]
            self._sweet_spots = raw if isinstance(raw, list) else None
        return self._sweet_spots


def worth_summary(toolkit: ToolkitData, program_hint: str | None) -> str | None:
    """One-line pointer if valuations file mentions program (very loose match)."""
    v = toolkit.valuations()
    if not v or not program_hint:
        return None
    key = program_hint.lower().replace(" ", "_")
    # File shape varies; keep generic
    for k, val in v.items():
        if key in str(k).lower():
            return f"Toolkit valuation note for {k!r}: see points-valuations.json"
    return None
