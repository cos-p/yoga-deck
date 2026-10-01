# Settings

Yoga Deck reads its user-owned settings from three files, lowest precedence first:

| File | Written by | Purpose |
| --- | --- | --- |
| `~/.config/yoga-deck/pen-actions.toml` | you | The original single-purpose pen file, folded in as `[pen]`. |
| `~/.config/yoga-deck/config.toml` | you | Hand-written and hand-commented. No program ever writes to it. |
| `~/.config/yoga-deck/config.local.toml` | Yoga Deck | Written by `yoga-doctor config set` and by the panel. |

[`config/config.toml.example`](../config/config.toml.example) lists every setting at its default
with every line commented, so copying it changes nothing until a line is uncommented.

## Why the machine writes to its own file

A control that rewrites the file a person edits has to either preserve their comments and layout or
destroy them, and a TOML round-tripper is a dependency this project does not otherwise need. So the
machine writes only to `config.local.toml`, which it owns completely: that file is regenerated from
scratch on every write, in canonical order, dropping anything the settings table does not know, and
it is deleted once nothing is overridden. `config.toml` is never touched by a program.

The machine-written file wins on precedence, which is the direction that trades a surprise for a
failure. If the hand-written file won, dragging a slider would silently do nothing whenever that key
happened to be set in `config.toml` -- and this is the folded posture, where the panel is the only
way in. Instead the override takes effect, `yoga-doctor config set` says which file it now shadows,
`yoga-doctor config get` names the source of every effective value, and `yoga-doctor config unset`
hands the setting back.

Each file is validated **before** it is folded onto the ones below it, not merged raw and validated
once at the end. A mistyped height in the overrides file therefore costs that one key and leaves the
hand-written value underneath it standing.

## Changing a setting without an editor

```
$ yoga-doctor config                       # every setting, its value, and where it came from
$ yoga-doctor config get osk.corner_radius # one setting, with what it accepts
$ yoga-doctor config set osk.landscape_height 400
$ yoga-doctor config unset osk.landscape_height
$ yoga-doctor config path                  # portable ~/ or $XDG_CONFIG_HOME paths and presence
```

`config get --json` renders the same listing with each value left in its own type, plus an `ignored`
array naming any key a file set that the loader had to discard.

`config set` refuses an unusable value at the prompt, with a nonzero exit and the rule that was
broken, rather than storing it for the loader to discard later. That is the opposite stance from the
file loader below, and deliberately so: the loader degrades because a keyboard still has to appear,
while a person at a prompt is owed the reason. A Hypothesis property holds the two in agreement --
anything `config set` accepts, the loader accepts back.

## Changing a setting from the panel

The Omarchy panel is not optional polish for these settings. The premise of the project is the
machine folded into a tablet, which is the posture with no keyboard attached -- and the keyboard is
what is being configured. "Edit a TOML file" is not an answer there, so every setting a control can
express is thumb-reachable in the panel.

The panel holds no list of settings. It asks the runtime for one over the existing shell transport:

```
{"command": "get_config"}
{"command": "set_config", "setting": "osk.landscape_height", "value": "420"}
{"command": "set_config", "setting": "osk.landscape_height"}
```

`get_config` returns every setting with its value, the file that set it, its default, its bounds or
choices, and its `kind`; the panel picks a control from `kind` alone. A row added to `SETTING_SPECS`
therefore becomes a control with no QML change, which is the whole reason for one table. Omitting
`value` clears the override, which is what the panel's **Reset** does.

| Kind | Control |
| --- | --- |
| `integer` | Slider, with the value read out live while it is dragged. |
| `flag` | Labelled switch row. |
| `choice` | One button per choice, plus **Unbound** where the setting may be absent. |
| `text` | Read-only, with the `yoga-doctor config set` line that changes it. |

Free text is deliberately read-only in the panel. A text field there would summon the very keyboard
being configured, on top of the panel, and a Pango font description is not something to type with a
thumb.

