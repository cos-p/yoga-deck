"""Pure user-settings contract: defaults, per-key validation, and argument rendering.

Settings live in one file so a user has one place to look, but nothing here reads it. The
adapter supplies a mapping and receives back a value plus the names of whatever it rejected;
deciding where the mapping came from, and what to do about the rejections, stays outside the
core.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from .model import PenAction

#: Height bounds for the keyboard surface. Below the floor no row is a fingertip tall at any
#: scale this project has measured; above the ceiling the surface exceeds every panel it
#: targets, and wvkbd would claim a layer-shell exclusive zone larger than the screen.
MIN_OSK_HEIGHT = 60
MAX_OSK_HEIGHT = 2000

#: wvkbd rounds individual key faces rather than the surface, so a radius past half a key is
#: not a shape anyone asked for.
MAX_CORNER_RADIUS = 64

#: Pango descriptions are short human-readable selectors, not arbitrary payloads. Keeping a
#: generous bound prevents an accidental pasted document from becoming one subprocess argument.
MAX_FONT_LENGTH = 200

#: Opacity bounds, as a percentage of full. The floor is not zero: a fully transparent keyboard
#: still claims its layer-shell exclusive zone and still swallows every touch inside it, so
#: zero would hand a user an invisible keyboard with no way to see what is eating their taps.
MIN_OSK_OPACITY = 10
MAX_OSK_OPACITY = 100

#: Section names the file is allowed to carry. Anything else is a typo worth reporting.
SECTIONS = ("osk", "pen")

#: Stands in for the whole document when the document itself is the thing that is unusable.
DOCUMENT_NAME = "<document>"


@dataclass(frozen=True, slots=True)
class OskSettings:
    """The on-screen keyboard's user-owned shape. Colors are not here, and never will be.

    wvkbd 0.20 takes its palette only from argv, so Yoga Deck derives one from the active
    Omarchy theme at every spawn; see `docs/osk-theming.md`. A color setting would be a
    standing instruction to ignore the user's theme, which is the opposite of the intent.

    Defaults reproduce the measured X390 sizing profile exactly. `corner_radius` is `None`
    rather than `0` because "unset" and "square" are different requests: only the second one
    should put a flag on the command line.
    """

    landscape_height: int = 280
    portrait_height: int = 360
    font: str = "Sans 20"
    corner_radius: int | None = None
    opacity: int = 100
    key_popup: bool = True
    highlight: bool = True

    def to_args(self) -> tuple[str, ...]:
        """Render as wvkbd flags, omitting every default that wvkbd already applies itself.

        `opacity` is deliberately absent. wvkbd has no opacity flag worth using -- its
        `--alpha` is applied after the palette flags and overwrites their alpha, and it reaches
        neither swiped key face -- so opacity is carried in the alpha byte of the colors
        themselves and is rendered by `OskPalette.to_args`, next to the colors it modifies.
        """

        args = [
            "-L",
            str(self.landscape_height),
            "-H",
            str(self.portrait_height),
            "--fn",
            self.font,
        ]
        if self.corner_radius is not None:
            args.extend(("-R", str(self.corner_radius)))
        # Both flags are negative-only, so agreeing with wvkbd's default means saying nothing.
        if not self.key_popup:
            args.append("--no-popup")
        if not self.highlight:
            args.append("--no-highlight")
        return tuple(args)


@dataclass(frozen=True, slots=True)
class PenSettings:
    """The measured far-button mapping. `None` leaves the button deliberately unbound."""

    far_button_action: PenAction | None = None


@dataclass(frozen=True, slots=True)
class Settings:
    osk: OskSettings = OskSettings()
    pen: PenSettings = PenSettings()


@dataclass(frozen=True, slots=True)
class ParsedSettings:
    """A usable value in every case, plus the dotted names of whatever was discarded.

    There is no error path. A settings file is read on the way to spawning a keyboard, and a
    typo in one key must not be able to stop the keyboard from appearing -- which is the whole
    difference between this and `load_pen_action_mapping`, where refusing to bind a button is
    the safe outcome. `rejected` exists so a discarded key is still visible in diagnostics
    rather than silently ignored.
    """

    settings: Settings = Settings()
    rejected: tuple[str, ...] = ()
    #: `(dotted name, source)` for every key a document actually set, so a client can say where
    #: an effective value came from and which of several files to edit to change it.
    origins: tuple[tuple[str, str], ...] = ()
    #: `(source, dotted name)` for every rejection. `rejected` answers what was lost; this
    #: answers which file to go and fix, which only matters once more than one file exists.
    problems: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SettingSpec:
    """Everything a client needs to render a control, check a value, and print an error.

    The loader validates through this table rather than beside it, so a key cannot be
    accepted by the settings file yet unknown to `yoga-doctor config`, or the reverse. The
    default is read back off the dataclass instead of repeated here, for the same reason.
    """

    name: str
    kind: str
    summary: str
    minimum: int | None = None
    maximum: int | None = None
    enum_type: type[Enum] | None = None
    #: The span a slider should sweep, when the accepted range is far wider than anyone would
    #: drag through. The file still accepts everything between `minimum` and `maximum`; this
    #: only stops a control from spending nine tenths of its travel on heights nobody wants.
    slider_range: tuple[int, int] | None = None

    @property
    def section(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def field(self) -> str:
        return self.name.split(".", 1)[1]

    @property
    def default(self) -> Any:
        return getattr(Settings.__dataclass_fields__[self.section].default, self.field)

    @property
    def slider_bounds(self) -> tuple[int | None, int | None]:
        """The range a control sweeps, which is the accepted range unless one was narrowed."""

        return self.slider_range or (self.minimum, self.maximum)

    @property
    def choices(self) -> tuple[str, ...]:
        return () if self.enum_type is None else tuple(member.value for member in self.enum_type)

    def read(self, settings: Settings) -> Any:
        """This setting's current value out of a resolved settings tree."""

        return getattr(getattr(settings, self.section), self.field)

    def validate(self, value: Any) -> Any | None:
        """Accept a decoded TOML value, or return `None` for anything unusable."""

        if self.kind == "integer":
            # `bool` subclasses `int`, and `landscape_height = true` is a mistake, not a 1.
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            low = MIN_INT if self.minimum is None else self.minimum
            high = MAX_INT if self.maximum is None else self.maximum
            return value if low <= value <= high else None
        if self.kind == "text":
            if not isinstance(value, str):
                return None
            # TOML can legally decode `\u0000`, but execve cannot carry NUL in argv. Reject all
            # Unicode control characters here so the same value is safe for diagnostics, QML,
            # and the subprocess boundary rather than special-casing only the known crash.
            if any(unicodedata.category(character) == "Cc" for character in value):
                return None
            stripped = value.strip()
            if not stripped or (self.maximum is not None and len(stripped) > self.maximum):
                return None
            return stripped
        if self.kind == "flag":
            return value if isinstance(value, bool) else None
        if self.kind == "choice":
            if not isinstance(value, str) or self.enum_type is None:
                return None
            try:
                return self.enum_type(value.strip())
            except ValueError:
                return None
        return None

    def parse_text(self, text: str) -> Any | None:
        """Accept what a person typed at a shell prompt, under exactly the same rules.

        Typed text has to become a TOML value first, so that `config set` can never store
        something the file loader would then turn around and reject.
        """

        stripped = text.strip()
        if self.kind == "integer":
            try:
                return self.validate(int(stripped, 10))
            except ValueError:
                return None
        if self.kind == "flag":
            lowered = stripped.lower()
            # Only the two words TOML itself uses, so the prompt teaches the file format.
            return True if lowered == "true" else (False if lowered == "false" else None)
        return self.validate(stripped)

    def to_toml(self, value: Any) -> Any:
        """The plain scalar this value is stored as, which is what `validate` reads back."""

        return value.value if isinstance(value, Enum) else value

    def describe(self) -> str:
        """What this setting accepts, phrased for the person who just typed it wrong."""

        if self.kind == "integer":
            return f"an integer from {self.minimum} to {self.maximum}"
        if self.kind == "text":
            suffix = "" if self.maximum is None else f" up to {self.maximum} characters"
            return f"a non-empty string{suffix} without control characters"
        if self.kind == "flag":
            return "true or false"
        if self.kind == "choice":
            return "one of " + ", ".join(self.choices)
        return "a value"


