"""Write an application-delivered touch to the narrow palm protocol."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .palm_rejection import SCENARIOS, trial_index


def main(argv: list[str] | None = None) -> int:
    values = argv if argv is not None else sys.argv[1:]
    if len(values) != 5:
        return 2
    output, scenario, trials_text, duration_text, started_text = values
    if scenario not in SCENARIOS:
        return 2
    try:
        elapsed_ms = (time.monotonic() - float(started_text)) * 1000
        record = {
            "trial": trial_index(
                elapsed_ms,
                trials=int(trials_text),
                duration_seconds=int(duration_text),
            ),
            "scenario": scenario,
            "source": "surface",
            "event": "touch",
            "elapsed_ms": round(elapsed_ms, 3),
        }
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    except (OSError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
