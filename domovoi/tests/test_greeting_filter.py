"""strip_leading_greeting — remove a satellite's bled-in wake greeting.

A bled-in greeting is its own clause (clip plays → gap → user speech, which
Whisper punctuates), so the filter only strips a leading greeting that's
followed by a sentence/clause boundary. That's what keeps it from mangling a
real command that merely starts with greeting-like words.
"""

from __future__ import annotations

import pytest

from domovoi.greeting_filter import is_greeting_only, strip_leading_greeting

_BANK = ["Hey!", "Hi there.", "What's up?", "Go ahead.", "Domovoi here."]


def test_strips_leading_greeting_with_boundary():
    assert strip_leading_greeting("Hi there. Say something mean.", _BANK) == "Say something mean."
    assert strip_leading_greeting("Hey! play some jazz", _BANK) == "play some jazz"
    assert strip_leading_greeting("What's up, what time is it?", _BANK) == "what time is it?"


def test_case_and_punctuation_insensitive():
    # Whisper may render the greeting differently than the canonical text,
    # but the trailing boundary is what authorizes the strip.
    assert strip_leading_greeting("hi there. set a timer", _BANK) == "set a timer"


def test_longest_greeting_wins():
    bank = ["Hi", "Hi there"]
    assert strip_leading_greeting("Hi there. play music", bank) == "play music"


# ─── The false-positive cases the boundary rule guards against ──────────────

def test_command_starting_with_greeting_words_is_untouched():
    # "What's up with dogs?" runs straight on — no break after "up" — so it's
    # a real question, not a bleed. Must NOT become "with dogs".
    assert strip_leading_greeting("What's up with dogs?", _BANK) == "What's up with dogs?"


def test_greeting_words_mid_sentence_are_untouched():
    # The greeting isn't even leading here.
    s = "I need to know what's up with dogs"
    assert strip_leading_greeting(s, _BANK) == s


def test_no_strip_without_boundary():
    # Same words, no punctuation after the greeting → can't distinguish from a
    # real command, so leave it alone.
    assert strip_leading_greeting("hi there set a timer", _BANK) == "hi there set a timer"


def test_no_match_returns_unchanged():
    assert strip_leading_greeting("what time is it", _BANK) == "what time is it"


def test_greeting_only_is_preserved():
    assert strip_leading_greeting("Hey!", _BANK) == "Hey!"
    assert strip_leading_greeting("hi there", _BANK) == "hi there"


def test_empty_and_blankish():
    assert strip_leading_greeting("", _BANK) == ""
    assert strip_leading_greeting("   ", _BANK) == "   "


# ─── Fuzzy matching: the greeting reaches Whisper as faint residual echo and
#     a small model snaps it to the nearest real words ─────────────────────────

_FUZZY_BANK = _BANK + ["Beep boop. How can I help?", "Shoot.", "Go on.", "I'm ready."]


def test_mistranscribed_greeting_is_stripped():
    # The real case from the office satellite on small.en/cpu/int8.
    assert (
        strip_leading_greeting("Big boob, how can I help? What time is it?", _FUZZY_BANK)
        == "What time is it?"
    )


def test_fuzzy_absorbs_a_merged_word():
    # Whisper fused the two nonsense syllables into one token, so the
    # greeting spans one fewer transcript token than the bank phrase.
    assert (
        strip_leading_greeting("Bee-boop, how can I help? play jazz", _FUZZY_BANK)
        == "play jazz"
    )


def test_fuzzy_prefers_exact_and_longer():
    bank = ["Hi there.", "Hi there, friend."]
    assert strip_leading_greeting("Hi there, friend. play jazz", bank) == "play jazz"
    assert strip_leading_greeting("Hi there. play jazz", bank) == "play jazz"


def test_fuzzy_still_requires_boundary():
    s = "big boob how can i help what time is it"
    assert strip_leading_greeting(s, _FUZZY_BANK) == s


def test_fuzzy_greeting_only_is_preserved():
    s = "Big boob, how can I help?"
    assert strip_leading_greeting(s, _FUZZY_BANK) == s


def test_dissimilar_sentence_sharing_filler_words_is_untouched():
    # Shares "how" and "help"-ish shape but is a different sentence.
    s = "Bob, how are you? what time is it"
    assert strip_leading_greeting(s, _FUZZY_BANK) == s


def test_short_greetings_never_match_fuzzily():
    # One-word / short greetings resemble too many real words; they must
    # match exactly or not at all.
    assert strip_leading_greeting("Shot, play jazz", _FUZZY_BANK) == "Shot, play jazz"
    assert strip_leading_greeting("Gone, play jazz", _FUZZY_BANK) == "Gone, play jazz"
    assert strip_leading_greeting("Shoot. play jazz", _FUZZY_BANK) == "play jazz"


