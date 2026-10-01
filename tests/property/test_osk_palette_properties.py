"""The contrast floors in docs/osk-theming.md must hold for any palette, not just bundled ones."""

from hypothesis import assume, given
from hypothesis import strategies as st

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
from yoga_deck.core.settings import MAX_OSK_OPACITY, MIN_OSK_OPACITY

hex_colors = st.integers(min_value=0, max_value=0xFFFFFF).map(lambda value: f"#{value:06x}")

# Anything a third-party theme might put in a colors.toml, including values Omarchy permits
# that are not plain hex literals.
palette_values = st.one_of(
    hex_colors,
    st.sampled_from(
        ["", "rgb(1,2,3)", "rgba(125,130,217,0.5)", "#7d82d9 #252e56 45deg", "0x0060B1E", "none"]
    ),
    st.text(alphabet=st.characters(codec="ascii", exclude_categories=("Cc",)), max_size=8),
)

PALETTE_KEYS = [
    "background",
    "foreground",
    "bright_foreground",
    "light_foreground",
    "accent",
    "blue",
    "selection",
    "color0",
    "color7",
    "color15",
    "mode",
]

palettes = st.dictionaries(
    st.sampled_from(PALETTE_KEYS), palette_values, max_size=len(PALETTE_KEYS)
)


@given(palettes)
def test_any_palette_yields_legible_labels_and_separated_surfaces(palette) -> None:
    derived = derive_osk_palette(palette)

    assert contrast_ratio(derived.text, derived.fg) >= TEXT_FLOOR
    assert contrast_ratio(derived.text_sp, derived.fg_sp) >= TEXT_FLOOR
    assert contrast_ratio(derived.text_press, derived.press) >= TEXT_FLOOR
    assert contrast_ratio(derived.text_swipe, derived.swipe) >= TEXT_FLOOR
    assert contrast_ratio(derived.fg_sp, derived.bg) >= SPECIAL_ON_BOARD
    assert contrast_ratio(derived.fg, derived.fg_sp) >= KEY_ON_SPECIAL
    assert contrast_ratio(derived.text_press_sp, derived.press_sp) >= TEXT_FLOOR
    assert contrast_ratio(derived.text_swipe_sp, derived.swipe_sp) >= TEXT_FLOOR
    assert contrast_ratio(derived.press, derived.fg) >= ACCENT_ON_KEY
    assert contrast_ratio(derived.swipe, derived.fg) >= ACCENT_ON_KEY
    assert contrast_ratio(derived.press_sp, derived.fg_sp) >= ACCENT_ON_KEY
    assert contrast_ratio(derived.swipe_sp, derived.fg_sp) >= ACCENT_ON_KEY


@given(palettes)
def test_any_palette_produces_a_well_formed_argument_vector(palette) -> None:
    args = derive_osk_palette(palette).to_args()

    assert len(args) == 26
    assert all(flag.startswith("--") for flag in args[::2])
    assert all(len(value) == 6 and set(value) <= set("0123456789abcdef") for value in args[1::2])


@given(palettes, st.integers(min_value=MIN_OSK_OPACITY, max_value=MAX_OSK_OPACITY))
def test_any_opacity_still_produces_a_well_formed_argument_vector(palette, opacity: int) -> None:
    """Whatever a user asks for, wvkbd must be handed hex it can parse.

    wvkbd's own parser silently ignores anything that is not six or eight characters, so a
    malformed value here would not fail loudly -- it would leave one role at its compiled-in
    default and let a keyboard render with a single wrong colour.
    """

    args = derive_osk_palette(palette).to_args(opacity)

    assert len(args) == 26
    assert all(flag.startswith("--") for flag in args[::2])
    for value in args[1::2]:
        assert len(value) in (6, 8)
        assert set(value) <= set("0123456789abcdef")


@given(st.dictionaries(st.sampled_from(PALETTE_KEYS), hex_colors, max_size=4))
def test_derivation_is_deterministic(palette) -> None:
    assert derive_osk_palette(palette) == derive_osk_palette(dict(palette))


@given(st.integers(min_value=0, max_value=255))
def test_a_theme_with_no_hue_anywhere_yields_no_hue_on_the_keyboard(level: int) -> None:
    """Tint is inherited, never invented. A grey theme must not get a colored keyboard."""

    grey = f"#{level:02x}{level:02x}{level:02x}"
    derived = derive_osk_palette({"background": grey, "accent": grey, "foreground": grey})

    assert chroma(derived.fg_sp) == 0
    assert chroma(derived.fg) == 0


@given(hex_colors, hex_colors)
def test_surfaces_hold_the_hue_of_a_board_that_has_one(board: str, accent: str) -> None:
    """The whole point of the ladder: lifting a key face must not drift off the theme's hue."""

    assume(chroma(board) >= BOARD_TINT_FLOOR)
    derived = derive_osk_palette({"background": board, "accent": accent})

    for surface in (derived.fg_sp, derived.fg):
        # A surface driven to the gamut's black or white endpoint has no hue left to compare.
        assume(chroma(surface) > 0)
        gap = abs(to_hsl(surface)[0] - to_hsl(board)[0]) % 1.0
        assert min(gap, 1.0 - gap) * 360 < 8
