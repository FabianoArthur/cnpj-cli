"""Integration tests against a real PostgreSQL.

Skipped unless ``CNPJ_TEST_DSN`` points at a disposable database, e.g.
``postgresql://postgres:test@127.0.0.1:5432/cnpj_test``. CI runs them with a
service container.
"""

import os
import uuid

import pytest

from cnpj_etl import archive, layout, postgres_load

from fakedump import build_tar_gz, write_month

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("CNPJ_TEST_DSN")
needs_pg = pytest.mark.skipif(not DSN, reason="set CNPJ_TEST_DSN to run PostgreSQL tests")


@pytest.mark.parametrize("name", ["cnpj", "cnpj_2026", "_x"])
def test_schema_name_accepted(name):
    assert postgres_load.validate_schema(name) == name


@pytest.mark.parametrize("name", ["", "Cnpj", "1abc", "a-b", 'x"; DROP TABLE y; --', "a" * 64])
def test_schema_name_rejected(name):
    with pytest.raises(ValueError):
        postgres_load.validate_schema(name)


@pytest.fixture
def conn():
    if not DSN:
        pytest.skip("set CNPJ_TEST_DSN to run PostgreSQL tests")
    with postgres_load.connect(DSN) as c:
        yield c


@pytest.fixture
def schema(conn):
    name = "t_" + uuid.uuid4().hex[:12]
    yield name
    conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


@pytest.fixture
def csv_dir(tmp_path):
    src = tmp_path / "dados.tar.gz"
    src.write_bytes(build_tar_gz())
    archive.extract(src, tmp_path / "csv")
    return tmp_path / "csv"


def count(conn, schema, table, where=""):
    return conn.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}" {where}').fetchone()[0]


@needs_pg
@pytest.mark.postgres
def test_snapshot_load_and_replace(conn, schema, csv_dir):
    counts = postgres_load.load_snapshot(conn, csv_dir, schema)
    assert counts["estabelecimentos"] == 3
    assert count(conn, schema, "empresas") == 3
    persistence = conn.execute(
        "SELECT relpersistence FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = 'empresas'",
        (schema,),
    ).fetchone()[0]
    assert persistence == "p", "tables must be LOGGED once the load finishes"

    postgres_load.load_snapshot(conn, csv_dir, schema)
    assert count(conn, schema, "empresas") == 3


@needs_pg
@pytest.mark.postgres
def test_snapshot_failure_keeps_previous_snapshot(conn, schema, csv_dir, monkeypatch):
    postgres_load.load_snapshot(conn, csv_dir, schema)
    real = layout.iter_rows

    def flaky(path, width):
        if "SOCIOCSV" in path.name:
            raise OSError("disk read error")
        return real(path, width)

    monkeypatch.setattr(layout, "iter_rows", flaky)
    with pytest.raises(OSError):
        postgres_load.load_snapshot(conn, csv_dir, schema)
    assert count(conn, schema, "empresas") == 3
    assert count(conn, schema, "socios") == 2


@needs_pg
@pytest.mark.postgres
def test_bulk_load_is_idempotent_and_reloadable(conn, schema, tmp_path):
    for m in ("2026-03", "2026-04"):
        write_month(tmp_path, m)
    (tmp_path / "2026-05").mkdir()
    (tmp_path / "2026-05" / ".downloading").touch()  # unfinished download: ignored

    months = postgres_load.list_months(tmp_path)
    assert [m for m, _ in months] == ["2026-03", "2026-04"]

    report = postgres_load.bulk_load(conn, tmp_path, months, schema)
    assert report == {"processed": ["2026-03", "2026-04"], "skipped": []}
    assert count(conn, schema, "empresas") == 6
    assert count(conn, schema, "empresas", "WHERE competencia = '2026-04'") == 3
    assert count(conn, schema, "cnaes") == 2, "lookup tables are loaded once"
    assert not list(tmp_path.glob(".work-*")), "extraction folders are cleaned up"

    again = postgres_load.bulk_load(conn, tmp_path, months, schema)
    assert again == {"processed": [], "skipped": ["2026-03", "2026-04"]}

    postgres_load.bulk_load(conn, tmp_path, months[:1], schema, reload=True)
    assert count(conn, schema, "empresas") == 6


@needs_pg
@pytest.mark.postgres
def test_bulk_load_failure_rolls_back_the_month(conn, schema, tmp_path, monkeypatch):
    write_month(tmp_path, "2026-03")
    write_month(tmp_path, "2026-04")
    months = postgres_load.list_months(tmp_path)
    real = layout.iter_rows
    calls = {"n": 0}

    def flaky(path, width):
        if "SOCIOCSV" in path.name:
            calls["n"] += 1
            if calls["n"] == 2:  # second month, after empresas/estabelecimentos went in
                raise OSError("disk read error")
        return real(path, width)

    monkeypatch.setattr(layout, "iter_rows", flaky)
    with pytest.raises(OSError):
        postgres_load.bulk_load(conn, tmp_path, months, schema)
    assert count(conn, schema, "empresas") == 3
    assert count(conn, schema, "empresas", "WHERE competencia = '2026-04'") == 0
    loaded = conn.execute(f'SELECT competencia FROM "{schema}".competencias_carregadas').fetchall()
    assert loaded == [("2026-03",)]


@needs_pg
@pytest.mark.postgres
def test_open_transaction_is_refused(schema, csv_dir):
    with psycopg.connect(DSN) as raw:
        raw.execute("SELECT 1")  # implicit transaction left open
        with pytest.raises(ValueError, match="open transaction"):
            postgres_load.load_snapshot(raw, csv_dir, schema)
