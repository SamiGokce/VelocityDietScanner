"""The ordinal suffix and the year line -- the two most public-facing details."""

import pytest

from common.ordinals import (EN_DASH, birthday_line, ordinal, ordinal_suffix,
                             year_line)


@pytest.mark.parametrize("n,expected", [
    (1, "ST"), (2, "ND"), (3, "RD"), (4, "TH"), (5, "TH"), (9, "TH"), (10, "TH"),
    # the classic trap: 11, 12, 13 are TH, not ST/ND/RD
    (11, "TH"), (12, "TH"), (13, "TH"), (14, "TH"),
    (21, "ST"), (22, "ND"), (23, "RD"), (24, "TH"),
    (30, "TH"), (31, "ST"), (32, "ND"), (33, "RD"),
    (40, "TH"), (41, "ST"), (50, "TH"), (60, "TH"), (64, "TH"), (65, "TH"),
    (100, "TH"), (101, "ST"), (102, "ND"), (103, "RD"),
    # ... and every hundred repeats the trap
    (111, "TH"), (112, "TH"), (113, "TH"), (121, "ST"), (212, "TH"), (1013, "TH"),
])
def test_ordinal_suffix(n, expected):
    assert ordinal_suffix(n) == expected


def test_every_age_a_living_person_could_plausibly_turn():
    """No age from 1 to 122 may produce a suffix that reads wrong."""
    for age in range(1, 123):
        suffix = ordinal_suffix(age)
        assert suffix in {"ST", "ND", "RD", "TH"}
        if age % 100 in (11, 12, 13):
            assert suffix == "TH", age
        elif age % 10 == 1:
            assert suffix == "ST", age
        elif age % 10 == 2:
            assert suffix == "ND", age
        elif age % 10 == 3:
            assert suffix == "RD", age
        else:
            assert suffix == "TH", age


def test_ordinal_joins_number_and_suffix():
    assert ordinal(41) == "41ST"
    assert ordinal(13) == "13TH"


def test_ordinal_rejects_nonsense():
    with pytest.raises(ValueError):
        ordinal_suffix(-1)
    with pytest.raises(TypeError):
        ordinal_suffix(3.5)
    with pytest.raises(TypeError):
        ordinal_suffix(True)


def test_birthday_line():
    assert birthday_line(41) == "HAPPY 41ST BIRTHDAY"
    assert birthday_line(13) == "HAPPY 13TH BIRTHDAY"
    with pytest.raises(ValueError):
        birthday_line(0)


def test_year_line_is_never_a_birth_death_range():
    line = year_line(1979)
    assert line == f"1979 {EN_DASH} PRESENT"
    assert "PRESENT" in line
    # A second four-digit number would mean a death year had crept in.
    assert sum(token.isdigit() for token in line.split()) == 1


def test_year_line_rejects_implausible_years():
    with pytest.raises(ValueError):
        year_line(1234)
    with pytest.raises(TypeError):
        year_line("1979")
