# Normalized Event Journal

Yoga Deck recordings use versioned JSON Lines so sanitized scenarios can be reviewed, diffed,
and replayed through the pure reducer. Raw capture adapters must normalize events before they
reach this format.

Each line in schema version 1 contains exactly four fields:

```json
{"event":"tablet_mode","schema_version":1,"t_ms":0,"value":true}
```

- `schema_version` is `1`.
- `t_ms` is a non-negative monotonic offset from the start of the recording.
- `event` is `tablet_mode`, `orientation`, `orientation_lock`, or `shutdown`.
- `value` is a boolean or `null` for tablet mode, one of `normal`, `right`, `inverted`,
  `left`, or `null` for orientation, a boolean for orientation lock, and `null` for shutdown.

Unknown fields and values are rejected. Records must not contain device nodes, sysfs paths,
kernel device names, serial numbers, usernames, wall-clock timestamps, personal content, or raw
exception text. Raw captures stay outside Git; only reviewed normalized fixtures belong under
`tests/fixtures/events/`.

Replay processes records in file order and sends only their typed events through the pure reducer.
It never invokes hardware or compositor adapters. The same initial state and recording therefore
produce the same final state and effect trace.
