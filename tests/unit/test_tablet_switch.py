from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from evdev import ecodes

from yoga_deck.adapters.input_access import InputAccessDenied, event_nodes
from yoga_deck.adapters.tablet_switch import (
    HingeAngleReader,
    TabletSwitchError,
    TabletSwitchPermissionError,
    TabletSwitchSource,
    find_tablet_switch,
    summarize_observations,
)
from yoga_deck.core import TabletModeChanged


@dataclass(frozen=True)
class FakeEvent:
    type: int
    code: int
    value: int
    kernel_timestamp: float | None = None

    def timestamp(self) -> float:
        return self.kernel_timestamp or 0.0


class FakeDevice:
    def __init__(self, name, capabilities, events=()) -> None:
        self.name = name
        self._capabilities = capabilities
        self._events = events
        self.closed = False

    def capabilities(self, verbose=False, absinfo=True):
        return self._capabilities

    def read_loop(self) -> Iterator[FakeEvent]:
        yield from self._events

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    def __init__(self, devices) -> None:
        self.devices = devices
        self.opened_paths = []

    def list_devices(self):
        return tuple(self.devices)

    def open_device(self, path):
        self.opened_paths.append(path)
        device = self.devices[path]
        if isinstance(device, Exception):
            raise device
        return device


def test_discovery_uses_stable_name_and_switch_capability_not_event_number() -> None:
    backend = FakeBackend(
        {
            "/dev/input/event71": FakeDevice("External switch", {ecodes.EV_SW: [0]}),
            "/dev/input/event3": FakeDevice(
                "ThinkPad Extra Buttons", {ecodes.EV_SW: [ecodes.SW_TABLET_MODE]}
            ),
        }
    )

    device = find_tablet_switch(backend)

    assert device.name == "ThinkPad Extra Buttons"
    assert backend.opened_paths == ["/dev/input/event71", "/dev/input/event3"]


def test_record_accepts_only_tablet_mode_events_and_preserves_duplicates() -> None:
    events = (
        FakeEvent(ecodes.EV_KEY, 1, 1),
        FakeEvent(ecodes.EV_SW, 0, 1),
        FakeEvent(ecodes.EV_SW, ecodes.SW_TABLET_MODE, 1),
        FakeEvent(ecodes.EV_SW, ecodes.SW_TABLET_MODE, 1),
    )
    device = FakeDevice("ThinkPad Extra Buttons", {ecodes.EV_SW: [ecodes.SW_TABLET_MODE]}, events)
    backend = FakeBackend({"stable-path-a": device})
    times = iter((10.0, 10.025))

    records = TabletSwitchSource(backend=backend, clock=lambda: next(times), max_events=2).record(
        "fold-unfold"
    )

    assert records[0].event == TabletModeChanged(True)
    assert records[1].event == TabletModeChanged(True)
    assert [record.t_ms for record in records] == [0, 25]
    assert device.closed is True


def test_device_disappearance_triggers_rediscovery() -> None:
    class DisappearingDevice(FakeDevice):
        def read_loop(self):
            yield FakeEvent(ecodes.EV_SW, ecodes.SW_TABLET_MODE, 1)
            raise OSError

    first = DisappearingDevice("ThinkPad Extra Buttons", {ecodes.EV_SW: [ecodes.SW_TABLET_MODE]})
    second = FakeDevice(
        "ThinkPad Extra Buttons",
        {ecodes.EV_SW: [ecodes.SW_TABLET_MODE]},
        (FakeEvent(ecodes.EV_SW, ecodes.SW_TABLET_MODE, 0),),
    )

    class ReconnectingBackend(FakeBackend):
        def __init__(self):
            self.attempt = 0
            self.opened_paths = []

        def list_devices(self):
            self.attempt += 1
            return (f"stable-path-{self.attempt}",)

        def open_device(self, path):
            return first if self.attempt == 1 else second

    times = iter((1.0, 1.1))
    records = TabletSwitchSource(
        backend=ReconnectingBackend(), clock=lambda: next(times), max_events=2
    ).record("fold-unfold")

    assert [record.event.enabled for record in records] == [True, False]


