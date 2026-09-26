import pytest

from cnpj_etl import cnpj


@pytest.mark.parametrize(
    "value",
    [
        "11.222.333/0001-81",
        "11222333000181",
        "00.000.000/0001-91",  # Banco do Brasil's well-known registration
        "12.ABC.345/01DE-35",  # alphanumeric example published by Receita Federal
        "12abc34501de35",  # lower case is accepted
    ],
)
def test_valid_cnpjs(value):
    assert cnpj.is_valid(value)


@pytest.mark.parametrize(
    "value",
    [
        "11.222.333/0001-82",  # wrong check digit
        "11222333000181 ",  # trailing junk is stripped, so this one is still valid
        "1122233300018",  # too short
        "112223330001811",  # too long
        "11111111111111",  # repeated digit
        "00000000000000",
        "12.ABC.345/01DE-3A",  # check digits must be numeric
        "",
    ],
)
def test_invalid_cnpjs(value):
    if value == "11222333000181 ":
        assert cnpj.is_valid(value)
    else:
        assert not cnpj.is_valid(value)


def test_normalize_strips_punctuation_and_upcases():
    assert cnpj.normalize(" 12.abc.345/01de-35 ") == "12ABC34501DE35"


def test_format():
    assert cnpj.format_cnpj("11222333000181") == "11.222.333/0001-81"
    assert cnpj.format_cnpj("12abc34501de35") == "12.ABC.345/01DE-35"


def test_format_rejects_wrong_length():
    with pytest.raises(ValueError):
        cnpj.format_cnpj("123")


def test_check_digits():
    assert cnpj.check_digits("112223330001") == "81"
    assert cnpj.check_digits("12ABC34501DE") == "35"


def test_split_into_registry_parts():
    assert cnpj.split("11.222.333/0001-81") == ("11222333", "0001", "81")


def test_kind():
    assert cnpj.kind("11222333000181") == "numeric"
    assert cnpj.kind("12ABC34501DE35") == "alphanumeric"
