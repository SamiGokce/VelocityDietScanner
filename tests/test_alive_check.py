"""Alive verification: the check that stops an obituary going out as a birthday.

The rule is deliberately conservative -- anything short of a positive
"Category:Living people, present-tense lead" is withheld for a human.
"""

import pytest

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


# --- real false positives this heuristic used to produce --------------------

@pytest.mark.parametrize("extract", [
    # Retired athletes: "former" plus a career described in the past tense.
    "Andrew Stephen Roddick is an American former professional tennis player. "
    "He was ranked as the world No. 1 in men's singles by the ATP.",
    "Timothy Henry Henman is a British former professional tennis player. "
    "He was ranked world No. 4 in men's singles.",
    # A living actor whose childhood is, necessarily, in the past tense.
    "Naomie Melanie Harris is a British actress. She started her career when "
    "she was a child, appearing in the television series Simon and the Witch.",
    # Past-tense achievements are not obituaries.
    "Michael Keaton is an American actor. He was nominated for an Academy Award.",
])
def test_a_living_person_with_a_past_tense_career_is_not_flagged(extract):
    """These three were all wrongly flagged on a real run.

    The lead opens "is a ...", which is the only tense that means anything;
    everything after it describes things that already happened.
    """
    assert check(["Category:Living people"], extract=extract).status == ALIVE_YES


@pytest.mark.parametrize("extract", [
    "Kobe Bean Bryant was an American professional basketball player.",
    "Matthew Perry was a Canadian-American actor best known for Friends.",
    # No article after the verb -- the earlier pattern missed this entirely.
    "Elizabeth II was Queen of the United Kingdom and other Commonwealth realms.",
])
def test_a_lead_in_the_past_tense_is_still_caught(extract):
    assert check(["Category:Living people"], extract=extract).status == ALIVE_MISMATCH
