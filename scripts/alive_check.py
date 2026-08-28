"""Second-source alive verification against English Wikipedia.

Wikidata is the first source (see `scripts.wikidata`, which rejects anyone
carrying any death-implying statement).  This module is the independent
cross-check the spec asks for, and it deliberately does *not* try to resolve
disagreements: a conflict is reported, flagged in the database and written to
the review log for a human to look at.

The signal used is en.wikipedia's own maintenance categorisation:

  * "Category:Living people" present, and no "Category:<year> deaths"
        -> verified alive
  * any "... deaths" category, or a REST summary describing the person in the
    past tense ("was an American actor")
        -> mismatch, never rendered
  * neither signal (no article, stub, unusual categorisation)
        -> unverified, never rendered, flagged for review
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass

from common.db import ALIVE_MISMATCH, ALIVE_UNVERIFIED, ALIVE_YES
from common.http import PoliteSession, WikimediaError

log = logging.getLogger(__name__)

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

LIVING_CATEGORY = "category:living people"
DEATHS_CATEGORY = re.compile(r"^category:\d{3,4}s?\s+deaths$")
DISAPPEARED = re.compile(r"^category:(missing|disappeared) people$")

# "John Smith was an American actor" / "... was a Turkish footballer".
PAST_TENSE = re.compile(r"\bwas (?:an?|the)\b", re.IGNORECASE)


@dataclass
class AliveResult:
    status: str          # ALIVE_YES | ALIVE_MISMATCH | ALIVE_UNVERIFIED
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == ALIVE_YES


class AliveChecker:
    def __init__(self, session: PoliteSession) -> None:
        self.session = session

    def check(self, wikipedia_title: str | None, full_name: str = "") -> AliveResult:
        if not wikipedia_title:
            return AliveResult(
                ALIVE_UNVERIFIED,
                "no English Wikipedia article to cross-check against",
            )
        try:
            categories = self._categories(wikipedia_title)
        except WikimediaError as exc:
            return AliveResult(ALIVE_UNVERIFIED, f"category lookup failed: {exc}")

        if categories is None:
            return AliveResult(ALIVE_UNVERIFIED, f"article not found: {wikipedia_title}")

        lowered = {c.lower() for c in categories}
        death_cats = sorted(c for c in lowered if DEATHS_CATEGORY.match(c))
        if death_cats:
            return AliveResult(
                ALIVE_MISMATCH,
                f"Wikidata says alive but en.wikipedia has {death_cats[0]!r}",
            )
        missing_cats = sorted(c for c in lowered if DISAPPEARED.match(c))
        if missing_cats:
            return AliveResult(
                ALIVE_MISMATCH, f"en.wikipedia has {missing_cats[0]!r}"
            )
        if LIVING_CATEGORY not in lowered:
            return AliveResult(
                ALIVE_UNVERIFIED,
                "en.wikipedia article is not in Category:Living people",
            )

        # Belt and braces: a living-people article whose lead is in the past
        # tense usually means the category has not caught up with the news yet.
        summary = self._summary(wikipedia_title)
        if summary and PAST_TENSE.search(summary):
            return AliveResult(
                ALIVE_MISMATCH,
                f"lead reads past tense while categorised as living: {summary[:160]!r}",
            )
        return AliveResult(ALIVE_YES, "en.wikipedia: Category:Living people, present tense lead")

    # -- helpers ------------------------------------------------------------
    def _categories(self, title: str) -> list[str] | None:
        data = self.session.get_json(WIKIPEDIA_API, params={
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "titles": title,
            "prop": "categories",
            "cllimit": "max",
            "clshow": "!hidden",
            "redirects": 1,
        })
        pages = (data or {}).get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            return None
        return [c.get("title", "") for c in (pages[0].get("categories") or [])]

    def _summary(self, title: str) -> str | None:
        try:
            data = self.session.get_json(
                SUMMARY_URL.format(title=urllib.parse.quote(title.replace(" ", "_"), safe=""))
            )
        except WikimediaError as exc:
            log.debug("summary lookup failed for %r: %s", title, exc)
            return None
        if not data:
            return None
        return (data.get("extract") or "")[:600] or None
