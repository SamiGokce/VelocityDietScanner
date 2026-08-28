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


def test_highlights_are_preserved_better_than_a_symmetric_cutoff():
    """The treatment must not blow out faces on brightly lit press photos.

    Compared against the naive symmetric autocontrast + harder contrast on the
    same input, rather than against a magic number: any image with a bright
    subject clips less under the asymmetric cutoff.
    """
    from PIL import ImageEnhance, ImageOps

    from render.graphic import to_black_and_white

    cfg = load_config().render
    # A photo-like image: a full tonal ramp with a brighter, graded subject in
    # it, the way a lit face sits against a background.
    source = Image.new("RGB", (600, 800))
    pixels = source.load()
    for y in range(800):
        for x in range(600):
            value = 30 + int(160 * y / 800)
            if 180 <= x < 420 and 120 <= y < 420:      # the "subject"
                value += int(60 * x / 600)
            pixels[x, y] = (min(value, 255),) * 3

    def clipped(image):
        histogram = image.convert("L").histogram()
        return sum(histogram[252:]) / sum(histogram)

    ours = to_black_and_white(source, cfg)
    naive = ImageEnhance.Contrast(
        ImageOps.autocontrast(ImageOps.grayscale(source), cutoff=1)
    ).enhance(1.25)

    assert clipped(ours) < clipped(naive)
    assert clipped(ours) < 0.10


def test_contrast_default_stays_below_the_blow_out_point():
    assert load_config().render.contrast <= 1.3
