"""Letta ↔ domovoi tool bridge for conversational chat mode (#8).

When Domovoi is in conversational mode the local Ollama (driving the
Letta agent) can call tools. Those tools are NOT new capabilities — they
are a CURATED subset of the core's *existing* handlers (the media
set: music, playlist, library, plus any chat-exposed plugin
handlers), exposed to Letta so a
chatty turn can still "do things" (put a song on) without leaving the
conversation. Opt-IN per handler via ``Handler.chat_exposed``.

Two halves:

  - ``build_chat_tool_sources()`` — for every ``chat_exposed`` handler,
    generates a tiny PROXY function as Python SOURCE (via ``_proxy_source``)
    from the handler's ``tool_schema``. letta-client 0.1.x tools ARE
    server-side code (``tools.upsert(source_code=…)``), not OpenAI-style
    JSON schemas — Letta derives the tool name + arg schema from the
    function signature + Google-style docstring, then runs the function
    inside its OWN container sandbox when the model calls it. Since that
    sandbox can't reach the core's handlers/DB/MPD directly, each
    proxy just POSTs ``{tool, args}`` back to ``POST /v1/admin/chat-tool``.

  - ``dispatch_tool(name, args, *, app)`` — the server side of that round
    trip. Routes the proxied call to the matching ``handler.execute_from_tool``
    (building an Intent+Context like ``main._admin_route_intent`` does),
    gating to ``chat_exposed`` handlers. Returns a SHORT text string — what
    the agent folds back into the conversation.

A SearXNG DISPATCH path (``SEARXNG_TOOL_NAME`` / ``_dispatch_searxng``)
is also present but currently UNREACHABLE: ``build_chat_tool_sources``
does not yet emit a searxng proxy, so no ``searxng_web_lookup`` tool is
registered for the model to call. It is staged for a future searxng
proxy source (see ``dispatch_tool``); until then it is dead-but-kept.

⚠️  SPIKE — live tool-call execution is unproven. The schemas are the
    real handler schemas and ``dispatch_tool`` runs the real handlers, but
    whether the local model reliably *emits* well-formed tool calls (and
    whether Letta's server-side proxy round-trip behaves) has not been
    validated end-to-end. See ``domovoi/clients/letta.py`` and the
    chat-mode runbook in ``domovoi/README.md``.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


# Tool name for the in-network web lookup. Distinct from any handler name
# (handlers don't use this string) so dispatch can branch on it cleanly.
# NOTE: the DISPATCH path for this is wired (``_dispatch_searxng``) but
# currently UNREACHABLE — ``build_chat_tool_sources`` does not emit a
# searxng proxy source, so no such tool is registered for the model to
# call. Staged for a future searxng proxy; kept so enabling it is one edit.
SEARXNG_TOOL_NAME = "searxng_web_lookup"

# How many SearXNG results to fold into the spoken summary. A voice turn
# can't read ten links aloud; the top few titles + snippets are enough for
# the agent to compose a short answer.
_SEARXNG_MAX_RESULTS = 4


def _sanitize_doc(text: str) -> str:
    """Make ``text`` safe to embed inside a generated ``\"\"\"…\"\"\"`` docstring.

    The proxy source is Python we generate and then Letta ``exec``s
    server-side, so any handler/schema string interpolated into the
    docstring must not be able to (a) close the docstring early with
    ``\"\"\"`` and inject code, or (b) corrupt the Google-style ``Args:``
    block layout with stray newlines/backslashes. Schemas were first-party
    once; with plugin handlers they are THIRD-PARTY input (design §7.7),
    so this is security-critical: neutralize triple-quotes, drop the
    backslash (so no accidental/injected escape sequences), flatten
    newlines + tabs to single spaces, strip remaining control characters,
    and length-cap the result before templating."""
    cleaned = (
        (text or "")
        .replace("\\", "")
        .replace('"""', "'''")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )
    cleaned = "".join(c for c in cleaned if c.isprintable())
    return cleaned[:500]


def _pytype(js_type: str | None) -> str:
    """JSON-Schema type → a Python type-hint name for the generated proxy fn."""
    return {"string": "str", "integer": "int", "number": "float",
            "boolean": "bool"}.get(js_type or "string", "str")


