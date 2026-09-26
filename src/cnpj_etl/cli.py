"""``cnpj`` - command-line interface.

Exit codes: 0 success; 1 runtime failure (network, database, corrupt archive)
or, for ``validate``, at least one invalid CNPJ; 2 usage error; 3 ``lookup``
found no such CNPJ; 130 interrupted.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from cnpj_etl import __version__, archive, cnpj, download, months, output

log = logging.getLogger("cnpj_etl")

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NOT_FOUND, EXIT_INTERRUPTED = 0, 1, 2, 3, 130


class UsageError(Exception):
    pass


def _month(value: str) -> str:
    try:
        return months.parse(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _data_dir(args: argparse.Namespace) -> Path:
    base = Path(args.dir).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _schema(name: str) -> str:
    from cnpj_etl import postgres_load

    try:
        return postgres_load.validate_schema(name)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc


def _is_db_error(exc: Exception) -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    return isinstance(exc, psycopg.Error)


def _policy() -> download.RetryPolicy:
    return download.RetryPolicy()


def _fetch_month(args: argparse.Namespace, base: Path, month: str) -> Path:
    """Make sure ``<base>/<month>/dados.tar.gz`` exists, downloading if needed."""
    download.adopt_legacy(base, month, min_size=download.LEGACY_MIN_SIZE)
    outcome = download.download_month(
        month, base, base_url=args.base_url, policy=_policy(), show_progress=not args.quiet
    )
    if outcome == "not_published":
        raise download.DownloadError(f"{month} is not published on the server yet")
    if outcome == "skipped":
        log.info("%s: using the archive already on disk", month)
    return base / month / download.ARCHIVE_NAME


def _extracted(args: argparse.Namespace, base: Path, month: str) -> Path:
    work = base / f".work-{month}"
    shutil.rmtree(work, ignore_errors=True)
    archive.extract(_fetch_month(args, base, month), work)
    return work


# ------------------------------------------------------------------ commands


def cmd_download(args: argparse.Namespace) -> int:
    end = args.end or months.current_month()
    if args.start > end:
        raise UsageError(f"--start ({args.start}) is after --end ({end})")
    base = _data_dir(args)
    wanted = list(months.iter_months(args.start, end))
    missing = [m for m in wanted if download.needs_download(base / m)]
    if args.dry_run:
        for month in missing:
            print(month)
        log.info("%d of %d month(s) missing in %s", len(missing), len(wanted), base)
        return EXIT_OK
    log.info(
        "%d month(s) to download into %s with %d worker(s)",
        len(missing),
        base,
        download.clamp_workers(args.workers),
    )
    report = download.sync(
        wanted,
        base,
        base_url=args.base_url,
        workers=args.workers,
        policy=_policy(),
        show_progress=not args.quiet,
    )
    log.info(
        "downloaded: %d, already there: %d, not published: %d, failed: %d",
        len(report.done),
        len(report.skipped),
        len(report.not_published),
        len(report.failed),
    )
    if report.not_published:
        log.info("not published yet: %s", ", ".join(report.not_published))
    if report.failed:
        log.error("failed (run again to resume): %s", ", ".join(report.failed))
        return EXIT_FAIL
    return EXIT_OK


def cmd_sqlite(args: argparse.Namespace) -> int:
    from cnpj_etl import sqlite_load

    month = args.month or months.last_closed_month()
    base = _data_dir(args)
    db = Path(args.db) if args.db else base / f"cnpj_{month.replace('-', '_')}.db"
    work = _extracted(args, base, month)
    try:
        counts = sqlite_load.load(work, db, only=args.only)
    finally:
        if not args.keep_extracted:
            shutil.rmtree(work, ignore_errors=True)
    log.info("%s: %d rows in %d tables -> %s", month, sum(counts.values()), len(counts), db)
    return EXIT_OK


def cmd_postgres(args: argparse.Namespace) -> int:
    from cnpj_etl import postgres_load

    _schema(args.schema)
    month = args.month or months.last_closed_month()
    work = None
    if args.csv_dir:
        csv_dir = Path(args.csv_dir)
        if not csv_dir.is_dir():
            raise UsageError(f"--csv-dir {csv_dir} is not a directory")
    else:
        base = _data_dir(args)
        work = csv_dir = _extracted(args, base, month)
    try:
        with postgres_load.connect(args.dsn) as conn:
            counts = postgres_load.load_snapshot(
                conn, csv_dir, args.schema, only=args.only, logged=args.logged
            )
    finally:
        if work and not args.keep_extracted:
            shutil.rmtree(work, ignore_errors=True)
    log.info("%s: %d rows in schema %s", month, sum(counts.values()), args.schema)
    return EXIT_OK


def cmd_bulk_load(args: argparse.Namespace) -> int:
    from cnpj_etl import postgres_load

    _schema(args.schema)
    base = Path(args.dir).expanduser()
    found = postgres_load.list_months(base, args.only)
    if not found:
        log.error("no finished downloads in %s (run `cnpj download` first)", base)
        return EXIT_FAIL
    log.info("%d month(s) found: %s", len(found), ", ".join(m for m, _ in found))
    with postgres_load.connect(args.dsn) as conn:
        report = postgres_load.bulk_load(
            conn, base, found, args.schema, reload=args.reload, skip_indexes=args.skip_indexes
        )
    log.info("loaded: %d, already loaded: %d", len(report["processed"]), len(report["skipped"]))
    return EXIT_OK


def _read_inputs(values: list[str]) -> list[str]:
    if values in ([], ["-"]):
        return [line.strip() for line in sys.stdin if line.strip()]
    return values


def cmd_validate(args: argparse.Namespace) -> int:
    records = []
    for value in _read_inputs(args.cnpj):
        normalized = cnpj.normalize(value)
        well_formed = len(normalized) == cnpj.LENGTH
        valid = cnpj.is_valid(value)
        records.append(
            {
                "input": value,
                "cnpj": cnpj.format_cnpj(normalized) if well_formed else None,
                "valid": valid,
                "kind": cnpj.kind(normalized) if well_formed else None,
            }
        )
    output.write(records, args.format, sys.stdout)
    return EXIT_OK if all(r["valid"] for r in records) else EXIT_FAIL


def cmd_lookup(args: argparse.Namespace) -> int:
    from cnpj_etl import lookup

    db = Path(args.db).expanduser()
    if not cnpj.is_valid(args.cnpj):
        raise UsageError(f"invalid CNPJ: {args.cnpj!r}")
    if not db.exists():
        log.error("database not found: %s (build it with `cnpj sqlite`)", db)
        return EXIT_FAIL
    record = lookup.lookup(db, args.cnpj)
    if record is None:
        log.error("%s not found in %s", cnpj.format_cnpj(cnpj.normalize(args.cnpj)), db.name)
        return EXIT_NOT_FOUND
    output.write_record(record, args.format, sys.stdout)
    return EXIT_OK


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    default_dir = os.environ.get("CNPJ_DATA_DIR", "data")
    default_url = os.environ.get("CNPJ_BASE_URL", download.DEFAULT_BASE_URL)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="debug logging"
    )
    common.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="warnings and errors only, no progress bars",
    )

    data = argparse.ArgumentParser(add_help=False)
    data.add_argument(
        "--dir",
        "--output-dir",
        default=default_dir,
        metavar="DIR",
        help=f"data folder, one YYYY-MM/ per month (default: $CNPJ_DATA_DIR or {default_dir!r})",
    )

    remote = argparse.ArgumentParser(add_help=False)
    remote.add_argument(
        "--base-url",
        default=default_url,
        metavar="URL",
        help="download URL with {} for the month (default: Receita Federal "
        "share, or $CNPJ_BASE_URL)",
    )

    pg = argparse.ArgumentParser(add_help=False)
    pg.add_argument("--dsn", help="postgresql://... (default: $DATABASE_URL, then PG* variables)")
    pg.add_argument("--schema", default="cnpj", help="target schema (default: cnpj)")

    from cnpj_etl import layout

    tables = list(layout.ALL_TABLES)
    parser = argparse.ArgumentParser(
        prog="cnpj",
        description="Download Brazil's open CNPJ registry (Receita Federal) and load it into "
        "SQLite or PostgreSQL.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser(
        "download",
        parents=[common, data, remote],
        help="download every monthly snapshot missing on disk",
    )
    p.add_argument(
        "--start",
        type=_month,
        default=months.EARLIEST,
        help=f"first month, YYYY-MM (default: {months.EARLIEST})",
    )
    p.add_argument("--end", type=_month, help="last month (default: current month)")
    p.add_argument(
        "--workers",
        type=int,
        default=download.DEFAULT_WORKERS,
        help=f"parallel downloads, 1-{download.MAX_WORKERS} (default: {download.DEFAULT_WORKERS})",
    )
    p.add_argument("--dry-run", action="store_true", help="print the missing months and exit")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser(
        "sqlite", parents=[common, data, remote], help="one month into a SQLite file"
    )
    p.add_argument("month", nargs="?", type=_month, help="YYYY-MM (default: last closed month)")
    p.add_argument("--db", help="database path (default: DIR/cnpj_YYYY_MM.db)")
    p.add_argument(
        "--only",
        nargs="+",
        choices=tables,
        metavar="TABLE",
        help=f"load only these tables: {', '.join(tables)}",
    )
    p.add_argument("--keep-extracted", action="store_true", help="keep the extracted CSVs")
    p.set_defaults(func=cmd_sqlite)

    p = sub.add_parser(
        "postgres",
        parents=[common, data, remote, pg],
        help="one month into PostgreSQL, replacing the previous snapshot",
    )
    p.add_argument("month", nargs="?", type=_month, help="YYYY-MM (default: last closed month)")
    p.add_argument(
        "--only", nargs="+", choices=tables, metavar="TABLE", help="load only these tables"
    )
    p.add_argument("--csv-dir", help="skip download and extraction, load CSVs from here")
    p.add_argument(
        "--logged",
        action="store_true",
        help="create LOGGED tables up front (default: UNLOGGED while loading, then SET LOGGED)",
    )
    p.add_argument("--keep-extracted", action="store_true", help="keep the extracted CSVs")
    p.set_defaults(func=cmd_postgres)

    p = sub.add_parser(
        "bulk-load",
        parents=[common, data, pg],
        help="every downloaded month into PostgreSQL, keeping history",
    )
    p.add_argument(
        "--only", nargs="+", type=_month, metavar="YYYY-MM", help="load only these months"
    )
    p.add_argument("--reload", action="store_true", help="replace months already loaded")
    p.add_argument("--skip-indexes", action="store_true", help="do not create indexes at the end")
    p.set_defaults(func=cmd_bulk_load)

    p = sub.add_parser(
        "validate", parents=[common], help="check CNPJ check digits (numeric and alphanumeric)"
    )
    p.add_argument("cnpj", nargs="*", help="CNPJs to check; '-' or nothing reads stdin")
    p.add_argument("--format", choices=output.FORMATS, default="table")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser(
        "lookup", parents=[common], help="show one CNPJ from a database built by `cnpj sqlite`"
    )
    p.add_argument("cnpj")
    p.add_argument("--db", required=True, help="SQLite file built by `cnpj sqlite`")
    p.add_argument("--format", choices=output.FORMATS, default="table")
    p.set_defaults(func=cmd_lookup)
    return parser


def _setup_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root = logging.getLogger("cnpj_etl")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.verbose = getattr(args, "verbose", False)
    args.quiet = getattr(args, "quiet", False)
    _setup_logging(args.verbose, args.quiet)
    try:
        return args.func(args)
    except UsageError as exc:
        log.error("%s", exc)
        return EXIT_USAGE
    except (download.DownloadError, archive.ArchiveError, OSError) as exc:
        log.error("%s", exc)
        return EXIT_FAIL
    except KeyboardInterrupt:
        log.warning("interrupted; run the same command again to resume")
        return EXIT_INTERRUPTED
    except Exception as exc:
        if _is_db_error(exc):
            log.error("PostgreSQL: %s", exc)
            return EXIT_FAIL
        raise


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
