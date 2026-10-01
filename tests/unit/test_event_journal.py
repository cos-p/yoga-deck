import json

import pytest

from yoga_deck.core import Orientation, OrientationChanged, TabletModeChanged
from yoga_deck.diagnostics.journal import (
    EventRecord,
    JournalError,
    dumps_jsonl,
    loads_jsonl,
)


def test_normalized_events_round_trip_deterministically() -> None:
    records = (
        EventRecord(0, TabletModeChanged(True)),
        EventRecord(125, OrientationChanged(Orientation.LEFT)),
        EventRecord(250, TabletModeChanged(False)),
    )

    encoded = dumps_jsonl(records)

    assert loads_jsonl(encoded) == records
    assert dumps_jsonl(loads_jsonl(encoded)) == encoded
    assert "/dev/input" not in encoded
    assert "event9" not in encoded


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            '{"schema_version":2,"t_ms":0,"event":"tablet_mode","value":true}\n',
            "unsupported_schema",
        ),
        (
            '{"schema_version":1,"t_ms":0,"event":"tablet_mode","value":true,'
            '"path":"/dev/input/event9"}\n',
            "unsafe_event_record",
        ),
        (
            '{"schema_version":1,"t_ms":0,"event":"orientation","value":"diagonal"}\n',
            "invalid_event_value",
        ),
        ("not-json\n", "malformed_event_record"),
    ],
)
def test_malformed_or_unsafe_records_fail_with_stable_codes(payload, code) -> None:
    with pytest.raises(JournalError) as error:
        loads_jsonl(payload)

    assert error.value.code == code
    assert "/dev/input" not in str(error.value)


def test_timestamp_regression_is_rejected() -> None:
    payload = "\n".join(
        (
            '{"event":"tablet_mode","schema_version":1,"t_ms":10,"value":true}',
            '{"event":"tablet_mode","schema_version":1,"t_ms":9,"value":false}',
        )
    )

    with pytest.raises(JournalError) as error:
        loads_jsonl(payload)

    assert error.value.code == "non_monotonic_timestamp"


def test_serialized_shape_has_only_public_schema_fields() -> None:
    payload = json.loads(dumps_jsonl((EventRecord(0, TabletModeChanged(None)),)))

    assert set(payload) == {"event", "schema_version", "t_ms", "value"}
