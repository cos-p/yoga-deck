import os

import pytest

from yoga_deck.adapters.sensor_proxy import SensorProxyError, SensorProxySource
from yoga_deck.core import Orientation

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(
        os.environ.get("YOGA_DECK_HARDWARE") != "1",
        reason="set YOGA_DECK_HARDWARE=1 for physical sensor tests",
    ),
]


@pytest.mark.asyncio
async def test_live_sensor_proxy_connection():
    """Verify live D-Bus connection to net.hadess.SensorProxy."""
    source = SensorProxySource()
    try:
        session = await source.connect()
    except SensorProxyError as err:
        pytest.skip(f"SensorProxy not running on system bus: {err}")

    valid_orientations = (*tuple(Orientation), None)
    assert session.current_event.orientation in valid_orientations
    await session.close()
