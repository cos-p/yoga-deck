"""Append one privacy-safe coordinate sample for the temporary QML oracle."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    values = argv if argv is not None else sys.argv[1:]
    if len(values) != 5:
        return 2
    output, role, index_text, x_text, y_text = values
    if role not in {"touch", "pen"}:
        return 2
    try:
        record = {
            "role": role,
            "index": int(index_text),
            "x": round(float(x_text), 2),
            "y": round(float(y_text), 2),
        }
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    except (OSError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
