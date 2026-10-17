"""A satellite says its approval code one clip per digit; each clip is a
sentence of its own.

Found on the owner's first card from the Windows install (2026-10-17):
Kristin, the default voice, rendered the bare words "seven", "nine",
"four" ... as a held first sound ("sss", "nnn", "ffff") or a stretched word
(a lone "seven" 1.7 to 2.9 s against 0.7 s for "seven."; "three" heard as
"B"). Norman, the default before 2026-10-12, said short sentences after a
lead-in, which hid it. Two guards: the setup lines end in a full stop
(which also re-renders a card's bare clips), and the Piper engine adds one
to any short sentence that has no end mark. DB-free; the ONNX session is a
stand-in that records what it is asked to say.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from domovoi import canned_sounds as cs
from domovoi import egress
from domovoi.clients import tts as tts_mod
from domovoi.config import settings
from domovoi.phonemizer import default_id_map, get_phonemizer, phoneme_ids
from domovoi.tests.test_canned_sounds_engine_marker import (  # noqa: F401 - fixtures
    PIPER_DEFAULT,
    engines,
    probe,
    sounds,
)

SR = 22050


# ─── The engine: a short sentence with no end mark gets a full stop ───────


@pytest.mark.parametrize("bare", ["sˈɛvən", "nˈaɪn", "fˈɔː", "θɹˈiː", "jɔː sˈɛtʌp kˈəʊd ˈɪz", "sˈɛvən  "])
def test_a_short_sentence_with_no_end_mark_gets_a_full_stop(bare):
    assert tts_mod.end_short_sentence(bare) == bare.rstrip() + "."


@pytest.mark.parametrize("ended", ["sˈɛvən.", "jˈɛs?", "hˈeɪ!", "wˈɛl,", "ðɪs;", "ðə kˈəʊd ˈɪz:", 'sˈɛd "hˈaɪ."',
                                   "(sˈɛvən.)"])
def test_a_sentence_that_has_an_end_mark_is_left_alone(ended):
    assert tts_mod.end_short_sentence(ended) == ended


def test_a_closing_quote_after_a_bare_word_still_gets_the_stop():
    assert tts_mod.end_short_sentence('"sˈɛvən"') == '"sˈɛvən".'


def test_a_long_sentence_with_no_end_mark_is_left_alone():
    """A reply chunk cut mid-sentence keeps its unfinished sound."""
    long = "ðə kˈɪtʃən lˈaɪt ɪz ˈɒn and ðə mjˈuːzɪk ɪz pˈɔːzd ɪn ðə lˈɪvɪŋ ɹˈuːm"
    assert tts_mod.phoneme_count(long) >= tts_mod.LEAD_IN_MAX_PHONEMES
    assert tts_mod.end_short_sentence(long) == long


@pytest.mark.parametrize("nothing", ["", "   ", '"', "."])
def test_nothing_to_say_is_left_alone(nothing):
    assert tts_mod.end_short_sentence(nothing) == nothing


class _Recording:
    """An ONNX session that says 0.5 s of tone for anything (a voice that
    does not run away) and records the ids it was given."""

    def __init__(self) -> None:
        self.calls: list[list[int]] = []

    def run(self, _outputs, feeds):
        self.calls.append(feeds["input"].tolist()[0])
        t = np.arange(int(0.5 * SR)) / SR
        return [(0.4 * np.sin(2 * np.pi * 220 * t)).astype(np.float32).reshape(1, 1, 1, -1)]


@pytest.fixture
def said():
    """What the converter writes for the texts below, worked out before the
    stand-in session replaces ONNX Runtime (the converter uses it too)."""
    ph = get_phonemizer()
    return {t: ph.phonemize(t, dialect="en") for t in ("seven", "Is it seven?")}


@pytest.fixture
def voice(monkeypatch, tmp_path, said):
    import onnxruntime as ort

    session = _Recording()
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: session)
    onnx = tmp_path / "en_US-kristin-medium.onnx"
    onnx.write_bytes(b"model")
    Path(f"{onnx}.json").write_text(json.dumps({
        "audio": {"sample_rate": SR}, "espeak": {"voice": "en"}, "phoneme_type": "espeak",
        "inference": {"noise_scale": 0.667, "length_scale": 1.0, "noise_w": 0.8},
        "num_speakers": 1, "phoneme_id_map": default_id_map(),
    }), encoding="utf-8")
    v = tts_mod.PiperVoice(onnx)
    v.test_session = session
    return v


def test_the_voice_is_given_the_bare_word_with_a_full_stop(voice, said):
    """What the card's "seven" clip was rendered from, and what it is now."""
    sentences = said["seven"]
    assert sentences == ["sˈɛvən"]                       # the converter writes no end mark
    voice.synthesize_pcm(sentences, 1.0)
    assert voice.test_session.calls[-1] == phoneme_ids("sˈɛvən.")


def test_a_punctuated_sentence_reaches_the_voice_unchanged(voice, said):
    sentences = said["Is it seven?"]
    voice.synthesize_pcm(sentences, 1.0)
    assert voice.test_session.calls[-1] == phoneme_ids(sentences[0])


# ─── The setup lines: every clip is a sentence ──────────────────────────


def test_every_setup_line_ends_in_an_end_mark():
    for name, text in cs.SETUP_LINES:
        assert text.rstrip()[-1] in tts_mod.SENTENCE_END_MARKS, name


def test_the_digit_clips_say_the_digit_words_in_order():
    digits = {name: text for name, text in cs.SETUP_LINES if name.startswith("digit_")}
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    assert digits == {f"digit_{d}.wav": f"{w.capitalize()}." for d, w in enumerate(words)}


async def test_a_card_whose_clips_were_rendered_bare_gets_them_again(engines, sounds, probe, monkeypatch):
    """The household's server still holds the bare clips an earlier build
    wrote (same voice, so the marker agrees); the next card prep must not
    copy them onto another card."""
    monkeypatch.setattr(settings, "tts_piper_voice", PIPER_DEFAULT)
    setup = sounds / "setup"
    setup.mkdir(parents=True)
    old = [("your_code_is.wav", "Your setup code is"),
           *[(f"digit_{d}.wav", w) for d, w in enumerate(
               ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"))]]
    for name, text in old:
        (setup / name).write_bytes(b"RIFF-old")
        (setup / f"{name[:-4]}.voice").write_text(
            f"piper|{PIPER_DEFAULT}\n{cs._hash(cs._spoken_key(text, 'piper'))}\n", encoding="utf-8")
    with egress.override_policy("always"):
        await cs.render_setup_clips()
    for name, _ in old:
        assert (setup / name).read_bytes() != b"RIFF-old", name
