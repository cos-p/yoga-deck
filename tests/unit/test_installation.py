"""Setup and uninstall planning against a temporary home and fake command runners."""

import json
import zipfile
from pathlib import Path

import pytest

from yoga_deck import installation as inst
from yoga_deck.cli import main

_ROOT = Path(__file__).parents[2]
_ALL_COMMANDS = frozenset(
    {"systemctl", "omarchy", "hyprctl", "python3", "monitor-sensor", "wvkbd-mobintl"}
)


class FakeRunner:
    def __init__(self, log: list[tuple[str, ...]], failing: set[tuple[str, ...]] = frozenset()):
        self.log = log
        self.failing = failing

    def __call__(self, argv):
        argv = tuple(argv)
        self.log.append(argv)
        if argv[:2] == ("omarchy", "hook") and argv[2] == "install":
            # Mirror omarchy-hook-install: copy into ~/.config/omarchy/hooks/<type>.d/.
            source = Path(argv[4])
            dest = self.home / ".config/omarchy/hooks" / f"{argv[3]}.d" / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read_bytes())
        return 1 if argv in self.failing else 0


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    for name in inst.RUNTIME_BINARIES:
        binary = bin_dir / name
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
    return home


def _observer(commands=_ALL_COMMANDS, input_readable=True):
    def observe(paths):
        return inst.observe_host(
            paths,
            which=lambda name: f"/usr/bin/{name}" if name in commands else None,
            input_probe=lambda: input_readable,
        )

    return observe


def _run(argv, home, log, *, observer=None, recover_log=None, failing=frozenset()):
    runner = FakeRunner(log, set(failing))
    runner.home = home

    class Recovery:
        def recover_inputs(self):
            (recover_log if recover_log is not None else log).append(("recover",))

    return main(
        argv,
        install_environment={"HOME": str(home)},
        install_runner=runner,
        install_observer=observer or _observer(),
        input_recovery=Recovery(),
        runtime_requester=lambda _: None,
    )


def _snapshot(root: Path) -> dict[str, bytes | str]:
    result: dict[str, bytes | str] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            result[key] = f"-> {path.readlink()}"
        elif path.is_file():
            result[key] = path.read_bytes()
        else:
            result[key] = "dir"
    return result


def test_setup_installs_unit_and_plugin_copy_without_enabling(home, capsys) -> None:
    log: list[tuple[str, ...]] = []

    assert _run(["setup"], home, log) == 0

    unit = home / ".config/systemd/user/yoga-deck.service"
    plugin = home / ".config/omarchy/plugins/cos.yoga-deck"
    assert unit.read_bytes() == (_ROOT / "systemd/yoga-deck.service").read_bytes()
    assert not plugin.is_symlink()
    assert sorted(p.name for p in plugin.iterdir()) == sorted(inst.PLUGIN_FILES)
    for name in inst.PLUGIN_FILES:
        assert (plugin / name).read_bytes() == (_ROOT / "omarchy-plugin" / name).read_bytes()
    assert ("systemctl", "--user", "daemon-reload") in log
    assert ("omarchy", "plugin", "validate", str(plugin)) in log
    assert not any("enable" in argv for argv in log)
    assert not any(argv[:2] == ("omarchy", "hook") for argv in log)
    assert not (home / ".config/omarchy/hooks").exists()
    assert "systemctl --user enable --now yoga-deck.service" in capsys.readouterr().out


def test_setup_is_idempotent_and_upgrades_a_stale_copy(home) -> None:
    log: list[tuple[str, ...]] = []
    assert _run(["setup", "--with-theme-hook"], home, log) == 0
    first = _snapshot(home)

    log.clear()
    assert _run(["setup", "--with-theme-hook"], home, log) == 0
    assert _snapshot(home) == first
    # Only read-only validation runs again; nothing is rewritten or reloaded.
    assert [argv[:3] for argv in log] == [("omarchy", "plugin", "validate")]

    panel = home / ".config/omarchy/plugins/cos.yoga-deck/Panel.qml"
    panel.write_text("// stale")
    (panel.parent / "__pycache__").mkdir()
    assert _run(["setup", "--with-theme-hook"], home, log) == 0
    assert _snapshot(home) == first


