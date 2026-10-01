"""Tablet-scoped wvkbd lifecycle with Fcitx-driven visibility."""

# D-Bus signatures intentionally use string annotations understood by dbus-next.
# ruff: noqa: F722, F821

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from yoga_deck.adapters.omarchy_theme import OmarchyOskPaletteSource, OskPaletteSource
from yoga_deck.core.settings import OskSettings

FCITX_OSK_BUS_NAME = "org.fcitx.Fcitx5.VirtualKeyboard"
FCITX_OSK_INTERFACE = "org.fcitx.Fcitx5.VirtualKeyboard1"
FCITX_OSK_PATH = "/org/fcitx/virtualkeyboard/impanel"
FCITX_BUS_NAME = "org.fcitx.Fcitx5"
FCITX_CONTROLLER_INTERFACE = "org.fcitx.Fcitx.Controller1"
FCITX_CONTROLLER_PATH = "/controller"
FCITX_VIRTUAL_KEYBOARD_INTERFACE = "org.fcitx.Fcitx.VirtualKeyboard1"
FCITX_VIRTUAL_KEYBOARD_PATH = "/virtualkeyboard"

#: Sizing profile measured on the X390 Yoga; see docs/on-screen-keyboard.md. Rendered from
#: the settings defaults rather than repeated here, so the shipped profile and the value a
#: user gets with no config file cannot drift apart.
WVKBD_SIZING_ARGS = OskSettings().to_args()


class OskProcess(Protocol):
    pid: int
    returncode: int | None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def send_signal(self, value: int) -> None: ...

    async def wait(self) -> int: ...


class ProcessSpawner(Protocol):
    async def spawn(
        self,
        args: tuple[str, ...],
        environment: dict[str, str],
        *,
        start_new_session: bool,
    ) -> OskProcess: ...


class VisibilityHost(Protocol):
    async def start(self, show: Callable[[], None], hide: Callable[[], None]) -> None: ...

    async def stop(self) -> None: ...


class StartupReadiness(Protocol):
    async def wait(self, process: OskProcess) -> None: ...


class WvkbdError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AsyncioProcessSpawner:
    async def spawn(
        self,
        args: tuple[str, ...],
        environment: dict[str, str],
        *,
        start_new_session: bool,
    ) -> OskProcess:
        return await asyncio.create_subprocess_exec(
            *args,
            env=environment,
            start_new_session=start_new_session,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )


class ProcSignalReadiness:
    """Wait until wvkbd has installed its signalfd mask."""

    def __init__(self, *, timeout: float = 2.0, poll_interval: float = 0.01) -> None:
        self._timeout = timeout
        self._poll_interval = poll_interval

    async def wait(self, process: OskProcess) -> None:
        required = (1 << (signal.SIGUSR1 - 1)) | (1 << (signal.SIGUSR2 - 1))
        status_path = Path("/proc") / str(process.pid) / "status"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout

        while process.returncode is None and loop.time() < deadline:
            try:
                status = status_path.read_text()
            except OSError:
                status = ""
            for line in status.splitlines():
                if line.startswith("SigBlk:"):
                    blocked = int(line.split(":", 1)[1].strip(), 16)
                    if blocked & required == required:
                        return
                    break
            await asyncio.sleep(self._poll_interval)

        raise TimeoutError("wvkbd signal handlers did not become ready")


class _FcitxVirtualKeyboardInterface:
    """Create the dbus-next service lazily so diagnostics can import without D-Bus."""

    @staticmethod
    def create(show: Callable[[], None], hide: Callable[[], None]) -> Any:
        from dbus_next.service import ServiceInterface, method

        class Interface(ServiceInterface):
            def __init__(self) -> None:
                super().__init__(FCITX_OSK_INTERFACE)

            @method()
            def ShowVirtualKeyboard(self) -> None:
                show()

            @method()
            def HideVirtualKeyboard(self) -> None:
                hide()

            @method()
            def UpdatePreeditArea(self, text: "s") -> None:
                pass

            @method()
            def UpdatePreeditCaret(self, index: "i") -> None:
                pass

            @method()
            def UpdateCandidateArea(
                self,
                candidates: "as",
                has_previous: "b",
                has_next: "b",
                page_index: "i",
                global_cursor_index: "i",
            ) -> None:
                pass

            @method()
            def NotifyIMActivated(self, unique_name: "s") -> None:
                pass

            @method()
            def NotifyIMDeactivated(self, unique_name: "s") -> None:
                pass

            @method()
            def NotifyIMListChanged(self) -> None:
                pass

        return Interface()


