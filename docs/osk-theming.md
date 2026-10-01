# On-screen keyboard theming

This document records the investigation behind Yoga Deck's decision to follow the active Omarchy
theme on its external wvkbd process. It fixes the palette source, the notification lifecycle, the
colour-role mapping, and the refresh policy. Read [`on-screen-keyboard.md`](on-screen-keyboard.md)
first; the lifecycle contract described there is unchanged by this design.

Host facts recorded here were verified on 2026-08-29 against Omarchy 4.0.0-1, wvkbd 0.20-1, and
Hyprland 0.56-era configuration. Treat versions, paths, and command surfaces as snapshots and
re-verify them before depending on them.

## What wvkbd 0.20 can and cannot do

wvkbd 0.20 takes its entire palette from `argv`. It exposes `--bg`, `--fg`, `--fg-sp`, `--press`,
`--press-sp`, `--swipe`, `--swipe-sp`, `--text`, `--text-sp`, `--text-press`, `--text-press-sp`,
`--text-swipe`, `--text-swipe-sp`, and a global `--alpha`. Each colour is `rrggbb` or `rrggbbaa`
with no leading `#`.

It reads no configuration file. The installed binary contains no configuration path string, and its
manual page documents exactly three signals: `SIGUSR1` hides, `SIGUSR2` shows, and `SIGRTMIN`
toggles. There is no palette-reload signal and no runtime colour interface.

The consequence is structural: **a live theme change cannot retint a running wvkbd.** Following the
theme requires either respawning the child with new arguments or replacing wvkbd. Every option below
is judged against that constraint.

## Palette source

Four sources were compared.

`~/.local/state/omarchy/current/theme/colors.toml` holds the applied palette, but it is the *raw*
theme file. Omarchy's consumers never read it directly. `omarchy-theme-set` copies the stock theme,
overlays `~/.config/omarchy/themes/<slug>/`, generates `colors.toml` from `alacritty.toml` when a
theme ships none, renders templates, and only then swaps the directory into place. A raw read misses
the legacy `colorN` aliases, the short-name (`bg`, `fg`) aliases, the derived shades
(`dark_background`, `bright_*`, `brown`), the `selection`/`muted` fallbacks, and the four-step mode
precedence. Re-implementing that cascade in Python would be roughly 150 lines that silently drift
from Omarchy on every update.

`omarchy theme color` is that cascade, factored out. Its source states that it exists so "every
consumer (templates, OSC sequences, tmux, GNOME, previews) resolves the exact same palette". It
accepts `--file <colors.toml>`, and `--all` prints every resolved key as `key<TAB>value`. It is
marked `omarchy:hidden=true`, so it is absent from `omarchy commands`, but the route resolves and
`omarchy theme color --all` exits 0 on this host. This is the authoritative palette API.

`~/.config/omarchy/themed/*.tpl` is Omarchy's supported third-party template mechanism.
`omarchy-theme-set-templates` renders user templates before built-in ones into the staged theme
directory, so a `yoga-deck-osk.env.tpl` would appear as
`~/.local/state/omarchy/current/theme/yoga-deck-osk.env` after every theme change. It is genuinely
supported and needs no subprocess at spawn time, but it installs a file into the user's
configuration, and the rendered output does not exist until the *next* theme change — a fresh
install would have no palette until the user switches themes. It is a viable optimisation, not a
foundation.

The Omarchy Shell holds the live palette too, but it is pushed to Quickshell by
`omarchy-shell shell applyTheme`. There is no subscribe or query path back out.

**Decision.** Resolve the palette by running `omarchy theme color --all` once, at wvkbd spawn time.
Fall back to a minimal in-process `colors.toml` read for the handful of keys the mapping needs if
the command is missing or fails, and to a fixed neutral dark palette if that also fails. Theming
must never be able to prevent the keyboard from starting.

## Notification lifecycle

