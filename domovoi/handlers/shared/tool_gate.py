"""Shared vocabulary for the per-handler LLM tool-offer gate.

``Handler.offers_tool`` withholds a handler's ``tool_schema`` from the
LLM router for utterances that provably can't be that handler's — a
small tool model on a CPU host reaches for the nearest schema it is
shown rather than answering with no tool at all (F-V002/F-V004).

Several handlers need the same first half of that judgement: "is this a
bare knowledge question?". Keeping the pattern here means the two gates
that use it can't drift apart — a handler added to the withhold list
later gets exactly the openers that were measured, not a fresh
approximation of them.
"""

from __future__ import annotations

import re

# Question openers that never front a command. Deliberately NOT "what",
# "how", "when": those front real handler turns ("what is a third of
# ninety", "how many songs do i have", "what did i add today", "when is
# thanksgiving"). The optional lead-in matches the polite wrappers
# Whisper transcribes in full ("tell me who wrote the odyssey").
KNOWLEDGE_QUESTION_RE = re.compile(
    r"^(?:(?:tell me|do you know|do you happen to know|any idea|i wonder)[,\s]+)?"
    r"(?:who|whose|whom|why|where)\b"
)

# What a statement about a timer or reminder goes on with: "my reminder for
# 10 minutes DIDN'T go off", "that timer WAS useless".
STATEMENT_VERBS = (
    "is|isn't|isnt|was|wasn't|wasnt|were|weren't|are|aren't|did|didn't|didnt|"
    "does|doesn't|doesnt|has|hasn't|had|hadn't|never|won't|wont|went|failed|"
    "keeps|kept|sucks|sucked|already"
)

_TIMER_NOUN = r"(?:reminder|timer|alarm)s?\b"

# Talk ABOUT a timer or reminder, which asks for no timer or reminder
# action. The reminder and timer handlers both withhold their tools on
# it: shown the reminder tool, qwen3:8b (2026-09-30) made "you have a
# reminder in 10 minutes" a new reminder and "the reminder in 10 minutes
# is for the oven" a reminder "oven"; with only that tool withheld it
# reached for the timer's instead (a 10 minute "oven reminder" timer).
# Kept to shapes a command cannot take.
TIMER_STATEMENT_RE = re.compile(
    r"^(?:"
    # "my reminder for 10 minutes didn't go off", "that reminder was
    # useless", "the reminder in 10 minutes is for the oven": the verb
    # straight after the reminder and what it is for, so "the pasta timer,
    # how long is left" still asks the timer
    rf"(?:my|the|that|this|your|our|those|these)(?: [a-z'-]+){{0,3}}? {_TIMER_NOUN}"
    r"(?: (?:for|in|at|from|on|about|to|that|called) [a-z0-9' -]+?)?,?"
    rf" (?:{STATEMENT_VERBS})(?![a-z'-])"
    # "you have a reminder in 10 minutes", "i've got a timer going",
    # "you've set a reminder" — someone else's, or one that already
    # exists. Not "i need / i want a reminder", and not "i set a reminder
    # ..." / "you set a timer ...", which are also what Whisper makes of
    # "hi, set a reminder ..." / "can you set a timer ...".
    r"|(?:you(?:'ve| have| had| got| gave me)|(?:i|we)(?:'ve| have| had| got))"
    rf"(?: got)? (?!to\b)(?:[a-z'-]+ ){{0,3}}?{_TIMER_NOUN}"
    # "why didn't my reminder go off", "where did my timers go"
    rf"|(?:why|where|who|whose|whom)\b.*?\b{_TIMER_NOUN}"
    # "i set a reminder for 10 minutes and it never went off"
    rf"|.*?\b{_TIMER_NOUN}.*?\b(?:didn't|didnt|did not|never|doesn't|doesnt|"
    r"does not|won't|wont|wasn't|wasnt|failed to)"
    r" (?:[a-z'-]+ )??(?:go(?:es)? off|went off|work|ring|fire|sound|play|remind)"
    r")"
)
