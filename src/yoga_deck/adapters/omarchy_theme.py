"""Resolve the active Omarchy palette and derive the on-screen keyboard's colors.

wvkbd 0.20 takes its whole palette from its argument vector, so Yoga Deck resolves the theme
once per spawn. Omarchy's `omarchy theme color` is the shared cascade every Omarchy consumer
uses; reading `colors.toml` directly would miss its alias, derived-shade, and mode resolution.
Theming must never prevent the keyboard from starting, so every failure degrades to a palette
rather than raising. The role mapping and its contrast floors are documented in
`docs/osk-theming.md`.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

DEFAULT_COLORS_PATH = Path.home() / ".local/state/omarchy/current/theme/colors.toml"

#: Used when no Omarchy palette can be resolved. Run through the same derivation as a real
#: theme so there is exactly one code path producing wvkbd arguments.
FALLBACK_PALETTE: Mapping[str, str] = {
    "background": "#101010",
    "foreground": "#e6e6e6",
    "accent": "#5e81ac",
    "mode": "dark",
}

#: Minimum contrast of a key label against its own key face. Labels render at `Sans 20` on a
#: 1.25-scale display, past the WCAG large-text threshold, so 3:1 is the floor; the derivation
#: prefers a themed color reaching `_TEXT_PREFERRED` and only then falls back to the most
#: legible candidate.
TEXT_FLOOR = 3.0
_TEXT_PREFERRED = 4.5

#: Surface separation floors. Special keys sit nearer the board and normal keys stand proud of
#: them, so modifiers and layer switches read as recessed chrome.
SPECIAL_ON_BOARD = 1.20
KEY_ON_SPECIAL = 1.30
ACCENT_ON_KEY = 1.25

#: Chroma is the span between a color's strongest and weakest channel, as a fraction of full
#: scale. It is the quantity theme authors hold roughly constant up a surface ladder --
#: `catppuccin`'s base through surface2 runs 16, 18, 21, 24 out of 255 -- and, unlike HSL
#: saturation, it is not inflated near the lightness extremes. Surfaces are derived at constant
#: chroma for that reason.

#: Below this chroma a board reads as neutral and has no hue worth carrying, so the surface hue
#: is borrowed from the theme's accent instead. `last-horizon`'s `#0c0b0c` is the case this
#: exists for: a grey board belonging to an unmistakably amber theme.
BOARD_TINT_FLOOR = 0.02

#: Chroma given to surfaces whose hue is borrowed from the accent. Deliberately faint: a theme
#: that chose a neutral board asked for a related keyboard, not a colored one.
BORROWED_TINT = 0.05

#: Ceiling on a board's own preserved chroma, so a heavily saturated board cannot produce key
#: faces louder than the content above them.
SURFACE_TINT_CEILING = 0.16

#: Chroma multiplier per rung of the surface ladder. Theme ladders do not hold chroma flat,
#: they open it up as they lighten: `catppuccin` gains about 1.15x per rung and
#: `catppuccin-latte` about 1.44x. Holding chroma flat instead flattens a faintly tinted board
#: back to grey by the time it has been lifted twice, which is the complaint this addresses.
SURFACE_TINT_GAIN = 1.25

#: How far a special key's pressed and swipe shades sit from their normal-key counterparts, in
#: HSL lightness, measured toward the board. Special keys are recessed chrome in every other
#: role, and aliasing the two roles wasted variation wvkbd offers for free.
SPECIAL_SHADE_STEP = 0.06

_ROLE_FLAGS = (
    "bg",
    "fg",
    "fg-sp",
    "press",
    "press-sp",
    "swipe",
    "swipe-sp",
    "text",
    "text-sp",
    "text-press",
    "text-press-sp",
    "text-swipe",
    "text-swipe-sp",
)

#: The roles opacity applies to: every painted surface, and no label. wvkbd fills a key face
#: with cairo's SOURCE operator, so a key face replaces the board's alpha rather than stacking
#: on it, and then draws the label OVER the face -- an opaque glyph over a translucent face
#: composites to an opaque glyph. So holding the text roles at full alpha is what keeps a
#: see-through keyboard readable, and is why this is a per-role alpha rather than `--alpha`.
_TRANSLUCENT_ROLES = frozenset({"bg", "fg", "fg-sp", "press", "press-sp", "swipe", "swipe-sp"})


class PaletteCompletedProcess(Protocol):
    returncode: int
    stdout: str


class PaletteCommandRunner(Protocol):
    def run(self, args: list[str], timeout: float) -> PaletteCompletedProcess: ...


@dataclass(frozen=True, slots=True)
class OskPalette:
    """One resolved wvkbd color role per flag, as `#rrggbb`."""

    bg: str
    fg: str
    fg_sp: str
    press: str
    press_sp: str
    swipe: str
    swipe_sp: str
    text: str
    text_sp: str
    text_press: str
    text_press_sp: str
    text_swipe: str
    text_swipe_sp: str

    def to_args(self, opacity: int = 100) -> tuple[str, ...]:
        """Render as wvkbd flags. wvkbd wants `rrggbb`, with no leading `#`.

        `opacity` is a percentage of full and belongs to the user, not to the theme, which is
        why it arrives as an argument rather than as a field: a palette is what the theme said,
        and this is what the user asked to see of it. At 100 the arguments are the six-character
        form wvkbd has always been given; below that the surface roles gain wvkbd's optional
        two-character alpha suffix and the label roles deliberately do not.
        """

        values = (
            self.bg,
            self.fg,
            self.fg_sp,
            self.press,
            self.press_sp,
            self.swipe,
            self.swipe_sp,
            self.text,
            self.text_sp,
            self.text_press,
            self.text_press_sp,
            self.text_swipe,
            self.text_swipe_sp,
        )
        alpha = min(max(round(opacity * 255 / 100), 0), 255)
        args: list[str] = []
        for flag, value in zip(_ROLE_FLAGS, values, strict=True):
            hex_value = value.lstrip("#")
            if alpha < 255 and flag in _TRANSLUCENT_ROLES:
                hex_value += f"{alpha:02x}"
            args.extend((f"--{flag}", hex_value))
        return tuple(args)


def _channels(color: str) -> tuple[int, int, int]:
    raw = color.lstrip("#")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def relative_luminance(color: str) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = _channels(color)
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = relative_luminance(first), relative_luminance(second)
    if lighter < darker:
        lighter, darker = darker, lighter
    return (lighter + 0.05) / (darker + 0.05)


def chroma(color: str) -> float:
    """Span between a color's strongest and weakest channel, as a fraction of full scale."""

    red, green, blue = _channels(color)
    return (max(red, green, blue) - min(red, green, blue)) / 255


