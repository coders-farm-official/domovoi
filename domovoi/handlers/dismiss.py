"""DismissHandler — stand down after a false-positive wake word.

When the wake word fires by accident (a TV line, a passing "hey Jarvis"),
Domovoi opens the mic and the user's natural reaction is to wave it off:
"never mind", "nothing", "no thanks", "I'm good", "I don't need anything".
Without this handler those land in the Q&A fallthrough and Domovoi earnestly
answers "I don't need anything" as if it were a question — the opposite of
standing down.

This handler recognizes those brush-off phrases and replies with a short
acknowledgment and nothing else: no side effects, no ``expect_followup`` (so
the mic closes and the satellite returns to wake-word listen). If music was
playing when the accidental wake killed the Pi's player, the streaming layer's
post-turn auto-resume brings it back — a dismissal doesn't stop it.

Fully local; ``requires_network="no"``.

Ordering: registered FIRST in ``handlers/__init__`` — ahead of
VoiceProfileHandler — on purpose. Its ``i'm <name>`` enrollment regex would
otherwise grab "I'm good" / "I'm fine" and try to enroll a person literally
named "good". The dismiss set here is CLOSED (good/fine/set/done/okay/…) so a
real "I'm Sarah" still falls straight through to enrollment.
"""

from __future__ import annotations

import random
import re

from sqlalchemy.ext.asyncio import AsyncSession

from domovoi.handlers.base import FastPath, Handler, HandlerDisplay
from domovoi.models import Context, Intent, Response

# The router normalizes before dispatch: lower-cased, trailing punctuation
# stripped, and leading filler ("um", "okay,", "can you please") removed. So
# these patterns match the bare dismissal core. Fully $-anchored — every arm
# is a WHOLE-utterance brush-off, never a fragment of a real command.
#
# Deliberately NOT matched: a bare "stop" (owned by music/radio/chat-exit),
# and "i'm <name>" for any name outside the closed good/fine/set/done/okay
# set (that's a VoiceProfile enrollment).
_DISMISS_RE = re.compile(
    r"^(?:"
    # never mind / nothing
    r"nvm|never ?mind(?: (?:then|sorry|it))?"
    r"|nothing(?: (?:thanks|thank you|really|for now|important|else|i'?m good))?"
    r"|it'?s nothing"
    # don't need anything
    r"|(?:i|we) (?:don'?t|do not|didn'?t|did not) need "
    r"(?:anything|you|help|it|that)(?: (?:else|right now|anymore|for now))?"
    # i'm good / fine / all set  (CLOSED set — must not shadow "i'm <name>")
    r"|(?:no,? )?(?:i'?m|i am|we'?re|we are) (?:all )?"
    r"(?:good|fine|set|done|okay|ok|alright|all set|all good)(?: (?:thanks|thank you|for now))?"
    r"|(?:no,? )?all (?:good|set|done)"
    # no thanks / nope / not now
    r"|no(?:pe)?(?: (?:thanks|thank you|not now|i'?m good|i'?m fine))?"
    r"|nah(?: (?:i'?m good|thanks))?"
    r"|not (?:now|right now|anymore|at the moment)"
    # forget it / ignore / disregard / cancel / drop it
    r"|forget (?:it|about it|that)"
    r"|ignore (?:that|me|it)"
    r"|disregard(?: that| it)?"
    r"|cancel(?: that| it)?"
    r"|(?:leave|drop) it(?: alone)?"
    # false alarm / my mistake / sorry / oops
    r"|false alarm"
    r"|(?:my|sorry,? my) (?:mistake|bad)"
    r"|sorry(?:,? (?:never ?mind|my mistake|wrong one|false alarm))?"
    r"|(?:oops|whoops)(?:,? (?:sorry|never ?mind|nothing|my mistake))?"
    # that's all / that's it
    r"|(?:that'?s|that is) (?:all|it|everything|fine)(?: (?:for now|thanks))?"
    # go back to sleep / stand down / dismissed
    r"|go (?:back to|to) sleep"
    r"|stand down|as you were|dismissed|at ease"
    r")$"
)


# Short, unobtrusive acknowledgments — confirm Domovoi heard and is standing
# down (better than dead air, which invites the user to repeat themselves).
_ACKS = ("Okay.", "No problem.", "Alright.", "Sure.", "No worries.")


def _ack() -> str:
    return random.choice(_ACKS)


class DismissHandler(Handler):
    name = "dismiss"
    # band rationale: FIRST — a false-positive-wake brush-off ("never mind",
    # "nothing", "no thanks", "I'm good", "I don't need anything") must win
    # before anything tries to act on it. Notably it sits AHEAD of
    # voice_profile (110) on purpose: that handler's "i'm <name>" enrollment
    # regex would otherwise grab "I'm good" / "I'm fine" and enroll a person
    # named "good". Dismiss's phrase set is CLOSED (good/fine/set/done/okay/…)
    # so a real "I'm Sarah" still falls straight through to enrollment. It
    # never matches a bare "stop" (owned by music/radio/chat-exit).
    priority_band = 100
    display = HandlerDisplay(label="Dismiss", tone="neutral")
    requires_network = "no"

    tool_schema = {
        "name": "dismiss",
        "description": (
            "Acknowledge and stand down when the user is waving off the "
            "assistant — declining help, cancelling the interaction, or "
            "reacting to an accidental wake word. Use for 'never mind', "
            "'nothing', 'no thanks', \"I'm good\", \"I don't need anything\", "
            "'forget it', 'false alarm', 'go back to sleep', and similar "
            "brush-offs. Takes no arguments — do NOT use this to stop music, "
            "a timer, or a drop-in call (those have their own handlers)."
        ),
        "parameters": {"type": "object", "properties": {}},
    }

    def __init__(self) -> None:
        self.fast_paths = [
            FastPath(_DISMISS_RE, DismissHandler._from_match),
        ]

    async def execute(
        self, intent: Intent, ctx: Context, session: AsyncSession
    ) -> Response:
        return self._dismiss(ctx)

    async def execute_from_tool(
        self, args: dict, ctx: Context, session: AsyncSession
    ) -> Response:
        return self._dismiss(ctx)

    async def _from_match(
        self, m: re.Match[str], ctx: Context, session: AsyncSession
    ) -> Response:
        return self._dismiss(ctx)

    def _dismiss(self, ctx: Context) -> Response:
        # No expect_followup: the interaction is over, the mic closes, the
        # satellite goes back to wake-word listen. No music_action: a
        # dismissal must not stop playback (the post-turn auto-resume restores
        # any music the accidental wake interrupted).
        return Response(
            text=_ack(),
            session_id=ctx.session_id,
            matched_handler=self.name,
            expect_followup=False,
        )
