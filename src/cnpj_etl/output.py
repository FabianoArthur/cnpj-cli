"""Render records as JSON, JSON Lines, CSV or an aligned text table."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

FORMATS = ("table", "json", "jsonl", "csv")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        if all(not isinstance(v, (dict, list)) for v in value):
            return "|".join(_cell(v) for v in value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _human(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)) and not value:
        return "-"
    return _cell(value) or "-"


def write(records: Sequence[Mapping[str, Any]], fmt: str, stream: TextIO) -> None:
    """Write a list of flat-ish records."""
    if fmt == "json":
        json.dump(list(records), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    elif fmt == "jsonl":
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    elif fmt == "csv":
        if not records:
            return
        writer = csv.DictWriter(stream, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({k: _cell(v) for k, v in record.items()})
    elif fmt == "table":
        if not records:
            return
        keys = list(records[0])
        rows = [[_human(r.get(k)) for k in keys] for r in records]
        widths = [max(len(k), *(len(row[i]) for row in rows)) for i, k in enumerate(keys)]
        stream.write("  ".join(k.upper().ljust(w) for k, w in zip(keys, widths)).rstrip() + "\n")
        for row in rows:
            stream.write("  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip() + "\n")
    else:
        raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(FORMATS)}")


def write_record(record: Mapping[str, Any], fmt: str, stream: TextIO) -> None:
    """Write one nested record; ``table`` becomes a key/value listing."""
    if fmt == "json":
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    elif fmt == "table":
        width = max(len(k) for k in record)
        for key, value in record.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                stream.write(f"{key.ljust(width)}  {len(value)}\n")
                for item in value:
                    stream.write(" " * (width + 2) + "- " + ", ".join(
                        f"{k}: {_human(v)}" for k, v in item.items()) + "\n")
            elif isinstance(value, dict):
                stream.write(f"{key.ljust(width)}  " + ", ".join(
                    f"{k}: {_human(v)}" for k, v in value.items()) + "\n")
            else:
                stream.write(f"{key.ljust(width)}  {_human(value)}\n")
    else:
        write([record], fmt, stream)
