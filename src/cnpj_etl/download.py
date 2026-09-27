"""Polite, resumable downloads of the monthly archives.

On-disk contract, one folder per month::

    <base>/YYYY-MM/dados.tar.gz          finished download
    <base>/YYYY-MM/.downloading          present while a download is unfinished
    <base>/YYYY-MM/dados.tar.gz.part     bytes received so far
    <base>/YYYY-MM/dados.tar.gz.part.json  URL + ETag/Last-Modified + total size

A month counts as done when its folder exists without the ``.downloading``
marker. The finished file only appears through an atomic rename of the
``.part`` file after its size is checked, and the marker is removed last, so a
crash at any point leaves the month marked for another try.
"""

from __future__ import annotations

import email.utils
import json
import logging
import os
import random
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
from tqdm import tqdm

from cnpj_etl import __version__, archive

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = (
    "https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9/download?path=/%2F{}"
)
ARCHIVE_NAME = "dados.tar.gz"
MARKER_NAME = ".downloading"
PART_SUFFIX = ".part"
USER_AGENT = f"cnpj-etl/{__version__} (+https://github.com/FabianoArthur/cnpj-cli)"
DEFAULT_WORKERS = 2
MAX_WORKERS = 4
CHUNK_SIZE = 64 * 1024
TIMEOUT = (15, 120)  # connect, read (seconds)
# A legacy archive smaller than this is almost certainly an error page.
LEGACY_MIN_SIZE = 100 * 1024 * 1024


class DownloadError(RuntimeError):
    """A month could not be downloaded."""


class _Retryable(Exception):
    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.retry_after = retry_after


@dataclass
class RetryPolicy:
    """Exponential backoff with jitter; ``Retry-After`` from the server wins."""

    attempts: int = 5
    base: float = 1.0
    cap: float = 60.0
    jitter: float = 0.25
    sleep: Callable[[float], None] = time.sleep

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, 10 * self.cap)
        delay = min(self.cap, self.base * 2 ** (attempt - 1))
        if self.jitter:
            delay *= random.uniform(1 - self.jitter, 1 + self.jitter)  # noqa: S311 - not crypto
        return delay


@dataclass
class SyncReport:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    not_published: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def clamp_workers(workers: int) -> int:
    return max(1, min(MAX_WORKERS, workers))


def needs_download(folder: Path) -> bool:
    """True unless the month folder exists and has no ``.downloading`` marker."""
    return not folder.exists() or (folder / MARKER_NAME).exists()


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def _retry_after(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _content_range(value: str | None) -> tuple[int | None, int | None]:
    """``bytes 100-199/200`` -> (100, 200); ``bytes */200`` -> (None, 200)."""
    if not value or not value.startswith("bytes "):
        return None, None
    span, _, total = value[6:].partition("/")
    first = span.split("-")[0]
    start = int(first) if first.isdigit() else None
    return start, (int(total) if total.isdigit() else None)


class _Paths:
    def __init__(self, folder: Path):
        self.folder = folder
        self.final = folder / ARCHIVE_NAME
        self.part = folder / (ARCHIVE_NAME + PART_SUFFIX)
        self.meta = folder / (ARCHIVE_NAME + PART_SUFFIX + ".json")
        self.marker = folder / MARKER_NAME

    def read_meta(self) -> dict | None:
        try:
            return json.loads(self.meta.read_text())
        except (OSError, ValueError):
            return None

    def reset_part(self) -> None:
        self.part.unlink(missing_ok=True)
        self.meta.unlink(missing_ok=True)


def _attempt(
    session: requests.Session,
    url: str,
    paths: _Paths,
    progress: tqdm,
) -> str:
    """One GET. Returns ``complete`` or ``not_published``; raises _Retryable or DownloadError."""
    meta = paths.read_meta()
    offset = 0
    headers = {}
    if paths.part.exists() and meta and meta.get("url") == url:
        validator = meta.get("etag") or meta.get("last_modified")
        offset = paths.part.stat().st_size
        if validator and offset:
            headers = {"Range": f"bytes={offset}-", "If-Range": validator}
    if not headers:
        offset = 0
        paths.reset_part()

    try:
        with session.get(url, stream=True, timeout=TIMEOUT, headers=headers) as r:
            if r.status_code == 404:
                return "not_published"
            if r.status_code == 416:
                _, total = _content_range(r.headers.get("Content-Range"))
                expected = total or (meta or {}).get("total")
                if expected and paths.part.exists() and paths.part.stat().st_size == expected:
                    return "complete"
                paths.reset_part()
                raise _Retryable("server rejected the resume range", retry_after=0)
            if r.status_code == 429 or r.status_code >= 500:
                raise _Retryable(f"HTTP {r.status_code}", _retry_after(r))
            if r.status_code >= 400:
                raise DownloadError(f"HTTP {r.status_code} for {url}")
            if "text/html" in r.headers.get("Content-Type", ""):
                paths.reset_part()
                raise DownloadError(
                    "the server returned an HTML page instead of the archive "
                    "(maintenance or a changed link?)"
                )

            if r.status_code == 206:
                start, total = _content_range(r.headers.get("Content-Range"))
                if start != offset:
                    log.warning("%s: resume offset mismatch, restarting", paths.folder.name)
                    paths.reset_part()
                    raise _Retryable("wrong Content-Range offset", retry_after=0)
                mode = "ab"
            else:
                offset = 0
                length = r.headers.get("Content-Length")
                total = int(length) if length and length.isdigit() else None
                mode = "wb"

            paths.meta.write_text(
                json.dumps(
                    {
                        "url": url,
                        "etag": r.headers.get("ETag"),
                        "last_modified": r.headers.get("Last-Modified"),
                        "total": total,
                    }
                )
            )
            progress.reset(total=total)
            progress.update(offset)
            with open(paths.part, mode) as fh:
                for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                    fh.write(chunk)
                    progress.update(len(chunk))
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    ) as exc:
        raise _Retryable(f"connection problem: {exc.__class__.__name__}") from exc

    size = paths.part.stat().st_size
    if total is not None and size != total:
        raise _Retryable(f"got {size} of {total} bytes")
    return "complete"