def test_fuzzy_below_threshold_is_untouched():
    s = "Beep, how can I? play jazz"
    # Explicitly tightened threshold: this near-miss must not strip.
    assert strip_leading_greeting(s, _FUZZY_BANK, min_ratio=0.95) == s


def test_lookalike_command_keeps_its_verb():
    # "make it quiet" is ~0.85 similar to the greeting "Make it quick." by
    # characters, but shares only two whole words with it — a real command,
    # not a mangled bleed. Must not become "please."
    bank = _FUZZY_BANK + ["Make it quick."]
    s = "Make it quiet, please."
    assert strip_leading_greeting(s, bank) == s
    # Same guard, two-word greeting: no fuzzy path at all.
    assert strip_leading_greeting("I'm already, play jazz", _FUZZY_BANK) == "I'm already, play jazz"


# ─── is_greeting_only: the satellite hearing nothing but its own greeting ───
#
# Office row #187 (2026-09-28): the greeting "Back so soon?" bled past the
# AEC, the capture closed on the pause after it, and "Back so soon." was
# answered as a command. The bank below is the shipped V001 seed, read from
# the migration so a new greeting is covered the day it lands.


def _v001_bank() -> list[str]:
    import re
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1] / "db" / "migrations" / "V001__baseline.sql"
    ).read_text(encoding="utf-8")
    block = sql[sql.index("INSERT INTO client_greetings"):]
    block = block[: block.index(";\n") + 1]
    texts = re.findall(r"\('((?:[^']|'')*)',\s*'(?:generic|funny)'\)", block)
    return [t.replace("''", "'").replace("{name}", "Domovoi") for t in texts]


_V001 = _v001_bank()


def test_the_bank_fixture_is_the_real_seed():
    assert "Back so soon?" in _V001 and "I'm listening." in _V001
    assert len(_V001) > 40


@pytest.mark.parametrize(
    "transcript",
    [
        "Back so soon.",            # #187, verbatim
        "Back so soon?",
        "back so soon",
        "  BACK  so soon!  ",
        "Backsosoon.",              # words merged
        "Uh, back so soon?",        # hesitation around it
        "Back so soon? Um.",
        "I'm listening.",           # #169 ("Nice to meet you, Listening.")
        "I am listening.",          # contraction written out
        "I’m listening.",           # curly apostrophe
        "I‘m listening.",           # the other curly one
        "Howdy.",                   # #175 isn't in V001; see the bank test below
        "Hello.",                   # #168
        "Hey.",                     # #45
        "Big boob, how can I help?",   # "Beep boop. How can I help?" as Whisper heard it
        "Domovoi, reporting for duty.",
    ],
)
def test_greeting_only_transcripts_are_recognised(transcript):
    bank = _V001 + ["Howdy!"]
    assert is_greeting_only(transcript, bank)


@pytest.mark.parametrize(
    "transcript",
    [
        "Tell me a joke.",                          # #188, the user's real request
        "Back so soon? Tell me a joke.",            # greeting + command (strip's job)
        "What time is it?",
        "What's going on here?",                    # a greeting plus a real word
        "How's it going with the timer?",
        "Hello, what's the weather?",
        "Play some jazz.",
        "Make it quiet, please.",                   # look-alike of "Make it quick."
        "I'm already home.",
        "",
        "...",
    ],
)
def test_commands_are_not_greeting_only(transcript):
    assert not is_greeting_only(transcript, _V001)


def test_a_short_greeting_needs_the_played_clip_to_match_loosely():
    # Against the whole bank a one-word greeting must match exactly...
    assert not is_greeting_only("I'm listenin.", _V001)
    assert not is_greeting_only("Hallo.", _V001)
    # ...but when the satellite names the clip that played, a close
    # mistranscription of that one line is recognised.
    assert is_greeting_only("I'm listenin.", ["I'm listening."], played=True)
    assert is_greeting_only("Hallo.", ["Hello!"], played=True)
    assert not is_greeting_only("Hi.", ["Hey!"], played=True)
    assert not is_greeting_only("Yes.", ["Yeah?"], played=True)


def test_only_the_played_greeting_counts_when_it_is_known():
    # The caller passes just the clip that played; another bank line is a
    # person talking.
    assert not is_greeting_only("Hello.", ["Back so soon?"], played=True)
    assert is_greeting_only("Back so soon.", ["Back so soon?"], played=True)


def test_the_strip_still_preserves_a_greeting_only_turn():
    # Unchanged contract: the strip leaves it, the new check drops it.
    assert strip_leading_greeting("Back so soon.", _V001) == "Back so soon."
