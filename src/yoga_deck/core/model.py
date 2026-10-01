"""Pure domain values for convertible posture and rotation policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class Orientation(Enum):
    """The four compositor-supported display orientations."""

    NORMAL = "normal"
    RIGHT = "right"
    INVERTED = "inverted"
    LEFT = "left"


@dataclass(frozen=True, slots=True)
class State:
    """Reducer-owned state; defaults represent the recoverable startup posture."""

    sequence: int = 0
    tablet_mode_observed: bool | None = False
    internal_inputs_enabled: bool = True
    observed_orientation: Orientation | None = Orientation.NORMAL
    applied_orientation: Orientation = Orientation.NORMAL
    orientation_locked: bool = False
    shutdown_requested: bool = False
    osk_enabled: bool = False
    last_pen_action_source_sequence: int = 0


@dataclass(frozen=True, slots=True)
class TabletModeChanged:
    """A normalized SW_TABLET_MODE reading; None means unavailable or invalid."""

    enabled: bool | None


@dataclass(frozen=True, slots=True)
class OrientationChanged:
    """A normalized orientation reading; None means unavailable or invalid."""

    orientation: Orientation | None


@dataclass(frozen=True, slots=True)
class OrientationLockChanged:
    locked: bool


@dataclass(frozen=True, slots=True)
class ManualOrientationRequested:
    """A user-requested transform that is allowed while sensor rotation is locked."""

    orientation: Orientation


@dataclass(frozen=True, slots=True)
class InputRecoveryRequested:
    """Explicit user request to recover the owned internal inputs."""


class PenAction(Enum):
    """Semantic action emitted by a measured and debounced pen adapter."""

    CAPTURE_TEXT = "capture_text"
    OPEN_SCRATCHPAD = "open_scratchpad"


@dataclass(frozen=True, slots=True)
class PenActionRequested:
    """A fresh debounced pen action; source sequence prevents replaying a click."""

    action: PenAction
    source_sequence: int


@dataclass(frozen=True, slots=True)
class ThemeChanged:
    """The desktop theme changed; the theme's identity is deliberately not retained."""


@dataclass(frozen=True, slots=True)
class SettingsChanged:
    """A user setting was stored; which one is deliberately not retained.

    The backend re-reads the whole settings file when it is respawned, so the reducer has
    nothing to do with the name or the value, and carrying either would put a font name or a
    key binding into the event journal for no gain.
    """


@dataclass(frozen=True, slots=True)
class ShutdownRequested:
    """Request a safe shutdown plan."""


@dataclass(frozen=True, slots=True)
class ReconcileRequested:
    """Reassert reducer-owned desired state after an adapter reconnect."""


@dataclass(frozen=True, slots=True)
class ResumeRequested:
    """Request fresh hardware observation after the system resumes."""


@dataclass(frozen=True, slots=True)
class MonitorTopologyChanged:
    """A monitor event with identity intentionally discarded at the boundary."""


type Event = (
    TabletModeChanged
    | OrientationChanged
    | OrientationLockChanged
    | ManualOrientationRequested
    | InputRecoveryRequested
    | PenActionRequested
    | ThemeChanged
    | SettingsChanged
    | ReconcileRequested
    | ResumeRequested
    | MonitorTopologyChanged
    | ShutdownRequested
)


@dataclass(frozen=True, slots=True)
class SetInternalInputsEnabled:
    """Enable or inhibit the discovered internal keyboard and pointing devices."""

    enabled: bool
    sequence: int


@dataclass(frozen=True, slots=True)
class SetOskEnabled:
    """Allow the OSK backend in tablet mode or stop it in laptop mode."""

    enabled: bool
    sequence: int


@dataclass(frozen=True, slots=True)
class RefreshOskTheme:
    """Re-resolve everything a running OSK backend takes from argv, by respawning it.

    Named for the theme because that was its first cause, but the backend rebuilds its whole
    command line on respawn, so a settings change plans the same effect.
    """

    sequence: int


@dataclass(frozen=True, slots=True)
class ApplyRotation:
    """One coordinated display, touchscreen, and pen rotation intent."""

    targets: ClassVar[tuple[str, ...]] = ("display", "touchscreen", "pen")

    orientation: Orientation
    sequence: int


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A privacy-safe diagnostic code emitted for rejected sensor input."""

    code: str
    sequence: int


@dataclass(frozen=True, slots=True)
class CaptureText:
    """Request an interactive OCR selection through the configured OCR adapter."""

    sequence: int


@dataclass(frozen=True, slots=True)
class LaunchScratchpad:
    """Request launch of the configured optional scratchpad application."""

    sequence: int


@dataclass(frozen=True, slots=True)
class ReconcileObservedState:
    """Reconnect adapters and refresh observed state before reconciliation."""

    sequence: int


type Effect = (
    SetInternalInputsEnabled
    | SetOskEnabled
    | RefreshOskTheme
    | ApplyRotation
    | CaptureText
    | LaunchScratchpad
    | ReconcileObservedState
    | Diagnostic
)


@dataclass(frozen=True, slots=True)
class Transition:
    state: State
    effects: tuple[Effect, ...] = ()
