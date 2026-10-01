# OSK theme respawn — 2026-08-29

Target: ThinkPad X390 Yoga described in [`../host-baseline.md`](../host-baseline.md), running
Omarchy 4.0.0-1, Hyprland 0.56.2, Fcitx 5.1.21, and wvkbd 0.20-1 on a single `eDP-1` at scale 1.25.
Protocol: [`../osk-theme-respawn-capture.md`](../osk-theme-respawn-capture.md).

## Result

The assumption in [`../osk-theming.md`](../osk-theming.md) holds. Replacing the owned
`wvkbd-mobintl` child while Yoga Deck still holds `org.fcitx.Fcitx5.VirtualKeyboard` preserved the
client's input context in every run. The bus name was never released, its owner never changed,
Fcitx stayed in `virtualkeyboard` across the swap, and Hyprland remapped the new layer at the same
settled geometry, `1536x280+0+584`.

| Run | Child replaced | Bus retained | Owner unchanged | Layer remapped | Geometry unchanged | Fcitx UI unchanged | Client hide | Remap |
|---|---|---|---|---|---|---|---|---:|
| 1 | yes | yes | yes | yes | *not measured* | yes | yes | 36.4 ms |
| 2 | yes | yes | yes | yes | yes | yes | yes | 243.2 ms |
| 3 | yes | yes | yes | yes | yes | yes | yes | 272.2 ms |

The operator observed run 3 directly and reported seeing the dark palette, seeing it change to the
light one with no flicker, typing successfully afterwards, and no stuck or repeating key. That
observation is the only evidence for the stuck-key and key-delivery half of the criteria; the
campaign does not and must not read typed text.

## Measurement notes

Run 1's `geometry_unchanged` is reported as not measured rather than as a failure. It recorded
`1536x838+0+26` after the respawn, which is the full area below the bar: a respawned layer surface
is briefly mapped at that size before it commits its exclusive zone, and the campaign sampled the
first geometry Hyprland reported rather than a settled one. Runs 2 and 3 wait for two consecutive
identical readings and both report the correct `1536x280+0+584`. The same stale rectangle also
widened run 1's second screenshot past the keyboard; that capture was destroyed and the campaign
now re-reads the rectangle at capture time and refuses any layer taller than half its output.

The two figures are therefore not comparable. Run 1's 36.4 ms is time to first map. Runs 2 and 3
measure time to *settled* geometry, and the stability detector's two 100 ms polls put a floor under
that, so 243 ms and 272 ms are upper bounds on remap cost rather than latency measurements. What
can be claimed is that the surface reappears in well under a second and, at a 5 s dwell, the
transition read as clean to the operator.

Runs 1 and 2 used no dwell, and the first palette was consequently on screen for only a few hundred
milliseconds — long enough for the capture but too brief for the operator to see, who reported the
keyboard appearing only in white. The `01-before-respawn.png` captures from both runs show the
Ethereal palette, confirming the first child did launch with it. `--dwell` was added so a guided
test is actually observable by the human guiding it; run 2's capture also shows the wallpaper
faintly through the keys because grim fired during Hyprland's layer fade-in, which the dwell fixes.

## Scope and what remains open

These runs were performed **unfolded**. `yoga-deck.service` claims the same bus name on entering
tablet mode, and the campaign refuses to start when the name is already owned, so a folded run is
not a variation of this test — it has to be driven through the runtime. The folded hidden and
folded visible theme switches described in `osk-theming.md` therefore remain unverified, as does
the deferral policy that holds a respawn until the next Hide. Those belong to the live-retint work.

No theme was applied, no package-owned file was touched, no Hyprland configuration was changed, and
Omarchy's Fcitx retained input-method ownership throughout. Every run released the bus name and
left no `wvkbd` process behind.
