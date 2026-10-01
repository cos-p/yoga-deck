import asyncio

import pytest

from yoga_deck.adapters.hyprland_inputs import InputRole, InternalInput
from yoga_deck.coordinator import (
    CoordinatorError,
    InputCoordinator,
    OcrCoordinator,
    OskCoordinator,
    SystemCoordinator,
)
from yoga_deck.core import (
    CaptureText,
    LaunchScratchpad,
    RefreshOskTheme,
    SetInternalInputsEnabled,
    SetOskEnabled,
    ShutdownRequested,
    State,
    TabletModeChanged,
    reduce,
)

DEVICES = (
    InternalInput(InputRole.KEYBOARD, "at-translated-set-2-keyboard"),
    InternalInput(InputRole.TOUCHPAD, "synps/2-synaptics-touchpad"),
    InternalInput(InputRole.TRACKPOINT, "tpps/2-alps-trackpoint"),
)


class FakeInputAdapter:
    def __init__(self, devices=DEVICES, fail_on=(), fail_discovery=False) -> None:
        self.devices = devices
        self.fail_on = set(fail_on)
        self.states = {device.name: True for device in devices}
        self.calls = []
        self.fail_discovery = fail_discovery

    async def discover_owned(self):
        if self.fail_discovery:
            raise OSError("private compositor detail")
        return self.devices

    async def set_enabled(self, device, enabled):
        self.calls.append((device.name, enabled))
        if (device.name, enabled) in self.fail_on:
            raise OSError("private adapter detail")
        self.states[device.name] = enabled


@pytest.mark.asyncio
async def test_fold_disables_only_owned_internal_devices() -> None:
    adapter = FakeInputAdapter()
    coordinator = InputCoordinator(adapter)

    await coordinator.apply(SetInternalInputsEnabled(False, sequence=1))

    assert adapter.states == {device.name: False for device in DEVICES}


@pytest.mark.asyncio
async def test_stale_and_duplicate_sequences_cannot_overwrite_newer_state() -> None:
    adapter = FakeInputAdapter()
    coordinator = InputCoordinator(adapter)

    await coordinator.apply(SetInternalInputsEnabled(False, sequence=2))
    await coordinator.apply(SetInternalInputsEnabled(True, sequence=3))
    await coordinator.apply(SetInternalInputsEnabled(False, sequence=2))
    await coordinator.apply(SetInternalInputsEnabled(True, sequence=3))

    assert all(adapter.states.values())
    assert coordinator.latest_sequence == 3
    assert len(adapter.calls) == 6


@pytest.mark.asyncio
async def test_concurrent_rapid_transitions_finish_at_newest_sequence() -> None:
    adapter = FakeInputAdapter()
    coordinator = InputCoordinator(adapter)

    await asyncio.gather(
        coordinator.apply(SetInternalInputsEnabled(False, sequence=1)),
        coordinator.apply(SetInternalInputsEnabled(True, sequence=2)),
        coordinator.apply(SetInternalInputsEnabled(False, sequence=3)),
        coordinator.apply(SetInternalInputsEnabled(True, sequence=4)),
    )

    assert coordinator.latest_sequence == 4
    assert all(adapter.states.values())


@pytest.mark.asyncio
async def test_partial_disable_failure_rolls_every_device_back_to_enabled() -> None:
    adapter = FakeInputAdapter(fail_on={(DEVICES[1].name, False)})
    coordinator = InputCoordinator(adapter)

    with pytest.raises(CoordinatorError) as error:
        await coordinator.apply(SetInternalInputsEnabled(False, sequence=1))

    assert error.value.code == "input_inhibition_failed"
    assert all(adapter.states.values())
    assert adapter.calls[-3:] == [(device.name, True) for device in DEVICES]


@pytest.mark.asyncio
async def test_recovery_attempts_every_device_despite_partial_failure() -> None:
    adapter = FakeInputAdapter(fail_on={(DEVICES[0].name, True)})
    adapter.states = {device.name: False for device in DEVICES}
    coordinator = InputCoordinator(adapter)

    with pytest.raises(CoordinatorError) as error:
        await coordinator.recover_inputs()

    assert error.value.code == "input_recovery_incomplete"
    assert adapter.states[DEVICES[1].name] is True
    assert adapter.states[DEVICES[2].name] is True


