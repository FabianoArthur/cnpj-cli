import io
import tarfile
import zipfile

import pytest

from cnpj_etl import archive, layout

from fakedump import build_tar_gz, build_zip


def test_detect_format(tmp_path):
    (tmp_path / "a").write_bytes(build_tar_gz())
    (tmp_path / "b").write_bytes(build_zip())
    (tmp_path / "c").write_bytes(b"<!DOCTYPE html><html>")
    assert archive.detect_format(tmp_path / "a") == "gzip"
    assert archive.detect_format(tmp_path / "b") == "zip"
    assert archive.detect_format(tmp_path / "c") == "unknown"


@pytest.mark.parametrize("builder", [build_tar_gz, build_zip])
def test_extract_unpacks_inner_zips(tmp_path, builder):
    src = tmp_path / "dados.tar.gz"
    src.write_bytes(builder())
    dest = tmp_path / "out"
    archive.extract(src, dest)
    assert not list(dest.rglob("*.zip")), "inner zips must be removed after extraction"
    assert len(layout.find_csvs(dest, "ESTABELE")) == 2
    assert len(layout.find_csvs(dest, "SIMPLES")) == 1


def test_extract_unknown_format_explains(tmp_path):
    src = tmp_path / "dados.tar.gz"
    src.write_bytes(b"<html>Service Unavailable</html>")
    with pytest.raises(archive.ArchiveError, match=r"not a tar\.gz or zip"):
        archive.extract(src, tmp_path / "out")


def test_extract_refuses_path_traversal_in_tar(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("../evil.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"evil"))
    src = tmp_path / "bad.tar.gz"
    src.write_bytes(buf.getvalue())
    dest = tmp_path / "out"
    with pytest.raises(archive.ArchiveError):
        archive.extract(src, dest)
    assert not (tmp_path / "evil.txt").exists()


def test_extract_refuses_symlink_in_tar(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    src = tmp_path / "bad.tar.gz"
    src.write_bytes(buf.getvalue())
    with pytest.raises(archive.ArchiveError):
        archive.extract(src, tmp_path / "out")


def test_zip_traversal_is_neutralised(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.txt", "evil")
    src = tmp_path / "bad.zip"
    src.write_bytes(buf.getvalue())
    dest = tmp_path / "out"
    archive.extract(src, dest)
    assert not (tmp_path / "evil.txt").exists()
    assert (dest / "evil.txt").exists()


def test_verify_detects_truncation(tmp_path):
    good = tmp_path / "good.tar.gz"
    good.write_bytes(build_tar_gz(padding=50_000))
    truncated = tmp_path / "truncated.tar.gz"
    truncated.write_bytes(good.read_bytes()[:-2000])
    z = tmp_path / "good.zip"
    z.write_bytes(build_zip())
    assert archive.verify(good)
    assert archive.verify(z)
    assert not archive.verify(truncated)


def test_truncated_tar_raises_archive_error(tmp_path):
    src = tmp_path / "dados.tar.gz"
    src.write_bytes(build_tar_gz(padding=50_000)[:-2000])
    with pytest.raises(archive.ArchiveError, match="corrupt or truncated"):
        archive.extract(src, tmp_path / "out")


def test_corrupt_inner_zip_raises_archive_error(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"PK\x03\x04 this is not really a zip"
        info = tarfile.TarInfo("Empresas0.zip")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    src = tmp_path / "dados.tar.gz"
    src.write_bytes(buf.getvalue())
    with pytest.raises(archive.ArchiveError, match=r"Empresas0\.zip"):
        archive.extract(src, tmp_path / "out")
