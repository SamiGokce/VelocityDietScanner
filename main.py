#!/usr/bin/env python3
"""One entry point for the whole pipeline.

    python main.py fetch  --days 90            # build the database
    python main.py render --date 2026-09-01    # make graphics/videos
    python main.py upload --limit 3            # post the day's Shorts
    python main.py run    --dry-run            # fetch only, no graphics
    python main.py status                      # what is in the database
    python main.py export --out data/birthdays.csv

Each subcommand is a thin wrapper around the module that does the work, so
`python -m scripts.fetch_birthdays`, `python -m render.generate` and
`python -m upload.upload_daily` remain equally valid (and are what cron and
the GitHub Actions workflow call).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from common.config import load_config
from common.db import Database

log = logging.getLogger("pipeline")


def cmd_fetch(rest: list[str]) -> int:
    from scripts.fetch_birthdays import main as fetch_main
    return fetch_main(rest)


def cmd_render(rest: list[str]) -> int:
    from render.generate import main as render_main
    return render_main(rest)


def cmd_upload(rest: list[str]) -> int:
    from upload.upload_daily import main as upload_main
    return upload_main(rest)


def cmd_run(rest: list[str]) -> int:
    """fetch (+ render, unless --dry-run)."""
    parser = argparse.ArgumentParser(prog="main.py run")
    parser.add_argument("--config", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="build the database only -- no graphics, so the data "
                             "can be spot-checked before anything is rendered")
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(rest)

    common = []
    if args.config:
        common += ["--config", args.config]
    if args.verbose:
        common += ["-v"]

    fetch_args = list(common)
    if args.start:
        fetch_args += ["--start", args.start]
    if args.days:
        fetch_args += ["--days", str(args.days)]
    code = cmd_fetch(fetch_args)
    if code != 0 or args.dry_run:
        if args.dry_run:
            print("\nDry run: database built, no graphics rendered.")
            print("Spot-check it with `python main.py status` or "
                  "`python main.py export --out data/preview.csv`,")
            print("then render with `python main.py render`.")
        return code

    render_args = list(common)
    if args.start:
        render_args += ["--start", args.start]
    if args.days:
        render_args += ["--days", str(args.days)]
    if args.no_video:
        render_args += ["--no-video"]
    return cmd_render(render_args)


def cmd_status(rest: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="main.py status")
    parser.add_argument("--config", default=None)
    parser.add_argument("--date", default=None, help="detail for one date")
    args = parser.parse_args(rest)

    cfg = load_config(args.config)
    if not cfg.paths.database.is_file():
        print(f"no database yet at {cfg.paths.database}; run `python main.py fetch` first")
        return 1

    with Database(cfg.paths.database) as db:
        if args.date:
            rows = db.rows_for_date(args.date)
            print(f"{args.date}: {len(rows)} row(s)\n")
            for row in rows:
                print(f"  {row['full_name']:<32} turns {row['age_turning']:<4} "
                      f"{row['category']:<16} alive={row['alive_verified']:<11} "
                      f"graphic={row['graphic_status']:<12} upload={row['upload_status']}")
                if row["notes"]:
                    print(f"      note: {row['notes'][:150]}")
            return 0

        counts = db.counts()
        print(f"database: {cfg.paths.database}")
        print(f"  people:            {counts.get('total', 0)}")
        for key in sorted(k for k in counts if k.startswith("graphic:")):
            print(f"  graphic {key.split(':', 1)[1]:<14} {counts[key]}")
        for key in sorted(k for k in counts if k.startswith("upload:")):
            print(f"  upload  {key.split(':', 1)[1]:<14} {counts[key]}")
        for key in sorted(k for k in counts if k.startswith("alive:")):
            print(f"  alive   {key.split(':', 1)[1]:<14} {counts[key]}")
        rows = db.all_rows()
        if rows:
            print(f"  window:            {rows[0]['birthday']} .. {rows[-1]['birthday']}")
        review = cfg.paths.review_log
        if review.is_file():
            print(f"  review log:        {review} "
                  f"({sum(1 for _ in review.open(encoding='utf-8'))} entries)")
    return 0


def cmd_export(rest: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="main.py export")
    parser.add_argument("--config", default=None)
    parser.add_argument("--out", default="data/birthdays.csv")
    parser.add_argument("--all-columns", action="store_true",
                        help="export every column, not just the spec's eleven")
    args = parser.parse_args(rest)
    cfg = load_config(args.config)
    with Database(cfg.paths.database) as db:
        out = db.export_csv(Path(args.out), spec_columns_only=not args.all_columns)
    print(f"wrote {out}")
    return 0


COMMANDS = {
    "fetch": cmd_fetch,
    "render": cmd_render,
    "upload": cmd_upload,
    "run": cmd_run,
    "status": cmd_status,
    "export": cmd_export,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        print("commands: " + ", ".join(COMMANDS))
        return 0
    command = argv[0]
    if command not in COMMANDS:
        print(f"unknown command {command!r}; try one of: {', '.join(COMMANDS)}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return COMMANDS[command](argv[1:])


if __name__ == "__main__":
    sys.exit(main())
