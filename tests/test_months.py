from datetime import date

import pytest

from cnpj_etl import months


def test_iter_months_crosses_year_boundary():
    assert list(months.iter_months("2023-11", "2024-02")) == [
        "2023-11",
        "2023-12",
        "2024-01",
        "2024-02",
    ]


def test_iter_months_single_and_empty():
    assert list(months.iter_months("2024-05", "2024-05")) == ["2024-05"]
    assert list(months.iter_months("2024-06", "2024-05")) == []


def test_last_closed_month_and_current():
    assert months.last_closed_month(date(2026, 1, 15)) == "2025-12"
    assert months.last_closed_month(date(2026, 7, 1)) == "2026-06"
    assert months.current_month(date(2026, 7, 1)) == "2026-07"


@pytest.mark.parametrize("value", ["2024-01", "2023-12"])
def test_parse_accepts(value):
    assert months.parse(value) == value


@pytest.mark.parametrize("value", ["2024-1", "2024-13", "2024-00", "24-01", "abcd-ef", ""])
def test_parse_rejects(value):
    with pytest.raises(ValueError):
        months.parse(value)
