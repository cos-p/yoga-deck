# Coordinated rotation contract

Yoga Deck owns rotation only for the initial X390 Yoga targets:

- internal output: `eDP-1`;
- touchscreen: `wacom-pen-and-multitouch-sensor-finger`;
- pen tablet: `wacom-pen-and-multitouch-sensor-pen`.

Discovery uses `hyprctl -j monitors all` and `hyprctl -j devices` on every transition.
All three exact targets must exist or no mutation is attempted. Similarly named external and
passthrough devices are excluded.

Hyprland 0.56 uses Lua configuration. The runtime submits one evaluation containing partial
updates for the internal monitor and both input devices. The monitor update contains only
`output` and `transform`; touch and pen updates contain only `name`, `transform`, and
`output`. Resolution, refresh rate, position, fractional scale, color management, VRR,
reserved areas, and external monitor rules are never reconstructed by Yoga Deck.

The transform mapping is normal `0`, right `3`, inverted `2`, and left `1`. This follows the
Wayland transform numbering documented by Hyprland (where transform 1 is 90° CCW and transform 3
is 90° CW). Sensor mount direction, physical touch/pen alignment, and external monitor preservation
were validated on the host on 2026-08-26. See
[`results/orientation-alignment-2026-08-26.md`](results/orientation-alignment-2026-08-26.md).



Rotation intents are serialized by monotonically increasing sequence number. A stale or
duplicate effect cannot overwrite a newer transform. Compositor failure produces only the
stable `rotation_transaction_failed` diagnostic; periodic reconciliation rediscovers all
three targets and reapplies the latest desired orientation.

References: [Hyprland monitor configuration](https://wiki.hypr.land/Configuring/Basics/Monitors/)
and [Hyprland per-device configuration](https://wiki.hypr.land/Configuring/Advanced-and-Cool/Devices/).
