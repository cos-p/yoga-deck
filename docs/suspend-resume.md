# Suspend and resume reconciliation contract

Yoga Deck listens on the system bus for logind's
`org.freedesktop.login1.Manager.PrepareForSleep(boolean)` signal. A `true` value is the
pre-suspend notification and causes no transition. A `false` value is a resume notification and
enters the pure reducer as `ResumeRequested`.

The reducer emits a `ReconcileObservedState` effect with a new monotonic sequence number. The
runtime consumes that effect by closing and reopening the tablet-switch, orientation, and optional
pen-action sessions. The fresh tablet-switch reading re-establishes the authoritative posture
state before the runtime reconciles the coordinator and reasserts the reducer-owned internal-input
intent. The orientation session likewise refreshes its initial reading, so a transform observed
while suspended is not silently lost. Pen-action sessions reconnect without replaying an action.

The logind boundary exposes only the boolean signal and stable error codes. Connection failure or
service-owner loss emits `{"component": "resume", "code": "resume_monitor_unavailable"}`;
it never includes D-Bus names, paths, or exception text. Startup and shutdown recovery continue to
plan internal-input enablement, so a failed resume monitor cannot strand the internal keyboard,
touchpad, or TrackPoint.

The repository does not install or enable a user service automatically. After an explicitly
authorized user deployment, a live campaign completed five deep-suspend/resume trials. Every
kernel suspend entry had a matching exit; the same runtime process survived without a failure
restart and returned to laptop posture with internal inputs enabled. The sanitized evidence and
resource observations are in
[`results/runtime-reliability-2026-08-27.md`](results/runtime-reliability-2026-08-27.md).

Reference: [logind D-Bus interface](https://www.freedesktop.org/software/systemd/man/org.freedesktop.login1.html).
