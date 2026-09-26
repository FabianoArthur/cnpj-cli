"""Load snapshots into PostgreSQL with ``COPY ... FROM STDIN``.

Two modes:

* **snapshot** (``cnpj postgres``) - one month replaces the tables in a schema.
  Everything happens in one transaction, so a failed load leaves the previous
  snapshot untouched. The old tables stay locked for the whole load.
* **bulk** (``cnpj bulk-load``) - every downloaded month is appended to the
  same tables with a ``competencia`` column. One transaction per month,
  recorded in ``competencias_carregadas``; months already there are skipped.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from cnpj_etl import archive, layout
from cnpj_etl.download import ARCHIVE_NAME, MARKER_NAME

if TYPE_CHECKING:
    import psycopg

log = logging.getLogger(__name__)

TRACKING_TABLE = "competencias_carregadas"
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")

SNAPSHOT_INDEXES = [
    ("empresas", "idx_empresas_cnpj", ["cnpj_basico"]),
    ("estabelecimentos", "idx_estab_cnpj", ["cnpj_basico"]),
    ("estabelecimentos", "idx_estab_uf", ["uf"]),
    ("estabelecimentos", "idx_estab_municipio", ["municipio"]),
    ("socios", "idx_socios_cnpj", ["cnpj_basico"]),
    ("simples", "idx_simples_cnpj", ["cnpj_basico"]),
]
BULK_INDEXES = [
    ("empresas", "idx_empresas_comp_cnpj", ["competencia", "cnpj_basico"]),
    ("empresas", "idx_empresas_cnpj", ["cnpj_basico"]),
    ("estabelecimentos", "idx_estab_comp_cnpj", ["competencia", "cnpj_basico"]),
    ("estabelecimentos", "idx_estab_cnpj", ["cnpj_basico"]),
    ("estabelecimentos", "idx_estab_uf", ["uf"]),
    ("socios", "idx_socios_comp_cnpj", ["competencia", "cnpj_basico"]),
    ("socios", "idx_socios_cnpj", ["cnpj_basico"]),
    ("simples", "idx_simples_comp_cnpj", ["competencia", "cnpj_basico"]),
]


def _psycopg():
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise SystemExit(
            "PostgreSQL support is optional: pip install 'cnpj-etl[postgres]'"
        ) from exc
    return psycopg


def validate_schema(name: str) -> str:
    """Only plain lower-case identifiers: nothing that needs quoting to be read."""
    if not _SCHEMA_RE.fullmatch(name):
        raise ValueError(
            f"invalid schema name {name!r}: use lower-case letters, digits and _ "
            "(max 63, not starting with a digit)"
        )
    return name


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Connect with ``dsn``, else ``DATABASE_URL``, else the libpq ``PG*`` variables."""
    psycopg = _psycopg()
    dsn = dsn or os.environ.get("DATABASE_URL") or ""
    # autocommit + explicit ``conn.transaction()`` blocks: each block is a real
    # transaction, never a savepoint inside an implicit one.
    return psycopg.connect(dsn, autocommit=True)


def _require_autocommit(conn) -> None:
    if conn.autocommit:
        return
    if conn.info.transaction_status != conn.info.transaction_status.IDLE:
        raise ValueError("the connection has an open transaction; commit or roll back first")
    conn.autocommit = True


def _ident(*parts: str):
    from psycopg import sql

    return sql.Identifier(*parts)


def _copy_file(cur, path: Path, schema: str, table: str, competencia: str | None) -> int:
    from psycopg import sql

    base_cols = layout.column_names(table)
    columns = (["competencia", *base_cols]) if competencia else base_cols
    stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        _ident(schema, table), sql.SQL(", ").join(_ident(c) for c in columns)
    )
    rows = 0
    with cur.copy(stmt) as copy:
        for row in layout.iter_rows(path, len(base_cols)):
            copy.write_row([competencia, *row] if competencia else row)
            rows += 1
    return rows


def _copy_table(cur, csv_dir: Path, schema: str, table: str, competencia: str | None) -> int:
    files = layout.find_csvs(csv_dir, layout.ALL_TABLES[table]["prefix"])
    if not files:
        log.warning("no files for %s (prefix %s)", table, layout.ALL_TABLES[table]["prefix"])
    total = 0
    for path in files:
        log.debug("%s.%s <- %s", schema, table, path.name)
        total += _copy_file(cur, path, schema, table, competencia)
    log.info("%s.%s: %d rows", schema, table, total)
    return total


def _create_indexes(cur, schema: str, indexes, tables: Iterable[str]) -> None:
    from psycopg import sql

    tables = set(tables)
    for table, name, cols in indexes:
        if table in tables:
            cur.execute(
                sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} ({})").format(
                    _ident(name), _ident(schema, table), sql.SQL(", ").join(_ident(c) for c in cols)
                )
            )


def _analyze(conn, schema: str, tables: Iterable[str]) -> None:
    from psycopg import sql

    for table in tables:  # autocommit: each ANALYZE commits on its own
        conn.execute(sql.SQL("ANALYZE {}").format(_ident(schema, table)))


