# Security policy

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability
reporting instead: *Security → Report a vulnerability* on this repository.
You should get a first answer within a week.

## Scope

Relevant reports include, for example:

- extraction of a crafted archive writing outside the target folder;
- SQL injection through command-line options such as `--schema`;
- credentials (`--dsn`, `DATABASE_URL`) leaking into logs or error messages.

## Handling data

The registry is public, but the partners table (`socios`) contains names of
real people. Treat any database you build with it as personal data under
Brazil's LGPD: restrict access and do not republish it out of context.
