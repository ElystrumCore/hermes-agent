from autonomy.heartbeat import Heartbeat
from autonomy.soul import Goal


class _Soul:
    def __init__(self, active):
        self._active = active
    def active_goals(self):
        return self._active


class _Mem:
    def __init__(self, stuck=False, done=()):
        self._stuck = stuck
        self._done = set(done)
    def no_progress(self, window):
        return self._stuck
    def completed_goals(self):
        return self._done


def test_continue_when_all_bounds_ok():
    hb = Heartbeat(max_ticks=10, action_budget=100, no_progress_window=3)
    ok, reason = hb.should_continue(_Soul([Goal("g1", "A", False)]), _Mem(), action_count=0)
    assert ok is True and reason == ""


def test_stops_on_all_goals_done():
    hb = Heartbeat()
    ok, reason = hb.should_continue(_Soul([]), _Mem(), action_count=0)
    assert ok is False and reason == "all goals done"


def test_stops_on_max_ticks():
    hb = Heartbeat(max_ticks=2)
    hb._tick = 2
    ok, reason = hb.should_continue(_Soul([Goal("g1", "A", False)]), _Mem(), action_count=0)
    assert ok is False and reason == "max_ticks"


def test_stops_on_action_budget():
    hb = Heartbeat(action_budget=5)
    ok, reason = hb.should_continue(_Soul([Goal("g1", "A", False)]), _Mem(), action_count=5)
    assert ok is False and reason == "action budget"


def test_stops_on_no_progress():
    hb = Heartbeat(no_progress_window=3)
    ok, reason = hb.should_continue(_Soul([Goal("g1", "A", False)]), _Mem(stuck=True), action_count=0)
    assert ok is False and reason == "no progress"


def test_stops_on_stop_file(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("x", encoding="utf-8")
    hb = Heartbeat(stop_file=str(stop))
    ok, reason = hb.should_continue(_Soul([Goal("g1", "A", False)]), _Mem(), action_count=0)
    assert ok is False and reason == "stop-file"
