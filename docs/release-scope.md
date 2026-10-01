# Yoga Deck 0.1.0 public-alpha scope

Version `0.1.0` is the first public-alpha candidate. It is intentionally a measured release for
one hardware and desktop baseline, not a claim of universal Linux convertible support.

## Validated target

The supported `0.1.0` claim is limited to the ThinkPad X390 Yoga and the dated Omarchy, Hyprland,
Quickshell, input, and sensor snapshot in [`host-baseline.md`](host-baseline.md). Exact device nodes,
package versions, and monitor topology are volatile and must be rediscovered rather than treated as
stable identifiers.

On that target, the public alpha includes:

- authoritative binary laptop/folded posture from `SW_TABLET_MODE`;
- inhibition and recovery of only the discovered internal keyboard, touchpad, and TrackPoint;
- SensorProxy-driven internal-display rotation coordinated with touchscreen and pen transforms;
- orientation lock and explicit manual orientation controls;
- startup, shutdown, suspend/resume, device reconnect, periodic, and monitor-event reconciliation;
- measured far-from-tip pen-button actions, disabled unless explicitly configured;
- Omarchy OCR capture and an optional Xournal++ scratchpad action;
- normalized discovery, event recording/replay, guided measurement commands, emergency input
  recovery, and a redacted support report;
- a user-owned Omarchy Shell status and control plugin.

The implementation remains adapter-oriented so other hardware and compositors can be added later.
That design goal does not make them supported by `0.1.0`.

## Disclosed deferred validation

The following gaps do not invalidate the measured X390 Yoga alpha, but they must remain visible in
release notes, support reports where applicable, and articles:

- physical external-monitor or dock attach/detach has not been tested; fake-adapter and source
  contracts must not be presented as live hardware evidence;
- hard compositor loss is not validated live: a Hyprland configuration reload and a controlled
  Yoga Deck restart passed, but no compositor crash, in-session compositor relaunch, or
  logout/login session replacement has been exercised on the target (see
  [`hyprland-reconnect.md`](hyprland-reconnect.md));
- the hinge observations are preliminary edge distributions, not a tent/stand/tablet classifier;
- no distinct chassis pen insertion/removal event has been captured;
- the near-tip button and opposite end do not have a supported public action contract;
- OCR evidence is a five-trial prepared-target workflow, not general OCR accuracy;
- the tablet-scoped wvkbd/Fcitx lifecycle has pure and adapter coverage, and folded touch typing plus
  laptop-mode recovery passed live, but pen delivery and the broader app matrix remain;
- short resource samples do not establish long-term memory, energy, or fleet reliability.

Physical external-monitor validation remains deferred.

Hard compositor reconnect remains an explicit `0.1.0` limitation,
reported by the support report as `hard_compositor_reconnect_unvalidated`. What exists is
fake-adapter and source-level coverage only: against a fake compositor that crashes and is
replaced by an instance with default configuration, the real runtime and adapters re-apply folded
inhibition, keep laptop-mode inputs enabled, re-apply coordinated rotation, reconnect the event
socket, and follow a changed instance signature. None of that is live evidence, and nothing about
it may be inferred from `hyprctl reload`. The safety argument for shipping the limitation is
narrower than the fake coverage: inhibition and rotation are runtime mutations, so a replacement
compositor starts with internal inputs enabled, and a session replacement restarts the service
through `graphical-session.target`. The known failure mode is therefore lost folded inhibition or
rotation, not stranded internal inputs. The guided procedure that would produce live evidence is
in [`hyprland-reconnect.md`](hyprland-reconnect.md).

## Publication gate

The `0.1.0` version number identifies the candidate scope but does not mean the repository has
already been released. Public publication requires release work to complete, including a reproducible installation/removal path, clean automated checks, agreement
between the documented and actual CLI, QML behavior tests, a public-tree privacy audit, and a
clean-checkout rehearsal. Creating commits, configuring a remote, tagging, and publishing remain
explicitly authorized final actions.

The clean-checkout rehearsal must rerun its automated checks against the final candidate tree.

## Claim discipline

Every published result must link to a sanitized evidence document under `docs/results/`, identify
the tested baseline, retain exclusions and denominators, and distinguish an observed result from an
architectural invariant. Pen proximity must never be described as a chassis-silo event, and the
binary tablet switch must never be described as proof of tent or stand posture.
