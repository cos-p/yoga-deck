# Monitor hotplug reconciliation contract

Yoga Deck listens to Hyprland socket2 for the exact `monitoradded`, `monitorremoved`,
`monitoraddedv2`, and `monitorremovedv2` event names. It discards the event data—including
connector names and monitor descriptions—at the adapter boundary. Each accepted event becomes a
payload-free `MonitorTopologyChanged` domain event.

The pure reducer responds with `ReconcileObservedState`. The runtime reopens its tablet-switch,
orientation, and optional pen-action sessions, rereads their current state, and re-runs the
coordinator's observed-state reconciliation. This uses the same serialized freshness mechanism as
suspend/resume, so a topology change cannot leave stale posture or transform intent in place.

The Hyprland rotation adapter queries the current monitor and input records afresh and submits one
transaction for the owned internal output plus its touchscreen and pen. It preserves the internal
output's observed mode, position, scale, VRR, color, bit depth, and SDR properties; it does not
reconstruct, move, disable, or otherwise target an external monitor.

Socket unavailability, disconnect, and malformed events produce only stable diagnostics. No socket
path, instance signature, connector, description, or exception text is emitted. This is a
source-level contract: no live monitor was connected, disconnected, or reconfigured. Deployment
and a standard Hyprland reload have now been validated, but no external monitor was available.
Physical attach/detach coverage remains deferred; the reload result must not be
presented as physical hotplug evidence.

Reference: [Hyprland IPC event socket](https://wiki.hypr.land/IPC/).
