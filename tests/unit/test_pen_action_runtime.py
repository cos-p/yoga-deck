import pytest
from evdev import ecodes

from yoga_deck.adapters.input_access import InputAccessDenied
from yoga_deck.adapters.pen_action_runtime import (
    EvdevPenActionSession,
    FarButtonDebouncer,
    PenActionMapping,
    PenActionMappingError,
    PenActionPermissionError,
    PenActionSourceError,
    find_far_button_pen,
    load_pen_action_mapping,
)
from yoga_deck.core import PenAction


def test_missing_user_mapping_keeps_far_button_safe_off(tmp_path) -> None:
    mapping = load_pen_action_mapping(tmp_path / "missing.toml")
    debouncer = FarButtonDebouncer(mapping)

    assert debouncer.observe(1, 10.0) is None
    assert debouncer.observe(0, 11.0) is None


def test_explicit_mapping_emits_one_monotonic_action_per_debounced_cycle() -> None:
    debouncer = FarButtonDebouncer(PenActionMapping(PenAction.CAPTURE_TEXT))

    assert debouncer.observe(1, 10.0) is None
    first = debouncer.observe(0, 11.0)
    assert first is not None
    assert first.action is PenAction.CAPTURE_TEXT
    assert first.source_sequence == 1

    assert debouncer.observe(1, 20.0) is None
    assert debouncer.observe(0, 30.0) is None

    assert debouncer.observe(1, 70.0) is None
    second = debouncer.observe(0, 80.0)
    assert second is not None
    assert second.source_sequence == 2


def test_duplicate_down_or_unmatched_up_cannot_create_an_action() -> None:
    debouncer = FarButtonDebouncer(PenActionMapping(PenAction.OPEN_SCRATCHPAD))

    assert debouncer.observe(0, 1.0) is None
    assert debouncer.observe(1, 2.0) is None
    assert debouncer.observe(1, 3.0) is None
    event = debouncer.observe(0, 4.0)

    assert event is not None
    assert event.source_sequence == 1
    assert debouncer.observe(0, 5.0) is None


def test_user_mapping_accepts_only_supported_semantic_actions(tmp_path) -> None:
    path = tmp_path / "pen-actions.toml"
    path.write_text('far_button_action = "open_scratchpad"\n', encoding="utf-8")

    assert load_pen_action_mapping(path) == PenActionMapping(PenAction.OPEN_SCRATCHPAD)

    path.write_text('far_button_action = "near_tip"\n', encoding="utf-8")
    with pytest.raises(PenActionMappingError, match="pen_action_mapping_invalid"):
        load_pen_action_mapping(path)


class FakeEvent:
    def __init__(self, event_type: int, code: int, value: int) -> None:
        self.type = event_type
        self.code = code
        self.value = value


class FakeDevice:
    def __init__(self, name: str, keys: list[int], events=()) -> None:
        self.name = name
        self._keys = keys
        self._events = events
        self.closed = False

    def capabilities(self, *, verbose=False, absinfo=False):
        return {ecodes.EV_KEY: self._keys}

    async def async_read_loop(self):
        for event in self._events:
            yield event

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    def __init__(self, devices: dict[str, FakeDevice]) -> None:
        self._devices = devices

    def list_devices(self):
        return self._devices

    def open_device(self, path: str):
        return self._devices[path]


def test_discovery_requires_internal_wacom_pen_identity_and_capabilities() -> None:
    external = FakeDevice("external tablet pen", [ecodes.BTN_TOOL_PEN, ecodes.BTN_STYLUS])
    incomplete = FakeDevice("wacom-pen-and-multitouch-sensor-pen", [ecodes.BTN_TOOL_PEN])
    target = FakeDevice(
        "wacom-pen-and-multitouch-sensor-pen", [ecodes.BTN_TOOL_PEN, ecodes.BTN_STYLUS]
    )

    found = find_far_button_pen(
        FakeBackend({"external": external, "incomplete": incomplete, "target": target})
    )

    assert found is target
    assert external.closed and incomplete.closed
    assert target.closed is False


@pytest.mark.asyncio
async def test_session_filters_to_primary_barrel_cycles_and_applies_debounce() -> None:
    device = FakeDevice(
        "wacom-pen-and-multitouch-sensor-pen",
        [ecodes.BTN_TOOL_PEN, ecodes.BTN_STYLUS],
        events=(
            FakeEvent(ecodes.EV_KEY, ecodes.BTN_STYLUS2, 1),
            FakeEvent(ecodes.EV_KEY, ecodes.BTN_STYLUS, 1),
            FakeEvent(ecodes.EV_KEY, ecodes.BTN_STYLUS, 0),
            FakeEvent(ecodes.EV_KEY, ecodes.BTN_STYLUS, 1),
            FakeEvent(ecodes.EV_KEY, ecodes.BTN_STYLUS, 0),
            FakeEvent(ecodes.EV_KEY, ecodes.BTN_STYLUS, 1),
            FakeEvent(ecodes.EV_KEY, ecodes.BTN_STYLUS, 0),
        ),
    )
    ticks = iter((0.010, 0.011, 0.020, 0.030, 0.070, 0.080))
    session = EvdevPenActionSession(
        device,
        PenActionMapping(PenAction.CAPTURE_TEXT),
        clock=lambda: next(ticks),
    )

    events = [event async for event in session.events()]
    await session.close()

    assert [(event.action, event.source_sequence) for event in events] == [
        (PenAction.CAPTURE_TEXT, 1),
        (PenAction.CAPTURE_TEXT, 2),
    ]
    assert device.closed


class DeniedBackend:
    def list_devices(self):
        return ("/dev/input/event12",)

    def open_device(self, path: str):
        raise PermissionError(13, "denied", path)


def test_unreadable_pen_nodes_raise_a_distinct_permission_error() -> None:
    with pytest.raises(PenActionPermissionError) as error:
        find_far_button_pen(DeniedBackend())

    assert isinstance(error.value, PenActionSourceError)
    assert isinstance(error.value, InputAccessDenied)
    assert str(error.value) == "pen_action_permission_denied"


def test_absent_pen_is_still_unavailable_not_denied() -> None:
    with pytest.raises(PenActionSourceError) as error:
        find_far_button_pen(FakeBackend({}))

    assert not isinstance(error.value, InputAccessDenied)
    assert str(error.value) == "pen_action_source_unavailable"
