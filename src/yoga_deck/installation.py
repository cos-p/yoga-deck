"""User-level installation and removal of the Yoga Deck service, plugin, and hook.

Planning is pure: :func:`plan_setup` and :func:`plan_uninstall` take an observed
:class:`HostState` plus the packaged :class:`Assets` and return an ordered list of actions.
Only :func:`observe_host`, :func:`load_assets`, and :func:`execute` touch the filesystem or run
commands, so the whole policy (idempotency, ownership checks, recovery ordering) is testable
without a real home directory.

Nothing here installs packages, and nothing enables or starts the service or the bar plugin
unless the caller explicitly asks for it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from yoga_deck.adapters.linux_discovery import LinuxDiscovery

PLUGIN_ID = "cos.yoga-deck"
PLUGIN_FILES = ("manifest.json", "Panel.qml", "status.py")
UNIT_NAME = "yoga-deck.service"
HOOK_TYPE = "theme-set"
HOOK_NAME = "yoga-deck"
HOOK_MARKER = b"Yoga Deck theme-set hook"
RUNTIME_BINARIES = ("yoga-deck-runtime", "yoga-doctor")


# --- Paths and observations -------------------------------------------------------------------


@dataclass(frozen=True)
class Paths:
    home: Path
    config_home: Path

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> Paths:
        home = Path(environment.get("HOME") or Path.home())
        config = environment.get("XDG_CONFIG_HOME")
        return cls(home=home, config_home=Path(config) if config else home / ".config")

    @property
    def bin_dir(self) -> Path:
        # The unit's ExecStart uses %h/.local/bin, where uv and pipx both place tool entry points.
        return self.home / ".local" / "bin"

    @property
    def unit(self) -> Path:
        return self.config_home / "systemd" / "user" / UNIT_NAME

    @property
    def plugin_dir(self) -> Path:
        # Omarchy resolves plugins and hooks below $HOME/.config regardless of XDG_CONFIG_HOME.
        return self.home / ".config" / "omarchy" / "plugins" / PLUGIN_ID

    @property
    def hook(self) -> Path:
        return self.home / ".config" / "omarchy" / "hooks" / f"{HOOK_TYPE}.d" / HOOK_NAME

    @property
    def settings_dir(self) -> Path:
        return self.config_home / "yoga-deck"


@dataclass(frozen=True)
class FileState:
    kind: str = "absent"  # absent | file | symlink | other
    content: bytes | None = None


@dataclass(frozen=True)
class PluginState:
    kind: str = "absent"  # absent | dir | symlink | other
    manifest_id: str | None = None
    files: Mapping[str, bytes] = field(default_factory=dict)
    link_target: Path | None = None
    extra_entries: bool = False


@dataclass(frozen=True)
class HostState:
    commands: frozenset[str]
    binaries: frozenset[str]
    input_readable: bool
    unit: FileState
    plugin: PluginState
    hook: FileState
    settings_dir_exists: bool


@dataclass(frozen=True)
class Assets:
    plugin_files: Mapping[str, bytes]
    plugin_sources: Mapping[str, Path]
    unit: bytes
    unit_source: Path
    hook: bytes
    hook_source: Path
    checkout_plugin_dir: Path | None


# --- Actions ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteFile:
    source: Path
    dest: Path
    mode: int = 0o644

    def describe(self) -> str:
        return f"install {self.dest}"


@dataclass(frozen=True)
class CopyPlugin:
    sources: tuple[tuple[str, Path], ...]
    dest: Path

    def describe(self) -> str:
        return f"copy Omarchy plugin to {self.dest}"


@dataclass(frozen=True)
class LinkPlugin:
    target: Path
    dest: Path

    def describe(self) -> str:
        return f"link {self.dest} -> {self.target}"


@dataclass(frozen=True)
class RemovePath:
    path: Path

    def describe(self) -> str:
        return f"remove {self.path}"


@dataclass(frozen=True)
class Run:
    argv: tuple[str, ...]
    required: bool = True

    def describe(self) -> str:
        suffix = "" if self.required else " (failure tolerated)"
        return "run " + " ".join(self.argv) + suffix


@dataclass(frozen=True)
class RecoverInputs:
    def describe(self) -> str:
        return "re-enable internal keyboard, touchpad, and TrackPoint"


Action = WriteFile | CopyPlugin | LinkPlugin | RemovePath | Run | RecoverInputs


@dataclass(frozen=True)
class Finding:
    name: str
    ok: bool
    required: bool
    hint: str


@dataclass(frozen=True)
class Plan:
    actions: tuple[Action, ...]
    findings: tuple[Finding, ...] = ()
    errors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return bool(self.errors) or any(f.required and not f.ok for f in self.findings)


@dataclass(frozen=True)
class SetupOptions:
    dev_link: bool = False
    with_theme_hook: bool = False
    enable: bool = False
    enable_plugin: bool = False


@dataclass(frozen=True)
class UninstallOptions:
    purge: bool = False


# --- Pure planning ----------------------------------------------------------------------------


def check_prerequisites(host: HostState) -> tuple[Finding, ...]:
    commands = host.commands

    def command(name: str, required: bool, hint: str) -> Finding:
        return Finding(name, name in commands, required, hint)

    findings = [
        command("systemctl", True, "systemd user session is required"),
        command("omarchy", True, "the Omarchy CLI is required for plugin validation and hooks"),
        command("hyprctl", True, "Hyprland is required for input and rotation control"),
        command("python3", True, "the plugin bridge and theme hook run with python3"),
    ]
    for binary in RUNTIME_BINARIES:
        findings.append(
            Finding(
                f"~/.local/bin/{binary}",
                binary in host.binaries,
                True,
                "install the Python tool first: see docs/install.md (uv tool install ...)",
            )
        )
    findings.extend(
        (
            Finding(
                "input device access",
                host.input_readable,
                False,
                "the runtime cannot read the tablet switch; run "
                '`sudo usermod -aG input "$USER"`, then log out and back in',
            ),
            command(
                "monitor-sensor",
                False,
                "install iio-sensor-proxy for automatic rotation: sudo pacman -S iio-sensor-proxy",
            ),
            command(
                "wvkbd-mobintl",
                False,
                "optional on-screen keyboard: install wvkbd from the AUR (yay -S wvkbd)",
            ),
        )
    )
    return tuple(findings)


def _plugin_is_ours(state: PluginState) -> bool:
    return state.kind in {"dir", "symlink"} and state.manifest_id == PLUGIN_ID


def plan_setup(options: SetupOptions, paths: Paths, assets: Assets, host: HostState) -> Plan:
    actions: list[Action] = []
    errors: list[str] = []
    notes: list[str] = []

    unit_changed = host.unit.kind != "file" or host.unit.content != assets.unit
    if host.unit.kind == "other":
        errors.append(f"{paths.unit} exists and is not a regular file; remove it first")
    elif unit_changed:
        actions.append(WriteFile(assets.unit_source, paths.unit))
        actions.append(Run(("systemctl", "--user", "daemon-reload")))
    else:
        notes.append("user unit is up to date")

    plugin = host.plugin
    if plugin.kind != "absent" and not _plugin_is_ours(plugin):
        errors.append(
            f"{paths.plugin_dir} exists but is not the {PLUGIN_ID} plugin; refusing to replace it"
        )
    elif options.dev_link:
        target = assets.checkout_plugin_dir
        if target is None:
            errors.append("--dev-link requires running from a source checkout")
        elif plugin.kind == "symlink" and plugin.link_target == target:
            notes.append("plugin link is up to date")
        else:
            if plugin.kind != "absent":
                actions.append(RemovePath(paths.plugin_dir))
            actions.append(LinkPlugin(target, paths.plugin_dir))
        if target is not None:
            # `omarchy plugin validate` refuses symlinks, so validate the link target itself.
            actions.append(Run(("omarchy", "plugin", "validate", str(target))))
    else:
        current = (
            plugin.kind == "dir"
            and not plugin.extra_entries
            and dict(plugin.files) == dict(assets.plugin_files)
        )
        if current:
            notes.append("plugin copy is up to date")
        else:
            if plugin.kind == "symlink":
                actions.append(RemovePath(paths.plugin_dir))
            actions.append(
                CopyPlugin(
                    tuple((name, assets.plugin_sources[name]) for name in PLUGIN_FILES),
                    paths.plugin_dir,
                )
            )
        actions.append(Run(("omarchy", "plugin", "validate", str(paths.plugin_dir))))

    if options.with_theme_hook:
        if host.hook.kind == "file" and host.hook.content == assets.hook:
            notes.append("theme hook is up to date")
        elif host.hook.kind in {"file", "symlink"} and HOOK_MARKER not in (
            host.hook.content or b""
        ):
            errors.append(f"{paths.hook} exists but is not the Yoga Deck hook")
        else:
            actions.append(Run(("omarchy", "hook", "install", HOOK_TYPE, str(assets.hook_source))))

    if options.enable:
        actions.append(Run(("systemctl", "--user", "enable", UNIT_NAME)))
        # restart also starts a stopped unit, and picks up an upgraded runtime.
        actions.append(Run(("systemctl", "--user", "restart", UNIT_NAME)))
    if options.enable_plugin:
        actions.append(Run(("omarchy", "plugin", "enable", PLUGIN_ID), required=False))

    findings = check_prerequisites(host)
    steps: list[str] = []
    if not host.input_readable:
        steps.append('grant input access: sudo usermod -aG input "$USER", then log out and in')
    steps.append("verify discovery: yoga-doctor inspect && yoga-doctor report --redact")
    if not options.enable:
        steps.append(f"start the runtime: systemctl --user enable --now {UNIT_NAME}")
    if not options.enable_plugin:
        steps.append(f"add the bar widget: omarchy plugin enable {PLUGIN_ID}")
    steps.append("emergency input recovery at any time: yoga-doctor recover-inputs")

    return Plan(tuple(actions), findings, tuple(errors), tuple(notes), tuple(steps))


def plan_uninstall(options: UninstallOptions, paths: Paths, host: HostState) -> Plan:
    # Recovery comes first: nothing below may leave the internal keyboard disabled.
    actions: list[Action] = [RecoverInputs()]
    errors: list[str] = []
    notes: list[str] = []

    plugin = host.plugin
    plugin_ours = _plugin_is_ours(plugin)
    if plugin_ours and "omarchy" in host.commands:
        actions.append(Run(("omarchy", "plugin", "disable", PLUGIN_ID), required=False))

    if host.unit.kind != "absent":
        actions.append(Run(("systemctl", "--user", "disable", "--now", UNIT_NAME), required=False))
        actions.append(RemovePath(paths.unit))
        actions.append(Run(("systemctl", "--user", "daemon-reload"), required=False))
    else:
        notes.append("user unit is not installed")

    if plugin.kind == "absent":
        notes.append("plugin is not installed")
    elif plugin_ours:
        actions.append(RemovePath(paths.plugin_dir))
    else:
        errors.append(f"{paths.plugin_dir} is not the {PLUGIN_ID} plugin; leaving it in place")

    if host.hook.kind == "absent":
        pass
    elif HOOK_MARKER in (host.hook.content or b""):
        actions.append(RemovePath(paths.hook))
    else:
        errors.append(f"{paths.hook} is not the Yoga Deck hook; leaving it in place")

    if options.purge and host.settings_dir_exists:
        actions.append(RemovePath(paths.settings_dir))
    elif host.settings_dir_exists:
        notes.append(f"keeping settings in {paths.settings_dir} (use --purge to remove)")

    steps = ("remove the Python tool: uv tool uninstall yoga-deck (or: pipx uninstall yoga-deck)",)
    return Plan(tuple(actions), (), tuple(errors), tuple(notes), steps)


# --- Boundary: observation, assets, execution -------------------------------------------------


def _file_state(path: Path) -> FileState:
    if path.is_symlink():
        try:
            return FileState("symlink", path.read_bytes())
        except OSError:
            return FileState("symlink", None)
    if not path.exists():
        return FileState()
    if path.is_file():
        return FileState("file", path.read_bytes())
    return FileState("other")


def _manifest_id(directory: Path) -> str | None:
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
    except (OSError, ValueError):
        return None
    value = manifest.get("id") if isinstance(manifest, dict) else None
    return value if isinstance(value, str) else None


def _plugin_state(path: Path) -> PluginState:
    if path.is_symlink():
        return PluginState("symlink", _manifest_id(path), link_target=Path(os.path.realpath(path)))
    if not path.exists():
        return PluginState()
    if not path.is_dir():
        return PluginState("other")
    files: dict[str, bytes] = {}
    extra = False
    for entry in path.iterdir():
        if entry.name in PLUGIN_FILES and entry.is_file() and not entry.is_symlink():
            files[entry.name] = entry.read_bytes()
        else:
            extra = True
    return PluginState("dir", _manifest_id(path), files, extra_entries=extra)


def _default_input_probe() -> bool:
    """The same per-role access check `yoga-doctor inspect` reports as ``permission_denied``."""

    return not LinuxDiscovery().runtime_input_denied()


def observe_host(
    paths: Paths,
    *,
    which: Callable[[str], str | None] = shutil.which,
    input_probe: Callable[[], bool] = _default_input_probe,
) -> HostState:
    names = ("systemctl", "omarchy", "hyprctl", "python3", "monitor-sensor", "wvkbd-mobintl")
    return HostState(
        commands=frozenset(name for name in names if which(name)),
        binaries=frozenset(
            name for name in RUNTIME_BINARIES if os.access(paths.bin_dir / name, os.X_OK)
        ),
        input_readable=input_probe(),
        unit=_file_state(paths.unit),
        plugin=_plugin_state(paths.plugin_dir),
        hook=_file_state(paths.hook),
        settings_dir_exists=paths.settings_dir.is_dir(),
    )


def _checkout_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    manifest = root / "omarchy-plugin" / "manifest.json"
    if (root / "pyproject.toml").is_file() and manifest.is_file():
        return root
    return None


def load_assets() -> Assets:
    """Locate the plugin, unit, and hook shipped with this exact runtime version.

    A wheel carries them below ``yoga_deck/data``; an editable or source checkout falls back to
    the repository files that are their source of truth.
    """

    checkout = _checkout_root()
    packaged = Path(str(resources.files("yoga_deck") / "data"))
    if (packaged / "omarchy-plugin" / "manifest.json").is_file():
        plugin_dir = packaged / "omarchy-plugin"
        unit = packaged / "systemd" / UNIT_NAME
        hook = packaged / "hooks" / f"{HOOK_TYPE}.d" / HOOK_NAME
    elif checkout is not None:
        plugin_dir = checkout / "omarchy-plugin"
        unit = checkout / "systemd" / UNIT_NAME
        hook = checkout / "hooks" / f"{HOOK_TYPE}.d" / HOOK_NAME
    else:
        raise FileNotFoundError("packaged_assets_missing")
    sources = {name: plugin_dir / name for name in PLUGIN_FILES}
    return Assets(
        plugin_files={name: path.read_bytes() for name, path in sources.items()},
        plugin_sources=sources,
        unit=unit.read_bytes(),
        unit_source=unit,
        hook=hook.read_bytes(),
        hook_source=hook,
        checkout_plugin_dir=checkout / "omarchy-plugin" if checkout else None,
    )


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _copy_plugin(action: CopyPlugin) -> None:
    dest = action.dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.with_name(dest.name + ".yoga-deck-new")
    _remove(staging)
    staging.mkdir()
    for name, source in action.sources:
        shutil.copyfile(source, staging / name)
        (staging / name).chmod(0o644)
    _remove(dest)
    staging.rename(dest)


def default_runner(argv: Sequence[str]) -> int:
    try:
        return subprocess.run(list(argv), check=False).returncode
    except OSError:
        return 127


def execute(
    actions: Sequence[Action],
    *,
    runner: Callable[[Sequence[str]], int],
    recover: Callable[[], bool],
    echo: Callable[[str], None] = print,
) -> bool:
    """Perform actions in order; stop at the first failed required step."""

    for action in actions:
        echo(f"- {action.describe()}")
        match action:
            case WriteFile(source=source, dest=dest, mode=mode):
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.is_symlink():
                    dest.unlink()
                shutil.copyfile(source, dest)
                dest.chmod(mode)
            case CopyPlugin():
                _copy_plugin(action)
            case LinkPlugin(target=target, dest=dest):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.symlink_to(target, target_is_directory=True)
            case RemovePath(path=path):
                _remove(path)
            case RecoverInputs():
                if not recover():
                    echo("  warning: input recovery incomplete; the service stop will retry it")
            case Run(argv=argv, required=required):
                code = runner(argv)
                if code != 0:
                    if required:
                        echo(f"  failed with exit status {code}")
                        return False
                    echo(f"  exit status {code} (continuing)")
    return True
