# Internal-Input Inhibition

Yoga Deck treats input inhibition as a safety transaction owned by one coordinator. The pure
reducer emits `SetInternalInputsEnabled`; the coordinator serializes that effect and invokes a
replaceable compositor adapter.

## Ownership

The initial X390 Yoga adapter recognizes exactly these Hyprland device names and roles:

| Role | Hyprland name |
|---|---|
| Internal keyboard | `at-translated-set-2-keyboard` |
| Touchpad | `synps/2-synaptics-touchpad` |
| TrackPoint | `tpps/2-alps-trackpoint` |

Discovery starts from `hyprctl -j devices`, but substring matches are not sufficient for ownership.
External USB/Bluetooth devices, touchscreens, pens, virtual keyboards, and passthrough devices are
never returned as inhibition targets. The coordinator rechecks the adapter result against the same
allowlist as defense in depth. Other hardware should supply a replacement ownership adapter rather
than weakening this one.

## Runtime mutation

Hyprland 0.55 and later use Lua configuration. The adapter invokes `hyprctl` directly without a
shell and evaluates one per-device update:

```lua
hl.device({ name = "synps/2-synaptics-touchpad", enabled = false })
```

The `enabled` property supports keyboards, mice, and touchpads. Hyprland 0.56 does not expose a
reliable query for the effective per-device enabled value, so Yoga Deck does not pretend it can
verify that state from `getoption`. Reconciliation reapplies the coordinator-owned desired state
idempotently after startup, hotplug, or compositor reconnect.

## Failure and ordering

- Effects are serialized under one asynchronous lock.
- An effect whose sequence is not newer than the latest accepted sequence is ignored.
- A failed disable transaction immediately changes desired state back to enabled, attempts to
  enable every discovered owned device, and reports `input_inhibition_failed`.
- Enabling and explicit recovery attempt every target even when one operation fails, then report
  `input_recovery_incomplete`.
- Discovery failure during inhibition is treated as inhibition failure and leaves recovery as the
  desired state.
- Cancellation drains an already-started compositor mutation before releasing its ordering lock.
  Shutdown recovery therefore runs after all older mutation workers have finished.
- `yoga-doctor recover-inputs` first sends `recover_inputs` to the runtime socket. Successful
  recovery updates reducer-owned intent as well as enabling devices, so reconciliation and
  duplicate folded observations keep inputs enabled. A subsequent laptop-to-folded transition
  restores normal posture inhibition.
- If the runtime is absent, unresponsive, or reports failure, the CLI falls back to rediscovering
  and enabling the exact owned targets directly. This fallback provides immediate best-effort
  access; persistence cannot be guaranteed until the runtime accepts the recovery request.
  A recovery mutation failure is returned to the client instead of reporting success from
  desired state alone.

Default tests use fake adapters. The marked `live_desktop` contract temporarily disables only the
touchpad and reenables it in `finally`; it must never run without explicit authorization.
