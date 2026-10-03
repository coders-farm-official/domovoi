"""Whether a transcript taken at a pause is a whole command — early commit.

Early endpointing, part B (design notes 2026-09-28). With speculative
transcription (``domovoi/streaming.py``) the core has a transcript of what
was said up to the first short pause while the satellite is still counting
its silence timeout (1.2 s). When that transcript is a complete, closed
command — "pause the music", "set a timer for ten minutes" — waiting out
the rest of the silence buys nothing, so the core may end the capture
itself (``end_capture``) after a much shorter hold and answer.

The cost of being wrong is real: whatever the person says after the hold
is lost ("set a timer for ten minutes … for the pasta" loses the label;
"stop … the timer" stops the music). So this is deliberately narrow:

* only a transcript that the router would send down a fast path whose
  ``FastPath.early_commit`` opts in — tier ``"A"`` (closed phrases nothing
  extends, short hold) or ``"B"`` (phrases a pause can split: a duration,
  a number, a clock question; longer hold). Language-model turns and open
  slots (``play (.+)``) never commit early. Plugin fast paths never;
* a complete yes/no answer to a parked confirmation, as tier ``"B"`` —
  but to a parked CHOICE (``Handler.choice_kinds``: "did you mean X?")
  only a complete yes: there a bare "no" is the reply most often followed
  by the correction ("no… play Pink Floyd"), so it runs to the
  satellite's own silence timeout;
* every one-word transcript is held as ``"B"`` whatever its path says —
  "stop" is also the start of "stop the timer", and lone words are what
  Whisper hears worst;
* never with sentence punctuation inside it ("Set a timer for 10 minutes.
  And 30 seconds." is two clauses and a pause), never trailing off ("…",
  a cut-off "2-"), never ending on a word that can't end a command
  ("… for the", "what's 5 plus").

The route is worked out by ``router.plan_route`` — the router's own
decision, without dispatching anything. Pure: no I/O, no settings; the
streaming layer supplies the parked confirmation and applies the holds.
``domovoi/tests/test_early_commit.py`` checks every tier against a corpus
of real commands for a shorter prefix that means something else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from domovoi.router import RoutePlan, plan_route

# A yes/no answer that is the whole utterance: the words the router's
# confirmation pre-empt accepts (router._YES_RE / _NO_RE), and at most a
# thank-you after them. "yes, and add the other one too" is not complete.
_COMPLETE_ANSWER_RE = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|correct|right|that's right|exactly|affirmative|"
    r"that is right|that's correct|confirmed|"
    r"no|nope|nah|wrong|incorrect|that's wrong|not quite|not exactly)"
    r"(?:,? (?:please|thanks|thank you))?$"
)
# The yes half of it: all a parked CHOICE commits early on.
_COMPLETE_YES_RE = re.compile(
    r"^(?:yes|yeah|yep|yup|sure|correct|right|that's right|exactly|affirmative|"
    r"that is right|that's correct|confirmed)"
    r"(?:,? (?:please|thanks|thank you))?$"
)

# Sentence punctuation with more words after it: Whisper writes a pause
# between clauses as a full stop ("What time is it? In Tokyo."), and an
# ellipsis anywhere is a hesitation.
_INTERNAL_BREAK_RE = re.compile(r"[.!?;:](?=\s+\S)|…")
# Trailing off: an ellipsis, or a word cut off at the pause ("… for 2-").
_TRAILING_CUT_RE = re.compile(r"(?:\.\.\.|…|[-–—])\s*$")
# A last word no command ends on: whatever was coming next was cut off.
# ("that" and "this" are left out on purpose: "cancel that", "skip this".)
_DANGLING_TAIL_RE = re.compile(
    r"\b(?:the|a|an|and|or|but|so|then|for|to|of|my|your|our|their|his|her|"
    r"in|on|at|with|by|from|into|called|named|about|"
    r"plus|minus|times|divided|multiplied|over|point|um|uh|er)$"
)

TIER_A = "A"
TIER_B = "B"


@dataclass(frozen=True)
class EarlyCommit:
    """A transcript that may end its capture early: which hold tier, and
    the route it would take (``router.plan_route``)."""

    tier: str
    plan: RoutePlan


def early_commit_for(raw_transcript: str, *, pending: Any = None) -> EarlyCommit | None:
    """The early-commit tier for a transcript taken at a pause, or None
    when the capture must run to the satellite's own silence timeout.
    ``pending`` is the session's parked ``pending_confirmation`` payload,
    if any."""
    raw = raw_transcript.strip()
    if not raw or _TRAILING_CUT_RE.search(raw) or _INTERNAL_BREAK_RE.search(raw):
        return None
    plan = plan_route(raw, pending=pending)
    if plan is None or not plan.transcript or _DANGLING_TAIL_RE.search(plan.transcript):
        return None
    if plan.path == "confirmation":
        whole = (
            _COMPLETE_YES_RE if plan.kind in plan.handler.choice_kinds else _COMPLETE_ANSWER_RE
        )
        tier = TIER_B if whole.match(plan.transcript) else None
    elif plan.handler.plugin_slug is not None or plan.fast_path is None:
        tier = None
    else:
        tier = plan.fast_path.early_commit
    if tier is None:
        return None
    if len(plan.transcript.split()) == 1:
        tier = TIER_B
    return EarlyCommit(tier=tier, plan=plan)
