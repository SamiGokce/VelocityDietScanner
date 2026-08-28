"""Render pending rows into graphics (and, optionally, videos).

    python -m render.generate                 # everything still pending
    python -m render.generate --date 2026-09-01
    python -m render.generate --no-video --limit 5

Rendering is decoupled from uploading, and one bad row never stops the batch:
a failure marks that row `Failed` with a note, writes a review-log line, and
the loop moves on.  Only rows whose alive status was independently verified
are eligible -- `Needs Review` rows are never rendered.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from math import ceil
from datetime import date
from pathlib import Path

from PIL import Image

from common.config import Config, ConfigError, load_config
from common.dates import date_range
from common.db import (GRAPHIC_FAILED, GRAPHIC_PENDING, GRAPHIC_READY, Database)
from common.http import PoliteSession, WikimediaError
from common.review_log import RENDER_FAILED, ReviewLog
from render.graphic import RenderError, render_layers_to_files, render_to_file
from scripts.commons import upscale_factor
from render.video import VideoError, render_video

log = logging.getLogger("render")

FILEPATH_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}"


def _slug(text: str, limit: int = 48) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:limit] or "person"


def thumbnail_width(source_width: int, source_height: int,
                    target_width: int, target_height: int) -> int:
    """The smallest Commons thumbnail that still covers the target frame.

    Original Commons files are routinely 5-20 MB, so asking for the full file
    every time is wasteful -- but asking for a fixed width throws away detail
    on landscape sources, where height is the binding constraint.  This asks
    for exactly enough, and never more than the original (Special:FilePath does
    not upscale, so a larger request would silently return the original).
    """
    if source_width <= 0 or source_height <= 0:
        return target_width * 2          # unknown source: ask for a generous size
    needed = max(target_width / source_width, target_height / source_height)
    # needed >= target_width/source_width, so the scaled width always covers the
    # target; capping at source_width keeps us from asking for an upscale that
    # Special:FilePath would just answer with the original anyway.
    return min(source_width, ceil(source_width * needed))


def download_url_for(row, target_width: int, target_height: int) -> str:
    file_page = row["image_file_page"] or ""
    if "/File:" in file_page:
        name = file_page.split("/File:", 1)[1]
        width = thumbnail_width(
            int(row["image_width"] or 0), int(row["image_height"] or 0),
            target_width, target_height,
        )
        return FILEPATH_URL.format(name=name) + f"?width={width}"
    return row["image_url"]


class Renderer:
    def __init__(self, cfg: Config, make_video: bool | None = None) -> None:
        self.cfg = cfg
        self.session = PoliteSession(
            cfg.sourcing.user_agent, cfg.sourcing.request_delay_seconds, cfg.sourcing.max_retries
        )
        self.review = ReviewLog(cfg.paths.review_log)
        self.make_video = cfg.video.enabled if make_video is None else make_video
        if self.make_video:
            # Fail before rendering a single frame rather than after 300 of them.
            cfg.require_audio_track()

    # -- image fetching -----------------------------------------------------
    def fetch_photo(self, row) -> Path:
        scale = self.cfg.render.supersample if self.make_video else 1
        url = download_url_for(
            row, self.cfg.render.width * scale, self.cfg.render.height * scale
        )
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        cached = self.cfg.paths.image_cache_dir / f"{row['wikidata_id']}-{digest}"
        if cached.is_file() and cached.stat().st_size > 0:
            return cached

        response = self.session.get(url, stream=True)
        if not response.ok:
            raise WikimediaError(f"image download failed ({response.status_code}): {url}")
        cached.parent.mkdir(parents=True, exist_ok=True)
        with cached.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1 << 16):
                fh.write(chunk)
        if cached.stat().st_size == 0:
            cached.unlink(missing_ok=True)
            raise WikimediaError(f"empty image download: {url}")
        return cached

    # -- one row ------------------------------------------------------------
    def render_row(self, db: Database, row) -> bool:
        person_id = row["id"]
        name = row["full_name"]
        try:
            photo_path = self.fetch_photo(row)
            with Image.open(photo_path) as probe:
                width, height = probe.size
            # Sourcing already applied this rule against the Commons metadata;
            # re-check the bytes we actually downloaded, in case the file was
            # replaced or the row predates the quality gate.
            scale = upscale_factor(width, height, self.cfg.render.width, self.cfg.render.height)
            if scale > self.cfg.sourcing.max_upscale:
                raise RenderError(
                    f"source photo is {width}x{height} and would need a {scale:.2f}x "
                    f"enlargement to fill {self.cfg.render.width}x{self.cfg.render.height} "
                    f"(limit {self.cfg.sourcing.max_upscale:.2f}x)"
                )

            stem = f"{row['birthday']}_{_slug(name)}"
            graphic_path = self.cfg.paths.graphics_dir / f"{stem}.png"
            render_to_file(
                photo_path, graphic_path,
                full_name=name,
                age_turning=int(row["age_turning"]),
                birth_year=int(row["birth_year"]),
                cfg=self.cfg.render,
            )

            video_path = None
            if self.make_video:
                # The photo layer is zoomed; the type layer is held still on
                # top of it, so a Ken Burns push never drags the words about.
                layer_dir = self.cfg.paths.image_cache_dir / "layers"
                background, overlay = render_layers_to_files(
                    photo_path,
                    layer_dir / f"{stem}_photo.png",
                    layer_dir / f"{stem}_type.png",
                    full_name=name,
                    age_turning=int(row["age_turning"]),
                    birth_year=int(row["birth_year"]),
                    cfg=self.cfg.render,
                    scale=self.cfg.render.supersample,
                )
                video_path = self.cfg.paths.videos_dir / f"{stem}.mp4"
                render_video(background, video_path, self.cfg, overlay_path=overlay)

            db.update(
                person_id,
                graphic_status=GRAPHIC_READY,
                graphic_path=str(graphic_path),
                video_path=str(video_path) if video_path else None,
            )
            log.info("rendered %s (%s)", name, row["birthday"])
            return True
        except (RenderError, VideoError, WikimediaError, ConfigError, OSError, ValueError) as exc:
            log.error("render failed for %s: %s", name, exc)
            db.update(person_id, graphic_status=GRAPHIC_FAILED)
            db.append_note(person_id, f"render failed: {exc}")
            self.review.record(
                RENDER_FAILED, name=name, wikidata_id=row["wikidata_id"],
                target_date=row["birthday"], detail=str(exc),
            )
            return False

    # -- the batch ----------------------------------------------------------
    def run(self, start: str | None, end: str | None, limit: int | None,
            retry_failed: bool) -> tuple[int, int]:
        statuses = (GRAPHIC_PENDING, GRAPHIC_FAILED) if retry_failed else (GRAPHIC_PENDING,)
        done = failed = 0
        with Database(self.cfg.paths.database) as db:
            rows = db.pending_renders(start=start, end=end, statuses=statuses, limit=limit)
            log.info("%d row(s) to render", len(rows))
            for row in rows:
                if self.render_row(db, row):
                    done += 1
                else:
                    failed += 1
        return done, failed


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Render graphics/videos for pending people.")
    p.add_argument("--config", default=None)
    p.add_argument("--date", default=None, help="render a single date (YYYY-MM-DD)")
    p.add_argument("--start", default=None, help="first date to render")
    p.add_argument("--days", type=int, default=None, help="number of days from --start")
    p.add_argument("--limit", type=int, default=None, help="stop after N rows")
    p.add_argument("--no-video", action="store_true", help="stills only, no MP4/audio")
    p.add_argument("--video", action="store_true", help="force video even if disabled in config")
    p.add_argument("--retry-failed", action="store_true", help="also retry rows marked Failed")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S",
    )
    cfg = load_config(args.config)
    cfg.ensure_dirs()

    if args.date:
        start = end = args.date
    elif args.start:
        window = date_range(date.fromisoformat(args.start), args.days or cfg.schedule.days)
        start, end = window[0].isoformat(), window[-1].isoformat()
    else:
        start = end = None

    make_video = True if args.video else (False if args.no_video else None)
    renderer = Renderer(cfg, make_video=make_video)
    done, failed = renderer.run(start, end, args.limit, args.retry_failed)
    log.info("rendered %d, failed %d", done, failed)
    if failed:
        log.info("failures are in %s and in the notes column", cfg.paths.review_log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
