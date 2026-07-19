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


class HermesHands:
    """Live actuator: execute the action via Hermes _run_agent (a full one-shot run)."""

    def __init__(self, model=None) -> None:
        self._model = model

    def act(self, decision: Decision) -> Outcome:
        try:
            from hermes_cli.oneshot import _run_agent
            final_response, _ = _run_agent(decision.action, model=self._model)
            return Outcome(ok=True, output=final_response or "", error=None)
        except Exception as exc:  # noqa: BLE001 — a failed action is recorded, never crashes the loop
            return Outcome(ok=False, output="", error=str(exc))
