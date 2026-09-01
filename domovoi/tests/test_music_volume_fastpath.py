"""Volume fast-path coverage.

Volume is said many times a day, and a miss costs the whole LLM routing
round-trip (seconds on a CPU host) instead of ~20 ms. The original patterns
accepted only three exact phrasings, so "turn the volume up" fell through
to the router. These lock in the natural variants — and, just as
importantly, that nothing adjacent gets swallowed.
"""

from __future__ import annotations

import pytest

from domovoi.handlers.music import _VOLUME_DOWN_RE, _VOLUME_UP_RE


@pytest.mark.parametrize("phrase", [
    "volume up", "louder", "turn it up",
    "turn the volume up", "turn up the volume",
    "turn the music up", "turn up the music",
    "turn the sound up", "turn up the sound",
])
def test_volume_up_phrasings_hit_the_fast_path(phrase):
    assert _VOLUME_UP_RE.match(phrase), f"{phrase!r} should take the fast path"


@pytest.mark.parametrize("phrase", [
    "volume down", "quieter", "softer", "turn it down",
    "turn the volume down", "turn down the volume",
    "turn the music down", "turn down the music",
    "turn the sound down", "turn down the sound",
])
def test_volume_down_phrasings_hit_the_fast_path(phrase):
    assert _VOLUME_DOWN_RE.match(phrase), f"{phrase!r} should take the fast path"


@pytest.mark.parametrize("phrase", [
    # Home control, not volume — must reach the real router.
    "turn on the lights", "turn the lights up", "turn off the lights",
    # Music requests.
    "play some jazz", "play the beatles in the kitchen",
    # Fragments that must not be treated as commands.
    "volume", "up", "down", "turn up", "turn",
    "what time is it",
])
def test_unrelated_phrases_do_not_match(phrase):
    assert not _VOLUME_UP_RE.match(phrase)
    assert not _VOLUME_DOWN_RE.match(phrase)


def test_patterns_stay_anchored():
    """The router lowercases and strips trailing punctuation before matching,
    but does not trim surrounding words — an unanchored pattern would fire
    inside a longer sentence."""
    for phrase in ("please turn the volume up now", "i said turn it up because"):
        assert not _VOLUME_UP_RE.match(phrase)