def _proxy_source(schema: dict[str, Any], callback_url: str) -> str:
    """Generate a Letta tool as PYTHON SOURCE that proxies back to this
    domovoi's ``POST /v1/admin/chat-tool`` endpoint.

    letta-client 0.1.x tools are SERVER-SIDE code — ``tools.upsert`` takes
    ``source_code``, not a JSON schema, and the function runs inside Letta's own
    container, which can't reach the core's handlers/DB/MPD directly. So
    each tool is a thin proxy: it packs its args and POSTs ``{tool, args}`` to the
    core, which runs the REAL handler (``dispatch_tool``) and returns text
    the agent folds back into the conversation. Letta derives the tool name +
    arg-schema from the function signature + a Google-style docstring, so EVERY
    parameter needs an ``Args:`` description (Letta 400s otherwise)."""
    name = schema["name"]
    desc = _sanitize_doc(schema.get("description") or f"Run the {name} action.")
    params = schema.get("parameters") or {}
    props: dict[str, Any] = params.get("properties") or {}
    required = [p for p in (params.get("required") or []) if p in props]
    ordered = required + [p for p in props if p not in required]

    sig_parts: list[str] = []
    doc_args: list[str] = []
    body_lines: list[str] = []
    for p in ordered:
        spec = props.get(p) or {}
        pt = _pytype(spec.get("type"))
        sig_parts.append(f"{p}: {pt}" if p in required else f"{p}: {pt} = None")
        pdesc = _sanitize_doc((spec.get("description") or f"The {p.replace('_', ' ')}.").strip())
        if spec.get("enum"):
            pdesc = _sanitize_doc(f"{pdesc} One of: {', '.join(str(e) for e in spec['enum'])}.")
        doc_args.append(f"        {p}: {pdesc}")
        body_lines.append(f"    if {p} is not None and {p} != '':\n        _a[{p!r}] = {p}")

    sig = ", ".join(sig_parts)
    docstring = (desc + "\n\n    Args:\n" + "\n".join(doc_args) + "\n    ") if doc_args else desc
    body = "\n".join(body_lines) if body_lines else "    pass"
    url = callback_url.rstrip("/") + "/v1/admin/chat-tool"
    return (
        f"def {name}({sig}):\n"
        f'    """{docstring}"""\n'
        f"    import json as _json, urllib.request as _u\n"
        f"    _a = {{}}\n"
        f"{body}\n"
        f"    _req = _u.Request(\n"
        f"        {url!r},\n"
        f"        data=_json.dumps({{'tool': {name!r}, 'args': _a}}).encode(),\n"
        f"        headers={{'Content-Type': 'application/json'}},\n"
        f"    )\n"
        f"    try:\n"
        f"        with _u.urlopen(_req, timeout=25) as _r:\n"
        f"            return _json.load(_r).get('text', '') or 'Done.'\n"
        f"    except Exception:\n"
        f"        return 'Sorry, I could not do that right now.'\n"
    )


def build_chat_tool_sources() -> list[str]:
    """Proxy-tool SOURCE CODE for every handler with ``chat_exposed = True``.

    Opt-IN via the flag (not opt-out): a new handler is invisible to chat until
    it deliberately sets ``chat_exposed``. Only handlers with a usable
    ``tool_schema`` are bridged. Returns Python source strings for
    ``RealLettaClient._ensure_tools`` to ``tools.upsert(source_code=…)``.
    Curated to conversational-fit handlers (media first) rather than all 20.
    """
    from domovoi.config import settings
    from domovoi.handlers import HANDLERS

    sources: list[str] = []
    for h in HANDLERS:
        if not getattr(h, "chat_exposed", False):
            continue
        schema = getattr(h, "tool_schema", None)
        if not isinstance(schema, dict) or not schema.get("name"):
            continue
        # The tool name becomes a Python ``def`` name and each param name a
        # positional arg, both interpolated into generated source; a non-
        # identifier would emit un-compilable code. With plugin handlers
        # these are third-party schemas (design §7.7): names must be
        # identifiers AND ASCII-only (Unicode-homoglyph identifiers
        # rejected) AND the tool name must equal the registered handler
        # name. Failures skip THAT tool loudly, never the plugin's voice path.
        name = schema["name"]
        props = (schema.get("parameters") or {}).get("properties") or {}
        if (
            not str(name).isidentifier()
            or not str(name).isascii()
            or name != h.name
            or any(
                not str(p).isidentifier() or not str(p).isascii() for p in props
            )
        ):
            log.warning(
                "chat-tool source skipped: %r has a non-ASCII-identifier "
                "tool/param name or does not match its handler name",
                name,
            )
            continue
        source = _proxy_source(schema, settings.letta_tool_callback_url)
        try:
            # Final syntax gate (§7.7): generated source must compile.
            compile(source, "<letta-proxy-tool>", "exec")
        except SyntaxError as e:
            log.warning("chat-tool source skipped: %r does not compile (%s)", name, e)
            continue
        sources.append(source)
    return sources


