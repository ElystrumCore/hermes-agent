"""Brain: the deciding organ. MockBrain (deterministic) + LiveBrain (Task 7)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from autonomy.soul import Goal


@dataclass(frozen=True)
class Decision:
    target_goal: Optional[str]
    action: str
    rationale: str
    idle: bool
    done: bool


@runtime_checkable
class Brain(Protocol):
    def decide(self, soul_render: str, goals: list[Goal], recent: list) -> Decision: ...


class MockBrain:
    """Deterministic offline brain: work the top goal, finish it after ticks_per_goal ticks."""

    def __init__(self, ticks_per_goal: int = 1) -> None:
        self._ticks_per_goal = ticks_per_goal
        self._worked: dict[str, int] = {}

    def decide(self, soul_render: str, goals: list[Goal], recent: list) -> Decision:
        if not goals:
            return Decision(target_goal=None, action="", rationale="no active goals", idle=True, done=False)
        goal = goals[0]
        self._worked[goal.id] = self._worked.get(goal.id, 0) + 1
        done = self._worked[goal.id] >= self._ticks_per_goal
        return Decision(
            target_goal=goal.id,
            action=f"[mock] work on {goal.text}",
            rationale="top active goal",
            idle=False,
            done=done,
        )