def mix(start: str, end: str, amount: float) -> str:
    start_channels, end_channels = _channels(start), _channels(end)
    blended = (
        round(start_channels[index] * (1 - amount) + end_channels[index] * amount)
        for index in range(3)
    )
    return "#" + "".join(f"{channel:02x}" for channel in blended)


def to_hsl(color: str) -> tuple[float, float, float]:
    """Hue, saturation, and lightness in `0..1`. Hue is 0 for any achromatic colour."""

    red, green, blue = (channel / 255 for channel in _channels(color))
    high, low = max(red, green, blue), min(red, green, blue)
    lightness = (high + low) / 2
    if high == low:
        return 0.0, 0.0, lightness

    span = high - low
    saturation = span / (2 - high - low) if lightness > 0.5 else span / (high + low)
    if high == red:
        hue = (green - blue) / span + (6 if green < blue else 0)
    elif high == green:
        hue = (blue - red) / span + 2
    else:
        hue = (red - green) / span + 4
    return hue / 6, saturation, lightness


def from_hsl(hue: float, saturation: float, lightness: float) -> str:
    """Inverse of `to_hsl`, clamped. Always in gamut, which is why HSL is used here at all."""

    saturation = min(max(saturation, 0.0), 1.0)
    lightness = min(max(lightness, 0.0), 1.0)
    if saturation == 0:
        level = round(lightness * 255)
        return f"#{level:02x}{level:02x}{level:02x}"

    upper = (
        lightness * (1 + saturation)
        if lightness < 0.5
        else lightness + saturation - lightness * saturation
    )
    lower = 2 * lightness - upper

    def channel(offset: float) -> int:
        point = (hue + offset) % 1.0
        if point < 1 / 6:
            value = lower + (upper - lower) * 6 * point
        elif point < 1 / 2:
            value = upper
        elif point < 2 / 3:
            value = lower + (upper - lower) * (2 / 3 - point) * 6
        else:
            value = lower
        return round(value * 255)

    return f"#{channel(1 / 3):02x}{channel(0):02x}{channel(-1 / 3):02x}"


