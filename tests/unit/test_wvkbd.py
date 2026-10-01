import asyncio
import signal
from dataclasses import dataclass, replace

import pytest
from dbus_next import MessageType, RequestNameReply

from yoga_deck.adapters.omarchy_theme import OskPalette, derive_osk_palette
from yoga_deck.adapters.wvkbd import (
    FCITX_OSK_INTERFACE,
    WVKBD_SIZING_ARGS,
    FcitxVisibilityHost,
    WvkbdAdapter,
    WvkbdError,
    _FcitxVirtualKeyboardInterface,
)
from yoga_deck.core.settings import OskSettings

STUB_PALETTE = derive_osk_palette({"background": "#101010", "foreground": "#e6e6e6"})


class FakePaletteSource:
    """Keep unit tests off the real `omarchy theme color` subprocess."""

    def __init__(self, *, palette: OskPalette | None = None, error: Exception | None = None):
        self.palette = palette or STUB_PALETTE
        self.error = error
        self.calls = 0

    async def resolve(self) -> OskPalette:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.palette


@dataclass
class FakeProcess:
    pid: int = 1234
    returncode: int | None = None
    terminated: int = 0
    killed: int = 0
    waits: int = 0

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1

    def send_signal(self, value: int) -> None:
        self.signals.append(value)

    async def wait(self) -> int:
        self.waits += 1
        self.returncode = 0
        return 0

    def __post_init__(self) -> None:
        self.signals: list[int] = []


class FakeSpawner:
    def __init__(
        self, *, process: FakeProcess | None = None, error: Exception | None = None
    ) -> None:
        self.process = process or FakeProcess()
        self.error = error
        self.calls = []

    async def spawn(self, args, environment, *, start_new_session):
        self.calls.append((args, environment, start_new_session))
        if self.error is not None:
            raise self.error
        return self.process


class FakeVisibilityHost:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.starts = 0
        self.stops = 0
        self.show = None
        self.hide = None

    async def start(self, show, hide) -> None:
        self.starts += 1
        if self.error is not None:
            raise self.error
        self.show = show
        self.hide = hide

    async def stop(self) -> None:
        self.stops += 1


class FakeReadiness:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[FakeProcess] = []

    async def wait(self, process) -> None:
        self.calls.append(process)
        if self.error is not None:
            raise self.error


class FakeReply:
    def __init__(self, body=None) -> None:
        self.message_type = MessageType.METHOD_RETURN
        self.body = body or []


class FakeBus:
    def __init__(self, *, debug_info: str = "", current_ui: str = "virtualkeyboard") -> None:
        self.debug_info = debug_info
        self.current_ui = current_ui
        self.exported = []
        self.messages = []

    def export(self, path, interface) -> None:
        self.exported.append((path, interface))

    async def request_name(self, name):
        return RequestNameReply.PRIMARY_OWNER

    async def call(self, message):
        self.messages.append(message)
        if message.member == "DebugInfo":
            return FakeReply([self.debug_info])
        if message.member == "CurrentUI":
            return FakeReply([self.current_ui])
        return FakeReply()


@pytest.mark.asyncio
async def test_fcitx_host_does_not_force_show_without_a_focused_context() -> None:
    bus = FakeBus()

    async def connect():
        return bus

    host = FcitxVisibilityHost(bus_factory=connect)
    await host.start(lambda: None, lambda: None)

    assert [message.member for message in bus.messages] == ["DebugInfo"]


@pytest.mark.asyncio
async def test_fcitx_host_replays_show_when_a_context_was_already_focused() -> None:
    bus = FakeBus(debug_info="ignored private fields focus:1 ignored")

    async def connect():
        return bus

    host = FcitxVisibilityHost(bus_factory=connect)
    await host.start(lambda: None, lambda: None)

    assert [message.member for message in bus.messages] == [
        "DebugInfo",
        "ShowVirtualKeyboard",
    ]