`omarchy-theme-set` calls `omarchy-hook theme-set "$THEME_NAME"` after the staged directory is
swapped in, after `theme.name` is written, and after the shell has accepted the transition. Hook
failures are tolerated by the runner (`bash "$hook" "$@" || echo "Hook failed"`), and
`~/.config/omarchy/hooks/theme-set.d/` accepts independent scripts from multiple owners. This is the
supported notification point and the one Yoga Deck should use.

The hook must not know anything about wvkbd. Yoga Deck already exposes a JSON line protocol on a
`0600` unix socket at `$XDG_RUNTIME_DIR/yoga-deck/shell.sock`; the hook runs as the same user, so a
one-line `theme_changed` command over that socket is the whole integration. When the runtime is not
running, the connection fails and the hook exits quietly.

Watching `~/.local/state/omarchy/current/` with inotify was considered and rejected. It works —
`theme` is replaced by `rm -rf` plus `mv`, and `theme.name` is written afterwards, so a
`CLOSE_WRITE` on `theme.name` is a reliable single trigger — but it depends on an implementation
detail of `omarchy-theme-set` rather than a published contract, and it adds a watch dependency for
an event that fires a few times a week.

## Whether Quickshell should participate

It should not. The Omarchy Shell receives the palette as a push and offers plugins no supported way
to observe theme changes or forward them outward; reaching the palette from QML would mean coupling
to shell internals, which this project's Omarchy rules forbid. Routing theme state through the
plugin would also invert Yoga Deck's transport, which today publishes status outward and accepts a
closed set of control commands, and it would make OSK appearance depend on the shell process being
alive. The `cos.yoga-deck` bar widget stays a status and control surface.

Replacing wvkbd with a Quickshell-native keyboard surface was also considered and rejected for now.
It would theme perfectly and retint live, but it would mean reimplementing layer-shell exclusive
zones, `zwp_virtual_keyboard_v1` key delivery, XKB modifier latching, compose sequences, swipe
input, and per-orientation layouts — and it would discard the Fcitx focus integration, the
signal-readiness gate, and the stuck-key filter that the current adapter has already proven on the
physical machine. Revisit only if wvkbd's layout or touch-target limits prove unfixable.

## Colour-role mapping

The obvious mapping — take wvkbd's surfaces from Omarchy's background ladder
(`darker_background`, `lighter_background`, `dark_background`) — was tested against all 22 bundled
themes and **fails on 10 of them**. Those keys are frequently derived by mixing rather than
authored, so the ladder collapses: `flexoki-light` yields a key-versus-board contrast of 1.02:1 and
`last-horizon` a special-versus-normal contrast of 1.02:1. Keys would be invisible against the
board.

Surfaces are therefore *derived* rather than read. Given `background`, lift toward whichever of
black and white has more headroom, choosing the smallest step that reaches a required contrast ratio
against the surface below. The direction is taken from headroom rather than from `mode` so that a
theme declaring a mode its background contradicts still separates; it agrees with `mode` on every
bundled theme.

### The surface ladder is built in the theme's own hue

The first version of this derivation lifted by *mixing toward pure white or black*. That works for
contrast and fails for identity. Mixing a colour toward a neutral endpoint reduces its chroma
monotonically, so every step taken away from the board to make a key visible also bleached it: the
result was a set of grey slabs sitting on a coloured board. On the seven bundled themes whose
background is achromatic the keyboard came out **literally grey**, which left it looking like a
light-mode/dark-mode switch rather than a themed surface.

Surfaces are now generated by moving *lightness* while holding hue and chroma.

- **Chroma**, here, is the span between a colour's strongest and weakest channel as a fraction of
  full scale. It is deliberately not HSL saturation: saturation is inflated near both lightness
  extremes — `ethereal`'s `#060b1e` spans 24 levels out of 255 yet reports a saturation of 0.67 —
  and preserving it turned `flexoki-light`'s cream board into a 53/255 tan key face.
- **Chroma opens up as the ladder rises**, by `SURFACE_TINT_GAIN` per rung. Theme authors do the
  same thing: `catppuccin` runs 16, 18, 21, 24 out of 255 from `base` to `surface2`, a gain of about
  1.15× per rung, and `catppuccin-latte` about 1.44×. Holding chroma flat instead flattens a faintly
  tinted board back to grey by the time it has been lifted twice.
