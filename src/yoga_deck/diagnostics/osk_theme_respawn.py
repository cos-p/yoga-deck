"""Observe whether a wvkbd palette respawn preserves the Fcitx input context.

`docs/osk-theming.md` argues from resource ownership that Yoga Deck can replace its wvkbd child
while still holding `org.fcitx.Fcitx5.VirtualKeyboard`, so a theme change retints the keyboard
without the client losing its input context. That is the one load-bearing assumption in the
theming design that has not been observed on the physical machine.

This campaign owns exactly the two resources the real adapter owns and mutates nothing else. It
records app-agnostic evidence only: layer geometry, process identity, bus ownership, and Fcitx's
`CurrentUI`. Fcitx's `DebugInfo` reply is never requested or retained here, no window titles or
client identities are read, and screenshots are cropped to the keyboard layer so no surrounding
window content is captured.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yoga_deck.adapters.omarchy_theme import (
    OmarchyOskPaletteSource,
    OskPalette,
    derive_osk_palette,
    parse_colors_toml,
)
from yoga_deck.adapters.wvkbd import (
    FCITX_OSK_BUS_NAME,
    WVKBD_SIZING_ARGS,
    AsyncioProcessSpawner,
    FcitxVisibilityHost,
    ProcSignalReadiness,
)

WVKBD_NAMESPACE = "wvkbd"
BUNDLED_THEMES = Path("/usr/share/omarchy/themes")


class OskThemeProtocolError(RuntimeError):
    """Stable failure that never carries desktop content or client identity."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LayerObservation:
    monitor: str
    geometry: str

    def as_dict(self) -> dict[str, str]:
        return {"monitor": self.monitor, "geometry": self.geometry}


@dataclass(frozen=True, slots=True)
class OskThemeRespawnSummary:
    palette_before: str
    palette_after: str
    pid_before: int
    pid_after: int
    bus_owner_before: str | None
    bus_owner_after: str | None
    layer_before: LayerObservation | None
    layer_after: LayerObservation | None
    fcitx_ui_before: str | None
    fcitx_ui_after: str | None
    remap_ms: float
    client_hide_observed: bool
    screenshots: tuple[str, ...]

    @property
    def bus_name_retained(self) -> bool:
        return self.bus_owner_before is not None and self.bus_owner_after is not None

    @property
    def bus_owner_unchanged(self) -> bool:
        return self.bus_owner_before == self.bus_owner_after

    @property
    def child_replaced(self) -> bool:
        return self.pid_before != self.pid_after

    @property
    def layer_remapped(self) -> bool:
        return self.layer_after is not None

    @property
    def geometry_unchanged(self) -> bool:
        return (
            self.layer_before is not None
            and self.layer_after is not None
            and self.layer_before.geometry == self.layer_after.geometry
        )

    @property
    def fcitx_ui_unchanged(self) -> bool:
        return self.fcitx_ui_before == self.fcitx_ui_after

    @property
    def focus_preserved(self) -> bool:
        """The assumption under test: the client keeps its context across the respawn."""

        return self.bus_name_retained and self.bus_owner_unchanged and self.layer_remapped

    def as_dict(self) -> dict[str, Any]:
        return {
            "palette_before": self.palette_before,
            "palette_after": self.palette_after,
            "child_replaced": self.child_replaced,
            "bus_name_retained": self.bus_name_retained,
            "bus_owner_unchanged": self.bus_owner_unchanged,
            "layer_remapped": self.layer_remapped,
            "geometry_unchanged": self.geometry_unchanged,
            "geometry_before": self.layer_before.geometry if self.layer_before else None,
            "geometry_after": self.layer_after.geometry if self.layer_after else None,
            "fcitx_ui_before": self.fcitx_ui_before,
            "fcitx_ui_after": self.fcitx_ui_after,
            "fcitx_ui_unchanged": self.fcitx_ui_unchanged,
            "focus_preserved": self.focus_preserved,
            "remap_ms": self.remap_ms,
            "client_hide_observed": self.client_hide_observed,
            "screenshots": list(self.screenshots),
        }


