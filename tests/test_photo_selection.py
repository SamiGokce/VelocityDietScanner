"""Choosing the best photograph, not just accepting or rejecting one.

Wikidata's P18 names a single image. When that one file is too small or badly
licensed, the person used to be dropped -- which meant losing people like
Beyonce over one 900x1200 file while hundreds of usable photographs of them sat
one query away in their Commons category.
"""

import pytest

from scripts.commons import (ImageInfo, looks_like_a_portrait, score_image)

CANVAS = (1080, 1920)


def image(filename, width, height):
    return ImageInfo(
        filename=filename, file_page_url="", image_url="",
        license_family="cc-by-sa", license_name="CC BY-SA 4.0", license_url=None,
        artist="Someone", credit=None, attribution="Photo: Someone, CC BY-SA 4.0",
        width=width, height=height,
    )


# --- weeding out what is not a photograph of the person --------------------

@pytest.mark.parametrize("filename", [
    "Beyonce at the 2018 festival.jpg",
    "Kyrie Irving 2023.png",
    "Some Person portrait.jpeg",
])
def test_real_photographs_are_kept(filename):
    assert looks_like_a_portrait(filename)


@pytest.mark.parametrize("filename", [
    "Beyonce signature.svg",          # not even a photo format
    "Beyonce signature.png",          # a signature, not a portrait
    "Taylor Swift autograph.jpg",
    "Lemonade album cover.jpg",
    "Hollywood Walk of Fame star.jpg",
    "Michael Jordan statue.jpg",
    "Player jersey.jpg",
    "Madame Tussauds wax figure.jpg",
    "Fan tattoo.jpg",
    "Career statistics chart.png",
    "Concert poster.jpg",
    "Interview.webm",                 # video
    "Discography.pdf",
])
def test_non_portraits_are_rejected(filename):
    assert not looks_like_a_portrait(filename)


# --- ranking the usable photographs ----------------------------------------

def test_the_curated_p18_photo_wins_when_it_is_good_enough():
    """Widening the search is a fallback, not a second-guess of every pick."""
    curated = image("Curated 2020.jpg", 2000, 3000)
    other = image("Random 2020.jpg", 2000, 3000)
    assert score_image(curated, CANVAS, is_primary=True) > \
        score_image(other, CANVAS, is_primary=False)


def test_portrait_beats_landscape_for_a_nine_by_sixteen_frame():
    portrait = image("A 2020.jpg", 2000, 3000)
    landscape = image("B 2020.jpg", 3000, 2000)
    assert score_image(portrait, CANVAS) > score_image(landscape, CANVAS)


def test_a_photo_named_after_the_person_beats_an_anonymous_one():
    named = image("Kyrie Irving 2023.jpg", 2000, 3000)
    anon = image("DSC01234 2023.jpg", 2000, 3000)
    assert score_image(named, CANVAS, person_name="Kyrie Irving") > \
        score_image(anon, CANVAS, person_name="Kyrie Irving")


def test_a_recent_photo_beats_a_decades_old_one():
    recent = image("Person 2023.jpg", 2000, 3000)
    old = image("Person 1994.jpg", 2000, 3000)
    assert score_image(recent, CANVAS) > score_image(old, CANVAS)


def test_more_resolution_helps_but_does_not_dominate():
    """A 6000px crowd shot should not beat a well-composed 2500px portrait."""
    huge_landscape = image("Crowd 2023.jpg", 6000, 4000)
    good_portrait = image("Person 2023.jpg", 2000, 3000)
    assert score_image(good_portrait, CANVAS) > score_image(huge_landscape, CANVAS)


def test_resolution_score_saturates():
    big = image("A 2023.jpg", 2160, 3840)
    enormous = image("B 2023.jpg", 8000, 14000)
    # Both are comfortably above the canvas; the difference should be small.
    assert abs(score_image(enormous, CANVAS) - score_image(big, CANVAS)) < 5
