"""Governed persistence funnel -- the single chokepoint for runtime writes to
Hermes' canonical memory/skill/SOUL.md paths.

Companion piece to agent-lineage's ``framework/adapters/hermes_persistence``
(governed-runtime repo, external to this fork): every runtime mutation of a
memory, skill, or SOUL.md file is routed through :func:`governed_persist`
instead of writing its canonical path directly. This is the ONLY module in
this fork that performs the actual disk write for those paths -- refactored
call sites (``tools/memory_tool.py``, ``tools/skill_manager_tool.py``) hand
their content to ``governed_persist`` and never touch the canonical path
themselves; a source-scan test pins this (see
``tests/agent/test_persist_boundary.py``).

``governed_persist`` invokes the ``pre_persist_write`` REQUIRED hook the same
way ``cron/scheduler.py`` and ``hermes_cli/kanban_db.py`` invoke
``pre_run_start`` -- via ``hermes_cli.plugins.get_required_hook_directive`` --
and translates its directive into one of three outcomes:

* **staged** (``action == "allow"`` with ``staged=True``): the content was
  diverted into a governed, content-addressed quarantine by the
  ``agent-lineage`` policy worker. The canonical path is NOT written here --
  release (verification + independent Ed25519 attestation + atomic promote)
  is a separate, later governed step. Staging alone confers no authority.
* **denied** (``action == "block"``): the write is refused outright.
  ``governed_persist`` returns ``PersistResult(denied=True, message=...)``
  without writing anything; each call site raises :class:`PersistDenied`
  and lets it propagate exactly the way a failed write already did (the
  tool registry's broad ``except Exception`` -> ``{"error": ...}`` dispatch
  wrapper) -- no canonical write, no crash.
* **passthrough** (``action == "allow"`` with no ``staged`` flag): no
  required-hook enforcer is currently registered for ``pre_persist_write``
  (the pre-cutover default -- see ``docs/HERMES_INTEGRATION.md`` and the
  companion agent-lineage plan; deploying the governed install is an
  explicit, separate cutover, not part of wiring this funnel). This mirrors
  the established ``pre_run_start`` precedent (``cron/scheduler.py``,
  ``hermes_cli/kanban_db.py``): a bare "allow" with nothing further attached
  means "no governance is configured for this boundary yet" -- ``governed_
  persist`` performs the ORIGINAL atomic canonical write itself, unchanged.
  Once an operator opts in (``plugins.required: [agent-lineage]``), the SAME
  ``pre_persist_write`` callback starts returning ``staged=True`` and this
  same code path enforces it with no further changes anywhere.

When the hook itself is unreachable (the required-hook call raises -- for
example because the plugin subsystem isn't importable in this execution
context, or a worker crashes mid-call) OR returns something this funnel does
not recognize as a coherent decision, ``governed_persist`` makes NO policy
decision of its own. It stages the content locally, decision-less, under
``$HERMES_HOME/persist-quarantine-local/<sha256hex>/`` and logs a warning.
This grants nothing -- the canonical path is still never written on this
path -- and the audit gap is recorded (as a warning) for the next human or
operator to see; only a later governed sweep can ever release it. This is
deliberately safer than either raising a crash (losing the agent's turn) or
silently writing straight through (defeating the whole point of the funnel
during an outage).

SOUL.md: recon across this fork (``hermes_cli/config.py``,
``hermes_cli/profiles.py``, ``hermes_cli/doctor.py``, and the dashboard's
``PUT /api/profiles/{name}/soul`` in ``hermes_cli/web_server.py``) found only
bootstrap/default-creation writers and the dashboard's human-editor endpoint
(a person editing SOUL.md through the desktop/web UI) -- no runtime
(agent-driven, autonomous) SOUL.md write site exists in this fork today. Per
the design's scope decision #1, human editor edits to SOUL.md are explicitly
OUT of scope for this funnel by construction (there is no runtime hook to
traverse). ``governed_persist("soul", ...)`` is fully implemented and tested
so a future runtime SOUL writer only has to call it -- no funnel changes
needed -- but nothing calls it with ``kind="soul"`` yet.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home
from utils import atomic_replace

__all__ = ["PersistResult", "PersistDenied", "governed_persist"]

logger = logging.getLogger(__name__)

VALID_KINDS = frozenset({"memory", "skill", "soul"})

_LOCAL_STAGE_DIRNAME = "persist-quarantine-local"
_DEFAULT_SESSION_ID = "hermes.persist.local"


class PersistDenied(RuntimeError):
    """Raised by a refactored write site when ``governed_persist`` denies a write.

    ``governed_persist`` itself only RETURNS ``PersistResult(denied=True, ...)``
    -- it never raises. Each call site is responsible for turning a denial
    into this exception and letting it propagate to whatever already turns
    an exception from the old direct write into a caller-visible error (the
    tool registry's broad ``except Exception`` -> ``{"error": ...}`` dispatch
    wrapper) -- exactly mirroring how an ``OSError`` from the previous direct
    write used to surface.
    """


@dataclass(frozen=True)
class PersistResult:
    """Outcome of routing one write through the governed persistence funnel.

    The canonical write, when one happens at all, is already DONE by the
    time this is returned -- ``governed_persist`` is the only place that
    performs it (directly on passthrough, never on ``staged``/``denied``).
    """

    staged: bool
    digest: Optional[str]
    denied: bool
    message: str = ""


def _atomic_write_bytes(path: Path, content: bytes, *, prefix: str) -> None:
    """Temp-file + atomic rename, shared by the canonical write and the
    decision-less local stage. fsync's before rename for durability."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _hook_target_path(file_path: Path) -> str:
    """Best-effort path, relative to HERMES_HOME, to send as the hook's
    ``target_path`` -- purely descriptive/audit metadata for the eventual
    governed release step. Falls back to the bare filename when the target
    lives outside HERMES_HOME (e.g. an externally-configured skills root);
    the actual write below always uses the caller's exact ``file_path``, so
    this fallback never affects where content is written.
    """
    try:
        home = get_hermes_home().resolve()
        return file_path.resolve().relative_to(home).as_posix()
    except (OSError, ValueError):
        return file_path.name


