"""``hermes persist-smoke`` -- operator-facing smoke test for the governed
persistence funnel (``agent.persist_boundary.governed_persist``).

Confirms, in one shot, that:

1. plugin discovery actually finds a policy enforcer that registered the
   required ``pre_persist_write`` hook (distinguishing "no agent-lineage
   plugin discovered at all" from "discovered, but this hook is missing --
   a stale plugin version?"), and
2. a real probe write routed through ``governed_persist`` comes back
   genuinely staged (governed) rather than silently falling through to a
   canonical write, OR falling through to ``governed_persist``'s own
   decision-less local fallback (``persist_boundary._stage_local``) -- which
   also reports ``staged=True`` but reflects an unreachable/malformed
   enforcer, not a real policy decision, and must not be reported as green
   either (a mid-crash worker is not a healthy one).

This is deliberately a read-mostly diagnostic. On a genuine "staged" outcome
nothing new lands on the canonical path (agent-lineage's own quarantine is
outside this fork's filesystem view); on "denied" nothing is written either.
The "local_fallback" outcome writes durably under this fork's own
``$HERMES_HOME/persist-quarantine-local/`` -- that's ``governed_persist``'s
concern, not this command's, and is left alone. Only the "passthrough"
outcome -- no enforcer wired at all, so ``governed_persist`` performs the
pre-cutover canonical write itself -- actually creates a file under this
command's own probe path, and this command deletes it (and the now-empty
``persist-smoke/`` directory it lived in) before returning, so an ungoverned
smoke run leaves the working tree exactly as it found it.

Plugin discovery and registry introspection run inside a broad
``try/except``: a fail-loud duplicate-required-plugin-name abort
(``RequiredPluginError``) or any other discovery-time exception is reported
as a clean ``discovery-failed`` JSON diagnostic (exit 1) instead of an
uncaught traceback -- the same condition would abort real Hermes startup, so
this smoke's job is to say so legibly, not to crash trying to say it.

Not to be confused with agent-lineage's OWN ``hermes-persist smoke``
(``framework/tools/cli.py`` in the agent-lineage repo): that is a black-box
harness which shells out to a real ``hermes`` binary (``--hermes-bin``) and
judges a live chat turn from NEW hash-chained policy-ledger events written
during it. This command is the fork-side white box: it runs in-process,
inspects the plugin manager's own registries directly, and drives
``governed_persist`` with a synthetic probe rather than a real model turn.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

__all__ = ["run_persist_smoke"]

_HOOK_NAME = "pre_persist_write"
_PLUGIN_NAME = "agent-lineage"
_PROBE_DIR = "persist-smoke"
_PROBE_PATH = f"{_PROBE_DIR}/probe.md"


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
    detail = (
        payload.get("message")
        or payload.get("reason")
        or payload.get("error")
        or ""
    )
    print(f"FAIL: persist-smoke ({mode}): {detail}")


def run_persist_smoke(args) -> None:
    """Implementation of ``hermes persist-smoke``.

    Exits 0 ONLY when a probe write through ``governed_persist`` comes back
    genuinely staged -- ``staged=True``, ``denied=False``, and no fallback
    message attached. Every other outcome exits 1: the required hook isn't
    registered (``no_hook``), plugin discovery/registry introspection itself
    raised (``discovery-failed``), the write was refused (``denied``),
    ``governed_persist`` fell through to its own decision-less local staging
    because the enforcer was unreachable or malformed (``local_fallback``),
    or no enforcer is wired at all so the pre-cutover canonical write ran
    (``passthrough``).
    """
    as_json = bool(getattr(args, "json", False))

    from hermes_cli.plugins import discover_plugins, get_plugin_manager

    try:
        discover_plugins(force=True)
        manager = get_plugin_manager()
        hooks = sorted(getattr(manager, "_required_hooks", None) or {})
    except Exception as exc:
        # Whatever would abort real Hermes startup (a fail-loud duplicate
        # required-plugin-name RequiredPluginError, or any other discovery-
        # time failure) must not crash this diagnostic too -- report it as
        # a legible, still-valid-JSON verdict instead of a bare traceback.
        _emit(
            {
                "ok": False,
                "mode": "discovery-failed",
                "error": str(exc),
                "hint": "hermes startup itself would abort — fix plugin discovery first",
            },
            as_json=as_json,
        )
        sys.exit(1)

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

    # A unique session id per invocation, never a shared fixed default.
    # governed_persist threads meta["session_id"] straight through to the
    # pre_persist_write hook, and agent-lineage's durable budgets meter
    # external_side_effects PER SESSION with a cap (currently 100). A
    # periodic/cron-driven smoke that reused one fixed session across every
    # run would eventually exhaust that budget purely from its OWN
    # accumulated history and start self-denying -- a false "denied" that
    # says nothing about whether governance is actually working right now.
    # Minting a fresh session per probe keeps the smoke's own call history
    # from ever being the thing that fails it.
    session_id = f"persist-smoke:{uuid4().hex[:12]}"
    result = governed_persist(
        "memory",
        _PROBE_PATH,
        _probe_content(),
        {"origin": "persist-smoke", "session_id": session_id},
    )

    if result.denied:
        _emit(
            {"ok": False, "mode": "denied", "message": result.message},
            as_json=as_json,
        )
        sys.exit(1)

    if result.staged and not result.message:
        # Genuine governance: a real enforcer made a real "stage this"
        # decision, with nothing left unsaid.
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

    if result.staged:
        # governed_persist's OWN decision-less local fallback
        # (persist_boundary._stage_local) also reports staged=True -- for
        # an unreachable hook, a non-dict directive, or a registered
        # enforcer's malformed bare "allow" -- but it is not a policy
        # decision at all, just durable loss-prevention while governance
        # itself is broken. The non-empty message is the tell; reporting
        # this as green would invert the whole point of the smoke.
        _emit(
            {
                "ok": False,
                "mode": "local_fallback",
                "digest": result.digest,
                "message": result.message,
            },
            as_json=as_json,
        )
        sys.exit(1)

    # Neither denied nor staged: governed_persist performed the real
    # pre-cutover canonical write (no enforcer registered for this hook).
    # Clean up the probe file -- and the now-empty persist-smoke/ directory
    # it lived in -- so an ungoverned run leaves the working tree exactly
    # as it found it, then fail the smoke -- it exists precisely to prove
    # governance is wired, and here it isn't.
    probe = Path(_PROBE_PATH)
    try:
        probe.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        probe.parent.rmdir()
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
