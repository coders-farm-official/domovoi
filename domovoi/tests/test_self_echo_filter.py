"""Self-echo detection — the satellite hearing its own reply.

Worked from the real 2026-09-10 office log (turns #54–#57), where the
satellite barged in on its own TTS and then answered itself.

Pure units, no DB, no `requires_db` — a diagnostic that skips silently on a
box without Postgres is a diagnostic that ships broken.
"""

from __future__ import annotations

import pytest

from domovoi.self_echo_filter import is_self_echo, strip_leading_echo


# The actual strings, from `conversation_log` rows 54–57.
SPOKE_STOPPED = "Stopped."
HEARD_STOPPED = "stopped."
SPOKE_AMBER = (
    'The name Amber is derived from the Latin word "ambrosia", which refers '
    "to a food that grants immortality in Greek mythology. It is also "
    "associated with the color amber, a type of fossilized tree resin."
)
HEARD_AMBER = (
    "The name Amber is associated with the color A. That was super slow. Is "
    "this custom hardware, or is it just like if you put together words?"
)


# ─── is_self_echo ─────────────────────────────────────────────────────────


def test_the_stopped_stopped_case() -> None:
    """Turn #57: the whole transcript was the reply to turn #56."""
    assert is_self_echo(HEARD_STOPPED, SPOKE_STOPPED) is True


def test_echo_ignores_case_and_punctuation() -> None:
    assert is_self_echo("PLAYING JAZZ", "Playing jazz.") is True
    assert is_self_echo("it's 6:57 PM", "It's 6:57 PM.") is True


def test_a_partial_quote_of_a_long_reply_is_not_a_whole_echo() -> None:
    """Echo of a few words inside a long real command must not drop it."""
    heard = "It is also associated with the color amber. Play some jazz please"
    assert is_self_echo(heard, SPOKE_AMBER) is False


def test_a_real_command_is_never_an_echo() -> None:
    assert is_self_echo("play some jazz", SPOKE_AMBER) is False
    assert is_self_echo("what time is it", SPOKE_STOPPED) is False
    assert is_self_echo("turn the volume up", "Volume up to 80 percent.") is False


def test_empty_inputs_are_never_an_echo() -> None:
    """A caller with nothing to compare against must not discard turns."""
    assert is_self_echo("", SPOKE_STOPPED) is False
    assert is_self_echo(HEARD_STOPPED, "") is False
    assert is_self_echo("", "") is False
    assert is_self_echo("   ", "  ...  ") is False


def test_scattered_common_words_are_not_an_echo() -> None:
    """Requires a CONTIGUOUS run — otherwise any transcript made of common
    words looks like an echo of any sufficiently long reply."""
    spoken = "It is also associated with the color amber and it is a resin"
    assert is_self_echo("is it a color", spoken) is False


def test_the_garbled_paraphrase_case_is_a_known_miss() -> None:
    """Turn #55 — documents the limit stated in the module docstring.

    Barge-in cut playback mid-word, so Whisper rendered the echo as a
    paraphrase ("...the color amber" → "the color A") rather than the actual
    words. It doesn't match contiguously, so this survives the filter. The
    real fix for this one is config: don't let the barge fire on echo.
    """
    assert is_self_echo(HEARD_AMBER, SPOKE_AMBER) is False


# ─── strip_leading_echo ───────────────────────────────────────────────────


def test_strips_a_leading_echo_followed_by_a_real_command() -> None:
    heard = "It is also associated with the color amber. Play some jazz"
    assert strip_leading_echo(heard, SPOKE_AMBER) == "Play some jazz"


def test_requires_a_clause_boundary_after_the_echo() -> None:
    """No break means the words ran straight on — a command that happens to
    open with the same words, not a bleed."""
    heard = "It is also associated with jazz music"
    assert strip_leading_echo(heard, SPOKE_AMBER) == heard


def test_short_matches_are_left_alone() -> None:
    """Two words of overlap is coincidence, not evidence."""
    heard = "It is. Play some jazz"
    assert strip_leading_echo(heard, SPOKE_AMBER) == heard


def test_a_whole_echo_is_left_for_is_self_echo_to_judge() -> None:
    assert strip_leading_echo(HEARD_STOPPED, SPOKE_STOPPED) == HEARD_STOPPED


def test_preserves_original_casing_and_punctuation_of_the_remainder() -> None:
    heard = "It is also associated with the color amber. Play McCoy Tyner's \"Passion Dance\"!"
    out = strip_leading_echo(heard, SPOKE_AMBER)
    assert out == "Play McCoy Tyner's \"Passion Dance\"!"


def test_empty_inputs_pass_through_unchanged() -> None:
    assert strip_leading_echo("", SPOKE_AMBER) == ""
    assert strip_leading_echo(HEARD_STOPPED, "") == HEARD_STOPPED


def test_never_returns_empty_for_a_nonempty_transcript() -> None:
    """Routing an empty transcript would make the bot answer nothing at all;
    leaving it intact at least surfaces the problem."""
    for heard in [HEARD_STOPPED, "It is also associated.", "Play some jazz"]:
        assert strip_leading_echo(heard, SPOKE_AMBER).strip() != ""


@pytest.mark.parametrize("ratio", [0.5, 0.8, 1.0])
def test_ratio_is_tunable(ratio: float) -> None:
    assert is_self_echo(HEARD_STOPPED, SPOKE_STOPPED, min_ratio=ratio) is True
