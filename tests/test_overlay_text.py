"""The rendered frame must carry three lines and nothing else.

CC BY / CC BY-SA attribution is a legal requirement, but the spec puts it in
the YouTube description, never on the image.  These tests pin both halves of
that rule: the overlay contains exactly the three intended lines, and the
renderer physically draws nothing else.
"""

from PIL import Image, ImageDraw

import render.graphic as graphic
from common.config import load_config
from render.text import overlay_lines

ATTRIBUTION = "Photo: Gage Skidmore, CC BY-SA 4.0, via Wikimedia Commons"


def test_overlay_is_exactly_three_lines():
    text = overlay_lines("Jack Black", 57, 1969)
    assert text.as_tuple() == ("HAPPY 57TH BIRTHDAY", "JACK BLACK", "1969 – PRESENT")


def test_overlay_uppercases_and_normalises_whitespace():
    assert overlay_lines("  jean-claude   van damme ", 66, 1960).name == "JEAN-CLAUDE VAN DAMME"


def test_renderer_draws_only_the_three_lines(monkeypatch):
    """Capture every string the renderer draws and check the complete set."""
    drawn: list[str] = []
    original = graphic.draw_tracked

    def spy(draw, xy, text, font, tracking, fill):
        drawn.append(text)
        return original(draw, xy, text, font, tracking, fill)

    monkeypatch.setattr(graphic, "draw_tracked", spy)

    cfg = load_config().render
    photo = Image.new("RGB", (1200, 1600), (90, 90, 90))
    ImageDraw.Draw(photo).ellipse([300, 200, 900, 800], fill=(220, 220, 220))
    graphic.render_frame(photo, "Jack Black", 57, 1969, cfg)

    # The name may be split across lines, so compare on joined words.
    assert " ".join(drawn) == "HAPPY 57TH BIRTHDAY JACK BLACK 1969 – PRESENT"
    joined = " ".join(drawn)
    for forbidden in ("Photo", "CC BY", "Commons", "©", "Wikimedia", ATTRIBUTION):
        assert forbidden not in joined


def test_render_frame_takes_no_attribution_argument():
    """A frame cannot accidentally grow a credit line: it is never passed one."""
    import inspect
    params = set(inspect.signature(graphic.render_frame).parameters)
    assert params == {"photo", "full_name", "age_turning", "birth_year", "cfg"}
