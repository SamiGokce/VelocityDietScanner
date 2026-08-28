"""Wikidata candidate discovery: who has a birthday on a given month/day.

Two queries per day, deliberately:

  A. **Candidates** -- humans born on that month/day, with day-level date
     precision, at least `min_sitelinks` Wikipedia editions, and no statement
     implying death.  It returns nothing but Q-ids and sitelink counts, so it
     stays inside the query service's 60-second budget.  Joining 67 occupation
     values and pulling labels/images/articles in the same query does not, and
     that is what times out on a busy day.
  B. **Details** -- label, image, English article and occupations for a bounded
     `VALUES ?person { ... }` list (the day's top candidates plus anyone on the
     curated list).  Binding the subjects up front makes this one cheap
     regardless of how many people share the birthday.

Alive filtering starts here: query A excludes anyone carrying *any* death
signal -- date of death (P570, including deprecated statements), place of death
(P20), cause of death (P509), manner of death (P1196) or place of burial
(P119) -- and query B re-checks P570 on the detail pass.  An independent
cross-check against English Wikipedia follows in `scripts.alive_check`.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from common.http import PoliteSession, WikimediaError
from scripts.occupations import QID_TO_CATEGORY, categorise

log = logging.getLogger(__name__)

# WDQS registers these prefixes automatically; other endpoints (a QLever
# mirror, a self-hosted Blazegraph) do not, so declare them explicitly and keep
# `sourcing.sparql_endpoint` swappable when the public service is degraded.
PREFIXES = """
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX psv: <http://www.wikidata.org/prop/statement/value/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <http://schema.org/>
"""

CANDIDATE_QUERY = PREFIXES + """
SELECT ?person ?dob ?sitelinks
WHERE {
  ?person wdt:P31 wd:Q5 ;
          wikibase:sitelinks ?sitelinks ;
          p:P569/psv:P569 ?dobNode .
  ?dobNode wikibase:timeValue ?dob ;
           wikibase:timePrecision ?precision .
  FILTER(?sitelinks >= %(min_sitelinks)d)
  FILTER(?precision >= 11)
  FILTER(MONTH(?dob) = %(month)d && DAY(?dob) = %(day)d)
  FILTER(YEAR(?dob) >= %(min_year)d && YEAR(?dob) <= %(max_year)d)
  # --- anything that implies the person is not alive ---
  FILTER NOT EXISTS { ?person p:P570  ?anyDeathStatement }
  FILTER NOT EXISTS { ?person wdt:P20   ?placeOfDeath }
  FILTER NOT EXISTS { ?person wdt:P509  ?causeOfDeath }
  FILTER NOT EXISTS { ?person wdt:P1196 ?mannerOfDeath }
  FILTER NOT EXISTS { ?person wdt:P119  ?placeOfBurial }
}
ORDER BY DESC(?sitelinks)
LIMIT %(limit)d
"""

DETAIL_QUERY = PREFIXES + """
SELECT ?person ?personLabel ?dob ?sitelinks ?image ?article ?dead
       (GROUP_CONCAT(DISTINCT STRAFTER(STR(?occ), "/entity/"); separator=",") AS ?occupations)
