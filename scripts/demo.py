"""Run the real CLI end to end against a local fake share with synthetic data.

    python scripts/demo.py              # prints the session
    python scripts/demo.py --html out.html   # also writes a terminal-style page

Nothing is downloaded from the internet and every company in the sample is
fictional (see tests/fakedump.py).
"""

from __future__ import annotations

import argparse
import html
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from fakedump import build_tar_gz  # noqa: E402
from fakeshare import FakeShare, serve  # noqa: E402

MONTH = "2026-08"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--html", type=Path, help="write a terminal-style HTML transcript")
    args = parser.parse_args()

    share = FakeShare(files={MONTH: build_tar_gz(padding=2_000_000)})
    share.script[MONTH] = ["503"]  # show a retry
    server = serve(share)
    transcript: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="cnpj-demo-") as tmp:
        env = {
            **os.environ,
            "CNPJ_DATA_DIR": str(Path(tmp) / "data"),
            "CNPJ_BASE_URL": share.base_url,
            "COLUMNS": "100",
        }
        db = "data/cnpj_2026_08.db"
        commands = [
            f"cnpj download --start {MONTH} --end {MONTH}",
            f"cnpj sqlite {MONTH}",
            f"cnpj lookup 11.222.333/0001-81 --db {db}",
            "cnpj validate 11.222.333/0001-81 12.ABC.345/01DE-35 11.222.333/0001-82",
        ]
        for command in commands:
            argv = [sys.executable, "-m", "cnpj_etl", *shlex.split(command)[1:]]
            done = subprocess.run(
                argv, cwd=tmp, env=env, capture_output=True, text=True, check=False
            )
            lines = [ln for ln in done.stderr.splitlines() if "%|" not in ln]
            text = "\n".join([*lines, *done.stdout.rstrip("\n").splitlines()])
            text = text.replace(str(Path(tmp).resolve()) + "/", "").replace(tmp + "/", "")
            transcript.append((command, text))
            print(f"$ {command}\n{text}\n")
    server.shutdown()

    if args.html:
        args.html.write_text(render(transcript), encoding="utf-8")
    return 0


def render(transcript: list[tuple[str, str]]) -> str:
    blocks = []
    for command, text in transcript:
        out = html.escape(text)
        for word, cls in (("WARNING", "warn"), ("ERROR", "err"), (" yes ", "ok"), (" no ", "err")):
            out = out.replace(word, f'<span class="{cls}">{word}</span>')
        blocks.append(
            f'<div class="cmd"><span class="p">$</span> {html.escape(command)}</div>'
            f"<pre>{out}</pre>"
        )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#0d1117;display:flex;justify-content:center;padding:28px}}
.win{{width:1120px;background:#161b22;border:1px solid #30363d;border-radius:12px;
overflow:hidden;box-shadow:0 12px 40px rgba(0,0,0,.45)}}
.bar{{height:34px;background:#21262d;display:flex;align-items:center;gap:8px;padding:0 14px}}
.bar i{{width:12px;height:12px;border-radius:50%;display:block}}
.t{{color:#8b949e;font:13px ui-sans-serif,-apple-system,sans-serif;margin-left:12px}}
.body{{padding:18px 22px 8px;font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
color:#c9d1d9}}
.cmd{{color:#e6edf3;font-weight:600}} .p{{color:#3fb950}}
pre{{margin:2px 0 16px;white-space:pre-wrap;color:#9da7b3;font:inherit}}
.warn{{color:#d29922}} .err{{color:#f85149}} .ok{{color:#3fb950}}
</style></head><body><div class="win"><div class="bar"><i style="background:#ff5f57"></i>
<i style="background:#febc2e"></i><i style="background:#28c840"></i>
<span class="t">cnpj — synthetic sample data</span></div><div class="body">
{"".join(blocks)}</div></div></body></html>"""


if __name__ == "__main__":
    sys.exit(main())
