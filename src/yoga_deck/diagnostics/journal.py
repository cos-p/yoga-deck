"""Versioned, normalized JSONL event journal format."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from yoga_deck.core import (
    Event,
    Orientation,
    OrientationChanged,
    OrientationLockChanged,
    ShutdownRequested,
    TabletModeChanged,
)

SCHEMA_VERSION = 1
_FIELDS = {"event", "schema_version", "t_ms", "value"}


class JournalError(ValueError):
    """A stable public failure code with no raw record content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EventRecord:
    t_ms: int
    event: Event

    def __post_init__(self) -> None:
        if isinstance(self.t_ms, bool) or not isinstance(self.t_ms, int) or self.t_ms < 0:
            raise JournalError("invalid_timestamp")


def dumps_jsonl(records: tuple[EventRecord, ...]) -> str:
    _validate_monotonic(records)
    return "".join(
        json.dumps(_record_to_dict(record), separators=(",", ":"), sort_keys=True) + "\n"
        for record in records
    )


def loads_jsonl(payload: str) -> tuple[EventRecord, ...]:
    records: list[EventRecord] = []
    for line in payload.splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise JournalError("malformed_event_record") from error
        records.append(_record_from_dict(raw))
    result = tuple(records)
    _validate_monotonic(result)
    return result


def _record_to_dict(record: EventRecord) -> dict[str, object]:
    event = record.event
    if isinstance(event, TabletModeChanged):
        kind, value = "tablet_mode", event.enabled
    elif isinstance(event, OrientationChanged):
        kind = "orientation"
        value = event.orientation.value if event.orientation is not None else None
    elif isinstance(event, OrientationLockChanged):
        kind, value = "orientation_lock", event.locked
    elif isinstance(event, ShutdownRequested):
        kind, value = "shutdown", None
    else:
        raise JournalError("unsupported_event")
    return {
        "event": kind,
        "schema_version": SCHEMA_VERSION,
        "t_ms": record.t_ms,
        "value": value,
    }


def _record_from_dict(raw: Any) -> EventRecord:
    if not isinstance(raw, dict):
        raise JournalError("malformed_event_record")
    if set(raw) != _FIELDS:
        raise JournalError("unsafe_event_record")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise JournalError("unsupported_schema")
    timestamp = raw["t_ms"]
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise JournalError("invalid_timestamp")
    return EventRecord(timestamp, _parse_event(raw["event"], raw["value"]))


def _parse_event(kind: Any, value: Any) -> Event:
    if kind == "tablet_mode" and (value is None or isinstance(value, bool)):
        return TabletModeChanged(value)
    if kind == "orientation":
        if value is None:
            return OrientationChanged(None)
        if isinstance(value, str):
            try:
                return OrientationChanged(Orientation(value))
            except ValueError as error:
                raise JournalError("invalid_event_value") from error
    if kind == "orientation_lock" and isinstance(value, bool):
        return OrientationLockChanged(value)
    if kind == "shutdown" and value is None:
        return ShutdownRequested()
    if kind not in {"tablet_mode", "orientation", "orientation_lock", "shutdown"}:
        raise JournalError("unsupported_event")
    raise JournalError("invalid_event_value")


def _validate_monotonic(records: tuple[EventRecord, ...]) -> None:
    pairs = zip(records, records[1:], strict=False)
    if any(current.t_ms < previous.t_ms for previous, current in pairs):
        raise JournalError("non_monotonic_timestamp")
