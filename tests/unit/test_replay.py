from pathlib import Path

from yoga_deck.core import ApplyRotation, SetInternalInputsEnabled
from yoga_deck.diagnostics.journal import loads_jsonl
from yoga_deck.diagnostics.replay import replay

FIXTURE = Path(__file__).parents[1] / "fixtures/events/fold_rotate_unfold.jsonl"


def test_sanitized_fixture_replays_to_deterministic_trace() -> None:
    records = loads_jsonl(FIXTURE.read_text())

    first = replay(records)
    second = replay(records)

    assert first == second
    assert first.final_state.internal_inputs_enabled is True
    assert first.final_state.applied_orientation.value == "left"
    assert any(isinstance(effect, SetInternalInputsEnabled) for effect in first.effects)
    assert any(isinstance(effect, ApplyRotation) for effect in first.effects)


def test_replay_trace_sequence_is_monotonic() -> None:
    trace = replay(loads_jsonl(FIXTURE.read_text()))

    sequences = [step.state.sequence for step in trace.steps]
    assert sequences == sorted(sequences)
