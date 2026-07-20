"""Tests for agent.persist_boundary -- the governed persistence funnel.

Covers the funnel's own decision logic (``governed_persist``), its wiring
into the two real write sites it replaced (``tools/memory_tool.py::
MemoryStore._write_file``, ``tools/skill_manager_tool.py::_atomic_write_text``
-- ``agent/learning_mutations.py`` shares the memory site's underlying
``_write_file``, so it is covered by the same funnel automatically), and an
AST-scoped source-scan pin asserting no canonical memory/skill write
primitive survives outside the funnel module.

Mirrors the monkeypatched-``get_required_hook_directive`` style of
tests/hermes_cli/test_required_enforcers.py: the funnel calls
``hermes_cli.plugins.get_required_hook_directive("pre_persist_write", ...)``
exactly the way cron/scheduler.py and hermes_cli/kanban_db.py call it for
``pre_run_start`` -- no real plugin runtime is loaded here, the directive is
supplied directly.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from agent import persist_boundary as pb
from agent.persist_boundary import PersistDenied, PersistResult, governed_persist


def _directive(action, **extra):
    def _fake(hook_name, **kwargs):
        assert hook_name == "pre_persist_write"
        return {"action": action, **extra}
    return _fake


# ---------------------------------------------------------------------------
# governed_persist -- input validation
# ---------------------------------------------------------------------------


class TestGovernedPersistValidation:
    def test_bad_kind_raises(self, tmp_path):
        with pytest.raises(ValueError):
            governed_persist("bogus", str(tmp_path / "x.md"), b"content")

    def test_empty_path_raises(self):
        with pytest.raises(ValueError):
            governed_persist("memory", "", b"content")

    def test_non_string_path_raises(self):
        with pytest.raises(ValueError):
            governed_persist("memory", None, b"content")  # type: ignore[arg-type]

    def test_bad_content_type_raises(self, tmp_path):
        with pytest.raises(TypeError):
            governed_persist("memory", str(tmp_path / "x.md"), 12345)  # type: ignore[arg-type]

    def test_str_content_is_utf8_encoded(self, tmp_path, monkeypatch):
        target = tmp_path / "MEMORY.md"
        seen = {}

        def fake(hook_name, **kwargs):
            seen["content_b64"] = kwargs["content_b64"]
            return {"action": "allow"}

        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", fake)
        governed_persist("memory", str(target), "héllo")
        import base64
        assert base64.b64decode(seen["content_b64"]) == "héllo".encode("utf-8")


# ---------------------------------------------------------------------------
# governed_persist -- directive handling
# ---------------------------------------------------------------------------


class TestGovernedPersistDirectives:
    def test_deny_blocks_persistence_no_canonical_write(self, tmp_path, monkeypatch):
        target = tmp_path / "MEMORY.md"
        monkeypatch.setattr(
            "hermes_cli.plugins.get_required_hook_directive",
            _directive("block", message="POLICY_DENIED: refused"),
        )

        result = governed_persist("memory", str(target), "some content")

        assert result == PersistResult(staged=False, digest=None, denied=True, message="POLICY_DENIED: refused")
        assert not target.exists()

    def test_deny_without_message_gets_a_default(self, tmp_path, monkeypatch):
        target = tmp_path / "MEMORY.md"
        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", _directive("block"))

        result = governed_persist("memory", str(target), b"x")

        assert result.denied is True
        assert result.message
        assert not target.exists()

    def test_allow_staged_leaves_canonical_absent(self, tmp_path, monkeypatch):
        target = tmp_path / "MEMORY.md"
        digest = "sha256:" + "a" * 64
        monkeypatch.setattr(
            "hermes_cli.plugins.get_required_hook_directive",
            _directive("allow", staged=True, digest=digest),
        )

        result = governed_persist("memory", str(target), b"quarantined content")

        assert result == PersistResult(staged=True, digest=digest, denied=False, message="")
        assert not target.exists()

    def test_bare_allow_is_passthrough_and_writes_canonical(self, tmp_path, monkeypatch):
        target = tmp_path / "MEMORY.md"
        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", _directive("allow"))

        result = governed_persist("memory", str(target), "hello")

        assert result == PersistResult(staged=False, digest=None, denied=False, message="")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_passthrough_writes_bytes_content_exactly(self, tmp_path, monkeypatch):
        target = tmp_path / "SKILL.md"
        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", _directive("allow"))

        governed_persist("skill", str(target), b"\x00binary-ish\xff")

        assert target.read_bytes() == b"\x00binary-ish\xff"

    def test_passthrough_creates_parent_directories(self, tmp_path, monkeypatch):
        target = tmp_path / "nested" / "dir" / "MEMORY.md"
        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", _directive("allow"))

        governed_persist("memory", str(target), "x")

        assert target.exists()


# ---------------------------------------------------------------------------
# governed_persist -- enforcer presence gates a bare "allow"
#
# A bare "allow" (no `staged` flag) is only a legitimate pre-cutover
# passthrough when NO required-hook enforcer is wired for pre_persist_write.
# When one IS wired, invoke_required_hook's own type guard silently strips a
# missing/non-bool `staged` key rather than blocking, so the resulting
# directive is shape-identical to the no-enforcer case -- governed_persist
# must tell the two apart itself (via _enforcer_registered) and fail closed
# to a local stage rather than guessing it into a canonical write.
# ---------------------------------------------------------------------------


class TestGovernedPersistEnforcerPresence:
    def test_enforcer_present_bare_allow_stages_locally_not_canonical(
        self, tmp_path, monkeypatch, caplog
    ):
        target = tmp_path / "MEMORY.md"
        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", _directive("allow"))
        monkeypatch.setattr(pb, "_enforcer_registered", lambda hook_name: True)

        with caplog.at_level("WARNING"):
            result = governed_persist("memory", str(target), b"malformed-enforcer-allow")

        assert result.staged is True
        assert result.denied is False
        assert not target.exists()
        assert "enforcer allow without staged" in caplog.text

    def test_enforcer_present_well_formed_staged_allow_is_unaffected(self, tmp_path, monkeypatch):
        # staged=True short-circuits before the enforcer-presence check runs
        # at all -- an enforcer that behaves correctly is unaffected by this
        # fix either way.
        target = tmp_path / "MEMORY.md"
        digest = "sha256:" + "b" * 64
        monkeypatch.setattr(
            "hermes_cli.plugins.get_required_hook_directive",
            _directive("allow", staged=True, digest=digest),
        )
        monkeypatch.setattr(pb, "_enforcer_registered", lambda hook_name: True)

        result = governed_persist("memory", str(target), b"content")

        assert result == PersistResult(staged=True, digest=digest, denied=False, message="")
        assert not target.exists()

    def test_no_enforcer_bare_allow_is_still_passthrough(self, tmp_path, monkeypatch):
        # Explicit no-enforcer case, decoupled from the real plugin manager's
        # test-time default -- pins the other half of the fix: nothing
        # regresses when there genuinely is no enforcer registered.
        target = tmp_path / "MEMORY.md"
        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", _directive("allow"))
        monkeypatch.setattr(pb, "_enforcer_registered", lambda hook_name: False)

        result = governed_persist("memory", str(target), "hello")

        assert result == PersistResult(staged=False, digest=None, denied=False, message="")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_enforcer_registered_reads_the_required_hooks_registry(self, monkeypatch):
        class _FakeManager:
            _required_hooks = {"pre_persist_write": [("guard", lambda **kw: None)]}

        monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: _FakeManager())

        assert pb._enforcer_registered("pre_persist_write") is True
        assert pb._enforcer_registered("pre_run_start") is False

    def test_enforcer_registered_fails_closed_on_introspection_error(self, monkeypatch):
        def boom():
            raise RuntimeError("plugin manager unavailable")

        monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", boom)

        assert pb._enforcer_registered("pre_persist_write") is True


# ---------------------------------------------------------------------------
# governed_persist -- hook unreachable / unrecognized -> decision-less local stage
# ---------------------------------------------------------------------------


class TestGovernedPersistHookUnreachable:
    def test_hook_raises_stages_locally_with_no_canonical_write(self, tmp_path, monkeypatch, caplog):
        target = tmp_path / "MEMORY.md"
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        def boom(hook_name, **kwargs):
            raise RuntimeError("plugin subsystem exploded")

        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", boom)

        with caplog.at_level("WARNING"):
            result = governed_persist("memory", str(target), b"unsaved content", meta={"session_id": "s1"})

        assert result.staged is True
        assert result.denied is False
        assert result.digest == "sha256:" + hashlib.sha256(b"unsaved content").hexdigest()
        assert not target.exists()
        assert "pre_persist_write hook unreachable" in caplog.text

        staged_dir = tmp_path / "persist-quarantine-local" / hashlib.sha256(b"unsaved content").hexdigest()
        assert staged_dir.joinpath("content.bin").read_bytes() == b"unsaved content"
        meta = json.loads(staged_dir.joinpath("meta.json").read_text(encoding="utf-8"))
        assert meta["kind"] == "memory"
        assert meta["status"] == "staged-local"
        assert meta["meta"]["session_id"] == "s1"

    def test_unrecognized_directive_stages_locally(self, tmp_path, monkeypatch):
        target = tmp_path / "MEMORY.md"
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(
            "hermes_cli.plugins.get_required_hook_directive",
            _directive("approve", message="needs a human"),
        )

        result = governed_persist("memory", str(target), b"content")

        assert result.staged is True
        assert result.denied is False
        assert not target.exists()

    def test_non_dict_directive_stages_locally(self, tmp_path, monkeypatch):
        target = tmp_path / "MEMORY.md"
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", lambda hook_name, **kw: None)

        result = governed_persist("memory", str(target), b"content")

        assert result.staged is True
        assert not target.exists()

    def test_local_stage_is_idempotent_by_digest(self, tmp_path, monkeypatch):
        target = tmp_path / "MEMORY.md"
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(
            "hermes_cli.plugins.get_required_hook_directive",
            lambda hook_name, **kw: (_ for _ in ()).throw(RuntimeError("down")),
        )

        first = governed_persist("memory", str(target), b"same content")
        second = governed_persist("memory", str(target), b"same content")

        assert first.digest == second.digest


# ---------------------------------------------------------------------------
# Refactored site: tools/memory_tool.py (kind="memory")
# ---------------------------------------------------------------------------


class TestMemoryToolSiteWiring:
    def test_add_denied_raises_persist_denied_no_canonical_write(self, tmp_path, monkeypatch):
        from tools.memory_tool import MemoryStore

        monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "hermes_cli.plugins.get_required_hook_directive",
            _directive("block", message="POLICY_DENIED: nope"),
        )

        store = MemoryStore()
        store.load_from_disk()
        with pytest.raises(PersistDenied):
            store.add("memory", "should never land on disk")

        assert not (tmp_path / "MEMORY.md").exists()

    def test_add_staged_leaves_canonical_absent(self, tmp_path, monkeypatch):
        from tools.memory_tool import MemoryStore

        monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "hermes_cli.plugins.get_required_hook_directive",
            _directive("allow", staged=True, digest="sha256:" + "c" * 64),
        )

        store = MemoryStore()
        store.load_from_disk()
        result = store.add("memory", "quarantined text")

        assert result["success"] is True
        assert not (tmp_path / "MEMORY.md").exists()

    def test_add_passthrough_still_writes_canonical(self, tmp_path, monkeypatch):
        from tools.memory_tool import MemoryStore

        monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", _directive("allow"))

        store = MemoryStore()
        store.load_from_disk()
        store.add("memory", "regular entry")

        assert "regular entry" in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")

    def test_add_routes_kind_memory(self, tmp_path, monkeypatch):
        from tools.memory_tool import MemoryStore

        monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
        seen = {}

        def fake_governed_persist(kind, path, content, meta=None):
            seen["kind"] = kind
            seen["path"] = path
            return PersistResult(staged=False, digest=None, denied=False)

        monkeypatch.setattr("agent.persist_boundary.governed_persist", fake_governed_persist)

        store = MemoryStore()
        store.load_from_disk()
        store.add("memory", "hello")

        assert seen["kind"] == "memory"
        assert seen["path"].endswith("MEMORY.md")


# ---------------------------------------------------------------------------
# Refactored site: agent/learning_mutations.py (shares MemoryStore._write_file)
# ---------------------------------------------------------------------------


class TestLearningMutationsSiteWiring:
    def test_edit_memory_denied_raises_persist_denied(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
        (tmp_path / "memories" / "MEMORY.md").write_text("alpha\n§\nbeta", encoding="utf-8")
        monkeypatch.setattr(
            "hermes_cli.plugins.get_required_hook_directive",
            _directive("block", message="POLICY_DENIED: nope"),
        )

        from agent import learning_mutations as lm

        with pytest.raises(PersistDenied):
            lm.edit_node("memory:memory:0", "replacement text")

        assert "alpha" in (tmp_path / "memories" / "MEMORY.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Refactored site: tools/skill_manager_tool.py (kind="skill")
# ---------------------------------------------------------------------------

_VALID_SKILL = """\
---
name: my-skill
description: A test skill for the persist boundary.
---

