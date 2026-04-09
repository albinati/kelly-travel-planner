"""Shared httpx client for RapidAPI hosts (X-RapidAPI-Key / X-RapidAPI-Host)."""

from __future__ import annotations

from typing import Any

import httpx

from kelly import settings


class RapidAPIClient:
    """Thin wrapper: injects RapidAPI headers and normalizes errors."""

    def __init__(
        self,
        *,
        host: str,
        base_url: str,
        api_key: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._host = host
        self._base = base_url.rstrip("/")
        self._key = api_key or settings.rapidapi_key() or ""
        self._timeout = timeout_s

    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self._key,
            "X-RapidAPI-Host": self._host,
        }

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path if path.startswith('/') else '/' + path}"
        with httpx.Client(timeout=self._timeout) as client:
            r = client.get(url, headers=self._headers(), params=params or {})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            return {"data": data}
        return data

    def post_json(
        self,
        path: str,
        *,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self._base}{path if path.startswith('/') else '/' + path}"
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(url, headers={**self._headers(), "Content-Type": "application/json"}, json=body)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            return {"data": data}
        return data