#: Bounds for an integer setting that declares none. No setting uses these today; they exist so
#: `validate` stays total rather than special-casing an absent bound at every comparison.
MIN_INT = -(2**63)
MAX_INT = 2**63 - 1

#: Every addressable setting, in the order a listing shows them. Adding a row here is the whole
#: job of adding a setting: the loader, the CLI, and the panel all read this table.
SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "osk.landscape_height",
        "integer",
        "Keyboard height in landscape, in pixels.",
        minimum=MIN_OSK_HEIGHT,
        maximum=MAX_OSK_HEIGHT,
        # Roughly half to double the measured X390 profile. A slider that swept the whole
        # accepted range would put every height worth choosing inside a tenth of its travel.
        slider_range=(140, 560),
    ),
    SettingSpec(
        "osk.portrait_height",
        "integer",
        "Keyboard height in portrait, in pixels.",
        minimum=MIN_OSK_HEIGHT,
        maximum=MAX_OSK_HEIGHT,
        slider_range=(180, 720),
    ),
    SettingSpec(
        "osk.font",
        "text",
        "Pango font description used for key labels.",
        maximum=MAX_FONT_LENGTH,
    ),
    SettingSpec(
        "osk.corner_radius",
        "integer",
        "Rounding radius of each key face, in pixels.",
        minimum=0,
        maximum=MAX_CORNER_RADIUS,
    ),
    SettingSpec(
        "osk.opacity",
        "integer",
        "Keyboard opacity as a percentage; labels stay solid at every setting.",
        minimum=MIN_OSK_OPACITY,
        maximum=MAX_OSK_OPACITY,
        # Below roughly 40 the derived surface ladder -- board, special key, key face -- stops
        # separating against whatever happens to be behind the keyboard, so the slider stops
        # there. Someone who wants a ghost keyboard can still write a smaller number by hand.
        slider_range=(40, MAX_OSK_OPACITY),
    ),
    SettingSpec("osk.key_popup", "flag", "Show a magnified preview above a pressed key."),
    SettingSpec("osk.highlight", "flag", "Tint a key while it is held."),
    SettingSpec(
        "pen.far_button_action",
        "choice",
        "Action requested by the measured far-from-tip pen button.",
        enum_type=PenAction,
    ),
)

