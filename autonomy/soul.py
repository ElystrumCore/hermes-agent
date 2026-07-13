"""Soul: the agent's identity (SOUL.md) + standing intentions (GOALS.md)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_CHECKLIST = re.compile(r"^\s*-\s*\[([ xX])\]\s+(.*\S)\s*$")


@dataclass(frozen=True)
class Goal:
    id: str
    text: str
    done: bool


class Soul:
    """Loads identity from SOUL.md and goals from GOALS.md (a markdown checklist)."""

    def __init__(self, soul_path: Optional[str] = None, goals_path: Optional[str] = None) -> None:
        self._soul_path = soul_path
        self._goals_path = goals_path

    def _identity(self) -> str:
        if self._soul_path and Path(self._soul_path).is_file():
            return Path(self._soul_path).read_text(encoding="utf-8")
        # Default: Hermes' HERMES_HOME/SOUL.md loader (imported lazily).
        try:
            from agent.prompt_builder import load_soul_md
            return load_soul_md() or ""
        except Exception:
            return ""

    def all_goals(self) -> list[Goal]:
        if not (self._goals_path and Path(self._goals_path).is_file()):
            return []
        goals: list[Goal] = []
        for line in Path(self._goals_path).read_text(encoding="utf-8").splitlines():
            m = _CHECKLIST.match(line)
            if m:
                goals.append(Goal(id=f"g{len(goals) + 1}", text=m.group(2), done=m.group(1).lower() == "x"))
        return goals

    def active_goals(self) -> list[Goal]:
        return [g for g in self.all_goals() if not g.done]

    def render(self) -> str:
        identity = self._identity().strip() or "(no SOUL.md identity configured)"
        return f"# Identity\n{identity}"


def remaining_goals(soul, memory) -> list[Goal]:
    """Active goals (unchecked in GOALS.md) MINUS those completed in memory this run.

    Completion is tracked in memory, not by mutating GOALS.md, so `soul.active_goals()`
    (which re-reads the static file) is NOT sufficient on its own — the loop and the
    heartbeat must both subtract memory-completed goals or a finished goal would stay
    "active" forever and the loop would never terminate. `memory` is duck-typed: it only
    needs `.completed_goals() -> set[str]`.
    """
    completed = memory.completed_goals()
    return [g for g in soul.active_goals() if g.id not in completed]