def download_month(
    month: str,
    base_dir: Path,
    *,
    base_url: str = DEFAULT_BASE_URL,
    policy: RetryPolicy | None = None,
    show_progress: bool = True,
    position: int | None = None,
) -> str:
    """Download one month. Returns ``done``, ``skipped`` or ``not_published``."""
    policy = policy or RetryPolicy()
    paths = _Paths(base_dir / month)
    if not needs_download(paths.folder):
        return "skipped"

    paths.folder.mkdir(parents=True, exist_ok=True)
    paths.marker.touch()
    if paths.final.exists():
        # The marker says the last run did not finish: never trust this file.
        log.info("%s: discarding unfinished %s", month, ARCHIVE_NAME)
        paths.final.unlink()

    url = base_url.format(month)
    log.debug("%s: GET %s", month, url)
    with (
        make_session() as session,
        tqdm(
            desc=month,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            disable=not show_progress or None,
            position=position,
            leave=True,
            dynamic_ncols=True,
        ) as progress,
    ):
        attempt = 0
        while True:
            attempt += 1
            try:
                outcome = _attempt(session, url, paths, progress)
                break
            except _Retryable as exc:
                if attempt >= policy.attempts:
                    raise DownloadError(f"{month}: {exc} (gave up after {attempt} tries)") from exc
                wait = policy.delay(attempt, exc.retry_after)
                log.warning(
                    "%s: %s, retrying in %.1fs (%d/%d)", month, exc, wait, attempt, policy.attempts
                )
                policy.sleep(wait)

    if outcome == "not_published":
        log.warning("%s: not published on the server (404)", month)
        paths.reset_part()
        paths.marker.unlink(missing_ok=True)
        if not any(paths.folder.iterdir()):
            paths.folder.rmdir()
        return "not_published"

    os.replace(paths.part, paths.final)
    paths.meta.unlink(missing_ok=True)
    paths.marker.unlink(missing_ok=True)
    log.info("%s: downloaded %s", month, ARCHIVE_NAME)
    return "done"


def sync(
    months: Iterable[str],
    base_dir: Path,
    *,
    base_url: str = DEFAULT_BASE_URL,
    workers: int = DEFAULT_WORKERS,
    policy: RetryPolicy | None = None,
    show_progress: bool = True,
) -> SyncReport:
    """Download every month that is not on disk yet, a few at a time."""
    report = SyncReport()
    todo = []
    for month in months:
        (todo if needs_download(base_dir / month) else report.skipped).append(month)
    if not todo:
        return report

    workers = clamp_workers(workers)
    positions = list(range(workers))
    lock = threading.Lock()
    results: dict[str, str] = {}

    def run(month: str) -> str:
        with lock:
            position = positions.pop(0)
        try:
            return download_month(
                month,
                base_dir,
                base_url=base_url,
                policy=policy,
                show_progress=show_progress,
                position=position,
            )
        finally:
            with lock:
                positions.append(position)

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="download")
    try:
        futures = {pool.submit(run, m): m for m in todo}
        for future in as_completed(futures):
            month = futures[future]
            try:
                results[month] = future.result()
            except (DownloadError, OSError) as exc:
                log.error("%s", exc)
                results[month] = "failed"
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()

    buckets = {
        "done": report.done,
        "not_published": report.not_published,
        "failed": report.failed,
        "skipped": report.skipped,
    }
    for month in todo:
        buckets[results[month]].append(month)
    return report


def adopt_legacy(base_dir: Path, month: str, min_size: int = LEGACY_MIN_SIZE) -> Path | None:
    """Move an archive left by the old one-month scripts into the month folder.

    The old scripts saved ``<base>/downloads/cnpj_YYYY-MM.tar.gz`` with no
    marker, so the file is only adopted after reading it end to end.
    """
    paths = _Paths(base_dir / month)
    if paths.final.exists() and not paths.marker.exists():
        return None
    for name in (f"cnpj_{month}.tar.gz", f"cnpj_{month}.zip"):
        legacy = base_dir / "downloads" / name
        if not legacy.exists() or legacy.stat().st_size < min_size:
            continue
        log.info("checking legacy archive %s before reusing it", legacy.name)
        if not archive.verify(legacy):
            log.warning("%s is incomplete or corrupt; it will be downloaded again", legacy.name)
            continue
        paths.folder.mkdir(parents=True, exist_ok=True)
        paths.reset_part()
        os.replace(legacy, paths.final)
        paths.marker.unlink(missing_ok=True)
        log.info("reused %s as %s", legacy.name, paths.final)
        return paths.final
    return None
