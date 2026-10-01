"""Read and write the user's settings files.

Reading never raises, so a broken file cannot withhold a keyboard. Writing is the exception:
`yoga-doctor config set` has a person waiting on the result, and a failed write must be said
out loud rather than swallowed.

Three files stack, lowest precedence first:

* `pen-actions.toml` -- the original single-purpose pen file, folded in as `[pen]`.
* `config.toml` -- hand-written and hand-commented. No program ever writes to it.
* `config.local.toml` -- machine-written, by `yoga-doctor config set` and by the panel.

The machine never writes to the file a person edits, so a slider cannot silently reformat a
hand-written file or discard its comments. It wins on precedence for the opposite reason: a
control that quietly does nothing because a key happens to be set in `config.toml` would be a
worse failure than a surprising one, and `config get` names the source of every value so the
surprise is answerable.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
import tomllib
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from yoga_deck.core.settings import (
    SECTIONS,
    SETTING_NAMES,
    SPECS_BY_NAME,
    ParsedSettings,
    Settings,
    parse_settings,
    render_document,
    resolve_layers,
    settings_snapshot,
)

SETTINGS_FILENAME = "config.toml"

#: Written by controls, never by hand. Overrides `config.toml` key by key.
OVERRIDES_FILENAME = "config.local.toml"

#: Reproduced at the top of the overrides file on every write, because the one thing a reader
#: who stumbles onto a machine-written file needs to know is that it is machine-written.
OVERRIDES_HEADER = """\
# Written by Yoga Deck. The Omarchy panel and `yoga-doctor config set` write here.
#
# Every key in this file overrides the same key in config.toml. Hand-written settings and
# comments belong in config.toml, which no program ever rewrites; this file is reformatted
# from scratch on each write and unknown keys in it are dropped.
#
# Remove a key with `yoga-doctor config unset <name>`, which hands that setting back to
# config.toml. `yoga-doctor config get` names the file each effective value came from.
"""

#: The original single-purpose pen file, documented in `docs/pen-action-mapping.md` and shipped
#: as `config/pen-actions.toml.example`. Its flat body is exactly the `[pen]` section's schema,
#: so it is folded in as that section and `config.toml` wins wherever both name a key.
LEGACY_PEN_FILENAME = "pen-actions.toml"


def config_root(environment: Mapping[str, str] | None = None) -> Path:
    environment = environment if environment is not None else os.environ
    config_home = environment.get("XDG_CONFIG_HOME")
    root = Path(config_home) if config_home else Path.home() / ".config"
    return root / "yoga-deck"


def default_settings_path(environment: Mapping[str, str] | None = None) -> Path:
    return config_root(environment) / SETTINGS_FILENAME


def default_overrides_path(environment: Mapping[str, str] | None = None) -> Path:
    return config_root(environment) / OVERRIDES_FILENAME


def _read(path: Path) -> tuple[Mapping[str, Any] | None, bool]:
    """`(document, readable)`. An absent file is `(None, True)`; a broken one is `(None, False)`."""

    try:
        return tomllib.loads(path.read_text(encoding="utf-8")), True
    except FileNotFoundError:
        return None, True
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError, ValueError):
        return None, False


def load_settings(
    path: Path | None = None,
    *,
    legacy_pen_path: Path | None = None,
    overrides_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> ParsedSettings:
    """Resolve every file into one value. An absent set leaves every default in place."""

    root = config_root(environment)
    settings_path = path or root / SETTINGS_FILENAME
    pen_path = legacy_pen_path or root / LEGACY_PEN_FILENAME
    local_path = overrides_path or root / OVERRIDES_FILENAME

    layers: list[tuple[str, Any]] = []
    unreadable: list[str] = []

    legacy, readable = _read(pen_path)
    if not readable:
        unreadable.append(pen_path.name)
    elif legacy is not None:
        # Its body is flat, and is exactly the `[pen]` section's schema.
        layers.append((pen_path.name, {"pen": legacy}))

    for candidate in (settings_path, local_path):
        document, readable = _read(candidate)
        if not readable:
            unreadable.append(candidate.name)
        elif document is not None:
            layers.append((candidate.name, document))

    parsed = resolve_layers(layers)
    if not unreadable:
        return parsed
    # File names only. A path would carry the user's home directory into diagnostics.
    return replace(
        parsed,
        rejected=tuple(sorted((*parsed.rejected, *unreadable))),
        problems=tuple(sorted((*parsed.problems, *((name, name) for name in unreadable)))),
    )


def read_overrides(
    path: Path | None = None, environment: Mapping[str, str] | None = None
) -> dict[str, dict[str, Any]]:
    """The machine-written file as a plain document. An absent or broken file reads as empty.

    A broken overrides file is treated as no file rather than as an error, because the only
    way to repair it through this module is to write it again, and refusing to read it would
    make `config set` the one command that cannot fix the problem it is reporting.
    """

    document, _ = _read(path or default_overrides_path(environment))
    if not isinstance(document, Mapping):
        return {}
    return {
        section: dict(body)
        for section, body in document.items()
        if section in SECTIONS and isinstance(body, Mapping)
    }


def _write_overrides(document: Mapping[str, Mapping[str, Any]], target: Path) -> None:
    """Replace the overrides file atomically, or remove it once nothing is left in it."""

    body = render_document(document, header=OVERRIDES_HEADER)
    if not body:
        # Nothing overridden any more. An empty stub would only invite the question of what it
        # is for, so the file goes away and config.toml is visibly back in charge.
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # Same directory, so the replace is a rename within one filesystem and never a partial file.
    descriptor, scratch = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(scratch, target)
    except BaseException:
        Path(scratch).unlink(missing_ok=True)
        raise


def _lock_path(target: Path) -> Path:
    """A stable inode beside the atomically replaced document.

    Locking the document itself would stop protecting readers as soon as `os.replace` gives
    the path a new inode. The adjacent lock file stays put across every replacement.
    """

    return target.with_name(f".{target.name}.lock")


@contextmanager
def _overrides_transaction(target: Path):
    """Serialize one complete read-modify-replace transaction across local processes."""

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(_lock_path(target), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+b", closefd=False) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _apply_override(name: str, value: Any | None, target: Path) -> bool:
    """Apply or clear one key under the transaction lock; return whether it changed."""

    spec = SPECS_BY_NAME[name]
    with _overrides_transaction(target):
        document = read_overrides(target)
        section = document.setdefault(spec.section, {})
        if value is None:
            if spec.field not in section:
                return False
            del section[spec.field]
        else:
            wanted = spec.to_toml(value)
            stored = section.get(spec.field)
            # Compared by type as well as value, because `True == 1` in Python and a
            # hand-broken overrides file can hold either where the other belongs.
            if type(stored) is type(wanted) and stored == wanted:
                return False
            section[spec.field] = wanted
        _write_overrides(document, target)
        return True


def write_override(
    name: str,
    value: Any,
    *,
    path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Store one already-validated setting. Raises `OSError` if the file cannot be written."""

    spec = SPECS_BY_NAME.get(name)
    if spec is None:
        raise KeyError(name)
    target = path or default_overrides_path(environment)
    _apply_override(name, value, target)
    return target


