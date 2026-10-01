"""Privacy-safe measurement of physical pen actions."""

from __future__ import annotations

import json
import os
import selectors
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

SCENARIOS = ("proximity", "tip_pressure", "button_near_tip", "button_far_tip", "eraser")
_EXPECTED_ACTION = {
    "proximity": "proximity",
    "tip_pressure": "tip",
    "button_near_tip": None,
    "button_far_tip": None,
    "eraser": "eraser",
}
_INSTRUCTIONS = {
    "proximity": "Move the pen into hover range, then well away",
    "tip_pressure": "Draw one short stroke, varying pressure",
    "button_near_tip": "Hover and press/release the side button nearest the tip once",
    "button_far_tip": "Hover and press/release the side button farthest from the tip once",
    "eraser": "Flip the pen, bring the eraser into range, then move it away",
}
_ACTIONS = frozenset(
    ("proximity", "tip", "barrel_primary", "barrel_secondary", "eraser", "pressure")
)
_STATES = frozenset(("down", "up", "sample"))


class PenProtocolError(RuntimeError):
    """Raised when records do not match the normalized public protocol."""


@dataclass(frozen=True, slots=True)
class PenActionEvent:
    trial: int
    scenario: str
    action: str
    state: str
    elapsed_ms: float
    value: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "trial": self.trial,
            "scenario": self.scenario,
            "action": self.action,
            "state": self.state,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "value": round(self.value, 6) if self.value is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    attempted: int
    complete: int
    incomplete: int

    def as_dict(self) -> dict[str, int]:
        return {
            "attempted": self.attempted,
            "complete": self.complete,
            "incomplete": self.incomplete,
        }


@dataclass(frozen=True, slots=True)
class PenActionSummary:
    scenarios: dict[str, ScenarioResult]
    pressure_peaks: tuple[float, ...]
    observed_cycles: dict[str, dict[str, int]] | None = None
    raw_cycles: dict[str, dict[str, int]] | None = None

    def as_dict(self) -> dict[str, Any]:
        peaks = sorted(self.pressure_peaks)
        return {
            "scenarios": {key: value.as_dict() for key, value in self.scenarios.items()},
            "observed_cycles": self.observed_cycles or {},
            "raw_cycles": self.raw_cycles or {},
            "pressure_peak": {
                "count": len(peaks),
                "min": round(peaks[0], 6) if peaks else None,
                "median": round(median(peaks), 6) if peaks else None,
                "max": round(peaks[-1], 6) if peaks else None,
                "samples": [round(value, 6) for value in peaks],
            },
        }


def normalize_pen_event(
    code_name: str | tuple[str, ...], value: int, *, pressure_max: int | None = None
) -> tuple[str, str, float | None] | None:
    """Reduce one evdev field to the narrow action protocol; omit position and identity."""
    keys = {
        "BTN_TOOL_PEN": "proximity",
        "BTN_TOUCH": "tip",
        "BTN_STYLUS": "barrel_primary",
        "BTN_STYLUS2": "barrel_secondary",
        "BTN_TOOL_RUBBER": "eraser",
    }
    aliases = (code_name,) if isinstance(code_name, str) else code_name
    matched_key = next((alias for alias in aliases if alias in keys), None)
    if matched_key is not None:
        return keys[matched_key], "down" if value else "up", None
    if "ABS_PRESSURE" in aliases and pressure_max and pressure_max > 0:
        normalized = min(max(value / pressure_max, 0.0), 1.0)
        return "pressure", "sample", normalized
    return None


def loads_pen_events(payload: str) -> tuple[PenActionEvent, ...]:
    result: list[PenActionEvent] = []
    try:
        for line in payload.splitlines():
            raw = json.loads(line)
            if set(raw) != {"trial", "scenario", "action", "state", "elapsed_ms", "value"}:
                raise ValueError
            trial, elapsed, value = raw["trial"], raw["elapsed_ms"], raw["value"]
            if isinstance(trial, bool) or not isinstance(trial, int) or trial < 0:
                raise ValueError
            if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
                raise ValueError
            if raw["scenario"] not in SCENARIOS:
                raise ValueError
            if raw["action"] not in _ACTIONS or raw["state"] not in _STATES:
                raise ValueError
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 1
            ):
                raise ValueError
            if (raw["action"] == "pressure") != (value is not None):
                raise ValueError
            result.append(
                PenActionEvent(
                    trial,
                    raw["scenario"],
                    raw["action"],
                    raw["state"],
                    float(elapsed),
                    float(value) if value is not None else None,
                )
            )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise PenProtocolError("pen_protocol_invalid") from error
    return tuple(result)


