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


def _normalise(title: str) -> str:
    return title.replace("_", " ").strip().removeprefix("File:").strip()


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
