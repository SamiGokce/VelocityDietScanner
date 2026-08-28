"""The candidate and detail queries: valid SPARQL, with the alive filters intact.

A typo here does not raise -- it comes back as an HTTP 400 from a service that
is also, occasionally, returning 500s for unrelated reasons.  Parsing the query
locally turns that into a test failure instead of a confusing outage.
"""

import pytest

from scripts.wikidata import (CANDIDATE_QUERY, DETAIL_QUERY, WikidataClient,
                              load_curated_list)

CANDIDATE_PARAMS = dict(min_sitelinks=40, month=8, day=28,
                        min_year=1916, max_year=2026, limit=250)
DETAIL_PARAMS = dict(people="wd:Q42 wd:Q36949")


@pytest.mark.parametrize("query", [
    CANDIDATE_QUERY % CANDIDATE_PARAMS,
    DETAIL_QUERY % DETAIL_PARAMS,
])
def test_query_is_valid_sparql(query):
    rdflib_sparql = pytest.importorskip(
        "rdflib.plugins.sparql", reason="pip install rdflib to syntax-check the queries"
    )
    rdflib_sparql.prepareQuery(query)


@pytest.mark.parametrize("template", [CANDIDATE_QUERY, DETAIL_QUERY])
def test_queries_declare_their_own_prefixes(template):
    """WDQS pre-registers wd:/wdt:/etc.; mirrors and self-hosted endpoints do not."""
    for prefix in ("wd:", "wdt:", "wikibase:", "rdfs:"):
        assert f"PREFIX {prefix}" in template


def test_candidate_query_excludes_every_death_signal():
    query = CANDIDATE_QUERY % CANDIDATE_PARAMS
    for prop, meaning in [
        ("p:P570", "date of death (any statement, including deprecated)"),
        ("wdt:P20", "place of death"),
        ("wdt:P509", "cause of death"),
        ("wdt:P1196", "manner of death"),
        ("wdt:P119", "place of burial"),
    ]:
        assert f"FILTER NOT EXISTS {{ ?person {prop}" in query, meaning


def test_detail_query_still_reports_death_dates():
    """Second line of defence for cached rows and curated entries."""
    assert "OPTIONAL { ?person wdt:P570 ?dead }" in DETAIL_QUERY


def test_candidate_query_requires_day_level_birth_date_precision():
    """Precision 9 (year only) renders as 1 January and would flood that day."""
    assert "?precision >= 11" in CANDIDATE_QUERY % CANDIDATE_PARAMS


def test_candidate_query_filters_to_the_requested_month_and_day():
    query = CANDIDATE_QUERY % CANDIDATE_PARAMS
    assert "MONTH(?dob) = 8" in query and "DAY(?dob) = 28" in query


def test_detail_query_binds_its_subjects_up_front():
    """VALUES ?person keeps the detail pass cheap however busy the day is."""
    query = DETAIL_QUERY % DETAIL_PARAMS
    assert query.index("VALUES ?person") < query.index("?person wikibase:sitelinks")
    assert "wd:Q42" in query


def test_curated_list_parsing(tmp_path):
    path = tmp_path / "curated.csv"
    path.write_text(
        "# comment\n\nQ17455,MrBeast,creator\nq42,Douglas Adams\nnot-a-qid,Someone\n",
        encoding="utf-8",
    )
    assert load_curated_list(path) == ["Q17455", "Q42"]


def test_missing_curated_list_is_not_an_error(tmp_path):
    assert load_curated_list(tmp_path / "nope.csv") == []


def test_timeout_detection_covers_the_shapes_the_service_actually_returns():
    for message in [
        "SPARQL query failed with HTTP 504: upstream request timeout",
        "GET ... failed after 5 retries: Read timed out.",
        "java.util.concurrent.TimeoutException",
        "HTTP 502: bad gateway",
    ]:
        assert WikidataClient._is_timeout(Exception(message))
    assert not WikidataClient._is_timeout(Exception("HTTP 400: malformed query"))