- **A board with no hue of its own borrows the accent's**, at the much fainter `BORROWED_TINT`. This
  is what carries `matte-black` and `last-horizon` — grey boards belonging to unmistakably amber
  themes — onto the keyboard at all. A theme that chose a neutral board asked for a *related*
  keyboard, not a coloured one.
- **Tint is inherited, never invented.** When neither the board nor the accent has a hue, the
  keyboard is grey. `vantablack` and `white` are the two bundled themes where that is correct, and
  it is asserted as a property over every possible grey.

Lightness travel is bounded by the gamut, so each scan falls back to the original neutral mix if it
runs out of room. The contrast floors are guaranteed by that fallback, not by the ladder.

### The accent has to appear while nobody is touching the keyboard

Every role the accent reached in the first version — `--press`, `--press-sp` — is a *pressed* state,
visible for the length of a tap. A resting keyboard showed no theme colour at all, which is most of
what "the accents don't travel" means in practice.

`--text-sp`, the label colour on special keys, now takes the accent whenever it is legible there.
Shift, backspace, and the layer switches are large chrome glyphs, so `TEXT_FLOOR` is the applicable
gate for them rather than the 4.5:1 preference the lettered keys are held to. The accent reaches the
special-key labels on 21 of the 22 bundled themes; `rose-pine`'s own accent is 1.9:1 against its
special keys, so it falls back to the readable pool and lands on the theme's foreground.

A failing accent is also no longer discarded. The first version replaced it with a neutral lift from
the key face — throwing the theme's only colour away on exactly the low-contrast themes that had
least of it. It is now darkened or lightened in place instead: a darkened orange is still orange.

### Special-key shades are no longer aliases

`--press-sp` and `--swipe-sp` used to repeat `--press` and `--swipe` verbatim, so six of wvkbd's
thirteen roles carried distinct values. Each `-sp` role is now its own shade, nudged
`SPECIAL_SHADE_STEP` toward the board's lightness — special keys are recessed chrome in every other
role, so a pressed modifier reads a shade deeper than a pressed letter.

| wvkbd role | derivation |
|---|---|
| `--bg` | `background`, verbatim |
| `--fg-sp` | smallest lightness step from `--bg`, in the theme's hue, reaching 1.20:1 |
| `--fg` | smallest lightness step from `--fg-sp`, in the theme's hue, reaching 1.30:1 |
| `--press` | `accent`, falling back to `blue` then `foreground`; lightness-shifted in place if it fails 1.25:1 against the key face |
| `--press-sp` | `--press` recessed toward the board, then held to 1.25:1 against `--fg-sp` |
| `--swipe` | `selection`, else a lift from `--fg` *against* the ladder's direction; held to 1.25:1 against the key face |
| `--swipe-sp` | `--swipe` recessed toward the board, then held to 1.25:1 against `--fg-sp` |
| `--text-sp` | `accent` when it clears 3:1 on `--fg-sp`; otherwise the readable pool below |
| every other `--text*` | the first of `foreground`, `bright_foreground`, `light_foreground`, `background`, and both black and white that clears 4.5:1 against its own face; otherwise the most legible of those |

Labels stay themed: the ordered preference keeps `ethereal` on `#ffcead` and `catppuccin-latte` on
`#4c4f69` rather than collapsing every theme to black or white, while the 4.5:1 gate still forces a
pure fallback on the few themes whose foreground is too close to its key face.

Key labels render at `Sans 20` on a 1.25-scale display, comfortably past the WCAG large-text
threshold, so 3:1 is the applicable label floor; the derivation targets 4.5:1 and only falls back to
maximum contrast when no themed colour reaches it. Because both black and white close the candidate
pool, a legible label always exists: the better of the two is never below 4.58:1 against any face.