def test_missing_switch_uses_stable_error_without_device_details() -> None:
    backend = FakeBackend(
        {"/dev/input/event99": FakeDevice("Private serial 123", {ecodes.EV_KEY: [1]})}
    )

    with pytest.raises(TabletSwitchError) as error:
        find_tablet_switch(backend)

    assert error.value.code == "tablet_switch_unavailable"
    assert "serial" not in str(error.value)


def test_capture_measures_delivery_latency_and_hinge_threshold_distribution() -> None:
    events = (
        FakeEvent(ecodes.EV_SW, ecodes.SW_TABLET_MODE, 1, 100.000),
        FakeEvent(ecodes.EV_SW, ecodes.SW_TABLET_MODE, 0, 101.000),
    )
    device = FakeDevice("ThinkPad Extra Buttons", {ecodes.EV_SW: [ecodes.SW_TABLET_MODE]}, events)
    hinge_values = iter((218.0, 192.0))
    source = TabletSwitchSource(
        backend=FakeBackend({"stable-path": device}),
        clock=iter((1.0, 1.1)).__next__,
        wall_clock=iter((100.005, 101.010)).__next__,
        hinge_reader=lambda: next(hinge_values),
        max_events=2,
    )

    source.record("switch-threshold")
    summary = summarize_observations(source.observations)

    assert summary["delivery_latency_ms"]["count"] == 2
    assert summary["delivery_latency_ms"]["max"] == pytest.approx(10.0)
    assert summary["folded_hinge_degrees"]["median"] == 218.0
    assert summary["laptop_hinge_degrees"]["median"] == 192.0


def test_hinge_reader_discovers_semantic_iio_attributes(tmp_path) -> None:
    device = tmp_path / "bus/iio/devices/iio:device42"
    device.mkdir(parents=True)
    (device / "name").write_text("hinge\n")
    (device / "in_angl0_label").write_text("hinge\n")
    (device / "in_angl0_raw").write_text("210\n")
    (device / "in_angl_scale").write_text("0.017453293\n")
    (device / "in_angl_offset").write_text("0\n")

    assert HingeAngleReader(tmp_path).read_degrees() == pytest.approx(210.0)


def test_permission_denied_switch_is_distinct_from_missing_hardware() -> None:
    backend = FakeBackend(
        {
            "/dev/input/event71": PermissionError(13, "denied", "/dev/input/event71"),
            "/dev/input/event3": FakeDevice("AT Translated Set 2 keyboard", {}),
        }
    )

    with pytest.raises(TabletSwitchPermissionError) as error:
        find_tablet_switch(backend)

    assert isinstance(error.value, TabletSwitchError)
    assert isinstance(error.value, InputAccessDenied)
    assert error.value.code == "tablet_switch_permission_denied"
    assert "event" not in str(error.value)


def test_unrelated_open_errors_still_mean_unavailable() -> None:
    backend = FakeBackend({"/dev/input/event71": OSError(19, "gone")})

    with pytest.raises(TabletSwitchError) as error:
        find_tablet_switch(backend)

    assert not isinstance(error.value, InputAccessDenied)
    assert error.value.code == "tablet_switch_unavailable"


def test_evdev_backend_lists_unreadable_nodes_instead_of_hiding_them(tmp_path) -> None:
    # evdev.list_devices() drops nodes this user cannot open, which made a missing input
    # group indistinguishable from absent hardware.
    (tmp_path / "input").mkdir()
    for name in ("event7", "event12", "mouse0"):
        (tmp_path / "input" / name).write_text("")
        (tmp_path / "input" / name).chmod(0)

    assert [node.rsplit("/", 1)[1] for node in event_nodes(tmp_path)] == ["event12", "event7"]
    assert event_nodes(tmp_path / "absent") == []
