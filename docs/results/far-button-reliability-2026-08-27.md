# Far pen-button reliability measurement — 2026-08-27

## Scope

This guided hardware campaign measured the X390 Yoga pen's side button farthest from the tip.
Five separately synchronized trials were run. For each trial, the observation window began before
the user was prompted to make exactly one hover press/release and ended only after the capture
window completed.

The protocol retained only public condition labels and normalized event-cycle counts. It retained
no device path, hardware identifier, coordinates, application state, clipboard content, screenshot,
or raw event trace. The measurement did not inject input, configure a button, or mutate Hyprland.

## Results

| Measure | Observation |
|---|---|
| Intended presses | 5 |
| Synchronized trials | 5 |
| Ambiguous trials excluded from reliability denominator | 0 |
| Raw `barrel_primary` cycles | 5 total; 1 in each trial |
| 50 ms-debounced `barrel_primary` cycles | 5 total; 1 in each trial |
| Accepted one-press outcomes | 5/5 |

Hover proximity was also observed in every accepted trial but was not used as a button outcome.
No duplicate raw cycle was observed in this five-trial run. The separately observed 5.3 ms chatter
from the earlier characterization remains the reason the 50 ms debounce stays mandatory.

## Product consequences

- The measured far-from-tip `BTN_STYLUS` source is reliable for this narrowly scoped five-press
  campaign when converted to `barrel_primary` and debounced at 50 ms.
- The evidence supports considering a future, explicitly authorized semantic pen-action binding.
  It does not install or select such a binding, and the existing desktop configuration was not
  changed.
- Near-tip button, eraser, and chassis-silo claims remain unchanged and unbound.
