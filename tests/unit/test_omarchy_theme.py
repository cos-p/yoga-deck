from dataclasses import dataclass
from pathlib import Path

import pytest

from yoga_deck.adapters.omarchy_theme import (
    _ROLE_FLAGS,
    _TRANSLUCENT_ROLES,
    ACCENT_ON_KEY,
    BORROWED_TINT,
    FALLBACK_PALETTE,
    KEY_ON_SPECIAL,
    SPECIAL_ON_BOARD,
    TEXT_FLOOR,
    OmarchyOskPaletteSource,
    chroma,
    contrast_ratio,
    derive_osk_palette,
    from_hsl,
    parse_colors_toml,
    to_hsl,
)

# Trimmed from the bundled themes on 2026-08-29; see docs/osk-theming.md.
ETHEREAL = {
    "mode": "dark",
    "background": "#060B1E",
    "foreground": "#ffcead",
    "bright_foreground": "#ffcead",
    "accent": "#7d82d9",
    "selection": "#252e56",
}
CATPPUCCIN_LATTE = {
    "mode": "light",
    "background": "#eff1f5",
    "foreground": "#4c4f69",
    "bright_foreground": "#4c4f69",
    "accent": "#1e66f5",
    "selection": "#dadbdd",
}
MATTE_BLACK = {
    "mode": "dark",
    "background": "#121212",
    "foreground": "#bebebe",
    "accent": "#e68e0d",
    "selection": "#2a2a2a",
}
ROSE_PINE = {
    "mode": "light",
    "background": "#faf4ed",
    "foreground": "#575279",
    "accent": "#56949f",
    "selection": "#f2e9e1",
}


def hue_gap(first: str, second: str) -> float:
    """Separation between two hues, in degrees, the short way round the wheel."""

    gap = abs(to_hsl(first)[0] - to_hsl(second)[0]) % 1.0
    return min(gap, 1.0 - gap) * 360


@dataclass
class FakeCompletedProcess:
    returncode: int = 0
    stdout: str = ""


class FakeRunner:
    def __init__(self, *, result=None, error: Exception | None = None) -> None:
        self.result = result or FakeCompletedProcess()
        self.error = error
        self.calls: list[list[str]] = []

    def run(self, args, timeout):
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return self.result


def tabbed(palette: dict[str, str]) -> str:
    return "".join(f"{key}\t{value}\n" for key, value in palette.items())


def assert_legible(palette) -> None:
    """Every floor documented in docs/osk-theming.md."""

    assert contrast_ratio(palette.text, palette.fg) >= TEXT_FLOOR
    assert contrast_ratio(palette.text_sp, palette.fg_sp) >= TEXT_FLOOR
    assert contrast_ratio(palette.text_press, palette.press) >= TEXT_FLOOR
    assert contrast_ratio(palette.text_swipe, palette.swipe) >= TEXT_FLOOR
    assert contrast_ratio(palette.fg_sp, palette.bg) >= SPECIAL_ON_BOARD
    assert contrast_ratio(palette.fg, palette.fg_sp) >= KEY_ON_SPECIAL
    assert contrast_ratio(palette.text_press_sp, palette.press_sp) >= TEXT_FLOOR
    assert contrast_ratio(palette.text_swipe_sp, palette.swipe_sp) >= TEXT_FLOOR
    assert contrast_ratio(palette.press, palette.fg) >= ACCENT_ON_KEY
    assert contrast_ratio(palette.swipe, palette.fg) >= ACCENT_ON_KEY
    assert contrast_ratio(palette.press_sp, palette.fg_sp) >= ACCENT_ON_KEY
    assert contrast_ratio(palette.swipe_sp, palette.fg_sp) >= ACCENT_ON_KEY


def test_dark_theme_lifts_key_surfaces_out_of_its_background() -> None:
    palette = derive_osk_palette(ETHEREAL)

    assert palette.bg == "#060b1e"
    assert palette.press == "#7d82d9"
    # Key faces stand proud of the board, and special keys sit between the two.
    assert contrast_ratio(palette.fg, palette.bg) > contrast_ratio(palette.fg_sp, palette.bg)
    assert_legible(palette)


def test_light_theme_darkens_key_surfaces_instead_of_lightening_them() -> None:
    palette = derive_osk_palette(CATPPUCCIN_LATTE)

    assert palette.bg == "#eff1f5"
    # The board is already near white, so separation has to move toward black.
    assert contrast_ratio(palette.fg, "#000000") < contrast_ratio(palette.bg, "#000000")
    assert_legible(palette)


def test_key_surfaces_keep_the_boards_hue_and_open_its_chroma() -> None:
    """Lifting toward pure white or black desaturates, which bleached every theme to grey."""

    palette = derive_osk_palette(ETHEREAL)

    assert hue_gap(palette.fg_sp, palette.bg) < 8
    assert hue_gap(palette.fg, palette.bg) < 8
    # Theme ladders open their chroma up as they lighten; the keyboard's has to as well, or a
    # faintly tinted board is grey again by the time it has been lifted twice.
    assert chroma(palette.fg) > chroma(palette.fg_sp) > chroma(palette.bg) > 0


