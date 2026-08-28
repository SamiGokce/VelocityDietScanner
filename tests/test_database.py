from pathlib import Path

from common.db import (ALIVE_MISMATCH, ALIVE_YES, GRAPHIC_NEEDS_REVIEW,
                       GRAPHIC_PENDING, GRAPHIC_READY, SPEC_COLUMNS,
                       UPLOAD_POSTED, Database, Person)


def person(qid="Q1", name="Test Person", day="2026-08-28", **kw):
    base = dict(
        wikidata_id=qid, full_name=name, birthday=day, birth_date="1984-08-28",
        birth_year=1984, age_turning=42, category="Actor", alive_verified=ALIVE_YES,
        image_url="https://example.org/x.jpg", image_license="CC BY-SA 4.0 [cc-by-sa]",
        image_attribution="Photo: Someone, CC BY-SA 4.0",
    )
    base.update(kw)
    return Person(**base)


def test_schema_has_every_column_the_spec_asks_for(tmp_path):
    with Database(tmp_path / "db.sqlite3") as db:
        columns = {r[1] for r in db.conn.execute("PRAGMA table_info(people)")}
    assert set(SPEC_COLUMNS) <= columns


def test_refetch_does_not_clobber_render_progress(tmp_path):
    with Database(tmp_path / "db.sqlite3") as db:
        pid = db.upsert_person(person())
        db.update(pid, graphic_status=GRAPHIC_READY, graphic_path="/tmp/a.png")
        again = db.upsert_person(person(name="Test Person Renamed"))
        assert again == pid
        row = db.rows_for_date("2026-08-28")[0]
        assert row["graphic_status"] == GRAPHIC_READY
        assert row["graphic_path"] == "/tmp/a.png"
        assert row["full_name"] == "Test Person Renamed"


def test_same_person_on_two_dates_is_two_rows(tmp_path):
    with Database(tmp_path / "db.sqlite3") as db:
        db.upsert_person(person(day="2026-08-28"))
        db.upsert_person(person(day="2027-08-28"))
        assert len(db.all_rows()) == 2


def test_pending_renders_skips_rows_needing_review(tmp_path):
    with Database(tmp_path / "db.sqlite3") as db:
        db.upsert_person(person("Q1"))
        db.upsert_person(person("Q2", alive_verified=ALIVE_MISMATCH,
                                graphic_status=GRAPHIC_NEEDS_REVIEW))
        pending = db.pending_renders(statuses=(GRAPHIC_PENDING,))
        assert [r["wikidata_id"] for r in pending] == ["Q1"]


def test_uploadable_requires_ready_alive_and_unposted(tmp_path):
    with Database(tmp_path / "db.sqlite3") as db:
        ready = db.upsert_person(person("Q1"))
        db.update(ready, graphic_status=GRAPHIC_READY)
        pending = db.upsert_person(person("Q2"))                      # still Pending
        flagged = db.upsert_person(person("Q3", alive_verified=ALIVE_MISMATCH))
        db.update(flagged, graphic_status=GRAPHIC_READY)
        posted = db.upsert_person(person("Q4"))
        db.update(posted, graphic_status=GRAPHIC_READY)
        db.mark_posted(posted, "abc123")

        rows = db.uploadable("2026-08-28", 10)
        assert [r["wikidata_id"] for r in rows] == ["Q1"]
        assert db.conn.execute(
            "SELECT upload_status, youtube_video_id FROM people WHERE id = ?", (posted,)
        ).fetchone()["upload_status"] == UPLOAD_POSTED
        assert pending  # referenced so the intent is obvious


def test_uploadable_respects_the_daily_limit(tmp_path):
    with Database(tmp_path / "db.sqlite3") as db:
        for i in range(5):
            pid = db.upsert_person(person(f"Q{i}", name=f"Person {i}"))
            db.update(pid, graphic_status=GRAPHIC_READY, notability_score=float(i))
        rows = db.uploadable("2026-08-28", 3)
        assert len(rows) == 3
        assert [r["full_name"] for r in rows] == ["Person 4", "Person 3", "Person 2"]


def test_notes_accumulate(tmp_path):
    with Database(tmp_path / "db.sqlite3") as db:
        pid = db.upsert_person(person())
        db.append_note(pid, "first")
        db.append_note(pid, "second")
        assert db.all_rows()[0]["notes"] == "first | second"


def test_csv_export_uses_the_spec_columns(tmp_path):
    with Database(tmp_path / "db.sqlite3") as db:
        db.upsert_person(person())
        out = db.export_csv(tmp_path / "out.csv", spec_columns_only=True)
    header = Path(out).read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == list(SPEC_COLUMNS)
