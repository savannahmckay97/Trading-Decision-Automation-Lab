"""Bounded, dependency-free JSON HTTP client."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Callable


class HttpError(RuntimeError):
    pass


class JsonHttpClient:
    def __init__(
        self,
        timeout_seconds: float = 12.0,
        attempts: int = 3,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self.opener = opener or urllib.request.urlopen

    def get(self, base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = f"{base_url}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, headers={"User-Agent": "TradingDecisionAutomationLab/0.1"})
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                with self.opener(request, timeout=self.timeout_seconds) as response:
                    payload = json.load(response)
                self._raise_api_error(payload)
                return payload
            except Exception as exc:  # bounded retry owns transport and provider errors
                last_error = exc
                if attempt + 1 < self.attempts:
                    time.sleep(2**attempt)
        raise HttpError(f"GET {path} failed after {self.attempts} attempts: {last_error}") from last_error

    @staticmethod
    def _raise_api_error(payload: Any) -> None:
        if isinstance(payload, dict):
            if payload.get("error"):
                raise HttpError(f"provider error: {payload['error']}")
            if isinstance(payload.get("code"), int) and payload["code"] < 0:
                raise HttpError(f"provider error: {payload}")
