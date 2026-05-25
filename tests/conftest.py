"""Pytest fixtures shared across the suite.

Keeps `open_default_store()` and other settings-derived paths sandboxed to a
per-test tmp dir so tests never touch the developer's real
``data/kelly_history.sqlite`` and don't leak state between tests.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_kelly_data_dir(tmp_path, monkeypatch) -> None:
    """Redirect ``KELLY_DATA_DIR`` to a per-test tmp dir for every test."""
    monkeypatch.setenv("KELLY_DATA_DIR", str(tmp_path))
