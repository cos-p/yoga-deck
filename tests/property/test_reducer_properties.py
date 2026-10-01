from hypothesis import given
from hypothesis import strategies as st

from yoga_deck.core import (
    ApplyRotation,
    Orientation,
    OrientationChanged,
    OrientationLockChanged,
    RefreshOskTheme,
    SetInternalInputsEnabled,
    SetOskEnabled,
    ShutdownRequested,
    State,
    TabletModeChanged,
    ThemeChanged,
    reduce,
)

events = st.one_of(
    st.builds(TabletModeChanged, st.one_of(st.none(), st.booleans())),
    st.builds(OrientationChanged, st.one_of(st.none(), st.sampled_from(Orientation))),
    st.builds(OrientationLockChanged, st.booleans()),
)

# Kept out of `events`: a theme change is a notification, not a re-reading of a sensor, so
# two in a row legitimately plan two refreshes and are not expected to be idempotent.
themed_events = st.one_of(events, st.builds(ThemeChanged))


@given(st.lists(events, max_size=100))
def test_arbitrary_sensor_sequences_preserve_safety(events) -> None:
    state = State()
    previous_sequence = state.sequence

    for event in events:
        transition = reduce(state, event)
        state = transition.state

        assert state.sequence >= previous_sequence
        assert isinstance(state.applied_orientation, Orientation)
        assert all(effect.sequence == state.sequence for effect in transition.effects)
        previous_sequence = state.sequence


@given(st.lists(events, max_size=100))
def test_laptop_and_shutdown_converge_to_enabled_internal_inputs(events) -> None:
    state = State()
    for event in events:
        state = reduce(state, event).state

    laptop = reduce(state, TabletModeChanged(enabled=False))
    recovered = reduce(laptop.state, ShutdownRequested())

    assert laptop.state.internal_inputs_enabled is True
    assert laptop.state.osk_enabled is False
    assert recovered.state.internal_inputs_enabled is True
    assert recovered.state.osk_enabled is False
    assert not any(
        isinstance(effect, SetInternalInputsEnabled) and not effect.enabled
        for effect in recovered.effects
    )
    assert not any(
        isinstance(effect, SetOskEnabled) and effect.enabled for effect in recovered.effects
    )


@given(st.lists(events, max_size=100), st.sampled_from(Orientation))
def test_lock_never_emits_rotation(events, orientation) -> None:
    state = State()
    for event in events:
        state = reduce(state, event).state

    state = reduce(state, OrientationLockChanged(locked=True)).state
    transition = reduce(state, OrientationChanged(orientation))

    assert not any(isinstance(effect, ApplyRotation) for effect in transition.effects)


@given(st.lists(events, min_size=1, max_size=100))
def test_immediate_duplicate_is_idempotent(events) -> None:
    state = State()
    for event in events[:-1]:
        state = reduce(state, event).state

    once = reduce(state, events[-1])
    twice = reduce(once.state, events[-1])

    assert twice.state == once.state
    assert twice.effects == ()


@given(st.lists(themed_events, max_size=100))
def test_theme_changes_never_plan_a_refresh_for_a_stopped_keyboard(events) -> None:
    state = State()

    for event in events:
        transition = reduce(state, event)
        for effect in transition.effects:
            if isinstance(effect, RefreshOskTheme):
                assert state.osk_enabled is True
        state = transition.state


@given(st.lists(themed_events, max_size=100))
def test_theme_changes_never_disturb_posture_or_input_safety(events) -> None:
    state = State()

    for event in events:
        before = state
        state = reduce(state, event).state
        if isinstance(event, ThemeChanged):
            assert state.osk_enabled is before.osk_enabled
            assert state.internal_inputs_enabled is before.internal_inputs_enabled
            assert state.tablet_mode_observed is before.tablet_mode_observed
            assert state.applied_orientation is before.applied_orientation

    laptop = reduce(state, TabletModeChanged(enabled=False))
    assert laptop.state.internal_inputs_enabled is True
    assert laptop.state.osk_enabled is False
