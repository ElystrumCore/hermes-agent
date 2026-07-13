from autonomy.brain import MockBrain
from autonomy.hands import MockHands
from autonomy.heartbeat import Heartbeat
from autonomy.loop import AutonomyLoop, NullGate
from autonomy.memory import Memory
from autonomy.soul import Soul


def _soul(tmp_path, goals="# Goals\n- [ ] A\n- [ ] B\n"):
    (tmp_path / "SOUL.md").write_text("test agent", encoding="utf-8")
    (tmp_path / "GOALS.md").write_text(goals, encoding="utf-8")
    return Soul(soul_path=str(tmp_path / "SOUL.md"), goals_path=str(tmp_path / "GOALS.md"))


def _loop(tmp_path, brain=None, hands=None, hb=None, gate=None, goals="# Goals\n- [ ] A\n- [ ] B\n"):
    soul = _soul(tmp_path, goals)
    mem = Memory(str(tmp_path / "mem"))
    return AutonomyLoop(
        soul, mem, brain or MockBrain(ticks_per_goal=1), hands or MockHands(),
        hb or Heartbeat(max_ticks=20, no_progress_window=99), gate or NullGate(),
    )


def test_loop_completes_all_goals_and_terminates(tmp_path):
    loop = _loop(tmp_path)
    report = loop.run()
    assert report.terminate_reason == "all goals done"
    assert set(report.completed) == {"g1", "g2"}
    assert report.ticks >= 2
    assert report.failures == []


def test_loop_honors_max_ticks(tmp_path):
    loop = _loop(tmp_path, brain=MockBrain(ticks_per_goal=9999),
                 hb=Heartbeat(max_ticks=3, no_progress_window=99))
    report = loop.run()
    assert report.terminate_reason == "max_ticks" and report.ticks == 3


def test_loop_no_progress_kill(tmp_path):
    loop = _loop(tmp_path, brain=MockBrain(ticks_per_goal=9999),
                 hb=Heartbeat(max_ticks=99, no_progress_window=3))
    report = loop.run()
    assert report.terminate_reason == "no progress"


def test_loop_idle_when_no_goals(tmp_path):
    loop = _loop(tmp_path, goals="# Goals\n")
    report = loop.run()
    assert report.terminate_reason == "all goals done" and report.ticks == 0


class _DenyGate:
    def before_decide(self, ctx): return None
    def before_act(self, decision): return "test-deny"
    def after_act(self, decision, outcome): return None


def test_deny_gate_records_skipped_outcome_and_does_not_act(tmp_path):
    loop = _loop(tmp_path, gate=_DenyGate(), hb=Heartbeat(max_ticks=1, no_progress_window=99))
    report = loop.run()
    assert any("gate denied" in f for f in report.failures)
    assert report.completed == []          # act never ran -> no completion