The keyboard is fully opaque unless the user asks otherwise. `[osk] opacity` sets the alpha byte
on the seven surface roles and leaves the six label roles at full, so a translucent keyboard keeps
crisp labels; `--alpha` is never used, for the reasons in [`settings.md`](settings.md). Every
contrast floor below is stated for an opaque keyboard, which is what the derivation controls. At a
lower opacity the surface separations shrink in proportion against whatever is behind the
keyboard, while the label floors — measured against a face drawn with cairo's `SOURCE` operator,
under a glyph drawn `OVER` it — hold unchanged.

### Verified results

Across all 22 bundled themes this mapping produces no check failures. Worst observed values:

| check | floor | worst | theme |
|---|---|---|---|
| key label on key face | 3.00:1 | 4.61:1 | `everforest` |
| special label on special face | 3.00:1 | 3.18:1 | `miasma` |
| pressed label on pressed face | 3.00:1 | 4.64:1 | `nord` |
| pressed special label on its face | 3.00:1 | 4.62:1 | `ethereal` |
| swipe label on swipe face | 3.00:1 | 5.30:1 | `rose-pine` |
| swipe special label on its face | 3.00:1 | 6.91:1 | `rose-pine` |
| special face vs board | 1.20:1 | 1.20:1 | `nord` |
| key face vs special face | 1.30:1 | 1.30:1 | `lupine` |
| pressed face vs key face | 1.25:1 | 1.99:1 | `rose-pine` |
| pressed special vs special face | 1.25:1 | 2.15:1 | `rose-pine` |
| swipe face vs key face | 1.25:1 | 1.25:1 | `catppuccin-latte` |
| swipe special vs special face | 1.25:1 | 1.25:1 | `hackerman` |

Key face versus board is not a floor of its own; it is the product of the two lifts, and lands at
1.58:1 on every theme.

Tint, measured as key-face chroma out of 255:

| | mixing toward white/black | ladder in the theme's hue |
|---|---|---|
| themes whose keyboard was achromatic | 7 of 22 | 2 of 22 (`vantablack`, `white`, correctly) |
| `catppuccin` | 13 | 26 |
| `ethereal` | 20 | 38 |
| `tokyo-night` | 10 | 18 |
| `catppuccin-latte` | 5 | 10 |
| `last-horizon` | 1 | 19 |
| `matte-black` | 0 | 20 |
| `gruvbox` | 0 | 20 |

Bundled themes split 17 dark and 5 light (`catppuccin-latte`, `flexoki-light`, `lupine`,
`rose-pine`, `white`). All 22 ship a `colors.toml`.

#### `ethereal` (dark, board chroma 24/255)

| flag | value | | check | ratio |
|---|---|---|---|---|
| `--bg` | `060b1e` | | key label on key face | 8.66:1 |
| `--fg` | `2b3351` | | special label on special face | 4.69:1 |
| `--fg-sp` | `191f37` | | pressed label on pressed face | 5.65:1 |
| `--press` | `7d82d9` | | pressed special label | 4.62:1 |
| `--press-sp` | `6e73ca` | | key face vs board | 1.58:1 |
| `--swipe` | `182149` | | special face vs board | 1.20:1 |
| `--swipe-sp` | `000417` | | special face vs key face | 1.31:1 |
| `--text` | `ffcead` | | pressed face vs key face | 3.58:1 |
| `--text-sp` | `7d82d9` | | pressed special vs special face | 3.84:1 |
| `--text-press` | `060b1e` | | swipe face vs key face | 1.25:1 |

Surface chroma runs 24 → 30 → 38 up the ladder, holding the board's hue.

#### `catppuccin-latte` (light, board chroma 6/255)

| flag | value | | check | ratio |
|---|---|---|---|---|
| `--bg` | `eff1f5` | | key label on key face | 11.78:1 |
| `--fg` | `bfc2c9` | | special label on special face | 3.61:1 |
| `--fg-sp` | `dadde2` | | pressed label on pressed face | 4.91:1 |
| `--press` | `1e66f5` | | pressed special label | 5.22:1 |
| `--press-sp` | `3377ff` | | key face vs board | 1.58:1 |
| `--swipe` | `d4d8e2` | | special face vs board | 1.20:1 |
| `--swipe-sp` | `f3f6ff` | | special face vs key face | 1.31:1 |
| `--text` | `000000` | | pressed face vs key face | 2.75:1 |
| `--text-sp` | `1e66f5` | | pressed special vs special face | 2.95:1 |
| `--text-press` | `ffffff` | | swipe face vs key face | 1.25:1 |

