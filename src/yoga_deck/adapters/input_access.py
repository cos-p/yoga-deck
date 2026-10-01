"""Shared evdev access boundary for the input roles the runtime opens itself."""

from __future__ import annotations

from pathlib import Path


class InputAccessDenied(Exception):
    """Marker for a source whose device exists but this user may not open.

    Adapters mix it into their own stable error so the runtime can tell a missing ``input``
    group apart from a disconnect without seeing a device path or errno text.
    """


def event_nodes(dev_root: Path = Path("/dev")) -> list[str]:
    """Every evdev node, readable or not.

    ``evdev.list_devices()`` silently drops nodes this user cannot open, which makes a
    permission problem indistinguishable from absent hardware.
    """

    directory = dev_root / "input"
    try:
        return sorted(str(node) for node in directory.iterdir() if node.name.startswith("event"))
    except OSError:
        return []
