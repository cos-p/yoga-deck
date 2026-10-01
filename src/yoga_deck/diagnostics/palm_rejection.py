"""Normalized palm-rejection observations and evidence summaries."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

QML_PATH = Path(__file__).with_name("palm_rejection.qml")

SCENARIOS = ("palm_only", "pen_hover_palm", "pen_tip_palm", "recovery")
SOURCES = ("control", "pen", "raw_touch", "surface")
EVENTS = (
    "trial_start",
    "proximity_in",
    "proximity_out",
    "tip_down",
    "tip_up",
    "contact",
    "touch",
)


class PalmProtocolError(RuntimeError):
    """Raised when a capture does not match the public normalized protocol."""


@dataclass(frozen=True, slots=True)
class PalmEvent:
    trial: int
    scenario: str
    source: str
    event: str
    elapsed_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "trial": self.trial,
            "scenario": self.scenario,
            "source": self.source,
            "event": self.event,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


@dataclass(frozen=True, slots=True)
class ScenarioSummary:
    attempted: int
    delivered: int
    suppressed: int
    invalid_state: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "attempted": self.attempted,
            "delivered": self.delivered,
            "suppressed": self.suppressed,
            "invalid_state": self.invalid_state,
        }


@dataclass(frozen=True, slots=True)
class PalmSummary:
    scenarios: dict[str, ScenarioSummary]
    recovery_latency_ms: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        latencies = sorted(self.recovery_latency_ms)
        return {
            "scenarios": {name: value.as_dict() for name, value in self.scenarios.items()},
            "recovery_latency_ms": {
                "count": len(latencies),
                "min": round(latencies[0], 3) if latencies else None,
                "median": round(median(latencies), 3) if latencies else None,
                "max": round(latencies[-1], 3) if latencies else None,
                "samples": [round(value, 3) for value in latencies],
            },
        }


def loads_events(payload: str) -> tuple[PalmEvent, ...]:
    records: list[PalmEvent] = []
    try:
        for line in payload.splitlines():
            raw = json.loads(line)
            if set(raw) != {"trial", "scenario", "source", "event", "elapsed_ms"}:
                raise ValueError
            trial = raw["trial"]
            elapsed = raw["elapsed_ms"]
            if isinstance(trial, bool) or not isinstance(trial, int) or trial < 0:
                raise ValueError
            if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
                raise ValueError
            if raw["scenario"] not in SCENARIOS:
                raise ValueError
            if raw["source"] not in SOURCES or raw["event"] not in EVENTS:
                raise ValueError
            records.append(
                PalmEvent(
                    trial=trial,
                    scenario=raw["scenario"],
                    source=raw["source"],
                    event=raw["event"],
                    elapsed_ms=float(elapsed),
                )
            )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise PalmProtocolError("palm_protocol_invalid") from error
    return tuple(records)


def analyze_trials(events: list[PalmEvent] | tuple[PalmEvent, ...]) -> PalmSummary:
    grouped: dict[tuple[str, int], list[PalmEvent]] = {}
    for event in events:
        grouped.setdefault((event.scenario, event.trial), []).append(event)

    scenarios: dict[str, ScenarioSummary] = {}
    for scenario in SCENARIOS:
        proximity = tip_down = False
        guided_trials: set[int] = set()
        state_valid_trials: set[int] = set()
        delivered_trials: set[int] = set()
        observed_contact_trials: set[int] = set()
        scenario_events = sorted(
            (event for event in events if event.scenario == scenario),
            key=lambda event: event.elapsed_ms,
        )
        for event in scenario_events:
            if event.source == "control" and event.event == "trial_start":
                guided_trials.add(event.trial)
            elif event.source == "pen":
                if event.event == "proximity_in":
                    proximity = True
                elif event.event == "proximity_out":
                    proximity = tip_down = False
                elif event.event == "tip_down":
                    tip_down = True
                elif event.event == "tip_up":
                    tip_down = False
            state_matches = (
                (scenario == "palm_only" and not proximity)
                or (scenario in {"pen_hover_palm", "recovery"} and proximity and not tip_down)
                or (scenario == "pen_tip_palm" and proximity and tip_down)
            )
            if state_matches:
                state_valid_trials.add(event.trial)
                if event.source == "surface" and event.event == "touch":
                    delivered_trials.add(event.trial)
            if event.source == "raw_touch" and event.event == "contact":
                observed_contact_trials.add(event.trial)
        if scenario == "palm_only":
            state_valid_trials |= guided_trials
        attempted_trials = guided_trials & state_valid_trials
        delivered_trials &= attempted_trials
        attempted = len(attempted_trials)
        delivered = len(delivered_trials)
        scenarios[scenario] = ScenarioSummary(
            attempted,
            delivered,
            attempted - delivered,
            len((guided_trials or observed_contact_trials) - state_valid_trials),
        )

    recovery: list[float] = []
    for (scenario, _trial), trial_events in grouped.items():
        if scenario != "recovery":
            continue
        exits = sorted(
            event.elapsed_ms
            for event in trial_events
            if event.source == "pen" and event.event == "proximity_out"
        )
        touches = sorted(
            event.elapsed_ms
            for event in trial_events
            if event.source == "surface" and event.event == "touch"
        )
        if exits and touches:
            first_touch = next((value for value in touches if value >= exits[0]), None)
            if first_touch is not None:
                preceding_exits = [value for value in exits if value <= first_touch]
                recovery.append(first_touch - preceding_exits[-1])
    return PalmSummary(scenarios, tuple(recovery))


def trial_index(elapsed_ms: float, *, trials: int, duration_seconds: int) -> int:
    if trials < 1 or duration_seconds < 1 or elapsed_ms < 0:
        raise ValueError("palm_trial_boundary_invalid")
    slot_ms = duration_seconds * 1000 / trials
    return min(int(elapsed_ms / slot_ms), trials - 1)


def _append_event(path: Path, event: PalmEvent) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event.as_dict(), sort_keys=True) + "\n")


class WacomEventCapture:
    """Capture only normalized pen-state and touch-contact transitions."""

    def __init__(
        self,
        output: Path,
        scenario: str,
        started: float,
        *,
        trials: int,
        duration_seconds: int,
    ) -> None:
        self.output = output
        self.scenario = scenario
        self.started = started
        self.trials = trials
        self.duration_seconds = duration_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._devices: list[Any] = []

    def start(self) -> None:
        from evdev import InputDevice, list_devices

        for device_path in list_devices():
            candidate = InputDevice(device_path)
            normalized = candidate.name.casefold()
            if "wacom" in normalized and (
                normalized.endswith(" pen") or normalized.endswith(" finger")
            ):
                os.set_blocking(candidate.fd, False)
                self._devices.append(candidate)
            else:
                candidate.close()
        roles = {
            "pen" if device.name.casefold().endswith(" pen") else "touch"
            for device in self._devices
        }
        if roles != {"pen", "touch"}:
            self.close()
            raise RuntimeError("palm_devices_unavailable")
        self._thread = threading.Thread(target=self._run, name="palm-event-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from evdev import ecodes

        selector = selectors.DefaultSelector()
        for device in self._devices:
            selector.register(device.fd, selectors.EVENT_READ, device)
        try:
            while not self._stop.is_set():
                for key, _mask in selector.select(timeout=0.05):
                    device = key.data
                    is_pen = device.name.casefold().endswith(" pen")
                    for raw in device.read():
                        event_name = None
                        if raw.type == ecodes.EV_KEY and is_pen:
                            if raw.code in {ecodes.BTN_TOOL_PEN, ecodes.BTN_TOOL_RUBBER}:
                                event_name = "proximity_in" if raw.value else "proximity_out"
                            elif raw.code == ecodes.BTN_TOUCH:
                                event_name = "tip_down" if raw.value else "tip_up"
                        elif (
                            raw.type == ecodes.EV_KEY
                            and not is_pen
                            and raw.code == ecodes.BTN_TOUCH
                            and raw.value
                        ):
                            event_name = "contact"
                        if event_name is not None:
                            _append_event(
                                self.output,
                                PalmEvent(
                                    trial_index(
                                        (time.monotonic() - self.started) * 1000,
                                        trials=self.trials,
                                        duration_seconds=self.duration_seconds,
                                    ),
                                    self.scenario,
                                    "pen" if is_pen else "raw_touch",
                                    event_name,
                                    (time.monotonic() - self.started) * 1000,
                                ),
                            )
        finally:
            selector.close()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        for device in self._devices:
            device.close()
        self._devices.clear()


_INSTRUCTIONS = {
    "palm_only": "Touch the panel firmly with your palm only.",
    "pen_hover_palm": ("Hover the pen close to the panel, then touch the panel with your palm."),
    "pen_tip_palm": ("Hold the pen tip on the panel, then touch elsewhere with your palm."),
    "recovery": (
        "Hover the pen, touch with your palm, move the pen well away, then repeatedly tap with "
        "one finger until a touch registers."
    ),
}


def run_guided_palm_campaign(
    *,
    trials: int = 5,
    prompt_callback: Any = print,
    observation_seconds: int = 30,
    recovery_seconds: int = 45,
    timeout: float = 50.0,
    scenarios: tuple[str, ...] | None = None,
) -> PalmSummary:
    """Run a non-injecting, full-screen palm-rejection measurement."""
    if trials < 1:
        raise ValueError("palm_trials_invalid")
    if observation_seconds < 5 or recovery_seconds < 5 or timeout <= recovery_seconds:
        raise ValueError("palm_timing_invalid")
    selected = scenarios or SCENARIOS
    if not selected or any(scenario not in SCENARIOS for scenario in selected):
        raise ValueError("palm_scenarios_invalid")
    with tempfile.TemporaryDirectory(prefix="yoga-deck-palm-") as directory:
        output = Path(directory) / "events.jsonl"
        for scenario in selected:
            duration = recovery_seconds if scenario == "recovery" else observation_seconds
            started = time.monotonic()
            for trial in range(trials):
                _append_event(
                    output,
                    PalmEvent(
                        trial,
                        scenario,
                        "control",
                        "trial_start",
                        trial * duration * 1000 / trials,
                    ),
                )
            environment = os.environ.copy()
            environment.update(
                {
                    "YOGA_DECK_PALM_OUTPUT": str(output),
                    "YOGA_DECK_PALM_PYTHON": os.environ.get(
                        "YOGA_DECK_PALM_PYTHON", os.sys.executable
                    ),
                    "YOGA_DECK_PALM_SCENARIO": scenario,
                    "YOGA_DECK_PALM_STARTED": repr(started),
                    "YOGA_DECK_PALM_INSTRUCTION": (
                        f"{_INSTRUCTIONS[scenario]} Repeat {trials} times."
                    ),
                    "YOGA_DECK_PALM_SECONDS": str(duration),
                    "YOGA_DECK_PALM_TRIALS": str(trials),
                }
            )
            capture = WacomEventCapture(
                output,
                scenario,
                started,
                trials=trials,
                duration_seconds=duration,
            )
            surface = None
            try:
                capture.start()
                surface = subprocess.Popen(
                    ["qs", "--no-color", "--path", str(QML_PATH)],
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                prompt_callback(f"[{scenario}] {_INSTRUCTIONS[scenario]} Repeat {trials} times.")
                surface.wait(timeout=timeout)
            finally:
                if surface is not None:
                    surface.terminate()
                    try:
                        surface.wait(timeout=min(timeout, 2.0))
                    except subprocess.TimeoutExpired:
                        surface.kill()
                        surface.wait(timeout=2.0)
                capture.close()
        return analyze_trials(loads_events(output.read_text() if output.exists() else ""))
