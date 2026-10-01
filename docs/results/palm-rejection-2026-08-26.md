# Native Palm-Rejection Characterization

Date: 2026-08-26  
Target: ThinkPad X390 Yoga running the observed Omarchy and Hyprland baseline.

## Result

The native Wacom/libinput/Hyprland path rejected application-visible palm input while the
pen was active. Yoga Deck does not need to disable the touchscreen merely because the pen is
in proximity.

| Condition | Valid guided trials | Touch delivered to test surface | Result |
|---|---:|---:|---|
| Palm only, pen away | 5 | 5 | Control passed |
| Pen hovering, tip up | 4 | 0 | 4/4 suppressed |
| Pen tip down | 5 | 0 | 5/5 suppressed |
| Recovery sequence, palm while pen active | 5 | 0 | 5/5 suppressed |

One of five hover slots was excluded because the recorded pen stream did not prove proximity
during that slot. It is not counted as either a pass or a failure.

After the pen left proximity, all five recovery slots produced a subsequent application-visible
finger touch. Observed time from the last recorded pen-proximity-out transition to the first
delivered finger touch was:

- count: 5;
- minimum: 505.320 ms;
- median: 889.526 ms;
- maximum: 1,091.807 ms;
- samples: 505.320, 561.177, 889.526, 999.776, and 1,091.807 ms.

This interval includes the operator's finger-tap cadence. It is evidence that touch recovered
reliably, but it is an upper-bound observation rather than a precise internal libinput recovery
latency.

## Method

`yoga-doctor test-palm-rejection --live` ran a temporary full-screen Quickshell surface on the
internal display. Five numbered monotonic time slots were used per condition. The first three
conditions used 30 seconds total; recovery used 45 seconds.

Two streams were observed concurrently:

- normalized Wacom pen proximity/tip and touchscreen contact transitions from devices discovered
  by stable name and role rather than an `eventN` path;
- finger touches delivered to the Quickshell application surface.

The analyzer accepted a hover or tip trial only when the pen event timeline proved the required
state in that slot. Duplicate contacts within a slot did not inflate the trial count. The surface
took no keyboard focus, displayed a countdown, closed automatically, and left no process behind.
No input was injected and no Hyprland configuration was changed.

## Scope and limitations

- The result characterizes the combined native Wacom, kernel, libinput, compositor, and Qt path;
  it does not attribute rejection to one layer.
- The sample is small and was collected in one session on the initial target machine.
- Palm shapes, contact area, pressure, pen distance, and application toolkit may affect behavior.
- One hover trial was excluded, so hover evidence is 4 valid trials rather than 5.
- No distinct chassis pen-silo event was observed or claimed. Pen proximity is not a silo signal.
- Earlier exploratory runs were discarded after exposing harness and analysis defects; their
  counts are not included here.

## Decision

Retain native palm rejection as the default. Do not add Yoga Deck policy that disables the whole
touchscreen during pen proximity. Future stylus actions and scratchpad workflows should preserve
this behavior and regression-test application delivery rather than assuming arbitration works.