SPECS_BY_NAME: Mapping[str, SettingSpec] = {spec.name: spec for spec in SETTING_SPECS}

#: Dotted names in listing order, for help text and for a client enumerating what exists.
SETTING_NAMES: tuple[str, ...] = tuple(spec.name for spec in SETTING_SPECS)


def specs_in(section: str) -> tuple[SettingSpec, ...]:
    return tuple(spec for spec in SETTING_SPECS if spec.section == section)


def _build(accepted: Mapping[str, Any]) -> Settings:
    """Fold accepted dotted names onto the defaults, section by section."""

    sections: dict[str, Any] = {}
    for section in SECTIONS:
        defaults = Settings.__dataclass_fields__[section].default
        fields = {
            spec.field: accepted[spec.name] for spec in specs_in(section) if spec.name in accepted
        }
        sections[section] = replace(defaults, **fields) if fields else defaults
    return Settings(**sections)


def resolve_layers(layers: Iterable[tuple[str, Any]]) -> ParsedSettings:
    """Fold labelled documents, lowest precedence first, keeping every key that validates.

    Each layer is validated before it is folded, rather than merged raw and validated once at
    the end. That is the difference between a mistyped height in the machine-written overlay
    costing the user the value they hand-wrote underneath it, and costing them nothing.
    """

    accepted: dict[str, Any] = {}
    origins: dict[str, str] = {}
    rejected: set[str] = set()
    problems: list[tuple[str, str]] = []

    def discard(source: str, name: str) -> None:
        rejected.add(name)
        problems.append((source, name))

    # An absent file contributes no layer at all, so reaching here with a non-mapping means a
    # document that exists and is the wrong shape -- which is worth reporting, not skipping.
    for source, payload in layers:
        if not isinstance(payload, Mapping):
            discard(source, DOCUMENT_NAME)
            continue
        for raw_section, body in payload.items():
            section = str(raw_section)
            if section not in SECTIONS or not isinstance(body, Mapping):
                discard(source, section)
                continue
            for raw_key, value in body.items():
                name = f"{section}.{raw_key}"
                spec = SPECS_BY_NAME.get(name)
                parsed = None if spec is None else spec.validate(value)
                if parsed is None:
                    discard(source, name)
                else:
                    accepted[name] = parsed
                    origins[name] = source

    return ParsedSettings(
        _build(accepted),
        # Sorted so a diagnostic does not depend on the order keys happened to appear in.
        tuple(sorted(rejected)),
        tuple(sorted(origins.items())),
        tuple(sorted(problems)),
    )


