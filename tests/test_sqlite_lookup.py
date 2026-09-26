import csv
import io
import json
import sqlite3

import pytest

from cnpj_etl import archive, lookup, output, sqlite_load

from fakedump import build_tar_gz


@pytest.fixture
def csv_dir(tmp_path):
    src = tmp_path / "dados.tar.gz"
    src.write_bytes(build_tar_gz())
    dest = tmp_path / "extracted"
    archive.extract(src, dest)
    return dest


@pytest.fixture
def db(tmp_path, csv_dir):
    path = tmp_path / "cnpj.db"
    counts = sqlite_load.load(csv_dir, path)
    assert counts["estabelecimentos"] == 3
    return path


def test_load_counts_rows_and_pads_short_rows(tmp_path, csv_dir):
    path = tmp_path / "cnpj.db"
    counts = sqlite_load.load(csv_dir, path)
    assert counts == {
        "empresas": 3,
        "estabelecimentos": 3,
        "socios": 2,
        "simples": 1,
        "cnaes": 2,
        "municipios": 2,
        "naturezas": 2,
        "paises": 1,
        "qualificacoes": 2,
        "motivos": 2,
    }
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT razao_social, porte_empresa, ente_federativo FROM empresas "
            "WHERE cnpj_basico = '77888999'"
        ).fetchone()
        assert row == ("OFICINA MODELO ME", "", "")
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_estab_cnpj" in names


def test_load_only_some_tables_and_reload_replaces(tmp_path, csv_dir):
    path = tmp_path / "cnpj.db"
    sqlite_load.load(csv_dir, path, only=["empresas"])
    sqlite_load.load(csv_dir, path, only=["empresas"])  # second run replaces, no duplicates
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM empresas").fetchone() == (3,)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {"empresas"}


def test_failed_load_keeps_previous_database(tmp_path, csv_dir, monkeypatch):
    path = tmp_path / "cnpj.db"
    sqlite_load.load(csv_dir, path, only=["empresas"])

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(sqlite_load, "_create_indexes", boom)
    with pytest.raises(RuntimeError):
        sqlite_load.load(csv_dir, path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM empresas").fetchone() == (3,)
    assert not list(tmp_path.glob("cnpj.db.tmp*"))


def test_lookup_joins_descriptions(db):
    result = lookup.lookup(db, "11.222.333/0001-81")
    assert result["cnpj"] == "11.222.333/0001-81"
    assert result["razao_social"] == "PADARIA EXEMPLO LTDA"
    assert result["nome_fantasia"] == "PADARIA DO EXEMPLO"
    assert result["matriz_filial"] == "matriz"
    assert result["situacao_cadastral"] == "ativa"
    assert result["natureza_juridica"] == "2062 - Sociedade Empresária Limitada"
    assert result["cnae_principal"] == "1091102 - Fabricação de produtos de padaria e confeitaria"
    assert result["cnaes_secundarios"] == ["4721102", "5611203 - Lanchonetes e similares"]
    assert result["municipio"] == "SAO PAULO"
    assert result["uf"] == "SP"
    assert result["capital_social"] == 50000.0
    assert result["data_inicio_atividade"] == "2015-03-02"
    assert result["simples"] == {"opcao_simples": True, "opcao_mei": False}
    assert [s["nome"] for s in result["socios"]] == ["MARIA EXEMPLO", "JOÃO MODELO"]
    assert result["socios"][0]["qualificacao"] == "49 - Sócio-Administrador"


def test_lookup_branch_and_missing(db):
    assert lookup.lookup(db, "11222333000262")["matriz_filial"] == "filial"
    closed = lookup.lookup(db, "44555666000181")
    assert closed["situacao_cadastral"] == "baixada"
    assert closed["simples"] is None
    assert lookup.lookup(db, "11444777000161") is None


def test_lookup_rejects_invalid_cnpj(db):
    with pytest.raises(ValueError):
        lookup.lookup(db, "11222333000182")


def test_lookup_missing_database(tmp_path):
    with pytest.raises(FileNotFoundError):
        lookup.lookup(tmp_path / "nope.db", "11222333000181")


RECORDS = [
    {"cnpj": "11.222.333/0001-81", "valid": True, "tags": ["a", "b"]},
    {"cnpj": "11.222.333/0001-82", "valid": False, "tags": []},
]


def test_output_json():
    buf = io.StringIO()
    output.write(RECORDS, "json", buf)
    assert json.loads(buf.getvalue()) == RECORDS


def test_output_jsonl_and_csv():
    buf = io.StringIO()
    output.write(RECORDS, "jsonl", buf)
    assert [json.loads(line) for line in buf.getvalue().splitlines()] == RECORDS
    buf = io.StringIO()
    output.write(RECORDS, "csv", buf)
    rows = list(csv.DictReader(io.StringIO(buf.getvalue())))
    assert rows[0] == {"cnpj": "11.222.333/0001-81", "valid": "true", "tags": "a|b"}


def test_output_table_aligns_columns():
    buf = io.StringIO()
    output.write(RECORDS, "table", buf)
    lines = buf.getvalue().splitlines()
    assert lines[0].split() == ["CNPJ", "VALID", "TAGS"]
    assert lines[1].startswith("11.222.333/0001-81  yes")
