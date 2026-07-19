"""Mandatory pre-tool plugin enforcement is fail-closed and operator-configured."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

import hermes_cli.plugins as plugins_module
from hermes_cli.plugins import (
    PluginContext,
    PluginManager,
    PluginManifest,
    RequiredPluginError,
    get_required_hook_directive,
    get_pre_tool_call_directive,
)
from hermes_cli._parser import build_top_level_parser


def _write_config(home: Path, *, enabled=None, required=None, disabled=None) -> None:
    plugins: dict = {}
    if enabled is not None:
        plugins["enabled"] = enabled
    if required is not None:
        plugins["required"] = required
    if disabled is not None:
        plugins["disabled"] = disabled
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": plugins}), encoding="utf-8"
    )


def _plugin(home: Path, name: str, register_body: str) -> Path:
    directory = home / "plugins" / name
    directory.mkdir(parents=True)
    (directory / "plugin.yaml").write_text(
        yaml.safe_dump({"name": name, "version": "0.1.0"}), encoding="utf-8"
    )
    (directory / "__init__.py").write_text(
        f"def register(ctx):\n    {register_body}\n", encoding="utf-8"
    )
    return directory


def test_required_plugin_must_be_discovered(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home, enabled=["missing"], required=["missing"])
    monkeypatch.setenv("HERMES_HOME", str(home))

    with pytest.raises(RequiredPluginError, match="not discovered"):
        PluginManager().discover_and_load()


def test_required_plugin_must_be_enabled_and_register_required_hook(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _plugin(home, "guard", "ctx.register_hook('pre_tool_call', lambda **kw: None)")
    _write_config(home, enabled=[], required=["guard"])
    monkeypatch.setenv("HERMES_HOME", str(home))
    with pytest.raises(RequiredPluginError, match="unavailable"):
        PluginManager().discover_and_load()

    _write_config(home, enabled=["guard"], required=["guard"])
    with pytest.raises(RequiredPluginError, match="registered no required"):
        PluginManager().discover_and_load()


def test_required_plugin_load_failure_aborts_discovery(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _plugin(home, "guard", "raise RuntimeError('worker unavailable')")
    _write_config(home, enabled=["guard"], required=["guard"])
    monkeypatch.setenv("HERMES_HOME", str(home))

    with pytest.raises(RequiredPluginError, match="unavailable"):
        PluginManager().discover_and_load()


def test_ambiguous_bare_required_name_fails_closed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for category in ("first", "second"):
        directory = home / "plugins" / category / "guard"
        directory.mkdir(parents=True)
        (directory / "plugin.yaml").write_text(
            yaml.safe_dump({"name": "guard", "version": "0.1.0"}),
            encoding="utf-8",
        )
        (directory / "__init__.py").write_text(
            "def register(ctx):\n"
            "    ctx.register_required_hook('pre_tool_call', "
            "lambda **kw: {'action': 'allow'})\n",
            encoding="utf-8",
        )
    _write_config(
        home,
        enabled=["first/guard", "second/guard"],
        required=["guard"],
    )
    monkeypatch.setenv("HERMES_HOME", str(home))

    with pytest.raises(RequiredPluginError, match="ambiguous"):
        PluginManager().discover_and_load()


def test_required_allow_is_explicit_and_plugin_is_attributed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _plugin(
        home,
        "guard",
        "ctx.register_required_hook('pre_tool_call', lambda **kw: {'action': 'allow'})",
    )
    _write_config(home, enabled=["guard"], required=["guard"])
    monkeypatch.setenv("HERMES_HOME", str(home))

    manager = PluginManager()
    manager.discover_and_load()
    assert manager._plugins["guard"].required_hooks_registered == ["pre_tool_call"]
    assert manager.invoke_required_hook("pre_tool_call", tool_name="read_file") == [
        {"action": "allow"}
    ]


def test_required_callback_exception_and_malformed_result_block(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home, required=["guard"])
    monkeypatch.setenv("HERMES_HOME", str(home))

    for callback, expected in (
        (lambda **kw: (_ for _ in ()).throw(RuntimeError("down")), "failed"),
        (lambda **kw: None, "no valid decision"),
    ):
        manager = PluginManager()
        context = PluginContext(
            PluginManifest(name="guard", key="guard", source="user"), manager
        )
        context.register_required_hook("pre_tool_call", callback)
        manager._plugins["guard"] = plugins_module.LoadedPlugin(
            manifest=context.manifest, enabled=True
        )
        monkeypatch.setattr(plugins_module, "_plugin_manager", manager)
        directive, message = get_pre_tool_call_directive("terminal", {"command": "true"})
        assert directive == "block"
        assert expected in (message or "")


def test_partially_loaded_required_plugin_cannot_leave_an_allow_callback(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    _write_config(home, required=["guard"])
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = PluginManager()
    context = PluginContext(
        PluginManifest(name="guard", key="guard", source="user"), manager
    )
    context.register_required_hook(
        "pre_tool_call", lambda **kw: {"action": "allow"}
    )
    manager._plugins["guard"] = plugins_module.LoadedPlugin(
        manifest=context.manifest,
        enabled=False,
        error="register failed after installing callback",
    )

    assert manager.invoke_required_hook("pre_tool_call", tool_name="read_file") == [
        {
            "action": "block",
            "message": "BLOCKED: required policy enforcer is unavailable",
        }
    ]


def test_required_block_outranks_approval_and_optional_hooks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home, required=["first", "second"])
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = PluginManager()
    first = PluginContext(
        PluginManifest(name="first", key="first", source="user"), manager
    )
    second = PluginContext(
        PluginManifest(name="second", key="second", source="user"), manager
    )
    first.register_required_hook(
        "pre_tool_call",
        lambda **kw: {"action": "approve", "message": "confirm"},
    )
    second.register_required_hook(
        "pre_tool_call",
        lambda **kw: {"action": "block", "message": "policy says no"},
    )
    manager._plugins["first"] = plugins_module.LoadedPlugin(first.manifest, enabled=True)
    manager._plugins["second"] = plugins_module.LoadedPlugin(second.manifest, enabled=True)
    manager._hooks.setdefault("pre_tool_call", []).append(
        lambda **kw: {"action": "approve", "message": "optional approval"}
    )
    monkeypatch.setattr(plugins_module, "_plugin_manager", manager)

    assert get_pre_tool_call_directive("terminal", {"command": "true"}) == (
        "block",
        "policy says no",
    )


def test_safe_mode_cannot_silently_disable_required_enforcer(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home, enabled=["guard"], required=["guard"])
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_SAFE_MODE", "1")

    with pytest.raises(RequiredPluginError, match="SAFE_MODE"):
        PluginManager().discover_and_load()


def test_ignore_user_config_cannot_erase_raw_required_floor(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home, enabled=["guard"], required=["guard"])
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_IGNORE_USER_CONFIG", "1")

    with pytest.raises(RequiredPluginError, match="not discovered|unavailable"):
        PluginManager().discover_and_load()


def test_malformed_required_config_blocks_pre_tool_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"required": "guard"}}), encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = PluginManager()
    monkeypatch.setattr(plugins_module, "_plugin_manager", manager)

    directive, message = get_pre_tool_call_directive("read_file", {"path": "x"})
    assert directive == "block"
    assert "configuration is invalid" in (message or "")


def test_invocation_required_plugin_is_additive(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home, enabled=["configured", "invoked"], required=["configured"])
    _plugin(
        home,
        "configured",
        "ctx.register_required_hook('pre_tool_call', lambda **kw: {'action': 'allow'})",
    )
    _plugin(
        home,
        "invoked",
        "ctx.register_required_hook('pre_tool_call', lambda **kw: {'action': 'allow'})",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_REQUIRED_PLUGINS", "invoked")

    manager = PluginManager()
    manager.discover_and_load()
    assert manager.invoke_required_hook("pre_tool_call", tool_name="read_file") == [
        {"action": "allow"}
    ]


def test_late_invocation_requirement_revalidates_prior_discovery(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home, enabled=[], required=[])
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = PluginManager()
    manager.discover_and_load()

    monkeypatch.setenv("HERMES_REQUIRED_PLUGINS", "missing")
    with pytest.raises(RequiredPluginError, match="not discovered"):
        manager.discover_and_load()


def test_require_plugin_cli_flag_is_a_generic_launch_contract():
    parser, _subparsers, _chat_parser = build_top_level_parser()
    args = parser.parse_args(["--require-plugin", "agent-lineage", "chat", "-q", "x"])
    assert args.require_plugin == ["agent-lineage"]


def test_require_plugin_value_is_not_misread_as_plugin_subcommand(monkeypatch):
    from hermes_cli.main import _first_positional_argv

    monkeypatch.setattr(
        sys,
        "argv",
        ["hermes", "--require-plugin", "agent-lineage", "chat", "-q", "x"],
    )
    assert _first_positional_argv() == "chat"


def test_required_hook_receives_registry_toolset_provenance(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home, required=["guard"])
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = PluginManager()
    context = PluginContext(
        PluginManifest(name="guard", key="guard", source="user"), manager
    )
    observed = {}

    def enforce(**kwargs):
        observed.update(kwargs)
        return {"action": "allow"}

    context.register_required_hook("pre_tool_call", enforce)
    manager._plugins["guard"] = plugins_module.LoadedPlugin(
        context.manifest, enabled=True
    )
    monkeypatch.setattr(plugins_module, "_plugin_manager", manager)

    from tools.registry import registry

    monkeypatch.setattr(registry, "get_toolset_for_tool", lambda _name: "mcp-example")
    assert get_pre_tool_call_directive("mcp__Example__Lookup", {}) == (None, None)
    assert observed["toolset"] == "mcp-example"


def test_required_model_and_run_boundaries_are_supported(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home, required=["guard"])
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = PluginManager()
    context = PluginContext(
        PluginManifest(name="guard", key="guard", source="user"), manager
    )
    for hook_name in ("pre_api_request", "post_api_request", "pre_run_start"):
        context.register_required_hook(
            hook_name,
            lambda _hook=hook_name, **kw: {
                "action": "allow",
                **(
                    {"run_id": "run.hermes.test", "session_id": "session.test"}
                    if _hook == "pre_run_start"
                    else {}
                ),
            },
        )
    # Required plugins remain load-bearing tool enforcers too.
    context.register_required_hook(
        "pre_tool_call", lambda **kw: {"action": "allow"}
    )
    manager._plugins["guard"] = plugins_module.LoadedPlugin(
        context.manifest, enabled=True
    )
    monkeypatch.setattr(plugins_module, "_plugin_manager", manager)

    assert get_required_hook_directive(
        "pre_api_request", api_request_id="request.1"
    ) == {"action": "allow"}
    assert get_required_hook_directive(
        "pre_run_start", run_kind="kanban", external_id="task.1"
    ) == {
        "action": "allow",
        "run_id": "run.hermes.test",
        "session_id": "session.test",
    }


def test_conflicting_required_run_bindings_fail_closed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_config(home, required=["first", "second"])
    monkeypatch.setenv("HERMES_HOME", str(home))
    manager = PluginManager()
    for name, run_id in (("first", "run.first"), ("second", "run.second")):
        context = PluginContext(
            PluginManifest(name=name, key=name, source="user"), manager
        )
        context.register_required_hook(
            "pre_run_start",
            lambda _run_id=run_id, **kw: {"action": "allow", "run_id": _run_id},
        )
        manager._plugins[name] = plugins_module.LoadedPlugin(
            context.manifest, enabled=True
        )
    monkeypatch.setattr(plugins_module, "_plugin_manager", manager)

    directive = get_required_hook_directive(
        "pre_run_start", run_kind="kanban", external_id="task.1"
    )
    assert directive["action"] == "block"
    assert "conflicting run context" in directive["message"]
