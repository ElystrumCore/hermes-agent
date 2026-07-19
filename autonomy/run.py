"""autonomy.run: the driving harness. `python -m autonomy.run [flags]`."""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Optional

from autonomy.brain import MockBrain
from autonomy.hands import MockHands
from autonomy.heartbeat import Heartbeat
from autonomy.loop import AutonomyLoop
from autonomy.memory import Memory
from autonomy.soul import Soul


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="autonomy.run", description="Drive Hermes from a GOALS.md.")
    ap.add_argument("--goals", default="autonomy/GOALS.md")
    ap.add_argument("--soul", default="autonomy/SOUL.md")
    ap.add_argument("--ticks", type=int, default=5)
    ap.add_argument("--interval", type=float, default=0.0)
    ap.add_argument("--action-budget", type=int, default=100)
    ap.add_argument("--no-progress-window", type=int, default=3)
    ap.add_argument("--memory-dir", default=None)
    ap.add_argument("--stop-file", default=None)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--write-back-goals", action="store_true")
    args = ap.parse_args(argv)

    memory_dir = args.memory_dir or tempfile.mkdtemp(prefix="autonomy-")
    soul = Soul(soul_path=args.soul, goals_path=args.goals)
    memory = Memory(memory_dir)

    if args.live:
        from autonomy.brain import LiveBrain
        from autonomy.hands import HermesHands
        brain = LiveBrain(model=args.model)
        hands = HermesHands(model=args.model)
    else:
        brain = MockBrain(ticks_per_goal=1)
        hands = MockHands()

    heartbeat = Heartbeat(
        interval=args.interval, max_ticks=args.ticks, action_budget=args.action_budget,
        no_progress_window=args.no_progress_window, stop_file=args.stop_file,
    )
    loop = AutonomyLoop(soul, memory, brain, hands, heartbeat)

    print(f"[autonomy] driving {'LIVE' if args.live else 'MOCK'} — goals={args.goals} memory={memory_dir}")
    report = loop.run()
    for line in report.summaries:
        print(line)
    print(f"[autonomy] terminated: {report.terminate_reason} | ticks={report.ticks} "
          f"| completed={report.completed} | failures={len(report.failures)}")
    for f in report.failures:
        print(f"  FAIL {f}")

    if args.write_back_goals and report.completed:
        _write_back(args.goals, report.completed)
    return 0


import re as _re

_WB_CHECKLIST = _re.compile(r"^(\s*-\s*\[)([ xX])(\]\s+.*\S\s*)$")


def _write_back(goals_path: str, completed) -> None:
    """Best-effort: tick the checkbox for completed goals BY id/position (dup-text safe)."""
    completed = set(completed)
    p = Path(goals_path)
    out, n = [], 0
    for line in p.read_text(encoding="utf-8").splitlines():
        m = _WB_CHECKLIST.match(line)
        if m:
            n += 1
            if f"g{n}" in completed and m.group(2) == " ":
                line = f"{m.group(1)}x{m.group(3)}"
        out.append(line)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
