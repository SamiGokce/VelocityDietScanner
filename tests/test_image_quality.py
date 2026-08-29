"""The resolution gate: no pixelated or upscaled-to-mush photos.

Commons' P18 pool ranges from 2600px press photos down to 149x224 thumbnails.
Filling a 1080x1920 frame from the small end means inventing most of the
pixels, so resolution is checked while sourcing -- a rejected photo costs that
person their slot, not the day's output.
"""

import pytest

from common.config import load_config
from render.generate import thumbnail_width
from scripts.commons import (ImageTooSmall, LicenseRejected, upscale_factor)

CANVAS = (1080, 1920)


# --- the metric ------------------------------------------------------------

@pytest.mark.parametrize("width,height,expected", [
    (1080, 1920, 1.0),        # exactly fills the frame
    (2160, 3840, 0.5),        # twice the frame: downscaled, always crisp
    (2666, 4000, 0.48),       # a real Commons press photo
    (580, 800, 2.40),         # a real Commons thumbnail -- visibly pixelated
    (149, 224, 8.57),         # also real, and unusable
    (4000, 1200, 1.6),        # plenty of pixels, but not enough height for 9:16
])
def test_upscale_factor(width, height, expected):
    assert upscale_factor(width, height, *CANVAS) == pytest.approx(expected, abs=0.01)


def test_upscale_factor_of_a_degenerate_image_is_infinite():
    assert upscale_factor(0, 0, *CANVAS) == float("inf")


def test_a_wide_photo_is_judged_on_the_crop_not_the_pixel_count():
    """A 4000x1200 panorama has more pixels than 1080x1920 and still fails."""
    assert upscale_factor(4000, 1200, *CANVAS) > 1.0
    assert 4000 * 1200 > 1080 * 1920


# --- the gate --------------------------------------------------------------

def build(width, height, mime="image/jpeg"):
    return {
        "title": f"File:Test {width}x{height}.jpg",
        "imageinfo": [{
            "width": width, "height": height, "mime": mime,
            "url": "https://upload.wikimedia.org/test.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Test.jpg",
            "extmetadata": {
                "License": {"value": "cc-by-sa-4.0"},
                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                "Artist": {"value": "A Photographer"},
            },
        }],
    }


@pytest.fixture
def client():
    from scripts.commons import CommonsClient
    cfg = load_config()
    return CommonsClient(
        session=None,
        allowed_families=cfg.sourcing.allowed_licenses,
        canvas=CANVAS,
        min_width=cfg.sourcing.min_image_width,
        min_height=cfg.sourcing.min_image_height,
        max_upscale=cfg.sourcing.max_upscale,
    )


def test_a_large_press_photo_passes(client):
    info = client._build("Test.jpg", build(2666, 4000))
    assert info.width == 2666 and info.height == 4000
    assert info.license_family == "cc-by-sa"


def test_a_thumbnail_is_rejected(client):
    with pytest.raises(ImageTooSmall, match="580x800"):
        client._build("Test.jpg", build(580, 800))


def test_a_tiny_image_is_rejected(client):
    with pytest.raises(ImageTooSmall):
        client._build("Test.jpg", build(149, 224))


def test_a_photo_needing_a_big_enlargement_is_rejected_even_above_the_floor(client):
    """1044x1484 clears the pixel floor but still needs a 1.29x blow-up."""
    with pytest.raises(ImageTooSmall, match="enlargement"):
        client._build("Test.jpg", build(1044, 1484))


def test_the_rejection_message_says_why(client):
    with pytest.raises(ImageTooSmall) as excinfo:
        client._build("Test.jpg", build(800, 1000))
    assert "800x1000" in str(excinfo.value)


def test_resolution_is_checked_before_the_licence_matters(client):
    """Both are disqualifying; neither should mask the other."""
    with pytest.raises((ImageTooSmall, LicenseRejected)):
        client._build("Test.jpg", build(200, 300))


def test_a_borderline_photo_passes_at_the_configured_limit(client):
    # 1200x1600 needs exactly 1.20x -- inside the 1.25 default
    assert client._build("Test.jpg", build(1200, 1600)).height == 1600


# --- asking Commons for enough pixels --------------------------------------

@pytest.mark.parametrize("source,target,expected", [
    ((2666, 4000), (2160, 3840), 2560),   # portrait: enough for a 2x layer
    ((2666, 4000), (1080, 1920), 1280),   # stills need less
    ((4000, 2000), (2160, 3840), 4000),   # landscape: height binds, take it all
    ((1200, 1600), (2160, 3840), 1200),   # never ask for more than exists
])
def test_thumbnail_width_asks_for_exactly_enough(source, target, expected):
    assert thumbnail_width(*source, *target) == expected


def test_thumbnail_width_covers_the_target_frame():
    """Whatever it asks for must actually cover the frame when it can."""
    source_width, source_height = 3000, 4500
    target = (2160, 3840)
    width = thumbnail_width(source_width, source_height, *target)
    height = round(width * source_height / source_width)
    assert upscale_factor(width, height, *target) <= 1.001


def test_unknown_source_dimensions_ask_generously():
    assert thumbnail_width(0, 0, 1080, 1920) == 2160


# --- guards against pathologically large files ------------------------------

def test_a_normal_large_press_photo_is_fine(client):
    """45MP originals are common and cost nothing: we fetch a thumbnail."""
    assert client._build("Test.jpg", build(5508, 8256)).width == 5508


def test_an_absurdly_large_file_is_refused(client):
    """Commons cannot reliably thumbnail these, and Pillow refuses ~89MP+."""
    with pytest.raises(ImageTooSmall, match="MP"):
        client._build("Test.jpg", build(12000, 9000))   # 108MP


def test_the_megapixel_ceiling_sits_under_pillows_bomb_limit():
    from PIL import Image
    from common.config import load_config
    ceiling = load_config().sourcing.max_image_megapixels
    assert ceiling * 1e6 < Image.MAX_IMAGE_PIXELS


def test_download_ceiling_is_configured():
    from common.config import load_config
    assert load_config().sourcing.max_download_mb >= 10
