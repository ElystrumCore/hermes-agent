from autonomy.brain import Decision
from autonomy.hands import Outcome
from autonomy.memory import Memory, TickRecord


def _rec(tick, action, done=False):
    return TickRecord(
        tick=tick, ts="2026-07-12T00:00:00Z",
        decision=Decision(target_goal="g1", action=action, rationale="", idle=False, done=done),
        outcome=Outcome(ok=True, output="ok"), goal_states={"g1": done},
    )


def test_records_persist_and_recent_returns_last_k(tmp_path):
    m = Memory(str(tmp_path))
    for i in range(5):
        m.record(_rec(i, f"a{i}"))
    assert (tmp_path / "memory.jsonl").exists()
    recent = m.recent(2)
    assert [r.tick for r in recent] == [3, 4]


def test_mark_done_tracks_completed_goals(tmp_path):
    m = Memory(str(tmp_path))
    m.mark_done("g1")
    assert m.completed_goals() == {"g1"}


def test_no_progress_true_when_same_action_repeats_without_completion(tmp_path):
    m = Memory(str(tmp_path))
    for i in range(3):
        m.record(_rec(i, "same action", done=False))
    assert m.no_progress(window=3) is True


def test_no_progress_false_when_actions_vary(tmp_path):
    m = Memory(str(tmp_path))
    for i in range(3):
        m.record(_rec(i, f"different {i}", done=False))
    assert m.no_progress(window=3) is False


def test_no_progress_false_when_a_goal_completed_in_window(tmp_path):
    m = Memory(str(tmp_path))
    m.record(_rec(0, "same action", done=False))
    m.record(_rec(1, "same action", done=False))
    m.record(_rec(2, "same action", done=True))
    assert m.no_progress(window=3) is False


def test_no_progress_window_zero_is_false(tmp_path):
    m = Memory(str(tmp_path))
    m.record(_rec(0, "same", done=False))
    assert m.no_progress(window=0) is False
