import sys
import types

from autonomy.brain import LiveBrain, MockBrain


def _fake_oneshot(monkeypatch, fn):
    pkg = sys.modules.get("hermes_cli") or types.ModuleType("hermes_cli")
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    mod = types.ModuleType("hermes_cli.oneshot")
    mod._run_agent = fn
    monkeypatch.setitem(sys.modules, "hermes_cli.oneshot", mod)


def test_mock_brain_verify_done_true():
    assert MockBrain().verify_done("goal", "output") is True


def test_live_verify_done_yes(monkeypatch):
    _fake_oneshot(monkeypatch, lambda prompt, model=None: ("yes", {}))
    assert LiveBrain().verify_done("Summarize files", "here is the summary") is True


def test_live_verify_done_no(monkeypatch):
    _fake_oneshot(monkeypatch, lambda prompt, model=None: ("no, not yet", {}))
    assert LiveBrain().verify_done("Summarize files", "partial") is False


def test_live_verify_done_through_think(monkeypatch):
    _fake_oneshot(monkeypatch, lambda prompt, model=None: ("<think>assessing</think>\nyes", {}))
    assert LiveBrain().verify_done("g", "o") is True


def test_live_verify_done_fail_closed_on_exception(monkeypatch):
    def boom(prompt, model=None):
        raise RuntimeError("down")
    _fake_oneshot(monkeypatch, boom)
    assert LiveBrain().verify_done("g", "o") is False
