# OSK live retint, folded — 2026-08-29

Target: ThinkPad X390 Yoga described in [`../host-baseline.md`](../host-baseline.md), running
Omarchy 4.0.0-1, Hyprland 0.56.2, Fcitx 5.1.21, and wvkbd 0.20-1 on a single `eDP-1` at scale 1.25.

This is the folded companion to [`osk-theme-respawn-2026-08-29.md`](osk-theme-respawn-2026-08-29.md),
which had to run unfolded and therefore left every folded case open. Here the respawn is driven by
the runtime itself, through the installed `theme-set` hook, with `yoga-deck.service` holding
`org.fcitx.Fcitx5.VirtualKeyboard` exactly as it does in normal use.

## Protocol

The service was restarted onto the current source and the hook installed with
`omarchy hook install theme-set hooks/theme-set.d/yoga-deck`. The operator folded the machine and
focused a browser tab rather than a terminal, so that no editable field was focused and the keyboard
stayed hidden. Theme switches were issued as ordinary `omarchy theme set` commands; nothing in the
campaign talks to wvkbd, the socket, or the hook directly. Observation was limited to the child's
argument vector, the bus name's owner, Fcitx's current UI, and whether a `wvkbd` layer was mapped.

Baseline theme was `last-horizon` (dark). The switch target was `catppuccin-latte` (light), chosen
because a light palette is the case where a wrong or stale result is most visible.

## Result

All three folded cases in [`../osk-theming.md`](../osk-theming.md) behave as designed.

| Step | Child | `--bg` | `--press` | Layer | Bus owner |
|---|---|---|---|---|---|
| Baseline, `last-horizon` | A | `0c0b0c` | `b59790` | hidden | unchanged |
| → `catppuccin-latte`, **hidden** | B | `eff1f5` | `1e66f5` | hidden | unchanged |
| Client show | B | `eff1f5` | `1e66f5` | mapped | unchanged |
| → `last-horizon`, **visible** | B | `eff1f5` | `1e66f5` | **stayed mapped** | unchanged |
| Client hide, deferral fires | C | `0c0b0c` | `b59790` | hidden | unchanged |

**Hidden.** The switch respawned the child immediately. The old child was reaped, exactly one
`wvkbd` remained, and the new argument vector carried the Latte palette.

**Visible.** The switch did *not* respawn. Across six samples over roughly two seconds the child pid
and the mapped layer were both unchanged while the rest of the desktop had already retinted, leaving
a light keyboard on a dark desktop. This window is the point of the design: the touch surface is
never destroyed while a finger may be on it.

**Hide.** The pending respawn fired on the client's next Hide. The hide and the respawn both landed
inside a single 0.4 s poll, consistent with the sub-second timing measured unfolded.

Across all three respawns the bus name was never released and its owner never changed, Fcitx stayed
in `virtualkeyboard`, posture stayed `tablet`, and internal inputs stayed inhibited. Child C's
palette is byte-identical to baseline child A's, so the round trip is exact.

A later show/hide pair with no theme change pending produced no respawn, confirming the pending flag
is cleared rather than retinting on every Hide.

## Operator observation

The operator reported that **one key appeared to stick for a few seconds**, and could not reproduce
it on further attempts. This is recorded as an open failure, not as noise: it is the same symptom
class the deferral exists to prevent, and it was observed in the first folded session of this
feature. No timestamp was captured for it, so it cannot be correlated with a specific respawn.

Two mechanisms are possible and neither is excluded:

1. The pre-existing one in [`../on-screen-keyboard.md`](../on-screen-keyboard.md): a genuine client
   Hide removes the touch surface between `touch_down` and `touch_up`. That path is unchanged by
   this work.
2. One introduced by this work, found by reading the code after the report rather than by
   reproduction. `_hide` sent `SIGUSR1` and scheduled the retint respawn on the very next event-loop
   iteration. `SIGUSR1` is asynchronous, so wvkbd had not necessarily processed the hide, or
   released a key still logically held, before the respawn sent `SIGKILL`. Destroying the
   virtual-keyboard object with a key down leaves the compositor no release to deliver, and
   Hyprland's repeat is configured at rate 40 / delay 250, so a stranded key would repeat rather
   than sit silent — which matches the symptom.

The second was fixed by settling for `retint_settle` (default 250 ms) after the hide before
replacing the child, with unit coverage for both the settle and for a keyboard reshown during it,
which defers the respawn again instead of tearing the surface back out. **This is a mitigation for a
plausible mechanism, not a demonstrated fix for the observed symptom.** The symptom remains
unreproduced and it remains an open item.

The operator did not report on whether the palette transition itself looked clean.

### Source analysis, 2026-09-30 (no hardware)

A later read of the upstream wvkbd 0.20 source and of the installed Hyprland 0.56.2 narrows the
stuck key to one root cause that both mechanisms share, and shows the `retint_settle` mitigation
cannot close it:

- wvkbd's `hide()` destroys the layer surface and clears `layer_surface_configured` without calling
  `kbd_unpress_key`. Every touch handler returns early while that flag is false, and
  `wl_touch_cancel` is empty. A key pressed at `touch_down` is therefore never released by wvkbd if
  the Hide lands before `touch_up`; the release is sent only by the next `touch_down` after a later
  Show.
- Hyprland 0.56.2 can release a destroyed virtual keyboard's held keys, but only when
  `input:virtualkeyboard:release_pressed_on_close` is true. It is false by default, and a read-only
  `hyprctl getoption` on this host reported `false` and not set. Killing wvkbd with a key held
  therefore sends no release either.

The trigger is a genuine Hide arriving while a finger is still down. wvkbd sends a key press at
`touch_down`, so a key that moves focus out of a text field (Enter in a search or address bar, for
example) starts the Fcitx hide path, which waits only `hide_focus_delay` (50 ms) plus two D-Bus
round trips. That is shorter than a normal tap. No theme change is needed. With a retint pending,
the respawn then removes the only process that could have sent the late release. The settle helps
only when `touch_up` was already queued before wvkbd handled `SIGUSR1`.

The client keeps repeating the stranded key until another key is pressed, the same key is pressed
again, or keyboard focus moves. That matches "stuck for a few seconds, then gone". This remains
source analysis, not a reproduction. The fix candidates are enabling
`release_pressed_on_close` in the user's Hyprland configuration, which makes both the retint
respawn and the unfold kill clear held keys, and a hide grace longer than a typical tap. Both need
a bounded folded campaign before anything is claimed.

## Scope and what remains open

Only the dark-to-light and light-to-dark pair was exercised, on one host, in landscape. Rotation,
rapid repeated theme switches, and a theme change arriving during the fold or unfold transition
itself were not tested; the last is covered by unit tests asserting a pending respawn is discarded
when OSK intent changes, but has no physical observation.

No package-owned file was touched and no Hyprland configuration was changed. The desktop was left on
its original `last-horizon` theme. The `theme-set` hook remains installed, which is a deliberate
change to the live desktop made with the operator's authorization.
