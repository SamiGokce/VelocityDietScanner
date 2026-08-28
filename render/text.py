"""Letter-tracked, centred display type, and the three lines of the layout.

`overlay_lines()` is the single place that decides what words appear on a
frame.  It returns exactly three strings and nothing else -- no attribution, no
watermark, no credit.  A test asserts that (see tests/test_overlay_text.py):
attribution for CC BY / CC BY-SA photos is satisfied in the YouTube
description, never on the image.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import ImageDraw, ImageFont

from common.ordinals import birthday_line, year_line


@dataclass(frozen=True)
class OverlayText:
    top: str      # HAPPY 41ST BIRTHDAY
    name: str     # JACK BLACK
    bottom: str   # 1969 - PRESENT

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.top, self.name, self.bottom)


def overlay_lines(full_name: str, age_turning: int, birth_year: int) -> OverlayText:
    """The complete set of words that may appear on a rendered frame."""
    name = " ".join(full_name.split()).upper()
    if not name:
        raise ValueError("full_name is empty")
    return OverlayText(
        top=birthday_line(age_turning),
        name=name,
        bottom=year_line(birth_year),
    )


# --- tracked text metrics/drawing ------------------------------------------

def tracking_px(font: ImageFont.FreeTypeFont, tracking_em: float) -> float:
    """Convert tracking expressed in ems to pixels for this font size."""
    return font.size * tracking_em


def text_width(font: ImageFont.FreeTypeFont, text: str, tracking: float) -> float:
    """Width of `text` drawn glyph-by-glyph with `tracking` px between glyphs."""
    if not text:
        return 0.0
    return sum(font.getlength(ch) for ch in text) + tracking * (len(text) - 1)


def line_height(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return ascent + descent


def draw_tracked(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str,
                 font: ImageFont.FreeTypeFont, tracking: float,
                 fill: tuple[int, int, int, int]) -> None:
    """Draw `text` centred on x=xy[0], with its top at y=xy[1]."""
    x = xy[0] - text_width(font, text, tracking) / 2
    y = xy[1]
    for char in text:
        draw.text((x, y), char, font=font, fill=fill, anchor="la")
        x += font.getlength(char) + tracking


def wrap_to_width(text: str, font: ImageFont.FreeTypeFont, tracking: float,
                  max_width: float, max_lines: int = 3) -> list[str] | None:
    """Greedy word wrap; None when the text cannot fit in `max_lines`."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if text_width(font, trial, tracking) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
        if len(lines) > max_lines:
            return None
    if current:
        lines.append(current)
    if len(lines) > max_lines or any(
        text_width(font, line, tracking) > max_width for line in lines
    ):
        return None
    return lines
