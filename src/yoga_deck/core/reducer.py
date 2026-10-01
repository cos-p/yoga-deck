"""Pure Yoga Deck state transition reducer."""

from __future__ import annotations

from dataclasses import replace
from typing import assert_never

from .model import (
    ApplyRotation,
    CaptureText,
    Diagnostic,
    Event,
    InputRecoveryRequested,
    LaunchScratchpad,
    ManualOrientationRequested,
    MonitorTopologyChanged,
    OrientationChanged,
    OrientationLockChanged,
    PenAction,
    PenActionRequested,
    ReconcileObservedState,
    ReconcileRequested,
    RefreshOskTheme,
    ResumeRequested,
    SetInternalInputsEnabled,
    SetOskEnabled,
    SettingsChanged,
    ShutdownRequested,
    State,
    TabletModeChanged,
    ThemeChanged,
    Transition,
)


def reduce(state: State, event: Event) -> Transition:
    """Return the next immutable state and desired effects for one event."""

    if isinstance(event, TabletModeChanged):
        return _tablet_mode_changed(state, event)
    if isinstance(event, OrientationChanged):
        return _orientation_changed(state, event)
    if isinstance(event, OrientationLockChanged):
        return _orientation_lock_changed(state, event)
    if isinstance(event, ManualOrientationRequested):
        return _manual_orientation_requested(state, event)
    if isinstance(event, InputRecoveryRequested):
        return _input_recovery_requested(state)
    if isinstance(event, PenActionRequested):
        return _pen_action_requested(state, event)
    if isinstance(event, (ThemeChanged, SettingsChanged)):
        return _osk_respawn_requested(state)
    if isinstance(event, ReconcileRequested):
        return _reconcile_requested(state)
    if isinstance(event, ResumeRequested):
        return _resume_requested(state)
    if isinstance(event, MonitorTopologyChanged):
        return _monitor_topology_changed(state)
    if isinstance(event, ShutdownRequested):
        return _shutdown_requested(state)
    assert_never(event)


def _tablet_mode_changed(state: State, event: TabletModeChanged) -> Transition:
    if event.enabled is state.tablet_mode_observed:
        return Transition(state)

    sequence = state.sequence + 1
    observed = replace(state, sequence=sequence, tablet_mode_observed=event.enabled)
    if event.enabled is None:
        return Transition(
            observed,
            (Diagnostic(code="unknown_tablet_mode", sequence=sequence),),
        )

    inputs_enabled = not event.enabled
    osk_enabled = event.enabled
    next_state = replace(
        observed,
        internal_inputs_enabled=inputs_enabled,
        osk_enabled=osk_enabled,
    )
    effects = []
    if inputs_enabled != state.internal_inputs_enabled:
        effects.append(SetInternalInputsEnabled(enabled=inputs_enabled, sequence=sequence))
    if osk_enabled != state.osk_enabled:
        effects.append(SetOskEnabled(enabled=osk_enabled, sequence=sequence))
    return Transition(next_state, tuple(effects))


def _orientation_changed(state: State, event: OrientationChanged) -> Transition:
    if event.orientation is state.observed_orientation:
        return Transition(state)

    sequence = state.sequence + 1
    observed = replace(state, sequence=sequence, observed_orientation=event.orientation)
    if event.orientation is None:
        return Transition(
            observed,
            (Diagnostic(code="unknown_orientation", sequence=sequence),),
        )
    if state.orientation_locked or event.orientation is state.applied_orientation:
        return Transition(observed)

    next_state = replace(observed, applied_orientation=event.orientation)
    return Transition(
        next_state,
        (ApplyRotation(orientation=event.orientation, sequence=sequence),),
    )


def _orientation_lock_changed(state: State, event: OrientationLockChanged) -> Transition:
    if event.locked is state.orientation_locked:
        return Transition(state)

    sequence = state.sequence + 1
    next_state = replace(state, sequence=sequence, orientation_locked=event.locked)
    observed = state.observed_orientation
    if event.locked or observed is None or observed is state.applied_orientation:
        return Transition(next_state)

    next_state = replace(next_state, applied_orientation=observed)
    return Transition(
        next_state,
        (ApplyRotation(orientation=observed, sequence=sequence),),
    )


def _manual_orientation_requested(state: State, event: ManualOrientationRequested) -> Transition:
    if event.orientation is state.applied_orientation:
        return Transition(state)
    sequence = state.sequence + 1
    next_state = replace(state, sequence=sequence, applied_orientation=event.orientation)
    return Transition(
        next_state,
        (ApplyRotation(orientation=event.orientation, sequence=sequence),),
    )


def _input_recovery_requested(state: State) -> Transition:
    sequence = state.sequence + 1
    next_state = replace(state, sequence=sequence, internal_inputs_enabled=True)
    return Transition(
        next_state,
        (SetInternalInputsEnabled(enabled=True, sequence=sequence),),
    )


def _pen_action_requested(state: State, event: PenActionRequested) -> Transition:
    if event.source_sequence < 1:
        return Transition(
            state,
            (Diagnostic(code="invalid_pen_action_sequence", sequence=state.sequence),),
        )
    if event.source_sequence <= state.last_pen_action_source_sequence:
        return Transition(state)

    sequence = state.sequence + 1
    next_state = replace(
        state,
        sequence=sequence,
        last_pen_action_source_sequence=event.source_sequence,
    )
    if event.action is PenAction.CAPTURE_TEXT:
        return Transition(next_state, (CaptureText(sequence=sequence),))
    if event.action is PenAction.OPEN_SCRATCHPAD:
        return Transition(next_state, (LaunchScratchpad(sequence=sequence),))
    assert_never(event.action)


def _osk_respawn_requested(state: State) -> Transition:
    """A theme or settings change only means something while the OSK is meant to be running.

    Both plan the same effect because the backend rebuilds its whole command line when it is
    respawned, so there is nothing a settings change needs that a retint does not already do.
    """

    if not state.osk_enabled:
        return Transition(state)

    sequence = state.sequence + 1
    return Transition(replace(state, sequence=sequence), (RefreshOskTheme(sequence=sequence),))


def _shutdown_requested(state: State) -> Transition:
    if state.shutdown_requested:
        return Transition(state)

    sequence = state.sequence + 1
    next_state = replace(
        state,
        sequence=sequence,
        internal_inputs_enabled=True,
        osk_enabled=False,
        shutdown_requested=True,
    )
    return Transition(
        next_state,
        (
            SetInternalInputsEnabled(enabled=True, sequence=sequence),
            SetOskEnabled(enabled=False, sequence=sequence),
        ),
    )


def _reconcile_requested(state: State) -> Transition:
    sequence = state.sequence + 1
    next_state = replace(state, sequence=sequence)
    return Transition(
        next_state,
        (
            SetInternalInputsEnabled(state.internal_inputs_enabled, sequence),
            SetOskEnabled(state.osk_enabled, sequence),
        ),
    )


def _resume_requested(state: State) -> Transition:
    sequence = state.sequence + 1
    next_state = replace(state, sequence=sequence)
    return Transition(next_state, (ReconcileObservedState(sequence),))


def _monitor_topology_changed(state: State) -> Transition:
    sequence = state.sequence + 1
    next_state = replace(state, sequence=sequence)
    return Transition(next_state, (ReconcileObservedState(sequence),))
