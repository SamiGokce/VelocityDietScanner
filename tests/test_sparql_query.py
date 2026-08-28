"""The candidate query: valid SPARQL, and the alive filters actually present.

A typo here does not raise -- it comes back as an HTTP 400 from a service that
is also, occasionally, returning 500s for unrelated reasons. Parsing the query
locally turns that into a test failure instead of a confusing outage.
"""

import pytest

from scripts.occupations import ELIGIBLE_QIDS
from scripts.wikidata import CURATED_TEMPLATE, QUERY_TEMPLATE, load_curated_list

PARAMS = dict(
    occupations=" ".join(f"wd:{q}" for q in ELIGIBLE_QIDS),
    sitelink_filter="FILTER(?sitelinks >= 40)",
    month=8, day=28, min_year=1916, limit=250,
    curated="wd:Q42 wd:Q192643",
)


@pytest.mark.parametrize("template", [QUERY_TEMPLATE, CURATED_TEMPLATE])
def test_query_is_valid_sparql(template):
    rdflib_sparql = pytest.importorskip(
        "rdflib.plugins.sparql", reason="pip install rdflib to syntax-check the query"
    )
    rdflib_sparql.prepareQuery(template % PARAMS)


def test_query_declares_its_own_prefixes():
    """WDQS pre-registers wd:/wdt:/etc.; mirrors and self-hosted endpoints do not."""
    for prefix in ("wd:", "wdt:", "p:", "psv:", "wikibase:", "rdfs:", "schema:"):
        assert f"PREFIX {prefix}" in QUERY_TEMPLATE


def test_query_excludes_every_death_signal():
    query = QUERY_TEMPLATE % PARAMS
    for prop, meaning in [
        ("p:P570", "date of death (any statement, including deprecated)"),
        ("wdt:P20", "place of death"),
        ("wdt:P509", "cause of death"),
        ("wdt:P1196", "manner of death"),
        ("wdt:P119", "place of burial"),
    ]:
        assert f"FILTER NOT EXISTS {{ ?person {prop}" in query, meaning


def test_query_requires_day_level_birth_date_precision():
    """Precision 9 (year only) renders as 1 January and would flood that day."""
    assert "?precision >= 11" in QUERY_TEMPLATE % PARAMS


def test_query_filters_to_the_requested_month_and_day():
    query = QUERY_TEMPLATE % PARAMS
    assert "MONTH(?dob) = 8" in query and "DAY(?dob) = 28" in query


def test_curated_query_drops_the_sitelink_threshold():
    curated = CURATED_TEMPLATE % {**PARAMS, "sitelink_filter": ""}
    assert "VALUES ?person" in curated
    assert "?sitelinks >=" not in curated


def test_curated_list_parsing(tmp_path):
    path = tmp_path / "curated.csv"
    path.write_text(
        "# comment\n\nQ17455,MrBeast,creator\nq42,Douglas Adams\nnot-a-qid,Someone\n",
        encoding="utf-8",
    )
    assert load_curated_list(path) == ["Q17455", "Q42"]


def test_missing_curated_list_is_not_an_error(tmp_path):
    assert load_curated_list(tmp_path / "nope.csv") == []
