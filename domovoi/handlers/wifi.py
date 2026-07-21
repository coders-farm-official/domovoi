"""WiFi handler — voice-triggered reassociate + diagnostic for the Pi's link.

Two shapes:

1. **Action** ("fix the wifi", "reset the wifi", "reconnect", "refresh wifi",
   "reassociate") — returns ``pi_action="reassociate_wifi"`` so the streaming
   layer adds it to the ``response_end`` frame; the satellite runs
   ``sudo wpa_cli -i wlan0 reassociate`` once TTS playback has drained.
2. **Diagnostic** ("how's your wifi", "what's your wifi rate", "what's your
   wifi signal") — reads the most recent ``wifi_status`` push from the Pi
   out of ``app.state.wifi_status[room_id]`` and reports rate + SSID.

Why this lives domovoi-side at all (the action is fully Pi-local):
intent routing is already centralized here, fast-path regexes are easy to
audit alongside the others, and the LLM tool route works for free. The
``pi_action`` field carries the side-effect across the wire.

Background context: 2026-05-06 incident
(`docs/INCIDENT_2026-05-06_TTS_CHOP.md`) — a Pi's AP-side rate-control
latched at 1 Mbit/s and TTS chop ensued. ``wpa_cli reassociate`` cleared
it in 5 seconds. The new ``WifiWatcher`` on the Pi handles the
autonomous case (rate < 5 Mbit/s → reassociate); this handler covers
the impatient-user case (notice "this feels slow" mid-conversation,
ask the bot to fix it without waiting for the next 60s poll).

``requires_network="no"`` — even though the action is recovery for a
broken link, the routing decision is fully local. Lives in the
``"no"`` bucket so the router will dispatch even when the core
is offline (which is incidental: the core being offline
implies the Pi *is* talking to it, so the Pi's WiFi is fine).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

log = logging.getLogger(__name__)


# ─── Fast-path regexes ────────────────────────────────────────────────────
# Router lower-cases + strips trailing punctuation before dispatching, so
# patterns are anchored on a normalized lower-case transcript.

# Action verbs that all map to "reassociate" — the underlying recovery is
# the same regardless of the user's mental model ("fix it" / "reset" /
# "reconnect" / "refresh").
_ACTION_RE = re.compile(
    r"^(?:"
    r"(?:please |can you |could you )?"
    r"(?:fix|reset|reconnect|refresh|restart|cycle|bounce|reassociate)"
    r" (?:the |your |my )?(?:wi[- ]?fi|wifi|network|connection|wireless)"
    r"|reassociate"
    r")$"
)

# Diagnostic — reads back rate + SSID from the most recent Pi-pushed
# wifi_status. No action.
_DIAGNOSTIC_RE = re.compile(
    r"^(?:"
    r"(?:how(?:'s| is)|what(?:'s| is)) (?:the |your )?(?:wi[- ]?fi|wifi|network|connection|wireless|signal)"
    r"(?: (?:doing|like|looking|connection|signal|rate|speed))?"
    r"|what(?:'s| is) (?:the |your )?(?:wi[- ]?fi|wifi|network) (?:rate|speed|signal)"
    r"|are you (?:having|connected to) (?:wi[- ]?fi|wifi|network|wireless)"
    r"|are you (?:having|on)(?: any)? (?:wi[- ]?fi|wifi|network) (?:issues|trouble|problems)"
    r")$"
)


def _format_rate(mbits: float) -> str:
    """'43 megabits per second' / '1.5 megabits per second' for TTS.

    Speaks "megabits per second" rather than "Mbps" because TTS
    pronunciations of the abbreviation are unreliable across engines.
    Strips the decimal when the value is whole — '43' beats '43.0' aloud.
    """
    if mbits >= 10 or float(mbits).is_integer():
        return f"{int(round(mbits))} megabits per second"
    return f"{mbits:.1f} megabits per second"


# ─── Handler ──────────────────────────────────────────────────────────────


class WifiHandler(Handler):
    name = "wifi"
    # band rationale: "fix the wifi" wins over any future handler that could grab "fix";
    #   tightly anchored regexes mean it still won't poach unrelated phrases.
    priority_band = 120
    display = HandlerDisplay(label="Wi-Fi", tone="device")
    requires_network = "no"

    tool_schema = {
        "name": "wifi",
        "description": (
            "Reassociate the satellite's WiFi link, or report its current "
            "rate and SSID. Use 'reassociate' when the user asks to fix / "
            "reset / refresh / reconnect WiFi. Use 'status' when the user "
            "asks how the WiFi is doing or what rate it's connected at."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["reassociate", "status"],
                },
            },
            "required": ["action"],
        },
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_ACTION_RE, WifiHandler._reassociate_from_match),
            FastPath(_DIAGNOSTIC_RE, WifiHandler._status_from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return Response(
            text="Did you want me to fix the WiFi, or check on it?",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        action = args.get("action")
        if action == "reassociate":
            return self._reassociate_response(ctx)
        if action == "status":
            return self._status_response(ctx)
        return Response(
            text=f"I don't know how to {action!r} the WiFi.",
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    # ─── Fast-path adapters ───────────────────────────────────────────
    async def _reassociate_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._reassociate_response(ctx)

    async def _status_from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._status_response(ctx)

    # ─── Core responses ───────────────────────────────────────────────
    def _reassociate_response(self, ctx: Context) -> Response:
        # The pi_action carries the side-effect across the wire — the
        # streaming layer copies it into response_end, and the satellite
        # executes wpa_cli reassociate once playback has drained.
        # "OK, reconnecting now." sets user expectation that the bot
        # heard them and the WS may briefly drop in a moment.
        return Response(
            text="OK, reconnecting now.",
            session_id=ctx.session_id,
            matched_handler=self.name,
            pi_action="reassociate_wifi",
        )

    def _status_response(self, ctx: Context) -> Response:
        # Read the most recent push from the Pi out of app.state. The
        # handler doesn't have a direct reference to the FastAPI app,
        # so the streaming layer stamps the cache onto Context.data
        # before routing. Falls back to a friendly "no reading yet"
        # when the Pi hasn't emitted a wifi_status frame yet (e.g.,
        # immediately after connect — the WiFi watcher's first poll is
        # 60s in).
        status = self._lookup_status(ctx)
        if status is None:
            return Response(
                text=(
                    "I don't have a recent WiFi reading from this room yet. "
                    "Give me about a minute and ask again."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        rx_mbits = status.get("rx_mbits")
        ssid = status.get("ssid")
        if rx_mbits is None:
            # A degraded Pi may have pushed a partial status (e.g., link
            # down). Surface what we can without crashing.
            return Response(
                text=(
                    "WiFi looks unhealthy — the satellite reported a link "
                    "but no rate. Try asking me to fix the WiFi."
                ),
                session_id=ctx.session_id,
                matched_handler=self.name,
            )
        rate_text = _format_rate(float(rx_mbits))
        if ssid:
            text = f"WiFi is connected at {rate_text} to {ssid}."
        else:
            text = f"WiFi is connected at {rate_text}."
        return Response(
            text=text,
            session_id=ctx.session_id,
            matched_handler=self.name,
        )

    @staticmethod
    def _lookup_status(ctx: Context) -> dict[str, Any] | None:
        # ``ctx.data`` carries the streaming-layer-stamped wifi_status
        # for ctx.room_id. None when the room hasn't pushed anything yet.
        cache = getattr(ctx, "wifi_status", None)
        if not cache:
            return None
        return cache