@pytest.mark.asyncio
async def test_reconcile_rediscovers_hotplugged_devices() -> None:
    adapter = FakeInputAdapter(devices=DEVICES[:2])
    coordinator = InputCoordinator(adapter)
    await coordinator.apply(SetInternalInputsEnabled(False, sequence=1))
    adapter.devices = DEVICES

    await coordinator.reconcile()

    assert adapter.states[DEVICES[2].name] is False


@pytest.mark.asyncio
async def test_missing_devices_are_safe_no_op() -> None:
    coordinator = InputCoordinator(FakeInputAdapter(devices=()))

    await coordinator.apply(SetInternalInputsEnabled(False, sequence=1))
    await coordinator.recover_inputs()


@pytest.mark.asyncio
async def test_discovery_failure_during_disable_changes_desired_state_to_recovery() -> None:
    adapter = FakeInputAdapter(fail_discovery=True)
    coordinator = InputCoordinator(adapter)

    with pytest.raises(CoordinatorError) as error:
        await coordinator.apply(SetInternalInputsEnabled(False, sequence=1))
    adapter.fail_discovery = False
    await coordinator.reconcile()

    assert error.value.code == "input_inhibition_failed"
    assert all(adapter.states.values())


@pytest.mark.asyncio
async def test_faulty_adapter_cannot_smuggle_external_target_into_coordinator() -> None:
    external = InternalInput(InputRole.KEYBOARD, "external-usb-keyboard")
    adapter = FakeInputAdapter(devices=(*DEVICES, external))
    adapter.states[external.name] = True
    coordinator = InputCoordinator(adapter)

    await coordinator.apply(SetInternalInputsEnabled(False, sequence=1))

    assert adapter.states[external.name] is True
    assert all(call[0] != external.name for call in adapter.calls)


@pytest.mark.asyncio
async def test_shutdown_reducer_effect_recovers_previously_disabled_inputs() -> None:
    adapter = FakeInputAdapter()
    coordinator = InputCoordinator(adapter)
    folded = reduce(State(), TabletModeChanged(True))
    await coordinator.apply(folded.effects[0])

    shutdown = reduce(folded.state, ShutdownRequested())
    await coordinator.apply(shutdown.effects[0])

    assert all(adapter.states.values())


class FakeOskAdapter:
    def __init__(self, *, fail=False, fail_refresh=False) -> None:
        self.calls = []
        self.refreshes = 0
        self.fail = fail
        self.fail_refresh = fail_refresh

    async def set_enabled(self, enabled: bool) -> None:
        self.calls.append(enabled)
        if self.fail:
            raise OSError("private backend detail")

    async def refresh_theme(self) -> None:
        self.refreshes += 1
        if self.fail_refresh:
            raise OSError("private theme detail")


@pytest.mark.asyncio
async def test_osk_effect_is_serialized_and_stale_effects_cannot_stop_it() -> None:
    adapter = FakeOskAdapter()
    coordinator = OskCoordinator(adapter)

    await coordinator.apply(SetOskEnabled(True, sequence=2))
    await coordinator.apply(SetOskEnabled(False, sequence=1))
    await coordinator.apply(SetOskEnabled(True, sequence=2))

    assert adapter.calls == [True]


@pytest.mark.asyncio
async def test_osk_failure_is_redacted_to_a_stable_code() -> None:
    coordinator = OskCoordinator(FakeOskAdapter(fail=True))

    with pytest.raises(CoordinatorError, match="osk_transition_failed"):
        await coordinator.apply(SetOskEnabled(True, sequence=1))


@pytest.mark.asyncio
async def test_system_coordinator_accepts_paired_effects_with_the_same_sequence() -> None:
    inputs = FakeInputAdapter()
    osk = FakeOskAdapter()
    system = SystemCoordinator(
        InputCoordinator(inputs),
        rotation=type(
            "UnusedRotation", (), {"apply": lambda *_: None, "reconcile": lambda *_: None}
        )(),
        osk=OskCoordinator(osk),
    )

    await system.apply(SetInternalInputsEnabled(False, sequence=1))
    await system.apply(SetOskEnabled(True, sequence=1))

    assert not any(inputs.states.values())
    assert osk.calls == [True]