def test_dry_run_changes_nothing_and_runs_nothing(home, capsys) -> None:
    log: list[tuple[str, ...]] = []
    before = _snapshot(home)

    assert _run(["setup", "--dry-run", "--with-theme-hook", "--enable"], home, log) == 0
    assert _snapshot(home) == before
    assert log == []
    out = capsys.readouterr().out
    assert "copy Omarchy plugin" in out
    assert "run systemctl --user enable yoga-deck.service" in out

    assert _run(["setup"], home, log) == 0
    installed = _snapshot(home)
    log.clear()
    assert _run(["uninstall", "--dry-run", "--purge"], home, log) == 0
    assert _snapshot(home) == installed
    assert log == []


def test_enable_flags_are_explicit(home) -> None:
    log: list[tuple[str, ...]] = []

    assert _run(["setup", "--enable", "--enable-plugin"], home, log) == 0

    assert ("systemctl", "--user", "enable", "yoga-deck.service") in log
    assert ("systemctl", "--user", "restart", "yoga-deck.service") in log
    assert ("omarchy", "plugin", "enable", "cos.yoga-deck") in log
    # The service starts only after its unit and the plugin are in place.
    assert log.index(("systemctl", "--user", "daemon-reload")) < log.index(
        ("systemctl", "--user", "restart", "yoga-deck.service")
    )


def test_setup_blocks_on_missing_required_prerequisites(home, capsys) -> None:
    log: list[tuple[str, ...]] = []
    before = _snapshot(home)
    observer = _observer(commands=_ALL_COMMANDS - {"omarchy"}, input_readable=False)

    assert _run(["setup"], home, log, observer=observer) == 1

    assert _snapshot(home) == before
    assert log == []
    out = capsys.readouterr().out
    assert "omarchy: MISSING" in out
    assert "input device access: missing" in out


def test_setup_refuses_foreign_plugin_directory(home) -> None:
    foreign = home / ".config/omarchy/plugins/cos.yoga-deck"
    foreign.mkdir(parents=True)
    (foreign / "manifest.json").write_text(json.dumps({"id": "someone.else"}))
    log: list[tuple[str, ...]] = []

    assert _run(["setup"], home, log) == 1

    assert json.loads((foreign / "manifest.json").read_text())["id"] == "someone.else"
    assert log == []


def test_dev_link_symlinks_checkout_and_validates_target(home) -> None:
    log: list[tuple[str, ...]] = []
    assert _run(["setup"], home, log) == 0
    plugin = home / ".config/omarchy/plugins/cos.yoga-deck"

    log.clear()
    assert _run(["setup", "--dev-link"], home, log) == 0

    assert plugin.is_symlink()
    assert plugin.resolve() == (_ROOT / "omarchy-plugin").resolve()
    assert ("omarchy", "plugin", "validate", str((_ROOT / "omarchy-plugin").resolve())) in log

    # Uninstall removes only the link, never the checkout it points at.
    assert _run(["uninstall"], home, log) == 0
    assert not plugin.exists() and not plugin.is_symlink()
    assert (_ROOT / "omarchy-plugin" / "manifest.json").is_file()


def test_uninstall_recovers_inputs_before_touching_the_service(home, capsys) -> None:
    log: list[tuple[str, ...]] = []
    assert _run(["setup", "--with-theme-hook"], home, log) == 0
    settings = home / ".config/yoga-deck"
    settings.mkdir()
    (settings / "config.toml").write_text("")
    log.clear()

    assert _run(["uninstall"], home, log) == 0

    assert log[0] == ("recover",)
    stop = ("systemctl", "--user", "disable", "--now", "yoga-deck.service")
    assert stop in log
    assert log.index(("recover",)) < log.index(stop)
    assert not (home / ".config/systemd/user/yoga-deck.service").exists()
    assert not (home / ".config/omarchy/plugins/cos.yoga-deck").exists()
    assert not (home / ".config/omarchy/hooks/theme-set.d/yoga-deck").exists()
    assert (settings / "config.toml").exists()
    assert "uv tool uninstall yoga-deck" in capsys.readouterr().out

    assert _run(["uninstall", "--purge"], home, log) == 0
    assert not settings.exists()


