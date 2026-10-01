"""Locate the live Hyprland instance without trusting a signature inherited at startup.

Hyprland's watchdog launcher restarts the compositor after an unclean exit. The replacement
instance has a new signature, but a long-running user service keeps the signature it inherited
when it started, so every ``hyprctl`` call and event-socket connection would target a dead
instance until the service itself restarts. Resolution reads only the per-user instance lock
files and process names; it never returns or logs anything beyond the chosen signature.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

SIGNATURE_VARIABLE = "HYPRLAND_INSTANCE_SIGNATURE"
_PROC_ROOT = Path("/proc")


def resolve_instance_signature(
    environ: Mapping[str, str] | None = None, *, proc_root: Path = _PROC_ROOT
) -> str | None:
    """Return the inherited signature while it is live, else the single live replacement.

    With no live instance, or more than one candidate, the inherited value is returned
    unchanged so callers keep their previous failure behavior instead of guessing.
    """

    environ = os.environ if environ is None else environ
    configured = environ.get(SIGNATURE_VARIABLE) or None
    runtime_dir = environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return configured
    live = live_instance_signatures(Path(runtime_dir) / "hypr", proc_root)
    if configured is not None and configured in live:
        return configured
    if len(live) == 1:
        return live[0]
    return configured


def live_instance_signatures(hypr_dir: Path, proc_root: Path = _PROC_ROOT) -> tuple[str, ...]:
    try:
        directories = sorted(path for path in hypr_dir.iterdir() if path.is_dir())
    except OSError:
        return ()
    return tuple(path.name for path in directories if _is_live(path, proc_root))


def _is_live(directory: Path, proc_root: Path) -> bool:
    try:
        first_line = (directory / "hyprland.lock").read_text().splitlines()[0]
        pid = int(first_line.strip())
        command = (proc_root / str(pid) / "comm").read_text().strip()
    except (OSError, IndexError, ValueError):
        return False
    return pid > 0 and command.lower() == "hyprland"
