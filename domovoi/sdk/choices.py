"""``domovoi.sdk.choices`` — reading the reply to a "did you mean …?" (SDK 1.10).

A plugin handler that asks a question whose answer may be free text
("Did you mean Lofi 24/7?" answered "yes", "the second one", "no, the jazz
one", or a bare name) declares the kind in ``Handler.choice_kinds`` as well
as ``confirmation_kinds`` and overrides ``handle_choice_reply``. The router
hands the whole next reply there before its yes/no reading. This module is
the reader the core's own music question uses, so a plugin's question
understands the same answers::

    from domovoi.sdk import choices

    reply = choices.parse_choice_reply(transcript, len(candidates))
    if reply.action == "pick":      # yes / "the second one" / "either"
        ...candidates[reply.index]
    elif reply.action == "decline": # no / "no thanks" / "never mind"
        ...
    elif reply.action == "name":    # a name: reply.name, with
        ...                         # reply.negated / .explicit / .otherwise
    else:                           # "pass": not about the question —
        return None                 # the router routes it as a new turn

``ChoiceReply.otherwise`` says what a name that matches nothing means:
``"pick"`` (it followed a yes: take the first), ``"decline"`` (it
followed a no) or ``"pass"`` (a bare reply: route it normally).
``explicit`` is True when the reply asked for something new ("no, play
X", "I meant X"); ``negated`` when it turned the question down ("no, X",
"I said X").

The reader loads on first use: the core's router and music modules are
imported inside the call, never when a plugin imports this module.
"""

from __future__ import annotations

from typing import Any


def parse_choice_reply(transcript: str, n: int) -> Any:
    """Classify ``transcript`` (the reply, as the router normalized it:
    lower case, trailing punctuation gone) to a question that offered
    ``n`` candidates. Returns a ``ChoiceReply`` (``action``, ``index``,
    ``name``, ``negated``, ``explicit``, ``otherwise``); pure apart from
    reading the router's own word lists."""
    from domovoi.handlers.music_choice import parse_choice_reply as _parse

    return _parse(transcript, n)


def __getattr__(name: str) -> Any:
    if name == "ChoiceReply":
        from domovoi.handlers.music_choice import ChoiceReply

        return ChoiceReply
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["parse_choice_reply"]  # and ChoiceReply, loaded on first use
