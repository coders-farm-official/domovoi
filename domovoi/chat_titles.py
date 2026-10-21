"""Chat titles (assistant plan part 7): the rules both chat writers share.

A chat is titled in two steps:

* **right away**, the first five words of its first message
  (:func:`first_words_title`), written by whichever process stores that
  message: the web's typed send (``web/backend/api/chat.py``) or the
  core's voice turn (``domovoi/phone_voice.py``);
* **after the first reply has finished**, a title of at most five words
  written by the chat model in the background (the web's title job). What
  the model says is cleaned up here (:func:`clean_model_title`); anything
  that doesn't read as a title leaves the first words in place.

A name a person gave the chat (``chat_threads.title_by_user``, V034) is
never written over. The Android app applies the same rules to the chats
kept on the phone (``ChatTitles.kt``); keep the two in step.
"""

from __future__ import annotations

import re
import unicodedata

#: The most words a title has, by either step.
TITLE_WORDS = 5

#: The longest title, in characters (one long word, a pasted link).
TITLE_MAX_CHARS = 60

#: What the model is asked (the same sentence on the phone).
TITLE_SYSTEM = "Write a title of at most five words for this chat. Reply with the title only."

#: How much of the first question and the first answer the model reads.
TITLE_EXCERPT_CHARS = 800

#: Tokens the model may spend on a title: five words and some slack.
TITLE_MAX_TOKENS = 16

_LEAD = re.compile(r"^(?:title|chat title|subject)\s*[:\-–]\s*", re.IGNORECASE)
_WRAP = "\"'`*_#“”‘’«»[]()<> "
_TRAIL = ".,;:!?-–—…"


def _plain_text(text: str) -> str:
    """``text`` without control or format characters: control characters
    (tabs, escapes) become spaces, format characters (bidi overrides such
    as U+202E, zero-width marks) go, so a title can't reorder or hide what
    the list shows around it."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cf":
            continue
        out.append(" " if cat in ("Cc", "Zl", "Zp") else ch)
    return "".join(out)


def plain_title(text: str | None) -> str:
    """A name a person typed, as kept: no control or format characters,
    spacing flattened (empty when nothing is left)."""
    return " ".join(_plain_text(text or "").split())


def first_words_title(text: str | None) -> str | None:
    """The first :data:`TITLE_WORDS` words of ``text``, with "…" when
    there were more (or the words ran past :data:`TITLE_MAX_CHARS`); None
    for a message with no words (a picture alone, say)."""
    words = _plain_text(text or "").split()
    if not words:
        return None
    title = " ".join(words[:TITLE_WORDS])
    cut = len(words) > TITLE_WORDS
    if len(title) > TITLE_MAX_CHARS:
        title = title[:TITLE_MAX_CHARS].rstrip()
        cut = True
    return title + "…" if cut else title


def clean_model_title(raw: str | None) -> str | None:
    """What the model wrote, as a title: its first line, without a
    "Title:" lead, quotes, markdown marks or closing punctuation, cut to
    :data:`TITLE_WORDS` words. None when nothing is left, so the chat
    keeps the title it has."""
    for line in (raw or "").splitlines():
        line = _plain_text(line).strip()
        if not line:
            continue
        line = _LEAD.sub("", line.strip(_WRAP)).strip(_WRAP)
        line = line.rstrip(_TRAIL).strip(_WRAP)
        words = line.split()
        if not words:
            return None
        title = " ".join(words[:TITLE_WORDS])
        if len(title) > TITLE_MAX_CHARS:
            title = title[:TITLE_MAX_CHARS].rstrip()
        return title or None
    return None


def title_prompt(question: str, answer: str) -> str:
    """The user turn the title model is given: the chat's first exchange,
    each side cut to :data:`TITLE_EXCERPT_CHARS`."""
    q = (question or "").strip()[:TITLE_EXCERPT_CHARS]
    a = (answer or "").strip()[:TITLE_EXCERPT_CHARS]
    return f"Chat:\nPerson: {q}\nDomovoi: {a}\n\nTitle:"
