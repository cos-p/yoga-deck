# Near-button application test

`yoga-doctor test-near-button --live` compares a normal-pen control with the X390 Yoga
pen's side button nearest the tip. It opens a temporary, non-keyboard-focused Quickshell
surface on `eDP-1` and observes physical input without injecting events or changing Hyprland.

The control phase requires the pen to start well outside proximity with both buttons released.
The user brings the normal tip in, makes one short stroke, and moves it well away. The near-button
phase likewise starts outside proximity; the user holds the near button before bringing the pen
in, makes a short stroke, moves it away, and only then releases the button. Moving out of proximity
between conditions prevents the prior tool type from carrying into the next observation.

Two independent sources are correlated by bounded time slot:

- evdev normalizes only `BTN_TOOL_PEN` and `BTN_TOOL_RUBBER` lifecycles;
- the surface records only Qt `PointerDevice.Pen` and `PointerDevice.Eraser` delivery.

A near-button trial is confirmed only when a complete raw eraser lifecycle and an application
eraser observation occur in the same slot. A publishable campaign must also observe the normal-pen
control at both raw and application boundaries. Results contain counts and elapsed timing only;
they omit coordinates, device paths, unique identifiers, application content, and raw traces.

The test is marked both `hardware` and `live_desktop`. The surface closes automatically, takes no
keyboard focus, and its temporary files are deleted when the command exits.