An integer's slider sweeps `slider_range` when the spec declares one, which is narrower than the
range the file accepts. The bounds exist to reject nonsense -- a height of 5000 is a mistake -- while
a slider that swept all of 60 to 2000 would put every height worth choosing inside a tenth of its
travel. A value set by hand outside that span widens the slider rather than being misreported at one
end. Only the accepted range is a rule; the swept range is a nicety, and a test holds it inside the
rule.

Values travel as text, because a panel control shells out through argv and that is all a process
gets. The runtime parses them with the same parser `yoga-doctor config set` uses and refuses at the
socket, so no client can store what the prompt would refuse or what the loader would then discard.

A stored change re-enters the reducer as `SettingsChanged`, which plans the same `RefreshOskTheme`
effect a theme change does: the backend rebuilds its whole argument vector when it respawns, so
there is nothing a settings change needs that a retint does not already do. That means a slider
lands at the next time the keyboard appears rather than at the next fold, and the existing
visible/hidden deferral still applies, so a keyboard is never torn down mid-press. Resending a value
that is already stored plans nothing at all, so a control that repeats itself cannot churn the
keyboard.

## Why a file is the store

Settings must be readable and writable with no shell running, no bar widget loaded, and over SSH,
and they must survive being copied to another machine. That rules out Omarchy's plugin-settings
mechanism as the store, even though Yoga Deck already uses it: those values live in `shell.json`
scoped to the bar widget, so removing the widget from the bar would discard them. The manifest
schema in [`omarchy-plugin/manifest.json`](../omarchy-plugin/manifest.json) therefore stays scoped
to the widget's own presentation -- `showPostureIcon`, `pollIntervalMs` -- and never to daemon
behaviour.

The runtime also does not read `shell.json` at all. That file is Omarchy-owned, and the compositor
adapter is meant to be replaceable.

## A bad value costs one key, never the keyboard

This is the one place Yoga Deck's settings behaviour differs deliberately from
[`pen-action-mapping.md`](pen-action-mapping.md). `load_pen_action_mapping` raises on a malformed
file, which is right for an opt-in button: refusing to bind it is the safe outcome. The same stance
applied to a general settings file would let a typo in one key stop the on-screen keyboard from
appearing at all.

So the loader has no error path. Each key is validated on its own; a value that cannot be used is
discarded, that key keeps its default, and the runtime emits one diagnostic naming what it lost:

```json
{"code": "settings_keys_rejected", "component": "settings", "keys": "osk.hieght,osk.portrait_height"}
```

Rejected keys are reported by dotted name and files by bare file name, never by path, so the
diagnostic cannot carry a home directory into a support report.

Rejection is deliberate rather than clamping. A height of `5000` is a mistake, and silently
resolving it to `2000` would hide the mistake behind a keyboard that is merely the wrong size.

## Settings are re-read at every spawn

`WvkbdAdapter` resolves its settings on the same path it already resolves the theme palette: once
per spawn, inside `_spawn_child`. An edited file therefore takes effect the next time the machine
is folded, with no service restart and no file watcher. A visible keyboard is never torn down to
apply a change, because the existing visible/hidden deferral for theme changes governs the same
path; see [`on-screen-keyboard.md`](on-screen-keyboard.md).

The far-button mapping is the exception. It is bound when the pen device is opened, so it is read
once at startup.

## `[osk]`

Every key maps to a wvkbd 0.20 flag.

| Setting | Flag | Default | Accepted |
| --- | --- | --- | --- |
| `landscape_height` | `-L` | `280` | integer, 60 to 2000 |
| `portrait_height` | `-H` | `360` | integer, 60 to 2000 |
| `font` | `--fn` | `"Sans 20"` | Pango font description, 1–200 characters, no control characters |
| `corner_radius` | `-R` | unset | integer, 0 to 64 |
| `opacity` | alpha byte on the palette's surface colours | `100` | integer, 10 to 100 |
| `key_popup` | `--no-popup` when `false` | `true` | boolean |
| `highlight` | `--no-highlight` when `false` | `true` | boolean |

