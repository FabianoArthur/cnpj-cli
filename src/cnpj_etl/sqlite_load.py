"""Load one extracted snapshot into a single SQLite file.

The database is built next to the target under a temporary name and moved
into place only when complete, so a failed or interrupted load never leaves a
half-filled database behind (and the previous one stays usable).
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from cnpj_etl import layout

log = logging.getLogger(__name__)

BATCH_SIZE = 50_000

INDEXES = [
    ("idx_empresas_cnpj", "empresas", "cnpj_basico"),
    ("idx_estab_cnpj", "estabelecimentos", "cnpj_basico"),
    ("idx_estab_uf", "estabelecimentos", "uf"),
    ("idx_estab_municipio", "estabelecimentos", "municipio"),
    ("idx_socios_cnpj", "socios", "cnpj_basico"),
    ("idx_simples_cnpj", "simples", "cnpj_basico"),
]


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _load_table(conn: sqlite3.Connection, csv_dir: Path, table: str) -> int:
    columns = layout.column_names(table)
    conn.execute(f"CREATE TABLE {_quote(table)} ({', '.join(_quote(c) + ' TEXT' for c in columns)})")
    files = layout.find_csvs(csv_dir, layout.ALL_TABLES[table]["prefix"])
    if not files:
        log.warning("no files for %s (prefix %s)", table, layout.ALL_TABLES[table]["prefix"])
        return 0
    insert = (
        f"INSERT INTO {_quote(table)} ({', '.join(_quote(c) for c in columns)}) "
        f"VALUES ({', '.join('?' * len(columns))})"
    )
    total = 0
    for path in files:
        log.info("%s <- %s", table, path.name)
        batch: list[list[str]] = []
        for row in layout.iter_rows(path, len(columns)):
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                conn.executemany(insert, batch)
                total += len(batch)
                batch.clear()
        conn.executemany(insert, batch)
        total += len(batch)
    log.info("%s: %d rows", table, total)
    return total


def _create_indexes(conn: sqlite3.Connection, tables: Iterable[str]) -> None:
    tables = set(tables)
    for name, table, column in INDEXES:
        if table in tables:
            conn.execute(f"CREATE INDEX {_quote(name)} ON {_quote(table)} ({_quote(column)})")


def load(csv_dir: Path, db_path: Path, only: Iterable[str] | None = None) -> dict[str, int]:
    """Build ``db_path`` from the CSVs in ``csv_dir``. Returns rows loaded per table."""
    tables = list(layout.select_tables(only))
    tmp = db_path.with_name(f"{db_path.name}.tmp-{os.getpid()}")
    tmp.unlink(missing_ok=True)
    counts: dict[str, int] = {}
    try:
        conn = sqlite3.connect(tmp)
        try:
            conn.execute("PRAGMA journal_mode=OFF")  # the temp file is disposable
            conn.execute("PRAGMA synchronous=OFF")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA cache_size=-200000")  # ~200 MB
            for table in tables:
                with conn:
                    counts[table] = _load_table(conn, csv_dir, table)
            log.info("creating indexes")
            with conn:
                _create_indexes(conn, tables)
            conn.execute("ANALYZE")
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp, db_path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    log.info("database ready: %s", db_path)
    return counts
