"""Hands: the actuator. MockHands (offline) + HermesHands (Task 7)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from autonomy.brain import Decision


@dataclass(frozen=True)
class Outcome:
    ok: bool
    output: str
    error: Optional[str] = None


@runtime_checkable
class Hands(Protocol):
    def act(self, decision: Decision) -> Outcome: ...


class MockHands:
    """Offline actuator: records the action, runs nothing."""

    def act(self, decision: Decision) -> Outcome:
        return Outcome(ok=True, output=f"[mock-hands] {decision.action}", error=None)