def test_a_neutral_board_borrows_its_hue_from_the_accent() -> None:
    """`matte-black` is a grey board belonging to an unmistakably amber theme."""

    palette = derive_osk_palette(MATTE_BLACK)

    assert chroma(palette.bg) == 0
    assert chroma(palette.fg) >= BORROWED_TINT
    assert hue_gap(palette.fg, MATTE_BLACK["accent"]) < 8


def test_a_theme_with_no_hue_anywhere_still_produces_a_grey_keyboard() -> None:
    """Tint is inherited, never invented."""

    palette = derive_osk_palette({"background": "#000000", "accent": "#8d8d8d"})

    assert chroma(palette.fg_sp) == chroma(palette.fg) == 0


def test_special_key_labels_carry_the_accent() -> None:
    """The only place the accent reached before was a pressed state, gone the moment you lift."""

    assert derive_osk_palette(ETHEREAL).text_sp == "#7d82d9"
    assert derive_osk_palette(MATTE_BLACK).text_sp == "#e68e0d"


def test_special_key_labels_fall_back_to_the_readable_pool_when_the_accent_is_not_legible() -> None:
    """`rose-pine`'s accent on its own special keys is 1.9:1, well under the label floor."""

    palette = derive_osk_palette(ROSE_PINE)

    assert palette.text_sp != ROSE_PINE["accent"]
    assert palette.text_sp == ROSE_PINE["foreground"]
    assert_legible(palette)


def test_special_key_shades_are_no_longer_aliases_of_the_normal_key_ones() -> None:
    """wvkbd offers the variation for free; press_sp and swipe_sp used to just repeat."""

    palette = derive_osk_palette(ETHEREAL)

    assert palette.press_sp != palette.press
    assert palette.swipe_sp != palette.swipe
    # Special keys are recessed chrome in every other role, so their shades sit nearer the board.
    assert contrast_ratio(palette.press_sp, palette.bg) < contrast_ratio(palette.press, palette.bg)


def test_an_illegible_accent_is_darkened_rather_than_discarded() -> None:
    """A darkened orange is still orange. The previous derivation substituted a neutral."""

    invisible = dict(MATTE_BLACK, accent="#3e362a")
    palette = derive_osk_palette(invisible)

    assert contrast_ratio("#3e362a", palette.fg) < ACCENT_ON_KEY, "fixture no longer illegible"
    assert palette.press != "#3e362a"
    assert hue_gap(palette.press, "#3e362a") < 8
    assert chroma(palette.press) > 0
    assert_legible(palette)


@pytest.mark.parametrize(
    "color", ["#060b1e", "#eff1f5", "#121212", "#ffffff", "#000000", "#7d82d9", "#e68e0d"]
)
def test_hsl_round_trips_exactly(color: str) -> None:
    assert from_hsl(*to_hsl(color)) == color


def test_theme_foreground_is_kept_for_labels_when_it_is_legible() -> None:
    assert derive_osk_palette(ETHEREAL).text == "#ffcead"


def test_background_ladder_is_ignored_because_themes_collapse_it() -> None:
    """`flexoki-light` gives a 1.02:1 key-vs-board ratio if the ladder is trusted."""

    collapsed = dict(CATPPUCCIN_LATTE, lighter_background="#eff1f5", dark_background="#eff1f4")

    assert_legible(derive_osk_palette(collapsed))


def test_mode_contradicting_its_background_still_separates() -> None:
    assert_legible(derive_osk_palette({"mode": "dark", "background": "#ffffff"}))
    assert_legible(derive_osk_palette({"mode": "light", "background": "#000000"}))


@pytest.mark.parametrize(
    "value",
    ["rgba(125,130,217,0.5)", "rgb(125, 130, 217)", "#7d82d9 #252e56 45deg", "", "not-a-color"],
)
def test_non_hex_palette_values_never_reach_the_argument_vector(value: str) -> None:
    palette = derive_osk_palette(dict(ETHEREAL, accent=value, selection=value))

    assert value not in palette.to_args()
    assert_legible(palette)


def test_missing_background_falls_back_without_raising() -> None:
    palette = derive_osk_palette({"foreground": "#ffffff"})

    assert palette.bg == FALLBACK_PALETTE["background"]
    assert_legible(palette)


def test_legacy_ansi_names_are_accepted_for_the_required_keys() -> None:
    palette = derive_osk_palette({"color0": "#1d2021", "color7": "#ebdbb2"})

    assert palette.bg == "#1d2021"
    assert_legible(palette)


