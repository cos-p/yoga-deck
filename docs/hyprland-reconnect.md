# Hyprland reconnect and compositor replacement

Status: fake-adapter and source-level contract. **No hard compositor crash, restart, or
graphical-session replacement has been exercised on the target machine.** A `hyprctl reload` is
not a disconnect and is not evidence for anything on this page. Live validation is still pending.

## Two different events

A new Hyprland process has a new instance signature and starts from its configuration files. The
runtime's input inhibition (`hl.device({ ..., enabled = false })`) and rotation are runtime
mutations sent through `hyprctl eval`; they are not written to configuration, so a replacement
compositor starts with every internal input **enabled** and the internal display untransformed.
Losing the compositor therefore cannot strand the internal keyboard, touchpad, or TrackPoint in a
disabled state. What it can lose is folded-mode inhibition and rotation.

The target session is started by SDDM through `uwsm`, which runs Hyprland inside a
`wayland-wm@` user unit through Hyprland's `start-hyprland` watchdog launcher (read-only
observation, 2026-09-30, Hyprland 0.56.2). That gives two cases:

| Case | What systemd does | What the runtime must do |
|---|---|---|
| Session ends and a new one starts (logout, clean exit, uwsm stops the session) | `graphical-session.target` stops; `PartOf=` stops `yoga-deck.service` with `SIGTERM`; the runtime runs its in-process shutdown recovery and exits, then `ExecStopPost` runs `yoga-doctor recover-inputs` as a backup. On the next login the target starts and `WantedBy=` starts a fresh service with the new session environment. | Nothing in-process: the fresh service performs normal startup recovery and reconciliation. |
| Compositor replaced inside the same session (watchdog relaunch after an unclean exit) | Not observed. The launcher contains a restart-after-unclean-exit path. If the `wayland-wm@` unit stays active, `graphical-session.target` stays active and the service keeps running. | Follow the new instance and re-apply reducer-owned intent. |

Two details of the first case: on `SIGTERM` (or `SIGINT`) the runtime cancels itself once and
runs the reducer-planned shutdown in-process (internal inputs re-enabled, OSK torn down), bounded
at 10 s, and exits 0; `ExecStopPost` still runs afterwards as a backup. If the compositor is
already gone, both recoveries cannot reach it and may fail, which is harmless because the next
compositor starts with inputs enabled; a failed in-process recovery emits
`input_recovery_incomplete` but does not change the exit status. Only a shutdown that overruns its
bound exits 1 (`shutdown_timed_out`). The signal path is covered by
`tests/integration/test_runtime_signals.py` against fakes, including a real `SIGTERM` to a
subprocess; a live logout has not been observed with it.

## Runtime contract for in-session replacement

**Signature resolution.** The service inherits `HYPRLAND_INSTANCE_SIGNATURE` once, at start. On
this host `hyprctl` exits non-zero when that signature names an instance that no longer exists,
so before this change a runtime that survived a compositor replacement would have failed every
query, mutation, and event-socket connection until the service restarted. The Hyprland adapters
now resolve the instance on every `hyprctl` call and every socket2 connection
(`adapters/hyprland_instance.py`):

- the inherited signature is used while its `hyprland.lock` names a live `Hyprland` process;
- otherwise, if exactly one live instance exists under `$XDG_RUNTIME_DIR/hypr/`, it is used;
- with no live instance, or more than one, the inherited value is kept and calls fail as before.

Resolution reads only per-user lock files and process names. Signatures, paths, and PIDs are
never emitted in diagnostics. The same rule lets `yoga-doctor recover-inputs` find the running
compositor from a TTY login that has `XDG_RUNTIME_DIR` but no compositor environment (source
contract; not yet exercised live).

**While the compositor is gone.** Periodic reconciliation (every 2 s) fails and emits
`{"component": "hyprland", "code": "reconcile_required"}`; the input coordinator falls back to
desiring enabled inputs. The socket2 loop retries and emits
`{"component": "hyprland", "code": "monitor_topology_unavailable"}`. Reducer state, and therefore
shell status, keeps the last posture intent: during the outage it describes intent, not compositor
truth. The tablet switch and orientation sessions are unaffected.

**After a replacement appears.** The next periodic reconciliation reaches it, enables owned
inputs, reapplies the current transform to the internal display, touchscreen, and pen, and then
the reducer's reconcile transition re-applies folded inhibition. Rotation preserves the new
instance's observed mode, position, scale, and color properties; it does not restore the old
instance's values. External inputs and monitors are never targeted. Socket2 reconnects to the new
instance.

## Fake-adapter coverage

