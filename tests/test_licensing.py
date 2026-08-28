"""Only reusable licences pass, and the credit string is always built."""

import pytest

from scripts.commons import build_attribution, classify_license


@pytest.mark.parametrize("license_id,short_name,expected", [
    ("cc0", "CC0", "cc0"),
    ("cc-by-4.0", "CC BY 4.0", "cc-by"),
    ("cc-by-3.0", "CC BY 3.0", "cc-by"),
    ("cc-by-sa-4.0", "CC BY-SA 4.0", "cc-by-sa"),
    ("cc-by-sa-2.0", "CC BY-SA 2.0", "cc-by-sa"),
    ("pd", "Public domain", "public-domain"),
    ("pd-usgov", "Public domain", "public-domain"),
    (None, None, None),
])
def test_allowed_licences(license_id, short_name, expected):
    assert classify_license(license_id, short_name)[0] == expected


@pytest.mark.parametrize("license_id,short_name", [
    ("cc-by-nc-4.0", "CC BY-NC 4.0"),
    ("cc-by-nc-sa-3.0", "CC BY-NC-SA 3.0"),
    ("cc-by-nd-2.0", "CC BY-ND 2.0"),
    ("fairuse", "Fair use"),
    ("nonfree", "Non-free"),
    (None, "All rights reserved"),
    ("attribution", "Attribution"),          # ambiguous -> rejected, not assumed
    ("some-new-licence", "Weird Licence 9"),  # unknown -> rejected
])
def test_rejected_licences(license_id, short_name):
    assert classify_license(license_id, short_name)[0] is None


def test_noncommercial_beats_the_cc_by_sa_lookalike():
    """CC BY-NC-SA must not be mistaken for CC BY-SA."""
    family, name = classify_license("cc-by-nc-sa-4.0", "CC BY-NC-SA 4.0")
    assert family is None
    assert "NC" in name


def test_attribution_string_carries_author_licence_and_source():
    text = build_attribution(
        filename="Jack Black 2017.jpg",
        artist='<a href="//commons.wikimedia.org/wiki/User:X">Gage Skidmore</a>',
        credit="Own work",
        license_name="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0",
        family="cc-by-sa",
    )
    assert "Gage Skidmore" in text
    assert "<a" not in text and "href" not in text, "HTML must be stripped"
    assert "CC BY-SA 4.0" in text
    assert "creativecommons.org" in text
    assert "commons.wikimedia.org/wiki/File:Jack_Black_2017.jpg" in text


def test_attribution_survives_missing_author():
    text = build_attribution("X.jpg", None, None, "CC0", None, "cc0")
    assert "unknown author" in text
    assert "CC0" in text


def test_personality_rights_are_advisory_not_blocking():
    """Almost every celebrity portrait on Commons carries a personality-rights
    note; treating it as a licence bar would empty the pool."""
    from scripts.commons import ADVISORY_RESTRICTIONS
    assert "personality" in ADVISORY_RESTRICTIONS
    assert "trademarked" not in ADVISORY_RESTRICTIONS
