# Tablet-mode on-screen keyboard

## Contract

Yoga Deck treats `SW_TABLET_MODE` as the authority for OSK lifecycle. Entering folded mode plans
both internal-input inhibition and `SetOskEnabled(true)` at the same reducer sequence. Returning to
laptop mode plans internal-input recovery and `SetOskEnabled(false)`. Unknown switch values retain
the last safe OSK state, duplicate events are idempotent, reconciliation reasserts current intent,
and shutdown stops the OSK as well as recovering internal inputs.

The adapter owns exactly two resources while folded:

1. one `wvkbd-mobintl` child process, initially hidden;
2. the session-bus name `org.fcitx.Fcitx5.VirtualKeyboard`, exporting Fcitx's virtual-keyboard UI
   interface at `/org/fcitx/virtualkeyboard/impanel`.

Omarchy's existing Fcitx instance remains the sole `input-method-v2` owner. Fcitx already observes
native Wayland and toolkit input contexts; it calls `ShowVirtualKeyboard` when an editable context
gains focus and `HideVirtualKeyboard` when it loses focus. Yoga Deck translates only those calls to
`SIGUSR2` and `SIGUSR1` for its owned wvkbd child. It does not inspect window titles, guess from
global focus, stop Fcitx, run configured shell commands, or inject a synthetic kernel device.

wvkbd starts with `--hidden`. Before exporting the Fcitx interface, Yoga Deck waits until the owned
process has blocked `SIGUSR1` and `SIGUSR2` in `/proc/<pid>/status`; that is the observable point at
which wvkbd 0.20 has installed its `signalfd`. This prevents the early `SIGUSR2` race seen on the
physical machine without flashing a keyboard on every fold. Thereafter the client toolkit asks Fcitx
to show the keyboard when an editable field needs an input panel and to hide it when it does not.
When tablet mode begins with a field already focused, Fcitx 5.1.21 does not replay its prior show
state after a new virtual-keyboard UI registers. Yoga Deck therefore reads only the `focus:1` token
from Fcitx's in-memory `DebugInfo` reply, discards the entire reply without logging it, and asks
Fcitx's public `/virtualkeyboard` service to replay Show after registration. No program names, input
context identifiers, or other debug fields are retained. If no context is focused, wvkbd stays hidden
until a client requests it normally.

Fcitx may also emit Hide when a wvkbd-generated key changes its input mode. Acting on that request
immediately can destroy wvkbd's layer surface between `touch_down` and `touch_up`, leaving its key
logically pressed and repeating. Yoga Deck defers Hide for 50 ms, then checks Fcitx's semantic UI
state. If a context is still focused and wvkbd's key has switched Fcitx to `classicui`, Yoga Deck
replays Show through Fcitx without removing the layer. A genuine client Hide leaves Fcitx in
`virtualkeyboard`, so Yoga Deck forwards it even when an application keeps a broad input context
focused. A new Show cancels the pending check, and tablet exit always stops the owned process.

Colours are not configured here. wvkbd 0.20 takes its palette only from `argv`, so the adapter
resolves the active Omarchy theme at every spawn and derives the key, surface, and label roles from
it; there is no Yoga Deck theme to set. Following a theme change *live* requires respawning the
child, which is gated on visibility: hidden respawns immediately, visible defers to the next Hide so
no touch surface is destroyed mid-press, and unfolded does nothing because the next fold reads the
current theme anyway. The Fcitx bus name is held by Yoga Deck rather than wvkbd, so the replacement
keeps the client's input context. See [`osk-theming.md`](osk-theming.md).

The initial X390 sizing profile is `-L 280 -H 360 --fn "Sans 20"`. It is the default rather than a
constant: `landscape_height`, `portrait_height`, `font`, `corner_radius`, `key_popup`, and
`highlight` are settable under `[osk]` in `~/.config/yoga-deck/config.toml`, re-read at every spawn,
and `WVKBD_SIZING_ARGS` is rendered from those defaults so the two cannot drift apart. See
[`settings.md`](settings.md). At the observed scale of 1.25,
the four-row landscape layout is 350 physical pixels high and provides approximately 87.5 physical
pixels (about 13 mm) per row before borders. It occupies 32% of the 864-logical-pixel landscape
height. The five-row portrait layout provides approximately 90 physical pixels per row. These are
deliberately comfortable starting targets based on the 7–10 mm Material touch-target recommendation
and the proportions of current Gboard/iPad tablet keyboards; final dimensions still require repeated
finger and pen measurements on the device.