`tests/integration/test_hyprland_reconnect.py` runs the real runtime, coordinators, and Hyprland
adapters against a fake compositor that can crash and be replaced by an instance with default
configuration:

- folded posture: inhibition is re-applied on the replacement, external inputs untouched;
- laptop posture: internal inputs stay enabled and no disable is ever sent;
- rotation: display, touchscreen, and pen transforms are re-applied with scale preserved and the
  external monitor untouched;
- event-socket drop with a live compositor: socket2 reconnects without changing intent;
- diagnostics contain only stable component and code fields.

`tests/unit/test_hyprland_instance.py` covers signature resolution: a live inherited signature
is kept, a stale one follows the single replacement, reused PIDs and malformed locks are rejected,
ambiguity is not guessed, `hyprctl` receives the replacement signature, and socket2 connects to
the replacement instance.

## Known limits

- `SystemCoordinator.reconcile` applies inputs, then rotation, then the OSK. If rotation or OSK
  reconciliation keeps failing on the replacement, the reducer's reconcile transition does not run
  and folded inhibition is not re-applied. This fails toward enabled inputs.
- Child processes (wvkbd, `omarchy capture text`, Xournal++) inherit the runtime's original
  environment, including the compositor signature and `WAYLAND_DISPLAY`. After an in-session
  replacement they may target stale values. Not covered.
- If the watchdog relaunches Hyprland in a safe mode with a different configuration, the internal
  display's observed scale and mode on the new instance are what rotation preserves.
- Multiple simultaneous live Hyprland instances for one user are not disambiguated.

## Guided live validation

This procedure is destructive to the running desktop session: unsaved work in every graphical
application is lost. Run it only with explicit authorization, on the target machine, with an
external USB keyboard attached as an independent recovery path. Record only the normalized
outcomes listed below; do not retain raw journal output, PIDs, signatures, or screenshots.

Before starting, make sure the running service uses the code under test (the service must have
been restarted after this change, which is itself an authorized action). Note the time.

### Baseline

1. In the graphical session, in laptop posture: `yoga-doctor report --redact` shows `ready`,
   laptop posture, internal inputs enabled.
2. `systemctl --user show yoga-deck.service -p ActiveState -p NRestarts` — record both values.
3. Record whether the service's main PID is the same before and after each trial as a yes/no
   (compare `systemctl --user show -p MainPID` values; do not write the numbers down).

### Trial A — session replacement (logout and login)

1. Log out through the Omarchy menu and log back in (or let autologin return).
2. Check: service active; `yoga-doctor report --redact` shows `ready`, laptop, inputs enabled;
   type with the internal keyboard and move the touchpad and TrackPoint.
3. Fold to tablet posture: internal keyboard and touchpad produce no input; unfold: they work.

### Trial B — in-session compositor crash, laptop posture

1. Switch to a text console (Ctrl+Alt+F3) and log in as the same user.
2. `pgrep -x Hyprland` returns one process. Kill it uncleanly: `pkill -KILL -x Hyprland`.
3. Switch back to the graphical VT (Ctrl+Alt+F1 or F2) and wait for the relaunch or return to the
   login screen; log in if required. Record which one happened.
4. Check: whether the service survived (same main PID yes/no) and its `NRestarts`;
   `yoga-doctor report --redact`; internal keyboard, touchpad, and TrackPoint work.
5. From the text console, count stable diagnostics only:
   `journalctl --user -u yoga-deck.service --since '<start time>' -o cat | grep -c reconcile_required`.

### Trial C — in-session compositor crash, folded posture

1. Fold to tablet posture and confirm the internal keyboard produces no input.
2. Repeat Trial B steps 1–3 while folded (use the external keyboard on the text console).
3. Check within ten seconds of the desktop returning: internal keyboard and touchpad produce no
   input while folded; the display, touch, and pen follow the current orientation (four-corner
   touch and pen check); the on-screen keyboard state; `yoga-doctor report --redact`.
4. Unfold: internal keyboard, touchpad, and TrackPoint work.

### Recovery if the internal keyboard is unusable in laptop posture

Use the external keyboard, or a text console: `yoga-doctor recover-inputs`, then
`systemctl --user restart yoga-deck.service` if needed. Stopping the service also runs input
recovery, in-process on `SIGTERM` and again through `ExecStopPost`.

### Evidence to record

For each trial: posture, whether Hyprland relaunched in-session or the session was replaced,
whether the service process survived, `NRestarts` delta, the report's posture/input/readiness
fields, physical input observations (enabled in laptop, suppressed when folded), rotation
alignment result, OSK state, and the `reconcile_required` count. Publish the sanitized result
under `docs/results/` and link it from [`release-scope.md`](release-scope.md).