def _stage_local(kind: str, target_path: str, content: bytes, meta: dict[str, Any]) -> PersistResult:
    """Decision-less local fallback when the required hook is unreachable.

    Grants nothing: the content is durably recorded so it isn't lost, but
    only a later governed sweep (release, agent-lineage side) can ever
    promote it to a canonical path.
    """
    hex_digest = hashlib.sha256(content).hexdigest()
    root = get_hermes_home() / _LOCAL_STAGE_DIRNAME / hex_digest
    record = {
        "kind": kind,
        "target_path": target_path,
        "digest": f"sha256:{hex_digest}",
        "meta": meta,
        "staged_at": datetime.now(timezone.utc).isoformat(),
        "status": "staged-local",
    }
    try:
        _atomic_write_bytes(root / "content.bin", content, prefix=".persist-local-content-")
        _atomic_write_bytes(
            root / "meta.json",
            (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            prefix=".persist-local-meta-",
        )
    except OSError as exc:
        logger.error(
            "governed_persist: local staging failed for kind=%s (%s) -- refusing the write",
            kind, exc,
        )
        return PersistResult(
            staged=False,
            digest=None,
            denied=True,
            message="persistence unavailable: local staging failed",
        )
    logger.warning(
        "governed_persist: pre_persist_write is unreachable -- staged '%s' (kind=%s) "
        "locally under %s with no policy decision; canonical path was NOT written",
        target_path, kind, root,
    )
    return PersistResult(
        staged=True,
        digest=f"sha256:{hex_digest}",
        denied=False,
        message="staged locally: no policy enforcer reachable for pre_persist_write",
    )


def governed_persist(
    kind: str,
    path: str,
    content: "bytes | str",
    meta: Optional[dict[str, Any]] = None,
) -> PersistResult:
    """Route one runtime write through the ``pre_persist_write`` funnel.

    ``path`` is the actual canonical filesystem path the runtime would have
    written to (absolute, or resolvable from the process's cwd). Returns a
    :class:`PersistResult` describing what happened; raises
    :class:`PersistDenied` when the write was refused. The canonical write
    itself -- when one happens at all -- is performed HERE, atomically, and
    only on the passthrough outcome; ``staged``/``denied`` never touch it.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}, got {kind!r}")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray)):
        content_bytes = bytes(content)
    else:
        raise TypeError("content must be bytes or str")

    file_path = Path(path)
    target_path = _hook_target_path(file_path)
    meta_dict: dict[str, Any] = dict(meta) if isinstance(meta, dict) else {}
    session_id = str(meta_dict.get("session_id") or _DEFAULT_SESSION_ID)[:256]

    try:
        from hermes_cli.plugins import get_required_hook_directive

        directive = get_required_hook_directive(
            "pre_persist_write",
            session_id=session_id,
            kind=kind,
            target_path=target_path,
            content_b64=base64.b64encode(content_bytes).decode("ascii"),
            meta=meta_dict,
        )
    except Exception as exc:
        logger.warning(
            "governed_persist: pre_persist_write hook unreachable (%s: %s) -- "
            "staging kind=%s locally with no governance decision",
            type(exc).__name__, exc, kind,
        )
        return _stage_local(kind, target_path, content_bytes, meta_dict)

    if not isinstance(directive, dict):
        logger.warning(
            "governed_persist: pre_persist_write returned a non-dict directive "
            "(%r) -- staging kind=%s locally with no governance decision",
            directive, kind,
        )
        return _stage_local(kind, target_path, content_bytes, meta_dict)

    action = directive.get("action")

    if action == "block":
        message = str(directive.get("message") or "persistence denied by policy")
        return PersistResult(staged=False, digest=None, denied=True, message=message)

    if action == "allow" and directive.get("staged") is True:
        digest = directive.get("digest")
        digest = digest if isinstance(digest, str) and digest else None
        return PersistResult(staged=True, digest=digest, denied=False, message="")

    if action == "allow":
        # No required enforcer is registered for pre_persist_write (the
        # pre-cutover default) -- perform the original canonical write
        # ourselves. See the module docstring's "passthrough" case.
        _atomic_write_bytes(file_path, content_bytes, prefix=f".{file_path.name}.persist.")
        return PersistResult(staged=False, digest=None, denied=False, message="")

    # Anything else -- a missing action, or a directive shape this funnel
    # does not recognize as a coherent decision ("approve" is not valid for
    # pre_persist_write) -- is not something to guess about. Fail closed to
    # the same decision-less local stage as an unreachable hook.
    logger.warning(
        "governed_persist: pre_persist_write returned an unrecognized directive "
        "%r -- staging kind=%s locally with no governance decision",
        directive, kind,
    )
    return _stage_local(kind, target_path, content_bytes, meta_dict)
