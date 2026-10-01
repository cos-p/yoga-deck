# OSK theme respawn capture

wvkbd 0.20 takes its palette only from its argument vector, so following an Omarchy theme change
while the keyboard exists means replacing the child process. [`osk-theming.md`](osk-theming.md)
argues from resource ownership that this is safe for focus: the Fcitx virtual-keyboard bus name is
owned by the Yoga Deck runtime, not by wvkbd, so the client's input context should survive. This
campaign observes that claim instead of assuming it.

## What the campaign owns

It owns exactly the two resources the real adapter owns — one `wvkbd-mobintl` child and the
`org.fcitx.Fcitx5.VirtualKeyboard` name — and refuses to start if something already holds that
name. It applies no theme, writes no configuration, changes no Hyprland setting, and leaves
Omarchy's Fcitx as the sole `input-method-v2` owner. The second palette is derived from a bundled
theme's `colors.toml` and passed only as process arguments.

```bash
YOGA_DECK_HARDWARE=1 YOGA_DECK_LIVE_DESKTOP=1 .venv/bin/pytest -m 'hardware and live_desktop' \
  tests/hardware/test_osk_theme_respawn.py -s
```

Or directly, which is the form used for a monitored run:

```bash
.venv/bin/yoga-doctor test-osk-theme --live --theme white --dwell 5 --output <dir> --json
```

## Operator protocol

Run it **unfolded**. `yoga-deck.service` claims the same bus name on entering tablet mode, and the
campaign refuses to start rather than contend with it. Folding during a run is therefore not a
variation of this test; it is a different test that has to go through the runtime.

Focus an empty, throwaway text field when prompted. The first palette is held for `--dwell`
seconds, the child is then replaced with the second palette and held again, after which the
operator types a few characters and clicks away to release focus. `--dwell` exists because the
respawn otherwise completes in a few hundred milliseconds: without it the first palette is gone
before an operator can see it, and screenshots catch Hyprland's layer fade-in.

## Evidence discipline

Screenshots are cropped to the wvkbd layer rectangle, re-read immediately before each capture, and
refused outright when the layer is taller than half its output. Both guards exist because a
respawned layer surface is briefly mapped at the full area below the bar before it commits its
exclusive zone; reusing that pre-commit rectangle photographs the operator's whole screen. The
campaign records only layer geometry, process identity, bus ownership, and Fcitx's `CurrentUI`.
It never requests `DebugInfo`, reads window titles, or retains typed text.

`geometry_unchanged` compares settled geometry on both sides. `remap_ms` is measured to a *settled*
geometry, and its resolution is bounded below by the stability detector's two 100 ms polls, so it
is an upper bound on remap cost rather than a latency measurement.

Results are recorded in [`results/`](results/).
