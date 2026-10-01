"""User-service entry point for the Yoga Deck posture runtime."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections.abc import Awaitable, Callable

from yoga_deck.adapters.hyprland_inputs import AsyncHyprlandInputAdapter
from yoga_deck.adapters.hyprland_monitor_events import HyprlandMonitorEventSource
from yoga_deck.adapters.hyprland_rotation import AsyncHyprlandRotationAdapter
from yoga_deck.adapters.omarchy_ocr import AsyncOmarchyOcrAdapter
from yoga_deck.adapters.pen_action_runtime import ContinuousPenActionSource, PenActionMapping
from yoga_deck.adapters.resume_monitor import ResumeMonitorSource
from yoga_deck.adapters.scratchpad import ScratchpadAdapter
from yoga_deck.adapters.sensor_proxy import SensorProxySource
from yoga_deck.adapters.shell_transport import ShellTransportServer
from yoga_deck.adapters.tablet_switch_runtime import ContinuousTabletSwitchSource
from yoga_deck.adapters.user_settings import UserSettingsSource
from yoga_deck.adapters.wvkbd import WvkbdAdapter
from yoga_deck.coordinator import (
    InputCoordinator,
    OcrCoordinator,
    OskCoordinator,
    RotationCoordinator,
    ScratchpadCoordinator,
    SystemCoordinator,
)
from yoga_deck.core.settings import OskSettings, ParsedSettings
from yoga_deck.runtime import PostureRuntime, SafeDiagnostic

#: Long enough to name several mistyped keys, short enough that a pathological settings file
#: cannot flood the journal through this path.
REJECTED_KEYS_LIMIT = 200

#: Stop requests from systemd (SIGTERM) and an interactive terminal (SIGINT).
SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)

#: Upper bound on in-process shutdown after a stop signal. Recovery is a few bounded hyprctl calls
#: and one wvkbd stop; this stays well inside systemd's default 90 s stop timeout so the process
#: exits on its own and the unit's ExecStopPost recovery still runs as the backup.
SHUTDOWN_TIMEOUT = 10.0

#: Exit status when in-process shutdown did not finish within SHUTDOWN_TIMEOUT.
EXIT_SHUTDOWN_TIMED_OUT = 1


def _emit(diagnostic: SafeDiagnostic) -> None:
    print(json.dumps(diagnostic, sort_keys=True), file=sys.stderr, flush=True)


def _report(parsed: ParsedSettings) -> ParsedSettings:
    """Name whatever the settings file lost, then carry on with what survived."""

    if parsed.rejected:
        _emit(
            {
                "component": "settings",
                "code": "settings_keys_rejected",
                "keys": ",".join(parsed.rejected)[:REJECTED_KEYS_LIMIT],
            }
        )
    return parsed


async def _run() -> None:
    settings_source = UserSettingsSource()

    def osk_settings() -> OskSettings:
        # Resolved per spawn rather than captured once, so editing the settings file takes
        # effect at the next fold instead of at the next service restart.
        return _report(settings_source.resolve()).settings.osk

    startup = _report(settings_source.resolve())
    coordinator = SystemCoordinator(
        InputCoordinator(AsyncHyprlandInputAdapter()),
        RotationCoordinator(AsyncHyprlandRotationAdapter()),
        ocr=OcrCoordinator(AsyncOmarchyOcrAdapter()),
        scratchpad=ScratchpadCoordinator(ScratchpadAdapter()),
        osk=OskCoordinator(WvkbdAdapter(settings_provider=osk_settings)),
    )
    runtime = PostureRuntime(
        ContinuousTabletSwitchSource(),
        coordinator,
        _emit,
        orientation_source=SensorProxySource(),
        # The far-button mapping is bound when the pen device is opened, so it is read once
        # here rather than per spawn; both settings files feed the same loader.
        pen_action_source=ContinuousPenActionSource(
            PenActionMapping(startup.settings.pen.far_button_action)
        ),
        resume_source=ResumeMonitorSource(),
        monitor_source=HyprlandMonitorEventSource(),
        # The same object the keyboard reads from, so a panel control and the next spawn can
        # never disagree about what the settings files say.
        settings_store=settings_source,
    )
    shell = ShellTransportServer(
        state_provider=lambda: runtime.state,
        command_handler=runtime.handle_shell_command,
        config_provider=settings_source.snapshot,
        degraded_provider=lambda: runtime.degraded,
    )
    await shell.start()
    try:
        await runtime.run()
    finally:
        await shell.close()


def _abandon(code: int) -> None:
    """Leave without waiting for a shutdown that exceeded its bound.

    ``asyncio.run`` would otherwise wait on the stuck work again while cleaning up. The unit's
    ``ExecStopPost`` recovery runs after this exit.
    """

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


async def run_until_signalled(
    main: Awaitable[None],
    *,
    emit: Callable[[SafeDiagnostic], None] = _emit,
    signals: tuple[signal.Signals, ...] = SHUTDOWN_SIGNALS,
    shutdown_timeout: float = SHUTDOWN_TIMEOUT,
    on_timeout: Callable[[int], None] = _abandon,
) -> int:
    """Run ``main`` until it ends or a stop signal arrives, then shut it down in order.

    The first signal cancels ``main``; :meth:`PostureRuntime.run` turns that cancellation into
    its reducer-planned shutdown (internal inputs re-enabled, OSK torn down) and shields that
    work from further cancellation. Later signals are reported and otherwise ignored, so they
    can neither skip nor repeat recovery. A shutdown that outlives ``shutdown_timeout`` is
    reported and handed to ``on_timeout`` rather than left for systemd to kill.
    """

    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(main)
    stop_requested = asyncio.Event()

    def request_stop() -> None:
        if stop_requested.is_set():
            emit({"component": "runtime", "code": "shutdown_in_progress"})
            return
        stop_requested.set()
        task.cancel()

    installed: list[signal.Signals] = []
    try:
        for signum in signals:
            loop.add_signal_handler(signum, request_stop)
            installed.append(signum)
        stop_waiter = asyncio.ensure_future(stop_requested.wait())
        try:
            await asyncio.wait({task, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_waiter.cancel()
        if not task.done():
            done, _ = await asyncio.wait({task}, timeout=shutdown_timeout)
            if not done:
                emit({"component": "runtime", "code": "shutdown_timed_out"})
                on_timeout(EXIT_SHUTDOWN_TIMED_OUT)
                return EXIT_SHUTDOWN_TIMED_OUT
        if task.cancelled():
            return 0
        # A crash that was not a requested stop must still fail the unit so systemd restarts it.
        task.result()
        return 0
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)


def main() -> int:
    return asyncio.run(run_until_signalled(_run()))


if __name__ == "__main__":
    raise SystemExit(main())
