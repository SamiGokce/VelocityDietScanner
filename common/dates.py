"""Date helpers: the 90-day window and the age someone turns on a given day."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator


def parse_date(value: str | date | None) -> date:
    """Accept a date, an ISO string, or the literal 'today'."""
    if value is None or value == "today":
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def date_range(start: date, days: int) -> list[date]:
    if days < 1:
        raise ValueError("days must be >= 1")
    return [start + timedelta(days=i) for i in range(days)]


def iter_dates(start: date, days: int) -> Iterator[date]:
    yield from date_range(start, days)


def age_turning(birth_date: date, target_date: date) -> int:
    """Age the person reaches on `target_date`.

    In this pipeline `target_date` always shares month/day with `birth_date`,
    so the result is simply the year difference -- but the general rule is
    implemented anyway so callers cannot be surprised.
    """
    years = target_date.year - birth_date.year
    if (target_date.month, target_date.day) < (birth_date.month, birth_date.day):
        years -= 1
    if years < 0:
        raise ValueError(
            f"target_date {target_date} precedes birth_date {birth_date}"
        )
    return years
