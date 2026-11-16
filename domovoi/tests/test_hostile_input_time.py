"""Text a caller sends must not buy minutes of the server's time.

Three readers were found slow on crafted input by the CodeQL triage of
2026-11 (py/redos, py/polynomial-redos). Each runs in an event loop, so a
slow read there stalls every other request the process is serving:

* ``music_choice._ACCEPT_TAIL_RE``: the reply to "did you mean ...?" took
  exponential time on "yes sure thing sure thing ... x" ("sure thing" read
  both as one phrase and as "sure" + "thing"), and ~40 repetitions held the
  core for days. Device tier (``/v1/intent``).
* ``drafts.blocks.inline``: a draft line of "[" with no "]" was quadratic
  in its length (the link pattern rescanned the rest of the line from every
  "["); a 200,000-character draft edit held the web process for half a
  minute, again on every render of it. Device tier.
* ``documents._blank_html``: the editor's HTML of "<" with no ">" was
  quadratic the same way; the save allows 32 MB of HTML, so one save held
  the web process for hours. Device tier.

Each case is timed against a budget the old code missed by an order of
magnitude, and the new readers are held equal to the old patterns on
everything else (the old pattern is kept here, as the oracle).
"""

from __future__ import annotations

import random
import re
import time

from domovoi.drafts import blocks
from domovoi.handlers import music_choice
from domovoi.handlers.music_choice import parse_choice_reply

# Generous: the fixed readers take milliseconds; the old ones took seconds
# on these inputs (measured 2026-11-15: ~5 s, ~8 s and ~6 s).
BUDGET_SEC = 1.0


def _timed(fn, *args):
    t0 = time.perf_counter()
    out = fn(*args)
    return out, time.perf_counter() - t0


# ─── "did you mean ...?" ──────────────────────────────────────────────────

def test_a_long_run_of_yes_words_is_read_in_linear_time():
    reply = "yes " + "sure thing " * 24 + "x"
    _, took = _timed(parse_choice_reply, reply, 2)
    assert took < BUDGET_SEC, f"parse_choice_reply took {took:.2f}s on {len(reply)} characters"


# The tail pattern as it was, "sure thing" alternative and all: the
# language the new one must still accept, word for word.
_OLD_ACCEPT_TAIL_RE = re.compile(
    r"^(?:(?:please|thanks|thank you(?: very much| so much)?|cheers|go ahead|please do|do it"
    r"|that's it|that's the one|that one|exactly|correct|right|that's right|i did|i do"
    r"|yes|yeah|yep|yup|sure|sure thing|thing|of course|definitely|absolutely|ok|okay"
    r"|sir|ma'am|mate|buddy"
    r"|(?:play|put on) (?:it|that|that one|this|this one|them|those))"
    r"(?:[,.!]?\s+|$))+$"
)
_TAIL_WORDS = ["please", "thanks", "thank you", "thank you very much", "sure", "sure thing",
               "thing", "do it", "please do", "that's it", "ok", "okay", "play it", "put on that one",
               "yes", "of course", "sir", "very", "much", "it", "do", "x", "that"]
_SEPS = [" ", ", ", ". ", "! ", "  ", ",", ""]


def test_the_yes_tail_still_accepts_exactly_what_it_did():
    rng = random.Random(20261115)
    for _ in range(4000):
        words = [rng.choice(_TAIL_WORDS) for _ in range(rng.randint(1, 6))]
        text = "".join(w + rng.choice(_SEPS) for w in words).rstrip(rng.choice(["", " "]))
        assert bool(music_choice._ACCEPT_TAIL_RE.match(text)) == bool(_OLD_ACCEPT_TAIL_RE.match(text)), text


# ─── a draft's markdown ───────────────────────────────────────────────────

def test_a_draft_line_of_open_brackets_is_read_in_linear_time():
    line = "[" + "[\\" * 50_000
    _, took = _timed(blocks.parse, line)
    assert took < BUDGET_SEC, f"blocks.parse took {took:.2f}s on {len(line)} characters"


_OLD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def test_links_are_unwrapped_exactly_as_the_pattern_did():
    rng = random.Random(1115)
    alphabet = "[]()ab \\\n"
    for _ in range(20000):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        assert blocks.strip_links(s) == _OLD_LINK.sub(r"\1", s), repr(s)
    for s in ["[a](b)", "[a [b](c) d](e)", "x [a]  (b)", "[a](b", "[a]", "[](", "[[a](b)](c)",
              "see [the site](https://example.org/x_(y)) now", "[a](b)[c](d)"]:
        assert blocks.strip_links(s) == _OLD_LINK.sub(r"\1", s), s


# ─── the document editor's "is this empty?" ───────────────────────────────

def test_an_editor_body_of_open_angle_brackets_is_read_in_linear_time():
    from web.backend.api import documents as documents_api

    html = "<" * 150_000
    blank, took = _timed(documents_api._blank_html, html)
    assert took < BUDGET_SEC, f"_blank_html took {took:.2f}s on {len(html)} characters"
    assert blank is False


_OLD_TAGS = re.compile(r"<[^>]*>")


def test_tags_are_dropped_exactly_as_the_pattern_did():
    from web.backend.api import documents as documents_api

    rng = random.Random(6369)
    alphabet = "<>/ab \n&"
    for _ in range(20000):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))
        assert documents_api._strip_tags(s) == _OLD_TAGS.sub(" ", s), repr(s)