def clear_override(
    name: str,
    *,
    path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Drop one setting from the overrides file. `False` if it was not overridden."""

    spec = SPECS_BY_NAME.get(name)
    if spec is None:
        raise KeyError(name)
    target = path or default_overrides_path(environment)
    return _apply_override(name, None, target)


class UserSettingsSource:
    """Adapter seam so the runtime can be handed settings without touching a filesystem."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        legacy_pen_path: Path | None = None,
        overrides_path: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._path = path
        self._legacy_pen_path = legacy_pen_path
        self._overrides_path = overrides_path
        self._environment = environment

    def resolve(self) -> ParsedSettings:
        return load_settings(
            self._path,
            legacy_pen_path=self._legacy_pen_path,
            overrides_path=self._overrides_path,
            environment=self._environment,
        )

    def snapshot(self) -> dict[str, Any]:
        """Every setting as plain scalars, for a client that has to render controls for them.

        Carries the name of the machine-written file so a client can tell an override it may
        clear from a hand-written value it must not, without knowing the file layout itself.
        """

        return {**settings_snapshot(self.resolve()), "overrides_file": OVERRIDES_FILENAME}

    def apply(self, name: str, value: Any) -> bool:
        """Store one validated setting, or clear it when `value` is `None`.

        Returns whether the stored document actually changed, so a caller can skip the work a
        change implies -- respawning the keyboard -- when a control resends what is already
        there. Raises `KeyError` for an unknown name and `OSError` if the file cannot be
        written; a person or a panel is waiting on the answer either way.
        """

        spec = SPECS_BY_NAME.get(name)
        if spec is None:
            raise KeyError(name)
        target = self._overrides_path or default_overrides_path(self._environment)
        return _apply_override(name, value, target)


__all__ = [
    "LEGACY_PEN_FILENAME",
    "OVERRIDES_FILENAME",
    "SECTIONS",
    "SETTINGS_FILENAME",
    "SETTING_NAMES",
    "SPECS_BY_NAME",
    "ParsedSettings",
    "Settings",
    "UserSettingsSource",
    "clear_override",
    "config_root",
    "default_overrides_path",
    "default_settings_path",
    "load_settings",
    "parse_settings",
    "read_overrides",
    "settings_snapshot",
    "write_override",
]
