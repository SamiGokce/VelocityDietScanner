"""Instagram Graph API: publish a Reel from a public video URL.

Posting a Reel is three calls, not one:

  1. POST /{ig-user-id}/media   with video_url + caption -> a container id.
     Instagram fetches the video from that URL in the background; this call
     returns immediately, before the fetch is done.
  2. GET  /{container-id}?fields=status_code   polled until it reports
     FINISHED (or ERROR/EXPIRED).
  3. POST /{ig-user-id}/media_publish   with the container id -> the actual
     post, returning a media id.

There is no "upload a file" call in this API for Reels -- step 1 only takes a
URL, which is the entire reason this pipeline stages videos on R2 first.

This module keeps its own small retry loop rather than reusing
`common.http.PoliteSession`: that class is written specifically for Wikimedia
(its warnings and exception type name Wikimedia, and its rate-limit shape is
tuned to WDQS), and the existing YouTube uploader already sets the precedent
of giving each external API its own retry handling rather than forcing every
integration through one shared client.
"""

from __future__ import annotations

import logging
import random
import time

import requests

from common.config import InstagramCfg

log = logging.getLogger(__name__)

TERMINAL_ERROR_STATUSES = {"ERROR", "EXPIRED"}
RETRY_STATUS = {429, 500, 502, 503, 504}


class InstagramError(RuntimeError):
    pass


class InstagramClient:
    def __init__(self, cfg: InstagramCfg, timeout: float = 30.0) -> None:
        if not cfg.configured():
            raise InstagramError(
                "instagram is not configured. Set instagram.access_token and "
                "instagram.ig_user_id in config.yaml (the token via .env -- see README)."
            )
        self.cfg = cfg
        self.timeout = timeout
        self.session = requests.Session()

    # -- the three-step publish flow ------------------------------------------
    def create_container(self, video_url: str, caption: str) -> str:
        data = self._request(
            "POST", f"/{self.cfg.ig_user_id}/media",
            {"media_type": "REELS", "video_url": video_url, "caption": caption},
            action="creating the media container",
        )
        container_id = data.get("id")
        if not container_id:
            raise InstagramError(f"no container id in response: {data}")
        return container_id

    def wait_until_ready(self, container_id: str) -> None:
        """Poll until Instagram has finished fetching and processing the video."""
        deadline = time.monotonic() + self.cfg.poll_timeout_seconds
        last_status = None
        while time.monotonic() < deadline:
            data = self._request(
                "GET", f"/{container_id}", {"fields": "status_code,status"},
                action="checking container status",
            )
            status = data.get("status_code", "")
            last_status = status
            if status == "FINISHED":
                return
            if status in TERMINAL_ERROR_STATUSES:
                raise InstagramError(
                    f"container {container_id} failed processing: "
                    f"{status} -- {data.get('status', '')}"
                )
            time.sleep(self.cfg.poll_interval_seconds)
        raise InstagramError(
            f"container {container_id} did not finish processing within "
            f"{self.cfg.poll_timeout_seconds:.0f}s (last status: {last_status})"
        )

    def publish(self, container_id: str) -> str:
        data = self._request(
            "POST", f"/{self.cfg.ig_user_id}/media_publish",
            {"creation_id": container_id}, action="publishing the container",
        )
        media_id = data.get("id")
        if not media_id:
            raise InstagramError(f"no media id in publish response: {data}")
        return media_id

    def post_reel(self, video_url: str, caption: str) -> str:
        """The full flow: container -> wait -> publish. Returns the media id."""
        container_id = self.create_container(video_url, caption)
        self.wait_until_ready(container_id)
        return self.publish(container_id)

    # -- HTTP with its own backoff, distinct from PoliteSession's Wikimedia
    #    tuning and error type ---------------------------------------------
    def _request(self, method: str, path: str, params: dict, *, action: str) -> dict:
        url = f"https://graph.facebook.com/{self.cfg.graph_api_version}{path}"
        params = {**params, "access_token": self.cfg.access_token}
        last_error: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                resp = self.session.request(method, url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.cfg.max_retries:
                    break
                delay = self._backoff(attempt)
                log.warning("%s: %s, retrying in %.1fs", action, exc, delay)
                time.sleep(delay)
                continue

            if resp.status_code in RETRY_STATUS and attempt < self.cfg.max_retries:
                delay = self._backoff(attempt)
                log.warning("%s: HTTP %s, retrying in %.1fs (attempt %d/%d)",
                           action, resp.status_code, delay, attempt + 1, self.cfg.max_retries)
                time.sleep(delay)
                continue

            return self._parse(resp, action)

        raise InstagramError(f"{action} failed after {self.cfg.max_retries} retries: {last_error}")

    def _backoff(self, attempt: int) -> float:
        return min(60.0, self.cfg.retry_base_delay * (2 ** attempt)) + random.uniform(0, 1.0)

    @staticmethod
    def _parse(resp: requests.Response, action: str) -> dict:
        try:
            data = resp.json()
        except ValueError as exc:
            raise InstagramError(
                f"non-JSON response while {action} (HTTP {resp.status_code}): "
                f"{resp.text[:300]}"
            ) from exc
        if not resp.ok or "error" in data:
            error = data.get("error", {})
            message = error.get("message", resp.text[:300])
            code = error.get("code", resp.status_code)
            raise InstagramError(f"Instagram API error while {action} ({code}): {message}")
        return data
