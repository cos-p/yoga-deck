"""`yoga-doctor config` over a scratch config root.

Every test drives `main` with a `settings_environment` pointing at `tmp_path`, so nothing here
can read or write the developer's own `~/.config/yoga-deck`.
"""

import json
import tomllib
from pathlib import Path

import pytest

from yoga_deck.adapters.user_settings import (
    LEGACY_PEN_FILENAME,
    OVERRIDES_FILENAME,
    SETTINGS_FILENAME,
    load_settings,
)
from yoga_deck.cli import main
from yoga_deck.core.settings import SETTING_NAMES


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "yoga-deck"
    directory.mkdir()
    return directory


@pytest.fixture
def environment(tmp_path: Path) -> dict[str, str]:
    return {"XDG_CONFIG_HOME": str(tmp_path)}


def run(argv: list[str], environment: dict[str, str]) -> int:
    return main(argv, settings_environment=environment)


def test_get_lists_every_setting_with_its_source(capsys, root, environment) -> None:
    assert run(["config", "get"], environment) == 0

    output = capsys.readouterr().out
    for name in SETTING_NAMES:
        assert name in output
    assert output.count("default") == len(SETTING_NAMES)


def test_config_without_a_verb_is_the_listing(capsys, root, environment) -> None:
    assert run(["config"], environment) == 0

    assert "osk.landscape_height" in capsys.readouterr().out


def test_set_writes_the_overrides_file_and_leaves_the_hand_written_one_alone(
    capsys, root, environment
) -> None:
    hand_written = "# a comment worth keeping\n[osk]\nfont = 'Sans 22'\n"
    (root / SETTINGS_FILENAME).write_text(hand_written)

    assert run(["config", "set", "osk.landscape_height", "400"], environment) == 0

    assert (root / SETTINGS_FILENAME).read_text() == hand_written
    stored = tomllib.loads((root / OVERRIDES_FILENAME).read_text())
    assert stored == {"osk": {"landscape_height": 400}}


def test_a_stored_override_reaches_the_keyboard_arguments(root, environment) -> None:
    assert run(["config", "set", "osk.landscape_height", "400"], environment) == 0

    settings = load_settings(environment=environment).settings
    assert settings.osk.to_args()[:2] == ("-L", "400")


def test_set_says_which_hand_written_file_its_override_now_shadows(
    capsys, root, environment
) -> None:
    (root / SETTINGS_FILENAME).write_text("[osk]\nlandscape_height = 320\n")

    assert run(["config", "set", "osk.landscape_height", "400"], environment) == 0

    output = capsys.readouterr().out
    assert f"{SETTINGS_FILENAME} also sets osk.landscape_height" in output


def test_set_is_quiet_when_nothing_is_being_shadowed(capsys, root, environment) -> None:
    assert run(["config", "set", "osk.landscape_height", "400"], environment) == 0

    assert "also sets" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("osk.landscape_height", "5000"),
        ("osk.landscape_height", "10"),
        ("osk.landscape_height", "tall"),
        ("osk.landscape_height", "280.0"),
        ("osk.corner_radius", "-1"),
        ("osk.key_popup", "yes"),
        ("osk.font", "   "),
        ("pen.far_button_action", "launch_missiles"),
    ],
)
def test_an_unusable_value_is_refused_at_the_prompt_rather_than_stored(
    capsys, root, environment, name, value
) -> None:
    assert run(["config", "set", name, value], environment) == 2

    # Refused before the file was touched, so the loader never has to degrade around it.
    assert not (root / OVERRIDES_FILENAME).exists()
    assert "invalid_value" in capsys.readouterr().err


def test_a_refusal_names_what_the_setting_would_have_accepted(capsys, root, environment) -> None:
    assert run(["config", "set", "osk.landscape_height", "5000"], environment) == 2

    assert "an integer from 60 to 2000" in capsys.readouterr().err


def test_a_refusal_for_a_choice_lists_the_choices(capsys, root, environment) -> None:
    assert run(["config", "set", "pen.far_button_action", "nope"], environment) == 2

    error = capsys.readouterr().err
    assert "capture_text" in error and "open_scratchpad" in error


@pytest.mark.parametrize("verb", ["get", "set", "unset"])
def test_an_unknown_name_is_refused_and_the_known_ones_are_listed(
    capsys, root, environment, verb
) -> None:
    argv = ["config", verb, "osk.hieght"] + (["400"] if verb == "set" else [])

    assert run(argv, environment) == 2

    error = capsys.readouterr().err
    assert "unknown_setting" in error
    assert all(name in error for name in SETTING_NAMES)


@pytest.mark.parametrize(
    ("value", "stored"), [("true", True), ("false", False), ("TRUE", True), ("False", False)]
)
def test_flags_accept_the_two_words_the_settings_file_uses(
    root, environment, value, stored
) -> None:
    assert run(["config", "set", "osk.key_popup", value], environment) == 0

    assert tomllib.loads((root / OVERRIDES_FILENAME).read_text())["osk"]["key_popup"] is stored


def test_unset_hands_the_setting_back_to_the_hand_written_file(capsys, root, environment) -> None:
    (root / SETTINGS_FILENAME).write_text("[osk]\nlandscape_height = 320\n")
    assert run(["config", "set", "osk.landscape_height", "400"], environment) == 0
    capsys.readouterr()

    assert run(["config", "unset", "osk.landscape_height"], environment) == 0

    assert f"comes from {SETTINGS_FILENAME}" in capsys.readouterr().out
    assert load_settings(environment=environment).settings.osk.landscape_height == 320


