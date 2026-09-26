"""Shape of the Receita Federal dump: tables, columns and how to read the CSVs.

Every column is kept as TEXT on purpose: ``cnpj_basico`` has leading zeros,
``capital_social`` uses a decimal comma and dates come as ``YYYYMMDD``.
Convert in your queries or in typed views.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from pathlib import Path

ENCODING = "latin1"
DELIMITER = ";"

MAIN_TABLES: dict[str, dict] = {
    "empresas": {
        "prefix": "EMPRECSV",
        "columns": [
            "cnpj_basico",
            "razao_social",
            "natureza_juridica",
            "qualificacao_responsavel",
            "capital_social",
            "porte_empresa",
            "ente_federativo",
        ],
    },
    "estabelecimentos": {
        "prefix": "ESTABELE",
        "columns": [
            "cnpj_basico",
            "cnpj_ordem",
            "cnpj_dv",
            "matriz_filial",
            "nome_fantasia",
            "situacao_cadastral",
            "data_situacao_cadastral",
            "motivo_situacao_cadastral",
            "nome_cidade_exterior",
            "pais",
            "data_inicio_atividade",
            "cnae_principal",
            "cnae_secundario",
            "tipo_logradouro",
            "logradouro",
            "numero",
            "complemento",
            "bairro",
            "cep",
            "uf",
            "municipio",
            "ddd1",
            "telefone1",
            "ddd2",
            "telefone2",
            "ddd_fax",
            "fax",
            "email",
            "situacao_especial",
            "data_situacao_especial",
        ],
    },
    "socios": {
        "prefix": "SOCIOCSV",
        "columns": [
            "cnpj_basico",
            "tipo_socio",
            "nome_socio",
            "documento_socio",
            "qualificacao_socio",
            "data_entrada",
            "pais",
            "cpf_representante",
            "nome_representante",
            "qualificacao_representante",
            "faixa_etaria",
        ],
    },
    "simples": {
        "prefix": "SIMPLES",
        "columns": [
            "cnpj_basico",
            "opcao_simples",
            "data_opcao_simples",
            "data_exclusao_simples",
            "opcao_mei",
            "data_opcao_mei",
            "data_exclusao_mei",
        ],
    },
}

_CODE = ["codigo", "descricao"]
LOOKUP_TABLES: dict[str, dict] = {
    "cnaes": {"prefix": "CNAECSV", "columns": _CODE},
    "municipios": {"prefix": "MUNICCSV", "columns": _CODE},
    "naturezas": {"prefix": "NATJUCSV", "columns": _CODE},
    "paises": {"prefix": "PAISCSV", "columns": _CODE},
    "qualificacoes": {"prefix": "QUALSCSV", "columns": _CODE},
    "motivos": {"prefix": "MOTICSV", "columns": _CODE},
}

ALL_TABLES: dict[str, dict] = {**MAIN_TABLES, **LOOKUP_TABLES}


def column_names(table: str) -> list[str]:
    return list(ALL_TABLES[table]["columns"])


def select_tables(only: Iterable[str] | None) -> dict[str, dict]:
    """All tables, or just the requested ones (in the requested order)."""
    if not only:
        return dict(ALL_TABLES)
    return {name: ALL_TABLES[name] for name in only}


def find_csvs(base_dir: Path, prefix: str) -> list[Path]:
    """Data files whose name contains ``prefix``.

    Names vary between snapshots (``K3241.K03200Y0.D60510.EMPRECSV``,
    ``F.K03200$W.SIMPLES.CSV.D60510``...), so the match is a case-insensitive
    substring test.
    """
    prefix = prefix.upper()
    return sorted(p for p in base_dir.rglob("*") if p.is_file() and prefix in p.name.upper())


def iter_rows(path: Path, width: int) -> Iterator[list[str]]:
    """Rows of one CSV, padded or truncated to ``width`` columns.

    Some snapshots ship rows with a column more or less than the documented
    layout; the loaders must not crash on them.
    """
    with open(path, encoding=ENCODING, newline="") as fh:
        for row in csv.reader(fh, delimiter=DELIMITER, quotechar='"'):
            if len(row) < width:
                row = row + [""] * (width - len(row))
            elif len(row) > width:
                row = row[:width]
            yield row