The height bounds are not arbitrary. Below 60 logical pixels no row is a fingertip tall at any
scale measured here, and above 2000 the surface exceeds every panel this project targets while
claiming a layer-shell exclusive zone larger than the screen.

`corner_radius` defaults to unset rather than `0` because *unset* and *square* are different
requests: only the second one should put `-R` on the command line. The same reasoning applies to
`key_popup` and `highlight`, whose flags are negative-only, so agreeing with wvkbd's own default
means emitting nothing.

`WVKBD_SIZING_ARGS` is rendered from `OskSettings()` rather than written out separately, so the
profile the diagnostics tool spawns with and the profile a user with no settings file gets cannot
drift apart.

### `opacity` is the one setting with no flag of its own

wvkbd has an `--alpha`, and it is the wrong instrument. It is applied after the palette flags are
parsed, so it overwrites any alpha they carried; it reaches `bg`, `fg` and `press` on both colour
schemes but neither swipe colour, so swipe-typing on a translucent keyboard would light up solid;
and it offers no way to hold anything back from it. So opacity is carried in the optional alpha
byte of the colours themselves, rendered by `OskPalette.to_args(opacity)` next to the colours it
modifies, and `OskSettings.to_args` deliberately emits nothing for it.

It applies to every painted surface and to no label. That split is what makes a see-through
keyboard a readable one, and it works because of how wvkbd draws: a key face is filled with
cairo's `SOURCE` operator, so it *replaces* the board's alpha rather than stacking on it, and the
label is then drawn `OVER` the face — an opaque glyph over a translucent face composites to an
opaque glyph. Key labels stay exactly as legible at 40% as at 100%.

What does erode is the surface ladder. Compositing two surfaces at the same alpha over the same
backdrop preserves only that fraction of the difference between them, so at 50% opacity half the
separation between board, special key and key face survives — against content the keyboard cannot
see or control. That is why the panel slider stops at 40 while the file accepts 10: the floor is a
judgement about legibility, not a limit of the mechanism, and a user who wants a ghost keyboard
can write one by hand. It stops at 10 rather than 0 because a fully transparent keyboard still
claims its layer-shell exclusive zone and still swallows every touch inside it.

### Colours are not settings

wvkbd 0.20 takes its palette only from `argv`, and Yoga Deck derives a full thirteen-role palette
from the active Omarchy theme at every spawn. A colour setting would be a standing instruction to
ignore the user's theme, which is the opposite of the intent. See
[`osk-theming.md`](osk-theming.md).

## `[pen]`

| Setting | Default | Accepted |
| --- | --- | --- |
| `far_button_action` | unbound | `"capture_text"`, `"open_scratchpad"` |

`~/.config/yoga-deck/pen-actions.toml` is still read, so an existing setup keeps working. Its flat
body is exactly this section's schema and is folded in as `[pen]`, at the lowest precedence of the
three files. Read through the settings loader, a malformed legacy file degrades to the default
rather than raising.

## Code layout

Following the core/adapter boundary in [`architecture.md`](architecture.md), and mirroring the existing
`parse_colors_toml` / `OmarchyOskPaletteSource` split:

| Layer | Module | Responsibility |
| --- | --- | --- |
| pure | `core/settings.py` | `SETTING_SPECS`, `Settings`, `resolve_layers`, `render_document`, `settings_snapshot`. No filesystem. |
| adapter | `adapters/user_settings.py` | Resolve the XDG paths, read the three files, write the overrides file. |
| adapter | `adapters/shell_transport.py` | `get_config` and `set_config`, refused at the socket against the same table. |
| client | `cli.py` | `yoga-doctor config get/set/unset/path`. |
| client | `omarchy-plugin/` | `status.py` speaks the transport; `Panel.qml` renders whatever the snapshot describes. |
| wiring | `runtime_main.py` | Load, report rejections, inject into adapters, hand the store to the runtime. |

