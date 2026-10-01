# Orientation and Alignment Validation — 2026-08-26

Target: ThinkPad X390 Yoga, Hyprland 0.56.2, internal 1920×1080 display at scale 1.25.

## Result

The corrected guided campaign passed. A temporary full-screen Quickshell surface received
native Qt touch and tablet events, verified their device types, presented four corners plus
center in sequence, and recorded monitor-local logical coordinates. It stored no screenshots,
device IDs, personal content, or raw device paths.

- SensorProxy mount direction: pass for `right-up`, `bottom-up`, `left-up`, and `normal`.
- Touch alignment: 20/20 points passed within the 60 logical-pixel tolerance.
- Pen alignment: 20/20 points passed within the 60 logical-pixel tolerance.
- External monitors: unchanged.
- Internal mode, position, scale, VRR, format, color, SDR state, and starting transform: restored.
- Visible flicker: none observed in four transitions.

## Coordinate error distribution

| Device | Samples | Minimum | Median | Maximum |
|---|---:|---:|---:|---:|
| Touch | 20 | 0.09 px | 10.57 px | 18.50 px |
| Pen | 20 | 1.45 px | 3.77 px | 12.12 px |

Maximum error by orientation:

| Orientation | Transform | Touch | Pen |
|---|---:|---:|---:|
| Right | 3 | 18.50 px | 11.20 px |
| Inverted | 2 | 12.83 px | 9.13 px |
| Left | 1 | 14.57 px | 5.11 px |
| Normal | 0 | 13.90 px | 12.12 px |

## Compositor-application latency

This interval measures SensorProxy event acceptance through completion of the coordinated
Hyprland display/touch/pen transaction. It is not a presentation-frame timestamp.

| Samples | Minimum | Median | p95 | Maximum |
|---:|---:|---:|---:|---:|
| 4 | 49.753 ms | 54.914 ms | 55.810 ms | 55.810 ms |

## Audit history

An earlier report was retracted because it copied expected coordinates into its result. A first
corrected attempt then exposed two further defects: Wayland touch does not move the mouse cursor,
and a partial Lua monitor rule reapplied monitor defaults. The final campaign used a native
Wayland surface and a dynamically reconstructed observed monitor rule. Only this final result
should be cited as alignment evidence.
