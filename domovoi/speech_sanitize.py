"""Strip non-speakable content (emoji + Markdown syntax) from text before TTS.

Symptom this fixes: the LLM-driven paths (the Q&A fallthrough, Letta chat mode,
web-search answers) emit Markdown and emoji that the neural TTS engines
verbalize *literally* — "asterisk asterisk important asterisk asterisk" for
``**important**``, "thumbs up sign" for 👍. Users have heard Domovoi read
asterisks and describe emoji out loud a handful of times.

Every spoken path funnels through ``TTSClient.synthesize`` (streaming turns,
intercom/broadcast announcements, chat mode, drop-in, timer/reminder fire,
canned greeting rendering, ``/v1/intent`` audio). Running the scrub *there* is
the single choke point — see ``domovoi/clients/tts.py``. It touches only
the audio: the ``response_start`` text frame, ``conversation_log``, and the web
dashboard keep the original formatted text.

Design stance — conservative on purpose. We remove emoji/pictographs and
Markdown *markers*, but never characters a TTS engine already reads sensibly:
``°`` (degrees), ``™`` ``©`` ``®``, ``×`` ``÷``, currency signs, ``C#``, ``#1``
all survive. Asterisks and backticks are removed unconditionally (they are
never meaningfully spoken and asterisks are the reported pain point);
underscores are only unwrapped when they clearly delimit emphasis, so
``snake_case`` survives. Idempotent and cheap — safe to run per sentence.
"""

from __future__ import annotations

import re

# ── Emoji / pictographs ───────────────────────────────────────────────────
# Curated ranges rather than a Unicode-category sweep: category "So" (Symbol,
# other) would also eat ° ™ © ® and the like, which the engines pronounce
# correctly. These blocks cover the emoji/pictograph/dingbat space plus the
# ZWJ / variation-selector / keycap combining marks that glue emoji sequences.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # CJK tiles, emoticons, pictographs, transport,
    #                          supplemental & extended-A/B symbols, flags
    "\U00002190-\U000021FF"  # Arrows (← → ↔ ↩ …)
    "\U00002300-\U000023FF"  # Misc Technical (⌚ ⌛ ⏰ ⏳ ⏸ ⏯ ⏭ …)
    "\U00002600-\U000026FF"  # Miscellaneous Symbols (☀ ☂ ☎ ☑ ♻ ⚠ …)
    "\U00002700-\U000027BF"  # Dingbats (✂ ✅ ✈ ✉ ✨ ❤ ➡ …)
    "\U00002B00-\U00002BFF"  # Misc Symbols & Arrows (⭐ ⬆ ⬇ ⬅ …)
    "\U0000FE00-\U0000FE0F"  # Variation selectors (emoji presentation)
    "\U0000200D"             # Zero-width joiner (emoji ZWJ sequences)
    "\U000020E3"             # Combining enclosing keycap (digit-keycap emoji)
    "]",
    flags=re.UNICODE,
)

# Stray bullet / list glyphs that show up mid-text (not just line-leading).
_BULLET_GLYPHS_RE = re.compile(r"[•◦▪▫●○‣⁃]")

# Markdown image / link → keep the visible label, drop the target.
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")

# Emphasis wrappers that properly enclose non-space text.
#   double/tilde: ** __ ~~  ·  single: * _
# The single-marker form is boundary-guarded so intra-word underscores
# (snake_case) and a spaced "5 * 3" are left untouched.
_EMPH_DOUBLE_RE = re.compile(r"(\*\*|__|~~)(?=\S)(.+?)(?<=\S)\1")
_EMPH_SINGLE_RE = re.compile(
    r"(?<![A-Za-z0-9])([*_])(?=\S)(.+?)(?<=\S)\1(?![A-Za-z0-9])"
)

# Line-leading structure markers (heading #, blockquote >, list bullets).
_HEADING_RE = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+")
_BLOCKQUOTE_RE = re.compile(r"(?m)^[ \t]*>[ \t]?")
_LIST_BULLET_RE = re.compile(r"(?m)^[ \t]*[-+•‣⁃][ \t]+")

_WS_RE = re.compile(r"\s+")


def sanitize_for_speech(text: str) -> str:
    """Return ``text`` with emoji and Markdown syntax removed for TTS.

    Empty in → empty out. A string that is *entirely* emoji/markup collapses
    to ``""`` (the engine then renders nothing rather than narrating symbol
    names) — an acceptable, quiet outcome for the rare all-emoji reply.
    """
    if not text:
        return text or ""

    s = _EMOJI_RE.sub("", text)

    # Images before links (![alt](url) is a superset of [text](url)).
    s = _IMAGE_RE.sub(r"\1", s)
    s = _LINK_RE.sub(r"\1", s)

    # Unwrap emphasis, twice so nesting like **_x_** fully unwinds.
    for _ in range(2):
        s = _EMPH_DOUBLE_RE.sub(r"\2", s)
        s = _EMPH_SINGLE_RE.sub(r"\2", s)

    # Drop line-leading structure markers.
    s = _HEADING_RE.sub("", s)
    s = _BLOCKQUOTE_RE.sub("", s)
    s = _LIST_BULLET_RE.sub("", s)

    # Blanket-remove characters that are never meaningfully spoken. Asterisk is
    # the reported offender; backtick (inline code / fences) is the other.
    s = s.replace("*", "").replace("`", "")

    # Any surviving bullet glyphs anywhere in the line.
    s = _BULLET_GLYPHS_RE.sub("", s)

    # Collapse the whitespace the removals leave behind (incl. newlines →
    # a single space, which the engines render as a natural pause).
    s = _WS_RE.sub(" ", s).strip()
    return s
