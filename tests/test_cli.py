import json
import os

import pytest

from cnpj_etl import __version__, cli

from fakedump import build_tar_gz

DATA = build_tar_gz(padding=100_000)


def run(capsys, *argv):
    code = cli.main(["-q", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_validate_json_and_exit_code(capsys):
    code, out, _ = run(
        capsys, "validate", "11222333000181", "12.abc.345/01de-35", "--format", "json"
    )
    assert code == 0
    assert json.loads(out) == [
        {"input": "11222333000181", "cnpj": "11.222.333/0001-81", "valid": True, "kind": "numeric"},
        {
            "input": "12.abc.345/01de-35",
            "cnpj": "12.ABC.345/01DE-35",
            "valid": True,
            "kind": "alphanumeric",
        },
    ]
    code, out, _ = run(capsys, "validate", "11222333000182", "abc", "--format", "csv")
    assert code == 1
    assert out.splitlines() == [
        "input,cnpj,valid,kind",
        "11222333000182,11.222.333/0001-82,false,numeric",
        "abc,,false,",
    ]


def test_validate_reads_stdin(capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("11222333000181\n\n11.444.777/0001-61\n"))
    code, out, _ = run(capsys, "validate", "-", "--format", "jsonl")
    assert code == 0
    assert [json.loads(line)["valid"] for line in out.splitlines()] == [True, True]


def test_download_dry_run(capsys, tmp_path):
    (tmp_path / "2024-02").mkdir()
    code, out, _ = run(
        capsys,
        "download",
        "--dir",
        str(tmp_path),
        "--start",
        "2024-01",
        "--end",
        "2024-03",
        "--dry-run",
    )
    assert code == 0
    assert out.split() == ["2024-01", "2024-03"]
    assert not (tmp_path / "2024-01").exists()


def test_download_rejects_inverted_range(capsys, tmp_path):
    code, _, err = run(
        capsys, "download", "--dir", str(tmp_path), "--start", "2024-05", "--end", "2024-01"
    )
    assert code == 2
    assert "--start" in err


def test_bad_month_is_a_usage_error(capsys, tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.main(["download", "--dir", str(tmp_path), "--start", "2024-13"])
    assert exc.value.code == 2


def test_download_then_sqlite_then_lookup(capsys, tmp_path, share):
    share.files["2026-04"] = DATA
    base = ["--base-url", share.base_url]
    code, _, _ = run(
        capsys, "download", "--dir", str(tmp_path), "--start", "2026-04", "--end", "2026-05", *base
    )
    assert code == 0
    assert (tmp_path / "2026-04" / "dados.tar.gz").exists()
    assert not (tmp_path / "2026-05").exists()

    code, _, _ = run(capsys, "sqlite", "2026-04", "--dir", str(tmp_path), *base)
    assert code == 0
    db = tmp_path / "cnpj_2026_04.db"
    assert db.exists()
    assert len(share.requests) == 2, "sqlite reuses the downloaded month"
    assert not list(tmp_path.glob(".work-*"))

    code, out, _ = run(capsys, "lookup", "11.222.333/0001-81", "--db", str(db), "--format", "json")
    assert code == 0
    assert json.loads(out)["razao_social"] == "PADARIA EXEMPLO LTDA"

    code, out, _ = run(capsys, "lookup", "11222333000181", "--db", str(db))
    assert code == 0
    assert "PADARIA EXEMPLO LTDA" in out and "socios" in out

    code, out, err = run(capsys, "lookup", "11444777000161", "--db", str(db))
    assert code == 3
    assert "not found" in err


def test_sqlite_not_published(capsys, tmp_path, share):
    code, _, err = run(
        capsys, "sqlite", "2099-01", "--dir", str(tmp_path), "--base-url", share.base_url
    )
    assert code == 1
    assert "not published" in err


def test_lookup_errors(capsys, tmp_path):
    code, _, err = run(capsys, "lookup", "123", "--db", str(tmp_path / "x.db"))
    assert code == 2
    assert "invalid CNPJ" in err
    code, _, err = run(capsys, "lookup", "11222333000181", "--db", str(tmp_path / "x.db"))
    assert code == 1
    assert "database not found" in err


def test_sqlite_reuses_legacy_download(capsys, tmp_path, share, monkeypatch):
    from cnpj_etl import download

    monkeypatch.setattr(download, "LEGACY_MIN_SIZE", 0)
    legacy = tmp_path / "downloads" / "cnpj_2026-04.tar.gz"
    legacy.parent.mkdir()
    legacy.write_bytes(DATA)
    code, _, _ = run(
        capsys, "sqlite", "2026-04", "--dir", str(tmp_path), "--base-url", share.base_url
    )
    assert code == 0
    assert share.requests == []
    assert (tmp_path / "2026-04" / "dados.tar.gz").exists()


@pytest.mark.skipif(not os.environ.get("CNPJ_TEST_DSN"), reason="needs CNPJ_TEST_DSN")
def test_postgres_commands(capsys, tmp_path, share):
    import uuid

    from cnpj_etl import postgres_load

    from fakedump import write_month

    dsn = os.environ["CNPJ_TEST_DSN"]
    schema = "t_" + uuid.uuid4().hex[:12]
    share.files["2026-04"] = DATA
    try:
        code, _, _ = run(
            capsys,
            "postgres",
            "2026-04",
            "--dir",
            str(tmp_path),
            "--dsn",
            dsn,
            "--schema",
            schema,
            "--only",
            "empresas",
            "cnaes",
            "--base-url",
            share.base_url,
        )
        assert code == 0
        write_month(tmp_path, "2026-03")
        code, _, _ = run(
            capsys, "bulk-load", "--dir", str(tmp_path), "--dsn", dsn, "--schema", schema + "_h"
        )
        assert code == 0
        with postgres_load.connect(dsn) as conn:
            n = conn.execute(f'SELECT COUNT(*) FROM "{schema}_h".empresas').fetchone()[0]
        assert n == 6
    finally:
        with postgres_load.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}_h" CASCADE')


def test_invalid_schema_is_usage_error(capsys, tmp_path):
    code, _, err = run(capsys, "bulk-load", "--dir", str(tmp_path), "--schema", "Bad-Name")
    assert code == 2
    assert "invalid schema" in err


def test_postgres_snapshot_has_its_own_default_schema():
    args = cli.build_parser().parse_args(["postgres"])
    assert args.schema == "cnpj_snapshot"
    assert cli.build_parser().parse_args(["bulk-load"]).schema == "cnpj"
