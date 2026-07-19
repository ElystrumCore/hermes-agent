"""AutonomyLoop: the orchestrator + the governance seam (NullGate now)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from autonomy.hands import Outcome
from autonomy.memory import TickRecord
from autonomy.time_util import utc_now


@runtime_checkable
class Gate(Protocol):
    def before_decide(self, ctx: dict) -> None: ...
    def before_act(self, decision) -> Optional[str]: ...   # deny reason, or None to allow
    def after_act(self, decision, outcome) -> None: ...


class NullGate:
    """No-op gate. The agent-lineage trust kernel becomes GovernedGate here later."""
    def before_decide(self, ctx: dict) -> None:
        return None
    def before_act(self, decision) -> Optional[str]:
        return None
    def after_act(self, decision, outcome) -> None:
        return None


@dataclass
class RunReport:
    ticks: int = 0
    completed: list = field(default_factory=list)
    terminate_reason: str = ""
    failures: list = field(default_factory=list)
    summaries: list = field(default_factory=list)


class AutonomyLoop:
    def __init__(self, soul, memory, brain, hands, heartbeat, gate=None) -> None:
        self.soul = soul
        self.memory = memory
        self.brain = brain
        self.hands = hands
        self.heartbeat = heartbeat
        self.gate = gate or NullGate()
        self._action_count = 0

    def tick(self) -> TickRecord:
        from autonomy.soul import remaining_goals
        self.heartbeat.beat(self.memory.dir)
        goals = remaining_goals(self.soul, self.memory)
        recent = self.memory.recent(5)
        self.gate.before_decide({"goals": goals, "recent": recent})
        decision = self.brain.decide(self.soul.render(), goals, recent)

        if decision.idle:
            outcome = Outcome(ok=True, output="[idle]", error=None)
        else:
            deny = self.gate.before_act(decision)
            if deny is not None:
                outcome = Outcome(ok=False, output="", error=f"gate denied: {deny}")
            else:
                outcome = self.hands.act(decision)
                self._action_count += 1
                self.gate.after_act(decision, outcome)
                if outcome.ok and decision.done and decision.target_goal:
                    goal_text = next((g.text for g in self.soul.all_goals()
                                      if g.id == decision.target_goal), "")
                    if self.brain.verify_done(goal_text, outcome.output):
                        self.memory.mark_done(decision.target_goal)

        goal_states = {g.id: (g.id in self.memory.completed_goals()) for g in self.soul.all_goals()}
        rec = TickRecord(
            tick=self.heartbeat.tick_count, ts=utc_now(),
            decision=decision, outcome=outcome, goal_states=goal_states,
        )
        self.memory.record(rec)
        return rec

    def run(self) -> RunReport:
        report = RunReport()
        while True:
            cont, reason = self.heartbeat.should_continue(self.soul, self.memory, self._action_count)
            if not cont:
                report.terminate_reason = reason
                break
            rec = self.tick()
            report.ticks += 1
            report.summaries.append(
                f"#{rec.tick} goal={rec.decision.target_goal} action={rec.decision.action!r} -> "
                f"{'ok' if rec.outcome.ok else 'ERR:' + str(rec.outcome.error)}"
            )
            if not rec.outcome.ok:
                report.failures.append(report.summaries[-1])
            self.heartbeat.wait()
        report.completed = sorted(self.memory.completed_goals())
        return report
