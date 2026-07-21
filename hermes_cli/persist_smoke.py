"""``hermes persist-smoke`` -- operator-facing smoke test for the governed
persistence funnel (``agent.persist_boundary.governed_persist``).

Confirms, in one shot, that:

1. plugin discovery actually finds a policy enforcer that registered the
   required ``pre_persist_write`` hook (distinguishing "no agent-lineage
   plugin discovered at all" from "discovered, but this hook is missing --
   a stale plugin version?"), and
2. a real probe write routed through ``governed_persist`` comes back
   genuinely staged (governed) rather than silently falling through to a
   canonical write.

This is deliberately a read-mostly diagnostic. On the "staged" outcome
nothing new lands on the canonical path (agent-lineage's own quarantine is
outside this fork's filesystem view); on "denied" nothing is written either.
Only the "passthrough" outcome -- no enforcer wired, so ``governed_persist``
performs the pre-cutover canonical write itself -- actually creates a file,
and this command deletes it before returning so an ungoverned smoke run
leaves the working tree exactly as it found it.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["run_persist_smoke"]

_HOOK_NAME = "pre_persist_write"
_PLUGIN_NAME = "agent-lineage"
_PROBE_PATH = "persist-smoke/probe.md"


def _probe_content() -> bytes:
    stamp = datetime.now(timezone.utc).isoformat()
    return f"hermes persist-smoke probe @ {stamp}\n".encode("utf-8")


def _plugin_discovered(manager: Any, name: str) -> bool:
    """True when *name* was discovered by plugin scanning at all -- loaded,
    disabled, or otherwise -- as long as it showed up in the registry."""
    plugins = getattr(manager, "_plugins", None)
    if not isinstance(plugins, dict):
        return False
    if name in plugins:
        return True
    return any(
        getattr(getattr(loaded, "manifest", None), "name", None) == name
        for loaded in plugins.values()
    )


def _required_hook_registered(manager: Any, hook_name: str) -> bool:
    required_hooks = getattr(manager, "_required_hooks", None)
    if not isinstance(required_hooks, dict):
        return False
    return bool(required_hooks.get(hook_name))


def _emit(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload))
        return
    mode = payload.get("mode", "?")
    if payload.get("ok"):
        print(
            f"OK: persist-smoke probe was genuinely staged "
            f"(mode={mode}, digest={payload.get('digest')})"
        )
        return
    detail = payload.get("message") or payload.get("reason") or ""
    print(f"FAIL: persist-smoke ({mode}): {detail}")


def run_persist_smoke(args) -> None:
    """Implementation of ``hermes persist-smoke``.

    Exits 0 only when a probe write through ``governed_persist`` comes back
    genuinely staged. Every other outcome (hook not registered, denied, or
    an ungoverned canonical passthrough) exits 1.
    """
    as_json = bool(getattr(args, "json", False))

    from hermes_cli.plugins import discover_plugins, get_plugin_manager

    discover_plugins(force=True)
    manager = get_plugin_manager()
    hooks = sorted(getattr(manager, "_required_hooks", None) or {})

    if not _required_hook_registered(manager, _HOOK_NAME):
        if _plugin_discovered(manager, _PLUGIN_NAME):
            message = (
                f"'{_PLUGIN_NAME}' plugin was discovered but registered no "
                f"required '{_HOOK_NAME}' hook -- stale plugin version?"
            )
        else:
            message = (
                f"no '{_PLUGIN_NAME}' plugin was discovered -- install it "
                f"under ~/.hermes/plugins/{_PLUGIN_NAME}/ and add it to "
                f"plugins.enabled / plugins.required in config.yaml"
            )
        _emit(
            {"ok": False, "hooks": hooks, "mode": "no_hook", "message": message},
            as_json=as_json,
        )
        sys.exit(1)

    from agent.persist_boundary import governed_persist

    result = governed_persist(
        "memory",
        _PROBE_PATH,
        _probe_content(),
        {"origin": "persist-smoke"},
    )

    if result.denied:
        _emit(
            {"ok": False, "mode": "denied", "message": result.message},
            as_json=as_json,
        )
        sys.exit(1)

    if result.staged:
        _emit(
            {
                "ok": True,
                "hooks": hooks,
                "staged": True,
                "digest": result.digest,
                "mode": "staged",
            },
            as_json=as_json,
        )
        return

    # Neither denied nor staged: governed_persist performed the real
    # pre-cutover canonical write (no enforcer registered for this hook).
    # Clean up the probe file it created so an ungoverned run leaves no
    # litter, then fail the smoke -- it exists precisely to prove
    # governance is wired, and here it isn't.
    try:
        Path(_PROBE_PATH).unlink(missing_ok=True)
    except OSError:
        pass
    _emit(
        {
            "ok": False,
            "mode": "passthrough",
            "reason": "no enforcer registered — governed config required for the smoke",
        },
        as_json=as_json,
    )
    sys.exit(1)
