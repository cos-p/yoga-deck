from yoga_deck.core import (
    ApplyRotation,
    CaptureText,
    Diagnostic,
    InputRecoveryRequested,
    LaunchScratchpad,
    ManualOrientationRequested,
    MonitorTopologyChanged,
    Orientation,
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
    ShutdownRequested,
    State,
    TabletModeChanged,
    ThemeChanged,
    reduce,
)


def test_fold_and_unfold_coordinate_internal_inputs_and_osk() -> None:
    folded = reduce(State(), TabletModeChanged(enabled=True))

    assert folded.state.internal_inputs_enabled is False
    assert folded.state.osk_enabled is True
    assert folded.effects == (
        SetInternalInputsEnabled(enabled=False, sequence=1),
        SetOskEnabled(enabled=True, sequence=1),
    )

    laptop = reduce(folded.state, TabletModeChanged(enabled=False))

    assert laptop.state.internal_inputs_enabled is True
    assert laptop.state.osk_enabled is False
    assert laptop.effects == (
        SetInternalInputsEnabled(enabled=True, sequence=2),
        SetOskEnabled(enabled=False, sequence=2),
    )


def test_orientation_change_is_one_coordinated_rotation_intent() -> None:
    transition = reduce(State(), OrientationChanged(Orientation.LEFT))

    assert transition.state.applied_orientation is Orientation.LEFT
    assert transition.effects == (ApplyRotation(orientation=Orientation.LEFT, sequence=1),)
    assert ApplyRotation.targets == ("display", "touchscreen", "pen")


def test_orientation_lock_blocks_rotation_but_not_posture_inhibition() -> None:
    locked = reduce(State(), OrientationLockChanged(locked=True))
    turned = reduce(locked.state, OrientationChanged(Orientation.RIGHT))
    folded = reduce(turned.state, TabletModeChanged(enabled=True))

    assert turned.state.observed_orientation is Orientation.RIGHT
    assert turned.state.applied_orientation is Orientation.NORMAL
    assert turned.effects == ()
    assert folded.effects == (
        SetInternalInputsEnabled(enabled=False, sequence=3),
        SetOskEnabled(enabled=True, sequence=3),
    )


def test_unlock_reconciles_to_latest_observed_orientation() -> None:
    state = reduce(State(), OrientationLockChanged(locked=True)).state
    state = reduce(state, OrientationChanged(Orientation.INVERTED)).state

    unlocked = reduce(state, OrientationLockChanged(locked=False))

    assert unlocked.state.applied_orientation is Orientation.INVERTED
    assert unlocked.effects == (ApplyRotation(orientation=Orientation.INVERTED, sequence=3),)


def test_manual_orientation_request_applies_while_sensor_rotation_is_locked() -> None:
    state = reduce(State(), OrientationLockChanged(locked=True)).state

    requested = reduce(state, ManualOrientationRequested(Orientation.RIGHT))

    assert requested.state.applied_orientation is Orientation.RIGHT
    assert requested.state.observed_orientation is Orientation.NORMAL
    assert requested.effects == (ApplyRotation(orientation=Orientation.RIGHT, sequence=2),)


def test_unknown_tablet_value_preserves_folded_safe_state_and_diagnoses() -> None:
    folded = reduce(State(), TabletModeChanged(enabled=True)).state

    unknown = reduce(folded, TabletModeChanged(enabled=None))

    assert unknown.state.internal_inputs_enabled is False
    assert unknown.state.osk_enabled is True
    assert unknown.effects == (Diagnostic(code="unknown_tablet_mode", sequence=2),)


def test_unknown_orientation_preserves_applied_rotation_and_diagnoses() -> None:
    turned = reduce(State(), OrientationChanged(Orientation.LEFT)).state

    unknown = reduce(turned, OrientationChanged(None))

    assert unknown.state.applied_orientation is Orientation.LEFT
    assert unknown.effects == (Diagnostic(code="unknown_orientation", sequence=2),)


def test_duplicate_events_are_idempotent() -> None:
    once = reduce(State(), TabletModeChanged(enabled=True))
    twice = reduce(once.state, TabletModeChanged(enabled=True))

    assert twice.state == once.state
    assert twice.effects == ()


