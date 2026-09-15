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
