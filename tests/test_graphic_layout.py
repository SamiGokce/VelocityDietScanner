"""Canvas, crop, vignette and type hierarchy."""

from PIL import Image

from common.config import load_config
from render.graphic import (apply_vignette, cover_crop, layout_block,
                            render_frame, vignette_mask)
from render.text import overlay_lines


def photo(w=1400, h=1000, colour=(120, 130, 140)):
    return Image.new("RGB", (w, h), colour)


def test_frame_is_1080x1920():
    frame = render_frame(photo(), "Zendaya", 30, 1996, load_config().render)
    assert frame.size == (1080, 1920)


def test_cover_crop_fills_the_frame_from_any_aspect_ratio():
    for size in [(4000, 2000), (500, 4000), (1080, 1920), (600, 600)]:
        assert cover_crop(photo(*size), 1080, 1920).size == (1080, 1920)


def test_full_bleed_no_letterboxing():
    """A uniform photo must reach every edge -- no borders, no negative space."""
    cfg = load_config().render
    frame = render_frame(photo(2000, 1500, (128, 128, 128)), "Test Person", 40, 1986, cfg)
    px = frame.load()
    for x in (0, 539, 1079):
        assert px[x, 0] != (0, 0, 0), "top edge should be photo, not padding"
    for y in (0, 500, 1000):
        assert px[0, y] == px[1079, y], "left and right edges should match a flat photo"


def test_vignette_is_transparent_at_the_top_and_darkens_downwards():
    mask = vignette_mask(100, 1000, start=0.6, opacity=0.18)
    px = mask.load()
    assert px[50, 0] == 0
    assert px[50, 500] == 0            # still nothing at the halfway point
    assert px[50, 599] == 0            # transparent right up to 60%
    assert 0 < px[50, 800] < px[50, 999]
    assert px[50, 999] <= int(255 * 0.18) + 1


def test_vignette_darkens_the_bottom_of_a_flat_photo():
    cfg = load_config().render
    flat = Image.new("RGB", (1080, 1920), (200, 200, 200))
    shaded = apply_vignette(flat, cfg)
    assert shaded.getpixel((10, 10)) == (200, 200, 200)
    assert shaded.getpixel((10, 1900))[0] < 200


def test_name_is_the_largest_element():
    cfg = load_config().render
    block = layout_block(overlay_lines("Jack Black", 57, 1969), cfg, 1080 - 2 * cfg.side_margin)
    assert block.name_font_size >= 3 * block.top_font_size
    assert block.name_font_size <= 4 * block.top_font_size


def test_long_names_shrink_and_wrap_instead_of_overflowing():
    cfg = load_config().render
    long_name = overlay_lines("Alexandria Ocasio-Cortez Fitzwilliam", 40, 1986)
    block = layout_block(long_name, cfg, 1080 - 2 * cfg.side_margin)
    assert len(block.name_lines) >= 2
    assert block.name_font_size <= cfg.name_max_size
    assert block.name_font_size >= cfg.name_min_size


def test_text_block_sits_in_the_bottom_third():
    cfg = load_config().render
    block = layout_block(overlay_lines("Zendaya", 30, 1996), cfg, 1080 - 2 * cfg.side_margin)
    top = cfg.height - cfg.block_bottom_margin - block.height
    assert top > cfg.height * 0.55, "block should not climb into the middle of the frame"
    assert top + block.height < cfg.height


def test_photo_is_desaturated():
    colourful = Image.new("RGB", (1200, 1600), (200, 40, 40))
    frame = render_frame(colourful, "Test Person", 40, 1986, load_config().render)
    r, g, b = frame.getpixel((20, 20))
    assert r == g == b, "output must be black and white, never colourised"
