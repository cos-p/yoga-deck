import asyncio

import pytest

from yoga_deck.coordinator import CoordinatorError, RotationCoordinator, SystemCoordinator
from yoga_deck.core import ApplyRotation, Orientation, SetInternalInputsEnabled


class FakeRotationAdapter:
    def __init__(self, fail=False) -> None:
        self.fail = fail
        self.calls = []

    async def apply(self, orientation):
        self.calls.append(orientation)
        if self.fail:
            raise OSError("private compositor output")


@pytest.mark.asyncio
async def test_rotation_effect_coordinates_all_targets_through_adapter() -> None:
    adapter = FakeRotationAdapter()
    coordinator = RotationCoordinator(adapter)

    await coordinator.apply(ApplyRotation(Orientation.LEFT, sequence=1))

    assert adapter.calls == [Orientation.LEFT]


@pytest.mark.asyncio
async def test_stale_and_duplicate_rotation_effects_are_ignored() -> None:
    adapter = FakeRotationAdapter()
    coordinator = RotationCoordinator(adapter)

    await coordinator.apply(ApplyRotation(Orientation.RIGHT, sequence=2))
    await coordinator.apply(ApplyRotation(Orientation.LEFT, sequence=1))
    await coordinator.apply(ApplyRotation(Orientation.RIGHT, sequence=2))

    assert adapter.calls == [Orientation.RIGHT]


@pytest.mark.asyncio
async def test_concurrent_rotation_finishes_with_newest_effect() -> None:
    adapter = FakeRotationAdapter()
    coordinator = RotationCoordinator(adapter)

    await asyncio.gather(
        coordinator.apply(ApplyRotation(Orientation.RIGHT, sequence=1)),
        coordinator.apply(ApplyRotation(Orientation.INVERTED, sequence=2)),
        coordinator.apply(ApplyRotation(Orientation.LEFT, sequence=3)),
    )

    assert adapter.calls[-1] is Orientation.LEFT
    assert coordinator.latest_sequence == 3


@pytest.mark.asyncio
async def test_rotation_failure_is_redacted_and_can_be_reconciled() -> None:
    adapter = FakeRotationAdapter(fail=True)
    coordinator = RotationCoordinator(adapter)

    with pytest.raises(CoordinatorError) as error:
        await coordinator.apply(ApplyRotation(Orientation.RIGHT, sequence=1))
    adapter.fail = False
    await coordinator.reconcile()

    assert error.value.code == "rotation_transaction_failed"
    assert adapter.calls == [Orientation.RIGHT, Orientation.NORMAL, Orientation.RIGHT]


@pytest.mark.asyncio
async def test_system_coordinator_rejects_stale_work_across_effect_domains() -> None:
    class Inputs:
        def __init__(self):
            self.calls = []

        async def apply(self, effect):
            self.calls.append(effect)

        async def recover_inputs(self):
            pass

        async def reconcile(self):
            pass

    inputs = Inputs()
    rotation_adapter = FakeRotationAdapter()
    system = SystemCoordinator(inputs, RotationCoordinator(rotation_adapter))

    await system.apply(SetInternalInputsEnabled(False, sequence=3))
    await system.apply(ApplyRotation(Orientation.LEFT, sequence=2))
    await system.apply(ApplyRotation(Orientation.RIGHT, sequence=4))

    assert len(inputs.calls) == 1
    assert rotation_adapter.calls == [Orientation.RIGHT]
    assert system.latest_sequence == 4
