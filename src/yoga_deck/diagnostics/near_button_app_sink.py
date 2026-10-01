"""Append one application-delivered stylus mode to the narrow protocol."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .near_button_app import POINTER_TYPES
from .palm_rejection import trial_index


def main(argv: list[str] | None = None) -> int:
    values = argv if argv is not None else sys.argv[1:]
    if len(values) != 5:
        return 2
    output, pointer_type, trials_text, seconds_text, started_text = values
    if pointer_type not in POINTER_TYPES:
        return 2
    try:
        elapsed_ms = (time.monotonic() - float(started_text)) * 1000
        record = {
            "trial": trial_index(
                elapsed_ms,
                trials=int(trials_text),
                duration_seconds=int(seconds_text),
            ),
            "pointer_type": pointer_type,
            "elapsed_ms": round(elapsed_ms, 3),
        }
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    except (OSError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
