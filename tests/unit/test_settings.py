import fcntl
import multiprocessing
import tomllib
from pathlib import Path

import pytest

from yoga_deck.adapters.user_settings import (
    LEGACY_PEN_FILENAME,
    OVERRIDES_FILENAME,
    SETTINGS_FILENAME,
    default_settings_path,
    load_settings,
    write_override,
)
from yoga_deck.adapters.wvkbd import WVKBD_SIZING_ARGS
from yoga_deck.core import PenAction
from yoga_deck.core.settings import (
    MAX_CORNER_RADIUS,
    MAX_FONT_LENGTH,
    MAX_OSK_HEIGHT,
    MAX_OSK_OPACITY,
    MIN_OSK_HEIGHT,
    MIN_OSK_OPACITY,
    SECTIONS,
    SETTING_NAMES,
    SETTING_SPECS,
    OskSettings,
    Settings,
    parse_settings,
    render_document,
    settings_snapshot,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "config.toml.example"

#: The profile measured on the X390 Yoga. A user with no settings file must get exactly this.
SHIPPED_SIZING = ("-L", "280", "-H", "360", "--fn", "Sans 20")


def write(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def isolated_settings_environment(tmp_path, monkeypatch):
    # Every implicit layer (including config.local.toml) belongs to the test.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


def test_no_settings_file_leaves_every_default_in_place(tmp_path: Path) -> None:
    parsed = load_settings(
        tmp_path / SETTINGS_FILENAME, legacy_pen_path=tmp_path / LEGACY_PEN_FILENAME
    )

    assert parsed == parse_settings({})
    assert parsed.settings == Settings()
    assert parsed.rejected == ()


def test_the_shipped_sizing_profile_is_what_a_user_with_no_config_gets() -> None:
    """The constant the diagnostics tool spawns with is rendered from the same defaults."""

    assert OskSettings().to_args() == SHIPPED_SIZING
    assert WVKBD_SIZING_ARGS == SHIPPED_SIZING


def test_one_bad_key_does_not_take_the_rest_of_the_file_with_it() -> None:
    """The difference from `load_pen_action_mapping`: a typo must not withhold a keyboard."""

    parsed = parse_settings(
        {"osk": {"landscape_height": 320, "portrait_height": "tall", "font": "Sans 24"}}
    )

    assert parsed.settings.osk.landscape_height == 320
    assert parsed.settings.osk.font == "Sans 24"
    assert parsed.settings.osk.portrait_height == OskSettings().portrait_height
    assert parsed.rejected == ("osk.portrait_height",)


@pytest.mark.parametrize(
    "value",
    [
        MIN_OSK_HEIGHT - 1,
        MAX_OSK_HEIGHT + 1,
        0,
        -280,
        "280",
        280.0,
        None,
        [280],
        # `bool` subclasses `int`, so an unguarded isinstance check would read this as 1.
        True,
    ],
)
def test_unusable_heights_are_rejected_rather_than_clamped(value: object) -> None:
    parsed = parse_settings({"osk": {"landscape_height": value}})

    assert parsed.settings.osk.landscape_height == OskSettings().landscape_height
    assert parsed.rejected == ("osk.landscape_height",)


@pytest.mark.parametrize("value", [MIN_OSK_HEIGHT, MAX_OSK_HEIGHT, 320])
def test_heights_inside_the_bounds_are_accepted(value: int) -> None:
    assert parse_settings({"osk": {"portrait_height": value}}).settings.osk.portrait_height == value


def test_unset_corner_radius_puts_no_flag_on_the_command_line() -> None:
    """`unset` and `square` are different requests; only the second names a value."""

    assert "-R" not in OskSettings().to_args()
    assert parse_settings({"osk": {"corner_radius": 0}}).settings.osk.to_args()[-2:] == ("-R", "0")


@pytest.mark.parametrize("value", [-1, MAX_CORNER_RADIUS + 1])
def test_unusable_corner_radii_are_rejected(value: int) -> None:
    parsed = parse_settings({"osk": {"corner_radius": value}})

    assert parsed.settings.osk.corner_radius is None
    assert parsed.rejected == ("osk.corner_radius",)


def test_agreeing_with_wvkbds_own_defaults_says_nothing_on_the_command_line() -> None:
    """`--no-popup` and `--no-highlight` are negative-only, so `true` must emit no flag."""

    enabled = parse_settings({"osk": {"key_popup": True, "highlight": True}})
    disabled = parse_settings({"osk": {"key_popup": False, "highlight": False}})

    assert enabled.settings.osk.to_args() == SHIPPED_SIZING
    assert disabled.settings.osk.to_args() == (*SHIPPED_SIZING, "--no-popup", "--no-highlight")


@pytest.mark.parametrize("value", ["true", 1, "yes", None])
def test_non_boolean_flags_are_rejected(value: object) -> None:
    parsed = parse_settings({"osk": {"key_popup": value}})

    assert parsed.settings.osk.key_popup is True
    assert parsed.rejected == ("osk.key_popup",)


@pytest.mark.parametrize("value", ["", "   ", 20, None])
def test_an_empty_or_non_string_font_is_rejected(value: object) -> None:
    parsed = parse_settings({"osk": {"font": value}})

    assert parsed.settings.osk.font == OskSettings().font
    assert parsed.rejected == ("osk.font",)


@pytest.mark.parametrize("value", ["Sans\x00 20", "Sans\n20", "Sans\x7f 20"])
def test_a_font_with_process_unsafe_control_characters_is_rejected(value: str) -> None:
    parsed = parse_settings({"osk": {"font": value}})

    assert parsed.settings.osk.font == OskSettings().font
    assert parsed.rejected == ("osk.font",)


def test_a_valid_toml_nul_escape_cannot_reach_the_process_arguments() -> None:
    document = tomllib.loads('[osk]\nfont = "\\u0000"\n')

    parsed = parse_settings(document)

    assert parsed.settings.osk.font == OskSettings().font
    assert "\x00" not in parsed.settings.osk.to_args()
    assert parsed.rejected == ("osk.font",)


def test_an_unbounded_font_description_is_rejected() -> None:
    parsed = parse_settings({"osk": {"font": "x" * (MAX_FONT_LENGTH + 1)}})

    assert parsed.settings.osk.font == OskSettings().font
    assert parsed.rejected == ("osk.font",)


def test_unknown_sections_and_keys_are_reported_so_a_typo_is_visible() -> None:
    parsed = parse_settings({"osk": {"hieght": 320}, "keyboard": {}, "font": "Sans 24"})

    assert parsed.settings == Settings()
    assert parsed.rejected == ("font", "keyboard", "osk.hieght")


def test_a_section_that_is_not_a_table_does_not_take_the_other_section_with_it() -> None:
    parsed = parse_settings({"osk": "Sans 24", "pen": {"far_button_action": "capture_text"}})

    assert parsed.settings.osk == OskSettings()
    assert parsed.settings.pen.far_button_action is PenAction.CAPTURE_TEXT
    assert parsed.rejected == ("osk",)


@pytest.mark.parametrize("payload", [None, "not a table", 7, ["osk"]])
def test_a_document_that_is_not_a_table_still_yields_defaults(payload: object) -> None:
    parsed = parse_settings(payload)

    assert parsed.settings == Settings()
    assert parsed.rejected == ("<document>",)


def test_the_legacy_pen_file_still_binds_the_button(tmp_path: Path) -> None:
    legacy = write(tmp_path, LEGACY_PEN_FILENAME, 'far_button_action = "open_scratchpad"\n')

    parsed = load_settings(tmp_path / SETTINGS_FILENAME, legacy_pen_path=legacy)

    assert parsed.settings.pen.far_button_action is PenAction.OPEN_SCRATCHPAD
    assert parsed.rejected == ()


def test_config_toml_wins_over_the_legacy_pen_file(tmp_path: Path) -> None:
    legacy = write(tmp_path, LEGACY_PEN_FILENAME, 'far_button_action = "open_scratchpad"\n')
    settings = write(tmp_path, SETTINGS_FILENAME, '[pen]\nfar_button_action = "capture_text"\n')

    parsed = load_settings(settings, legacy_pen_path=legacy)

    assert parsed.settings.pen.far_button_action is PenAction.CAPTURE_TEXT


def test_a_broken_settings_file_still_lets_the_legacy_pen_file_apply(tmp_path: Path) -> None:
    """Neither file may be able to discard the other; only the broken one is reported."""

    legacy = write(tmp_path, LEGACY_PEN_FILENAME, 'far_button_action = "capture_text"\n')
    settings = write(tmp_path, SETTINGS_FILENAME, "this is not toml [[[\n")

    parsed = load_settings(settings, legacy_pen_path=legacy)

    assert parsed.settings.pen.far_button_action is PenAction.CAPTURE_TEXT
    assert parsed.rejected == (SETTINGS_FILENAME,)


def test_a_broken_legacy_file_no_longer_raises(tmp_path: Path) -> None:
    """`load_pen_action_mapping` raises here. Through the settings loader it degrades instead."""

    legacy = write(tmp_path, LEGACY_PEN_FILENAME, "far_button_action = \n")
    settings = write(tmp_path, SETTINGS_FILENAME, "[osk]\nfont = 'Sans 24'\n")

    parsed = load_settings(settings, legacy_pen_path=legacy)

    assert parsed.settings.osk.font == "Sans 24"
    assert parsed.settings.pen.far_button_action is None
    assert parsed.rejected == (LEGACY_PEN_FILENAME,)


def test_a_rejected_file_is_named_without_its_path(tmp_path: Path) -> None:
    """Diagnostics must omit usernames in paths; a bare file name still identifies the file."""

    settings = write(tmp_path, SETTINGS_FILENAME, "broken [[[\n")

    parsed = load_settings(settings, legacy_pen_path=tmp_path / LEGACY_PEN_FILENAME)

    assert parsed.rejected == (SETTINGS_FILENAME,)
    assert not any(str(tmp_path) in name for name in parsed.rejected)


def test_a_directory_where_the_settings_file_should_be_degrades_to_defaults(tmp_path: Path) -> None:
    (tmp_path / SETTINGS_FILENAME).mkdir()

    parsed = load_settings(
        tmp_path / SETTINGS_FILENAME, legacy_pen_path=tmp_path / LEGACY_PEN_FILENAME
    )

    assert parsed.settings == Settings()
    assert parsed.rejected == (SETTINGS_FILENAME,)


def test_the_path_follows_xdg_config_home() -> None:
    assert default_settings_path({"XDG_CONFIG_HOME": "/x/cfg"}) == Path(
        "/x/cfg/yoga-deck/config.toml"
    )
    assert default_settings_path({}).parts[-3:] == (".config", "yoga-deck", "config.toml")


def test_the_shipped_example_is_valid_and_documents_the_real_defaults() -> None:
    """Every line in the example is commented, so it must parse to exactly the defaults."""

    parsed = parse_settings(tomllib.loads(EXAMPLE.read_text(encoding="utf-8")))

    assert parsed.settings == Settings()
    assert parsed.rejected == ()


def test_the_example_names_every_setting_that_exists() -> None:
    """A new setting that never reaches the example file is a setting nobody can discover."""

    body = EXAMPLE.read_text(encoding="utf-8")

    for section, fields in (("osk", OskSettings), ("pen", type(Settings().pen))):
        assert f"[{section}]" in body, section
        for name in fields.__dataclass_fields__:
            assert name in body, f"{section}.{name}"


# --- The spec table, which the loader, the CLI, and later the panel all read from ---


def test_every_setting_has_exactly_one_spec_and_every_spec_a_setting() -> None:
    declared = {
        f"{section}.{field}"
        for section in SECTIONS
        for field in type(getattr(Settings(), section)).__dataclass_fields__
    }

    assert set(SETTING_NAMES) == declared
    assert len(SETTING_NAMES) == len(SETTING_SPECS)


def test_each_spec_reports_the_dataclass_default_rather_than_a_copy_of_it() -> None:
    for spec in SETTING_SPECS:
        assert spec.default == getattr(getattr(Settings(), spec.section), spec.field)


@pytest.mark.parametrize("spec", SETTING_SPECS, ids=lambda spec: spec.name)
def test_every_spec_accepts_its_own_default_back(spec) -> None:
    # A default the loader would reject would make an unedited file behave unlike no file.
    if spec.default is not None:
        assert spec.validate(spec.to_toml(spec.default)) == spec.default


# --- Layering: three files, lowest precedence first ---


def _write(root: Path, name: str, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(body)


def test_the_overrides_file_wins_over_the_hand_written_one(tmp_path: Path) -> None:
    root = tmp_path / "yoga-deck"
    _write(root, SETTINGS_FILENAME, "[osk]\nlandscape_height = 320\nfont = 'Sans 22'\n")
    _write(root, OVERRIDES_FILENAME, "[osk]\nlandscape_height = 400\n")

    parsed = load_settings(environment={"XDG_CONFIG_HOME": str(tmp_path)})

    assert parsed.settings.osk.landscape_height == 400
    # A key the overrides file does not mention is left entirely to the hand-written one.
    assert parsed.settings.osk.font == "Sans 22"


def test_an_unusable_override_does_not_take_the_hand_written_value_with_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "yoga-deck"
    _write(root, SETTINGS_FILENAME, "[osk]\nlandscape_height = 320\n")
    _write(root, OVERRIDES_FILENAME, "[osk]\nlandscape_height = 5000\n")

    parsed = load_settings(environment={"XDG_CONFIG_HOME": str(tmp_path)})

    # Each layer is validated before it is folded, so the top layer can only lose its own key.
    assert parsed.settings.osk.landscape_height == 320
    assert parsed.rejected == ("osk.landscape_height",)
    assert parsed.problems == ((OVERRIDES_FILENAME, "osk.landscape_height"),)


def test_origins_name_the_file_that_set_each_value(tmp_path: Path) -> None:
    root = tmp_path / "yoga-deck"
    _write(root, LEGACY_PEN_FILENAME, "far_button_action = 'capture_text'\n")
    _write(root, SETTINGS_FILENAME, "[osk]\nfont = 'Sans 22'\n")
    _write(root, OVERRIDES_FILENAME, "[osk]\nlandscape_height = 400\n")

    origins = dict(load_settings(environment={"XDG_CONFIG_HOME": str(tmp_path)}).origins)

    assert origins == {
        "osk.font": SETTINGS_FILENAME,
        "osk.landscape_height": OVERRIDES_FILENAME,
        "pen.far_button_action": LEGACY_PEN_FILENAME,
    }


def test_a_setting_no_file_mentions_has_no_origin(tmp_path: Path) -> None:
    parsed = load_settings(environment={"XDG_CONFIG_HOME": str(tmp_path)})

    assert parsed.origins == ()


def test_an_unreadable_file_is_named_in_problems_without_its_path(tmp_path: Path) -> None:
    root = tmp_path / "yoga-deck"
    _write(root, OVERRIDES_FILENAME, "not toml [[[\n")

    parsed = load_settings(environment={"XDG_CONFIG_HOME": str(tmp_path)})

    assert parsed.problems == ((OVERRIDES_FILENAME, OVERRIDES_FILENAME),)
    assert str(tmp_path) not in repr(parsed.problems)


# --- The writer, which exists so the project needs no TOML-writing dependency ---


def test_the_writer_emits_only_keys_the_table_knows(tmp_path: Path) -> None:
    body = render_document({"osk": {"font": "Sans 22", "hieght": 400}, "nonsense": {"a": 1}})

    assert tomllib.loads(body) == {"osk": {"font": "Sans 22"}}


def test_the_writer_drops_a_value_the_loader_would_reject(tmp_path: Path) -> None:
    # The machine file is rewritten from scratch each time, so this is where junk stops.
    body = render_document({"osk": {"landscape_height": 5000, "font": "Sans 22"}})

    assert tomllib.loads(body) == {"osk": {"font": "Sans 22"}}


def test_the_writer_keeps_a_header_readable_and_out_of_the_data() -> None:
    body = render_document({"osk": {"font": "Sans 22"}}, header="# written by a machine\n")

    assert body.startswith("# written by a machine\n")
    assert tomllib.loads(body) == {"osk": {"font": "Sans 22"}}


def test_an_empty_document_renders_to_nothing_at_all() -> None:
    assert render_document({"osk": {}}, header="# header\n") == ""


def test_a_font_name_with_quotes_survives_the_round_trip() -> None:
    awkward = 'Sans "Display" 20\\x'

    body = render_document({"osk": {"font": awkward}})

    assert tomllib.loads(body)["osk"]["font"] == awkward


def _write_font_override(path: str, finished) -> None:
    write_override("osk.font", "Sans 24", path=Path(path))
    finished.set()


def test_override_read_modify_replace_is_serialized_across_processes(tmp_path: Path) -> None:
    """A panel write cannot erase a disjoint CLI write made from a newer snapshot."""

    target = tmp_path / OVERRIDES_FILENAME
    lock_path = target.with_name(f".{target.name}.lock")
    context = multiprocessing.get_context("fork")
    finished = context.Event()

    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        writer = context.Process(target=_write_font_override, args=(str(target), finished))
        writer.start()
        try:
            # The child must wait before reading. While holding the transaction lock, model
            # another client committing a disjoint key from a newer snapshot.
            assert not finished.wait(0.25)
            target.write_text("[osk]\nlandscape_height = 400\n", encoding="utf-8")
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

    writer.join(timeout=3)
    if writer.is_alive():
        writer.terminate()
        writer.join()
        pytest.fail("settings writer did not leave the transaction lock")

    assert writer.exitcode == 0
    assert tomllib.loads(target.read_text(encoding="utf-8"))["osk"] == {
        "landscape_height": 400,
        "font": "Sans 24",
    }


def test_the_snapshot_describes_every_setting_and_invents_none() -> None:
    snapshot = settings_snapshot(parse_settings({}))

    assert [entry["name"] for entry in snapshot["settings"]] == list(SETTING_NAMES)


def test_the_snapshot_carries_only_scalars_a_transport_can_send() -> None:
    """A client reads this over a socket, so an enum or a dataclass would not survive it."""

    parsed = parse_settings({"pen": {"far_button_action": "capture_text"}})

    entries = settings_snapshot(parsed)["settings"]

    values = [entry["value"] for entry in entries] + [entry["default"] for entry in entries]
    assert all(value is None or isinstance(value, (bool, int, str)) for value in values)
    action = next(e for e in entries if e["name"] == "pen.far_button_action")
    assert action["value"] == "capture_text"
    assert action["choices"] == ["capture_text", "open_scratchpad"]


def test_the_snapshot_names_the_file_behind_each_value_and_leaves_defaults_unattributed(
    tmp_path: Path,
) -> None:
    write(tmp_path, SETTINGS_FILENAME, "[osk]\nlandscape_height = 300\n")
    write(tmp_path, OVERRIDES_FILENAME, "[osk]\nportrait_height = 500\n")

    entries = {
        entry["name"]: entry
        for entry in settings_snapshot(
            load_settings(
                tmp_path / SETTINGS_FILENAME,
                overrides_path=tmp_path / OVERRIDES_FILENAME,
                legacy_pen_path=tmp_path / LEGACY_PEN_FILENAME,
            )
        )["settings"]
    }

    assert entries["osk.landscape_height"]["source"] == SETTINGS_FILENAME
    assert entries["osk.portrait_height"]["source"] == OVERRIDES_FILENAME
    # Nothing set it, so there is no file to name. A client shows a default, not a choice.
    assert entries["osk.font"]["source"] is None


def test_the_snapshot_reports_what_a_file_asked_for_and_did_not_get(tmp_path: Path) -> None:
    write(tmp_path, SETTINGS_FILENAME, "[osk]\nhieght = 300\n")

    snapshot = settings_snapshot(
        load_settings(
            tmp_path / SETTINGS_FILENAME,
            overrides_path=tmp_path / OVERRIDES_FILENAME,
            legacy_pen_path=tmp_path / LEGACY_PEN_FILENAME,
        )
    )

    assert snapshot["ignored"] == [{"source": SETTINGS_FILENAME, "name": "osk.hieght"}]


@pytest.mark.parametrize(
    "spec",
    [spec for spec in SETTING_SPECS if spec.kind == "integer"],
    ids=lambda spec: spec.name,
)
def test_a_control_sweeps_a_range_the_file_would_accept(spec) -> None:
    """A narrowed slider span is a nicer sweep, never a second set of rules."""

    low, high = spec.slider_bounds

    assert spec.minimum <= low < high <= spec.maximum
    assert spec.validate(low) == low
    assert spec.validate(high) == high


def test_the_default_of_every_setting_sits_inside_the_span_a_control_sweeps() -> None:
    """Otherwise a slider would open pinned to one end, showing a value nobody chose."""

    for spec in SETTING_SPECS:
        if spec.kind != "integer" or spec.default is None:
            continue
        low, high = spec.slider_bounds
        assert low <= spec.default <= high


def test_opacity_puts_no_flag_on_the_command_line() -> None:
    """It is carried in the palette's own colour arguments; wvkbd has no opacity flag."""

    assert parse_settings({"osk": {"opacity": 50}}).settings.osk.to_args() == SHIPPED_SIZING


@pytest.mark.parametrize("value", [MIN_OSK_OPACITY - 1, MAX_OSK_OPACITY + 1, 0, -20, 255])
def test_opacities_outside_the_accepted_range_are_rejected(value: int) -> None:
    """Zero included: an invisible keyboard still holds its exclusive zone and eats every tap."""

    parsed = parse_settings({"osk": {"opacity": value}})

    assert parsed.settings.osk.opacity == OskSettings().opacity
    assert parsed.rejected == ("osk.opacity",)


def test_a_user_with_no_config_gets_a_solid_keyboard() -> None:
    assert OskSettings().opacity == MAX_OSK_OPACITY
