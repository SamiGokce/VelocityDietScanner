"""SQLite storage for the 90-day birthday database.

One row per (person, featured date).  The columns the build spec calls for --
full_name, birthday, birth_year, age_turning, category, image_url,
image_license, image_attribution, alive_verified, graphic_status, notes -- are
all present under exactly those names; everything else is extra bookkeeping
that makes reruns cheap and failures debuggable.

`birthday` is the ISO date the person is *featured* (their birthday in the
90-day window).  `birth_date` is their full date of birth.
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

# --- status vocabularies ---------------------------------------------------
GRAPHIC_PENDING = "Pending"
GRAPHIC_READY = "Ready"
GRAPHIC_FAILED = "Failed"
GRAPHIC_NEEDS_REVIEW = "Needs Review"

UPLOAD_PENDING = "Pending"
UPLOAD_POSTED = "Posted"
UPLOAD_FAILED = "Failed"

ALIVE_YES = "yes"
ALIVE_MISMATCH = "mismatch"
ALIVE_UNVERIFIED = "unverified"

# The column set the spec asks for, in spec order -- used for CSV export.
log = logging.getLogger(__name__)

SPEC_COLUMNS = (
    "full_name", "birthday", "birth_year", "age_turning", "category",
    "image_url", "image_license", "image_attribution", "alive_verified",
    "graphic_status", "notes",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    wikidata_id       TEXT    NOT NULL,
    full_name         TEXT    NOT NULL,
    birthday          TEXT    NOT NULL,   -- ISO date this person is featured
    birth_date        TEXT    NOT NULL,   -- ISO full date of birth
    birth_year        INTEGER NOT NULL,
    age_turning       INTEGER NOT NULL,
    category          TEXT    NOT NULL,
    image_url         TEXT,
    image_file_page   TEXT,
    image_width       INTEGER DEFAULT 0,
    image_height      INTEGER DEFAULT 0,
    image_license     TEXT,
    image_attribution TEXT,
    alive_verified    TEXT    NOT NULL DEFAULT 'unverified',
    graphic_status    TEXT    NOT NULL DEFAULT 'Pending',
    graphic_path      TEXT,
    video_path        TEXT,
    upload_status     TEXT    NOT NULL DEFAULT 'Pending',
    youtube_video_id  TEXT,
    posted_at         TEXT,
    instagram_status  TEXT    NOT NULL DEFAULT 'Pending',
    instagram_media_id TEXT,
    instagram_posted_at TEXT,
    wikipedia_title   TEXT,
    sitelinks         INTEGER DEFAULT 0,
    pageviews         INTEGER DEFAULT 0,
    notability_score  REAL    DEFAULT 0,
    notes             TEXT    DEFAULT '',
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    UNIQUE (wikidata_id, birthday)
);
CREATE INDEX IF NOT EXISTS idx_people_birthday ON people (birthday);
CREATE INDEX IF NOT EXISTS idx_people_graphic_status ON people (graphic_status);
CREATE INDEX IF NOT EXISTS idx_people_upload_status ON people (upload_status);
"""


