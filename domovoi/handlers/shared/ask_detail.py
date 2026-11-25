"""Ask for the one missing detail, then finish the command (1.4.1, M2).

"Can you set a timer?" (filler-stripped to "set a timer") has no
duration; "set an alarm" has no time; "set a reminder" neither a task
nor a time. Each is something Domovoi does, so the answer is never the
model's hedge: the handler asks for the missing part ("Sure. For how
long?") and parks the question as a CHOICE (``Handler.choice_kinds``).
The next turn is the answer: "five minutes" becomes "timer for five
minutes", fed to the handler's own fast paths, so it sets exactly what
the whole command would have.

A reply that no fast path of the handler takes ("what time is it") is
not an answer: the question is dropped and the turn routed as usual. A
"never mind" drops it with "Okay.", a bare "yes" asks again.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.models import Context, Response

log = logging.getLogger(__name__)

#: How long the question waits for its answer.
TTL_SEC = 120.0

_NEVER_MIND_RE = re.compile(
    r"^(?:no|nope|nah|never ?mind|nvm|forget it|forget about it|cancel|cancel that|stop"
    r"|don't bother|no thanks|no thank you)(?:,? thanks|,? thank you)?\Z"
)
_YES_RE = re.compile(r"^(?:yes|yeah|yep|sure|ok|okay|please|yes please)\Z")
# Said in front of the detail: "um, five minutes", "make it 7 am", "for 10
# minutes please".
_LEAD_RE = re.compile(
    r"^(?:(?:um|uh|er|oh|ok|okay|well|so|yeah|yes)[,\s]+)*"
    r"(?:(?:make it|set it for|set it to|let's say|let's do|how about|maybe)\s+)?"
)
_PREP_RE = re.compile(r"^(?:for|at|in)\s+")
_TAIL_RE = re.compile(r"(?:,?\s+(?:please|thanks|thank you))+\Z")


async def ask(
    handler: Any, ctx: Context, session: AsyncSession, *, kind: str, question: str,
    prefixes: tuple[str, ...],
) -> Response:
    """Ask ``question`` and park it: the reply is completed with each of
    ``prefixes`` in turn ("timer for " + "five minutes") and run through
    ``handler``'s fast paths."""
    from domovoi.confirmations import request_confirmation

    if ctx.session_id is not None:
        try:
            await request_confirmation(
                session, ctx.session_id, kind=kind, handler=handler.name, prompt=question,
                data={"prefixes": list(prefixes), "expires_at": time.time() + TTL_SEC},
            )
        except Exception as e:  # noqa: BLE001 — the question is still asked
            log.warning("couldn't park %s: %s", kind, e)
    return Response(
        text=question, session_id=ctx.session_id, matched_handler=handler.name,
        expect_followup=ctx.session_id is not None,
    )


def candidates(data: dict[str, Any], transcript: str) -> list[str]:
    """The whole commands a reply could complete, in the order tried."""
    said = _TAIL_RE.sub("", _LEAD_RE.sub("", (transcript or "").strip().rstrip(".,!?"))).strip()
    if not said:
        return []
    bare = _PREP_RE.sub("", said)
    out: list[str] = []
    for prefix in data.get("prefixes") or []:
        for body in (said, bare):
            whole = f"{prefix}{body}".strip()
            if whole not in out:
                out.append(whole)
    return out


async def answer(
    handler: Any, kind: str, data: dict[str, Any], transcript: str, ctx: Context,
    session: AsyncSession,
) -> Response | None:
    """``handle_choice_reply`` for a question :func:`ask` parked."""
    try:
        expired = time.time() > float(data.get("expires_at"))
    except (TypeError, ValueError):
        expired = True
    if expired:
        return None
    said = (transcript or "").strip().rstrip(".,!?")
    if _NEVER_MIND_RE.match(said):
        return Response(text="Okay.", session_id=ctx.session_id, matched_handler=handler.name)
    if _YES_RE.match(said):
        question = str(data.get("prompt") or "")
        if not question:
            return None
        return await ask(handler, ctx, session, kind=kind, question=question,
                         prefixes=tuple(data.get("prefixes") or ()))
    from domovoi.handlers.base import as_fast_path
    from domovoi.router import run_fast_path

    for whole in candidates(data, said):
        for entry in handler.fast_paths:
            fp = as_fast_path(entry)
            m = fp.pattern.match(whole)
            if m is None:
                continue
            response = await run_fast_path(handler, fp, m, ctx, session)
            if response is not None:
                return response
    return None


__all__ = ["TTL_SEC", "answer", "ask", "candidates"]
