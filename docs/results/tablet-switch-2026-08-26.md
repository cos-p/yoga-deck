# Tablet-Switch Characterization — 2026-08-26

Target: ThinkPad X390 Yoga described in [`../host-baseline.md`](../host-baseline.md).

The guided read-only test captured repeated transitions in both directions using capability-based
discovery of `SW_TABLET_MODE`. No fixed event node, unique identifier, or raw device name was
recorded.

Two completed ten-edge campaigns produced these kernel-event-to-userspace delivery latency
distributions:

| Campaign | Samples | Minimum | Median | p95 | Maximum |
|---|---:|---:|---:|---:|---:|
| 1 | 10 | 0.076 ms | 0.131 ms | 1.119 ms | 1.119 ms |
| 2 | 10 | 0.074 ms | 0.124 ms | 0.223 ms | 0.223 ms |

These figures measure event delivery after the kernel timestamps the switch event. They do not
measure the physical movement-to-switch delay or compositor response time.

## Hinge threshold result

No hinge threshold is claimed. Direct reads of the HID-IIO hinge sysfs channel returned zero most
of the time with brief non-zero samples. A continuously polling tracker found no non-zero samples
within 300 ms of any of ten switch edges in the strict correlation run. Earlier broad retention
associated stale values with both directions and was rejected as invalid evidence.

Threshold characterization therefore requires a buffered IIO capture with shared monotonic timing
before the tablet-switch hinge angle can be reported. `SW_TABLET_MODE` remains the authoritative
binary safety input independently of this missing richer measurement.

## Buffered follow-up

A subsequent privileged buffered-IIO campaign decoded 459 hinge samples and correlated all ten
switch edges on a normalized monotonic timeline:

| Transition | Samples | Minimum | Median | Maximum |
|---|---:|---:|---:|---:|
| Enter folded/tablet mode | 5 | 264° | 299° | 360° |
| Return to laptop mode | 5 | 8° | 79° | 92° |

The same campaign measured switch-event delivery latency of 0.056 ms minimum, 0.104 ms median,
and 0.185 ms maximum. Coverage was 100% with no switch edges missing a hinge sample inside the
correlation window.

The HID-IIO trigger continued to emit realtime timestamps even while
`current_timestamp_clock` read `monotonic`. Yoga Deck detects the observed clock domain using a
paired realtime/monotonic reference and normalizes timestamps before correlation. The buffer,
channels, clock setting, and length were restored to their original disabled state after every
campaign, including failed runs.

These are preliminary edge distributions from one ten-event campaign, not yet a general posture
classifier. Tent and stand labels still require accelerometer fusion, hysteresis, and repeated
validation. `SW_TABLET_MODE` remains authoritative for binary input safety.
