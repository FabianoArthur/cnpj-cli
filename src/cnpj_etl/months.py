"""Helpers for *competências*: the monthly snapshots, written ``YYYY-MM``."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date

_RE = re.compile(r"^(\d{4})-(\d{2})$")

# Oldest monthly snapshot published on the current Receita Federal share.
EARLIEST = "2023-05"


def parse(value: str) -> str:
    """Validate a ``YYYY-MM`` string and return it unchanged."""
    match = _RE.match(value)
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise ValueError(f"invalid month {value!r}, expected YYYY-MM")
    return value


def iter_months(start: str, end: str) -> Iterator[str]:
    """Yield every ``YYYY-MM`` from ``start`` to ``end``, both inclusive."""
    y, m = (int(x) for x in parse(start).split("-"))
    end_y, end_m = (int(x) for x in parse(end).split("-"))
    while (y, m) <= (end_y, end_m):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m > 12:
            y, m = y + 1, 1


def current_month(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.year:04d}-{today.month:02d}"


def last_closed_month(today: date | None = None) -> str:
    """The month before the current one: the newest snapshot likely to be published."""
    today = today or date.today()
    if today.month == 1:
        return f"{today.year - 1:04d}-12"
    return f"{today.year:04d}-{today.month - 1:02d}"
