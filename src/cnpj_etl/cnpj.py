"""CNPJ validation and formatting.

Handles both the classic 14-digit CNPJ and the alphanumeric CNPJ introduced by
Receita Federal (IN RFB 2.229/2024): the first 12 characters may be letters or
digits, the last two are always numeric check digits. Each character counts as
``ord(char) - 48``, so digits keep their value and ``A`` is 17, ``B`` is 18...
"""

from __future__ import annotations

import re

LENGTH = 14
_BASE_RE = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")
_STRIP_RE = re.compile(r"[\s./-]")
_WEIGHTS_FIRST = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_WEIGHTS_SECOND = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


def normalize(value: str) -> str:
    """Drop punctuation and whitespace, upper-case letters."""
    return _STRIP_RE.sub("", value).upper()


def _digit(chars: str, weights: tuple[int, ...]) -> int:
    total = sum((ord(c) - 48) * w for c, w in zip(chars, weights, strict=True))
    remainder = total % 11
    return 0 if remainder < 2 else 11 - remainder


def check_digits(base12: str) -> str:
    """Return the two check digits for the first 12 characters of a CNPJ."""
    base12 = normalize(base12)
    if len(base12) != 12 or not re.fullmatch(r"[0-9A-Z]{12}", base12):
        raise ValueError(f"expected 12 alphanumeric characters, got {base12!r}")
    first = _digit(base12, _WEIGHTS_FIRST)
    second = _digit(base12 + str(first), _WEIGHTS_SECOND)
    return f"{first}{second}"


def is_valid(value: str) -> bool:
    value = normalize(value)
    if not _BASE_RE.fullmatch(value):
        return False
    if len(set(value)) == 1:
        return False
    return check_digits(value[:12]) == value[12:]


def kind(value: str) -> str:
    """``numeric`` for the classic format, ``alphanumeric`` otherwise."""
    return "numeric" if normalize(value).isdigit() else "alphanumeric"


def format_cnpj(value: str) -> str:
    value = normalize(value)
    if len(value) != LENGTH:
        raise ValueError(f"a CNPJ has {LENGTH} characters, got {len(value)}")
    return f"{value[:2]}.{value[2:5]}.{value[5:8]}/{value[8:12]}-{value[12:]}"


def split(value: str) -> tuple[str, str, str]:
    """Split into the registry's (cnpj_basico, cnpj_ordem, cnpj_dv) columns."""
    value = normalize(value)
    if len(value) != LENGTH:
        raise ValueError(f"a CNPJ has {LENGTH} characters, got {len(value)}")
    return value[:8], value[8:12], value[12:]