async def dispatch_tool(name: str, args: dict[str, Any], *, app: Any = None) -> str:
    """Route a proxied chat-mode tool call to the matching handler or SearXNG.

    ``name`` is the tool name (a ``chat_exposed`` handler ``name`` or the
    staged ``searxng_web_lookup``); ``args`` is the model-supplied argument
    dict. Returns a short text result for the agent to fold into the
    conversation. Called by ``POST /v1/admin/chat-tool`` (the endpoint the
    generated proxy tools POST back to).

    For handler tools this rebuilds the same Intent+Context the voice/admin
    paths use (cf. ``main._admin_route_intent``) and calls
    ``handler.execute_from_tool`` inside a fresh DB session — so a chat-mode
    "put some jazz on" hits the exact code a spoken command would, including
    persistence. Non-``chat_exposed`` handlers are refused before any DB
    or side effect. Online state is read from the live
    ``ConnectivityProbe`` when ``app`` is provided so a network-dependent
    handler degrades correctly; when ``app`` is None (e.g. a direct test
    call) we assume online.

    Errors degrade to a short apology string rather than raising — a flaky
    tool must never crash the spoken stream. This is part of the spike's
    tool-reliability surface.
    """
    if not name:
        return "I couldn't tell which tool to run."

    if name == SEARXNG_TOOL_NAME:
        return await _dispatch_searxng(args)

    return await _dispatch_handler(name, args, app=app)


async def _dispatch_searxng(args: dict[str, Any]) -> str:
    """Run an in-network SearXNG lookup and render a short text summary."""
    from domovoi.clients.searxng import get_searxng_client

    query = str(args.get("query") or "").strip()
    if not query:
        return "I need something to search for."
    client = get_searxng_client()
    try:
        results = await client.search(query, max_results=_SEARXNG_MAX_RESULTS)
    except Exception as e:  # noqa: BLE001 — degrade, don't crash the stream
        log.warning("chat-mode searxng lookup failed for %r: %s", query, e)
        return f"I couldn't search for {query} right now."
    if not results:
        return f"I didn't find anything for {query}."
    # Compact, voice-friendly: title + snippet per result, no URLs.
    lines = [f"{r.title}: {r.content}".strip().rstrip(":") for r in results]
    return f"Web results for {query}:\n" + "\n".join(lines)


async def _dispatch_handler(name: str, args: dict[str, Any], *, app: Any) -> str:
    """Run an existing handler's ``execute_from_tool`` and return its text."""
    from domovoi.config import settings
    from domovoi.db.session import session_scope
    from domovoi.handlers import HANDLER_BY_NAME
    from domovoi.models import Context

    handler = HANDLER_BY_NAME.get(name)
    if handler is None:
        log.warning("chat-mode tool call for unknown handler %r", name)
        return f"I don't have a way to {name} right now."
    if not getattr(handler, "chat_exposed", False):
        # Defense: the /v1/admin/chat-tool endpoint is reachable from Letta's
        # sandbox, so refuse any handler that didn't opt into chat — even if a
        # generated tool or a stray call names one.
        log.warning("chat-mode tool call for non-chat-exposed handler %r", name)
        return f"I can't do {name} from a conversation."

    # Mirror main._admin_route_intent's Context build. room_id comes from
    # the live session map when available so room-scoped handlers (music,
    # radio) act on the right satellite; falls back to None otherwise.
    online = True
    room_id: str | None = None
    if app is not None:
        probe = getattr(app.state, "probe", None)
        if probe is not None:
            online = bool(getattr(probe, "online", True))
        room_id = _current_chat_room(app)

    ctx = Context(
        room_id=room_id,
        online=online,
        bot_name=settings.bot_name,
        app=app,
    )

    try:
        async with session_scope() as s:
            # Honor the requires_network contract the router enforces: a
            # network-dependent handler called while offline runs its
            # offline fallback instead of execute_from_tool.
            if handler.requires_network == "yes" and not online:
                from domovoi.models import Intent

                intent = Intent(transcript="", room_id=room_id)
                response = await handler.fallback_offline(intent, ctx, s)
            else:
                response = await handler.execute_from_tool(args or {}, ctx, s)
    except Exception as e:  # noqa: BLE001 — degrade, don't crash the stream
        log.warning("chat-mode handler dispatch failed (%s): %s", name, e)
        return f"I ran into a problem trying to {name} that."

    return (response.text or "").strip() or "Done."


def _current_chat_room(app: Any) -> str | None:
    """Best-effort: pick the room of the single active chat-mode session.

    The proxied tool call reaches ``dispatch_tool`` via the callback
    endpoint without an explicit room handle, so we look for an active
    session currently flagged conversational. If zero or more than one room
    is in chat mode we
    return None (handlers that need a room then surface their own "which
    room?" behavior rather than acting on the wrong satellite). This is a
    pragmatic spike-grade resolution; a cleaner binding can thread the
    room id through the Letta agent metadata once the live path is proven.
    """
    try:
        sessions: dict[str, Any] = getattr(app.state, "active_sessions", {}) or {}
    except Exception:
        return None
    chat_rooms = [
        rid
        for rid, sess in sessions.items()
        if bool(getattr(sess, "conversational_mode", False))
    ]
    if len(chat_rooms) == 1:
        return chat_rooms[0]
    return None