class FcitxVisibilityHost:
    """Expose the Fcitx virtual-keyboard UI contract on the session bus."""

    def __init__(
        self,
        bus_factory: Callable[[], Awaitable[Any]] | None = None,
        *,
        hide_focus_delay: float = 0.05,
    ) -> None:
        self._bus_factory = bus_factory or self._connect_session_bus
        self._hide_focus_delay = hide_focus_delay
        self._bus: Any | None = None
        self._interface: Any | None = None
        self._pending_hide: asyncio.Task[None] | None = None

    @staticmethod
    async def _connect_session_bus() -> Any:
        from dbus_next import BusType
        from dbus_next.aio import MessageBus

        return await MessageBus(bus_type=BusType.SESSION).connect()

    async def start(self, show: Callable[[], None], hide: Callable[[], None]) -> None:
        if self._bus is not None:
            return
        try:
            from dbus_next import RequestNameReply

            bus = await self._bus_factory()
            focused = await self._has_focused_input_context(bus)

            def request_show() -> None:
                self._cancel_pending_hide()
                show()

            def request_hide() -> None:
                self._cancel_pending_hide()
                self._pending_hide = asyncio.create_task(self._hide_if_unfocused(bus, hide))

            interface = _FcitxVirtualKeyboardInterface.create(request_show, request_hide)
            bus.export(FCITX_OSK_PATH, interface)
            reply = await bus.request_name(FCITX_OSK_BUS_NAME)
            if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
                raise RuntimeError("fcitx virtual keyboard name is already owned")
            if focused:
                await self._replay_show(bus)
        except Exception:
            if "bus" in locals():
                bus.disconnect()
            raise
        self._bus = bus
        self._interface = interface

    @staticmethod
    async def _has_focused_input_context(bus: Any) -> bool:
        """Read only Fcitx's focus bit; never retain or emit its debug details."""

        from dbus_next import Message, MessageType

        try:
            reply = await bus.call(
                Message(
                    destination=FCITX_BUS_NAME,
                    path=FCITX_CONTROLLER_PATH,
                    interface=FCITX_CONTROLLER_INTERFACE,
                    member="DebugInfo",
                )
            )
        except Exception:
            return False
        if reply.message_type is not MessageType.METHOD_RETURN or not reply.body:
            return False
        debug_info = reply.body[0]
        if not isinstance(debug_info, str):
            return False
        return any("focus:1" in line.split() for line in debug_info.splitlines())

    @staticmethod
    async def _replay_show(bus: Any) -> None:
        """Synchronize an already-focused input context through Fcitx itself."""

        from dbus_next import Message

        # Later client-driven focus requests remain available if replay fails.
        with suppress(Exception):
            await bus.call(
                Message(
                    destination=FCITX_BUS_NAME,
                    path=FCITX_VIRTUAL_KEYBOARD_PATH,
                    interface=FCITX_VIRTUAL_KEYBOARD_INTERFACE,
                    member="ShowVirtualKeyboard",
                )
            )

    async def _hide_if_unfocused(self, bus: Any, hide: Callable[[], None]) -> None:
        try:
            await asyncio.sleep(self._hide_focus_delay)
            focused = await self._has_focused_input_context(bus)
            current_ui = await self._current_ui(bus)
            if focused and current_ui != "virtualkeyboard":
                await self._replay_show(bus)
            else:
                hide()
        finally:
            if self._pending_hide is asyncio.current_task():
                self._pending_hide = None

    @staticmethod
    async def _current_ui(bus: Any) -> str | None:
        from dbus_next import Message, MessageType

        try:
            reply = await bus.call(
                Message(
                    destination=FCITX_BUS_NAME,
                    path=FCITX_CONTROLLER_PATH,
                    interface=FCITX_CONTROLLER_INTERFACE,
                    member="CurrentUI",
                )
            )
        except Exception:
            return None
        if reply.message_type is not MessageType.METHOD_RETURN or not reply.body:
            return None
        current_ui = reply.body[0]
        return current_ui if isinstance(current_ui, str) else None

    def _cancel_pending_hide(self) -> None:
        task = self._pending_hide
        self._pending_hide = None
        if task is not None:
            task.cancel()

    async def stop(self) -> None:
        pending_hide = self._pending_hide
        self._cancel_pending_hide()
        if pending_hide is not None:
            with suppress(asyncio.CancelledError):
                await pending_hide
        bus = self._bus
        interface = self._interface
        self._bus = None
        self._interface = None
        if bus is None:
            return
        try:
            await bus.release_name(FCITX_OSK_BUS_NAME)
        finally:
            if interface is not None:
                bus.unexport(FCITX_OSK_PATH, interface)
            bus.disconnect()


