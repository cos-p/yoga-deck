"""CLI recovery against a real temporary transport and fake compositor."""

import asyncio

import pytest

from yoga_deck.adapters.hyprland_inputs import HyprlandInputRecovery, InputRole, InternalInput
from yoga_deck.adapters.shell_transport import ShellTransportServer, request_sync
from yoga_deck.cli import main
from yoga_deck.coordinator import (
    InputCoordinator,
    OskCoordinator,
    RotationCoordinator,
    SystemCoordinator,
)
from yoga_deck.core import TabletModeChanged
from yoga_deck.runtime import PostureRuntime

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_cli_recovery_survives_reconciliation_until_new_fold(tmp_path):
    class Inputs:
        enabled = True

        def discover_owned(self):
            return (InternalInput(InputRole.KEYBOARD, "at-translated-set-2-keyboard"),)

        def set_enabled(self, device, enabled):
            self.enabled = enabled

    class Noop:
        async def apply(self, orientation):
            pass

        async def set_enabled(self, enabled):
            pass

    inputs = Inputs()
    runtime = PostureRuntime(
        None,
        SystemCoordinator(
            InputCoordinator(inputs), RotationCoordinator(Noop()), osk=OskCoordinator(Noop())
        ),
    )
    await runtime._accept(TabletModeChanged(True))
    server = ShellTransportServer(
        state_provider=lambda: runtime.state,
        command_handler=runtime.handle_shell_command,
        socket_path=tmp_path / "shell.sock",
    )
    await server.start()
    try:
        result = await asyncio.to_thread(
            main,
            ["recover-inputs"],
            input_recovery=HyprlandInputRecovery(inputs),
            runtime_requester=lambda payload: request_sync(payload, socket_path=server.socket_path),
        )
        assert result == 0
        await runtime._reconcile_compositor()
        await runtime._accept(TabletModeChanged(True))  # duplicate observation after reopen
        assert inputs.enabled is True
        await runtime._accept(TabletModeChanged(False))
        await runtime._accept(TabletModeChanged(True))
        assert inputs.enabled is False
    finally:
        await server.close()
