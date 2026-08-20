"""The one voice handler this plugin adds.

Remote access is a background service, not a conversational feature, so
the handler stays small on purpose: it answers "is remote access on?",
tells you who has been in lately, and can turn the link off with a
confirmation. Everything richer belongs on the dashboard page, where
there is room to show a fingerprint.

Band 265 — the device-control and comms range (200-269). Remote access
is a system-control surface, the same family as ``wifi`` (120) and
``homelab`` (250), so that range is where it belongs on the merits.

It also has to be there: the ``library`` handler at band 310 carries a
greedy ``^(?:do i have|is|have i got) (.+?)$`` fast path, which reads
"is remote access on" as a question about the music library. Sitting
above 310 is what stops that. Every fast path here is anchored on the
literal phrase "remote access", so nothing in this handler can poach in
the other direction.

``requires_network = "no"``: every answer comes from the plugin's own
tables. Being unable to reach LTL is exactly when you most want to be
told the link is down, so this must work offline.
"""

from __future__ import annotations

import re
from typing import Any

from domovoi.sdk import FastPath, Handler, HandlerDisplay, Response

from .. import store

_STATUS_RE = re.compile(
    r"^(?:is |what'?s the |show (?:me )?the )?remote access"
    r"(?: (?:on|off|up|working|status|state|enabled|connected))?\??$"
)
_DEVICES_RE = re.compile(
    r"^(?:who|what devices?) (?:can|has) (?:access|reach|connect to) "
    r"(?:the house|this house|domovoi|me) remotely\??$"
)
_TOGGLE_RE = re.compile(r"^turn (?P<state>on|off) remote access$")

CONFIRM_TOGGLE = "ltl_remote.toggle"


class RemoteAccessHandler(Handler):
    name = "ltl_remote"
    priority_band = 265          # band rationale: device/system control (200-269),
                                 # and it MUST sit above library's greedy
                                 # "^is (.+)$" at 310, which would otherwise
                                 # poach "is remote access on". Every fast path
                                 # here is anchored on the literal phrase
                                 # "remote access", so it poaches nothing.
    display = HandlerDisplay(
        label="Remote Access", tone="info", icon="web/static/icon.svg"
    )
    requires_network = "no"
    confirmation_kinds = (CONFIRM_TOGGLE,)
    tool_schema = {
        "name": "ltl_remote",
        "description": (
            "Report whether remote access to this house is connected, list the "
            "devices approved to connect, or turn remote access on or off. "
            "Examples: 'is remote access on', 'turn off remote access'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "devices", "enable", "disable"],
                    "description": "What to do. Defaults to reporting status.",
                }
            },
            "required": [],
        },
    }

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk
        self.fast_paths = [
            FastPath(_STATUS_RE, RemoteAccessHandler._say_status),
            FastPath(_DEVICES_RE, RemoteAccessHandler._say_devices),
            FastPath(_TOGGLE_RE, RemoteAccessHandler._offer_toggle),
        ]

    # ── spoken answers ──────────────────────────────────────────────────

    async def _say_status(self, match, ctx, session) -> Response:
        return Response(text=await self._status_sentence())

    async def _status_sentence(self) -> str:
        state = await store.get_link_state(self._sdk)
        if not self._sdk.config.enabled:
            return "Remote access is turned off in settings."
        if not state.get("household_id"):
            return (
                "Remote access isn't paired yet. There's a pairing code waiting "
                "on the Remote Access page of the dashboard."
            )
        connection = state.get("connection_state") or "idle"
        if connection == "connected":
            devices = await store.list_devices(self._sdk, status="approved")
            count = len(devices)
            plural = "device" if count == 1 else "devices"
            return f"Remote access is connected, with {count} approved {plural}."
        if connection == "revoked":
            return (
                "Remote access was turned off at the other end — that usually "
                "means the subscription lapsed. The dashboard has the details."
            )
        error = state.get("last_error")
        if error:
            return "Remote access is offline. It's retrying, but something's wrong."
        return "Remote access is paired but not connected right now. It'll keep trying."

    async def _say_devices(self, match, ctx, session) -> Response:
        approved = await store.list_devices(self._sdk, status="approved")
        pending = await store.list_devices(self._sdk, status="pending")
        if not approved and not pending:
            return Response(text="No devices are approved for remote access.")
        parts: list[str] = []
        if approved:
            labels = [str(d["label"]) for d in approved[:5]]
            if len(labels) > 1:
                listed = ", ".join(labels[:-1]) + f" and {labels[-1]}"
            else:
                listed = labels[0]
            parts.append(f"Approved: {listed}.")
        if pending:
            count = len(pending)
            noun = "device is" if count == 1 else "devices are"
            parts.append(f"{count} {noun} waiting for approval on the dashboard.")
        return Response(text=" ".join(parts))

    async def _offer_toggle(self, match, ctx, session) -> Response:
        """Turning the link on or off is a real change, so it asks first.

        The same courtesy Domovoi extends elsewhere: a voice command that
        alters how the house is reachable from the internet should never
        be one mis-hearing away from happening.
        """
        wanted_on = match.group("state") == "on"
        if wanted_on == bool(self._sdk.config.enabled):
            word = "already on" if wanted_on else "already off"
            return Response(text=f"Remote access is {word}.")
        await self._sdk.sessions.request_confirmation(
            session,
            ctx.session_id or ctx.room_id,
            kind=CONFIRM_TOGGLE,
            handler=self.name,
            data={"enable": wanted_on},
            prompt="confirm remote access toggle",
        )
        verb = "turn remote access on" if wanted_on else "turn remote access off"
        tail = (
            "Approved devices will be able to reach the house again."
            if wanted_on
            else "Nothing away from home will be able to reach the house."
        )
        return Response(text=f"Just to check — {verb}? {tail}")

    async def handle_confirmation(
        self, kind, data, affirmative, ctx, session
    ) -> Response:
        if not affirmative:
            return Response(text="Leaving remote access as it is.")
        enable = bool(data.get("enable"))
        # Written through the config bridge, not by mutating the settings
        # object: the value has to survive a restart, and the dashboard
        # has to see it change.
        from domovoi.plugins_runtime.config_bridge import PLUGIN_CONFIG

        PLUGIN_CONFIG.write_values(self._sdk.slug, {"enabled": enable})
        state = "on" if enable else "off"
        return Response(
            text=f"Remote access is {state}. "
            + ("It'll reconnect shortly." if enable else "The link is closing now.")
        )

    # ── LLM tool-call and fallthrough ───────────────────────────────────

    async def execute(self, intent, ctx, session) -> Response:
        return Response(text=await self._status_sentence())

    async def execute_from_tool(self, args, ctx, session) -> Response:
        action = str(args.get("action") or "status")
        if action == "devices":
            return await self._say_devices(None, ctx, session)
        if action in ("enable", "disable"):
            match = _TOGGLE_RE.match(
                f"turn {'on' if action == 'enable' else 'off'} remote access"
            )
            return await self._offer_toggle(match, ctx, session)
        return Response(text=await self._status_sentence())