class FakeOcrAdapter:
    def __init__(self, *, fail=False) -> None:
        self.calls = 0
        self.fail = fail

    async def capture_text(self) -> None:
        self.calls += 1
        if self.fail:
            raise OSError("private OCR output")


@pytest.mark.asyncio
async def test_ocr_effect_is_serialized_and_stale_effects_do_not_repeat_capture() -> None:
    ocr = FakeOcrAdapter()
    system = SystemCoordinator(
        InputCoordinator(FakeInputAdapter()),
        rotation=type(
            "UnusedRotation", (), {"apply": lambda *_: None, "reconcile": lambda *_: None}
        )(),
        ocr=OcrCoordinator(ocr),
    )

    await system.apply(CaptureText(sequence=2))
    await system.apply(CaptureText(sequence=1))
    await system.apply(CaptureText(sequence=2))

    assert ocr.calls == 1


@pytest.mark.asyncio
async def test_ocr_failure_is_redacted_to_a_stable_code() -> None:
    coordinator = OcrCoordinator(FakeOcrAdapter(fail=True))

    with pytest.raises(CoordinatorError, match="ocr_capture_failed"):
        await coordinator.apply(CaptureText(sequence=1))


class FakeScratchpadAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def launch(self) -> None:
        self.calls += 1


@pytest.mark.asyncio
async def test_scratchpad_effect_is_serialized_and_stale_effects_do_not_relaunch() -> None:
    from yoga_deck.coordinator import ScratchpadCoordinator

    scratchpad = FakeScratchpadAdapter()
    coordinator = ScratchpadCoordinator(scratchpad)

    await coordinator.apply(LaunchScratchpad(sequence=2))
    await coordinator.apply(LaunchScratchpad(sequence=1))
    await coordinator.apply(LaunchScratchpad(sequence=2))

    assert scratchpad.calls == 1


@pytest.mark.asyncio
async def test_theme_refresh_reaches_a_running_osk_backend() -> None:
    adapter = FakeOskAdapter()
    coordinator = OskCoordinator(adapter)
    await coordinator.apply(SetOskEnabled(True, sequence=1))

    await coordinator.refresh(RefreshOskTheme(sequence=2))

    assert adapter.refreshes == 1


@pytest.mark.asyncio
async def test_theme_refresh_is_skipped_while_the_osk_is_not_wanted() -> None:
    adapter = FakeOskAdapter()
    coordinator = OskCoordinator(adapter)

    await coordinator.refresh(RefreshOskTheme(sequence=1))

    assert adapter.refreshes == 0


@pytest.mark.asyncio
async def test_a_stale_theme_refresh_cannot_reorder_behind_a_newer_intent() -> None:
    adapter = FakeOskAdapter()
    coordinator = OskCoordinator(adapter)

    await coordinator.apply(SetOskEnabled(True, sequence=3))
    await coordinator.refresh(RefreshOskTheme(sequence=2))

    assert adapter.refreshes == 0


@pytest.mark.asyncio
async def test_theme_refresh_failure_is_redacted_to_a_stable_code() -> None:
    coordinator = OskCoordinator(FakeOskAdapter(fail_refresh=True))
    await coordinator.apply(SetOskEnabled(True, sequence=1))

    with pytest.raises(CoordinatorError, match="osk_theme_refresh_failed"):
        await coordinator.refresh(RefreshOskTheme(sequence=2))


@pytest.mark.asyncio
async def test_system_coordinator_routes_a_theme_refresh_to_the_osk() -> None:
    osk = FakeOskAdapter()
    system = SystemCoordinator(
        InputCoordinator(FakeInputAdapter()),
        rotation=type(
            "UnusedRotation", (), {"apply": lambda *_: None, "reconcile": lambda *_: None}
        )(),
        osk=OskCoordinator(osk),
    )
    await system.apply(SetOskEnabled(True, sequence=1))

    await system.apply(RefreshOskTheme(sequence=2))

    assert osk.refreshes == 1
