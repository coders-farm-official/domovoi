"""Chat-tool resync (design §4.4, locked 20; sanitization per §7.7).

``resync_tools()`` is called by every plugin lifecycle mutation
(install / uninstall / enable / disable / upgrade): it regenerates the
chat proxy-tool sources from the MERGED handler registry (plugin
handlers included — ``build_chat_tool_sources`` walks ``HANDLERS``),
pushes each through the sanitization gate, upserts them to Letta, and
re-attaches the current toolset to EVERY existing agent (not just at
agent create). Idempotent; failures are surfaced-but-non-fatal (§3.2
matrix row 14).

The §7.7 gate here is the last line: identifier checks (ASCII-only —
Unicode-homoglyph identifiers rejected) already run in
``build_chat_tool_sources``; this module re-verifies and adds the final
``compile()`` syntax gate over each generated source before anything is
uploaded. A schema failing sanitization excludes THAT tool from chat
(logged) — it never blocks the plugin's voice path.
"""

from __future__ import annotations

import ast
import logging
from typing import Any

log = logging.getLogger(__name__)


def sanitize_sources(sources: list[str]) -> list[str]:
    """The final §7.7 syntax gate: every generated proxy source must be
    ASCII, parse, and compile. Anything else is dropped (logged)."""
    safe: list[str] = []
    for src in sources:
        if not src.isascii():
            log.warning("chat-tool source dropped: non-ASCII content")
            continue
        try:
            tree = ast.parse(src)
            compile(src, "<letta-proxy-tool>", "exec")
        except SyntaxError as e:
            log.warning("chat-tool source dropped: does not compile (%s)", e)
            continue
        # Exactly one top-level def whose name is an ASCII identifier.
        defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        if len(defs) != 1 or not defs[0].name.isidentifier() or not defs[0].name.isascii():
            log.warning("chat-tool source dropped: unexpected shape")
            continue
        safe.append(src)
    return safe


async def resync_tools() -> dict[str, Any]:
    """Rebuild + upsert chat tools and re-attach to every existing agent.

    No-op (``{"skipped": true}``) under stubs or when chat mode is off —
    the stub Letta client has no tool surface and a deployment that never
    enabled chat has no agents to resync.
    """
    from domovoi.clients.letta import get_letta_client
    from domovoi.config import settings
    from domovoi.letta_tools import build_chat_tool_sources

    if settings.use_stubs or not settings.chat_mode_enabled:
        return {"skipped": True, "reason": "stubs or chat mode disabled"}

    client = get_letta_client()
    sources = sanitize_sources(build_chat_tool_sources())

    ensure_tools = getattr(client, "_ensure_tools", None)
    if ensure_tools is None:
        return {"skipped": True, "reason": "letta client exposes no tool surface"}

    tool_ids = await ensure_tools(sources)

    # Re-attach the current toolset to every existing agent. The SDK
    # surface drifts across letta-client builds — every call is
    # best-effort and isolated so a single agent failure never sinks the
    # resync (§3.2 matrix row 14).
    agents_updated = 0
    errors: list[str] = []
    try:
        agents = await client._alist_agents()  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001
        return {
            "tools": len(tool_ids),
            "agents_updated": 0,
            "errors": [f"could not list agents: {e}"],
        }
    for agent in agents or ():
        agent_id = getattr(agent, "id", None)
        if not agent_id:
            continue
        try:
            raw = getattr(client, "_client", None)
            if raw is None:
                break
            modify = getattr(raw.agents, "modify", None) or getattr(
                raw.agents, "update", None
            )
            if modify is None:
                errors.append("letta SDK exposes no agent modify/update")
                break
            from domovoi.clients.letta import _maybe_await

            await _maybe_await(modify(agent_id=str(agent_id), tool_ids=tool_ids))
            agents_updated += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"agent {agent_id}: {e}")
    result = {
        "tools": len(tool_ids),
        "agents_updated": agents_updated,
        "errors": errors,
    }
    if errors:
        log.warning("letta resync completed with errors: %s", errors)
    else:
        log.info(
            "letta resync: %d tool(s), %d agent(s) updated",
            len(tool_ids), agents_updated,
        )
    return result
