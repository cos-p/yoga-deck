"""Guided, privacy-safe measurement of interactive OCR capture."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median
from typing import Any


@dataclass(frozen=True, slots=True)
class OcrCaptureSummary:
    attempted: int
    verified: int
    unverified: int
    command_failed: int
    latency_ms: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        values = self.latency_ms
        return {
            "attempted": self.attempted,
            "verified": self.verified,
            "unverified": self.unverified,
            "command_failed": self.command_failed,
            "latency_ms": {
                "count": len(values),
                "median": round(median(values), 3) if values else None,
                "max": round(max(values), 3) if values else None,
            },
        }


def run_guided_ocr_capture(
    *,
    trials: int = 5,
    capture: Callable[[], None] | None = None,
    prompt: Callable[[str], None] = print,
    confirm: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> OcrCaptureSummary:
    """Measure user-approved selections without retaining imagery or recognized text."""
    if trials < 1:
        raise ValueError("ocr_trials_invalid")
    if capture is None:
        from yoga_deck.adapters.omarchy_ocr import OmarchyOcrAdapter

        capture = OmarchyOcrAdapter().capture_text
    if confirm is None:
        confirm = _confirm

    verified = 0
    unverified = 0
    command_failed = 0
    latency_ms: list[float] = []
    for trial in range(1, trials + 1):
        prompt(
            f"[OCR {trial}/{trials}] Select only a prepared, non-sensitive text sample "
            "in the picker."
        )
        started = clock()
        try:
            capture()
        except Exception:
            command_failed += 1
            latency_ms.append((clock() - started) * 1000)
            continue
        latency_ms.append((clock() - started) * 1000)
        if confirm():
            verified += 1
        else:
            unverified += 1

    return OcrCaptureSummary(trials, verified, unverified, command_failed, tuple(latency_ms))


def _confirm() -> bool:
    answer = input("Did the recognized result match the selected sample? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}
