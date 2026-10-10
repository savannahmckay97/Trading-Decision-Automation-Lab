"""Bounded, dependency-free JSON HTTP client for public market endpoints."""

from __future__ import annotations

from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import time
from threading import Lock
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


class HttpError(RuntimeError):
    pass


class JsonHttpClient:
    ERROR_BODY_BYTES = 1024
    DEFAULT_RATE_LIMIT_SECONDS = 60

    def __init__(
        self,
        timeout_seconds: float = 12.0,
        attempts: int = 3,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if type(attempts) is not int or attempts < 1:
            raise ValueError("attempts must be a positive integer")
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self.opener = opener or urllib.request.urlopen
        self._cooldowns: dict[str, tuple[float, str]] = {}
        self._cooldown_lock = Lock()

    def get(self, base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = f"{base_url}{path}" + (f"?{query}" if query else "")
        parsed = urllib.parse.urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        request = urllib.request.Request(url, headers={"User-Agent": "TradingDecisionAutomationLab/0.1"})
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            self._check_cooldown(origin, path)
            try:
                with self.opener(request, timeout=self.timeout_seconds) as response:
                    payload = json.load(response)
                self._raise_api_error(payload)
                return payload
            except urllib.error.HTTPError as exc:
                detail = self._http_detail(exc)
                if exc.code in {429, 418}:
                    delay = self._retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None)
                    deadline = time.monotonic() + delay
                    with self._cooldown_lock:
                        old = self._cooldowns.get(origin)
                        if old is None or deadline > old[0]:
                            self._cooldowns[origin] = (deadline, detail)
                    # Never sleep for an unbounded provider deadline or hammer a
                    # rate limit. Later polls retry only after the host cooldown.
                    raise HttpError(f"GET {path}: {detail}; host cooldown {delay:g}s") from exc
                if not 500 <= exc.code < 600:
                    raise HttpError(f"GET {path} failed after {attempt + 1} attempts: {detail}") from exc
                last_error = HttpError(detail)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise HttpError(f"GET {path}: malformed JSON response: {exc}") from exc
            # Provider-declared API errors are HttpError and propagate without
            # retry. Only transient HTTP 5xx and transport failures reach here.
            if attempt + 1 < self.attempts:
                time.sleep(2**attempt)
        raise HttpError(f"GET {path} failed after {self.attempts} attempts: {last_error}") from last_error

    def _check_cooldown(self, origin: str, path: str) -> None:
        with self._cooldown_lock:
            cooldown = self._cooldowns.get(origin)
            if cooldown is None:
                return
            remaining = cooldown[0] - time.monotonic()
            if remaining <= 0:
                del self._cooldowns[origin]
                return
        raise HttpError(f"GET {path}: host cooldown {remaining:g}s remaining; {cooldown[1]}")

    @classmethod
    def _http_detail(cls, error: urllib.error.HTTPError) -> str:
        try:
            data = error.read(cls.ERROR_BODY_BYTES + 1)
            body = data[:cls.ERROR_BODY_BYTES].decode("utf-8", errors="replace")
            if len(data) > cls.ERROR_BODY_BYTES:
                body += " [truncated]"
        except (OSError, ValueError) as exc:
            body = f"response body unavailable ({type(exc).__name__})"
        finally:
            error.close()
        return f"HTTP {error.code}: {body}"

    @classmethod
    def _retry_after_seconds(cls, header: str | None) -> float:
        if header is not None and len(header) <= 128:
            try:
                if header.strip().isascii() and header.strip().isdigit():
                    delay = float(int(header.strip()))
                else:
                    date = parsedate_to_datetime(header)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    delay = date.timestamp() - time.time()
                if delay > 0:
                    return delay
            except (TypeError, ValueError, OverflowError):
                pass
        # Missing, invalid or expired rate-limit guidance never causes an
        # immediate retry. This fallback is transport pacing, not a trade rule.
        return float(cls.DEFAULT_RATE_LIMIT_SECONDS)

    @staticmethod
    def _raise_api_error(payload: Any) -> None:
        if isinstance(payload, dict):
            if payload.get("error"):
                raise HttpError(f"provider error: {str(payload['error'])[:1024]}")
            if isinstance(payload.get("code"), int) and payload["code"] < 0:
                raise HttpError(f"provider error: {str(payload)[:1024]}")
