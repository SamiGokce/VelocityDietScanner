"""Ordinal suffixes and the two date-derived text lines used on every graphic.

Two things in this module are the most likely source of an embarrassing public
post, so they live here alone, with tests:

1. The ordinal suffix ("HAPPY 41ST BIRTHDAY").  11, 12 and 13 -- and every
   number ending in them (111th, 212th, 1013th) -- take "TH", not "ST/ND/RD".
2. The year line.  Everyone in this database is *alive*.  A birth-death range
   ("1979 - 2026") is the typographic convention of a gravestone and would read
   as a death announcement.  The only correct form is "{BIRTH YEAR} - PRESENT".
"""

from __future__ import annotations

# U+2013 EN DASH, the correct dash for a span, with hair spaces either side
# handled by the renderer's letter-tracking rather than by literal spaces.
EN_DASH = "–"

PRESENT = "PRESENT"


def ordinal_suffix(n: int) -> str:
    """Return the uppercase English ordinal suffix for a non-negative integer.

    >>> ordinal_suffix(1), ordinal_suffix(11), ordinal_suffix(112)
    ('ST', 'TH', 'TH')
    """
    if not isinstance(n, int) or isinstance(n, bool):
        raise TypeError(f"ordinal_suffix expects an int, got {type(n).__name__}")
    if n < 0:
        raise ValueError(f"ordinal_suffix expects a non-negative int, got {n}")
    if 11 <= (n % 100) <= 13:
        return "TH"
    return {1: "ST", 2: "ND", 3: "RD"}.get(n % 10, "TH")


def ordinal(n: int) -> str:
    """'41' -> '41ST'."""
    return f"{n}{ordinal_suffix(n)}"


def birthday_line(age_turning: int) -> str:
    """The small line above the name: 'HAPPY 41ST BIRTHDAY'."""
    if age_turning <= 0:
        raise ValueError(f"age_turning must be positive, got {age_turning}")
    return f"HAPPY {ordinal(age_turning)} BIRTHDAY"


def year_line(birth_year: int) -> str:
    """The small line below the name: '1984 - PRESENT' (en dash).

    There is deliberately no code path in this project that produces a
    birth-death range.  If you are tempted to add one, the person does not
    belong in this pipeline at all.
    """
    if not isinstance(birth_year, int) or isinstance(birth_year, bool):
        raise TypeError(f"birth_year must be an int, got {type(birth_year).__name__}")
    if not (1800 <= birth_year <= 2200):
        raise ValueError(f"implausible birth_year: {birth_year}")
    return f"{birth_year} {EN_DASH} {PRESENT}"
