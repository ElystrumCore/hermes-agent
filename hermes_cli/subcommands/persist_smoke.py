"""``hermes persist-smoke`` subcommand parser.

Follows the same extraction pattern as ``hermes_cli/subcommands/doctor.py``:
the parser lives here, the handler is injected by ``main.py`` to avoid an
import cycle, and the actual implementation lives in
``hermes_cli/persist_smoke.py``.
"""

from __future__ import annotations

from typing import Callable


def build_persist_smoke_parser(subparsers, *, cmd_persist_smoke: Callable) -> None:
    """Attach the ``persist-smoke`` subcommand to ``subparsers``."""
    persist_smoke_parser = subparsers.add_parser(
        "persist-smoke",
        help="Smoke-test the governed persistence funnel (pre_persist_write)",
        description=(
            "Discovers plugins, asserts the 'pre_persist_write' required "
            "hook is registered, then routes one probe write through "
            "agent.persist_boundary.governed_persist and confirms it comes "
            "back genuinely staged (governed) rather than silently passing "
            "through to a canonical write. Exits 0 only on a confirmed "
            "staged outcome; any other result -- hook not registered, "
            "denied, or an ungoverned passthrough -- exits 1."
        ),
    )
    persist_smoke_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable text",
    )
    persist_smoke_parser.set_defaults(func=cmd_persist_smoke)