class WvkbdAdapter:
    """Own one wvkbd child while Fcitx supplies later semantic focus events."""

    def __init__(
        self,
        spawner: ProcessSpawner | None = None,
        visibility_host: VisibilityHost | None = None,
        *,
        readiness: StartupReadiness | None = None,
        palette_source: OskPaletteSource | None = None,
        settings_provider: Callable[[], OskSettings] | None = None,
        stop_timeout: float = 2.0,
        retint_settle: float = 0.25,
    ) -> None:
        self._spawner = spawner or AsyncioProcessSpawner()
        self._visibility_host = visibility_host or FcitxVisibilityHost()
        self._readiness = readiness or ProcSignalReadiness()
        self._palette_source = palette_source or OmarchyOskPaletteSource()
        # Called at every spawn, not once at startup, so an edited settings file lands at the
        # next fold without a service restart. The default provider is the shipped profile.
        self._settings_provider = settings_provider or OskSettings
        self._stop_timeout = stop_timeout
        self._retint_settle = retint_settle
        self._process: OskProcess | None = None
        self._host_started = False
        self._visible = False
        self._pending_theme_refresh = False
        self._theme_refresh_task: asyncio.Task[None] | None = None
        # Serializes every process replacement, including the one a deferred refresh
        # schedules from the synchronous Fcitx hide callback.
        self._transition_lock = asyncio.Lock()

    async def set_enabled(self, enabled: bool) -> None:
        async with self._transition_lock:
            if enabled:
                await self._start()
            else:
                await self._stop()

    async def refresh_theme(self) -> None:
        """Re-read the Omarchy palette; wvkbd 0.20 can only retint by respawning.

        A visible keyboard is never torn down: destroying the layer between `touch_down` and
        `touch_up` leaves the key logically pressed and repeating, which is the stuck-key
        failure documented in `docs/on-screen-keyboard.md`. Record the intent instead and
        respawn at the next Hide.
        """

        async with self._transition_lock:
            if self._process is None or self._process.returncode is not None:
                self._pending_theme_refresh = False
                return
            if self._visible:
                self._pending_theme_refresh = True
                return
            self._pending_theme_refresh = False
            await self._respawn_child()

    async def _start(self) -> None:
        if self._process is not None and self._process.returncode is None and self._host_started:
            return
        await self._stop()
        await self._spawn_child()

        try:
            await self._visibility_host.start(self._show, self._hide)
            self._host_started = True
        except Exception as error:
            await self._stop_process()
            raise WvkbdError("osk_focus_integration_failed") from error

    async def _spawn_child(self) -> None:
        """Start one hidden child at the current theme and wait for its signal handlers."""

        try:
            self._process = await self._spawner.spawn(
                (
                    "wvkbd-mobintl",
                    "--hidden",
                    *self._sizing_args(settings := self._settings()),
                    *await self._palette_args(settings),
                ),
                dict(os.environ),
                start_new_session=True,
            )
        except FileNotFoundError as error:
            raise WvkbdError("osk_backend_unavailable") from error
        except (OSError, ValueError) as error:
            raise WvkbdError("osk_transition_failed") from error

        self._visible = False
        try:
            await self._readiness.wait(self._process)
        except Exception as error:
            await self._stop_process()
            raise WvkbdError("osk_backend_not_ready") from error

    async def _respawn_child(self) -> None:
        """Replace only the child. The bus name and exported Fcitx interface stay put.

        Verified live on the X390 Yoga: the client's input context, the bus owner, and
        the settled layer geometry all survive, so the visibility host must not be restarted.
        """

        await self._stop_process()
        await self._spawn_child()

    def _settings(self) -> OskSettings:
        """The user's settings for one spawn, or the shipped profile if they cannot be read.

        Read once and handed to both renderers. Reading twice would let a file edited between
        the two calls put a size from one revision beside an opacity from another, and would
        make the number of times a provider is called part of this class's contract.
        """

        try:
            return self._settings_provider()
        except Exception:
            return OskSettings()

    def _sizing_args(self, settings: OskSettings) -> tuple[str, ...]:
        """Follow the user's settings, but never let a settings problem withhold a keyboard."""

        try:
            args = settings.to_args()
            # A custom provider can bypass the validated file adapter. Keep its NUL out of
            # execve rather than allowing `ValueError: embedded null byte` to escape.
            if any("\x00" in argument for argument in args):
                return WVKBD_SIZING_ARGS
            return args
        except Exception:
            return WVKBD_SIZING_ARGS

    def _opacity(self, settings: OskSettings) -> int:
        """The user's opacity, or a solid keyboard if it is not a percentage.

        Checked here rather than trusted, for the same reason the sizing arguments are searched
        for NUL: a custom provider bypasses the validated file adapter, and a percentage that is
        not a whole number would fail while rendering the argument vector rather than before it.
        """

        opacity = getattr(settings, "opacity", None)
        if isinstance(opacity, bool) or not isinstance(opacity, int):
            return OskSettings().opacity
        return opacity

    async def _palette_args(self, settings: OskSettings) -> tuple[str, ...]:
        """Follow the active Omarchy theme, but never let theming block the keyboard."""

        try:
            palette = await self._palette_source.resolve()
        except Exception:
            return ()
        return palette.to_args(self._opacity(settings))

    async def _stop(self) -> None:
        self._cancel_theme_refresh()
        if self._host_started:
            self._host_started = False
            try:
                await self._visibility_host.stop()
            except Exception as error:
                await self._stop_process()
                raise WvkbdError("osk_focus_integration_failed") from error
        await self._stop_process()

    def _show(self) -> None:
        self._visible = self._signal(signal.SIGUSR2)

    def _hide(self) -> None:
        self._signal(signal.SIGUSR1)
        self._visible = False
        if self._pending_theme_refresh:
            self._schedule_theme_refresh()

    def _schedule_theme_refresh(self) -> None:
        """Fcitx drives hide synchronously, so the deferred respawn needs its own task."""

        if self._theme_refresh_task is not None and not self._theme_refresh_task.done():
            return
        self._theme_refresh_task = asyncio.create_task(self._refresh_theme_after_hide())

    async def _refresh_theme_after_hide(self) -> None:
        """Let the hide land before replacing the child.

        `SIGUSR1` is asynchronous: wvkbd has not necessarily processed it, or released a key
        that is still logically held, by the time this task first runs. Killing it in the same
        breath as the hide request destroys its virtual-keyboard object with no release to
        deliver, which is the stuck-key failure this deferral exists to prevent, reintroduced
        one step later. The keyboard is already hidden, so the wait costs nothing visible.
        """

        # A failed respawn leaves no process, which the runtime's periodic reconciliation
        # restarts. Stale colors are never worth propagating an error into a hide callback.
        with suppress(WvkbdError):
            await asyncio.sleep(self._retint_settle)
            await self.refresh_theme()

    def _cancel_theme_refresh(self) -> None:
        """Drop a deferred respawn. Never awaited: the caller holds the transition lock."""

        self._pending_theme_refresh = False
        task = self._theme_refresh_task
        self._theme_refresh_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _signal(self, value: int) -> bool:
        process = self._process
        if process is None or process.returncode is not None:
            return False
        try:
            process.send_signal(value)
        except (ProcessLookupError, OSError):
            return False
        return True

    async def _stop_process(self) -> None:
        process = self._process
        self._process = None
        self._visible = False
        if process is None or process.returncode is not None:
            return
        try:
            # wvkbd 0.20 can segfault while dispatching stale Wayland events during
            # teardown. It owns no persistent state, so skip that unsafe path.
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=self._stop_timeout)
        except ProcessLookupError:
            return
        except OSError as error:
            raise WvkbdError("osk_transition_failed") from error
