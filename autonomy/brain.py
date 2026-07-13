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


import json as _json
import re as _re

_JSON_OBJ = _re.compile(r"\{.*\}", _re.DOTALL)
_IDLE_FAIL = Decision(target_goal=None, action="", rationale="unparseable brain output", idle=True, done=False)


def _strip_think(text: str) -> str:
    return _re.sub(r"<think>.*?</think>", "", text or "", flags=_re.DOTALL | _re.IGNORECASE)


def _extract_json(text):
    """First COMPLETE balanced {...} object (after stripping <think> blocks), or None."""
    s = _strip_think(text)
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def _parse_yes(text: str) -> bool:
    """True iff the first standalone yes/no in the (think-stripped) text is 'yes'."""
    m = _re.search(r"\b(yes|no)\b", _strip_think(text).lower())
    return bool(m) and m.group(1) == "yes"


class LiveBrain:
    """Live brain: a cheap planning call via Hermes _run_agent, fail-closed parse."""

    def __init__(self, model=None) -> None:
        self._model = model

    def decide(self, soul_render: str, goals, recent) -> Decision:
        try:
            from hermes_cli.oneshot import _run_agent
            goals_txt = "\n".join(f"- {g.id}: {g.text}" for g in goals) or "(none)"
            recent_txt = "\n".join(getattr(r, "decision", r).action for r in recent[-3:]) or "(none)"
            prompt = (
                f"{soul_render}\n\n# Active goals\n{goals_txt}\n\n# Recent actions\n{recent_txt}\n\n"
                "Decide the single next action toward the top goal. Reply with ONE JSON object only: "
                '{"target_goal": <id or null>, "action": <string>, "rationale": <string>, '
                '"idle": <bool>, "done": <bool>}. Set idle=true only if nothing should be done now.'
            )
            final_response, _ = _run_agent(prompt, model=self._model)
            m = _JSON_OBJ.search(final_response or "")
            if not m:
                return _IDLE_FAIL
            data = _json.loads(m.group(0))
            action = str(data.get("action", "")).strip()
            return Decision(
                target_goal=data.get("target_goal"),
                action=action,
                rationale=str(data.get("rationale", "")),
                idle=bool(data.get("idle", False)) or not action,
                done=bool(data.get("done", False)),
            )
        except Exception:
            return _IDLE_FAIL
