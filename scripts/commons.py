"""Wikimedia Commons image licensing.

Only images whose Commons licence is reusable (CC0, public domain, CC BY,
CC BY-SA) are accepted.  Anything NonCommercial, NoDerivatives, fair-use or
otherwise non-free is rejected, and so is anything whose licence we cannot
positively identify -- an unrecognised licence string is a *rejection*, never
a default-allow.

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


@dataclass
class ImageInfo:
    filename: str
    file_page_url: str
    image_url: str
    thumb_url: str | None
    license_family: str
    license_name: str
    license_url: str | None
    artist: str | None
    credit: str | None
    attribution: str
    width: int = 0
    height: int = 0
    warnings: tuple[str, ...] = ()


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


class CommonsClient:
    def __init__(self, session: PoliteSession, allowed_families: tuple[str, ...],
                 thumb_width: int = 1600) -> None:
        self.session = session
        self.allowed = tuple(allowed_families)
        self.thumb_width = thumb_width

    def image_info(self, filename: str) -> ImageInfo:
        """Fetch and licence-check one Commons file. Raises LicenseRejected."""
        title = "File:" + filename.lstrip("File:").strip()
        data: Any = self.session.get_json(COMMONS_API, params={
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata|mime",
            "iiurlwidth": self.thumb_width,
            "iiextmetadatafilter": (
                "License|LicenseShortName|LicenseUrl|UsageTerms|Artist|Credit|"
                "Attribution|AttributionRequired|Restrictions"
            ),
        })
        pages = (data or {}).get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            raise LicenseRejected(f"file not found on Commons: {title}")
        info_list = pages[0].get("imageinfo") or []
        if not info_list:
            raise LicenseRejected(f"no imageinfo for {title}")
        info = info_list[0]
        meta = {k: v.get("value") for k, v in (info.get("extmetadata") or {}).items()}

        mime = info.get("mime", "")
        if not mime.startswith("image/"):
            raise LicenseRejected(f"{title} is not an image ({mime})")

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
            filename=pages[0]["title"].split(":", 1)[-1],
            artist=meta.get("Artist") or meta.get("Attribution"),
            credit=meta.get("Credit"),
            license_name=license_name,
            license_url=_clean(meta.get("LicenseUrl")),
            family=family,
        )
        return ImageInfo(
            filename=pages[0]["title"].split(":", 1)[-1],
            file_page_url=info.get("descriptionurl", ""),
            image_url=info.get("url", ""),
            thumb_url=info.get("thumburl"),
            license_family=family,
            license_name=license_name,
            license_url=_clean(meta.get("LicenseUrl")),
            artist=_clean(meta.get("Artist")),
            credit=_clean(meta.get("Credit")),
            attribution=attribution,
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
            warnings=tuple(warnings),
        )
