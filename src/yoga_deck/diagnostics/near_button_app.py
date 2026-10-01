"""Correlate the near-tip pen button's raw eraser mode with Qt delivery."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pen_actions import PenActionEvent, PenEventCapture, loads_pen_events

QML_PATH = Path(__file__).with_name("near_button_app.qml")
POINTER_TYPES = frozenset(("pen", "eraser"))


class NearButtonProtocolError(RuntimeError):
    """Raised when application observations exceed the public protocol."""


@dataclass(frozen=True, slots=True)
class ApplicationPointerEvent:
    trial: int
    pointer_type: str
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class NearButtonSummary:
    attempted: int
    control_raw_pen: int
    control_application_pen: int
    raw_eraser: int
    application_pen: int
    application_eraser: int
    confirmed: int
    raw_only: int
    application_only: int

    def as_dict(self) -> dict[str, int]:
        return {
            "attempted": self.attempted,
            "control_raw_pen": self.control_raw_pen,
            "control_application_pen": self.control_application_pen,
            "raw_eraser": self.raw_eraser,
            "application_pen": self.application_pen,
            "application_eraser": self.application_eraser,
            "confirmed": self.confirmed,
            "raw_only": self.raw_only,
            "application_only": self.application_only,
        }


def loads_application_events(payload: str) -> tuple[ApplicationPointerEvent, ...]:
    events: list[ApplicationPointerEvent] = []
    try:
        for line in payload.splitlines():
            raw = json.loads(line)
            if set(raw) != {"trial", "pointer_type", "elapsed_ms"}:
                raise ValueError
            trial = raw["trial"]
            elapsed_ms = raw["elapsed_ms"]
            if isinstance(trial, bool) or not isinstance(trial, int) or trial < 0:
                raise ValueError
            if raw["pointer_type"] not in POINTER_TYPES:
                raise ValueError
            if (
                isinstance(elapsed_ms, bool)
                or not isinstance(elapsed_ms, (int, float))
                or elapsed_ms < 0
            ):
                raise ValueError
            events.append(ApplicationPointerEvent(trial, raw["pointer_type"], float(elapsed_ms)))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise NearButtonProtocolError("near_button_protocol_invalid") from error
    return tuple(events)


def _completed_raw_trials(
    events: tuple[PenActionEvent, ...], *, scenario: str, action: str
) -> set[int]:
    active_trials: set[int] = set()
    complete_trials: set[int] = set()
    for event in sorted(events, key=lambda item: item.elapsed_ms):
        if event.scenario != scenario or event.action != action:
            continue
        if event.state == "down":
            active_trials.add(event.trial)
        elif event.state == "up" and event.trial in active_trials:
            active_trials.remove(event.trial)
            complete_trials.add(event.trial)
    return complete_trials


def analyze_near_button_trials(
    raw_events: tuple[PenActionEvent, ...],
    application_events: tuple[ApplicationPointerEvent, ...],
    *,
    trials: int,
    control_raw_events: tuple[PenActionEvent, ...] = (),
    control_application_events: tuple[ApplicationPointerEvent, ...] = (),
) -> NearButtonSummary:
    if trials < 1:
        raise ValueError("near_button_trials_invalid")
    valid_trials = set(range(trials))
    raw_trials = (
        _completed_raw_trials(raw_events, scenario="button_near_tip", action="eraser")
        & valid_trials
    )
    application_trials = {
        event.trial
        for event in application_events
        if event.pointer_type == "eraser" and event.trial in valid_trials
    }
    application_pen_trials = {
        event.trial
        for event in application_events
        if event.pointer_type == "pen" and event.trial in valid_trials
    }
    confirmed = raw_trials & application_trials
    control_raw_trials = _completed_raw_trials(
        control_raw_events, scenario="proximity", action="proximity"
    )
    control_application_trials = {
        event.trial for event in control_application_events if event.pointer_type == "pen"
    }
    return NearButtonSummary(
        attempted=trials,
        control_raw_pen=len(control_raw_trials),
        control_application_pen=len(control_application_trials),
        raw_eraser=len(raw_trials),
        application_pen=len(application_pen_trials),
        application_eraser=len(application_trials),
        confirmed=len(confirmed),
        raw_only=len(raw_trials - application_trials),
        application_only=len(application_trials - raw_trials),
    )


def run_guided_near_button_test(
    *,
    trials: int = 3,
    seconds: int = 15,
    timeout: float | None = None,
    prompt_callback: Any = print,
) -> NearButtonSummary:
    """Open a temporary surface and observe physical input without injecting events."""
    if trials < 1 or seconds < trials:
        raise ValueError("near_button_timing_invalid")
    wait_timeout = timeout if timeout is not None else seconds + 5.0
    if wait_timeout <= seconds:
        raise ValueError("near_button_timeout_invalid")

    with tempfile.TemporaryDirectory(prefix="yoga-deck-near-button-") as directory:
        root = Path(directory)

        def capture_phase(
            *,
            label: str,
            scenario: str,
            phase_trials: int,
            phase_seconds: int,
            instruction: str,
        ) -> tuple[tuple[PenActionEvent, ...], tuple[ApplicationPointerEvent, ...]]:
            raw_output = root / f"{label}-raw.jsonl"
            application_output = root / f"{label}-application.jsonl"
            started = time.monotonic()
            environment = os.environ.copy()
            environment.update(
                {
                    "YOGA_DECK_NEAR_BUTTON_OUTPUT": str(application_output),
                    "YOGA_DECK_NEAR_BUTTON_PYTHON": os.environ.get(
                        "YOGA_DECK_NEAR_BUTTON_PYTHON", os.sys.executable
                    ),
                    "YOGA_DECK_NEAR_BUTTON_SECONDS": str(phase_seconds),
                    "YOGA_DECK_NEAR_BUTTON_TRIALS": str(phase_trials),
                    "YOGA_DECK_NEAR_BUTTON_STARTED": repr(started),
                    "YOGA_DECK_NEAR_BUTTON_INSTRUCTION": instruction,
                }
            )
            capture = PenEventCapture(raw_output, scenario, started, phase_trials, phase_seconds)
            surface: subprocess.Popen[str] | None = None
            try:
                capture.start()
                surface = subprocess.Popen(
                    ["qs", "--no-color", "--path", str(QML_PATH)],
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                prompt_callback(f"[{label}] {instruction}")
                surface.wait(timeout=max(wait_timeout, phase_seconds + 1.0))
                if surface.returncode != 0:
                    raise RuntimeError("near_button_surface_failed")
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError("near_button_surface_unavailable") from error
            finally:
                if surface is not None and surface.poll() is None:
                    surface.terminate()
                    try:
                        surface.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        surface.kill()
                        surface.wait(timeout=2.0)
                capture.close()
            raw_events = loads_pen_events(raw_output.read_text() if raw_output.exists() else "")
            application_events = loads_application_events(
                application_output.read_text() if application_output.exists() else ""
            )
            return raw_events, application_events

        slot_seconds = max(5, (seconds + trials - 1) // trials)
        control_raw, control_application = capture_phase(
            label="near_button_control",
            scenario="proximity",
            phase_trials=1,
            phase_seconds=slot_seconds,
            instruction=(
                "With both buttons released and the pen well away, bring the normal tip in, "
                "make one short stroke, then move the pen well away."
            ),
        )
        raw_events, application_events = capture_phase(
            label="near_button_application",
            scenario="button_near_tip",
            phase_trials=trials,
            phase_seconds=seconds,
            instruction=(
                "Start well away, hold the near-tip button, bring the pen in, make one short "
                f"stroke, move well away, then release; repeat once per {seconds / trials:g}-"
                "second slot."
            ),
        )
        return analyze_near_button_trials(
            raw_events,
            application_events,
            trials=trials,
            control_raw_events=control_raw,
            control_application_events=control_application,
        )