def test_arguments_are_wvkbd_flags_carrying_bare_hex() -> None:
    args = derive_osk_palette(ETHEREAL).to_args()

    assert len(args) == 26
    flags, values = args[::2], args[1::2]
    assert flags[0] == "--bg"
    assert set(flags) == {
        "--bg",
        "--fg",
        "--fg-sp",
        "--press",
        "--press-sp",
        "--swipe",
        "--swipe-sp",
        "--text",
        "--text-sp",
        "--text-press",
        "--text-press-sp",
        "--text-swipe",
        "--text-swipe-sp",
    }
    assert all(len(value) == 6 and value == value.lower() for value in values)
    assert not any(value.startswith("#") for value in values)


def test_source_resolves_through_the_shared_omarchy_cascade() -> None:
    runner = FakeRunner(result=FakeCompletedProcess(stdout=tabbed(ETHEREAL)))

    palette = OmarchyOskPaletteSource(runner).resolve_sync()

    assert runner.calls == [["omarchy", "theme", "color", "--all"]]
    assert palette.bg == "#060b1e"


@pytest.mark.parametrize(
    "runner",
    [
        FakeRunner(error=FileNotFoundError("omarchy")),
        FakeRunner(error=OSError("timed out")),
        FakeRunner(result=FakeCompletedProcess(returncode=1)),
        FakeRunner(result=FakeCompletedProcess(stdout="not tabbed output at all")),
        FakeRunner(result=FakeCompletedProcess(stdout="background\tnope\n")),
    ],
)
def test_unusable_command_degrades_to_the_colors_file(runner, tmp_path: Path) -> None:
    colors = tmp_path / "colors.toml"
    colors.write_text('mode = "dark"\nbackground = "#1d2021"\nforeground = "#ebdbb2"\n')

    palette = OmarchyOskPaletteSource(runner, colors_path=colors).resolve_sync()

    assert palette.bg == "#1d2021"
    assert_legible(palette)


def test_unusable_command_and_missing_colors_file_still_yield_a_palette(tmp_path: Path) -> None:
    runner = FakeRunner(error=FileNotFoundError("omarchy"))

    palette = OmarchyOskPaletteSource(runner, colors_path=tmp_path / "absent.toml").resolve_sync()

    assert palette.bg == FALLBACK_PALETTE["background"]
    assert_legible(palette)


def test_colors_toml_reader_ignores_comments_and_keeps_quoted_values() -> None:
    parsed = parse_colors_toml(
        '# a comment\nmode = "dark"\nbackground = "#1d2021"  # trailing\nbroken\n'
    )

    assert parsed["mode"] == "dark"
    assert parsed["background"] == "#1d2021"
    assert "broken" not in parsed


@pytest.mark.asyncio
async def test_resolve_does_not_block_the_event_loop() -> None:
    runner = FakeRunner(result=FakeCompletedProcess(stdout=tabbed(ETHEREAL)))

    palette = await OmarchyOskPaletteSource(runner).resolve()

    assert palette.bg == "#060b1e"


def test_full_opacity_renders_exactly_what_an_opaque_keyboard_has_always_been_given() -> None:
    """The default must add nothing to the command line, so nothing about it can regress."""

    palette = derive_osk_palette(ETHEREAL)

    assert palette.to_args(100) == palette.to_args()


def test_opacity_reaches_every_surface_and_no_label() -> None:
    args = derive_osk_palette(ETHEREAL).to_args(60)
    rendered = dict(zip(args[::2], args[1::2], strict=True))

    # 60% of 255. Written out rather than recomputed, so a change to the conversion is a test
    # failure rather than a test that quietly agrees with whatever the code now does.
    assert all(rendered[f"--{role}"].endswith("99") for role in _TRANSLUCENT_ROLES)
    assert all(len(rendered[f"--{role}"]) == 8 for role in _TRANSLUCENT_ROLES)
    labels = {f"--{flag}" for flag in _ROLE_FLAGS} - {f"--{role}" for role in _TRANSLUCENT_ROLES}
    assert all(len(rendered[flag]) == 6 for flag in labels)


def test_a_translucent_keyboard_still_carries_the_theme_it_derived() -> None:
    """Opacity is a suffix, never a recolor. The six colour characters must not move."""

    palette = derive_osk_palette(ETHEREAL)
    opaque = dict(zip(palette.to_args()[::2], palette.to_args()[1::2], strict=True))
    faded = dict(zip(palette.to_args(45)[::2], palette.to_args(45)[1::2], strict=True))

    assert {flag: value[:6] for flag, value in faded.items()} == opaque


@pytest.mark.parametrize(("opacity", "expected"), [(10, "1a"), (50, "80"), (99, "fc")])
def test_a_percentage_becomes_the_alpha_byte_wvkbd_reads(opacity: int, expected: str) -> None:
    args = derive_osk_palette(ETHEREAL).to_args(opacity)

    assert dict(zip(args[::2], args[1::2], strict=True))["--bg"].endswith(expected)