The board darkens rather than lightens, and `--press-sp` is *lighter* than `--press` because the
board is the light end here; recessed means "toward the board", not "darker".

#### `matte-black` (dark, board chroma 0/255 — the borrowed-hue case)

| flag | value | | check | ratio |
|---|---|---|---|---|
| `--bg` | `121212` | | key label on key face | 6.40:1 |
| `--fg` | `3e362a` | | special label on special face | 6.11:1 |
| `--fg-sp` | `292319` | | pressed label on pressed face | 7.35:1 |
| `--press` | `e68e0d` | | pressed special label | 6.05:1 |
| `--press-sp` | `d47e00` | | key face vs board | 1.58:1 |
| `--swipe` | `272727` | | special face vs board | 1.20:1 |
| `--swipe-sp` | `0c0c0c` | | special face vs key face | 1.31:1 |
| `--text` | `bebebe` | | pressed face vs key face | 4.67:1 |
| `--text-sp` | `e68e0d` | | pressed special vs special face | 5.03:1 |
| `--text-press` | `121212` | | swipe face vs key face | 1.26:1 |

A pure `#121212` board with an amber accent: the key faces take the accent's hue at
`BORROWED_TINT`, reaching a chroma of 20/255. `--swipe` stays grey because this theme's `selection`
is grey and `selection` is used verbatim when a theme provides one — a transient trail is not worth
overriding an authored colour for.

### Missing keys and hostile input

`background`, `foreground`, and `mode` are the only required keys, and `omarchy theme color`
guarantees all three: `background` falls back to `color0`, `foreground` to `color7`, and `mode`
resolves through `theme_type`, a `light.mode` marker file, background luminance, and finally `dark`.
Every other role is derived. A palette value that is not a `#rrggbb` literal — Omarchy permits
`rgb()`, `rgba()`, and gradient specifications — is treated as absent and falls through to
derivation, so a theme cannot inject an unparsed string into wvkbd's argument vector.

## Refresh policy

Because colours are argv-only, resolving the palette at spawn time makes most of the problem
disappear. wvkbd only exists while folded, so a theme change in laptop mode needs no handling at
all: the next fold spawns with the current palette.

The remaining case is a theme change while folded. Respawning is safe for focus — the Fcitx
virtual-keyboard bus name is owned by the Yoga Deck runtime, not by wvkbd, so the child can be
replaced without releasing `org.fcitx.Fcitx5.VirtualKeyboard`, losing the client's input context, or
re-running the focused-context replay. Only the layer surface is torn down and remapped.

Respawning is *not* safe while a key is held. The stuck-key failure already recorded in
[`on-screen-keyboard.md`](on-screen-keyboard.md) — a surface removed between `touch_down` and
`touch_up` leaves the key logically pressed and repeating — applies identically to a palette
respawn, and this design must not reintroduce it. Behaviour is therefore gated on visibility:

- **folded, hidden:** respawn immediately with the new palette, keeping the bus name, re-running the
  signal-readiness gate, and leaving the child hidden.
- **folded, visible:** record the palette as pending and respawn at the next Hide. A visible
  keyboard is never torn down underneath a finger.
- **unfolded:** nothing to do; the palette is read at the next spawn.
- **theme change during the fold or unfold transition:** the coordinator already serialises
  compositor mutations by sequence number, and the palette respawn is subordinate to OSK intent — a
  pending respawn is discarded when the reducer's `SetOskEnabled` intent changes.

This is implemented. The adapter tracks visibility from the signals it actually delivers, and
serialises every process replacement — including the deferred one, which Fcitx's synchronous hide
callback can only schedule — behind a single transition lock, so a respawn can never interleave with
a fold or unfold. A retint that fails leaves no child, which the runtime's periodic reconciliation
restarts; stale colours are never worth propagating an error out of a hide callback.

