import json

import pytest

from yoga_deck.diagnostics.coordinate_oracle import OracleError, parse_samples


def test_parse_samples_requires_ordered_touch_then_pen_points() -> None:
    records = []
    for role in ("touch", "pen"):
        for index in range(5):
            records.append(json.dumps({"role": role, "index": index, "x": 10.0, "y": 20.0}))

    samples = parse_samples("\n".join(records))

    assert tuple(samples) == ("touch", "pen")
    assert len(samples["touch"]) == 5


def test_parse_samples_rejects_missing_or_reordered_points() -> None:
    with pytest.raises(OracleError, match="oracle_protocol_invalid"):
        parse_samples('{"role":"touch","index":1,"x":10,"y":20}\n')


def test_oracle_qml_does_not_collect_unique_device_ids() -> None:
    from yoga_deck.diagnostics.coordinate_oracle import QML_PATH

    source = QML_PATH.read_text()
    assert "uniqueId" not in source
    assert "PointerDevice.TouchScreen" in source
    assert "PointerDevice.Stylus" in source
    assert "acceptanceRadius" in source
