"""Safe extraction of the monthly archive and its inner zips."""

from __future__ import annotations

import gzip
import logging
import tarfile
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)


class ArchiveError(RuntimeError):
    """The archive is corrupt, of an unknown type, or tries to escape its folder."""


def detect_format(path: Path) -> str:
    """``gzip``, ``zip`` or ``unknown``, from the magic number.

    The Receita share serves ``dados.tar.gz`` as a real tar.gz on some months
    and as a zip on others, so the extension can't be trusted.
    """
    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic[:2] == b"\x1f\x8b":
        return "gzip"
    if magic[:2] == b"PK":
        return "zip"
    return "unknown"


def _check_member(member: tarfile.TarInfo, dest: Path) -> None:
    if not (member.isfile() or member.isdir()):
        raise ArchiveError(f"refusing link or special file in archive: {member.name}")
    target = (dest / member.name).resolve()
    if dest != target and dest not in target.parents:
        raise ArchiveError(f"refusing path outside the destination: {member.name}")


def _extract_tar(path: Path, dest: Path) -> None:
    with tarfile.open(path, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            _check_member(member, dest)
        # The explicit checks above cover Pythons without extraction filters;
        # where filters exist, "data" is a second line of defence.
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, members=members, filter="data")
        else:  # pragma: no cover - Python < 3.10.12
            tar.extractall(dest, members=members)  # noqa: S202 - members checked above


def extract(path: Path, dest: Path) -> None:
    """Extract ``path`` into ``dest`` and unpack every inner zip in place."""
    dest.mkdir(parents=True, exist_ok=True)
    dest = dest.resolve()
    fmt = detect_format(path)
    log.info("extracting %s (%s)", path.name, fmt)
    try:
        if fmt == "gzip":
            _extract_tar(path, dest)
        elif fmt == "zip":
            with zipfile.ZipFile(path) as z:
                # zipfile strips absolute paths and ".." from member names itself.
                z.extractall(dest)  # noqa: S202
        else:
            with open(path, "rb") as fh:
                preview = fh.read(80)
            raise ArchiveError(
                f"{path.name} is not a tar.gz or zip (starts with {preview!r}); "
                "it is probably an error page. Delete it and download again."
            )
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as exc:
        raise ArchiveError(f"{path.name} is corrupt or truncated: {exc}") from exc

    inner = sorted(dest.rglob("*.zip"))
    if inner:
        log.info("unpacking %d inner zip(s)", len(inner))
    for zip_path in inner:
        log.debug("unpacking %s", zip_path.name)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(zip_path.parent)  # noqa: S202 - same sanitising as above
        zip_path.unlink()


def verify(path: Path) -> bool:
    """Read the whole archive to prove it is complete (used before adopting old files)."""
    fmt = detect_format(path)
    try:
        if fmt == "gzip":
            with gzip.open(path, "rb") as fh:
                while fh.read(8 * 1024 * 1024):
                    pass
            return True
        if fmt == "zip":
            with zipfile.ZipFile(path) as z:
                return z.testzip() is None
    except (OSError, EOFError, zipfile.BadZipFile):
        return False
    return False
