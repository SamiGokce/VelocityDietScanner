from datetime import date

import pytest

from common.dates import age_turning, date_range, parse_date


def test_age_turning_on_the_birthday():
    assert age_turning(date(1984, 8, 28), date(2026, 8, 28)) == 42


def test_age_turning_before_and_after_in_the_year():
    assert age_turning(date(1984, 12, 31), date(2026, 8, 28)) == 41
    assert age_turning(date(1984, 1, 1), date(2026, 8, 28)) == 42


def test_leap_day_birthday():
    assert age_turning(date(2000, 2, 29), date(2028, 2, 29)) == 28


def test_age_turning_rejects_future_births():
    with pytest.raises(ValueError):
        age_turning(date(2030, 1, 1), date(2026, 1, 1))


def test_date_range_is_inclusive_of_start_and_90_days_long():
    window = date_range(date(2026, 8, 28), 90)
    assert len(window) == 90
    assert window[0] == date(2026, 8, 28)
    assert window[-1] == date(2026, 11, 25)
    assert len(set(window)) == 90


def test_parse_date():
    assert parse_date("2026-09-01") == date(2026, 9, 1)
    assert parse_date(date(2026, 9, 1)) == date(2026, 9, 1)
    assert parse_date("today") == date.today()
