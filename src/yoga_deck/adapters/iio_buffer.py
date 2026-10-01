"""Generic IIO scan parsing and semantic hinge-device discovery."""

from __future__ import annotations

import os
import re
import select
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Any


class IIOError(ValueError):
    """Stable IIO failure code without raw paths or frame contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ScanType:
    byte_order: str
    signed: bool
    real_bits: int
    storage_bits: int
    shift: int

    @property
    def storage_bytes(self) -> int:
        return self.storage_bits // 8


@dataclass(frozen=True, slots=True)
class ScanChannel:
    name: str
    index: int
    scan_type: ScanType


@dataclass(frozen=True, slots=True)
class PositionedChannel:
    name: str
    index: int
    scan_type: ScanType
    offset: int


@dataclass(frozen=True, slots=True)
class ScanLayout:
    channels: tuple[PositionedChannel, ...]
    frame_size: int


@dataclass(frozen=True, slots=True)
class DecodedSample:
    timestamp_ns: int
    values: dict[str, int]


@dataclass(frozen=True, slots=True)
class HingeMetadata:
    device_path: Path
    angle_kernel_name: str
    layout: ScanLayout
    scale: float
    offset: float
    timestamp_clock: str

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "channels": [channel.name for channel in self.layout.channels],
            "frame_size": self.layout.frame_size,
            "scale": self.scale,
            "offset": self.offset,
            "timestamp_clock": self.timestamp_clock,
        }


def parse_scan_type(encoded: str) -> ScanType:
    match = re.fullmatch(r"(le|be):([su])(\d+)/(\d+)>>(\d+)", encoded.strip())
    if match is None:
        raise IIOError("unsupported_iio_scan_type")
    endian, sign, real_bits, storage_bits, shift = match.groups()
    real = int(real_bits)
    storage = int(storage_bits)
    parsed_shift = int(shift)
    if storage % 8 or real < 1 or real + parsed_shift > storage:
        raise IIOError("unsupported_iio_scan_type")
    return ScanType(
        byte_order="little" if endian == "le" else "big",
        signed=sign == "s",
        real_bits=real,
        storage_bits=storage,
        shift=parsed_shift,
    )


def build_scan_layout(channels: tuple[ScanChannel, ...]) -> ScanLayout:
    if not channels or len({channel.index for channel in channels}) != len(channels):
        raise IIOError("invalid_iio_scan_layout")
    positioned: list[PositionedChannel] = []
    offset = 0
    largest_alignment = 1
    for channel in sorted(channels, key=lambda item: item.index):
        alignment = channel.scan_type.storage_bytes
        largest_alignment = max(largest_alignment, alignment)
        offset = _align(offset, alignment)
        positioned.append(PositionedChannel(channel.name, channel.index, channel.scan_type, offset))
        offset += channel.scan_type.storage_bytes
    return ScanLayout(tuple(positioned), _align(offset, largest_alignment))


def decode_frames(chunks: Iterable[bytes], layout: ScanLayout) -> tuple[DecodedSample, ...]:
    decoder = FrameDecoder(layout)
    samples: list[DecodedSample] = []
    try:
        for chunk in chunks:
            samples.extend(decoder.feed(chunk))
    except OSError as error:
        raise IIOError("iio_device_disconnected") from error
    decoder.finish()
    return tuple(samples)


class FrameDecoder:
    """Incrementally decode arbitrary IIO read boundaries."""

    def __init__(self, layout: ScanLayout) -> None:
        self._layout = layout
        self._pending = bytearray()

    def feed(self, chunk: bytes) -> tuple[DecodedSample, ...]:
        self._pending.extend(chunk)
        samples: list[DecodedSample] = []
        while len(self._pending) >= self._layout.frame_size:
            frame = bytes(self._pending[: self._layout.frame_size])
            del self._pending[: self._layout.frame_size]
            samples.append(_decode_frame(frame, self._layout))
        return tuple(samples)

    def finish(self) -> None:
        if self._pending:
            raise IIOError("partial_iio_frame")


def discover_hinge_metadata(sys_root: Path = Path("/sys")) -> HingeMetadata:
    try:
        for device in (sys_root / "bus/iio/devices").glob("iio:device*"):
            if (device / "name").read_text().strip().casefold() != "hinge":
                continue
            angle_stem = _hinge_channel_stem(device)
            scan = device / "scan_elements"
            channels = (
                _read_channel(scan, angle_stem, "hinge"),
                _read_channel(scan, "in_timestamp", "timestamp"),
            )
            timestamp_clock = (device / "current_timestamp_clock").read_text().strip()
            return HingeMetadata(
                device_path=device,
                angle_kernel_name=angle_stem,
                layout=build_scan_layout(channels),
                scale=float((device / "in_angl_scale").read_text()),
                offset=float((device / "in_angl_offset").read_text()),
                timestamp_clock=timestamp_clock,
            )
    except (OSError, ValueError) as error:
        raise IIOError("hinge_metadata_unavailable") from error
    raise IIOError("hinge_device_unavailable")


class BufferedHingeDevice:
    """Transactionally enable a hinge buffer and restore every owned setting."""

    def __init__(
        self,
        metadata: HingeMetadata,
        *,
        device_node: Path | None = None,
        buffer_length: int = 32,
    ) -> None:
        self._metadata = metadata
        self._device_node = device_node or Path("/dev") / metadata.device_path.name
        self._buffer_length = buffer_length
        self._stream: Any = None
        self._original: dict[Path, str] = {}

    def __enter__(self) -> BufferedHingeDevice:
        device = self._metadata.device_path
        buffer = device / "buffer0"
        settings = {
            device / "current_timestamp_clock": "monotonic",
            buffer / f"{self._metadata.angle_kernel_name}_en": "1",
            buffer / "in_timestamp_en": "1",
            buffer / "length": str(self._buffer_length),
            buffer / "enable": "1",
        }
        try:
            self._original = {path: path.read_text() for path in settings}
            if self._original[buffer / "enable"].strip() != "0":
                raise IIOError("iio_buffer_busy")
            for path, value in settings.items():
                path.write_text(value)
            self._stream = self._device_node.open("rb", buffering=0)
        except IIOError:
            self._restore()
            raise
        except OSError as error:
            self._restore()
            raise IIOError("iio_buffer_configuration_failed") from error
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self._restore()
        if self._stream is not None:
            self._stream.close()

    def chunks(
        self,
        size: int | None = None,
        *,
        stop_event: ThreadEvent | None = None,
        poll_interval: float = 0.1,
    ) -> Iterator[bytes]:
        if self._stream is None:
            raise IIOError("iio_buffer_not_configured")
        read_size = size or self._metadata.layout.frame_size * self._buffer_length
        stop = stop_event or ThreadEvent()
        os.set_blocking(self._stream.fileno(), False)
        while True:
            timeout = 0.0 if stop.is_set() else poll_interval
            readable, _, _ = select.select((self._stream,), (), (), timeout)
            if not readable:
                if stop.is_set():
                    return
                continue
            try:
                chunk = self._stream.read(read_size)
            except (BlockingIOError, ValueError):
                if stop.is_set():
                    return
                continue
            if not chunk:
                return
            yield chunk

    def _restore(self) -> None:
        if not self._original:
            return
        enable = self._metadata.device_path / "buffer0/enable"
        try:
            if enable in self._original:
                enable.write_text("0")
            for path, value in reversed(tuple(self._original.items())):
                if path != enable:
                    path.write_text(value)
            if enable in self._original:
                enable.write_text(self._original[enable])
        except OSError as error:
            raise IIOError("iio_buffer_restore_failed") from error


def unwrap_degrees(angles: tuple[float, ...]) -> tuple[float, ...]:
    if not angles:
        return ()
    unwrapped = [angles[0] % 360.0]
    previous_normalized = angles[0] % 360.0
    for angle in angles[1:]:
        normalized = angle % 360.0
        delta = (normalized - previous_normalized + 180.0) % 360.0 - 180.0
        unwrapped.append(unwrapped[-1] + delta)
        previous_normalized = normalized
    return tuple(unwrapped)


def normalize_timestamp_ns(
    timestamp_ns: int, *, monotonic_reference_ns: int, realtime_reference_ns: int
) -> int:
    """Map an IIO timestamp to monotonic time even if its trigger ignores clock selection."""

    realtime_distance = abs(timestamp_ns - realtime_reference_ns)
    monotonic_distance = abs(timestamp_ns - monotonic_reference_ns)
    if realtime_distance < monotonic_distance:
        return timestamp_ns - (realtime_reference_ns - monotonic_reference_ns)
    return timestamp_ns


def _decode_frame(frame: bytes, layout: ScanLayout) -> DecodedSample:
    values: dict[str, int] = {}
    timestamp: int | None = None
    for channel in layout.channels:
        scan_type = channel.scan_type
        encoded = frame[channel.offset : channel.offset + scan_type.storage_bytes]
        storage_value = int.from_bytes(encoded, byteorder=scan_type.byte_order, signed=False)
        value = (storage_value >> scan_type.shift) & ((1 << scan_type.real_bits) - 1)
        if scan_type.signed and value & (1 << (scan_type.real_bits - 1)):
            value -= 1 << scan_type.real_bits
        if channel.name == "timestamp":
            timestamp = value
        else:
            values[channel.name] = value
    if timestamp is None:
        raise IIOError("iio_timestamp_missing")
    return DecodedSample(timestamp, values)


def _hinge_channel_stem(device: Path) -> str:
    for label in device.glob("in_angl*_label"):
        if label.read_text().strip().casefold() == "hinge":
            return label.name.removesuffix("_label")
    raise IIOError("hinge_channel_unavailable")


def _read_channel(directory: Path, kernel_name: str, public_name: str) -> ScanChannel:
    return ScanChannel(
        public_name,
        int((directory / f"{kernel_name}_index").read_text()),
        parse_scan_type((directory / f"{kernel_name}_type").read_text()),
    )


def _align(offset: int, alignment: int) -> int:
    return (offset + alignment - 1) // alignment * alignment
