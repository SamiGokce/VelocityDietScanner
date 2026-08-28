"""Wikidata candidate discovery: who has a birthday on a given month/day.

One SPARQL query per calendar day returns everything the ranking step needs
(label, date of birth, sitelink count, P18 image filename, English Wikipedia
title, occupations), so the expensive per-person entity lookups are avoided.

Alive filtering happens twice.  Here, at the query level, we exclude anyone
carrying *any* statement that implies death -- date of death (P570, including
deprecated statements), place of death (P20), cause of death (P509), manner of
death (P1196) or place of burial (P119).  A second, independent check against
English Wikipedia happens in `scripts.alive_check`.
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
from scripts.occupations import ELIGIBLE_QIDS, categorise

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

QUERY_TEMPLATE = PREFIXES + """
SELECT ?person ?personLabel ?dob ?sitelinks ?image ?article
       (GROUP_CONCAT(DISTINCT STRAFTER(STR(?occ), "/entity/"); separator=",") AS ?occupations)
WHERE {
  VALUES ?occ { %(occupations)s }
  ?person wdt:P31 wd:Q5 ;
          wikibase:sitelinks ?sitelinks ;
          wdt:P106 ?occ .
  %(sitelink_filter)s
  ?person p:P569/psv:P569 ?dobNode .
  ?dobNode wikibase:timeValue ?dob ;
           wikibase:timePrecision ?precision .
  FILTER(?precision >= 11)
  FILTER(MONTH(?dob) = %(month)d && DAY(?dob) = %(day)d)
  FILTER(YEAR(?dob) >= %(min_year)d)
  # --- anything that implies the person is not alive ---
  FILTER NOT EXISTS { ?person p:P570  ?anyDeathStatement }
  FILTER NOT EXISTS { ?person wdt:P20   ?placeOfDeath }
  FILTER NOT EXISTS { ?person wdt:P509  ?causeOfDeath }
  FILTER NOT EXISTS { ?person wdt:P1196 ?mannerOfDeath }
  FILTER NOT EXISTS { ?person wdt:P119  ?placeOfBurial }
  OPTIONAL { ?person wdt:P18 ?image }
  OPTIONAL { ?person rdfs:label ?personLabel FILTER(LANG(?personLabel) = "en") }
  OPTIONAL {
    ?article schema:about ?person ;
             schema:isPartOf <https://en.wikipedia.org/> .
  }
}
GROUP BY ?person ?personLabel ?dob ?sitelinks ?image ?article
ORDER BY DESC(?sitelinks)
LIMIT %(limit)d
"""

CURATED_TEMPLATE = QUERY_TEMPLATE.replace(
    "?person wdt:P31 wd:Q5 ;", "VALUES ?person { %(curated)s }\n  ?person wdt:P31 wd:Q5 ;"
)


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

    def __init__(self, session: PoliteSession, endpoint: str,
                 cache_dir: Path | None = None) -> None:
        self.session = session
        self.endpoint = endpoint
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

    def _run_chunked(self, template: str, params: dict) -> list[dict]:
        """Run a query, falling back to smaller occupation chunks on timeout.

        The public query service enforces a 60s budget; the full 67-occupation
        VALUES block occasionally exceeds it on busy days.  Splitting the
        occupation list keeps each query inside the budget at the cost of a few
        extra round trips.
        """
        try:
            return self._run(template % params)
        except WikimediaError as exc:
            message = str(exc).lower()
            if not any(token in message for token in ("timeout", "timed out", "504")):
                raise
            log.warning("SPARQL timed out; retrying in occupation chunks")

        rows: list[dict] = []
        seen: set[str] = set()
        qids = list(ELIGIBLE_QIDS)
        for i in range(0, len(qids), 12):
            chunk = qids[i:i + 12]
            chunk_params = dict(params)
            chunk_params["occupations"] = " ".join(f"wd:{q}" for q in chunk)
            for row in self._run(template % chunk_params):
                key = row["person"]["value"]
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
        return rows

    def candidates_for(self, day: date, min_sitelinks: int, limit: int,
                       curated_ids: list[str] | None = None,
                       refresh: bool = False) -> list[Candidate]:
        cache_file = None
        if self.cache_dir:
            cache_file = self.cache_dir / f"{day.month:02d}-{day.day:02d}_s{min_sitelinks}.json"
            if cache_file.is_file() and not refresh:
                rows = json.loads(cache_file.read_text(encoding="utf-8"))
                log.debug("using cached SPARQL result %s", cache_file.name)
                return self._to_candidates(rows, day, set(curated_ids or []))

        base_params = {
            "occupations": " ".join(f"wd:{q}" for q in ELIGIBLE_QIDS),
            "sitelink_filter": f"FILTER(?sitelinks >= {int(min_sitelinks)})",
            "month": day.month,
            "day": day.day,
            "min_year": day.year - 110,  # nobody older than 110 is in scope
            "limit": int(limit),
        }
        rows = self._run_chunked(QUERY_TEMPLATE, base_params)

        if curated_ids:
            curated_params = dict(base_params)
            curated_params["sitelink_filter"] = ""  # curated people bypass the threshold
            curated_params["curated"] = " ".join(f"wd:{q}" for q in curated_ids)
            curated_params["occupations"] = base_params["occupations"]
            try:
                extra = self._run(CURATED_TEMPLATE % curated_params)
            except WikimediaError as exc:
                log.warning("curated-list query failed for %s: %s", day, exc)
                extra = []
            known = {r["person"]["value"] for r in rows}
            rows.extend(r for r in extra if r["person"]["value"] not in known)

        if cache_file:
            cache_file.write_text(json.dumps(rows), encoding="utf-8")
        return self._to_candidates(rows, day, set(curated_ids or []))

    # -- parsing ------------------------------------------------------------
    @staticmethod
    def _to_candidates(rows: list[dict], day: date, curated: set[str]) -> list[Candidate]:
        out: list[Candidate] = []
        for row in rows:
            qid = _qid(row["person"]["value"])
            label = row.get("personLabel", {}).get("value", "").strip()
            dob = _parse_dob(row.get("dob", {}).get("value", ""))
            if not label or label.startswith("Q") and label[1:].isdigit():
                continue  # no usable English label -- nothing to put on a graphic
            if dob is None or (dob.month, dob.day) != (day.month, day.day):
                continue
            occupations = [
                q for q in row.get("occupations", {}).get("value", "").split(",") if q
            ]
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