def test_shutdown_recovers_internal_inputs_and_stops_osk() -> None:
    folded = reduce(State(), TabletModeChanged(enabled=True)).state

    shutdown = reduce(folded, ShutdownRequested())

    assert shutdown.state.internal_inputs_enabled is True
    assert shutdown.state.osk_enabled is False
    assert shutdown.effects == (
        SetInternalInputsEnabled(enabled=True, sequence=2),
        SetOskEnabled(enabled=False, sequence=2),
    )


def test_emergency_recovery_is_a_reducer_owned_input_effect() -> None:
    folded = reduce(State(), TabletModeChanged(enabled=True)).state

    recovered = reduce(folded, InputRecoveryRequested())

    assert recovered.state.internal_inputs_enabled is True
    assert recovered.effects == (SetInternalInputsEnabled(enabled=True, sequence=2),)


def test_reconcile_reasserts_current_input_and_osk_intent_with_a_new_sequence() -> None:
    state = State(
        sequence=4,
        tablet_mode_observed=True,
        internal_inputs_enabled=False,
        osk_enabled=True,
    )

    transition = reduce(state, ReconcileRequested())

    assert transition.state.sequence == 5
    assert transition.effects == (
        SetInternalInputsEnabled(enabled=False, sequence=5),
        SetOskEnabled(enabled=True, sequence=5),
    )


def test_resume_requests_fresh_observation_through_a_pure_effect() -> None:
    transition = reduce(State(sequence=7), ResumeRequested())

    assert transition.state.sequence == 8
    assert transition.effects == (ReconcileObservedState(8),)


def test_monitor_topology_change_requests_fresh_observation_through_a_pure_effect() -> None:
    transition = reduce(State(sequence=7), MonitorTopologyChanged())

    assert transition.state.sequence == 8
    assert transition.effects == (ReconcileObservedState(8),)


def test_fresh_debounced_pen_action_requests_ocr_once() -> None:
    transition = reduce(
        State(),
        PenActionRequested(action=PenAction.CAPTURE_TEXT, source_sequence=1),
    )

    assert transition.state.last_pen_action_source_sequence == 1
    assert transition.effects == (CaptureText(sequence=1),)


def test_repeated_or_reordered_pen_action_is_idempotent() -> None:
    first = reduce(
        State(),
        PenActionRequested(action=PenAction.CAPTURE_TEXT, source_sequence=2),
    )

    duplicate = reduce(
        first.state,
        PenActionRequested(action=PenAction.CAPTURE_TEXT, source_sequence=2),
    )
    stale = reduce(
        first.state,
        PenActionRequested(action=PenAction.CAPTURE_TEXT, source_sequence=1),
    )

    assert duplicate == type(duplicate)(first.state)
    assert stale == type(stale)(first.state)


def test_fresh_scratchpad_action_plans_a_launch_effect() -> None:
    transition = reduce(
        State(),
        PenActionRequested(action=PenAction.OPEN_SCRATCHPAD, source_sequence=1),
    )

    assert transition.effects == (LaunchScratchpad(sequence=1),)


def test_theme_change_while_folded_plans_a_palette_refresh() -> None:
    folded = reduce(State(), TabletModeChanged(enabled=True)).state

    transition = reduce(folded, ThemeChanged())

    assert transition.effects == (RefreshOskTheme(sequence=folded.sequence + 1),)


def test_theme_change_in_laptop_mode_plans_nothing() -> None:
    transition = reduce(State(), ThemeChanged())

    assert transition.effects == ()
    assert transition.state == State()


def test_theme_change_never_alters_posture_or_input_safety() -> None:
    folded = reduce(State(), TabletModeChanged(enabled=True)).state

    themed = reduce(folded, ThemeChanged()).state

    assert themed.osk_enabled is folded.osk_enabled
    assert themed.internal_inputs_enabled is folded.internal_inputs_enabled
    assert themed.tablet_mode_observed is folded.tablet_mode_observed
    assert themed.applied_orientation is folded.applied_orientation


def test_repeated_theme_changes_each_plan_their_own_refresh() -> None:
    state = reduce(State(), TabletModeChanged(enabled=True)).state

    first = reduce(state, ThemeChanged())
    second = reduce(first.state, ThemeChanged())

    assert first.effects == (RefreshOskTheme(sequence=state.sequence + 1),)
    assert second.effects == (RefreshOskTheme(sequence=state.sequence + 2),)
