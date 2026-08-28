"""One polite HTTP session for every Wikimedia call.

Wikimedia's APIs ask for a descriptive User-Agent with contact details and
punish bursts with HTTP 429.  The public query service in particular can drop
to "1 request / minute" during an outage, so every request here honours
Retry-After and backs off exponentially rather than hammering.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Mapping

import requests

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class WikimediaError(RuntimeError):
    """A Wikimedia request failed after exhausting retries."""


class PoliteSession:
    def __init__(
        self,
        user_agent: str,
        delay_seconds: float = 1.0,
        max_retries: int = 5,
        timeout: float = 45.0,
    ) -> None:
        if not user_agent or "example.com" in user_agent:
            log.warning(
                "sourcing.user_agent is empty or still the placeholder; Wikimedia "
                "may block requests. Put a real contact address in config.yaml."
            )
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent or "BirthdayContentBot/1.0"})
        self.delay = max(0.0, delay_seconds)
        self.max_retries = max(0, max_retries)
        self.timeout = timeout
        self._last_request = 0.0

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(
                    url, params=params, headers=dict(headers or {}),
                    timeout=self.timeout, stream=stream,
                )
            except requests.RequestException as exc:  # network-level failure
                last_error = exc
                delay = self._backoff(attempt)
                log.warning("%s -> %s; retrying in %.1fs", url, exc, delay)
                time.sleep(delay)
                continue

            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                delay = self._retry_after(resp) or self._backoff(attempt)
                log.warning(
                    "%s -> HTTP %s; retrying in %.1fs (attempt %d/%d)",
                    url, resp.status_code, delay, attempt + 1, self.max_retries,
                )
                resp.close()
                time.sleep(delay)
                continue

            return resp

        raise WikimediaError(f"GET {url} failed after {self.max_retries} retries: {last_error}")

    def get_json(self, url: str, params: Mapping[str, Any] | None = None,
                 headers: Mapping[str, str] | None = None) -> Any:
        resp = self.get(url, params=params, headers=headers)
        if resp.status_code == 404:
            return None
        if not resp.ok:
            raise WikimediaError(f"GET {resp.url} -> HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise WikimediaError(f"GET {resp.url} returned non-JSON: {resp.text[:300]}") from exc

    @staticmethod
    def _retry_after(resp: requests.Response) -> float | None:
        raw = resp.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return min(float(raw), 300.0)
        except ValueError:
            return None

    def _backoff(self, attempt: int) -> float:
        return min(60.0, (2 ** attempt) * 2.0) + random.uniform(0, 1.0)
