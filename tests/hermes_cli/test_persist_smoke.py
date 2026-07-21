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
        # Each invocation mints its own session id (see
        # TestUniqueSession below) -- only "origin" is fixed.
        assert seen["meta"]["origin"] == "persist-smoke"
        assert seen["meta"]["session_id"].startswith("persist-smoke:")

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
        # The now-empty persist-smoke/ directory is cleaned up too, not
        # just the probe file inside it.
        assert not (tmp_path / "persist-smoke").exists()

    def test_passthrough_leaves_other_files_in_probe_dir_alone(
        self, monkeypatch, capsys, tmp_path
    ):
        # If persist-smoke/ isn't empty after the probe is removed (an
        # unrelated file happens to live alongside it), rmdir must fail
        # silently rather than raising -- the cleanup is best-effort.
        manager = _FakeManager(required_hooks=_REGISTERED)
        _patch_discovery(monkeypatch, manager)
        monkeypatch.chdir(tmp_path)
        sibling = tmp_path / "persist-smoke" / "keepme.txt"

        def fake_governed_persist(kind, path, content, meta=None):
            probe = tmp_path / path
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_bytes(content)
            sibling.write_text("do not delete")
            return PersistResult(staged=False, digest=None, denied=False, message="")

        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist", fake_governed_persist
        )

        with pytest.raises(SystemExit) as exc:
            persist_smoke.run_persist_smoke(_args())

        assert exc.value.code == 1
        assert not (tmp_path / "persist-smoke" / "probe.md").exists()
        assert sibling.exists()

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
# governed_persist reports staged=True for TWO different reasons: a genuine
# policy-decision stage (message == "") and its own decision-less local
# fallback (a non-empty message). Only the former is green.
# ---------------------------------------------------------------------------


class TestLocalFallback:
    def test_local_fallback_result_exits_one_not_zero(self, monkeypatch, capsys):
        manager = _FakeManager(required_hooks=_REGISTERED)
        _patch_discovery(monkeypatch, manager)
        digest = "sha256:" + "d" * 64
        fallback_message = (
            "staged locally: no policy enforcer reachable for pre_persist_write"
        )
        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist",
            lambda kind, path, content, meta=None: PersistResult(
                staged=True, digest=digest, denied=False, message=fallback_message
            ),
        )

        with pytest.raises(SystemExit) as exc:
            persist_smoke.run_persist_smoke(_args())

        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out == {
            "ok": False,
            "mode": "local_fallback",
            "digest": digest,
            "message": fallback_message,
        }

    def test_local_fallback_human_mode_reports_fail_not_ok(self, monkeypatch, capsys):
        manager = _FakeManager(required_hooks=_REGISTERED)
        _patch_discovery(monkeypatch, manager)
        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist",
            lambda kind, path, content, meta=None: PersistResult(
                staged=True,
                digest="sha256:" + "d" * 64,
                denied=False,
                message="staged locally: no policy enforcer reachable",
            ),
        )

        with pytest.raises(SystemExit) as exc:
            persist_smoke.run_persist_smoke(_args(as_json=False))

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "OK" not in out


# ---------------------------------------------------------------------------
# Each invocation must mint its own session id rather than reuse a fixed
# default -- otherwise a periodic smoke exhausts its own shared budget over
# time and starts self-denying.
# ---------------------------------------------------------------------------


class TestUniqueSession:
    def test_two_invocations_get_two_distinct_session_ids(self, monkeypatch):
        manager = _FakeManager(required_hooks=_REGISTERED)
        _patch_discovery(monkeypatch, manager)
        seen_sessions = []

        def fake_governed_persist(kind, path, content, meta=None):
            seen_sessions.append((meta or {}).get("session_id"))
            return PersistResult(
                staged=True, digest="sha256:" + "e" * 64, denied=False, message=""
            )

        monkeypatch.setattr(
            "agent.persist_boundary.governed_persist", fake_governed_persist
        )

        persist_smoke.run_persist_smoke(_args())
        persist_smoke.run_persist_smoke(_args())

        assert len(seen_sessions) == 2
        assert all(seen_sessions)
        assert seen_sessions[0] != seen_sessions[1]
        assert all(s.startswith("persist-smoke:") for s in seen_sessions)


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


# ---------------------------------------------------------------------------
# discover_plugins(force=True) (or registry introspection right after it)
# raising -- a fail-loud duplicate-required-plugin-name RequiredPluginError,
# or any other discovery-time exception -- must produce a clean JSON
# diagnostic, exit 1, and never reach governed_persist. Mirrors main.py's own
# CLI-startup handling of the same call.
# ---------------------------------------------------------------------------


class TestDiscoveryFailed:
    def test_required_plugin_error_is_diagnosed_not_raised(self, monkeypatch, capsys):
        from hermes_cli.plugins import RequiredPluginError

        def fake_discover(force=False):
            raise RequiredPluginError(
                "required plugin name 'agent-lineage' is ambiguous; configure "
                "its path-derived plugin key"
            )

        monkeypatch.setattr("hermes_cli.plugins.discover_plugins", fake_discover)
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
        assert out["mode"] == "discovery-failed"
        assert "agent-lineage" in out["error"]
        assert "ambiguous" in out["error"]
        assert "hint" in out

    def test_generic_discovery_exception_is_diagnosed_not_raised(
        self, monkeypatch, capsys
    ):
        def fake_discover(force=False):
            raise RuntimeError("plugin directory unreadable")

        monkeypatch.setattr("hermes_cli.plugins.discover_plugins", fake_discover)

        with pytest.raises(SystemExit) as exc:
            persist_smoke.run_persist_smoke(_args())

        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out == {
            "ok": False,
            "mode": "discovery-failed",
            "error": "plugin directory unreadable",
            "hint": "hermes startup itself would abort — fix plugin discovery first",
        }

    def test_registry_introspection_failure_after_discovery_is_diagnosed(
        self, monkeypatch, capsys
    ):
        # discover_plugins() itself can succeed while get_plugin_manager()
        # (or reading its registry) is what actually blows up -- both are
        # inside the same guarded block.
        def fake_discover(force=False):
            return None

        def fake_get_manager():
            raise RuntimeError("plugin manager singleton not initialized")

        monkeypatch.setattr("hermes_cli.plugins.discover_plugins", fake_discover)
        monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", fake_get_manager)

        with pytest.raises(SystemExit) as exc:
            persist_smoke.run_persist_smoke(_args())

        assert exc.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["mode"] == "discovery-failed"
        assert "not initialized" in out["error"]

    def test_human_mode_discovery_failed_is_fail_not_json(self, monkeypatch, capsys):
        def fake_discover(force=False):
            raise RuntimeError("boom")

        monkeypatch.setattr("hermes_cli.plugins.discover_plugins", fake_discover)

        with pytest.raises(SystemExit):
            persist_smoke.run_persist_smoke(_args(as_json=False))

        out = capsys.readouterr().out
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)
        assert "FAIL" in out
        assert "boom" in out
