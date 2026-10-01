# Orientation sensor contract

Yoga Deck consumes screen orientation from the system-bus service
`net.hadess.SensorProxy` at `/net/hadess/SensorProxy`. The adapter calls
`ClaimAccelerometer()` before reading and always best-effort calls `ReleaseAccelerometer()`
when its session ends, with a one-second limit on the release call. It then unregisters signal
handlers and disconnects the bus even if release fails. Partial connection, introspection,
claim, and initial-read failures (including cancellation) also dispose of the resources already
acquired. Session cleanup is idempotent. This prevents old sessions from retaining subscribed
connections and accumulating observations across resume or hotplug.

The documented values normalize as follows:

| SensorProxy value | Domain orientation |
|---|---|
| `normal` | normal |
| `right-up` | right |
| `bottom-up` | inverted |
| `left-up` | left |
| `undefined` or unknown | unknown, preserve last applied rotation |

Initial state and `PropertiesChanged` signals enter the pure reducer. The posture and
orientation producers share one async reducer lock, so their sequence numbers remain
monotonic. Orientation lock continues to block rotation effects without blocking posture
inhibition. Service owner loss ends the session, emits only a stable diagnostic, and retries
discovery; cancellation releases the claim.

The production transport uses the pure-Python `dbus-next` package communicating with
`net.hadess.SensorProxy`. Live direction, mount matrix, and four-corner alignment across all four
orientations were validated on the host on 2026-08-26. See
[`results/orientation-alignment-2026-08-26.md`](results/orientation-alignment-2026-08-26.md).

Reference: [upstream SensorProxy D-Bus API](https://hadess.pages.freedesktop.org/iio-sensor-proxy/gdbus-net.hadess.SensorProxy.html).

