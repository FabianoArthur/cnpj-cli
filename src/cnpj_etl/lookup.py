"""Look a CNPJ up in a SQLite database built by ``cnpj sqlite``."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from cnpj_etl import cnpj

SITUACAO = {"01": "nula", "02": "ativa", "03": "suspensa", "04": "inapta", "08": "baixada"}
MATRIZ_FILIAL = {"1": "matriz", "2": "filial"}
PORTE = {
    "00": "não informado",
    "01": "micro empresa",
    "03": "empresa de pequeno porte",
    "05": "demais",
}
TIPO_SOCIO = {"1": "pessoa jurídica", "2": "pessoa física", "3": "estrangeiro"}


def _date(value: str | None) -> str | None:
    if not value or len(value) != 8 or not value.isdigit() or value == "00000000":
        return None
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _money(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


class _Describer:
    """Turns codes into ``code - description`` using the lookup tables, if loaded."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.cache: dict[tuple[str, str], str | None] = {}

    def __call__(self, table: str, code: str | None, bare: bool = False) -> str | None:
        if not code:
            return None
        key = (table, code)
        if key not in self.cache:
            row = None
            if _has_table(self.conn, table):
                row = self.conn.execute(
                    f'SELECT descricao FROM "{table}" WHERE codigo = ?', (code,)
                ).fetchone()
            self.cache[key] = row[0] if row else None
        desc = self.cache[key]
        if bare:
            return desc or code
        return f"{code} - {desc}" if desc else code


def lookup(db_path: Path, value: str) -> dict[str, Any] | None:
    """Everything the snapshot knows about one establishment, or ``None``."""
    if not cnpj.is_valid(value):
        raise ValueError(f"invalid CNPJ: {value!r}")
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    basico, ordem, dv = cnpj.split(value)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        est = conn.execute(
            "SELECT * FROM estabelecimentos WHERE cnpj_basico = ? AND cnpj_ordem = ? "
            "AND cnpj_dv = ?",
            (basico, ordem, dv),
        ).fetchone()
        if est is None:
            return None
        emp = (
            conn.execute("SELECT * FROM empresas WHERE cnpj_basico = ?", (basico,)).fetchone()
            if _has_table(conn, "empresas")
            else None
        )
        simples = (
            conn.execute("SELECT * FROM simples WHERE cnpj_basico = ?", (basico,)).fetchone()
            if _has_table(conn, "simples")
            else None
        )
        socios = (
            conn.execute(
                "SELECT * FROM socios WHERE cnpj_basico = ? ORDER BY rowid", (basico,)
            ).fetchall()
            if _has_table(conn, "socios")
            else []
        )
        describe = _Describer(conn)

        secundarios = [c for c in (est["cnae_secundario"] or "").split(",") if c]
        address = " ".join(
            p
            for p in (est["tipo_logradouro"], est["logradouro"], est["numero"], est["complemento"])
            if p
        )
        phone = f"({est['ddd1']}) {est['telefone1']}" if est["telefone1"] else None
        return {
            "cnpj": cnpj.format_cnpj(basico + ordem + dv),
            "razao_social": emp["razao_social"] if emp else None,
            "nome_fantasia": est["nome_fantasia"] or None,
            "matriz_filial": MATRIZ_FILIAL.get(est["matriz_filial"], est["matriz_filial"]),
            "situacao_cadastral": SITUACAO.get(
                est["situacao_cadastral"], est["situacao_cadastral"]
            ),
            "data_situacao_cadastral": _date(est["data_situacao_cadastral"]),
            "motivo_situacao": describe("motivos", est["motivo_situacao_cadastral"]),
            "data_inicio_atividade": _date(est["data_inicio_atividade"]),
            "natureza_juridica": describe("naturezas", emp["natureza_juridica"]) if emp else None,
            "porte": PORTE.get(emp["porte_empresa"]) if emp else None,
            "capital_social": _money(emp["capital_social"]) if emp else None,
            "cnae_principal": describe("cnaes", est["cnae_principal"]),
            "cnaes_secundarios": [describe("cnaes", c) for c in secundarios],
            "endereco": address or None,
            "bairro": est["bairro"] or None,
            "cep": est["cep"] or None,
            "municipio": describe("municipios", est["municipio"], bare=True),
            "uf": est["uf"] or None,
            "telefone": phone,
            "email": (est["email"] or "").lower() or None,
            "simples": {
                "opcao_simples": simples["opcao_simples"] == "S",
                "opcao_mei": simples["opcao_mei"] == "S",
            }
            if simples
            else None,
            "socios": [
                {
                    "nome": s["nome_socio"],
                    "tipo": TIPO_SOCIO.get(s["tipo_socio"], s["tipo_socio"]),
                    "qualificacao": describe("qualificacoes", s["qualificacao_socio"]),
                    "data_entrada": _date(s["data_entrada"]),
                }
                for s in socios
            ],
        }
    finally:
        conn.close()
