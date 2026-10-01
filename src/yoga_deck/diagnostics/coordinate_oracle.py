"""Temporary Quickshell coordinate surface and its narrow result protocol."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .orientation_campaign import TargetPoint

QML_PATH = Path(__file__).with_name("coordinate_oracle.qml")


class OracleError(RuntimeError):
    pass


def parse_samples(payload: str) -> dict[str, tuple[tuple[float, float], ...]]:
    expected = [(role, index) for role in ("touch", "pen") for index in range(5)]
    observed = []
    samples: dict[str, list[tuple[float, float]]] = {"touch": [], "pen": []}
    try:
        for line in payload.splitlines():
            record = json.loads(line)
            role = record["role"]
            index = record["index"]
            x = record["x"]
            y = record["y"]
            if role not in samples or not isinstance(index, int):
                raise ValueError
            if isinstance(x, bool) or isinstance(y, bool):
                raise ValueError
            observed.append((role, index))
            samples[role].append((float(x), float(y)))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise OracleError("oracle_protocol_invalid") from error
    if observed != expected:
        raise OracleError("oracle_protocol_invalid")
    return {role: tuple(values) for role, values in samples.items()}


def run_coordinate_oracle(
    targets: Sequence[TargetPoint], *, timeout: float = 120.0
) -> dict[str, tuple[tuple[float, float], ...]]:
    if len(targets) != 5:
        raise OracleError("oracle_targets_invalid")
    with tempfile.TemporaryDirectory(prefix="yoga-deck-oracle-") as directory:
        output = Path(directory) / "samples.jsonl"
        environment = os.environ.copy()
        environment.update(
            {
                "YOGA_DECK_ORACLE_OUTPUT": str(output),
                "YOGA_DECK_ORACLE_PYTHON": os.environ.get(
                    "YOGA_DECK_ORACLE_PYTHON", os.sys.executable
                ),
                "YOGA_DECK_ORACLE_TARGETS": json.dumps(
                    [{"name": p.name, "x": p.target_x, "y": p.target_y} for p in targets]
                ),
            }
        )
        try:
            result = subprocess.run(
                ["qs", "--no-color", "--path", str(QML_PATH)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OracleError("oracle_unavailable") from error
        if result.returncode != 0 or not output.exists():
            raise OracleError("oracle_failed")
        return parse_samples(output.read_text())
