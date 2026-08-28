"""Upload metadata -- above all, that the photo credit is always in the description."""

import sqlite3

import pytest

from common.config import ConfigError, load_config
from common.db import Database, Person
from dataclasses import replace
from upload.upload_daily import (DESCRIPTION_LIMIT, TITLE_LIMIT, UploadError,
                                 build_metadata)

ATTRIBUTION = ("Photo: Gage Skidmore, CC BY-SA 4.0, "
               "https://creativecommons.org/licenses/by-sa/4.0, via Wikimedia Commons — "
               "https://commons.wikimedia.org/wiki/File:Jack_Black.jpg")


@pytest.fixture
def row(tmp_path):
    with Database(tmp_path / "db.sqlite3") as db:
        db.upsert_person(Person(
            wikidata_id="Q192643", full_name="Jack Black", birthday="2026-08-28",
            birth_date="1969-08-28", birth_year=1969, age_turning=57, category="Actor",
            image_url="https://upload.wikimedia.org/x.jpg",
            image_license="CC BY-SA 4.0 [cc-by-sa]", image_attribution=ATTRIBUTION,
            alive_verified="yes",
        ))
        yield db.all_rows()[0]


@pytest.fixture
def cfg():
    return load_config()


def test_description_contains_the_attribution(row, cfg):
    body = build_metadata(row, cfg)
    assert ATTRIBUTION in body["snippet"]["description"]


def test_title_uses_the_name_and_ordinal_age(row, cfg):
    title = build_metadata(row, cfg)["snippet"]["title"]
    assert "Jack Black" in title
    assert "57TH" in title
    assert len(title) <= TITLE_LIMIT


def test_template_without_attribution_is_refused(row, cfg):
    broken = replace(cfg, youtube=replace(
        cfg.youtube, description_template="Happy birthday {full_name}!"))
    with pytest.raises(ConfigError, match="attribution"):
        build_metadata(row, broken)


def test_missing_stored_attribution_blocks_the_upload(row, cfg, tmp_path):
    with Database(tmp_path / "db2.sqlite3") as db:
        db.upsert_person(Person(
            wikidata_id="Q1", full_name="No Credit", birthday="2026-08-28",
            birth_date="1980-08-28", birth_year=1980, age_turning=46, category="Actor",
            image_attribution=None, alive_verified="yes"))
        bad = db.all_rows()[0]
        with pytest.raises(UploadError, match="attribution"):
            build_metadata(bad, cfg)


def test_long_description_is_trimmed_but_keeps_the_credit(row, cfg):
    padded = replace(cfg, youtube=replace(
        cfg.youtube,
        description_template="x" * (DESCRIPTION_LIMIT + 500) + "\n{attribution}"))
    description = build_metadata(row, padded)["snippet"]["description"]
    assert len(description) <= DESCRIPTION_LIMIT
    assert ATTRIBUTION in description


def test_privacy_and_kids_flags_come_from_config(row, cfg):
    status = build_metadata(row, cfg)["status"]
    assert status["privacyStatus"] in {"private", "unlisted", "public"}
    assert status["selfDeclaredMadeForKids"] is False


def test_default_privacy_is_not_public(cfg):
    """The spec recommends staging uploads privately first."""
    assert cfg.youtube.privacy_status in {"private", "unlisted"}
