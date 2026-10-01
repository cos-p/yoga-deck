# Runtime reliability and resource measurement — 2026-08-27

Target: ThinkPad X390 Yoga described in [`../host-baseline.md`](../host-baseline.md).

The explicitly authorized user service was active but not enabled for automatic startup. The
campaign retained no process identifiers, device paths, monitor identities, desktop content, or
raw journal output. A temporary normalized tablet-switch recording was reduced to counts and then
removed.

## Reliability results

| Scenario | Trials | Result |
|---|---:|---|
| Full fold to tablet posture and return to laptop posture | 5 | 5/5 cycles produced one folded and one laptop edge |
| Suspend to RAM and resume | 5 | 5/5 kernel suspend entries produced a matching exit and runtime recovery |
| Hyprland configuration reload | 1 | Runtime remained available with no restart or config error |
| Controlled Yoga Deck service restart | 1 | Shutdown recovery, startup observation, and shell transport return passed |

The fold campaign captured 10/10 alternating `SW_TABLET_MODE` edges over 50.848 seconds. It ended
with reducer-owned laptop posture and internal inputs enabled. The deployed service recorded no
failure restart.

All five suspend trials entered deep suspend. After every resume batch, the runtime was available;
the original service process had survived with zero failure restarts, and the final state was
laptop posture with internal inputs enabled. The operator completed the requested checks without
reporting flicker, wrong orientation, delayed recovery, or unresponsive input.

The standard `hyprctl reload` check returned successfully, left no configuration errors, and did
not regress runtime state. This does not force a hard compositor-process disconnect, so it is not
evidence for recovery after a compositor crash or session replacement.

## Resource samples

The privacy-safe sampler reads only process CPU time, resident memory, and voluntary plus
involuntary scheduler-switch counters. CPU percentage is relative to one logical core.

| Window | Duration | Samples | CPU | RSS median | RSS maximum | Scheduler switches/s |
|---|---:|---:|---:|---:|---:|---:|
| Idle before physical trials | 60.001 s | 61 | 1.550% | 30.461 MiB | 30.461 MiB | 15.833 |
| Settled after five fold cycles | 30.001 s | 31 | 1.600% | 30.465 MiB | 30.465 MiB | 17.433 |
| Settled after five suspend cycles | 30.001 s | 31 | 0.533% | 31.145 MiB | 31.152 MiB | 15.966 |

RSS was flat inside each observation window. The final median was 0.684 MiB above the initial
median. These short windows do not establish long-term leak behavior or an energy budget.
Unprivileged per-process hardware wakeup counters were unavailable on this host. Scheduler switches
are therefore reported as a clearly labelled activity proxy, not as hardware wakeups or power use.

## Deferred coverage

No external monitor or dock was available. Physical attach/detach reliability and preservation of
an existing external layout remain deferred. The source and fake-adapter
hotplug contract remain covered, but no live result is inferred from them.