def _separate(base: str, toward: str, ratio: float, *, steps: int = 90) -> str:
    """Smallest mix of `base` toward `toward` reaching `ratio` against `base`."""

    for step in range(steps + 1):
        candidate = mix(base, toward, step / 100)
        if contrast_ratio(candidate, base) >= ratio:
            return candidate
    return mix(base, toward, steps / 100)


def _readable(candidates: Iterable[str], face: str) -> str:
    """First themed candidate clearing AA against `face`; else the most legible one."""

    ordered = list(candidates)
    for candidate in ordered:
        if contrast_ratio(candidate, face) >= _TEXT_PREFERRED:
            return candidate
    return max(ordered, key=lambda candidate: contrast_ratio(candidate, face))


def _color(palette: Mapping[str, str], *keys: str) -> str | None:
    """First key holding a `#rrggbb` literal.

    Omarchy palettes may also carry `rgb()`, `rgba()`, and gradient specifications. Those are
    treated as absent so a theme cannot inject an unparsed string into wvkbd's argument vector.
    """

    for key in keys:
        value = palette.get(key, "")
        if isinstance(value, str) and _HEX.match(value.strip()):
            return value.strip().lower()
    return None


_STEP = 1 / 255


def _hue_chroma(color: str) -> tuple[float, float, float]:
    """Hue, chroma, and lightness. Chroma replaces HSL saturation; see `BOARD_TINT_FLOOR`."""

    hue, _, lightness = to_hsl(color)
    return hue, chroma(color), lightness


def _shade(hue: float, chroma: float, lightness: float) -> str:
    """A color at `lightness` holding `hue` and, as far as the gamut allows, `chroma`.

    HSL's own span at a given lightness is `saturation * (1 - |2L - 1|)`, so the saturation that
    delivers a requested chroma is that expression inverted. Near black and near white the gamut
    cannot deliver it at all, and the clamp to full saturation is the honest answer.
    """

    reach = 1 - abs(2 * lightness - 1)
    saturation = 1.0 if reach <= 0 else min(chroma / reach, 1.0)
    return from_hsl(hue, saturation, lightness)


def _lightness_scan(hue: float, chroma: float, start: float, direction: float):
    """Colors stepping away from `start` lightness at one 8-bit level per step.

    Always reaches its endpoint: lightness 0 is black and lightness 1 is white whatever the
    chroma, so a scan cannot run out before every candidate has been offered.
    """

    for step in range(1, 256):
        lightness = min(max(start + direction * step * _STEP, 0.0), 1.0)
        yield _shade(hue, chroma, lightness)
        if lightness in (0.0, 1.0):
            return


def _tint(board: str, accent: str) -> tuple[float, float]:
    """The hue and chroma every derived surface is built from.

    A board with a hue of its own keeps it. A neutral board borrows the accent's hue at a faint
    chroma, which is what carries a theme like `last-horizon` -- grey board, amber accent --
    onto the keyboard at all.
    """

    hue, chroma, _ = _hue_chroma(board)
    if chroma >= BOARD_TINT_FLOOR:
        return hue, min(chroma, SURFACE_TINT_CEILING)

    accent_hue, accent_chroma, _ = _hue_chroma(accent)
    if accent_chroma >= BOARD_TINT_FLOOR:
        return accent_hue, BORROWED_TINT
    return hue, 0.0


def _rung(tint: tuple[float, float], step: int) -> tuple[float, float]:
    """`tint` as it should appear `step` rungs up the surface ladder."""

    hue, chroma = tint
    return hue, min(chroma * SURFACE_TINT_GAIN**step, SURFACE_TINT_CEILING)


