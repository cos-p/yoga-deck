# Pen action characterization — 2026-08-26

## Scope

This guided hardware measurement characterizes the X390 Yoga's Wacom pen event path from
evdev through the current host stack. It does not inject input, mutate Hyprland, retain raw
captures, or inspect paste payloads. Device discovery used the stable Wacom name and
capabilities rather than an `eventN` path.

The public protocol retains only physical-condition label, trial index, normalized action,
down/up/sample state, relative monotonic time, and normalized pressure. It excludes coordinates,
device paths, unique identifiers, and personal content.

## Results

| Physical condition | Observation | Decision |
|---|---|---|
| Pen proximity | 5/5 complete hover-in/away cycles | Supported |
| Tip contact | At least 5/5 requested strokes detected | Supported |
| Pressure | Eight observed stroke peaks: min 0.349695, median 0.743101, max 0.812943 of advertised maximum | Supported as a normalized continuous value |
| Side button farthest from tip | Emits `BTN_STYLUS`, despite the physical-label assumption used in the exploratory run | Supported only after debounce |
| Side button nearest tip | 0 button events in 5 guided hover attempts; proximity remained observable | Do not assign an action |
| Opposite end / eraser | 0 pen, contact, or `BTN_TOOL_RUBBER` events in 5 guided presentations | Unverified; do not expose an eraser action |

The known-working far-from-tip button produced a clean one-to-one cycle in a controlled
single-press trace. A replicated multi-press trace also contained two complete `BTN_STYLUS`
cycles separated by 5.3 ms during one intended actuation. Yoga Deck therefore preserves raw
cycle counts for diagnosis but collapses barrel-button cycles whose completions are less than
50 ms apart. This threshold is an empirical guard against the observed chatter; it is not a
claim about firmware internals.

The exploratory timed button runs were not used as reliability denominators because the user
performed an unknown number of presses in one run and physical actions crossed capture timing
boundaries in others. A later synchronized per-press campaign provided the required reliability
denominator: five intended presses produced one raw and one debounced `barrel_primary` cycle in
each trial. See [`far-button-reliability-2026-08-27.md`](far-button-reliability-2026-08-27.md).

## Product consequences

- Pen proximity, tip, and normalized pressure may enter the adapter contract.
- A physical-button workflow must bind by measured event code, not by button position inferred
  from `BTN_STYLUS` versus `BTN_STYLUS2` names.
- Any far-button action must pass through the 50 ms debounce before entering the reducer.
- No action is assigned to the near-tip button or opposite end on this host.
- No chassis pen-silo insertion/removal event was observed or inferred.
- OCR and scratchpad actions remain unassigned until an explicitly authorized source-to-action
  policy is integrated and separately tested.
