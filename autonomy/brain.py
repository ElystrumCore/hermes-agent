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


def _fmt_recent(recent) -> str:
    lines = []
    for r in recent[-3:]:
        d = getattr(r, "decision", None)
        o = getattr(r, "outcome", None)
        act = getattr(d, "action", "") if d is not None else str(r)
        out = (getattr(o, "output", "") or "")[:120]
        lines.append(f"- did: {act!r} -> result: {out!r}")
    return "\n".join(lines) or "(none)"


class LiveBrain:
    """Live brain: a cheap planning call via Hermes _run_agent, fail-closed parse."""

    def __init__(self, model=None) -> None:
        self._model = model

    def decide(self, soul_render: str, goals, recent) -> Decision:
        try:
            from hermes_cli.oneshot import _run_agent
        except Exception as exc:  # noqa: BLE001 — surfaced, fail-closed
            return Decision(target_goal=None, action="", rationale=f"brain call failed: {exc}",
                            idle=True, done=False)
        goals_txt = "\n".join(f"- {g.id}: {g.text}" for g in goals) or "(none)"
        recent_txt = _fmt_recent(recent)
        prompt = (
            f"{soul_render}\n\n# Active goals\n{goals_txt}\n\n"
            f"# Recent actions and results\n{recent_txt}\n\n"
            "You are driving toward the TOP active goal. Decide the SINGLE next action.\n"
            '- "action" MUST be a plain natural-language instruction for a general assistant '
            '(e.g. "Summarize the top-level files and save a note"). NEVER a function or tool name.\n'
            '- Set "done": true ONLY if the goal\'s deliverable is already achieved — check the most '
            "recent action result above.\n"
            '- Set "idle": true only if nothing useful should be done now.\n'
            "Reply with ONE JSON object only: "
            '{"target_goal": <id or null>, "action": <string>, "rationale": <string>, '
            '"idle": <bool>, "done": <bool>}'
        )
        try:
            final_response, _ = _run_agent(prompt, model=self._model)
        except Exception as exc:  # noqa: BLE001
            return Decision(target_goal=None, action="", rationale=f"brain call failed: {exc}",
                            idle=True, done=False)
        raw = _extract_json(final_response or "")
        if raw is None:
            return Decision(target_goal=None, action="",
                            rationale=f"unparseable brain output: {(final_response or '')[:80]!r}",
                            idle=True, done=False)
        try:
            data = _json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            return Decision(target_goal=None, action="", rationale=f"bad json: {exc}",
                            idle=True, done=False)
        action = str(data.get("action", "")).strip()
        return Decision(
            target_goal=data.get("target_goal"),
            action=action,
            rationale=str(data.get("rationale", "")),
            idle=bool(data.get("idle", False)) or not action,
            done=bool(data.get("done", False)),
        )
