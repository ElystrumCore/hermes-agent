"""Tests for the ``hermes persist-smoke`` CLI subcommand.

Mirrors the monkeypatched-dependency style of tests/hermes_cli/
test_required_enforcers.py and tests/agent/test_persist_boundary.py:
``hermes_cli.plugins.discover_plugins`` / ``get_plugin_manager`` and
``agent.persist_boundary.governed_persist`` are patched directly rather than
exercising the real plugin runtime, since ``run_persist_smoke`` imports each
of them locally (at call time) precisely so this works.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.persist_boundary import PersistResult
from hermes_cli import persist_smoke


class _FakeManager:
    def __init__(self, required_hooks=None, plugins=None):
        self._required_hooks = required_hooks or {}
        self._plugins = plugins or {}


def _loaded(name, enabled=True, error=None):
    return SimpleNamespace(
        manifest=SimpleNamespace(name=name), enabled=enabled, error=error
    )


def _args(as_json=True):
    return SimpleNamespace(json=as_json)


def _patch_discovery(monkeypatch, manager, *, spy=None):
    def fake_discover(force=False):
        if spy is not None:
            spy["force"] = force

    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", fake_discover)
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: manager)


_REGISTERED = {"pre_persist_write": [("agent-lineage", lambda **kw: None)]}


# ---------------------------------------------------------------------------
# Hook registered -- governed_persist is called and its outcome dictates
# the exit code / JSON envelope.
# ---------------------------------------------------------------------------


class TestHookRegistered:
    def test_discover_plugins_called_with_force_true(self, monkeypatch, capsys):
        manager = _FakeManager(required_hooks=_REGISTERED)
        spy: dict = {}
        _patch_discovery(monkeypatch, manager, spy=spy)
        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist",
            lambda kind, path, content, meta=None: PersistResult(
                staged=True, digest="sha256:" + "a" * 64, denied=False, message=""
            ),
        )

        persist_smoke.run_persist_smoke(_args())

        assert spy["force"] is True

    def test_staged_result_exits_zero_with_digest(self, monkeypatch, capsys):
        manager = _FakeManager(required_hooks=_REGISTERED)
        _patch_discovery(monkeypatch, manager)
        digest = "sha256:" + "a" * 64
        seen = {}

        def fake_governed_persist(kind, path, content, meta=None):
            seen["kind"] = kind
            seen["path"] = path
            seen["meta"] = meta
            return PersistResult(staged=True, digest=digest, denied=False, message="")

        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist", fake_governed_persist
        )

        # exit(0) doesn't raise SystemExit at all -- run_persist_smoke just
        # returns on the staged/ok path.
        persist_smoke.run_persist_smoke(_args())

        out = json.loads(capsys.readouterr().out)
        assert out == {
            "ok": True,
            "hooks": ["pre_persist_write"],
            "staged": True,
            "digest": digest,
            "mode": "staged",
        }
        assert seen["kind"] == "memory"
        assert seen["path"] == "persist-smoke/probe.md"
        assert seen["meta"] == {"origin": "persist-smoke"}

    def test_probe_content_includes_an_iso_timestamp(self, monkeypatch):
        manager = _FakeManager(required_hooks=_REGISTERED)
        _patch_discovery(monkeypatch, manager)
        seen = {}

        def fake_governed_persist(kind, path, content, meta=None):
            seen["content"] = content
            return PersistResult(
                staged=True, digest="sha256:" + "b" * 64, denied=False, message=""
            )

        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist", fake_governed_persist
        )

        persist_smoke.run_persist_smoke(_args())

        assert isinstance(seen["content"], bytes)
        text = seen["content"].decode("utf-8")
        # datetime.isoformat() always contains a literal "T" separator.
        assert "T" in text

    def test_denied_result_exits_one(self, monkeypatch, capsys):
        manager = _FakeManager(required_hooks=_REGISTERED)
        _patch_discovery(monkeypatch, manager)
        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist",
            lambda kind, path, content, meta=None: PersistResult(
                staged=False, digest=None, denied=True, message="POLICY_DENIED: nope"
            ),
        )

        with pytest.raises(SystemExit) as exc:
            persist_smoke.run_persist_smoke(_args())

        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out == {"ok": False, "mode": "denied", "message": "POLICY_DENIED: nope"}

    def test_passthrough_result_exits_one_and_cleans_up_probe(
        self, monkeypatch, capsys, tmp_path
    ):
        manager = _FakeManager(required_hooks=_REGISTERED)
        _patch_discovery(monkeypatch, manager)
        monkeypatch.chdir(tmp_path)

        def fake_governed_persist(kind, path, content, meta=None):
            # Simulate governed_persist's own pre-cutover canonical write --
            # exactly the file run_persist_smoke is responsible for cleaning
            # up on this outcome.
            probe = tmp_path / path
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_bytes(content)
            return PersistResult(staged=False, digest=None, denied=False, message="")

        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist", fake_governed_persist
        )

        with pytest.raises(SystemExit) as exc:
            persist_smoke.run_persist_smoke(_args())

        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out == {
            "ok": False,
            "mode": "passthrough",
            "reason": "no enforcer registered — governed config required for the smoke",
        }
        assert not (tmp_path / "persist-smoke" / "probe.md").exists()

    def test_human_mode_does_not_crash_and_is_not_json(self, monkeypatch, capsys):
        manager = _FakeManager(required_hooks=_REGISTERED)
        _patch_discovery(monkeypatch, manager)
        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist",
            lambda kind, path, content, meta=None: PersistResult(
                staged=True, digest="sha256:" + "c" * 64, denied=False, message=""
            ),
        )

        persist_smoke.run_persist_smoke(_args(as_json=False))

        out = capsys.readouterr().out
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)
        assert "OK" in out


# ---------------------------------------------------------------------------
# Hook absent -- exits 1 before ever calling governed_persist, with a
# diagnosis distinguishing "no plugin discovered" from "plugin present but
# this hook is missing".
# ---------------------------------------------------------------------------


class TestHookAbsent:
    def test_plugin_not_discovered_diagnosis(self, monkeypatch, capsys):
        manager = _FakeManager()
        _patch_discovery(monkeypatch, manager)
        called = {"governed_persist": False}
        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist",
            lambda *a, **kw: called.__setitem__("governed_persist", True),
        )

        with pytest.raises(SystemExit) as exc:
            persist_smoke.run_persist_smoke(_args())

        assert exc.value.code == 1
        assert called["governed_persist"] is False
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False
        assert out["mode"] == "no_hook"
        assert "no 'agent-lineage' plugin was discovered" in out["message"]

    def test_plugin_present_hook_missing_diagnosis(self, monkeypatch, capsys):
        manager = _FakeManager(plugins={"agent-lineage": _loaded("agent-lineage")})
        _patch_discovery(monkeypatch, manager)

        with pytest.raises(SystemExit) as exc:
            persist_smoke.run_persist_smoke(_args())

        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is False
        assert out["mode"] == "no_hook"
        assert "'agent-lineage' plugin was discovered" in out["message"]
        assert "stale plugin version" in out["message"]

    def test_plugin_present_under_different_key_is_still_found_by_name(
        self, monkeypatch, capsys
    ):
        # A plugin discovered under a path-derived key (e.g. nested under a
        # category) rather than the bare name must still be recognized by
        # its manifest.name -- the diagnosis distinguishes "not discovered"
        # from "discovered, hook missing", not "found under this exact key".
        manager = _FakeManager(
            plugins={"custom/agent-lineage": _loaded("agent-lineage")}
        )
        _patch_discovery(monkeypatch, manager)

        with pytest.raises(SystemExit):
            persist_smoke.run_persist_smoke(_args())

        out = json.loads(capsys.readouterr().out)
        assert "stale plugin version" in out["message"]