@dataclass
class Person:
    """A fully sourced, render-ready person (or one flagged for review)."""
    wikidata_id: str
    full_name: str
    birthday: str
    birth_date: str
    birth_year: int
    age_turning: int
    category: str
    image_url: str | None = None
    image_file_page: str | None = None
    image_width: int = 0
    image_height: int = 0
    image_license: str | None = None
    image_attribution: str | None = None
    alive_verified: str = ALIVE_UNVERIFIED
    graphic_status: str = GRAPHIC_PENDING
    graphic_path: str | None = None
    video_path: str | None = None
    upload_status: str = UPLOAD_PENDING
    youtube_video_id: str | None = None
    posted_at: str | None = None
    wikipedia_title: str | None = None
    sitelinks: int = 0
    pageviews: int = 0
    notability_score: float = 0.0
    notes: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        CREATE TABLE IF NOT EXISTS leaves an existing table untouched, so a
        database built by an earlier version would otherwise be missing newer
        columns and every insert would fail.
        """
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(people)")}
        added = [
            ("image_width", "INTEGER DEFAULT 0"),
            ("image_height", "INTEGER DEFAULT 0"),
        ]
        for column, definition in added:
            if column not in existing:
                self.conn.execute(f"ALTER TABLE people ADD COLUMN {column} {definition}")
                log.info("migrated database: added people.%s", column)

    # -- lifecycle ----------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- writes -------------------------------------------------------------
    def upsert_person(self, person: Person) -> int:
        """Insert a person, or refresh the sourcing fields of an existing row.

        An existing row's render/upload progress is never clobbered by a
        re-run of the fetch step -- reruns are safe.
        """
        data = asdict(person)
        now = _now()
        cur = self.conn.execute(
            "SELECT id FROM people WHERE wikidata_id = ? AND birthday = ?",
            (person.wikidata_id, person.birthday),
        )
        row = cur.fetchone()
        if row is None:
            data["created_at"] = now
            data["updated_at"] = now
            cols = ", ".join(data)
            marks = ", ".join("?" for _ in data)
            cur = self.conn.execute(
                f"INSERT INTO people ({cols}) VALUES ({marks})", tuple(data.values())
            )
            self.conn.commit()
            return int(cur.lastrowid)

        keep = {"graphic_status", "graphic_path", "video_path", "upload_status",
                "youtube_video_id", "posted_at"}
        update = {k: v for k, v in data.items() if k not in keep}
        update["updated_at"] = now
        assignments = ", ".join(f"{k} = ?" for k in update)
        self.conn.execute(
            f"UPDATE people SET {assignments} WHERE id = ?",
            (*update.values(), row["id"]),
        )
        self.conn.commit()
        return int(row["id"])

    def update(self, person_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE people SET {assignments} WHERE id = ?",
            (*fields.values(), person_id),
        )
        self.conn.commit()

    def append_note(self, person_id: int, note: str) -> None:
        row = self.conn.execute("SELECT notes FROM people WHERE id = ?", (person_id,)).fetchone()
        existing = (row["notes"] if row and row["notes"] else "").strip()
        combined = f"{existing} | {note}".strip(" |") if existing else note
        self.update(person_id, notes=combined[:2000])

    def mark_posted(self, person_id: int, video_id: str) -> None:
        self.update(
            person_id,
            upload_status=UPLOAD_POSTED,
            youtube_video_id=video_id,
            posted_at=_now(),
        )

    # -- reads --------------------------------------------------------------
    def all_rows(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM people ORDER BY birthday, notability_score DESC"))

    def rows_for_date(self, day: str | date) -> list[sqlite3.Row]:
        day = day.isoformat() if isinstance(day, date) else day
        return list(self.conn.execute(
            "SELECT * FROM people WHERE birthday = ? ORDER BY notability_score DESC", (day,)
        ))

    def existing_ids_for_date(self, day: str) -> set[str]:
        return {
            r["wikidata_id"]
            for r in self.conn.execute("SELECT wikidata_id FROM people WHERE birthday = ?", (day,))
        }

    def pending_renders(self, start: str | None = None, end: str | None = None,
                        statuses: Sequence[str] = (GRAPHIC_PENDING, GRAPHIC_FAILED),
                        limit: int | None = None) -> list[sqlite3.Row]:
        sql = (
            "SELECT * FROM people WHERE graphic_status IN (%s) AND alive_verified = ?"
            % ", ".join("?" for _ in statuses)
        )
        params: list[Any] = [*statuses, ALIVE_YES]
        if start:
            sql += " AND birthday >= ?"
            params.append(start)
        if end:
            sql += " AND birthday <= ?"
            params.append(end)
        sql += " ORDER BY birthday, notability_score DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return list(self.conn.execute(sql, params))

    def uploadable(self, day: str, limit: int) -> list[sqlite3.Row]:
        """Ready, alive-verified, not yet posted rows for one day."""
        return list(self.conn.execute(
            "SELECT * FROM people WHERE birthday = ? AND graphic_status = ? "
            "AND alive_verified = ? AND upload_status != ? "
            "ORDER BY notability_score DESC LIMIT ?",
            (day, GRAPHIC_READY, ALIVE_YES, UPLOAD_POSTED, int(limit)),
        ))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        out["total"] = self.conn.execute("SELECT COUNT(*) c FROM people").fetchone()["c"]
        for label, col in (("graphic", "graphic_status"), ("upload", "upload_status"),
                           ("alive", "alive_verified")):
            for r in self.conn.execute(f"SELECT {col} k, COUNT(*) c FROM people GROUP BY {col}"):
                out[f"{label}:{r['k']}"] = r["c"]
        return out

    # -- export -------------------------------------------------------------
    def export_csv(self, path: str | Path, spec_columns_only: bool = False) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.all_rows()
        if spec_columns_only:
            columns: Iterable[str] = SPEC_COLUMNS
        else:
            columns = [d[0] for d in self.conn.execute("SELECT * FROM people LIMIT 1").description] \
                if rows else list(SPEC_COLUMNS)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row[k] for k in columns})
        return path
