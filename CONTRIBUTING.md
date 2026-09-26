# Contributing

Thanks for helping. Issues and pull requests are welcome.

## Set up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,postgres]"
```

## Before opening a pull request

```bash
ruff check . && ruff format --check .
pytest
```

The tests never touch the internet: downloads hit a local HTTP server and use a
small synthetic dump (`tests/fakedump.py`). To also run the PostgreSQL tests,
point `CNPJ_TEST_DSN` at a **disposable** database:

```bash
docker run -d --rm --name cnpj-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=cnpj_test \
  -p 127.0.0.1:55432:5432 postgres:16-alpine
CNPJ_TEST_DSN=postgresql://postgres:test@127.0.0.1:55432/cnpj_test pytest
docker stop cnpj-pg
```

## Guidelines

- Keep the default download behaviour gentle with the public server (few
  parallel downloads, backoff on errors).
- Never commit real registry extracts, `.env` files or database dumps.
- One topic per pull request, with a test for any behaviour change.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).
