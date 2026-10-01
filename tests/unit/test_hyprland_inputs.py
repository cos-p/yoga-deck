import json

import pytest

from yoga_deck.adapters.hyprland_inputs import (
    HyprlandError,
    HyprlandInputAdapter,
    InputRole,
    InternalInput,
    parse_owned_inputs,
)


def test_discovery_targets_only_known_internal_devices() -> None:
    payload = json.dumps(
        {
            "keyboards": [
                {"name": "at-translated-set-2-keyboard"},
                {"name": "external-usb-keyboard"},
                {"name": "hl-virtual-keyboard-fcitx5"},
            ],
            "mice": [
                {"name": "synps/2-synaptics-touchpad"},
                {"name": "tpps/2-alps-trackpoint"},
                {"name": "external-usb-mouse"},
                {"name": "mouse-passthrough"},
            ],
            "touch": [{"name": "wacom-pen-and-multitouch-sensor-finger"}],
        }
    )

    assert parse_owned_inputs(payload) == (
        InternalInput(InputRole.KEYBOARD, "at-translated-set-2-keyboard"),
        InternalInput(InputRole.TOUCHPAD, "synps/2-synaptics-touchpad"),
        InternalInput(InputRole.TRACKPOINT, "tpps/2-alps-trackpoint"),
    )


def test_similarly_named_external_devices_are_not_owned() -> None:
    payload = json.dumps(
        {
            "keyboards": [{"name": "usb-at-translated-set-2-keyboard"}],
            "mice": [
                {"name": "usb-synaptics-touchpad"},
                {"name": "bluetooth-alps-trackpoint"},
            ],
        }
    )

    assert parse_owned_inputs(payload) == ()


@pytest.mark.parametrize("payload", ("not-json", "[]", '{"keyboards":"bad"}'))
def test_malformed_device_response_uses_stable_error(payload) -> None:
    with pytest.raises(HyprlandError) as error:
        parse_owned_inputs(payload)

    assert error.value.code == "malformed_hyprland_devices"


def test_adapter_renders_lua_device_update_without_shell_interpolation() -> None:
    runner = FakeRunner(
        responses=(
            Result(stdout='{"keyboards":[],"mice":[]}'),
            Result(stdout="ok"),
        )
    )
    adapter = HyprlandInputAdapter(runner=runner)
    device = InternalInput(InputRole.TOUCHPAD, "synps/2-synaptics-touchpad")

    adapter.set_enabled(device, enabled=False)

    assert runner.calls[-1] == (
        "hyprctl",
        "eval",
        'hl.device({ name = "synps/2-synaptics-touchpad", enabled = false })',
    )


def test_adapter_discovers_owned_devices_from_hyprctl_json() -> None:
    runner = FakeRunner(
        (
            Result(
                stdout=json.dumps(
                    {
                        "keyboards": [{"name": "at-translated-set-2-keyboard"}],
                        "mice": [{"name": "external-mouse"}],
                    }
                )
            ),
        )
    )

    assert HyprlandInputAdapter(runner=runner).discover_owned() == (
        InternalInput(InputRole.KEYBOARD, "at-translated-set-2-keyboard"),
    )
    assert runner.calls == [("hyprctl", "-j", "devices")]


def test_mutation_failure_does_not_expose_compositor_output() -> None:
    adapter = HyprlandInputAdapter(runner=FakeRunner((Result(returncode=1),)))
    device = InternalInput(InputRole.KEYBOARD, "at-translated-set-2-keyboard")

    with pytest.raises(HyprlandError) as error:
        adapter.set_enabled(device, enabled=True)

    assert error.value.code == "hyprland_mutation_failed"
    assert "private" not in str(error.value)


def test_adapter_rejects_non_owned_device_even_if_constructed() -> None:
    adapter = HyprlandInputAdapter(runner=FakeRunner(()))
    external = InternalInput(InputRole.TOUCHPAD, "external-touchpad")

    with pytest.raises(HyprlandError) as error:
        adapter.set_enabled(external, enabled=False)

    assert error.value.code == "unsafe_input_target"


class Result:
    def __init__(self, stdout="", returncode=0) -> None:
        self.stdout = stdout
        self.stderr = "private error"
        self.returncode = returncode


class FakeRunner:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.calls = []

    def run(self, args, timeout):
        self.calls.append(tuple(args))
        return next(self.responses)


@pytest.mark.asyncio
async def test_repeated_cancellation_does_not_release_mutation_ownership():
    import asyncio
    import threading

    from yoga_deck.adapters.hyprland_inputs import AsyncHyprlandInputAdapter
    from yoga_deck.coordinator import InputCoordinator
    from yoga_deck.core import SetInternalInputsEnabled

    started, release = threading.Event(), threading.Event()
    device = InternalInput(InputRole.KEYBOARD, "at-translated-set-2-keyboard")

    class Adapter:
        enabled = True

        def discover_owned(self):
            return (device,)

        def set_enabled(self, device, enabled):
            if not enabled:
                started.set()
                release.wait(2)
            self.enabled = enabled

    adapter = Adapter()
    coordinator = InputCoordinator(AsyncHyprlandInputAdapter(adapter))
    task = asyncio.create_task(coordinator.apply(SetInternalInputsEnabled(False, 1)))
    await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    recovery = asyncio.create_task(coordinator.recover_inputs())
    try:
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await recovery
        assert adapter.enabled
    finally:
        release.set()
