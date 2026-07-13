"""Memory: the loop's episodic memory (JSONL of TickRecords)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from autonomy.brain import Decision
from autonomy.hands import Outcome


@dataclass(frozen=True)
class TickRecord:
    tick: int
    ts: str
    decision: Decision
    outcome: Outcome
    goal_states: dict


class Memory:
    def __init__(self, memory_dir: str) -> None:
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "memory.jsonl"
        self._records: list[TickRecord] = []
        self._done: set[str] = set()

    def record(self, rec: TickRecord) -> None:
        self._records.append(rec)
        with self._path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(asdict(rec), sort_keys=True) + "\n")

    def recent(self, k: int) -> list[TickRecord]:
        return self._records[-k:] if k > 0 else []

    def mark_done(self, goal_id: str) -> None:
        self._done.add(goal_id)

    def completed_goals(self) -> set:
        return set(self._done)

    def no_progress(self, window: int) -> bool:
        if len(self._records) < window:
            return False
        last = self._records[-window:]
        if any(r.decision.done for r in last):
            return False
        return len({r.decision.action for r in last}) <= 1
