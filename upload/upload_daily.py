"""Upload the day's ready videos to YouTube as Shorts.

    python -m upload.upload_daily                    # today, config's limit
    python -m upload.upload_daily --date 2026-09-01 --limit 5
    python -m upload.upload_daily --dry-run          # print, upload nothing

Each upload's description carries the Commons attribution string for that
person's photo.  That is how CC BY / CC BY-SA compliance is met, since nothing
appears on the video itself -- so the description is assembled from a template
that *must* contain {attribution}, and the check is enforced in code.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

from common.config import Config, ConfigError, load_config
from common.db import UPLOAD_FAILED, Database
from common.ordinals import ordinal
from common.review_log import UPLOAD_FAILED as REVIEW_UPLOAD_FAILED
from common.review_log import ReviewLog
from upload.youtube_auth import AuthError, _import_google, build_service

log = logging.getLogger("upload")

TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 5000
TAG_LIMIT = 500

# Retrying these is pointless until the quota window resets (midnight Pacific).
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded",
                 "rateLimitExceeded", "userRateLimitExceeded"}
RETRIABLE_STATUS = {500, 502, 503, 504}


class UploadError(RuntimeError):
    pass


# --- metadata --------------------------------------------------------------

def _category_tag(category: str) -> str:
    return "".join(ch for ch in category.title() if ch.isalnum()) or "Birthday"


def build_metadata(row, cfg: Config) -> dict:
    """Title/description/tags for one person.

    Raises ConfigError if the description template omits {attribution}: dropping
    the credit would breach the CC BY / CC BY-SA terms the photo was taken under.
    """
    template = cfg.youtube.description_template or ""
    if "{attribution}" not in template:
        raise ConfigError(
            "youtube.description_template must contain {attribution}.\n"
            "The rendered video carries no on-screen credit, so the description is "
            "the only place the CC BY / CC BY-SA attribution requirement is met."
        )
    attribution = (row["image_attribution"] or "").strip()
    if not attribution:
        raise UploadError(
            f"{row['full_name']}: no stored attribution string; refusing to upload."
        )

    fields = {
        "full_name": row["full_name"],
        "age_turning": row["age_turning"],
        # The graphic is set in caps; a YouTube title is not, so offer both.
        "ordinal_age": ordinal(int(row["age_turning"])).lower(),
        "ordinal_age_caps": ordinal(int(row["age_turning"])),
        "birth_year": row["birth_year"],
        "birth_date": row["birth_date"],
        "category": row["category"],
        "category_tag": _category_tag(row["category"] or ""),
        "birthday": row["birthday"],
        "license": row["image_license"] or "",
        "attribution": attribution,
    }
    try:
        title = cfg.youtube.title_template.format(**fields)[:TITLE_LIMIT]
        description = template.format(**fields)
    except KeyError as exc:
        raise ConfigError(
            f"unknown placeholder {exc} in a youtube template. "
            f"Available: {', '.join(sorted(fields))}."
        ) from exc
    if len(description) > DESCRIPTION_LIMIT:
        # Trim the body, never the credit.
        keep = f"\n\n{attribution}"
        description = description[: DESCRIPTION_LIMIT - len(keep)].rstrip() + keep
    if attribution not in description:
        raise UploadError(f"{row['full_name']}: attribution missing from description")

    tags, used = [], 0
    for tag in cfg.youtube.tags:
        if used + len(tag) + 1 > TAG_LIMIT:
            break
        tags.append(tag)
        used += len(tag) + 1

    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": cfg.youtube.category_id,
        },
        "status": {
            "privacyStatus": cfg.youtube.privacy_status,
            "selfDeclaredMadeForKids": cfg.youtube.made_for_kids,
        },
    }


# --- uploading -------------------------------------------------------------

class Uploader:
    def __init__(self, cfg: Config, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.review = ReviewLog(cfg.paths.review_log)
        self._service = None
        self._google = None
        if not dry_run:
            # Fail on a missing dependency now rather than mid-upload; a dry
            # run needs neither the libraries nor any credentials.
            self._load_google()

    def _load_google(self):
        if self._google is None:
            _, _, _, http_error, media_upload = _import_google()
            self._google = (http_error, media_upload)
        return self._google

    @property
    def HttpError(self):
        return self._load_google()[0]

    @property
    def MediaFileUpload(self):
        return self._load_google()[1]

    @property
    def service(self):
        if self._service is None:
            self._service = build_service(self.cfg)
        return self._service

    def upload(self, row) -> str:
        if not row["video_path"]:
            raise NoVideoYet(
                f"{row['full_name']}: rendered as a still only. Run "
                "`python -m render.generate` with video enabled (and an audio "
                "track configured) before uploading."
            )
        media_path = Path(row["video_path"])
        if not media_path.is_file():
            raise UploadError(f"media file missing: {media_path}")
        body = build_metadata(row, self.cfg)

        if self.dry_run:
            log.info("[dry-run] would upload %s as %r (%s)",
                     media_path.name, body["snippet"]["title"],
                     body["status"]["privacyStatus"])
            return "dry-run"

        media = self.MediaFileUpload(
            str(media_path), mimetype="video/mp4", chunksize=-1, resumable=True
        )
        request = self.service.videos().insert(
            part="snippet,status", body=body, media_body=media
        )
        return self._execute_with_retries(request, row["full_name"])

    def _execute_with_retries(self, request, label: str) -> str:
        delay = self.cfg.youtube.retry_base_delay
        for attempt in range(1, self.cfg.youtube.max_retries + 1):
            try:
                response = request.execute()
                video_id = response.get("id")
                if not video_id:
                    raise UploadError(f"{label}: upload returned no video id")
                return video_id
            except self.HttpError as exc:
                status = getattr(exc.resp, "status", None)
                reason = _http_error_reason(exc)
                if reason in QUOTA_REASONS:
                    raise QuotaExceeded(
                        f"{label}: YouTube API quota exhausted ({reason}). "
                        "The quota window resets at midnight US/Pacific; "
                        "reduce youtube.uploads_per_day or try again tomorrow."
                    ) from exc
                if status in RETRIABLE_STATUS and attempt < self.cfg.youtube.max_retries:
                    wait = delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                    log.warning("%s: HTTP %s, retrying in %.1fs (%d/%d)",
                                label, status, wait, attempt, self.cfg.youtube.max_retries)
                    time.sleep(wait)
                    continue
                raise UploadError(f"{label}: upload failed (HTTP {status}, {reason}): {exc}") from exc
            except (ConnectionError, TimeoutError, OSError) as exc:
                if attempt >= self.cfg.youtube.max_retries:
                    raise UploadError(f"{label}: upload failed after retries: {exc}") from exc
                wait = delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                log.warning("%s: %s, retrying in %.1fs", label, exc, wait)
                time.sleep(wait)
        raise UploadError(f"{label}: exhausted retries")

    def run(self, day: str, limit: int) -> tuple[int, int]:
        posted = failed = skipped = 0
        with Database(self.cfg.paths.database) as db:
            rows = db.uploadable(day, limit)
            if not rows:
                log.info("nothing ready to upload for %s", day)
                return 0, 0
            log.info("%d video(s) queued for %s (privacy: %s)",
                     len(rows), day, self.cfg.youtube.privacy_status)
            for row in rows:
                try:
                    video_id = self.upload(row)
                except QuotaExceeded as exc:
                    log.error("%s -- stopping for today", exc)
                    self.review.record(
                        REVIEW_UPLOAD_FAILED, name=row["full_name"],
                        wikidata_id=row["wikidata_id"], target_date=day, detail=str(exc),
                    )
                    break
                except NoVideoYet as exc:
                    # Leave the row Pending: it becomes uploadable as soon as
                    # the video is rendered, and a still is not a failure.
                    log.warning("skipping %s -- %s", row["full_name"], exc)
                    skipped += 1
                    continue
                except (UploadError, ConfigError, AuthError) as exc:
                    log.error("upload failed for %s: %s", row["full_name"], exc)
                    failed += 1
                    if not self.dry_run:
                        db.update(row["id"], upload_status=UPLOAD_FAILED)
                        db.append_note(row["id"], f"upload failed: {exc}")
                    self.review.record(
                        REVIEW_UPLOAD_FAILED, name=row["full_name"],
                        wikidata_id=row["wikidata_id"], target_date=day, detail=str(exc),
                    )
                    continue
                posted += 1
                if not self.dry_run:
                    db.mark_posted(row["id"], video_id)
                    log.info("posted %s -> https://youtu.be/%s", row["full_name"], video_id)
        if skipped:
            log.info("%d row(s) skipped: no video rendered yet", skipped)
        return posted, failed


class QuotaExceeded(UploadError):
    """The day's API quota is gone; retrying before it resets is pointless."""


