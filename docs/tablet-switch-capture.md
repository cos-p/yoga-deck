# Tablet-Switch Capture

`SW_TABLET_MODE` is Yoga Deck's authoritative binary laptop-versus-folded safety signal. The
adapter discovers the ThinkPad switch from its stable kernel name and advertised `EV_SW`
capability; it never stores or depends on `/dev/input/eventN` numbers.

Capture a fold and unfold into a local normalized recording:

```bash
.venv/bin/yoga-doctor record fold-unfold --events 2 --output captures/raw/fold-unfold.jsonl
```

Start the command in laptop mode, fold until the switch changes, then unfold until it changes
again. The command only reads input and IIO state. It does not grab the device, inject input, or
change the desktop. Raw characterization runs belong under `captures/raw/`, which Git ignores.

The marked hardware test captures repeated transitions and reports distributions for kernel-event
delivery latency and the hinge angle sampled at each switch edge:

```bash
YOGA_DECK_HARDWARE=1 .venv/bin/pytest -m hardware \
  tests/hardware/test_tablet_switch_capture.py -s
```

This test waits for ten physical switch events. Fold and unfold five times after starting it. A
single run is not sufficient evidence for a threshold or reliability claim; retain aggregate
results from repeated campaigns and sanitize them before publication.

On the initial target, direct hinge sysfs samples were not synchronized closely enough with switch
edges to measure a threshold. See [`results/tablet-switch-2026-08-26.md`](results/tablet-switch-2026-08-26.md).
The test reports `hinge_samples_not_synchronized` when this occurs rather than treating zeroes as
angles.

Buffered hinge correlation is a separate privileged hardware test because configuring an IIO
buffer requires temporary writes to root-owned sysfs controls and read access to `/dev/iio:*`.
The adapter snapshots the timestamp clock, channel enables, buffer length, and enable state;
disables the buffer and restores all values even when capture fails. Run it only through the
documented guided workflow after reviewing those controls.

The initial buffered campaign achieved complete switch-edge coverage. Its preliminary threshold
and latency distributions, including the observed realtime-versus-monotonic timestamp quirk, are
recorded in [`results/tablet-switch-2026-08-26.md`](results/tablet-switch-2026-08-26.md).