WHERE {
  VALUES ?person { %(people)s }
  ?person wikibase:sitelinks ?sitelinks ;
          wdt:P569 ?dob ;
          wdt:P106 ?occ .
  OPTIONAL { ?person wdt:P18 ?image }
  OPTIONAL { ?person rdfs:label ?personLabel FILTER(LANG(?personLabel) = "en") }
  OPTIONAL {
    ?article schema:about ?person ;
             schema:isPartOf <https://en.wikipedia.org/> .
  }
  OPTIONAL { ?person wdt:P570 ?dead }
}
GROUP BY ?person ?personLabel ?dob ?sitelinks ?image ?article ?dead
"""

#: Year bands used to split query A when it exceeds the service's time budget.
#: Their union is the full range, so chunking never changes which people match.
YEAR_CHUNKS = 4


@dataclass
class Candidate:
    wikidata_id: str
    full_name: str
    birth_date: date
    sitelinks: int
    occupations: list[str]
    image_filename: str | None = None
    wikipedia_title: str | None = None
    curated: bool = False
    pageviews: int = 0
    notability_score: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def category(self) -> str:
        return categorise(self.occupations)


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _image_filename(image_uri: str | None) -> str | None:
    """'http://commons.wikimedia.org/wiki/Special:FilePath/Foo%20bar.jpg' -> 'Foo bar.jpg'."""
    if not image_uri:
        return None
    name = urllib.parse.unquote(image_uri.rsplit("/", 1)[-1])
    return name.replace("_", " ").strip() or None


def _article_title(article_uri: str | None) -> str | None:
    if not article_uri:
        return None
    title = urllib.parse.unquote(article_uri.rsplit("/wiki/", 1)[-1])
    return title.replace("_", " ").strip() or None


def _parse_dob(raw: str) -> date | None:
    # WDQS returns e.g. "1984-08-28T00:00:00Z"
    m = re.match(r"^(-?\d{4})-(\d{2})-(\d{2})T", raw)
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    if year < 1 or month < 1 or day < 1:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


class WikidataClient:
    #: SPARQL gets its own, longer timeout: the query service enforces a 60s
    #: budget server-side, so a client timeout below that turns a slow-but-
    #: successful query into a pointless retry.
    QUERY_TIMEOUT = 95.0

    #: How many of the day's candidates get the detail query.  Comfortably more
    #: than the 3-5 that will be used, to leave room for people who turn out to
    #: have no free photo or an ineligible occupation.
    DETAIL_POOL = 45

    def __init__(self, session: PoliteSession, endpoint: str,
                 cache_dir: Path | None = None, detail_pool: int | None = None) -> None:
        self.session = session
        self.endpoint = endpoint
        self.detail_pool = detail_pool or self.DETAIL_POOL
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- querying -----------------------------------------------------------
    def _run(self, query: str) -> list[dict]:
        previous_timeout = self.session.timeout
        self.session.timeout = max(previous_timeout, self.QUERY_TIMEOUT)
        try:
            resp = self.session.get(
                self.endpoint,
                params={"query": query, "format": "json"},
                headers={"Accept": "application/sparql-results+json"},
            )
        finally:
            self.session.timeout = previous_timeout
        if not resp.ok:
            raise WikimediaError(
                f"SPARQL query failed with HTTP {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json().get("results", {}).get("bindings", [])

    @staticmethod
    def _is_timeout(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(t in message for t in ("timeout", "timed out", "504", "502"))

    def _candidate_rows(self, day: date, min_sitelinks: int, limit: int) -> list[dict]:
        """Query A, splitting into birth-year bands if the service times out."""
        params = {
            "min_sitelinks": int(min_sitelinks),
            "month": day.month,
            "day": day.day,
            "min_year": day.year - 110,   # nobody older than 110 is in scope
            "max_year": day.year,
            "limit": int(limit),
        }
        try:
            return self._run(CANDIDATE_QUERY % params)
        except WikimediaError as exc:
            if not self._is_timeout(exc):
                raise
            log.warning("candidate query timed out for %s; retrying in year bands", day)

        span = (params["max_year"] - params["min_year"] + 1) / YEAR_CHUNKS
        rows: list[dict] = []
        seen: set[str] = set()
        for index in range(YEAR_CHUNKS):
            band = dict(params)
            band["min_year"] = int(params["min_year"] + index * span)
            band["max_year"] = int(
                params["min_year"] + (index + 1) * span - 1 if index < YEAR_CHUNKS - 1
                else params["max_year"]
            )
            for row in self._run(CANDIDATE_QUERY % band):
                key = row["person"]["value"]
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
        return rows

    def _detail_rows(self, qids: list[str]) -> list[dict]:
        """Query B, in batches so the VALUES block stays a sensible size."""
        rows: list[dict] = []
        for start in range(0, len(qids), 60):
            batch = qids[start:start + 60]
            rows.extend(self._run(
                DETAIL_QUERY % {"people": " ".join(f"wd:{q}" for q in batch)}
            ))
        return rows

    def candidates_for(self, day: date, min_sitelinks: int, limit: int,
                       curated_ids: list[str] | None = None,
                       refresh: bool = False) -> list[Candidate]:
        curated = list(curated_ids or [])
        cache_file = None
        if self.cache_dir:
            cache_file = self.cache_dir / f"{day.month:02d}-{day.day:02d}_s{min_sitelinks}.json"
            if cache_file.is_file() and not refresh:
                log.debug("using cached SPARQL result %s", cache_file.name)
                return self._to_candidates(
                    json.loads(cache_file.read_text(encoding="utf-8")), day, set(curated)
                )

        candidate_rows = self._candidate_rows(day, min_sitelinks, limit)
        ranked = sorted(
            candidate_rows,
            key=lambda r: -int(r.get("sitelinks", {}).get("value", 0) or 0),
        )
        qids = [_qid(r["person"]["value"]) for r in ranked[:self.detail_pool]]
        # Curated people are looked up whether or not they cleared the threshold.
        qids.extend(q for q in curated if q not in qids)

        rows = self._detail_rows(qids) if qids else []
        if cache_file:
            cache_file.write_text(json.dumps(rows), encoding="utf-8")
        return self._to_candidates(rows, day, set(curated))

    # -- parsing ------------------------------------------------------------
    @staticmethod
    def _to_candidates(rows: list[dict], day: date, curated: set[str]) -> list[Candidate]:
        out: list[Candidate] = []
        for row in rows:
            qid = _qid(row["person"]["value"])
            if row.get("dead", {}).get("value"):
                # Belt and braces: query A excluded these, but a stale cache or a
                # curated entry could still carry one through.
                log.warning("%s has a date of death; skipping", qid)
                continue
            label = row.get("personLabel", {}).get("value", "").strip()
            if not label or re.fullmatch(r"Q\d+", label):
                continue  # no usable English label -- nothing to put on a graphic
            dob = _parse_dob(row.get("dob", {}).get("value", ""))
            if dob is None or (dob.month, dob.day) != (day.month, day.day):
                continue
            occupations = [
                q for q in row.get("occupations", {}).get("value", "").split(",") if q
            ]
            if not any(q in QID_TO_CATEGORY for q in occupations):
                continue  # none of the occupations this project features
            out.append(Candidate(
                wikidata_id=qid,
                full_name=label,
                birth_date=dob,
                sitelinks=int(row.get("sitelinks", {}).get("value", 0) or 0),
                occupations=occupations,
                image_filename=_image_filename(row.get("image", {}).get("value")),
                wikipedia_title=_article_title(row.get("article", {}).get("value")),
                curated=qid in curated,
            ))
        return out


def load_curated_list(path: Path) -> list[str]:
    """Read `wikidata_id,name,notes` rows; blank lines and `#` comments ignored."""
    if not path.is_file():
        return []
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        first = line.split(",", 1)[0].strip().upper()
        if re.fullmatch(r"Q\d+", first):
            ids.append(first)
    return ids