def parse_settings(payload: Any) -> ParsedSettings:
    """Turn one decoded settings document into values, keeping whatever part of it is usable."""

    return resolve_layers(((DOCUMENT_NAME, payload),))


def settings_snapshot(parsed: ParsedSettings) -> dict[str, Any]:
    """Every setting as plain scalars: what it is, what it is now, and where that came from.

    Both clients read this rather than describing the settings again. `yoga-doctor config`
    prints from it in the same process; the Omarchy panel receives it over the shell transport
    and picks a control from `kind` alone, so adding a row to `SETTING_SPECS` gives the panel a
    control without a line of QML changing.

    Values are rendered as the scalars the settings file holds, so a client that writes one
    back is handing over exactly what it was given. `source` is `None` when no file set the
    key, which is the difference between a value a user chose and a default they inherited.
    """

    origins = dict(parsed.origins)
    return {
        "schema_version": 1,
        "settings": [
            {
                "name": spec.name,
                "section": spec.section,
                "kind": spec.kind,
                "summary": spec.summary,
                "value": spec.to_toml(spec.read(parsed.settings)),
                "source": origins.get(spec.name),
                "default": spec.to_toml(spec.default),
                "minimum": spec.minimum,
                "maximum": spec.maximum,
                # Always present, so a control has one pair of numbers to read rather than a
                # rule about when the narrowed pair applies.
                "slider_minimum": spec.slider_bounds[0],
                "slider_maximum": spec.slider_bounds[1],
                "choices": list(spec.choices),
            }
            for spec in SETTING_SPECS
        ],
        # What some file asked for and did not get. A client that shows nothing here is
        # telling the user their file worked, so it is worth carrying alongside the values.
        "ignored": [{"source": source, "name": name} for source, name in parsed.problems],
    }


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    # TOML forbids the C0 controls and DEL unescaped in a basic string; everything above stays
    # literal, so a font name in any script is stored as itself rather than as escape sequences.
    escaped = "".join(
        character if " " <= character != "\x7f" else f"\\u{ord(character):04x}"
        for character in escaped
    )
    return f'"{escaped}"'


def render_document(document: Mapping[str, Mapping[str, Any]], *, header: str = "") -> str:
    """Serialise a settings document as TOML, keeping only keys this table knows.

    Deliberately not a general TOML writer. It emits known keys in listing order and drops
    everything else, so the machine-written file cannot accumulate stale or malformed keys no
    matter what is handed to it. That is why the project needs no TOML-writing dependency.
    """

    lines: list[str] = []
    for section in SECTIONS:
        body = document.get(section) or {}
        if not isinstance(body, Mapping):
            continue
        rows = [
            f"{spec.field} = {_toml_scalar(spec.to_toml(body[spec.field]))}"
            for spec in specs_in(section)
            if spec.field in body and spec.validate(spec.to_toml(body[spec.field])) is not None
        ]
        if not rows:
            continue
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        lines.extend(rows)
    # A header with nothing under it is not a document, so an empty result stays empty and the
    # caller can treat "" as "there is nothing left to store here".
    if not lines:
        return ""
    prelude = [*header.splitlines(), ""] if header else []
    return "\n".join([*prelude, *lines]) + "\n"