@pytest.mark.asyncio
async def test_fcitx_host_restores_osk_mode_when_its_key_switches_to_classic_ui() -> None:
    bus = FakeBus(debug_info="focus:1", current_ui="classicui")
    hides = []

    async def connect():
        return bus

    host = FcitxVisibilityHost(bus_factory=connect, hide_focus_delay=0)
    await host.start(lambda: None, lambda: hides.append(True))
    interface = bus.exported[0][1]
    bus.messages.clear()

    interface.HideVirtualKeyboard()
    await asyncio.sleep(0.01)

    assert hides == []
    assert [message.member for message in bus.messages] == [
        "DebugInfo",
        "CurrentUI",
        "ShowVirtualKeyboard",
    ]


@pytest.mark.asyncio
async def test_fcitx_host_honors_explicit_hide_while_context_remains_focused() -> None:
    bus = FakeBus(debug_info="focus:1", current_ui="virtualkeyboard")
    hides = []

    async def connect():
        return bus

    host = FcitxVisibilityHost(bus_factory=connect, hide_focus_delay=0)
    await host.start(lambda: None, lambda: hides.append(True))
    interface = bus.exported[0][1]

    interface.HideVirtualKeyboard()
    await asyncio.sleep(0.01)

    assert hides == [True]


@pytest.mark.asyncio
async def test_fcitx_host_hides_after_real_input_context_focus_loss() -> None:
    bus = FakeBus()
    hides = []

    async def connect():
        return bus

    host = FcitxVisibilityHost(bus_factory=connect, hide_focus_delay=0)
    await host.start(lambda: None, lambda: hides.append(True))
    interface = bus.exported[0][1]

    interface.HideVirtualKeyboard()
    await asyncio.sleep(0.01)

    assert hides == [True]


@pytest.mark.asyncio
async def test_fcitx_show_cancels_a_pending_focus_loss_hide() -> None:
    bus = FakeBus()
    shows = []
    hides = []

    async def connect():
        return bus

    host = FcitxVisibilityHost(bus_factory=connect, hide_focus_delay=0.05)
    await host.start(lambda: shows.append(True), lambda: hides.append(True))
    interface = bus.exported[0][1]

    interface.HideVirtualKeyboard()
    interface.ShowVirtualKeyboard()
    await asyncio.sleep(0.06)

    assert shows == [True]
    assert hides == []


def test_fcitx_ui_exports_the_expected_visibility_and_update_contract() -> None:
    interface = _FcitxVirtualKeyboardInterface.create(lambda: None, lambda: None)

    methods = {item.name: item.in_signature for item in interface.introspect().methods}

    assert interface.name == FCITX_OSK_INTERFACE
    assert methods == {
        "HideVirtualKeyboard": "",
        "NotifyIMActivated": "s",
        "NotifyIMDeactivated": "s",
        "NotifyIMListChanged": "",
        "ShowVirtualKeyboard": "",
        "UpdateCandidateArea": "asbbii",
        "UpdatePreeditArea": "s",
        "UpdatePreeditCaret": "i",
    }


