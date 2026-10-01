"""Deterministic replay of normalized records through the pure reducer."""

from __future__ import annotations

from dataclasses import dataclass

from yoga_deck.core import Effect, State, Transition, reduce

from .journal import EventRecord


@dataclass(frozen=True, slots=True)
class ReplayStep:
    record: EventRecord
    state: State
    effects: tuple[Effect, ...]


@dataclass(frozen=True, slots=True)
class ReplayTrace:
    steps: tuple[ReplayStep, ...]
    final_state: State

    @property
    def effects(self) -> tuple[Effect, ...]:
        return tuple(effect for step in self.steps for effect in step.effects)

    def as_dict(self) -> dict[str, object]:
        return {
            "events": len(self.steps),
            "effects": len(self.effects),
            "final_state": {
                "sequence": self.final_state.sequence,
                "internal_inputs_enabled": self.final_state.internal_inputs_enabled,
                "orientation": self.final_state.applied_orientation.value,
                "orientation_locked": self.final_state.orientation_locked,
                "shutdown_requested": self.final_state.shutdown_requested,
            },
        }


def replay(records: tuple[EventRecord, ...], initial_state: State | None = None) -> ReplayTrace:
    state = initial_state or State()
    steps: list[ReplayStep] = []
    for record in records:
        transition: Transition = reduce(state, record.event)
        state = transition.state
        steps.append(ReplayStep(record, state, transition.effects))
    return ReplayTrace(tuple(steps), state)
