"""Alive verification: the check that stops an obituary going out as a birthday.

The rule is deliberately conservative -- anything short of a positive
"Category:Living people, present-tense lead" is withheld for a human.
"""

from common.db import ALIVE_MISMATCH, ALIVE_UNVERIFIED, ALIVE_YES
from scripts.alive_check import AliveChecker


class FakeSession:
    """Stands in for PoliteSession: canned categories + summary extract."""

    def __init__(self, categories=None, extract="", missing=False):
        self.categories = categories or []
        self.extract = extract
        self.missing = missing

    def get_json(self, url, params=None, headers=None):
        if params and params.get("prop") == "categories":
            if self.missing:
                return {"query": {"pages": [{"missing": True}]}}
            return {"query": {"pages": [
                {"categories": [{"title": c} for c in self.categories]}
            ]}}
        return {"extract": self.extract}


def check(categories=None, extract="", missing=False, title="Someone"):
    return AliveChecker(FakeSession(categories, extract, missing)).check(title)


def test_living_people_category_verifies():
    result = check(["Category:Living people", "Category:American actors"],
                   extract="Jane Doe is an American actor.")
    assert result.status == ALIVE_YES
    assert result.ok


def test_deaths_category_is_a_mismatch():
    result = check(["Category:Living people", "Category:2026 deaths"])
    assert result.status == ALIVE_MISMATCH
    assert "2026 deaths" in result.detail


def test_past_tense_lead_is_a_mismatch_even_when_categorised_as_living():
    result = check(["Category:Living people"], extract="John Doe was an American singer.")
    assert result.status == ALIVE_MISMATCH


def test_missing_living_people_category_is_unverified_not_a_pass():
    assert check(["Category:American actors"]).status == ALIVE_UNVERIFIED


def test_missing_article_is_unverified():
    assert check(missing=True).status == ALIVE_UNVERIFIED


def test_no_wikipedia_title_is_unverified():
    result = AliveChecker(FakeSession()).check(None)
    assert result.status == ALIVE_UNVERIFIED
    assert not result.ok


def test_disappeared_people_are_flagged():
    assert check(["Category:Living people", "Category:Missing people"]).status == ALIVE_MISMATCH


def test_present_tense_lead_is_not_confused_by_a_was_elsewhere():
    result = check(["Category:Living people"],
                   extract="Jane Doe is a director. Her first film premiered in 2001.")
    assert result.status == ALIVE_YES