# My Skill

Step 1: Do the thing.
"""


class TestSkillManagerSiteWiring:
    def _patched(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tools.skill_manager_tool.SKILLS_DIR", tmp_path)
        monkeypatch.setattr("agent.skill_utils.get_all_skills_dirs", lambda: [tmp_path])

    def test_create_denied_raises_persist_denied_no_skill_md(self, tmp_path, monkeypatch):
        self._patched(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "hermes_cli.plugins.get_required_hook_directive",
            _directive("block", message="POLICY_DENIED: nope"),
        )

        from tools.skill_manager_tool import _create_skill

        with pytest.raises(PersistDenied):
            _create_skill("my-skill", _VALID_SKILL)

        assert not (tmp_path / "my-skill" / "SKILL.md").exists()

    def test_create_staged_leaves_skill_md_absent(self, tmp_path, monkeypatch):
        self._patched(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "hermes_cli.plugins.get_required_hook_directive",
            _directive("allow", staged=True, digest="sha256:" + "d" * 64),
        )

        from tools.skill_manager_tool import _create_skill

        # governed_persist doesn't raise on staged, so _create_skill runs its
        # post-write steps (security scan) against a file that was never
        # written -- documented residual of wiring the funnel ahead of the
        # governed install cutover (see agent/persist_boundary.py docstring).
        # The one invariant under test is the one that matters here: no
        # unattested content ever reaches the canonical path.
        try:
            _create_skill("my-skill", _VALID_SKILL)
        except Exception:
            pass

        assert not (tmp_path / "my-skill" / "SKILL.md").exists()

    def test_create_passthrough_still_writes_canonical(self, tmp_path, monkeypatch):
        self._patched(tmp_path, monkeypatch)
        monkeypatch.setattr("hermes_cli.plugins.get_required_hook_directive", _directive("allow"))

        from tools.skill_manager_tool import _create_skill

        result = _create_skill("my-skill", _VALID_SKILL)

        assert result["success"] is True
        assert (tmp_path / "my-skill" / "SKILL.md").read_text(encoding="utf-8") == _VALID_SKILL

    def test_create_routes_kind_skill(self, tmp_path, monkeypatch):
        self._patched(tmp_path, monkeypatch)
        seen = {}

        def fake_governed_persist(kind, path, content, meta=None):
            seen["kind"] = kind
            seen["path"] = path
            return PersistResult(staged=False, digest=None, denied=False)

        monkeypatch.setattr("agent.persist_boundary.governed_persist", fake_governed_persist)

        from tools.skill_manager_tool import _create_skill

        _create_skill("my-skill", _VALID_SKILL)

        assert seen["kind"] == "skill"
        assert seen["path"].endswith("SKILL.md")


# ---------------------------------------------------------------------------
# Regression tests for _open_write_mode_findings AST helper
# ---------------------------------------------------------------------------


class TestOpenWriteModeFindings:
    """Unit tests for the _open_write_mode_findings helper to ensure it reads
    the correct argument index for function vs. method calls."""

    def test_open_function_with_write_mode_positional_arg_is_caught(self):
        """open("MEMORY.md", "w") should be flagged (mode is args[1], not args[0])."""
        source = '''
def _write_file(path: str, content: str):
    with open("MEMORY.md", "w") as f:
        f.write(content)
'''
        findings = _open_write_mode_findings(source, "test")
        assert len(findings) == 1
        assert "w" in findings[0]

    def test_open_function_with_read_mode_positional_arg_not_flagged(self):
        """open("data.xlsx", "r") should NOT be flagged (mode "r" has no write chars)."""
        source = '''
def read_spreadsheet(path: str):
    with open("data.xlsx", "r") as f:
        return f.read()
'''
        findings = _open_write_mode_findings(source, "test")
        assert len(findings) == 0

    def test_path_method_with_write_mode_positional_arg_is_caught(self):
        """Path(...).open("w") should be flagged (mode is args[0] for method calls)."""
        source = '''
from pathlib import Path
def write_skill(path: Path, content: str):
    path.open("w").write(content)
'''
        findings = _open_write_mode_findings(source, "test")
        assert len(findings) == 1
        assert "w" in findings[0]

    def test_open_function_with_keyword_mode_is_caught(self):
        """open(path, mode="w") with keyword argument should be flagged."""
        source = '''
def write_file(path: str, content: str):
    with open(path, mode="w") as f:
        f.write(content)
'''
        findings = _open_write_mode_findings(source, "test")
        assert len(findings) == 1
        assert "w" in findings[0]

    def test_open_with_append_mode_is_caught(self):
        """open(path, "a") with append mode should be flagged."""
        source = '''
def append_log(path: str, entry: str):
    with open(path, "a") as f:
        f.write(entry)
'''
        findings = _open_write_mode_findings(source, "test")
        assert len(findings) == 1
        assert "a" in findings[0]


# ---------------------------------------------------------------------------
# Grep-pin: only the funnel module may perform a canonical write
#
# Scoped to the specific write-site FUNCTIONS (via AST), not a whole-file
# text scan -- tools/memory_tool.py legitimately writes a `.bak.<ts>` DRIFT
# snapshot elsewhere in the same file (external-drift recovery, an
# operator-facing diagnostic copy, not a canonical read path Hermes ever
# loads from) and that write is intentionally NOT part of this funnel
# ("DO NOT refactor unrelated writes"). Pinning the exact write-site
# function bodies is the precise version of "no direct canonical write
# outside the funnel module."
# ---------------------------------------------------------------------------

_FORBIDDEN_WRITE_SNIPPETS = (
    "os.fdopen(",
    "tempfile.mkstemp(",
    "atomic_replace(",
    ".write_text(",
    ".write_bytes(",
    "os.replace(",
    "os.rename(",
    "shutil.",
)

# `open(path, "w")` / `Path(...).open("wb")` / `open(path, mode="a")` don't
# contain any fixed token above -- the mode string can land in any argument
# position or as a `mode=` keyword. Caught separately via AST below instead
# of trying to enumerate every quoting/spacing variant as a substring.
_WRITE_MODE_CHARS = frozenset("wax+")


def _open_write_mode_findings(source: str, label: str) -> list[str]:
    """AST-scan *source* (a standalone function segment or a whole module)
    for `open(...)` / `<expr>.open(...)` calls whose mode argument permits
    writing (contains any of w/a/x/+). A no-mode `open(path)` defaults to
    read-only text mode and is not flagged.
    """
    tree = ast.parse(source)
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_method_call = isinstance(func, ast.Attribute) and func.attr == "open"
        is_function_call = isinstance(func, ast.Name) and func.id == "open"
        if not (is_method_call or is_function_call):
            continue

        mode_value = None
        # Check keyword argument first
        for kw in node.keywords:
            if (
                kw.arg == "mode"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                mode_value = kw.value.value
                break

        # If no keyword mode, check positional arguments
        if mode_value is None:
            # For method calls like Path(...).open("w"), mode is args[0]
            # For function calls like open(path, "w"), mode is args[1]
            arg_index = 0 if is_method_call else 1
            if arg_index < len(node.args):
                arg = node.args[arg_index]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    mode_value = arg.value

        if mode_value is None:
            continue
        if _WRITE_MODE_CHARS & set(mode_value):
            findings.append(f"{label}: open(...) call with write-capable mode {mode_value!r}")
    return findings

_WRITE_SITE_FUNCTIONS = (
    ("tools/memory_tool.py", "_write_file"),
    ("tools/skill_manager_tool.py", "_atomic_write_text"),
)

_WRITE_SITE_WHOLE_FILES = (
    # No write primitive of its own -- delegates entirely to
    # MemoryStore._write_file (pinned above). Whole-file scan is safe here:
    # unlike memory_tool.py, this module has no unrelated diagnostic write.
    "agent/learning_mutations.py",
)

_FUNNEL_MODULE = "agent/persist_boundary.py"


def _function_source(repo_root: Path, rel_path: str, func_name: str) -> str:
    file_path = repo_root / rel_path
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            segment = ast.get_source_segment(source, node)
            if segment is not None:
                return segment
    raise AssertionError(f"{func_name!r} not found in {rel_path}")


class TestNoDirectCanonicalWriteOutsideTheFunnel:
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def test_write_site_functions_contain_no_direct_write_primitive(self):
        repo_root = self._repo_root()
        for rel, func_name in _WRITE_SITE_FUNCTIONS:
            segment = _function_source(repo_root, rel, func_name)
            for snippet in _FORBIDDEN_WRITE_SNIPPETS:
                assert snippet not in segment, (
                    f"{rel}::{func_name} still contains a direct canonical "
                    f"write primitive ({snippet!r}) -- route it through "
                    f"agent.persist_boundary.governed_persist instead"
                )
            open_findings = _open_write_mode_findings(segment, f"{rel}::{func_name}")
            assert not open_findings, (
                f"{rel}::{func_name} still contains a direct write-mode "
                f"open() call ({open_findings}) -- route it through "
                f"agent.persist_boundary.governed_persist instead"
            )

    def test_delegating_modules_contain_no_direct_write_primitive(self):
        repo_root = self._repo_root()
        for rel in _WRITE_SITE_WHOLE_FILES:
            source = (repo_root / rel).read_text(encoding="utf-8")
            for snippet in _FORBIDDEN_WRITE_SNIPPETS:
                assert snippet not in source, (
                    f"{rel} contains a direct canonical write primitive "
                    f"({snippet!r}) -- route it through agent.persist_boundary."
                    f"governed_persist instead"
                )
            open_findings = _open_write_mode_findings(source, rel)
            assert not open_findings, (
                f"{rel} contains a direct write-mode open() call "
                f"({open_findings}) -- route it through agent.persist_boundary."
                f"governed_persist instead"
            )

    def test_funnel_module_is_the_one_allowed_writer(self):
        # Sanity check the allowlist isn't hiding a funnel that silently lost
        # its own ability to ever write a canonical path.
        repo_root = self._repo_root()
        source = (repo_root / _FUNNEL_MODULE).read_text(encoding="utf-8")
        assert "os.fdopen(" in source
        assert "atomic_replace(" in source
