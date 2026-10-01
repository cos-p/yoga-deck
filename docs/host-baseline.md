# Initial Target Host Baseline

Snapshot date: 2026-08-26. Verify volatile details before relying on them.

| Item | Observed value |
|---|---|
| Model | Lenovo ThinkPad X390 Yoga `20NQS1TV00` |
| OS | Omarchy 4.0.0-1, Arch Linux based |
| Compositor | Hyprland 0.56.2 |
| Internal display | `eDP-1`, 1920×1080 at approximately 60 Hz, scale 1.25 |
| Graphics | Intel UHD Graphics 620 using i915 |
| Lid accelerometer | `iio:device0`, `accel_3d` |
| Gyroscope | `iio:device1`, `gyro_3d` |
| Base accelerometer | `iio:device2`, `accel_3d` |
| Hinge sensor | `iio:device3`, `hinge` |
| Tablet switch | `ThinkPad Extra Buttons`; observed as event9, but event numbers are unstable |
| Touchscreen | `wacom-pen-and-multitouch-sensor-finger` |
| Pen | `wacom-pen-and-multitouch-sensor-pen` |
| Internal keyboard | `at-translated-set-2-keyboard` |
| Touchpad | `synps/2-synaptics-touchpad` |
| TrackPoint | `tpps/2-alps-trackpoint` |

The Wacom touch and pen nodes share a libinput device group. The pen node exposes pen/button/absolute-axis capabilities, but a distinct silo insertion/removal switch has not been verified.

At snapshot time, `iio-sensor-proxy` and Python `evdev` were not installed. Do not install packages merely to satisfy this document; implementation tasks should identify and authorize dependencies explicitly.

An OSK-specific check on 2026-08-28 found Fcitx 5.1.21 running for Omarchy's input-method support.
wvkbd 0.20-1 was then installed from the AUR with its source checksum verified; the temporary `scdoc`
build dependency was removed afterward. Fcitx accepted a Yoga Deck-owned virtual-keyboard UI service,
automatically showed wvkbd for a controlled focused Ghostty text field, and hid it on request. Finger
and pen key delivery and the full application matrix remain volatile live-validation work. A physical
fold later exposed an already-focused-field gap and an early `SIGUSR2` race that terminated hidden
wvkbd before its handler was ready. The adapter now starts hidden and waits for the process signal
mask before registering with Fcitx. A controlled live request mapped a 1536×280 logical surface,
then hid it again; ten additional hidden start/stop cycles left no process, bus owner, or new
coredump. The earlier folded touch test delivered text from the cramped 1536×120 surface, but the
new dimensions and focus-only behavior still require a physical folded finger/pen check. Unfolding
must continue to restore Fcitx `classicui` and internal inputs.

A subsequent folded tap captured the focus edge precisely: the runtime reported tablet posture,
wvkbd was alive, and the virtual-keyboard bus name was owned, but no layer appeared because the
already-focused client sent no fresh Show request. Fcitx's 5.1.21 implementation resets virtual
keyboard visibility when a new UI owner appears and does not replay current focus. The adapter now
checks only whether Fcitx has a focused input context and replays Show through Fcitx after UI
registration. Two subsequent physical folds from the focused field mapped the 1536×280 layer
immediately. Fcitx then drove observable hide/show transitions, and unfolding removed the child,
owner, and layer. A touch during those transitions remained logically pressed and repeated because
the Hide destroyed wvkbd's surface before `touch_up`. The adapter now confirms that Fcitx has actually
kept `virtualkeyboard` before forwarding Hide. A wvkbd key instead changes Fcitx to `classicui`, so
that case restores OSK mode without removing the surface. An earlier focus-only filter prevented the
repeat and kept the layer mapped while typing, but also prevented a genuine non-text Hide; physical
confirmation of the UI-state distinction then passed. Typing kept the 1536×280 layer mapped across
multiple `classicui`/`virtualkeyboard` mode transitions, genuine non-text Hide removed it, refocusing
restored it, and the submitted text contained no stuck repeat. Unfolding stopped the child with no
new coredump. Fcitx subsequently reported an empty current UI rather than `classicui`; that remaining
cleanup defect is tracked separately from the resolved touch-release safety bug.

Privacy note: do not add serial numbers, hardware addresses, network identities, unique IDs, personal paths, clipboard contents, screenshots, or raw personal logs to this file or public fixtures.