def analyze_pen_actions(
    events: tuple[PenActionEvent, ...] | list[PenActionEvent], *, trials: int
) -> PenActionSummary:
    if trials < 1:
        raise ValueError("pen_trials_invalid")
    results: dict[str, ScenarioResult] = {}
    peaks: list[float] = []
    observed_cycles: dict[str, dict[str, int]] = {}
    raw_cycles: dict[str, dict[str, int]] = {}
    for scenario in SCENARIOS:
        expected = _EXPECTED_ACTION[scenario]
        complete = 0
        active = False
        cycle_pressure: list[float] = []
        selected = sorted(
            (event for event in events if event.scenario == scenario),
            key=lambda event: event.elapsed_ms,
        )
        scenario_counts: dict[str, int] = {}
        scenario_raw_counts: dict[str, int] = {}
        for action in ("proximity", "tip", "barrel_primary", "barrel_secondary", "eraser"):
            action_active = False
            completed_at: list[float] = []
            for event in selected:
                if event.action != action:
                    continue
                if event.state == "down" and not action_active:
                    action_active = True
                elif event.state == "up" and action_active:
                    action_active = False
                    completed_at.append(event.elapsed_ms)
            if completed_at:
                scenario_raw_counts[action] = len(completed_at)
                if action.startswith("barrel_"):
                    debounced = [completed_at[0]]
                    for completed in completed_at[1:]:
                        if completed - debounced[-1] >= 50.0:
                            debounced.append(completed)
                    scenario_counts[action] = len(debounced)
                else:
                    scenario_counts[action] = len(completed_at)
        observed_cycles[scenario] = scenario_counts
        raw_cycles[scenario] = scenario_raw_counts
        for event in selected:
            if scenario == "tip_pressure" and active and event.action == "pressure":
                if event.value is not None:
                    cycle_pressure.append(event.value)
                continue
            if expected is None or event.action != expected:
                continue
            if event.state == "down" and not active:
                active = True
                cycle_pressure = []
            elif event.state == "up" and active:
                complete += 1
                active = False
                if scenario == "tip_pressure" and cycle_pressure:
                    peaks.append(max(cycle_pressure))
        if expected is None:
            complete = sum(
                scenario_counts.get(action, 0) for action in ("barrel_primary", "barrel_secondary")
            )
        accepted = min(complete, trials)
        results[scenario] = ScenarioResult(trials, accepted, trials - accepted)
    return PenActionSummary(results, tuple(sorted(peaks[:trials])), observed_cycles, raw_cycles)


def _trial_index(elapsed_ms: float, *, trials: int, seconds: int) -> int:
    return min(int(elapsed_ms / (seconds * 1000 / trials)), trials - 1)


class PenEventCapture:
    def __init__(self, output: Path, scenario: str, started: float, trials: int, seconds: int):
        self.output = output
        self.scenario = scenario
        self.started = started
        self.trials = trials
        self.seconds = seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._device: Any = None

    def start(self) -> None:
        from evdev import InputDevice, ecodes, list_devices

        for path in list_devices():
            candidate = InputDevice(path)
            capabilities = candidate.capabilities()
            if (
                "wacom" in candidate.name.casefold()
                and candidate.name.casefold().endswith(" pen")
                and ecodes.EV_KEY in capabilities
                and ecodes.BTN_TOOL_PEN in capabilities[ecodes.EV_KEY]
            ):
                if self._device is not None:
                    candidate.close()
                    self.close()
                    raise RuntimeError("pen_device_ambiguous")
                self._device = candidate
            else:
                candidate.close()
        if self._device is None:
            raise RuntimeError("pen_device_unavailable")
        os.set_blocking(self._device.fd, False)
        self._thread = threading.Thread(target=self._run, name="pen-action-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from evdev import ecodes

        pressure = self._device.absinfo(ecodes.ABS_PRESSURE)
        pressure_max = pressure.max if pressure is not None else None
        selector = selectors.DefaultSelector()
        selector.register(self._device.fd, selectors.EVENT_READ)
        try:
            while not self._stop.is_set():
                if not selector.select(timeout=0.05):
                    continue
                for raw in self._device.read():
                    code_name = ecodes.bytype.get(raw.type, {}).get(raw.code)
                    normalized = normalize_pen_event(
                        code_name or "", raw.value, pressure_max=pressure_max
                    )
                    if normalized is None:
                        continue
                    elapsed = (time.monotonic() - self.started) * 1000
                    action, state, value = normalized
                    event = PenActionEvent(
                        _trial_index(elapsed, trials=self.trials, seconds=self.seconds),
                        self.scenario,
                        action,
                        state,
                        elapsed,
                        value,
                    )
                    with self.output.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(event.as_dict(), sort_keys=True) + "\n")
        finally:
            selector.close()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self._device is not None:
            self._device.close()
            self._device = None


def run_guided_pen_campaign(
    *,
    trials: int = 5,
    seconds: int = 30,
    scenarios: tuple[str, ...] | None = None,
    prompt_callback: Any = print,
) -> PenActionSummary:
    """Observe physical pen input only; never inject input or mutate the compositor."""
    if trials < 1 or seconds < trials:
        raise ValueError("pen_timing_invalid")
    selected = scenarios or SCENARIOS
    if not selected or any(scenario not in SCENARIOS for scenario in selected):
        raise ValueError("pen_scenarios_invalid")
    with tempfile.TemporaryDirectory(prefix="yoga-deck-pen-") as directory:
        output = Path(directory) / "events.jsonl"
        for scenario in selected:
            prompt_callback(
                f"[{scenario}] {_INSTRUCTIONS[scenario]}. Repeat {trials} times, "
                f"once per {seconds / trials:g}-second slot."
            )
            started = time.monotonic()
            capture = PenEventCapture(output, scenario, started, trials, seconds)
            try:
                capture.start()
                time.sleep(seconds)
            finally:
                capture.close()
        events = loads_pen_events(output.read_text() if output.exists() else "")
        return analyze_pen_actions(events, trials=trials)
