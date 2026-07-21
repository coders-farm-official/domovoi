"""strip_leading_greeting — remove a satellite's bled-in wake greeting.

A bled-in greeting is its own clause (clip plays → gap → user speech, which
Whisper punctuates), so the filter only strips a leading greeting that's
followed by a sentence/clause boundary. That's what keeps it from mangling a
real command that merely starts with greeting-like words.
"""

from __future__ import annotations

from domovoi.greeting_filter import strip_leading_greeting

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
