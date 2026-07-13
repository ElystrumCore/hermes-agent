import os
import sys
import types

import pytest

from autonomy.brain import Decision, LiveBrain
from autonomy.hands import HermesHands, Outcome
from autonomy.soul import Goal


def _fake_oneshot(monkeypatch, fn):
    """Install a fake hermes_cli.oneshot module whose _run_agent is fn."""
    pkg = sys.modules.get("hermes_cli") or types.ModuleType("hermes_cli")
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    mod = types.ModuleType("hermes_cli.oneshot")
    mod._run_agent = fn
    monkeypatch.setitem(sys.modules, "hermes_cli.oneshot", mod)


def test_live_brain_parses_valid_json(monkeypatch):
    _fake_oneshot(monkeypatch, lambda prompt, model=None: (
        'Sure: {"target_goal":"g1","action":"do X","rationale":"why","idle":false,"done":false} done',
        {},
    ))
    d = LiveBrain().decide("id", [Goal("g1", "A", False)], [])
    assert d.target_goal == "g1" and d.action == "do X" and d.idle is False


def test_live_brain_empty_action_forces_idle(monkeypatch):
    _fake_oneshot(monkeypatch, lambda prompt, model=None: (
        '{"target_goal":"g1","rationale":"x"}',
        {},
    ))
    d = LiveBrain().decide("id", [Goal("g1", "A", False)], [])
    assert d.idle is True and d.action == ""


def test_live_brain_fail_closed_on_garbage(monkeypatch):
    _fake_oneshot(monkeypatch, lambda prompt, model=None: ("no json here at all", {}))
    d = LiveBrain().decide("id", [Goal("g1", "A", False)], [])
    assert d.idle is True and d.action == "" and "unparseable" in d.rationale


def test_live_brain_fail_closed_on_exception(monkeypatch):
    def boom(prompt, model=None):
        raise RuntimeError("model down")
    _fake_oneshot(monkeypatch, boom)
    d = LiveBrain().decide("id", [Goal("g1", "A", False)], [])
    assert d.idle is True and "brain call failed" in d.rationale


def test_hermes_hands_returns_final_response(monkeypatch):
    _fake_oneshot(monkeypatch, lambda prompt, model=None: ("the result text", {"usage": 1}))
    out = HermesHands().act(Decision("g1", "do the thing", "", False, False))
    assert out.ok is True and out.output == "the result text"


def test_hermes_hands_failure_is_recorded_not_raised(monkeypatch):
    def boom(prompt, model=None):
        raise RuntimeError("model down")
    _fake_oneshot(monkeypatch, boom)
    out = HermesHands().act(Decision("g1", "do the thing", "", False, False))
    assert out.ok is False and "model down" in out.error


@pytest.mark.skipif(os.environ.get("HERMES_AUTONOMY_LIVE") != "1",
                    reason="HERMES_AUTONOMY_LIVE!=1 (needs a real Hermes model config)")
def test_live_leg_real_run(tmp_path):
    out = HermesHands().act(Decision("g1", "Reply with the single word: pong.", "", False, False))
    assert isinstance(out, Outcome)
    assert out.ok is True and out.output.strip() != ""


def test_live_brain_surfaces_call_error_in_rationale(monkeypatch):
    def boom(prompt, model=None):
        raise RuntimeError("model down")
    _fake_oneshot(monkeypatch, boom)
    d = LiveBrain().decide("id", [Goal("g1", "A", False)], [])
    assert d.idle is True and "brain call failed" in d.rationale and "model down" in d.rationale


def test_live_brain_surfaces_unparseable_in_rationale(monkeypatch):
    _fake_oneshot(monkeypatch, lambda prompt, model=None: ("no json here", {}))
    d = LiveBrain().decide("id", [Goal("g1", "A", False)], [])
    assert d.idle is True and "unparseable" in d.rationale


def test_live_brain_surfaces_bad_json_in_rationale(monkeypatch):
    _fake_oneshot(monkeypatch, lambda prompt, model=None: ('{"action": "x", bad}', {}))
    d = LiveBrain().decide("id", [Goal("g1", "A", False)], [])
    assert d.idle is True and "bad json" in d.rationale


def test_live_brain_parses_valid_through_think(monkeypatch):
    _fake_oneshot(monkeypatch, lambda prompt, model=None: (
        '<think>plan</think>{"target_goal":"g1","action":"Summarize the files","idle":false,"done":false}', {}))
    d = LiveBrain().decide("id", [Goal("g1", "A", False)], [])
    assert d.target_goal == "g1" and d.action == "Summarize the files" and d.idle is False


def test_fmt_recent_includes_action_and_result():
    from autonomy.brain import _fmt_recent
    from autonomy.hands import Outcome
    from autonomy.memory import TickRecord
    rec = TickRecord(tick=1, ts="t", decision=Decision("g1", "did a thing", "", False, False),
                     outcome=Outcome(ok=True, output="the result"), goal_states={})
    s = _fmt_recent([rec])
    assert "did a thing" in s and "the result" in s
