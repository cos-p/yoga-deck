# Redacted support report

`yoga-doctor report --redact` produces a read-only support summary from two existing,
normalized sources: hardware/compositor discovery and reducer-owned runtime status. Add `--json`
for a deterministic machine-readable representation. The command does not mutate hardware or the
desktop.

The explicit `--redact` flag is required. Without it, the command exits with
`report_redaction_opt_in_required` and does not inspect either source.

## Schema

JSON reports use schema version 2 and contain only:

- `redacted`: always `true`;
- `status`: `ready` when discovery has no missing/error components or diagnostics and the runtime
  is available, otherwise `degraded`;
- `discovery`: availability, semantic component roles with a status (`available`, `missing`,
  `error`, or `permission_denied`), public capability classes, sanitized monitor geometry, stable
  diagnostic codes, stable `remediation` codes, and a stable error code;
- `runtime`: availability, posture, internal-input state, orientation, orientation-lock state, and
  a stable error code;
- `limitations`: stable identifiers for important validation gaps.

Version 2 added the `permission_denied` component status and the `remediation` list. A role the
runtime opens itself (`tablet_switch`, `pen`) is `permission_denied` when sysfs shows the device
but this user cannot read its event node; discovery then adds the `input_permission_denied`
diagnostic and the `input_group_required` remediation, which the text report explains with a
pointer to [`install.md`](install.md). Roles Hyprland reads through logind (keyboard, touchpad,
TrackPoint, touchscreen) are not access-checked. `yoga-doctor inspect` uses the same statuses and
also reports schema version 2; `yoga-doctor setup` uses the same check for its input-access
warning.

The runtime transition sequence is deliberately omitted because it is volatile and does not help
interpret support. When discovery raises an error, its section becomes `discovery_unavailable`.
When the runtime socket cannot be reached, its section becomes `runtime_unavailable`. Malformed or
unexpected runtime values become `runtime_status_invalid`. A degraded report remains a successful
report operation, so it can describe a broken or absent runtime.

## Privacy boundary

The report never includes raw logs, exception text, usernames or private paths, volatile event
nodes, device IDs, monitor descriptions or EDID data, network data, clipboard contents, captured
images, or recognized OCR text. Discovery objects validate semantic roles and connector syntax;
runtime data is treated as untrusted and copied only after strict field and value validation.

The `external_monitor_hotplug_unvalidated` limitation records that physical hotplug validation is
still deferred on the initial target. `hard_compositor_reconnect_unvalidated` records that
recovery after a compositor crash or in-session replacement has fake-adapter coverage only; see
[`hyprland-reconnect.md`](hyprland-reconnect.md). `pen_silo_event_unverified` records that pen proximity must
not be interpreted as chassis insertion or removal.
