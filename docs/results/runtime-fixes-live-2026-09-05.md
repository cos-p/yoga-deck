# Runtime fixes: deployment and initial live checks

Date: 2026-09-05. User authorized restarting the existing deployment and live validation.

## Deployment snapshot

The existing runtime launcher resolves to this repository's virtual environment and module.
The existing user service was active and autostart was already enabled; the restart preserved
that configuration. This supersedes the older deployment snapshot that described autostart as
disabled. No package installation or service enablement was performed.

Current desktop: Omarchy 4.0.1-1, Hyprland v0.56.2. One internal 1920×1080 display was present,
at scale 1.25 and transform 0. No external monitor was attached.

The restart produced a new runtime process. The service reported active/running, zero automatic
restarts, laptop posture, normal orientation, orientation unlocked, and internal inputs enabled.
The redacted support report returned `ready` with all expected input and sensor roles available.
These input-enabled values describe reducer intent; physical keyboard delivery requires a guided
check. The far pen button remained unbound.

## Automated live evidence

- The marked live internal-touchpad disable/restore test passed. Its `finally` path reenables the
  touchpad. It does not independently measure application-visible suppression.
- The marked live SensorProxy connect/read/release test passed.
- Twenty additional real SensorProxy connect/close cycles completed. After binding warmup and
  garbage collection, descriptor count was 8 at baseline, minimum, maximum, and final observation.
  Connect/close measurements (including a 20 ms settling wait and garbage collection) had median
  34.67 ms and maximum 36.20 ms. This checks connection ownership; it is not rotation latency.
- A post-restart scan of recent service messages found no structured Yoga Deck error diagnostics.
  Only stable component/code counts were examined; raw messages are not included here.

The implementation was previously validated by the default safe suite: 547 passed, 2 skipped,
11 hardware/live tests deselected. Ruff lint, formatting, and compilation also passed.

## Guided fold and emergency recovery

The user confirmed folding into tablet mode, using the panel, and successfully typing with the
on-screen keyboard. Immediately before recovery, runtime status reported tablet posture and
internal inputs disabled.

The production `yoga-doctor recover-inputs` command succeeded. Five normalized status observations
spanning 8.8 seconds all reported tablet posture with internal inputs enabled and no error. The
transition sequence advanced four times after the initial recovery sample, demonstrating that
periodic reconciliation continued while recovered intent persisted. This directly exercises the
previous CLI/runtime ownership bug. It does not assert physical key delivery while folded.

The user then unfolded the laptop and confirmed physical keyboard and touchpad operation and
on-screen-keyboard dismissal. A follow-up runtime query confirmed laptop posture, normal
orientation, inputs enabled, no status error, and an active/running service with zero automatic
restarts.

## Remaining physical validation

A subsequent fold restoring inhibition, pen presses across reconnect, and OCR responsiveness
during a posture change have not yet been confirmed in this session. Suspend/resume, hard compositor replacement, and external-monitor hotplug were not
exercised by these checks. No synthetic kernel input was injected and no clipboard content was read.
