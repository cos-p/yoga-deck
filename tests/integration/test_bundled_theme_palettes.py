"""Every Omarchy theme installed on this host, through the real palette cascade.

The derivation's floors are asserted as properties over generated palettes elsewhere. This is
the other half: the themes people actually switch to, resolved by `omarchy theme color` rather
than by a fixture, so a change to Omarchy's cascade or to the bundled set is caught here rather
than on the device. It is the regression guard for the tint work -- a derivation that quietly
went back to bleaching key faces would still pass every contrast floor.
"""

import subprocess
from pathlib import Path
from shutil import which

import pytest

from yoga_deck.adapters.omarchy_theme import (
    ACCENT_ON_KEY,
    BOARD_TINT_FLOOR,
    KEY_ON_SPECIAL,
    SPECIAL_ON_BOARD,
    TEXT_FLOOR,
    chroma,
    contrast_ratio,
    derive_osk_palette,
    to_hsl,
)

pytestmark = pytest.mark.integration

THEME_ROOT = Path("/usr/share/omarchy/themes")


def bundled_themes() -> list[str]:
    if which("omarchy") is None or not THEME_ROOT.is_dir():
        return []
    return sorted(theme.name for theme in THEME_ROOT.iterdir() if (theme / "colors.toml").is_file())


THEMES = bundled_themes()


@pytest.fixture(scope="module", params=THEMES)
def theme_palette(request):
    """One theme resolved through the same cascade every other Omarchy consumer reads."""

    resolved = subprocess.run(
        [
            "omarchy",
            "theme",
            "color",
            "--all",
            "--file",
            str(THEME_ROOT / request.param / "colors.toml"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if resolved.returncode != 0:
        pytest.skip(f"omarchy theme color failed for {request.param}")
    palette = {}
    for line in resolved.stdout.splitlines():
        key, tab, value = line.partition("\t")
        if tab:
            palette[key.strip()] = value.strip()
    return request.param, palette


@pytest.mark.skipif(not THEMES, reason="no bundled Omarchy themes on this host")
def test_every_bundled_theme_clears_every_contrast_floor(theme_palette) -> None:
    name, source = theme_palette
    palette = derive_osk_palette(source)

    floors = [
        ("key label on key face", palette.text, palette.fg, TEXT_FLOOR),
        ("special label on special face", palette.text_sp, palette.fg_sp, TEXT_FLOOR),
        ("pressed label on pressed face", palette.text_press, palette.press, TEXT_FLOOR),
        ("pressed special label", palette.text_press_sp, palette.press_sp, TEXT_FLOOR),
        ("swipe label on swipe face", palette.text_swipe, palette.swipe, TEXT_FLOOR),
        ("swipe special label", palette.text_swipe_sp, palette.swipe_sp, TEXT_FLOOR),
        ("special face vs board", palette.fg_sp, palette.bg, SPECIAL_ON_BOARD),
        ("key face vs special face", palette.fg, palette.fg_sp, KEY_ON_SPECIAL),
        ("pressed face vs key face", palette.press, palette.fg, ACCENT_ON_KEY),
        ("pressed special vs special face", palette.press_sp, palette.fg_sp, ACCENT_ON_KEY),
        ("swipe face vs key face", palette.swipe, palette.fg, ACCENT_ON_KEY),
        ("swipe special vs special face", palette.swipe_sp, palette.fg_sp, ACCENT_ON_KEY),
    ]
    for label, first, second, floor in floors:
        ratio = contrast_ratio(first, second)
        assert ratio >= floor, f"{name}: {label} is {ratio:.2f}:1, under {floor}:1"


@pytest.mark.skipif(not THEMES, reason="no bundled Omarchy themes on this host")
def test_every_bundled_theme_reaches_the_keyboard_as_more_than_light_or_dark(theme_palette) -> None:
    """A theme with a color anywhere must put one on the resting keyboard.

    Before the ladder was built in the theme's own hue, seven of the bundled themes produced a
    literally achromatic keyboard, and the accent appeared only while a key was held down.
    """

    name, source = theme_palette
    palette = derive_osk_palette(source)
    # Mirrors the derivation: a board under the floor is too faint to have a usable hue, so the
    # keyboard borrows the accent's. `last-horizon`'s `#0c0b0c` spans one level out of 255.
    board_has_hue = chroma(palette.bg) >= BOARD_TINT_FLOOR
    accent_has_hue = chroma(palette.press) >= BOARD_TINT_FLOOR

    if not (board_has_hue or accent_has_hue):
        pytest.skip(f"{name} is achromatic by design")

    assert chroma(palette.fg) > 0, f"{name}: key faces came out grey"
    hue_source = palette.bg if board_has_hue else palette.press
    gap = abs(to_hsl(palette.fg)[0] - to_hsl(hue_source)[0]) % 1.0
    assert min(gap, 1.0 - gap) * 360 < 8, f"{name}: key faces drifted off the theme's hue"