def load_snapshot(
    conn: psycopg.Connection,
    csv_dir: Path,
    schema: str,
    only: Iterable[str] | None = None,
    logged: bool = False,
) -> dict[str, int]:
    """Replace the tables in ``schema`` with one extracted snapshot, atomically."""
    from psycopg import sql

    validate_schema(schema)
    _require_autocommit(conn)
    tables = list(layout.select_tables(only))
    kind = sql.SQL("TABLE" if logged else "UNLOGGED TABLE")
    counts: dict[str, int] = {}
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(_ident(schema)))
        for table in tables:
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(_ident(schema, table)))
            cols = sql.SQL(", ").join(
                sql.SQL("{} TEXT").format(_ident(c)) for c in layout.column_names(table)
            )
            cur.execute(sql.SQL("CREATE {} {} ({})").format(kind, _ident(schema, table), cols))
            counts[table] = _copy_table(cur, csv_dir, schema, table, None)
        log.info("creating indexes")
        _create_indexes(cur, schema, SNAPSHOT_INDEXES, tables)
    if not logged:
        log.info("making tables durable (SET LOGGED rewrites them; it takes a while)")
        with conn.transaction(), conn.cursor() as cur:
            for table in tables:
                cur.execute(sql.SQL("ALTER TABLE {} SET LOGGED").format(_ident(schema, table)))
    _analyze(conn, schema, tables)
    return counts


def list_months(base: Path, only: Iterable[str] | None = None) -> list[tuple[str, Path]]:
    """Finished downloads under ``base`` as ``(YYYY-MM, archive path)``."""
    wanted = set(only or [])
    found = []
    for entry in sorted(base.iterdir()) if base.exists() else []:
        if not entry.is_dir() or not _MONTH_DIR_RE.match(entry.name):
            continue
        if wanted and entry.name not in wanted:
            continue
        if (entry / MARKER_NAME).exists():
            log.warning("%s: unfinished download, skipping", entry.name)
            continue
        if not (entry / ARCHIVE_NAME).exists():
            log.warning("%s: %s missing, skipping", entry.name, ARCHIVE_NAME)
            continue
        found.append((entry.name, entry / ARCHIVE_NAME))
    return found


def _ensure_bulk_schema(conn, schema: str) -> None:
    from psycopg import sql

    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(_ident(schema)))
        cur.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} (competencia TEXT PRIMARY KEY, "
                "carregada_em TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            ).format(_ident(schema, TRACKING_TABLE))
        )
        for table in layout.ALL_TABLES:
            cols = [sql.SQL("{} TEXT").format(_ident(c)) for c in layout.column_names(table)]
            if table in layout.MAIN_TABLES:
                cols.insert(0, sql.SQL("competencia TEXT NOT NULL"))
            cur.execute(
                sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                    _ident(schema, table), sql.SQL(", ").join(cols)
                )
            )


def _is_loaded(conn, schema: str, month: str) -> bool:
    from psycopg import sql

    return (
        conn.execute(
            sql.SQL("SELECT 1 FROM {} WHERE competencia = %s").format(
                _ident(schema, TRACKING_TABLE)
            ),
            (month,),
        ).fetchone()
        is not None
    )


def _is_empty(cur, schema: str, table: str) -> bool:
    from psycopg import sql

    cur.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(_ident(schema, table)))
    return cur.fetchone() is None


def bulk_load(
    conn: psycopg.Connection,
    base: Path,
    months: list[tuple[str, Path]],
    schema: str,
    reload: bool = False,
    skip_indexes: bool = False,
) -> dict[str, list[str]]:
    """Append each month to the history tables, one transaction per month."""
    from psycopg import sql

    validate_schema(schema)
    _require_autocommit(conn)
    _ensure_bulk_schema(conn, schema)
    report: dict[str, list[str]] = {"processed": [], "skipped": []}
    for month, archive_path in months:
        if not reload and _is_loaded(conn, schema, month):
            log.info("%s: already loaded, skipping", month)
            report["skipped"].append(month)
            continue
        log.info("=== %s ===", month)
        work = base / f".work-{month}"
        shutil.rmtree(work, ignore_errors=True)
        try:
            archive.extract(archive_path, work)
            with conn.transaction(), conn.cursor() as cur:
                if reload:
                    for table in layout.MAIN_TABLES:
                        cur.execute(
                            sql.SQL("DELETE FROM {} WHERE competencia = %s").format(
                                _ident(schema, table)
                            ),
                            (month,),
                        )
                    cur.execute(
                        sql.SQL("DELETE FROM {} WHERE competencia = %s").format(
                            _ident(schema, TRACKING_TABLE)
                        ),
                        (month,),
                    )
                for table in layout.LOOKUP_TABLES:
                    if _is_empty(cur, schema, table):
                        _copy_table(cur, work, schema, table, None)
                for table in layout.MAIN_TABLES:
                    _copy_table(cur, work, schema, table, month)
                cur.execute(
                    sql.SQL("INSERT INTO {} (competencia) VALUES (%s)").format(
                        _ident(schema, TRACKING_TABLE)
                    ),
                    (month,),
                )
        finally:
            shutil.rmtree(work, ignore_errors=True)
        report["processed"].append(month)
        log.info("%s: OK", month)

    if report["processed"]:
        if not skip_indexes:
            log.info("creating indexes (if missing)")
            with conn.transaction(), conn.cursor() as cur:
                _create_indexes(cur, schema, BULK_INDEXES, layout.MAIN_TABLES)
        _analyze(conn, schema, [*layout.ALL_TABLES, TRACKING_TABLE])
    return report
