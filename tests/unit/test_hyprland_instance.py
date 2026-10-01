"""Instance-signature resolution after a compositor restart; no real Hyprland is contacted."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from yoga_deck.adapters import hyprland_inputs
from yoga_deck.adapters.hyprland_instance import resolve_instance_signature
from yoga_deck.adapters.hyprland_monitor_events import (
    HyprlandMonitorEventError,
    Socket2Connection,
)


def add_instance(runtime: Path, proc: Path, signature: str, pid: int, *, alive: bool = True):
    directory = runtime / "hypr" / signature
    directory.mkdir(parents=True)
    (directory / "hyprland.lock").write_text(f"{pid}\nwayland-1\n")
    if alive:
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "comm").write_text("Hyprland\n")
    return directory


@pytest.fixture
def roots(tmp_path):
    runtime = tmp_path / "run"
    proc = tmp_path / "proc"
    runtime.mkdir()
    proc.mkdir()
    return runtime, proc


def test_live_inherited_signature_is_kept(roots) -> None:
    runtime, proc = roots
    add_instance(runtime, proc, "old", 10)
    add_instance(runtime, proc, "other", 11)
    environ = {"XDG_RUNTIME_DIR": str(runtime), "HYPRLAND_INSTANCE_SIGNATURE": "old"}

    assert resolve_instance_signature(environ, proc_root=proc) == "old"


def test_stale_signature_follows_the_single_replacement_instance(roots) -> None:
    runtime, proc = roots
    add_instance(runtime, proc, "crashed", 10, alive=False)
    add_instance(runtime, proc, "restarted", 20)
    environ = {"XDG_RUNTIME_DIR": str(runtime), "HYPRLAND_INSTANCE_SIGNATURE": "crashed"}

    assert resolve_instance_signature(environ, proc_root=proc) == "restarted"


def test_reused_pid_of_another_program_is_not_a_live_instance(roots) -> None:
    runtime, proc = roots
    add_instance(runtime, proc, "crashed", 10, alive=False)
    (proc / "10").mkdir()
    (proc / "10" / "comm").write_text("bash\n")
    environ = {"XDG_RUNTIME_DIR": str(runtime), "HYPRLAND_INSTANCE_SIGNATURE": "crashed"}

    assert resolve_instance_signature(environ, proc_root=proc) == "crashed"


def test_missing_signature_uses_the_only_live_instance(roots) -> None:
    # A TTY recovery shell has XDG_RUNTIME_DIR but no compositor environment.
    runtime, proc = roots
    add_instance(runtime, proc, "only", 30)

    assert resolve_instance_signature({"XDG_RUNTIME_DIR": str(runtime)}, proc_root=proc) == "only"


@pytest.mark.parametrize("live", [(), ("a", "b")])
def test_absent_or_ambiguous_replacement_keeps_inherited_value(roots, live) -> None:
    runtime, proc = roots
    add_instance(runtime, proc, "crashed", 10, alive=False)
    for index, signature in enumerate(live):
        add_instance(runtime, proc, signature, 40 + index)
    environ = {"XDG_RUNTIME_DIR": str(runtime), "HYPRLAND_INSTANCE_SIGNATURE": "crashed"}

    assert resolve_instance_signature(environ, proc_root=proc) == "crashed"


def test_malformed_lock_and_missing_runtime_dir_do_not_raise(roots) -> None:
    runtime, proc = roots
    directory = runtime / "hypr" / "broken"
    directory.mkdir(parents=True)
    (directory / "hyprland.lock").write_text("not-a-pid\n")

    assert resolve_instance_signature({"XDG_RUNTIME_DIR": str(runtime)}, proc_root=proc) is None
    assert resolve_instance_signature({"HYPRLAND_INSTANCE_SIGNATURE": "x"}, proc_root=proc) == "x"


def test_hyprctl_subprocess_targets_the_replacement_instance(roots, monkeypatch) -> None:
    runtime, proc = roots
    add_instance(runtime, proc, "crashed", 10, alive=False)
    add_instance(runtime, proc, "restarted", 20)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "crashed")
    monkeypatch.setattr(hyprland_inputs, "_PROC_ROOT", proc)
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs.get("env")))
        return subprocess.CompletedProcess(args, 0, "{}", "")

    monkeypatch.setattr(hyprland_inputs.subprocess, "run", fake_run)
    hyprland_inputs.SubprocessRunner().run(["hyprctl", "-j", "devices"], 1.0)

    ((args, env),) = calls
    assert args == ["hyprctl", "-j", "devices"]
    assert env["HYPRLAND_INSTANCE_SIGNATURE"] == "restarted"


def test_hyprctl_subprocess_inherits_environment_when_signature_is_current(
    roots, monkeypatch
) -> None:
    runtime, proc = roots
    add_instance(runtime, proc, "current", 20)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "current")
    monkeypatch.setattr(hyprland_inputs, "_PROC_ROOT", proc)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(kwargs.get("env"))
        return subprocess.CompletedProcess(args, 0, "{}", "")

    monkeypatch.setattr(hyprland_inputs.subprocess, "run", fake_run)
    hyprland_inputs.SubprocessRunner().run(["hyprctl", "-j", "devices"], 1.0)

    assert calls == [None]


@pytest.mark.asyncio
async def test_socket2_reconnects_to_the_replacement_instance(roots, monkeypatch) -> None:
    runtime, proc = roots
    add_instance(runtime, proc, "old", 10, alive=False)
    new_directory = add_instance(runtime, proc, "new", 20)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "old")
    monkeypatch.setattr("yoga_deck.adapters.hyprland_monitor_events._PROC_ROOT", proc)

    async def serve(reader, writer):
        writer.write(b"monitoraddedv2>>1,DP-1,private\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(serve, path=new_directory / ".socket2.sock")
    try:
        connection = await Socket2Connection.connect()
        events = connection.events()
        assert await anext(events) == "monitoraddedv2>>1,DP-1,private"
        with pytest.raises(HyprlandMonitorEventError) as error:
            await anext(events)
        assert error.value.code == "monitor_event_disconnected"
        await connection.close()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_socket2_without_any_instance_fails_with_stable_code(roots, monkeypatch) -> None:
    runtime, proc = roots
    add_instance(runtime, proc, "old", 10, alive=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("HYPRLAND_INSTANCE_SIGNATURE", "old")
    monkeypatch.setattr("yoga_deck.adapters.hyprland_monitor_events._PROC_ROOT", proc)

    with pytest.raises(HyprlandMonitorEventError) as error:
        await Socket2Connection.connect()

    assert error.value.code == "hyprland_socket_unavailable"
    assert "old" not in str(error.value)