The protocol does not identify whether touch, pen, mouse, or another device caused focus. A
compatible field can therefore show the OSK through any focus path while folded. Yoga Deck limits
this to tablet posture by not owning the Fcitx UI name in laptop mode. Clients without a usable
Fcitx input context may not show it automatically and must be measured rather than guessed.

## Host prerequisite and removal

wvkbd is an external package, not a Python dependency. On the initial Arch host, version 0.20-1 was
installed from the AUR after inspecting the PKGBUILD and validating its SHA-512 source checksum:

```bash
omarchy pkg aur add wvkbd
```

The AUR build required Arch's `scdoc` package to generate the manual page; it was removed after the
build because wvkbd does not need it at runtime. Neither an Fcitx addon nor a GNOME accessibility
setting is required. To remove wvkbd after disabling Yoga Deck's OSK runtime:

```bash
omarchy pkg drop wvkbd
```

## Validation status

Unmarked tests cover reducer policy, paired same-sequence effects, stale transition rejection,
process ownership, signal routing, registration rollback, and redacted failures. On 2026-08-28,
Fcitx 5.1.21 accepted the service, selected its `virtualkeyboard` UI, and automatically mapped wvkbd
for a controlled focused Ghostty/GTK4 text field under Hyprland 0.56.2. A later physical fold exposed
both an already-focused-field gap and an early-signal process race. Regression coverage now requires
hidden startup with signal-readiness gating before focus integration. A live folded retest then
confirmed that posture, process startup, and bus ownership all succeeded but the already-focused
client emitted no Show request after a tap. This added a focused-context replay through Fcitx itself;
a physical retest then showed the 1536×280 layer immediately on two folds from the focused field.
Fcitx also drove several subsequent hide/show transitions, and both unfolds removed the process,
owner, and layer. Those transitions exposed a stuck-key failure: Hide removed the touch surface
before release, so the pressed key kept repeating. An initial focus-only filter kept the layer stable
while typing but also suppressed a genuine Hide from non-text UI. The revised filter distinguishes
Fcitx's `classicui` key misclassification from a client Hide in `virtualkeyboard`. Its physical
retest passed: typing repeatedly switched modes without unmapping the layer, genuine client Hide
removed it, refocusing restored it, and no key remained pressed. The filter does not remove the
underlying race: wvkbd 0.20 does not release a held key when it hides, and Hyprland by default does
not release a destroyed virtual keyboard's keys. A genuine Hide that lands before `touch_up` can
still strand a key. See the 2026-09-30 source analysis in
[`results/osk-live-retint-2026-08-29.md`](results/osk-live-retint-2026-08-29.md). The earlier visible backend
remained running and Hyprland mapped its 1536×120 logical surface while
folded, but its four roughly 30-logical-pixel rows were not usable enough for touch. The user
nevertheless confirmed that touch typing reached the active application. The test content was not
retained in repository evidence. A one-off wvkbd 0.20 teardown segfault led Yoga Deck to stop only
its owned, stateless child with `SIGKILL`; this skips the faulty Wayland teardown path and cannot
affect another wvkbd process.

One lifecycle limitation remains: if tablet exit removes the UI owner while Fcitx is still in
on-screen-keyboard mode, Fcitx 5.1.21 can report no current UI instead of immediately selecting
`classicui`. Input delivery continues, and the next tablet entry still works, but Yoga Deck does not
claim complete Fcitx UI restoration. Source analysis found no supported non-synthetic restoration
call in this version; the safe fallback and rejected workarounds are recorded in
[`results/fcitx-ui-restoration-analysis-2026-08-29.md`](results/fcitx-ui-restoration-analysis-2026-08-29.md).

Live theme following is verified only for the unfolded respawn, by the live campaign in
[`osk-theme-respawn-capture.md`](osk-theme-respawn-capture.md): the bus name, its owner, the Fcitx
UI, and the settled layer geometry all survived, and the operator confirmed a flicker-free dark to
light change with no stuck keys. The folded cases — hidden, visible, and the deferral until Hide —
are covered by unit tests but have not been observed on the physical machine, because the campaign
had to run unfolded while `yoga-deck.service` holds the same bus name in tablet mode.

The remaining live campaign must verify pen key delivery, GTK, Qt, Chromium/Electron, XWayland,
rotation, rapid focus changes, repeated fold/unfold, suspend/resume, and backend failure. Results must
contain only app categories, timings, and outcomes, never entered text or personal content.
