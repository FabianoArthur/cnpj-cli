# cnpj-etl

**Brazil's whole company registry on your machine, in one command.** `cnpj` downloads the
monthly open-data snapshots of every CNPJ published by Receita Federal and loads them into
SQLite or PostgreSQL. It retries, resumes and keeps month-by-month history.

[Português (Brasil)](README.pt-BR.md)

[![CI](https://github.com/FabianoArthur/script-cnpj/actions/workflows/ci.yml/badge.svg)](https://github.com/FabianoArthur/script-cnpj/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

![A terminal session: cnpj download retries after an HTTP 503, cnpj sqlite loads ten tables, cnpj lookup prints a company and cnpj validate checks three CNPJs](docs/assets/demo.png)

<sub>Real output of `python scripts/demo.py`: the CLI runs against a local server with
synthetic data, so every company shown is fictional.</sub>

## Why it is interesting

The data is public but awkward. Each month is a ~6 GB archive on a government file share
that is slow and sometimes down. Inside are dozens of zipped latin-1 CSV files without
headers. The format of the archive itself changes between months: sometimes a tar.gz,
sometimes a zip under the same name. `cnpj` deals with all of that:

- **Downloads that survive a bad network.** Exponential backoff with jitter, `Retry-After`
  honoured, resume from the last byte with `Range` + `If-Range`, so a republished file is
  never stitched onto stale bytes. Files appear only through an atomic rename.
- **Polite by default.** Two months at a time (at most four), a descriptive `User-Agent`,
  and months already on disk are never fetched again.
- **Safe extraction.** It detects the real format from magic bytes, refuses path traversal
  and links, and recognises an HTML error page saved as `.tar.gz`.
- **All-or-nothing loads.** SQLite builds into a temporary file and swaps it in. A
  PostgreSQL snapshot loads in one transaction. The history load runs one transaction per
  month, so an interrupted run never leaves half a month behind.
- **Ready for the alphanumeric CNPJ** (IN RFB 2.229/2024). `cnpj validate` checks both
  formats.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/how-it-works-dark.svg">
  <img alt="Flow: Receita Federal share over HTTPS to cnpj download (retry, backoff, resume, up to 4 in parallel), into data/YYYY-MM/dados.tar.gz, then safe extraction to latin-1 CSV, then three loaders: cnpj sqlite (one month, then cnpj lookup prints JSON, CSV or a table), cnpj postgres (one month, replaces the snapshot atomically) and cnpj bulk-load (every month with history, one transaction per month) into PostgreSQL" src="docs/assets/how-it-works-light.svg">
</picture>

## Install

Python 3.10 or newer.

```bash
git clone https://github.com/FabianoArthur/script-cnpj.git
cd script-cnpj
python -m venv .venv && source .venv/bin/activate
pip install ".[postgres]"      # drop [postgres] if you only need SQLite
cnpj --help
```

## Quick start

```bash
# Last closed month into SQLite (~6 GB download, one .db file)
cnpj sqlite
cnpj lookup 00.000.000/0001-91 --db data/cnpj_2026_08.db --format json

# Full history into PostgreSQL
export DATABASE_URL=postgresql://user:password@localhost:5432/cnpj
cnpj download                  # every month since 2023-05 that is not on disk yet
cnpj bulk-load                 # every downloaded month, with a competencia column
```

Try a couple of months before committing to the full history (hundreds of GB):

```bash
cnpj download --start 2026-07 --end 2026-08
cnpj bulk-load --only 2026-07 2026-08
```

## Commands

| Command | What it does |
|---|---|
| `cnpj download [--start --end --workers --dry-run]` | Downloads every month missing under `--dir`. Safe to re-run: it only fetches what is missing and resumes partial files. |
| `cnpj sqlite [YYYY-MM] [--only TABLE…] [--db PATH]` | One month into a SQLite file (default: last closed month). Reuses the download if it is already on disk. |
| `cnpj postgres [YYYY-MM] [--schema --only --csv-dir --logged]` | One month into PostgreSQL through `COPY`, replacing the previous snapshot in a single transaction. |
| `cnpj bulk-load [--only YYYY-MM… --reload --skip-indexes]` | Every downloaded month into the same tables, with a `competencia` column. Months already loaded are skipped; `--reload` replaces them. |
| `cnpj validate CNPJ… [--format]` | Checks check digits, numeric or alphanumeric. Reads stdin with `-`. |
| `cnpj lookup CNPJ --db PATH [--format]` | One establishment with its company, partners, Simples status and code descriptions. |

Output formats: `table` (default), `json`, `jsonl`, `csv`. Logs go to stderr and data to
stdout, so `cnpj lookup … --format json | jq` works. Use `-v` for debug logs and `-q` for
warnings only.

**Exit codes:** `0` success · `1` failure (network, database, corrupt archive) or an invalid
CNPJ in `validate` · `2` usage error · `3` CNPJ not found by `lookup` · `130` interrupted.

**Configuration:** `--dir` / `CNPJ_DATA_DIR` (default `./data`), `--dsn` / `DATABASE_URL` /
the libpq `PG*` variables, `--base-url` / `CNPJ_BASE_URL` for a mirror. See
[`.env.example`](.env.example).

### On-disk layout

```
data/
├── 2026-07/dados.tar.gz
├── 2026-08/dados.tar.gz
├── 2026-09/.downloading          ← unfinished: resumed on the next run
│          dados.tar.gz.part
└── cnpj_2026_08.db               ← from `cnpj sqlite`
```

A month folder without `.downloading` counts as done.

### What lands in the database

`empresas`, `estabelecimentos`, `socios` and `simples`, plus the code tables `cnaes`,
`municipios`, `naturezas`, `paises`, `qualificacoes` and `motivos`. Every column is `TEXT`
on purpose: `cnpj_basico` keeps its leading zeros, `capital_social` uses a decimal comma and
dates come as `YYYYMMDD`. Cast in queries or typed views. `bulk-load` adds `competencia` as
the first column of the four main tables and records each month in
`competencias_carregadas`.

```sql
-- Companies that exist in 2026-08 but did not in 2026-07
SELECT n.cnpj_basico, n.razao_social
FROM cnpj.empresas n
LEFT JOIN cnpj.empresas p
  ON p.cnpj_basico = n.cnpj_basico AND p.competencia = '2026-07'
WHERE n.competencia = '2026-08' AND p.cnpj_basico IS NULL;
```

### Sizes and times (per month, rough)

| | One month | Full history (~40 months) |
|---|---|---|
| Download | ~6 GB | ~240 GB |
| PostgreSQL with indexes | ~40 GB | ~1.5 TB |
| Download time | 10–30 min | hours |
| Load time | 30–90 min | days |

`cnpj postgres` writes to the schema `cnpj_snapshot` by default and `cnpj bulk-load` to
`cnpj`, so a snapshot never drops the history tables. A PostgreSQL snapshot load holds an exclusive lock on the tables it replaces until it
commits. Point readers at another schema, or use `bulk-load`, if that matters.

## Migrating from the old scripts

| Old script | Now |
|---|---|
| `python sync_months.py --output-dir D` | `cnpj download --dir D` |
| `python bulk_load_postgres.py --output-dir D` | `cnpj bulk-load --dir D` |
| `python cnpj_pipeline.py 2026-04 --output-dir D` | `cnpj sqlite 2026-04 --dir D` |
| `python cnpj_to_postgres.py 2026-04 --output-dir D --schema S` | `cnpj postgres 2026-04 --dir D --schema S` |

The month folders and the `competencias_carregadas` table are unchanged, so existing
downloads and databases keep working. Archives that the old one-month scripts left in
`D/downloads/cnpj_YYYY-MM.tar.gz` are checked for integrity and moved into `D/YYYY-MM/`
instead of being downloaded again. `--output-dir` still works as an alias of `--dir`.
`--download-dir`, `--work-dir`, `--no-prompt`, `--skip-download` and the interactive folder
prompt are gone.

## Tests

```bash
pip install -e ".[dev,postgres]"
ruff check . && ruff format --check .
pytest
```

The suite never touches the internet. Downloads hit a local HTTP server that can be told to
fail with 503/429, drop the connection halfway, ignore `Range`, answer at the wrong offset
or serve an HTML page. The data is a synthetic dump with the real file layout
([`tests/fakedump.py`](tests/fakedump.py)). PostgreSQL tests run against a real server when
`CNPJ_TEST_DSN` points at a disposable database. CI starts one, and
[CONTRIBUTING.md](CONTRIBUTING.md) shows a one-line Docker setup.

`python scripts/demo.py` runs the whole flow offline. `python scripts/build_diagram.py`
regenerates the diagrams above.

## Data source, terms and privacy

- The data is published by **Receita Federal do Brasil** as open data
  ([dados.gov.br](https://dados.gov.br/dados/conjuntos-dados/cadastro-nacional-da-pessoa-juridica---cnpj)).
  Credit the source when you use it. This project is not affiliated with the Brazilian
  government.
- Be gentle with the public server: keep `--workers` low and don't script tight loops
  around `cnpj download`.
- `socios` holds names of real people. The source already masks their CPF numbers. A
  database built from it is personal data under Brazil's LGPD: keep it private and use it
  only for a legitimate purpose.

## License

[MIT](LICENSE) © 2026 Fabiano Arthur
