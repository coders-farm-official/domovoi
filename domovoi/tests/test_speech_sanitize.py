"""Speech sanitizer — emoji + Markdown are stripped before TTS, while
legitimate spoken characters (°, ™, ×, C#, contractions) survive.

Guards the reported bug: Domovoi reading "asterisk" for Markdown bold and
narrating emoji out loud.
"""

from __future__ import annotations

import pytest

from domovoi.speech_sanitize import sanitize_for_speech


# ─── Markdown emphasis / the reported asterisk bug ────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("This is **really** important.", "This is really important."),
        ("This is *really* important.", "This is really important."),
        ("This is __really__ important.", "This is really important."),
        ("This is _really_ important.", "This is really important."),
        # Strikethrough unwraps like bold/italic — markers gone, word kept.
        ("This is ~~wrong~~ right.", "This is wrong right."),
        ("Nested **_very_** bold.", "Nested very bold."),
        # A stray/unmatched asterisk is still removed — the guarantee.
        ("Wait * what", "Wait what"),
        ("**Bold start only", "Bold start only"),
    ],
)
def test_markdown_emphasis_removed(raw: str, expected: str) -> None:
    assert sanitize_for_speech(raw) == expected


def test_no_asterisk_ever_survives() -> None:
    for raw in ("a * b", "**x**", "* bullet", "3 * 4 = 12", "list:\n* one\n* two"):
        assert "*" not in sanitize_for_speech(raw)


def test_backticks_and_code_removed() -> None:
    assert sanitize_for_speech("Run `mpd update` now.") == "Run mpd update now."
    assert "`" not in sanitize_for_speech("```python\nprint(1)\n```")


def test_links_keep_label_drop_url() -> None:
    assert sanitize_for_speech("See [the docs](https://x.y/z) here.") == "See the docs here."
    assert sanitize_for_speech("![a cat](http://img/c.png) meow") == "a cat meow"


def test_line_leading_structure_stripped() -> None:
    assert sanitize_for_speech("# Heading\nBody") == "Heading Body"
    assert sanitize_for_speech("> quoted line") == "quoted line"
    assert sanitize_for_speech("- first\n- second") == "first second"
    assert sanitize_for_speech("• bullet one\n• bullet two") == "bullet one bullet two"


# ─── Emoji ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Sounds good 👍", "Sounds good"),
        ("Great job! 🎉🎉", "Great job!"),
        ("Weather ☀️ is nice", "Weather is nice"),
        ("Love it ❤️", "Love it"),
        ("Family 👨‍👩‍👧‍👦 time", "Family time"),  # ZWJ sequence
        ("Flag 🇺🇸 day", "Flag day"),
        ("Star ⭐ rating", "Star rating"),
        ("Arrow → there", "Arrow there"),
    ],
)
def test_emoji_removed(raw: str, expected: str) -> None:
    assert sanitize_for_speech(raw) == expected


# ─── What must NOT be touched (conservative stance) ───────────────────────


def test_legit_symbols_preserved() -> None:
    # Characters a TTS engine already reads correctly stay put.
    assert sanitize_for_speech("It's 72° outside.") == "It's 72° outside."
    assert sanitize_for_speech("Python™ rocks") == "Python™ rocks"
    assert sanitize_for_speech("© 2026 Acme") == "© 2026 Acme"
    assert sanitize_for_speech("I love C# and F#") == "I love C# and F#"
    assert sanitize_for_speech("That's #1 by far") == "That's #1 by far"
    assert sanitize_for_speech("5 × 3 ÷ 2") == "5 × 3 ÷ 2"


def test_intra_word_underscore_preserved() -> None:
    # snake_case identifiers keep their underscores (not emphasis).
    assert sanitize_for_speech("call get_or_create() first") == "call get_or_create() first"


def test_contractions_and_apostrophes_untouched() -> None:
    assert sanitize_for_speech("Don't worry, it's fine.") == "Don't worry, it's fine."


# ─── Edge cases ───────────────────────────────────────────────────────────


def test_empty_and_whitespace() -> None:
    assert sanitize_for_speech("") == ""
    assert sanitize_for_speech(None) == ""  # type: ignore[arg-type]
    assert sanitize_for_speech("   \n\t ") == ""


def test_all_emoji_collapses_to_empty() -> None:
    assert sanitize_for_speech("👍🎉❤️") == ""


def test_whitespace_collapsed() -> None:
    assert sanitize_for_speech("a\n\n\nb    c") == "a b c"


def test_idempotent() -> None:
    raw = "**Hi** there 👋, see [docs](http://x) — `code`."
    once = sanitize_for_speech(raw)
    assert sanitize_for_speech(once) == once


def test_plain_text_unchanged() -> None:
    plain = "It's 3:47 PM. Your timer has 5 minutes left."
    assert sanitize_for_speech(plain) == plain
