"""Temporary public hosting on Cloudflare R2, for Instagram's fetch-by-URL flow.

Instagram's Graph API does not accept a file upload for Reels -- it takes a
public `video_url` and fetches the file itself. R2 exists in this pipeline
purely to give it something to fetch: a video lands here right before a post,
Instagram downloads it, and (by default) it is deleted immediately after the
post succeeds. Nothing is meant to live here for long, which is also why the
free tier comfortably covers this project's whole traffic forever.

R2 speaks the S3 API, so this is a thin wrapper around boto3's S3 client
pointed at R2's endpoint -- no R2-specific SDK exists or is needed.
"""

from __future__ import annotations

import logging
import mimetypes
import time
import uuid
from pathlib import Path

from common.config import R2Cfg

log = logging.getLogger(__name__)


class R2Error(RuntimeError):
    pass


def _import_boto3():
    try:
        import boto3
        from botocore.config import Config as BotoConfig
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise R2Error(
            "boto3 is required for R2 uploads. Install it with:\n"
            "    pip install boto3"
        ) from exc
    return boto3, BotoConfig, (BotoCoreError, ClientError)


class R2Client:
    def __init__(self, cfg: R2Cfg) -> None:
        if not cfg.configured():
            raise R2Error(
                "r2 is not configured. Set r2.account_id, r2.access_key_id, "
                "r2.secret_access_key, r2.bucket and r2.public_url_base in "
                "config.yaml (credentials via .env -- see README)."
            )
        self.cfg = cfg
        boto3, BotoConfig, errors = _import_boto3()
        self._errors = errors
        self._client = boto3.client(
            "s3",
            endpoint_url=cfg.endpoint_url,
            aws_access_key_id=cfg.access_key_id,
            aws_secret_access_key=cfg.secret_access_key,
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}),
            region_name="auto",
        )

    # -- object naming --------------------------------------------------------
    @staticmethod
    def object_key(local_path: str | Path) -> str:
        """A short-lived, collision-proof key: a video never needs a stable name."""
        suffix = Path(local_path).suffix or ".mp4"
        return f"reels/{uuid.uuid4().hex}{suffix}"

    def public_url(self, key: str) -> str:
        return f"{self.cfg.public_url_base}/{key}"

    # -- lifecycle: upload just before a post, delete just after --------------
    def upload(self, local_path: str | Path, key: str | None = None) -> str:
        """Upload a file and return its public URL."""
        local_path = Path(local_path)
        if not local_path.is_file():
            raise R2Error(f"file not found: {local_path}")
        key = key or self.object_key(local_path)
        content_type = mimetypes.guess_type(local_path.name)[0] or "video/mp4"
        try:
            self._client.upload_file(
                str(local_path), self.cfg.bucket, key,
                ExtraArgs={"ContentType": content_type},
            )
        except self._errors as exc:
            raise R2Error(f"R2 upload failed for {local_path.name}: {exc}") from exc
        return self.public_url(key)

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.cfg.bucket, Key=key)
        except self._errors as exc:
            # Not fatal: an orphaned object just sits in a bucket well under
            # its free-tier ceiling. Log it and move on.
            log.warning("R2 cleanup failed for %s: %s", key, exc)

    def wait_until_fetchable(self, url: str, session, timeout: float = 20.0) -> bool:
        """Poll the public URL until it 200s.

        R2 writes are read-after-write consistent, so this is normally
        immediate -- it exists as a safety margin before handing the URL to
        Instagram, whose fetch failure is opaque and costly to debug blind.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = session.get(url, stream=True)
                if resp.ok:
                    resp.close()
                    return True
            except Exception as exc:  # noqa: BLE001 - purely a readiness probe
                log.debug("readiness probe failed for %s: %s", url, exc)
            time.sleep(0.5)
        return False
