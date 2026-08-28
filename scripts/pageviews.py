"""Wikipedia pageviews as a fame proxy (Wikimedia Pageviews REST API).

Used to rank the candidates that share a birthday.  Missing data is not an
error: a person with no English article, or one whose article is too new for
the API, simply scores 0 from pageviews and is ranked on sitelinks alone.
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import date, timedelta

from common.http import PoliteSession, WikimediaError

log = logging.getLogger(__name__)

PAGEVIEWS_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "{project}/all-access/user/{title}/daily/{start}/{end}"
)


class PageviewsClient:
    def __init__(self, session: PoliteSession, project: str = "en.wikipedia") -> None:
        self.session = session
        self.project = project
        self._cache: dict[str, int] = {}

    def total_views(self, title: str, days: int = 90,
                    end: date | None = None) -> int:
        """Total pageviews for `title` over the trailing `days` window."""
        if not title:
            return 0
        cache_key = f"{title}|{days}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        end_date = (end or date.today()) - timedelta(days=1)  # yesterday: last complete day
        start_date = end_date - timedelta(days=max(1, days))
        url = PAGEVIEWS_URL.format(
            project=self.project,
            title=urllib.parse.quote(title.replace(" ", "_"), safe=""),
            start=start_date.strftime("%Y%m%d"),
            end=end_date.strftime("%Y%m%d"),
        )
        try:
            data = self.session.get_json(url)
        except WikimediaError as exc:
            log.debug("pageviews lookup failed for %r: %s", title, exc)
            self._cache[cache_key] = 0
            return 0
        total = sum(int(item.get("views", 0)) for item in (data or {}).get("items", []))
        self._cache[cache_key] = total
        return total


def notability_score(pageviews: int, sitelinks: int, days: int,
                     pageview_weight: float = 1.0,
                     sitelink_weight: float = 3.0) -> float:
    """Blend pageviews and sitelinks into a single ranking number.

    Average daily pageviews carry the ranking (the spec's preferred proxy);
    sitelinks contribute a small, bounded bonus so that someone with a
    genuinely global profile but a quiet news month is not buried, and so that
    candidates with no pageview data are still ordered sensibly.
    """
    avg_daily = pageviews / max(1, days)
    return round(pageview_weight * avg_daily + sitelink_weight * sitelinks, 3)