class NoVideoYet(UploadError):
    """The row is rendered as a still but has no MP4 to upload."""


def _http_error_reason(exc) -> str:
    try:
        details = exc.error_details or []
        if details and isinstance(details, list):
            first = details[0]
            if isinstance(first, dict):
                return str(first.get("reason", ""))
    except AttributeError:
        pass
    text = str(exc)
    for reason in QUOTA_REASONS:
        if reason in text:
            return reason
    return ""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Upload the day's ready birthday videos.")
    p.add_argument("--config", default=None)
    p.add_argument("--date", default=None, help="date to upload (default: today)")
    p.add_argument("--limit", type=int, default=None, help="override uploads_per_day")
    p.add_argument("--privacy", choices=["private", "unlisted", "public"], default=None)
    p.add_argument("--dry-run", action="store_true", help="print what would be uploaded")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S",
    )
    cfg = load_config(args.config)
    if args.privacy:
        cfg = replace(cfg, youtube=replace(cfg.youtube, privacy_status=args.privacy))

    day = args.date or date.today().isoformat()
    limit = args.limit or cfg.youtube.uploads_per_day
    posted, failed = Uploader(cfg, dry_run=args.dry_run).run(day, limit)
    log.info("uploaded %d, failed %d", posted, failed)
    return 1 if failed and not posted else 0


if __name__ == "__main__":
    sys.exit(main())