`SETTING_SPECS` is the single table every layer reads. It carries each setting's dotted name, kind,
bounds or choices, one-line summary, validator, and text parser, and reads its default back off the
dataclass rather than repeating it. Adding a row is the whole job of adding a setting: the loader,
the CLI, and the panel pick it up without a second table to keep in step. `settings_snapshot` is how
a remote client reads that table: plain scalars only, since it crosses a socket and is parsed in
QML. `render_document` is a deliberately partial TOML writer -- known keys, canonical order, valid
values only -- which is why the project still needs no TOML-writing dependency.

The runtime never reads a settings value. It is handed a store it can write to, and a written
change plans a respawn; the keyboard adapter re-resolves the whole file itself at that spawn. That
is why `SettingsChanged` carries neither the name nor the value: keeping either would put a font
name or a key binding into the event journal for nothing.

## Test requirements

- An absent settings file leaves every default in place, and the rendered argument vector is
  byte-identical to the shipped X390 sizing profile.
- One unusable key is discarded on its own; the rest of the file still applies.
- Heights and radii outside their bounds are rejected rather than clamped, and `true` is not
  accepted as an integer.
- A setting that agrees with a wvkbd default emits no flag.
- `opacity` emits no sizing flag at all, `100` renders exactly the argument vector an opaque
  keyboard has always been given, and any accepted opacity leaves every rendered colour parseable
  by wvkbd -- which silently ignores a malformed one rather than failing, so a bad value would
  show up as a single wrongly coloured role rather than an error.
- Opacity reaches every surface role and no label role, and the six colour characters of each
  role are unchanged by it: opacity is a suffix, never a recolour.
- One spawn reads the settings exactly once, so a size and an opacity can never come from two
  different revisions of the file, and an opacity that is not a whole number costs the preference
  rather than the keyboard.
- The legacy pen file still binds the button, `config.toml` overrides it, and neither file being
  broken can discard the other.
- Rejected names carry no filesystem path.
- The shipped example parses to exactly the defaults and names every setting that exists, so a new
  setting cannot ship undiscoverable.
- A Hypothesis property that no document, however malformed, can raise out of the loader or produce
  an argument vector wvkbd could not accept, and that no stack of documents can either.
- Every settings dataclass field has exactly one spec and every spec a field, so the file and the
  CLI cannot disagree about what exists.
- `config set` refuses an out-of-range or wrongly typed value without touching the file, and a
  Hypothesis property that anything it does accept survives being written and read back.
- `config set` writes only `config.local.toml` and leaves a hand-written `config.toml` byte-identical.
- CLI and panel read-modify-write transactions share an adjacent mode-0600 lock, so concurrent
  clients cannot erase one another's disjoint overrides.
- An unusable value in the overrides file does not discard the hand-written value beneath it.
- Unsetting the last override removes the machine-written file rather than leaving an empty stub.
- The snapshot a panel reads names every setting, carries only JSON scalars, names the file behind
  each value, and leaves an inherited default unattributed.
- Every narrowed slider span sits inside the range the file accepts, and holds that setting's
  default.
- A value the settings file could not hold never reaches the runtime, whether it was sent as text
  or as JSON, and both forms of the same value arrive identically.
- A panel change reaches the file and the next snapshot, leaves a hand-written `config.toml`
  byte-identical, and plans the respawn that makes it visible; a refused one plans nothing.
- Resending a value that is already stored plans no respawn.
- No setting is named in `Panel.qml`, and every `kind` in the table has a control there, so the
  panel and the settings table cannot drift apart.
- A headless Qt Test instantiates the real `Panel.qml` with inert shell-boundary doubles, exercises
  valid and degraded status/config payloads, and invokes lock, rotation, and recovery controls
  without connecting to the live runtime or desktop.