def _lift(base: str, tint: tuple[float, float], *, lighten: bool, ratio: float, toward: str) -> str:
    """Smallest lightness move from `base`, in the theme's hue, reaching `ratio` against `base`.

    This replaced a mix toward pure white or black. That mix desaturates monotonically, so every
    step away from the board bleached the keyboard: on a tinted theme the key faces came out as
    grey slabs on a coloured board. Holding hue and saturation and moving only lightness carries
    the theme all the way up the ramp. The neutral mix remains as the fallback, so the contrast
    floor is still guaranteed for a palette that defeats the scan.
    """

    hue, chroma = tint
    _, _, lightness = to_hsl(base)
    for candidate in _lightness_scan(hue, chroma, lightness, 1.0 if lighten else -1.0):
        if contrast_ratio(candidate, base) >= ratio:
            return candidate
    return _separate(base, toward, ratio)


def _clear(color: str, face: str, ratio: float, *, lighten: bool, toward: str) -> str:
    """Move `color`'s lightness, keeping its hue, until it clears `ratio` against `face`.

    A darkened orange is still orange. The previous derivation discarded a failing accent
    outright and lifted a neutral in its place, which is exactly the low-contrast theme where
    the accent was the only theme colour the keyboard had.
    """

    if contrast_ratio(color, face) >= ratio:
        return color

    hue, chroma, lightness = _hue_chroma(color)
    for direction in (1.0, -1.0) if lighten else (-1.0, 1.0):
        for candidate in _lightness_scan(hue, chroma, lightness, direction):
            if contrast_ratio(candidate, face) >= ratio:
                return candidate
    return _separate(face, toward, ratio)


def _recess(color: str, board: str, amount: float) -> str:
    """`color` nudged toward the board's lightness: the special-key shade of a normal-key role."""

    hue, chroma, lightness = _hue_chroma(color)
    _, _, board_lightness = to_hsl(board)
    direction = 1.0 if board_lightness > lightness else -1.0
    return _shade(hue, chroma, min(max(lightness + direction * amount, 0.0), 1.0))


def _accent_label(accent: str, face: str) -> str | None:
    """The theme's accent on special-key glyphs, or `None` when it would not be legible there.

    This is the one place the *resting* keyboard shows an accent. Everything else the accent
    reached before was a pressed state, visible only for the length of a tap, which is why a
    themed keyboard still looked untinted while nobody was touching it. Shift, backspace, and
    the layer switches are large chrome glyphs, so `TEXT_FLOOR` is the applicable gate rather
    than the 4.5:1 preference the lettered keys are held to.
    """

    return accent if contrast_ratio(accent, face) >= TEXT_FLOOR else None