def _hyprctl(*args: str) -> Any:
    result = subprocess.run(
        ["hyprctl", "-j", *args], capture_output=True, text=True, timeout=5, check=False
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def keyboard_layer() -> tuple[LayerObservation, str] | None:
    """The wvkbd layer surface with a grim geometry string, or None when unmapped."""

    for monitor, levels in (_hyprctl("layers") or {}).items():
        for entries in levels.get("levels", {}).values():
            for entry in entries:
                if WVKBD_NAMESPACE in entry.get("namespace", ""):
                    geometry = f"{entry['w']}x{entry['h']}+{entry['x']}+{entry['y']}"
                    grim = f"{entry['x']},{entry['y']} {entry['w']}x{entry['h']}"
                    return LayerObservation(monitor=monitor, geometry=geometry), grim
    return None


def bus_owner(name: str) -> str | None:
    result = subprocess.run(
        ["busctl", "--user", "list", "--no-legend"], capture_output=True, text=True, check=False
    )
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == name:
            return fields[1] if len(fields) > 1 else "owned"
    return None


def fcitx_current_ui() -> str | None:
    """Read only Fcitx's UI name. No input context or program name is requested."""

    result = subprocess.run(
        [
            "busctl",
            "--user",
            "call",
            "org.fcitx.Fcitx5",
            "/controller",
            "org.fcitx.Fcitx.Controller1",
            "CurrentUI",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split('"')
    return parts[1] if len(parts) > 1 else None


def capture_layer_screenshot(destination: Path, max_height_fraction: float = 0.5) -> str | None:
    """Capture only the keyboard layer so no surrounding window content is recorded.

    The rectangle is re-read at capture time rather than reused from an earlier observation,
    and a layer taller than `max_height_fraction` of its output is refused as unsettled. A
    stale or pre-commit rectangle would otherwise photograph the user's whole screen.
    """

    layer = keyboard_layer()
    if layer is None:
        return None
    observation, grim_geometry = layer
    monitors = _hyprctl("monitors") or []
    output_height = next(
        (
            m.get("height", 0) / (m.get("scale") or 1)
            for m in monitors
            if m.get("name") == observation.monitor
        ),
        0,
    )
    height = int(observation.geometry.split("x", 1)[1].split("+", 1)[0])
    if output_height and height > output_height * max_height_fraction:
        return None
    result = subprocess.run(
        ["grim", "-g", grim_geometry, str(destination)], capture_output=True, check=False
    )
    return destination.name if result.returncode == 0 else None


def palette_for(theme: str | None) -> tuple[str, OskPalette]:
    if theme is None:
        return "current", OmarchyOskPaletteSource().resolve_sync()
    colors = BUNDLED_THEMES / theme / "colors.toml"
    if not colors.is_file():
        raise OskThemeProtocolError("osk_theme_palette_unavailable")
    return theme, derive_osk_palette(parse_colors_toml(colors.read_text()))


class _Child:
    """The owned wvkbd process, replaceable without touching the visibility host."""

    def __init__(self) -> None:
        self._spawner = AsyncioProcessSpawner()
        self._readiness = ProcSignalReadiness()
        self.process: Any = None
        self.visible = False

    async def spawn(self, palette: OskPalette) -> None:
        self.process = await self._spawner.spawn(
            ("wvkbd-mobintl", "--hidden", *WVKBD_SIZING_ARGS, *palette.to_args()),
            dict(os.environ),
            start_new_session=True,
        )
        await self._readiness.wait(self.process)

    async def replace(self, palette: OskPalette) -> None:
        """Kill and respawn only the child. The bus name and exported interface stay put."""

        self.process.kill()
        await self.process.wait()
        was_visible = self.visible
        await self.spawn(palette)
        self.visible = was_visible
        if was_visible:
            self.show()

    def show(self) -> None:
        self.visible = True
        self._signal(signal.SIGUSR2)

    def hide(self) -> None:
        self.visible = False
        self._signal(signal.SIGUSR1)

    def _signal(self, value: int) -> None:
        if self.process is not None and self.process.returncode is None:
            with suppress(ProcessLookupError, OSError):
                self.process.send_signal(value)

    async def stop(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()


async def _await_layer(present: bool, timeout: float) -> tuple[LayerObservation, str] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        layer = keyboard_layer()
        if (layer is not None) == present:
            return layer
        await asyncio.sleep(0.05)
    return keyboard_layer()


async def _await_settled_layer(timeout: float) -> tuple[LayerObservation, str] | None:
    """Wait for a layer whose geometry stops changing.

    A respawned wvkbd surface is briefly mapped at the full area below the bar before it
    commits its exclusive zone, so the first geometry Hyprland reports is not the final one.
    Sampling it would both misreport the remap and widen a screenshot past the keyboard.
    """

    deadline = time.monotonic() + timeout
    previous: str | None = None
    while time.monotonic() < deadline:
        layer = keyboard_layer()
        if layer is not None:
            current = layer[0].geometry
            if current == previous:
                return layer
            previous = current
        else:
            previous = None
        await asyncio.sleep(0.1)
    return keyboard_layer()


async def _run(
    *, theme: str | None, seconds: int, dwell: float, shots: Path, prompt: Any
) -> OskThemeRespawnSummary:
    before_name, before_palette = palette_for(None)
    after_name, after_palette = palette_for(theme)

    if bus_owner(FCITX_OSK_BUS_NAME) is not None:
        raise OskThemeProtocolError("osk_virtual_keyboard_name_already_owned")

    child = _Child()
    host = FcitxVisibilityHost()
    screenshots: list[str] = []

    await child.spawn(before_palette)
    await host.start(child.show, child.hide)
    prompt(
        f"osk-theme: armed with the {before_name} palette. "
        "Focus an empty text field so Fcitx requests the keyboard."
    )
    try:
        mapped = await _await_settled_layer(timeout=seconds)
        if mapped is None:
            raise OskThemeProtocolError("osk_keyboard_never_mapped")

        # Hold the first palette long enough to be seen and photographed. Hyprland fades a
        # newly mapped layer in, and respawning the moment geometry settles makes the
        # before state flash past both the operator and the capture.
        await asyncio.sleep(dwell)
        layer_before = mapped[0]
        pid_before = child.process.pid
        owner_before = bus_owner(FCITX_OSK_BUS_NAME)
        ui_before = fcitx_current_ui()
        shot = capture_layer_screenshot(shots / "01-before-respawn.png")
        if shot:
            screenshots.append(shot)

        prompt(
            f"osk-theme: {before_name} palette shown for {dwell:g}s; "
            f"respawning the child with the {after_name} palette."
        )
        started = time.monotonic()
        await child.replace(after_palette)
        remapped = await _await_settled_layer(timeout=seconds)
        remap_ms = round((time.monotonic() - started) * 1000, 1)

        await asyncio.sleep(dwell)
        layer_after = remapped[0] if remapped else None
        shot = capture_layer_screenshot(shots / "02-after-respawn.png")
        if shot:
            screenshots.append(shot)

        prompt(
            "osk-theme: type a few characters on the keyboard, confirm no key repeats, "
            "then click away from the text field to release it."
        )
        hidden = await _await_layer(present=False, timeout=seconds)

        return OskThemeRespawnSummary(
            palette_before=before_name,
            palette_after=after_name,
            pid_before=pid_before,
            pid_after=child.process.pid,
            bus_owner_before=owner_before,
            bus_owner_after=bus_owner(FCITX_OSK_BUS_NAME),
            layer_before=layer_before,
            layer_after=layer_after,
            fcitx_ui_before=ui_before,
            fcitx_ui_after=fcitx_current_ui(),
            remap_ms=remap_ms,
            client_hide_observed=hidden is None,
            screenshots=tuple(screenshots),
        )
    finally:
        await host.stop()
        await child.stop()


def run_guided_osk_theme_respawn(
    *,
    theme: str | None = None,
    seconds: int = 60,
    dwell: float = 3.0,
    output: Path | None = None,
    prompt_callback: Any = print,
) -> OskThemeRespawnSummary:
    """Replace the owned wvkbd child mid-session and observe what survives.

    Nothing outside Yoga Deck's own two resources is mutated: no package files, no theme is
    applied, no Hyprland configuration is touched, and Omarchy's Fcitx keeps input-method
    ownership throughout.
    """

    if seconds < 5 or dwell < 0:
        raise ValueError("osk_theme_timing_invalid")
    shots = output or Path.cwd()
    shots.mkdir(parents=True, exist_ok=True)
    return asyncio.run(
        _run(theme=theme, seconds=seconds, dwell=dwell, shots=shots, prompt=prompt_callback)
    )