@pytest.mark.asyncio
async def test_enable_starts_hidden_touch_sized_wvkbd_then_registers_focus_visibility(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    spawner = FakeSpawner()
    host = FakeVisibilityHost()
    readiness = FakeReadiness()
    adapter = WvkbdAdapter(spawner, host, readiness=readiness, palette_source=FakePaletteSource())

    await adapter.set_enabled(True)
    await adapter.set_enabled(True)

    assert len(spawner.calls) == 1
    args, environment, start_new_session = spawner.calls[0]
    assert args == ("wvkbd-mobintl", "--hidden", *WVKBD_SIZING_ARGS, *STUB_PALETTE.to_args())
    assert environment["WAYLAND_DISPLAY"] == "wayland-test"
    assert start_new_session is True
    assert readiness.calls == [spawner.process]
    assert host.starts == 1


@pytest.mark.asyncio
async def test_focus_host_is_not_exposed_until_wvkbd_signal_handlers_are_ready() -> None:
    events: list[str] = []

    class OrderedReadiness(FakeReadiness):
        async def wait(self, process) -> None:
            events.append("ready")

    class OrderedHost(FakeVisibilityHost):
        async def start(self, show, hide) -> None:
            events.append("host-start")
            await super().start(show, hide)

    adapter = WvkbdAdapter(
        FakeSpawner(),
        OrderedHost(),
        readiness=OrderedReadiness(),
        palette_source=FakePaletteSource(),
    )

    await adapter.set_enabled(True)

    assert events == ["ready", "host-start"]


@pytest.mark.asyncio
async def test_fcitx_visibility_calls_signal_only_the_owned_process(monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    process = FakeProcess()
    host = FakeVisibilityHost()
    adapter = WvkbdAdapter(
        FakeSpawner(process=process),
        host,
        readiness=FakeReadiness(),
        palette_source=FakePaletteSource(),
    )
    await adapter.set_enabled(True)

    host.show()
    host.hide()

    assert process.signals == [signal.SIGUSR2, signal.SIGUSR1]


@pytest.mark.asyncio
async def test_disable_unregisters_visibility_before_killing_owned_child() -> None:
    events: list[str] = []
    process = FakeProcess()
    host = FakeVisibilityHost()

    async def stop() -> None:
        events.append("host-stop")
        host.stops += 1

    def kill() -> None:
        events.append("process-stop")
        process.killed += 1

    host.stop = stop
    process.kill = kill
    adapter = WvkbdAdapter(
        FakeSpawner(process=process),
        host,
        readiness=FakeReadiness(),
        palette_source=FakePaletteSource(),
    )

    await adapter.set_enabled(True)
    await adapter.set_enabled(False)
    await adapter.set_enabled(False)

    assert events == ["host-stop", "process-stop"]
    assert process.terminated == 0
    assert process.killed == 1
    assert process.waits == 1


@pytest.mark.asyncio
async def test_readiness_failure_kills_child_before_reporting_redacted_error() -> None:
    process = FakeProcess()
    adapter = WvkbdAdapter(
        FakeSpawner(process=process),
        FakeVisibilityHost(),
        readiness=FakeReadiness(error=TimeoutError("private detail")),
    )

    with pytest.raises(WvkbdError, match="osk_backend_not_ready"):
        await adapter.set_enabled(True)

    assert process.terminated == 0
    assert process.killed == 1
    assert process.waits == 1


@pytest.mark.asyncio
async def test_visibility_registration_failure_rolls_back_child() -> None:
    process = FakeProcess()
    host = FakeVisibilityHost(error=RuntimeError("private detail"))
    adapter = WvkbdAdapter(
        FakeSpawner(process=process),
        host,
        readiness=FakeReadiness(),
        palette_source=FakePaletteSource(),
    )

    with pytest.raises(WvkbdError, match="osk_focus_integration_failed"):
        await adapter.set_enabled(True)

    assert process.terminated == 0
    assert process.killed == 1
    assert process.waits == 1


@pytest.mark.asyncio
async def test_missing_backend_is_redacted_to_stable_error(monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    adapter = WvkbdAdapter(
        FakeSpawner(error=FileNotFoundError("private path")),
        FakeVisibilityHost(),
        palette_source=FakePaletteSource(),
    )

    with pytest.raises(WvkbdError, match="osk_backend_unavailable"):
        await adapter.set_enabled(True)


@pytest.mark.asyncio
async def test_theming_failure_never_prevents_the_keyboard_from_starting() -> None:
    spawner = FakeSpawner()
    palette = FakePaletteSource(error=RuntimeError("omarchy theme color exploded"))
    adapter = WvkbdAdapter(
        spawner,
        FakeVisibilityHost(),
        readiness=FakeReadiness(),
        palette_source=palette,
    )

    await adapter.set_enabled(True)

    assert palette.calls == 1
    assert spawner.calls[0][0] == ("wvkbd-mobintl", "--hidden", *WVKBD_SIZING_ARGS)


@pytest.mark.asyncio
async def test_each_spawn_re_reads_the_theme_so_a_fold_picks_up_the_current_palette() -> None:
    palette = FakePaletteSource()
    adapter = WvkbdAdapter(
        FakeSpawner(),
        FakeVisibilityHost(),
        readiness=FakeReadiness(),
        palette_source=palette,
    )

    await adapter.set_enabled(True)
    await adapter.set_enabled(False)
    await adapter.set_enabled(True)

    assert palette.calls == 2


LIGHT_PALETTE = derive_osk_palette({"background": "#ffffff", "foreground": "#101010"})


class SequenceSpawner(FakeSpawner):
    """Hand out a distinct process per spawn so a respawn is observable."""

    def __init__(self) -> None:
        super().__init__()
        self.processes: list[FakeProcess] = []

    async def spawn(self, args, environment, *, start_new_session):
        self.calls.append((args, environment, start_new_session))
        process = FakeProcess(pid=1000 + len(self.processes))
        self.processes.append(process)
        return process


class SwitchablePaletteSource(FakePaletteSource):
    """Stand in for `omarchy theme color` returning a new theme mid-session."""

    def switch(self, palette: OskPalette) -> None:
        self.palette = palette


async def _retintable_adapter() -> tuple[
    WvkbdAdapter, SequenceSpawner, FakeVisibilityHost, FakeReadiness, SwitchablePaletteSource
]:
    spawner = SequenceSpawner()
    host = FakeVisibilityHost()
    readiness = FakeReadiness()
    palette = SwitchablePaletteSource()
    adapter = WvkbdAdapter(
        spawner, host, readiness=readiness, palette_source=palette, retint_settle=0
    )
    await adapter.set_enabled(True)
    return adapter, spawner, host, readiness, palette


@pytest.mark.asyncio
async def test_theme_change_while_hidden_respawns_the_child_in_the_new_palette() -> None:
    adapter, spawner, host, readiness, palette = await _retintable_adapter()
    palette.switch(LIGHT_PALETTE)

    await adapter.refresh_theme()

    assert len(spawner.calls) == 2
    assert spawner.calls[1][0] == (
        "wvkbd-mobintl",
        "--hidden",
        *WVKBD_SIZING_ARGS,
        *LIGHT_PALETTE.to_args(),
    )
    assert spawner.processes[0].killed == 1
    assert readiness.calls == spawner.processes
    # The Fcitx bus name and exported interface must outlive the child; a live campaign showed
    # that is what preserves the client's input context across the respawn.
    assert host.starts == 1
    assert host.stops == 0


@pytest.mark.asyncio
async def test_theme_change_while_visible_defers_until_the_next_hide() -> None:
    adapter, spawner, host, _, palette = await _retintable_adapter()
    host.show()
    palette.switch(LIGHT_PALETTE)

    await adapter.refresh_theme()

    # Respawning under a finger destroys the touch surface mid-press and strands the key.
    assert len(spawner.calls) == 1
    assert spawner.processes[0].killed == 0

    host.hide()
    await adapter._theme_refresh_task

    assert len(spawner.calls) == 2
    assert spawner.calls[1][0][-len(LIGHT_PALETTE.to_args()) :] == LIGHT_PALETTE.to_args()
    assert spawner.processes[0].signals == [signal.SIGUSR2, signal.SIGUSR1]
    assert host.stops == 0


@pytest.mark.asyncio
async def test_a_deferred_theme_change_is_discarded_when_the_osk_is_disabled() -> None:
    adapter, spawner, host, _, palette = await _retintable_adapter()
    host.show()
    palette.switch(LIGHT_PALETTE)
    await adapter.refresh_theme()

    await adapter.set_enabled(False)

    assert adapter._theme_refresh_task is None
    assert len(spawner.calls) == 1
    assert spawner.processes[0].killed == 1


@pytest.mark.asyncio
async def test_theme_change_without_a_running_backend_is_a_no_op() -> None:
    spawner = SequenceSpawner()
    palette = SwitchablePaletteSource()
    adapter = WvkbdAdapter(
        spawner,
        FakeVisibilityHost(),
        readiness=FakeReadiness(),
        palette_source=palette,
    )

    await adapter.refresh_theme()

    assert spawner.calls == []
    assert palette.calls == 0


@pytest.mark.asyncio
async def test_a_second_theme_change_before_hide_respawns_once_at_the_latest_palette() -> None:
    adapter, spawner, host, _, palette = await _retintable_adapter()
    host.show()

    await adapter.refresh_theme()
    palette.switch(LIGHT_PALETTE)
    await adapter.refresh_theme()

    host.hide()
    await adapter._theme_refresh_task

    assert len(spawner.calls) == 2
    assert spawner.calls[1][0][-len(LIGHT_PALETTE.to_args()) :] == LIGHT_PALETTE.to_args()


@pytest.mark.asyncio
async def test_a_failed_retint_leaves_the_adapter_recoverable_by_reconciliation() -> None:
    spawner = SequenceSpawner()
    host = FakeVisibilityHost()
    adapter = WvkbdAdapter(
        spawner,
        host,
        readiness=FakeReadiness(),
        palette_source=SwitchablePaletteSource(),
    )
    await adapter.set_enabled(True)

    async def failing_spawn(args, environment, *, start_new_session):
        raise FileNotFoundError("wvkbd-mobintl")

    spawner.spawn = failing_spawn
    with pytest.raises(WvkbdError, match="osk_backend_unavailable"):
        await adapter.refresh_theme()

    spawner.spawn = SequenceSpawner.spawn.__get__(spawner)
    await adapter.set_enabled(True)

    assert len(spawner.processes) == 2
    assert host.starts == 2


@pytest.mark.asyncio
async def test_a_deferred_retint_lets_the_hide_land_before_killing_the_child() -> None:
    """wvkbd handles SIGUSR1 asynchronously, so a same-breath kill can strand a held key."""

    spawner = SequenceSpawner()
    host = FakeVisibilityHost()
    adapter = WvkbdAdapter(
        spawner,
        host,
        readiness=FakeReadiness(),
        palette_source=SwitchablePaletteSource(),
        retint_settle=0.05,
    )
    await adapter.set_enabled(True)
    host.show()
    await adapter.refresh_theme()

    host.hide()
    await asyncio.sleep(0)
    assert spawner.processes[0].killed == 0, "child was killed before the hide could settle"

    await adapter._theme_refresh_task
    assert spawner.processes[0].killed == 1
    assert len(spawner.calls) == 2


@pytest.mark.asyncio
async def test_a_retint_that_settles_into_a_reshown_keyboard_defers_again() -> None:
    """Refocusing during the settle must not tear the surface back out."""

    spawner = SequenceSpawner()
    host = FakeVisibilityHost()
    adapter = WvkbdAdapter(
        spawner,
        host,
        readiness=FakeReadiness(),
        palette_source=SwitchablePaletteSource(),
        retint_settle=0.05,
    )
    await adapter.set_enabled(True)
    host.show()
    await adapter.refresh_theme()

    host.hide()
    host.show()
    await adapter._theme_refresh_task

    assert spawner.processes[0].killed == 0
    assert len(spawner.calls) == 1


@pytest.mark.asyncio
async def test_user_settings_reach_the_spawn_arguments() -> None:
    settings = OskSettings(landscape_height=320, font="Sans 24", key_popup=False)
    adapter = WvkbdAdapter(
        spawner := FakeSpawner(),
        FakeVisibilityHost(),
        readiness=FakeReadiness(),
        palette_source=FakePaletteSource(),
        settings_provider=lambda: settings,
    )

    await adapter.set_enabled(True)

    assert spawner.calls[0][0] == (
        "wvkbd-mobintl",
        "--hidden",
        "-L",
        "320",
        "-H",
        "360",
        "--fn",
        "Sans 24",
        "--no-popup",
        *STUB_PALETTE.to_args(),
    )


@pytest.mark.asyncio
async def test_each_spawn_re_reads_the_settings_so_an_edit_lands_at_the_next_fold() -> None:
    """Read per spawn, not once at startup; that is what removes the service restart."""

    heights = iter((280, 320))
    adapter = WvkbdAdapter(
        spawner := FakeSpawner(),
        FakeVisibilityHost(),
        readiness=FakeReadiness(),
        palette_source=FakePaletteSource(),
        settings_provider=lambda: OskSettings(landscape_height=next(heights)),
    )

    await adapter.set_enabled(True)
    await adapter.set_enabled(False)
    await adapter.set_enabled(True)

    assert [call[0][3] for call in spawner.calls] == ["280", "320"]


@pytest.mark.asyncio
async def test_a_failing_settings_provider_never_prevents_the_keyboard_from_starting() -> None:
    """Same stance as theming: a settings problem may cost customisation, never a keyboard."""

    def explode() -> OskSettings:
        raise RuntimeError("settings provider exploded")

    adapter = WvkbdAdapter(
        spawner := FakeSpawner(),
        FakeVisibilityHost(),
        readiness=FakeReadiness(),
        palette_source=FakePaletteSource(),
        settings_provider=explode,
    )

    await adapter.set_enabled(True)

    assert spawner.calls[0][0] == (
        "wvkbd-mobintl",
        "--hidden",
        *WVKBD_SIZING_ARGS,
        *STUB_PALETTE.to_args(),
    )


@pytest.mark.asyncio
async def test_process_unsafe_settings_fall_back_before_the_spawn_boundary() -> None:
    """A constructed provider can bypass the file parser, but never poison subprocess argv."""

    adapter = WvkbdAdapter(
        spawner := FakeSpawner(),
        FakeVisibilityHost(),
        readiness=FakeReadiness(),
        palette_source=FakePaletteSource(),
        settings_provider=lambda: OskSettings(font="Sans\x00 24"),
    )

    await adapter.set_enabled(True)

    assert spawner.calls[0][0] == (
        "wvkbd-mobintl",
        "--hidden",
        *WVKBD_SIZING_ARGS,
        *STUB_PALETTE.to_args(),
    )


@pytest.mark.asyncio
async def test_opacity_reaches_the_keyboard_through_the_palette_it_modifies() -> None:
    """The one setting wvkbd has no flag for. It must still arrive, in the colour arguments."""

    adapter = WvkbdAdapter(
        spawner := FakeSpawner(),
        FakeVisibilityHost(),
        readiness=FakeReadiness(),
        palette_source=FakePaletteSource(),
        settings_provider=lambda: OskSettings(opacity=60),
    )

    await adapter.set_enabled(True)

    assert spawner.calls[0][0][-len(STUB_PALETTE.to_args(60)) :] == STUB_PALETTE.to_args(60)
    assert STUB_PALETTE.to_args(60) != STUB_PALETTE.to_args()


@pytest.mark.asyncio
async def test_a_settings_provider_returning_nonsense_opacity_still_spawns_a_keyboard() -> None:
    """A custom provider bypasses the validated file adapter, so the type is checked here."""

    adapter = WvkbdAdapter(
        spawner := FakeSpawner(),
        FakeVisibilityHost(),
        readiness=FakeReadiness(),
        palette_source=FakePaletteSource(),
        settings_provider=lambda: replace(OskSettings(), opacity="most of it"),
    )

    await adapter.set_enabled(True)

    assert spawner.calls[0][0][-len(STUB_PALETTE.to_args()) :] == STUB_PALETTE.to_args()


@pytest.mark.asyncio
async def test_one_spawn_reads_the_settings_once() -> None:
    """Sizing and opacity come from the same read, so a mid-spawn edit cannot split them."""

    reads = 0

    def provider() -> OskSettings:
        nonlocal reads
        reads += 1
        return OskSettings()

    adapter = WvkbdAdapter(
        FakeSpawner(),
        FakeVisibilityHost(),
        readiness=FakeReadiness(),
        palette_source=FakePaletteSource(),
        settings_provider=provider,
    )

    await adapter.set_enabled(True)

    assert reads == 1