def test_unsetting_the_last_override_removes_the_machine_written_file(root, environment) -> None:
    assert run(["config", "set", "osk.landscape_height", "400"], environment) == 0
    assert (root / OVERRIDES_FILENAME).exists()

    assert run(["config", "unset", "osk.landscape_height"], environment) == 0

    # An empty stub would only raise the question of what it is for.
    assert not (root / OVERRIDES_FILENAME).exists()


def test_unsetting_one_of_several_overrides_keeps_the_others(root, environment) -> None:
    assert run(["config", "set", "osk.landscape_height", "400"], environment) == 0
    assert run(["config", "set", "osk.font", "Fira Sans 24"], environment) == 0

    assert run(["config", "unset", "osk.landscape_height"], environment) == 0

    assert tomllib.loads((root / OVERRIDES_FILENAME).read_text()) == {
        "osk": {"font": "Fira Sans 24"}
    }


def test_unsetting_something_that_was_never_set_is_not_an_error(capsys, root, environment) -> None:
    assert run(["config", "unset", "osk.corner_radius"], environment) == 0

    assert "was not overridden" in capsys.readouterr().out


def test_get_names_the_file_each_effective_value_came_from(capsys, root, environment) -> None:
    (root / LEGACY_PEN_FILENAME).write_text("far_button_action = 'capture_text'\n")
    (root / SETTINGS_FILENAME).write_text("[osk]\nfont = 'Sans 22'\n")
    assert run(["config", "set", "osk.landscape_height", "400"], environment) == 0
    capsys.readouterr()

    assert run(["config", "get", "--json"], environment) == 0

    settings = json.loads(capsys.readouterr().out)["settings"]
    assert settings["osk.landscape_height"] == {"value": 400, "source": OVERRIDES_FILENAME}
    assert settings["osk.font"] == {"value": "Sans 22", "source": SETTINGS_FILENAME}
    assert settings["pen.far_button_action"]["source"] == LEGACY_PEN_FILENAME
    assert settings["osk.highlight"] == {"value": True, "source": "default"}


def test_json_keeps_each_value_typed_rather_than_rendered(capsys, root, environment) -> None:
    assert run(["config", "get", "--json"], environment) == 0

    settings = json.loads(capsys.readouterr().out)["settings"]
    assert settings["osk.portrait_height"]["value"] == 360
    assert settings["osk.key_popup"]["value"] is True
    # Unset stays absent rather than becoming the string "(unset)" a consumer would have to know.
    assert settings["osk.corner_radius"]["value"] is None


def test_get_for_one_setting_explains_what_it_accepts(capsys, root, environment) -> None:
    assert run(["config", "get", "osk.corner_radius"], environment) == 0

    output = capsys.readouterr().out
    assert "osk.corner_radius = (unset)" in output
    assert "an integer from 0 to 64" in output
    assert "From default." in output


def test_get_reports_a_key_the_loader_had_to_discard(capsys, root, environment) -> None:
    (root / SETTINGS_FILENAME).write_text("[osk]\nhieght = 400\n")

    assert run(["config", "get"], environment) == 0

    assert f"{SETTINGS_FILENAME} sets osk.hieght" in capsys.readouterr().err


def test_a_discarded_key_is_reported_in_json_too(capsys, root, environment) -> None:
    (root / SETTINGS_FILENAME).write_text("[osk]\nlandscape_height = 5000\n")

    assert run(["config", "get", "--json"], environment) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ignored"] == [{"source": SETTINGS_FILENAME, "name": "osk.landscape_height"}]


def test_path_names_all_three_files_and_whether_they_exist(capsys, root, environment) -> None:
    (root / SETTINGS_FILENAME).write_text("[osk]\n")

    assert run(["config", "path"], environment) == 0

    output = capsys.readouterr().out
    assert str(root) not in output
    assert "$XDG_CONFIG_HOME/yoga-deck/config.toml" in output
    assert f"{SETTINGS_FILENAME}  present" in output
    assert f"{OVERRIDES_FILENAME}  absent" in output
    assert f"{LEGACY_PEN_FILENAME}  absent" in output


def test_a_broken_overrides_file_can_still_be_repaired_by_set(capsys, root, environment) -> None:
    (root / OVERRIDES_FILENAME).write_text("this is not toml [[[\n")

    assert run(["config", "set", "osk.landscape_height", "400"], environment) == 0

    assert tomllib.loads((root / OVERRIDES_FILENAME).read_text()) == {
        "osk": {"landscape_height": 400}
    }


def test_set_reports_a_write_it_could_not_perform(capsys, tmp_path, environment) -> None:
    # A file where the config directory should be: the write cannot succeed and must say so.
    (tmp_path / "yoga-deck").write_text("not a directory\n")

    assert run(["config", "set", "osk.landscape_height", "400"], environment) == 1

    assert "config_write_failed" in capsys.readouterr().err


def test_setting_the_pen_action_binds_the_button(root, environment) -> None:
    assert run(["config", "set", "pen.far_button_action", "open_scratchpad"], environment) == 0

    action = load_settings(environment=environment).settings.pen.far_button_action
    assert action is not None and action.value == "open_scratchpad"
