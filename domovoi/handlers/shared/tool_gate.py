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