def test_uninstall_refuses_plugin_directory_with_other_manifest_id(home, capsys) -> None:
    foreign = home / ".config/omarchy/plugins/cos.yoga-deck"
    foreign.mkdir(parents=True)
    (foreign / "manifest.json").write_text(json.dumps({"id": "someone.else"}))
    unit = home / ".config/systemd/user/yoga-deck.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]\n")
    log: list[tuple[str, ...]] = []

    assert _run(["uninstall"], home, log) == 1

    assert (foreign / "manifest.json").is_file()
    assert not unit.exists()
    assert ("omarchy", "plugin", "disable", "cos.yoga-deck") not in log
    assert "not the cos.yoga-deck plugin" in capsys.readouterr().err


def test_uninstall_continues_when_service_stop_fails(home) -> None:
    log: list[tuple[str, ...]] = []
    assert _run(["setup"], home, log) == 0
    stop = ("systemctl", "--user", "disable", "--now", "yoga-deck.service")

    assert _run(["uninstall"], home, log, failing={stop}) == 0
    assert not (home / ".config/systemd/user/yoga-deck.service").exists()


def test_uninstall_plan_always_starts_with_recovery() -> None:
    paths = inst.Paths(Path("/nonexistent"), Path("/nonexistent/.config"))
    empty = inst.HostState(
        commands=frozenset(),
        binaries=frozenset(),
        input_readable=False,
        unit=inst.FileState(),
        plugin=inst.PluginState(),
        hook=inst.FileState(),
        settings_dir_exists=False,
    )
    plan = inst.plan_uninstall(inst.UninstallOptions(purge=True), paths, empty)
    assert plan.actions == (inst.RecoverInputs(),)


def test_assets_match_repository_sources() -> None:
    assets = inst.load_assets()
    for name in inst.PLUGIN_FILES:
        assert assets.plugin_files[name] == (_ROOT / "omarchy-plugin" / name).read_bytes()
    assert assets.unit == (_ROOT / "systemd/yoga-deck.service").read_bytes()
    assert assets.hook == (_ROOT / "hooks/theme-set.d/yoga-deck").read_bytes()
    assert inst.HOOK_MARKER in assets.hook
    assert json.loads(assets.plugin_files["manifest.json"])["id"] == inst.PLUGIN_ID


def test_built_wheel_ships_plugin_unit_and_hook(tmp_path: Path) -> None:
    wheel_module = pytest.importorskip("hatchling.builders.wheel")
    builder = wheel_module.WheelBuilder(str(_ROOT))
    wheel = Path(next(iter(builder.build(directory=str(tmp_path), versions=["standard"]))))

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for name in inst.PLUGIN_FILES:
            member = f"yoga_deck/data/omarchy-plugin/{name}"
            assert member in names
            assert archive.read(member) == (_ROOT / "omarchy-plugin" / name).read_bytes()
        assert "yoga_deck/data/systemd/yoga-deck.service" in names
        assert "yoga_deck/data/hooks/theme-set.d/yoga-deck" in names
        assert "yoga_deck/installation.py" in names
    assert not [name for name in names if "__pycache__" in name or name.endswith(".pyc")]
    assert not [name for name in names if name.endswith(".gitkeep")]


def test_default_input_probe_reuses_the_inspect_permission_check(monkeypatch) -> None:
    monkeypatch.setattr(
        inst.LinuxDiscovery, "runtime_input_denied", lambda self: frozenset({"tablet_switch"})
    )
    assert inst._default_input_probe() is False

    monkeypatch.setattr(inst.LinuxDiscovery, "runtime_input_denied", lambda self: frozenset())
    assert inst._default_input_probe() is True