## Installation

The hook is not installed automatically:

```bash
omarchy hook install theme-set hooks/theme-set.d/yoga-deck
```

Its source is `hooks/theme-set.d/yoga-deck`. It knows nothing about wvkbd, forwards neither the
theme name in `$1` nor anything else, and exits 0 when the runtime is absent, the socket is
unreadable, or `python3` is missing — a theme change must never fail because of Yoga Deck. Without
it the keyboard still follows the theme, just at the next fold rather than immediately.

## Test requirements

Unit tests, against fake spawner and fake palette source: derivation output for a dark and a light
fixture palette; the contrast floors above asserted as properties over generated palettes; missing,
malformed, and non-hex values falling through to derivation; `omarchy theme color` absent, failing,
timing out, and returning garbage all yielding a usable palette and a spawn; the resolved palette
appearing in the spawn argument vector alongside the existing sizing flags.

Tint tests, unit and property: surfaces hold the board's hue and open its chroma up the ladder; a
neutral board borrows the accent's hue; a theme with no hue anywhere yields no hue on the keyboard,
asserted over every possible grey; special-key labels carry the accent, and fall back to the
readable pool when it is not legible on them; `--press-sp` and `--swipe-sp` are distinct from their
normal-key counterparts and sit nearer the board; an illegible accent is lightness-shifted rather
than replaced by a neutral; `to_hsl` and `from_hsl` round-trip exactly.

Integration tests over the installed themes, marked `integration` and skipped when Omarchy is not
present: every bundled theme resolved through the real `omarchy theme color` cascade clears every
floor above, and every theme with a colour anywhere puts one on the *resting* keyboard within 8° of
its own hue. The floors alone would not have caught the bleaching this work fixed — a fully grey
keyboard passes all of them — so the hue check is the actual regression guard.

Adapter tests: a palette change while hidden respawns the child, re-runs readiness, and does *not*
call `stop` on the visibility host; a palette change while visible defers until Hide and then
respawns; a pending respawn is discarded when the OSK is disabled; a theme change with no process
running is a no-op. Two more were added while implementing: repeated changes before a Hide respawn
once at the newest palette, and a failed retint leaves the adapter recoverable by reconciliation.

Reducer tests, pure: a theme change plans a refresh only while the OSK is wanted, plans nothing in
laptop mode, and never alters posture, input safety, or orientation. The last two are also asserted
as properties over generated event sequences. A repeated theme change is deliberately *not*
idempotent — unlike a sensor reading, it is a notification that something changed, and two of them
are two real changes.

Integration tests: the `theme-set` hook script reaching the transport socket; the transport
accepting `theme_changed` and rejecting it with a value, matching the existing `_parse_command`
discipline; the hook exiting cleanly when no runtime is listening. The hook is exercised as the
shell script Omarchy actually runs, under a redirected `XDG_RUNTIME_DIR`, never against the live
session's socket.

Live-desktop tests, marked `live_desktop` and requiring explicit authorisation: that a wvkbd child
can in fact be replaced while the Fcitx bus name is held without the client losing its input
context, and that Hyprland remaps the new layer surface at the same geometry. This was the one
load-bearing assumption in the design that had not been observed on the physical machine. It has
since been verified — see [`osk-theme-respawn-capture.md`](osk-theme-respawn-capture.md) for the
protocol and [`results/`](results/) for the measured runs.

Hardware tests, marked `hardware`: a theme switch while folded and hidden, then a switch while
folded and visible, confirming no stuck key and no lost focus in each case; both across a dark/light
boundary, since a light theme is the case where a wrong palette is most visible. The unfolded
respawn is covered by [`osk-theme-respawn-capture.md`](osk-theme-respawn-capture.md); the folded
cases still require the runtime to drive the respawn and remain open.

## Deferred

Rendering the palette through a `~/.config/omarchy/themed/yoga-deck-osk.env.tpl` template removes
the spawn-time subprocess, but it installs a file into the user's configuration and is stale until
the first theme change after install. Revisit if spawn latency is measured to matter.
