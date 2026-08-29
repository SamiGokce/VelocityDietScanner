"""Wikimedia Commons image licensing.

Only images whose Commons licence is reusable (CC0, public domain, CC BY,
CC BY-SA) are accepted.  Anything NonCommercial, NoDerivatives, fair-use or
otherwise non-free is rejected, and so is anything whose licence we cannot
positively identify -- an unrecognised licence string is a *rejection*, never
a default-allow.

Resolution is checked here too, not at render time.  A photo that has to be
blown up to fill a 1080x1920 frame looks soft or frankly pixelated, and the
Commons P18 pool is bimodal: on a typical day a third of candidates are large
press photos, a third are middling, and the rest are thumbnails as small as
149x224.  Rejecting a soft photo *during sourcing* means the next-ranked person
takes the slot; rejecting it at render time would leave the day short.

Every accepted image carries an attribution string built from the Commons
metadata.  That string is required by CC BY / CC BY-SA and is what goes into
the YouTube description.  It never goes on the rendered frame.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from typing import Any

from common.http import PoliteSession

log = logging.getLogger(__name__)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Licence family -> patterns matched against Commons' machine-readable
# `License` / `LicenseShortName` extmetadata fields (lower-cased).
LICENSE_FAMILIES: dict[str, tuple[re.Pattern[str], ...]] = {
    "cc0": (
        re.compile(r"\bcc0\b"),
        re.compile(r"creative\s*commons\s*zero"),
    ),
    "public-domain": (
        re.compile(r"^pd(\b|[-_])"),
        re.compile(r"\bpublic\s*domain\b"),
        re.compile(r"\bpd-\w+"),
    ),
    "cc-by-sa": (
        re.compile(r"\bcc[-\s]?by[-\s]?sa\b"),
    ),
    "cc-by": (
        re.compile(r"\bcc[-\s]?by\b"),
    ),
}

# Hard rejections, checked before the allow-list.  CC BY-NC-SA contains
# "cc-by-sa"-ish text, so these must win.
FORBIDDEN = (
    re.compile(r"[-\s]nc\b"),          # NonCommercial
    re.compile(r"noncommercial"),
    re.compile(r"[-\s]nd\b"),          # NoDerivatives
    re.compile(r"noderiv"),
    re.compile(r"fair\s*use"),
    re.compile(r"non[-\s]?free"),
    re.compile(r"\ball rights reserved\b"),
    re.compile(r"copyrighted"),
)

# Commons' "Restrictions" field flags non-copyright constraints.  Most photos of
# living public figures carry `personality` (a personality-rights reminder):
# that is a note to the reuser, not a licence limit, and rejecting it would
# throw away nearly every usable celebrity portrait.  It is recorded as a
# warning instead.  The rest -- trademarks, insignia, currency, costumes,
# protected designs -- genuinely constrain reuse, so those files are skipped.
ADVISORY_RESTRICTIONS = {"personality"}

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


class LicenseRejected(Exception):
    """The image exists but cannot be used under the sourcing rules."""


class ImageTooSmall(Exception):
    """The image is openly licensed but too low-resolution to render well."""


@dataclass
class ImageInfo:
    filename: str
    file_page_url: str
    image_url: str
    license_family: str
    license_name: str
    license_url: str | None
    artist: str | None
    credit: str | None
    attribution: str
    width: int = 0
    height: int = 0
    warnings: tuple[str, ...] = ()


def upscale_factor(width: int, height: int, canvas_width: int, canvas_height: int) -> float:
    """How much a photo must be enlarged to cover the canvas.

    1.0 means it fills the frame exactly; below 1.0 it is downscaled (always
    crisp); above 1.0 pixels are being invented.  This is the number that
    matters, not the raw pixel count: a 4000x1200 panorama has plenty of pixels
    and still cannot fill a 9:16 frame without a 1.6x blow-up.
    """
    if width <= 0 or height <= 0:
        return float("inf")
    return max(canvas_width / width, canvas_height / height)


def _clean(value: str | None) -> str | None:
    """Commons metadata is HTML; flatten it to a plain string."""
    if not value:
        return None
    text = TAG_RE.sub(" ", value)
    text = html.unescape(text)
    text = WS_RE.sub(" ", text).strip(" ,;|")
    return text or None


def classify_license(license_id: str | None, short_name: str | None,
                     usage_terms: str | None = None) -> tuple[str | None, str]:
    """Return (allowed_family, human_readable_name).

    `allowed_family` is None when the image must not be used.
    """
    name = _clean(short_name) or _clean(license_id) or _clean(usage_terms) or "unknown"
    haystack = " ".join(
        part.lower() for part in (license_id or "", short_name or "", usage_terms or "") if part
    )
    haystack = _clean(haystack) or ""
    if not haystack:
        return None, name
    for pattern in FORBIDDEN:
        if pattern.search(haystack):
            return None, name
    for family, patterns in LICENSE_FAMILIES.items():
        if any(p.search(haystack) for p in patterns):
            return family, name
    return None, name


def build_attribution(filename: str, artist: str | None, credit: str | None,
                      license_name: str, license_url: str | None,
                      family: str) -> str:
    """The credit line that goes in the YouTube description.

    Shape: "Photo: <author> / <source>, <licence> (<licence url>) via Wikimedia
    Commons: <file page>" -- trimmed of whatever Commons did not supply.
    """
    parts: list[str] = []
    author = _clean(artist)
    if author:
        parts.append(f"Photo: {author}")
    else:
        parts.append("Photo: unknown author")
    source = _clean(credit)
    if source and source.lower() not in {"own work", (author or "").lower()}:
        parts.append(source)
    if family == "public-domain":
        parts.append(f"{license_name} (public domain)")
    elif family == "cc0":
        parts.append(f"{license_name} (no rights reserved)")
    else:
        parts.append(license_name)
    if license_url:
        parts.append(license_url)
    file_page = "https://commons.wikimedia.org/wiki/File:" + filename.replace(" ", "_")
    parts.append(f"via Wikimedia Commons — {file_page}")
    return ", ".join(p for p in parts if p)


#: Commons' API accepts up to 50 titles per query for normal accounts.
BATCH_SIZE = 40

#: Only real photographs are usable; Commons categories are full of other things.
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png")

#: Files whose names say they are not a portrait of the person.  A Commons
#: category for a famous person contains their signature, album art, a star on
#: the Walk of Fame, memorabilia and fan tattoos alongside the actual photos.
#: Excluding these by name is crude but cheap, and there are always plenty of
#: real photographs left to choose from.
NOT_A_PORTRAIT = re.compile(
    r"\b("
    r"signature|autograph|logo|coat[ _]of[ _]arms|emblem|crest|"
    r"album|single|cover|poster|billboard|advert|"
    r"walk[ _]of[ _]fame|plaque|grave|tomb|headstone|memorial|"
    r"statue|sculpture|bust|wax|figurine|mural|graffiti|tattoo|"
    r"stamp|coin|banknote|ticket|book|dvd|vinyl|cassette|"
    r"map|chart|diagram|graph|screenshot|scan|timeline|"
    r"jersey|shirt|boots|glove|helmet|trophy|medal|award[ _]statue|"
    r"handprint|footprint|mosaic|painting|drawing|caricature|artwork"
    r")\b",
    re.IGNORECASE,
)

#: A four-digit year in a filename, used to prefer a recent photograph.
YEAR_IN_NAME = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def _normalise(title: str) -> str:
    return title.replace("_", " ").strip().removeprefix("File:").strip()


def looks_like_a_portrait(filename: str) -> bool:
    """Cheap name-based filter, applied before spending an API call on a file."""
    name = _normalise(filename)
    if not name.lower().endswith(PHOTO_EXTENSIONS):
        return False
    return not NOT_A_PORTRAIT.search(name)


def score_image(info: "ImageInfo", canvas: tuple[int, int], *,
                is_primary: bool = False, person_name: str = "") -> float:
    """Rank usable photos against each other for a 9:16 full-bleed frame.

    Every candidate here has already passed the licence and resolution gates,
    so this is purely about which one will *look* best once cropped.
    """
    score = 0.0

    # Wikidata's own P18 pick is the community's chosen portrait. Prefer it
    # when it is good enough -- widening the search is a fallback, not a
    # second-guess of every curated choice.
    if is_primary:
        score += 45.0

    # Resolution headroom: full marks once the photo is twice the canvas, so a
    # 6000px press shot does not automatically beat a well-composed 2500px one.
    scale = upscale_factor(info.width, info.height, *canvas)
    if scale > 0:
        score += 30.0 * min(1.0, (1.0 / scale) / 2.0)

    # Aspect: the frame is 9:16, so a portrait crops beautifully and a wide
    # landscape loses most of its sides (and often the subject with them).
    ratio = info.height / info.width if info.width else 0
    if ratio >= 1.2:
        score += 30.0
    elif ratio >= 0.95:
        score += 14.0
    elif ratio >= 0.75:
        score += 4.0

    # A file named after the person is far more likely to actually show them
    # than a crowd shot that happens to sit in their category.
    surname = (person_name or "").split()[-1].lower() if person_name else ""
    if surname and len(surname) > 2 and surname in _normalise(info.filename).lower():
        score += 16.0

    # Prefer a recent likeness: a birthday post wants the person as they look
    # now, not a press shot from three decades ago.
    years = [int(y) for y in YEAR_IN_NAME.findall(_normalise(info.filename))]
    if years:
        newest = max(years)
        if newest >= 2018:
            score += 12.0
        elif newest >= 2012:
            score += 6.0

    return round(score, 2)


class CommonsClient:
    def __init__(self, session: PoliteSession, allowed_families: tuple[str, ...],
                 canvas: tuple[int, int] = (1080, 1920), min_width: int = 1000,
                 min_height: int = 1200, max_upscale: float = 1.25) -> None:
        self.session = session
        self.allowed = tuple(allowed_families)
        self.canvas = canvas
        self.min_width = min_width
        self.min_height = min_height
        self.max_upscale = max_upscale

    # -- fetching -----------------------------------------------------------
    def _query(self, titles: list[str]) -> dict[str, dict]:
        """One API call for up to BATCH_SIZE files -> {normalised title: page}."""
        data: Any = self.session.get_json(COMMONS_API, params={
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "titles": "|".join(titles),
            "redirects": 1,
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata|mime",
            "iiextmetadatafilter": (
                "License|LicenseShortName|LicenseUrl|UsageTerms|Artist|Credit|"
                "Attribution|AttributionRequired|Restrictions"
            ),
        })
        query = (data or {}).get("query", {})
        pages = {_normalise(p.get("title", "")): p for p in query.get("pages", [])}
        # Follow the normalisation/redirect maps so a renamed file still resolves.
        for mapping in ("normalized", "redirects"):
            for entry in query.get(mapping, []) or []:
                target = pages.get(_normalise(entry.get("to", "")))
                if target is not None:
                    pages.setdefault(_normalise(entry.get("from", "")), target)
        return pages

    def image_info(self, filename: str) -> ImageInfo:
        """Fetch, licence-check and resolution-check one Commons file."""
        return self.image_info_batch([filename])[_normalise(filename)]

    def image_info_batch(
        self, filenames: list[str]
    ) -> dict[str, "ImageInfo | LicenseRejected | ImageTooSmall"]:
        """Look up many files at once.

        Returns a result per requested name: an ImageInfo, or the exception
        explaining why that file cannot be used.  Batching matters -- Wikimedia
        throttles hard, and one request for a day's whole shortlist is the
        difference between seconds and minutes.
        """
        results: dict[str, ImageInfo | LicenseRejected | ImageTooSmall] = {}
        wanted = [f for f in filenames if f]
        for start in range(0, len(wanted), BATCH_SIZE):
            batch = wanted[start:start + BATCH_SIZE]
            pages = self._query(["File:" + _normalise(f) for f in batch])
            for filename in batch:
                page = pages.get(_normalise(filename))
                try:
                    results[_normalise(filename)] = self._build(filename, page)
                except (LicenseRejected, ImageTooSmall) as exc:
                    results[_normalise(filename)] = exc
        return results

    # -- finding other photographs of the same person -----------------------
    def category_files(self, category: str, limit: int = 80) -> list[str]:
        """List photo files in a Commons category (P373).

        This is the whole point of the widened search: P18 names one photo,
        but the category holds every free photograph of the person.
        """
        title = category.strip()
        if not title.lower().startswith("category:"):
            title = "Category:" + title
        try:
            data: Any = self.session.get_json(COMMONS_API, params={
                "action": "query", "format": "json", "formatversion": "2",
                "list": "categorymembers", "cmtitle": title,
                "cmtype": "file", "cmlimit": min(int(limit), 500),
            })
        except WikimediaError as exc:
            log.debug("category listing failed for %r: %s", title, exc)
            return []
        members = (data or {}).get("query", {}).get("categorymembers", []) or []
        return [
            _normalise(m.get("title", "")) for m in members
            if looks_like_a_portrait(m.get("title", ""))
        ]

    def search_files(self, person_name: str, limit: int = 30) -> list[str]:
        """Full-text Commons search, for people with no Commons category."""
        if not person_name.strip():
            return []
        try:
            data: Any = self.session.get_json(COMMONS_API, params={
                "action": "query", "format": "json", "formatversion": "2",
                "list": "search", "srsearch": person_name.strip(),
                "srnamespace": 6, "srlimit": min(int(limit), 50),
            })
        except WikimediaError as exc:
            log.debug("Commons search failed for %r: %s", person_name, exc)
            return []
        hits = (data or {}).get("query", {}).get("search", []) or []
        return [
            _normalise(h.get("title", "")) for h in hits
            if looks_like_a_portrait(h.get("title", ""))
        ]

    def subcategory_files(self, category: str, max_subcats: int = 6,
                          per_subcat: int = 25) -> list[str]:
        """Files one level down, for names split across dated subcategories.

        Commons files a heavily photographed person under subcategories
        ("X in 2019", "X at the Grammy Awards") and leaves the parent category
        holding no files at all -- exactly the people this pipeline most wants.
        """
        title = category.strip()
        if not title.lower().startswith("category:"):
            title = "Category:" + title
        try:
            data: Any = self.session.get_json(COMMONS_API, params={
                "action": "query", "format": "json", "formatversion": "2",
                "list": "categorymembers", "cmtitle": title,
                "cmtype": "subcat", "cmlimit": min(int(max_subcats), 50),
            })
        except WikimediaError:
            return []
        subcats = [
            m.get("title", "")
            for m in (data or {}).get("query", {}).get("categorymembers", []) or []
        ]
        found: list[str] = []
        for subcat in subcats[:max_subcats]:
            found.extend(self.category_files(subcat, limit=per_subcat))
        return found

    def person_photo_candidates(self, person_name: str,
                                commons_category: str | None = None,
                                extra: tuple[str, ...] = ()) -> list[str]:
        """Every free photograph we can find of one person, best sources first."""
        candidates: list[str] = [name for name in extra if name]
        if commons_category:
            candidates += self.category_files(commons_category)
            if len(candidates) < 8:
                candidates += self.subcategory_files(commons_category)
        if len(candidates) < 8:
            candidates += self.search_files(person_name)

        seen, unique = set(), []
        for name in candidates:
            key = _normalise(name).lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(name)
        return unique

    def best_image(self, filenames: list[str], *, primary: tuple[str, ...] = (),
                   person_name: str = "") -> ImageInfo:
        """Return the best usable photo among `filenames`.

        Raises the most informative rejection when nothing is usable, so the
        review log still says *why* a person could not be featured.
        """
        wanted, seen = [], set()
        for name in filenames:
            key = _normalise(name).lower()
            if name and key not in seen and looks_like_a_portrait(name):
                seen.add(key)
                wanted.append(name)
        if not wanted:
            raise LicenseRejected("no candidate photographs to check")

        results = self.image_info_batch(wanted)
        primary_keys = {_normalise(p).lower() for p in primary}

        usable: list[tuple[float, ImageInfo]] = []
        failures: list[Exception] = []
        for key, result in results.items():
            if isinstance(result, ImageInfo):
                usable.append((
                    score_image(result, self.canvas,
                                is_primary=key.lower() in primary_keys,
                                person_name=person_name),
                    result,
                ))
            else:
                failures.append(result)

        if not usable:
            # Prefer a resolution complaint over a licence one: it is the more
            # actionable message, and the more common reason on Commons.
            for failure in failures:
                if isinstance(failure, ImageTooSmall):
                    raise failure
            raise failures[0] if failures else LicenseRejected("no usable photograph found")

        usable.sort(key=lambda pair: (-pair[0], -pair[1].width * pair[1].height))
        best_score, best = usable[0]
        log.debug("chose %s (score %.1f) from %d usable of %d candidates",
                  best.filename, best_score, len(usable), len(wanted))
        return best

    def _build(self, filename: str, page: dict | None) -> ImageInfo:
        title = "File:" + _normalise(filename)
        if not page or page.get("missing"):
            raise LicenseRejected(f"file not found on Commons: {title}")
        info_list = page.get("imageinfo") or []
        if not info_list:
            raise LicenseRejected(f"no imageinfo for {title}")
        info = info_list[0]
        meta = {k: v.get("value") for k, v in (info.get("extmetadata") or {}).items()}

        mime = info.get("mime", "")
        if not mime.startswith("image/"):
            raise LicenseRejected(f"{title} is not an image ({mime})")

        width, height = int(info.get("width") or 0), int(info.get("height") or 0)
        scale = upscale_factor(width, height, *self.canvas)
        if width < self.min_width or height < self.min_height:
            raise ImageTooSmall(
                f"{title} is {width}x{height}; minimum is "
                f"{self.min_width}x{self.min_height}"
            )
        if scale > self.max_upscale:
            raise ImageTooSmall(
                f"{title} is {width}x{height} and would need a {scale:.2f}x "
                f"enlargement to fill {self.canvas[0]}x{self.canvas[1]} "
                f"(limit {self.max_upscale:.2f}x)"
            )

        warnings: list[str] = []
        restrictions = [
            token.strip().lower()
            for token in re.split(r"[|,;]", _clean(meta.get("Restrictions")) or "")
            if token.strip()
        ]
        blocking = [r for r in restrictions if r not in ADVISORY_RESTRICTIONS]
        if blocking:
            raise LicenseRejected(
                f"{title} carries reuse restrictions: {', '.join(blocking)}"
            )
        if restrictions:
            warnings.append(
                "Commons notes " + ", ".join(restrictions) + " rights on this file"
            )

        family, license_name = classify_license(
            meta.get("License"), meta.get("LicenseShortName"), meta.get("UsageTerms")
        )
        if family is None:
            raise LicenseRejected(f"{title}: licence not reusable ({license_name})")
        if family not in self.allowed:
            raise LicenseRejected(
                f"{title}: licence {license_name} ({family}) not in allowed list {self.allowed}"
            )

        attribution = build_attribution(
            filename=_normalise(page["title"]),
            artist=meta.get("Artist") or meta.get("Attribution"),
            credit=meta.get("Credit"),
            license_name=license_name,
            license_url=_clean(meta.get("LicenseUrl")),
            family=family,
        )
        return ImageInfo(
            filename=_normalise(page["title"]),
            file_page_url=info.get("descriptionurl", ""),
            image_url=info.get("url", ""),
            license_family=family,
            license_name=license_name,
            license_url=_clean(meta.get("LicenseUrl")),
            artist=_clean(meta.get("Artist")),
            credit=_clean(meta.get("Credit")),
            attribution=attribution,
            width=width,
            height=height,
            warnings=tuple(warnings),
        )
