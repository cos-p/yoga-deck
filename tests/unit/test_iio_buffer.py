import json
import struct
from pathlib import Path
from threading import Event as ThreadEvent

import pytest

from yoga_deck.adapters.iio_buffer import (
    BufferedHingeDevice,
    IIOError,
    ScanChannel,
    build_scan_layout,
    decode_frames,
    discover_hinge_metadata,
    normalize_timestamp_ns,
    parse_scan_type,
    unwrap_degrees,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/iio/hinge_scan.json"


def test_target_scan_metadata_builds_aligned_frame_layout() -> None:
    fixture = json.loads(FIXTURE.read_text())
    channels = tuple(
        ScanChannel(item["name"], item["index"], parse_scan_type(item["type"]))
        for item in fixture["channels"]
    )

    layout = build_scan_layout(channels)

    assert [channel.offset for channel in layout.channels] == [0, 4, 8, 16]
    assert layout.frame_size == 24


@pytest.mark.parametrize(
    ("encoded", "byte_order", "signed", "real_bits", "storage_bits", "shift"),
    [
        ("le:s16/32>>0", "little", True, 16, 32, 0),
        ("be:u12/16>>4", "big", False, 12, 16, 4),
    ],
)
def test_scan_type_parser(encoded, byte_order, signed, real_bits, storage_bits, shift) -> None:
    scan_type = parse_scan_type(encoded)

    assert scan_type.byte_order == byte_order
    assert scan_type.signed is signed
    assert scan_type.real_bits == real_bits
    assert scan_type.storage_bits == storage_bits
    assert scan_type.shift == shift


def test_decoder_handles_signed_values_and_split_reads() -> None:
    layout = _target_layout()
    first = _frame(-2, 10_000_000_000)
    second = _frame(98, 10_100_000_000)

    samples = decode_frames((first[:7], first[7:] + second[:3], second[3:]), layout)

    assert [sample.values["hinge"] for sample in samples] == [-2, 98]
    assert [sample.timestamp_ns for sample in samples] == [10_000_000_000, 10_100_000_000]


def test_partial_final_frame_is_rejected() -> None:
    with pytest.raises(IIOError) as error:
        decode_frames((_frame(10, 1)[:-1],), _target_layout())

    assert error.value.code == "partial_iio_frame"


def test_disconnect_uses_stable_error_code() -> None:
    def chunks():
        yield _frame(10, 1)
        raise OSError("device path and serial")

    with pytest.raises(IIOError) as error:
        decode_frames(chunks(), _target_layout())

    assert error.value.code == "iio_device_disconnected"
    assert "serial" not in str(error.value)


def test_circular_angles_are_unwrapped_across_zero() -> None:
    assert unwrap_degrees((358.0, 1.0, 4.0)) == (358.0, 361.0, 364.0)


def test_realtime_iio_timestamp_is_normalized_to_monotonic_timeline() -> None:
    assert (
        normalize_timestamp_ns(
            1_800_000_000_100,
            monotonic_reference_ns=12_000_000_000,
            realtime_reference_ns=1_800_000_000_000,
        )
        == 12_000_000_100
    )


def test_monotonic_iio_timestamp_is_preserved() -> None:
    assert (
        normalize_timestamp_ns(
            12_000_000_100,
            monotonic_reference_ns=12_000_000_000,
            realtime_reference_ns=1_800_000_000_000,
        )
        == 12_000_000_100
    )


def test_discovery_uses_semantic_name_and_label_not_iio_number(tmp_path) -> None:
    fixture = json.loads(FIXTURE.read_text())
    device = tmp_path / "bus/iio/devices/iio:device77"
    scan = device / "scan_elements"
    scan.mkdir(parents=True)
    (device / "name").write_text("hinge\n")
    (device / "in_angl0_label").write_text("hinge\n")
    (device / "in_angl_scale").write_text(str(fixture["scale"]))
    (device / "in_angl_offset").write_text(str(fixture["offset"]))
    (device / "current_timestamp_clock").write_text("monotonic\n")
    for item in fixture["channels"]:
        kernel_name = "in_timestamp" if item["name"] == "timestamp" else "in_angl0"
        (scan / f"{kernel_name}_index").write_text(str(item["index"]))
        (scan / f"{kernel_name}_type").write_text(item["type"])

    metadata = discover_hinge_metadata(tmp_path)

    assert metadata.timestamp_clock == "monotonic"
    assert metadata.layout.frame_size == 16
    assert str(device) not in metadata.as_public_dict().values()


def test_buffer_configuration_is_restored_after_failure(tmp_path) -> None:
    device = _fake_hinge_device(tmp_path)
    metadata = discover_hinge_metadata(tmp_path)
    device_node = tmp_path / "dev/iio:device77"
    device_node.parent.mkdir()
    device_node.write_bytes(b"")

    with pytest.raises(RuntimeError), BufferedHingeDevice(metadata, device_node=device_node):
        assert (device / "current_timestamp_clock").read_text() == "monotonic"
        assert (device / "buffer0/in_angl0_en").read_text() == "1"
        assert (device / "buffer0/in_timestamp_en").read_text() == "1"
        assert (device / "buffer0/enable").read_text() == "1"
        raise RuntimeError

    assert (device / "current_timestamp_clock").read_text() == "realtime"
    assert (device / "buffer0/in_angl0_en").read_text() == "0"
    assert (device / "buffer0/in_timestamp_en").read_text() == "0"
    assert (device / "buffer0/enable").read_text() == "0"


def test_failed_buffer_configuration_rolls_back_partial_changes(tmp_path) -> None:
    device = _fake_hinge_device(tmp_path)
    metadata = discover_hinge_metadata(tmp_path)
    (device / "buffer0/in_timestamp_en").unlink()

    with (
        pytest.raises(IIOError) as error,
        BufferedHingeDevice(metadata, device_node=tmp_path / "missing-device"),
    ):
        pass

    assert error.value.code == "iio_buffer_configuration_failed"
    assert (device / "current_timestamp_clock").read_text() == "realtime"
    assert (device / "buffer0/in_angl0_en").read_text() == "0"
    assert (device / "buffer0/enable").read_text() == "0"


def test_nonblocking_chunk_reader_honors_stop_event(tmp_path) -> None:
    _fake_hinge_device(tmp_path)
    metadata = discover_hinge_metadata(tmp_path)
    device_node = tmp_path / "dev/iio:device77"
    device_node.parent.mkdir()
    device_node.write_bytes(_frame(42, 123))
    stop = ThreadEvent()

    with BufferedHingeDevice(metadata, device_node=device_node) as buffered:
        chunks = buffered.chunks(stop_event=stop)
        assert next(chunks) == _frame(42, 123)
        stop.set()
        assert tuple(chunks) == ()


def _target_layout():
    fixture = json.loads(FIXTURE.read_text())
    return build_scan_layout(
        tuple(
            ScanChannel(item["name"], item["index"], parse_scan_type(item["type"]))
            for item in fixture["channels"]
        )
    )


def _frame(hinge: int, timestamp_ns: int) -> bytes:
    return struct.pack("<iii4xq", hinge & 0xFFFF, 0, 0, timestamp_ns)


def _fake_hinge_device(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    device = tmp_path / "bus/iio/devices/iio:device77"
    scan = device / "scan_elements"
    buffer = device / "buffer0"
    scan.mkdir(parents=True)
    buffer.mkdir()
    (device / "name").write_text("hinge\n")
    (device / "in_angl0_label").write_text("hinge\n")
    (device / "in_angl_scale").write_text(str(fixture["scale"]))
    (device / "in_angl_offset").write_text(str(fixture["offset"]))
    (device / "current_timestamp_clock").write_text("realtime")
    for item in fixture["channels"]:
        if item["name"] not in {"hinge", "timestamp"}:
            continue
        kernel_name = "in_timestamp" if item["name"] == "timestamp" else "in_angl0"
        (scan / f"{kernel_name}_index").write_text(str(item["index"]))
        (scan / f"{kernel_name}_type").write_text(item["type"])
    for name, value in (
        ("enable", "0"),
        ("in_angl0_en", "0"),
        ("in_timestamp_en", "0"),
        ("length", "2"),
    ):
        (buffer / name).write_text(value)
    return device
