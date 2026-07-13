"""Heartbeat: cadence + liveness pulse + the single termination authority."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from autonomy.time_util import utc_now


class Heartbeat:
    def __init__(
        self,
        interval: float = 0.0,
        max_ticks: int = 10,
        action_budget: int = 100,
        no_progress_window: int = 3,
        stop_file: Optional[str] = None,
    ) -> None:
        self.interval = interval
        self.max_ticks = max_ticks
        self.action_budget = action_budget
        self.no_progress_window = no_progress_window
        self.stop_file = stop_file
        self._tick = 0

    @property
    def tick_count(self) -> int:
        return self._tick

    def beat(self, memory_dir: str) -> None:
        self._tick += 1
        p = Path(memory_dir) / "pulse.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"tick": self._tick, "ts": utc_now()}, sort_keys=True) + "\n")

    def wait(self) -> None:
        if self.interval > 0:
            time.sleep(self.interval)

    def should_continue(self, soul, memory, action_count: int) -> tuple[bool, str]:
        from autonomy.soul import remaining_goals
        if self.stop_file and Path(self.stop_file).exists():
            return (False, "stop-file")
        if self._tick >= self.max_ticks:
            return (False, "max_ticks")
        if not remaining_goals(soul, memory):
            return (False, "all goals done")
        if action_count >= self.action_budget:
            return (False, "action budget")
        if memory.no_progress(self.no_progress_window):
            return (False, "no progress")
        return (True, "")