def derive_osk_palette(palette: Mapping[str, str]) -> OskPalette:
    """Derive wvkbd's surfaces from `background`, rather than reading a theme's shade ladder.

    Omarchy's `darker_background` / `lighter_background` / `dark_background` are frequently
    derived rather than authored, so on 10 of 22 bundled themes they collapse to within 1.05:1
    of each other and keys would be invisible against the board. Lifting from `background`
    guarantees separation on every theme; lifting in the theme's own hue keeps it recognisable.
    """

    board = _color(palette, "background", "color0") or FALLBACK_PALETTE["background"]

    # Lift toward whichever endpoint has more headroom. Taking the direction from `mode` would
    # leave no room to separate anything on a theme that declares a mode its background
    # contradicts, and this agrees with `mode` on every bundled theme.
    lighten = contrast_ratio(board, "#ffffff") >= contrast_ratio(board, "#000000")
    toward, opposite = ("#ffffff", "#000000") if lighten else ("#000000", "#ffffff")

    accent = _color(palette, "accent", "blue", "foreground") or FALLBACK_PALETTE["accent"]
    tint = _tint(board, accent)

    special = _lift(board, _rung(tint, 1), lighten=lighten, ratio=SPECIAL_ON_BOARD, toward=toward)
    key = _lift(special, _rung(tint, 2), lighten=lighten, ratio=KEY_ON_SPECIAL, toward=toward)

    press = _clear(accent, key, ACCENT_ON_KEY, lighten=lighten, toward=toward)
    press_sp = _clear(
        _recess(press, board, SPECIAL_SHADE_STEP),
        special,
        ACCENT_ON_KEY,
        lighten=lighten,
        toward=toward,
    )

    # The swipe trail travels against the lift so it never reads as another key face.
    swipe_seed = _color(palette, "selection", "selection_background") or _lift(
        key, _rung(tint, 2), lighten=not lighten, ratio=ACCENT_ON_KEY, toward=opposite
    )
    swipe = _clear(swipe_seed, key, ACCENT_ON_KEY, lighten=not lighten, toward=opposite)
    swipe_sp = _clear(
        _recess(swipe, board, SPECIAL_SHADE_STEP),
        special,
        ACCENT_ON_KEY,
        lighten=not lighten,
        toward=opposite,
    )

    foreground = _color(palette, "foreground", "color7") or FALLBACK_PALETTE["foreground"]
    # Both endpoints close the pool, so a legible label exists for any face: the better of
    # black and white is never below 4.58:1 against any color.
    text_pool = [
        foreground,
        _color(palette, "bright_foreground", "color15") or foreground,
        _color(palette, "light_foreground") or foreground,
        board,
        toward,
        opposite,
    ]

    return OskPalette(
        bg=board,
        fg=key,
        fg_sp=special,
        press=press,
        press_sp=press_sp,
        swipe=swipe,
        swipe_sp=swipe_sp,
        text=_readable(text_pool, key),
        text_sp=_accent_label(accent, special) or _readable(text_pool, special),
        text_press=_readable(text_pool, press),
        text_press_sp=_readable(text_pool, press_sp),
        text_swipe=_readable(text_pool, swipe),
        text_swipe_sp=_readable(text_pool, swipe_sp),
    )


def parse_colors_toml(text: str) -> dict[str, str]:
    """Minimal `key = "value"` reader for the degraded path.

    This deliberately does not reproduce Omarchy's alias and derived-shade cascade; it only has
    to supply the few keys `derive_osk_palette` needs when `omarchy theme color` is unavailable.
    """

    palette: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().strip("\"'")
        value = value.strip()
        if value[:1] in {'"', "'"}:
            quote = value[0]
            value = value[1:].partition(quote)[0]
        if key and value:
            palette[key] = value
    return palette


class SubprocessPaletteRunner:
    def run(self, args: list[str], timeout: float) -> PaletteCompletedProcess:
        return subprocess.run(
            args,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )


class OskPaletteSource(Protocol):
    async def resolve(self) -> OskPalette: ...


class OmarchyOskPaletteSource:
    """Resolve through `omarchy theme color`, degrading to `colors.toml` and then a default."""

    def __init__(
        self,
        runner: PaletteCommandRunner | None = None,
        *,
        colors_path: Path | None = None,
        timeout: float = 2.0,
    ) -> None:
        self._runner = runner or SubprocessPaletteRunner()
        self._colors_path = colors_path or DEFAULT_COLORS_PATH
        self._timeout = timeout

    def resolve_sync(self) -> OskPalette:
        return derive_osk_palette(self._read_palette())

    async def resolve(self) -> OskPalette:
        return await asyncio.to_thread(self.resolve_sync)

    def _read_palette(self) -> Mapping[str, str]:
        for source in (self._from_command, self._from_colors_file):
            try:
                palette = source()
            except Exception:
                continue
            if _color(palette, "background", "color0"):
                return palette
        return FALLBACK_PALETTE

    def _from_command(self) -> Mapping[str, str]:
        result = self._runner.run(["omarchy", "theme", "color", "--all"], self._timeout)
        if result.returncode != 0:
            return {}
        palette: dict[str, str] = {}
        for line in (result.stdout or "").splitlines():
            key, tab, value = line.partition("\t")
            if tab:
                palette[key.strip()] = value.strip()
        return palette

    def _from_colors_file(self) -> Mapping[str, str]:
        return parse_colors_toml(self._colors_path.read_text())
