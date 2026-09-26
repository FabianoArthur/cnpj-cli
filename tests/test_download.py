import json

import pytest

from cnpj_etl import download
from cnpj_etl.download import RetryPolicy

from fakedump import build_tar_gz

DATA = build_tar_gz(padding=300_000)


def policy(waits, attempts=5):
    return RetryPolicy(attempts=attempts, base=1.0, cap=60.0, jitter=0.0, sleep=waits.append)


def fetch(share, tmp_path, waits, month="2024-01", **kw):
    return download.download_month(
        month, tmp_path, base_url=share.base_url, policy=policy(waits, **kw), show_progress=False
    )


def test_plain_download_is_atomic_and_clears_marker(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    assert fetch(share, tmp_path, no_sleep) == "done"
    folder = tmp_path / "2024-01"
    assert (folder / "dados.tar.gz").read_bytes() == DATA
    assert sorted(p.name for p in folder.iterdir()) == ["dados.tar.gz"]
    assert share.requests[0]["headers"]["User-Agent"].startswith("cnpj-etl/")


def test_retries_5xx_with_exponential_backoff(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    share.script["2024-01"] = ["503", "503"]
    assert fetch(share, tmp_path, no_sleep) == "done"
    assert no_sleep == [1.0, 2.0]
    assert (tmp_path / "2024-01" / "dados.tar.gz").read_bytes() == DATA


def test_honours_retry_after(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    share.script["2024-01"] = ["429"]
    assert fetch(share, tmp_path, no_sleep) == "done"
    assert no_sleep == [7.0]


def test_gives_up_after_max_attempts_and_keeps_marker(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    share.script["2024-01"] = ["503"] * 10
    with pytest.raises(download.DownloadError):
        fetch(share, tmp_path, no_sleep, attempts=3)
    assert len(no_sleep) == 2
    assert (tmp_path / "2024-01" / ".downloading").exists()
    assert not (tmp_path / "2024-01" / "dados.tar.gz").exists()


def test_not_published_cleans_up(share, tmp_path, no_sleep):
    assert fetch(share, tmp_path, no_sleep, month="2099-01") == "not_published"
    assert not (tmp_path / "2099-01").exists()
    assert no_sleep == []


def test_html_error_page_is_not_saved_and_not_retried(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    share.script["2024-01"] = ["html"]
    with pytest.raises(download.DownloadError, match="HTML"):
        fetch(share, tmp_path, no_sleep)
    assert no_sleep == []
    assert not (tmp_path / "2024-01" / "dados.tar.gz").exists()


def test_dropped_connection_resumes_with_range(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    share.script["2024-01"] = ["cut:200000"]
    assert fetch(share, tmp_path, no_sleep) == "done"
    assert (tmp_path / "2024-01" / "dados.tar.gz").read_bytes() == DATA
    second = share.requests[1]["headers"]
    resumed_at = int(second["Range"].removeprefix("bytes=").rstrip("-"))
    assert 0 < resumed_at <= 200000
    assert second["If-Range"] == share.etag("2024-01")


def test_resume_across_runs_uses_part_file(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    share.script["2024-01"] = ["cut:200000"]
    with pytest.raises(download.DownloadError):
        fetch(share, tmp_path, no_sleep, attempts=1)
    folder = tmp_path / "2024-01"
    received = (folder / "dados.tar.gz.part").stat().st_size
    assert 0 < received <= 200000
    meta = json.loads((folder / "dados.tar.gz.part.json").read_text())
    assert meta["etag"] == share.etag("2024-01")
    assert download.needs_download(folder)

    assert fetch(share, tmp_path, no_sleep) == "done"
    assert (folder / "dados.tar.gz").read_bytes() == DATA
    assert share.requests[-1]["headers"]["Range"] == f"bytes={received}-"


def test_server_ignoring_range_restarts_from_zero(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    share.script["2024-01"] = ["cut:200000", "ignore-range"]
    assert fetch(share, tmp_path, no_sleep) == "done"
    assert (tmp_path / "2024-01" / "dados.tar.gz").read_bytes() == DATA


def test_wrong_content_range_offset_restarts(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    share.script["2024-01"] = ["cut:200000", "bad-range"]
    assert fetch(share, tmp_path, no_sleep) == "done"
    assert (tmp_path / "2024-01" / "dados.tar.gz").read_bytes() == DATA
    assert "Range" not in share.requests[-1]["headers"]


def test_republished_file_is_not_stitched(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    share.script["2024-01"] = ["cut:200000"]
    with pytest.raises(download.DownloadError):
        fetch(share, tmp_path, no_sleep, attempts=1)
    new_data = build_tar_gz(padding=310_000)
    share.files["2024-01"] = new_data  # ETag changes, If-Range no longer matches
    assert fetch(share, tmp_path, no_sleep) == "done"
    assert (tmp_path / "2024-01" / "dados.tar.gz").read_bytes() == new_data


def test_416_with_complete_part_finalises(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    folder = tmp_path / "2024-01"
    folder.mkdir()
    (folder / ".downloading").touch()
    (folder / "dados.tar.gz.part").write_bytes(DATA)
    (folder / "dados.tar.gz.part.json").write_text(
        json.dumps(
            {
                "url": share.base_url.format("2024-01"),
                "etag": share.etag("2024-01"),
                "last_modified": None,
                "total": len(DATA),
            }
        )
    )
    assert fetch(share, tmp_path, no_sleep) == "done"
    assert (folder / "dados.tar.gz").read_bytes() == DATA
    assert not (folder / ".downloading").exists()


def test_legacy_marker_with_truncated_file_is_redone(share, tmp_path, no_sleep):
    share.files["2024-01"] = DATA
    folder = tmp_path / "2024-01"
    folder.mkdir()
    (folder / ".downloading").touch()
    (folder / "dados.tar.gz").write_bytes(DATA[:1000])
    assert download.needs_download(folder)
    assert fetch(share, tmp_path, no_sleep) == "done"
    assert (folder / "dados.tar.gz").read_bytes() == DATA
    assert "Range" not in share.requests[0]["headers"]


def test_needs_download_rules(tmp_path):
    assert download.needs_download(tmp_path / "2024-01")
    done = tmp_path / "2024-02"
    done.mkdir()
    assert not download.needs_download(done)
    (done / ".downloading").touch()
    assert download.needs_download(done)


def test_sync_downloads_only_missing_and_limits_concurrency(share, tmp_path):
    for m in ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05"]:
        share.files[m] = DATA
    share.delay = 0.2
    (tmp_path / "2024-02").mkdir()  # already downloaded
    report = download.sync(
        ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05", "2024-06"],
        tmp_path,
        base_url=share.base_url,
        workers=2,
        policy=RetryPolicy(attempts=1, sleep=lambda s: None),
        show_progress=False,
    )
    assert report.done == ["2024-01", "2024-03", "2024-04", "2024-05"]
    assert report.skipped == ["2024-02"]
    assert report.not_published == ["2024-06"]
    assert report.failed == []
    assert share.max_active == 2


def test_sync_reports_failures_without_stopping(share, tmp_path):
    share.files["2024-01"] = DATA
    share.files["2024-02"] = DATA
    share.script["2024-01"] = ["503"]
    report = download.sync(
        ["2024-01", "2024-02"],
        tmp_path,
        base_url=share.base_url,
        workers=1,
        policy=RetryPolicy(attempts=1, sleep=lambda s: None),
        show_progress=False,
    )
    assert report.failed == ["2024-01"]
    assert report.done == ["2024-02"]


def test_workers_are_capped():
    assert download.clamp_workers(10) == download.MAX_WORKERS
    assert download.clamp_workers(0) == 1


def test_adopt_legacy_archive(tmp_path):
    legacy = tmp_path / "downloads" / "cnpj_2024-01.tar.gz"
    legacy.parent.mkdir()
    legacy.write_bytes(DATA)
    target = download.adopt_legacy(tmp_path, "2024-01", min_size=0)
    assert target == tmp_path / "2024-01" / "dados.tar.gz"
    assert target.read_bytes() == DATA
    assert not legacy.exists()


def test_adopt_legacy_refuses_truncated(tmp_path):
    legacy = tmp_path / "downloads" / "cnpj_2024-01.tar.gz"
    legacy.parent.mkdir()
    legacy.write_bytes(DATA[:-5000])
    assert download.adopt_legacy(tmp_path, "2024-01", min_size=0) is None
    assert legacy.exists()


def test_content_range_parsing_tolerates_garbage():
    assert download._content_range("bytes 10-19/20") == (10, 20)
    assert download._content_range("bytes */20") == (None, 20)
    assert download._content_range("bytes abc-def/xyz") == (None, None)
    assert download._content_range(None) == (None, None)
