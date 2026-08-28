"""Build the 90-day birthday database.

    python -m scripts.fetch_birthdays --days 90
    python -m scripts.fetch_birthdays --date 2026-09-01 --refresh
    python -m scripts.fetch_birthdays --days 7 --no-write     # preview only

For each day in the window:

  1. ask Wikidata who is alive, notable enough and born on that month/day,
  2. rank the shortlist by Wikipedia pageviews (sitelinks as a bounded bonus),
  3. walk the ranking and accept the first N people who have an openly
     licensed Commons photo *and* pass an independent alive check,
  4. write one row per accepted person, and one review-log line for every
     person skipped, flagged or considered-but-not-used.

No graphics are produced here -- that is `render.generate`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

from common.config import Config, load_config
from common.dates import age_turning, date_range
from common.db import (ALIVE_YES, GRAPHIC_NEEDS_REVIEW, GRAPHIC_PENDING,
                       Database, Person)
from common.http import PoliteSession, WikimediaError
from common.review_log import (ALIVE_MISMATCH, ALIVE_UNVERIFIED, BELOW_THRESHOLD,
                               IMAGE_FETCH_FAILED, LOW_RESOLUTION,
                               NO_ENGLISH_ARTICLE, NO_IMAGE_CLAIM,
                               NO_OPEN_LICENSE, NOT_SELECTED, ReviewLog)
from scripts.alive_check import AliveChecker
from scripts.commons import (CommonsClient, ImageTooSmall, LicenseRejected,
                             upscale_factor)
from scripts.pageviews import PageviewsClient, notability_score
from scripts.wikidata import Candidate, WikidataClient, load_curated_list

log = logging.getLogger("fetch")

#: How many of the day's candidates get a pageviews lookup.  Everyone below
#: this cut is ranked on sitelinks alone and logged as below-threshold.
DEFAULT_SHORTLIST = 20


class BirthdayFetcher:
    def __init__(self, config: Config, refresh: bool = False,
                 shortlist_size: int = DEFAULT_SHORTLIST, write: bool = True) -> None:
        self.cfg = config
        self._images: dict = {}
        self.refresh = refresh
        self.shortlist_size = shortlist_size
        self.write = write

        self.session = PoliteSession(
            user_agent=config.sourcing.user_agent,
            delay_seconds=config.sourcing.request_delay_seconds,
            max_retries=config.sourcing.max_retries,
        )
        self.wikidata = WikidataClient(
            self.session, config.sourcing.sparql_endpoint,
            cache_dir=config.paths.image_cache_dir.parent / "sparql",
            detail_pool=config.sourcing.detail_pool,
        )
        self.commons = CommonsClient(
            self.session,
            config.sourcing.allowed_licenses,
            canvas=(config.render.width, config.render.height),
            min_width=config.sourcing.min_image_width,
            min_height=config.sourcing.min_image_height,
            max_upscale=config.sourcing.max_upscale,
        )
        self.pageviews = PageviewsClient(self.session)
        self.alive = AliveChecker(self.session)
        self.review = ReviewLog(config.paths.review_log)
        self.curated = load_curated_list(config.paths.curated_list)
        if self.curated:
            log.info("curated notability list: %d entries", len(self.curated))

    # -- one day ------------------------------------------------------------
    def run_day(self, db: Database | None, day: date) -> list[Person]:
        iso = day.isoformat()
        log.info("=== %s ===", iso)
        try:
            candidates = self.wikidata.candidates_for(
                day,
                min_sitelinks=self.cfg.sourcing.min_sitelinks,
                limit=self.cfg.sourcing.candidate_limit,
                curated_ids=self.curated,
                refresh=self.refresh,
            )
        except WikimediaError as exc:
            log.error("%s: Wikidata query failed, skipping day: %s", iso, exc)
            self.review.record("wikidata_query_failed", target_date=iso, detail=str(exc))
            return []

        if not candidates:
            log.warning("%s: no candidates matched the filters", iso)
            self.review.record("no_candidates_for_day", target_date=iso)
            return []

        shortlist, remainder = self._shortlist(candidates)
        for cand in remainder:
            self.review.record(
                BELOW_THRESHOLD, name=cand.full_name, wikidata_id=cand.wikidata_id,
                target_date=iso, sitelinks=cand.sitelinks,
                detail="outside the day's pageview shortlist",
            )

        for cand in shortlist:
            cand.pageviews = self.pageviews.total_views(
                cand.wikipedia_title, days=self.cfg.sourcing.pageviews_days, end=day,
            )
            cand.notability_score = notability_score(
                cand.pageviews, cand.sitelinks, self.cfg.sourcing.pageviews_days,
            )

        # Curated people are considered first, then everyone else by score.
        ranked = sorted(
            shortlist, key=lambda c: (not c.curated, -c.notability_score, -c.sitelinks)
        )

        # One batched Commons request for the whole shortlist rather than one
        # per person: Wikimedia throttles hard, and most of these photos are
        # checked only to be rejected on licence or resolution.
        self._images = {}
        filenames = [c.image_filename for c in ranked if c.image_filename]
        if filenames:
            try:
                self._images = self.commons.image_info_batch(filenames)
            except WikimediaError as exc:
                log.error("%s: Commons batch lookup failed: %s", iso, exc)
                self._images = {}

        accepted: list[Person] = []
        for cand in ranked:
            if len(accepted) >= self.cfg.schedule.per_day_max:
                self.review.record(
                    NOT_SELECTED, name=cand.full_name, wikidata_id=cand.wikidata_id,
                    target_date=iso, notability_score=cand.notability_score,
                    detail=f"day already filled with {len(accepted)} people",
                )
                continue
            person = self._evaluate(cand, day)
            if person is None:
                continue
            if person.alive_verified == ALIVE_YES:
                accepted.append(person)
            if db is not None and self.write:
                db.upsert_person(person)

        if len(accepted) < self.cfg.schedule.per_day_min:
            log.warning(
                "%s: only %d/%d people accepted -- see the review log",
                iso, len(accepted), self.cfg.schedule.per_day_min,
            )
            self.review.record(
                "day_underfilled", target_date=iso, accepted=len(accepted),
                wanted=self.cfg.schedule.per_day_min,
            )
        else:
            log.info("%s: accepted %d people", iso, len(accepted))
        return accepted

    def _image_for(self, cand: Candidate):
        """The prefetched Commons result, falling back to a single lookup."""
        from scripts.commons import _normalise

        key = _normalise(cand.image_filename or "")
        if key in getattr(self, "_images", {}):
            return self._images[key]
        try:
            return self.commons.image_info(cand.image_filename)
        except (LicenseRejected, ImageTooSmall) as exc:
            return exc
        except (WikimediaError, KeyError):
            return None

    def _shortlist(self, candidates: list[Candidate]) -> tuple[list[Candidate], list[Candidate]]:
        ordered = sorted(candidates, key=lambda c: (not c.curated, -c.sitelinks))
        return ordered[:self.shortlist_size], ordered[self.shortlist_size:]

    # -- one person ---------------------------------------------------------
    def _evaluate(self, cand: Candidate, day: date) -> Person | None:
        """Licence-check the photo and cross-check alive status.

        Returns None when the person cannot be used at all; returns a Person
        with a non-'yes' alive_verified when they need a human to look.
        """
        iso = day.isoformat()
        if self.cfg.sourcing.require_english_article and not cand.wikipedia_title:
            # No article means no pageview ranking and, more importantly, no
            # second source for the alive check -- so they could never be used.
            self.review.record(
                NO_ENGLISH_ARTICLE, name=cand.full_name, wikidata_id=cand.wikidata_id,
                target_date=iso, detail="no English Wikipedia article to verify against",
            )
            return None
        if not cand.image_filename:
            self.review.record(
                NO_IMAGE_CLAIM, name=cand.full_name, wikidata_id=cand.wikidata_id,
                target_date=iso, detail="no P18 image claim on Wikidata",
            )
            return None

        image = self._image_for(cand)
        if isinstance(image, ImageTooSmall):
            self.review.record(
                LOW_RESOLUTION, name=cand.full_name, wikidata_id=cand.wikidata_id,
                target_date=iso, file=cand.image_filename, detail=str(image),
            )
            log.info("%s: %s skipped -- %s", iso, cand.full_name, image)
            return None
        if isinstance(image, LicenseRejected):
            self.review.record(
                NO_OPEN_LICENSE, name=cand.full_name, wikidata_id=cand.wikidata_id,
                target_date=iso, file=cand.image_filename, detail=str(image),
            )
            return None
        if image is None:
            self.review.record(
                IMAGE_FETCH_FAILED, name=cand.full_name, wikidata_id=cand.wikidata_id,
                target_date=iso, file=cand.image_filename,
                detail="Commons lookup returned nothing for this file",
            )
            return None

        alive = self.alive.check(cand.wikipedia_title, cand.full_name)
        if not alive.ok:
            reason = ALIVE_MISMATCH if alive.status == "mismatch" else ALIVE_UNVERIFIED
            self.review.record(
                reason, name=cand.full_name, wikidata_id=cand.wikidata_id,
                target_date=iso, detail=alive.detail,
                wikipedia_title=cand.wikipedia_title,
            )
            log.warning("%s: %s flagged (%s)", iso, cand.full_name, alive.detail)

        notes: list[str] = []
        if cand.curated:
            notes.append("curated list")
        if not alive.ok:
            notes.append(alive.detail)
        notes.extend(image.warnings)
        scale = upscale_factor(image.width, image.height,
                               self.cfg.render.width, self.cfg.render.height)
        if scale > 1.0:
            notes.append(f"photo enlarged {scale:.2f}x to fill the frame")

        return Person(
            wikidata_id=cand.wikidata_id,
            full_name=cand.full_name,
            birthday=iso,
            birth_date=cand.birth_date.isoformat(),
            birth_year=cand.birth_date.year,
            age_turning=age_turning(cand.birth_date, day),
            category=cand.category,
            image_url=image.image_url,
            image_file_page=image.file_page_url,
            image_width=image.width,
            image_height=image.height,
            image_license=f"{image.license_name} [{image.license_family}]",
            image_attribution=image.attribution,
            alive_verified=alive.status,
            graphic_status=GRAPHIC_PENDING if alive.ok else GRAPHIC_NEEDS_REVIEW,
            wikipedia_title=cand.wikipedia_title,
            sitelinks=cand.sitelinks,
            pageviews=cand.pageviews,
            notability_score=cand.notability_score,
            notes=" | ".join(notes),
        )

    # -- the window ---------------------------------------------------------
    def run(self, days: list[date]) -> int:
        total = 0
        db = Database(self.cfg.paths.database) if self.write else None
        try:
            for day in days:
                total += len(self.run_day(db, day))
        finally:
            if db is not None:
                db.close()
        return total


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build the birthday database from Wikidata/Commons.")
    p.add_argument("--config", default=None, help="path to config.yaml")
    p.add_argument("--start", default=None, help="start date (YYYY-MM-DD); defaults to config")
    p.add_argument("--days", type=int, default=None, help="number of days; defaults to config")
    p.add_argument("--date", default=None, help="run a single date instead of a window")
    p.add_argument("--refresh", action="store_true", help="ignore cached SPARQL results")
    p.add_argument("--no-write", action="store_true",
                   help="preview only: query and rank but write nothing to the database")
    p.add_argument("--shortlist", type=int, default=DEFAULT_SHORTLIST,
                   help="how many candidates per day get a pageviews lookup")
    p.add_argument("--max-upscale", type=float, default=None,
                   help="override the photo-quality gate (config: sourcing.max_upscale). "
                        "Raise it to refill a day the review log reports as underfilled; "
                        "1.0 never enlarges a photo at all.")
    p.add_argument("--export-csv", default=None, help="also write the database out as CSV")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = load_config(args.config)
    if args.max_upscale:
        cfg = replace(cfg, sourcing=replace(cfg.sourcing, max_upscale=args.max_upscale))
        log.info("photo-quality gate overridden: max_upscale=%.2f", args.max_upscale)
    cfg.ensure_dirs()

    if args.date:
        days = [date.fromisoformat(args.date)]
    else:
        start = date.fromisoformat(args.start) if args.start else cfg.schedule.start_date
        days = date_range(start, args.days or cfg.schedule.days)

    fetcher = BirthdayFetcher(
        cfg, refresh=args.refresh, shortlist_size=args.shortlist, write=not args.no_write,
    )
    log.info("fetching %d day(s): %s .. %s", len(days), days[0], days[-1])
    accepted = fetcher.run(days)
    log.info("done: %d people accepted across %d day(s)", accepted, len(days))
    log.info("review log: %s", cfg.paths.review_log)

    if args.export_csv and not args.no_write:
        with Database(cfg.paths.database) as db:
            out = db.export_csv(Path(args.export_csv), spec_columns_only=True)
        log.info("csv written: %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
