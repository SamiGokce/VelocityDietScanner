"""Font loading.

Trajan is proprietary, so this project uses the open Google Fonts that give the
same classical inscriptional look: Cinzel (closest to Trajan), Cormorant, or
Playfair Display.  `scripts/get_fonts.py` downloads them into assets/fonts/.

Google ships these as variable fonts (`Cinzel[wght].ttf`); a weight is selected
with `set_variation_by_name` where the local FreeType supports it, and
otherwise the font's default instance is used.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

log = logging.getLogger(__name__)


class FontError(RuntimeError):
    pass


@lru_cache(maxsize=64)
def load_font(path: str, size: int, variation: str | None = None) -> ImageFont.FreeTypeFont:
    font_path = Path(path)
    if not font_path.is_file():
        raise FontError(
            f"font not found: {font_path}\n"
            "Run `python scripts/get_fonts.py` to download the open-licensed "
            "display fonts (Cinzel / Cormorant / Playfair Display) into assets/fonts/."
        )
    font = ImageFont.truetype(str(font_path), size=size)
    if variation:
        try:
            font.set_variation_by_name(variation)
        except (OSError, AttributeError, ValueError) as exc:
            log.debug("variation %r unavailable for %s (%s); using default instance",
                      variation, font_path.name, exc)
    return font
