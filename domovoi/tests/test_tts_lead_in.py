"""A voice that runs away on a short sentence said alone (Norman, the
shipped default: "Cameron." came out as five seconds of babble, the same
ids after a sentence of context as the word) says its short sentences after
a lead-in and is cut off from it. DB-free; the ONNX session is a stand-in.
The Kotlin engine's LeadInTest checks the same cut on the same numbers."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from domovoi.clients import tts as tts_mod
from domovoi.phonemizer import default_id_map, get_phonemizer, phoneme_ids

SR = 22050


def _config() -> dict:
    return {
        "audio": {"sample_rate": SR},
        "espeak": {"voice": "en"},
        "phoneme_type": "espeak",
        "inference": {"noise_scale": 0.667, "length_scale": 1.0, "noise_w": 0.8},
        "num_speakers": 1,
        "phoneme_id_map": default_id_map(),
    }


def _tone(seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (0.4 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


class _RunawaySession:
    """A voice that runs away: a short input alone is 3 s of babble. The
    lead-in alone is 1 s; the lead-in then a sentence is 0.9 s, a 0.4 s
    pause, then 0.3 s (the sentence). Anything else: 1 s."""

    def __init__(self, lead_ids: list[int]):
        self.lead_ids = lead_ids
        self.calls: list[list[int]] = []

    def run(self, _outputs, feeds):
        ids = feeds["input"].tolist()[0]
        self.calls.append(ids)
        body = self.lead_ids[:-1]          # the lead-in's ids before "$"
        if ids == self.lead_ids:
            a = _tone(1.0)
        elif ids[:len(body)] == body:
            a = np.concatenate([_tone(0.9), np.zeros(int(0.4 * SR), np.float32), _tone(0.3)])
        elif len(ids) < 60:
            a = np.random.default_rng(0).uniform(-0.5, 0.5, 3 * SR).astype(np.float32)
        else:
            a = _tone(1.0)
        return [a.reshape(1, 1, 1, -1)]


@pytest.fixture
def lead():
    return " ".join(get_phonemizer().phonemize(tts_mod.LEAD_IN_TEXT))


@pytest.fixture
def runaway(monkeypatch, tmp_path, lead):
    import onnxruntime as ort

    session = _RunawaySession(phoneme_ids(lead))
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **k: session)
    onnx = tmp_path / "en_US-norman-medium.onnx"
    onnx.write_bytes(b"model")
    Path(f"{onnx}.json").write_text(json.dumps(_config()), encoding="utf-8")
    v = tts_mod.PiperVoice(onnx)
    v.test_session = session
    return v


def test_a_voice_that_runs_away_is_found_once(runaway, lead):
    assert runaway.runs_away() is True
    probes = [phoneme_ids(" ".join(get_phonemizer().phonemize(w))) for w in tts_mod.RUNAWAY_PROBES]
    # each probe word alone, then the lead-in alone (its length), once
    assert runaway.test_session.calls == [*probes, phoneme_ids(lead)]
    assert runaway.runs_away() is True and len(runaway.test_session.calls) == 3


def test_its_short_sentence_is_said_after_the_lead_in_and_cut_off(runaway, lead):
    audio = runaway.sentence_audio("kˈaməɹən.", 1.0)
    assert runaway.test_session.calls[-1] == phoneme_ids(f"{lead} kˈaməɹən.")
    # the sentence (0.3 s) and 0.1 s of the pause before it, not the 3 s of babble
    assert 0.38 < audio.size / SR < 0.42
    assert np.abs(audio[: int(0.09 * SR)]).max() < 1e-3 and np.abs(audio).max() == pytest.approx(1.0)


def test_a_long_sentence_is_said_alone(runaway):
    long = "ðə kˈɪtʃən lˈaɪt ɪz ˈɒn, and ðə mjˈuːzɪk ɪz pˈɔːzd ɪn ðə lˈɪvɪŋ ɹˈuːm."
    assert tts_mod.phoneme_count(long) >= tts_mod.LEAD_IN_MAX_PHONEMES
    audio = runaway.sentence_audio(long, 1.0)
    assert runaway.test_session.calls == [phoneme_ids(long)]     # no check, no lead-in
    assert audio.size == SR


def test_at_another_speed_the_search_scales_too(runaway, lead):
    runaway.runs_away()
    runaway.test_session.calls.clear()
    audio = runaway.sentence_audio("dˈʌn.", 1.25)
    assert runaway.test_session.calls == [phoneme_ids(f"{lead} dˈʌn.")]
    assert 0.38 < audio.size / SR < 0.42


def test_no_pause_found_twice_says_the_sentence_alone(runaway, lead, monkeypatch, caplog):
    runaway.runs_away()
    runaway.test_session.calls.clear()
    monkeypatch.setattr(tts_mod, "cut_after_lead_in", lambda *a: None)
    audio = runaway.sentence_audio("dˈʌn.", 1.0)
    assert runaway.test_session.calls == [phoneme_ids(f"{lead} dˈʌn.")] * tts_mod.LEAD_IN_TRIES + [phoneme_ids("dˈʌn.")]
    assert audio.size == 3 * SR
    assert any("no pause after the lead-in" in r.getMessage() for r in caplog.records)


def test_phoneme_count():
    assert tts_mod.phoneme_count("kˈaməɹən.") == 7
    assert tts_mod.phoneme_count("hˈaɪ, aɪm nˈɔːmən.") == 11
    assert tts_mod.phoneme_count("") == 0


# The cut, on the numbers LeadInTest.kt uses too: 1,000 Hz, the lead-in alone
# 2.5 s (the search starts at 1.5 s), 2 s of sound, a 0.3 s pause, 0.5 s of
# sound: kept from 2.2 s (0.1 s before the pause ends), 600 samples.
def _shape(*parts):
    return np.concatenate([np.full(int(round(s * 1000)), v, np.float32) for s, v in parts])


def test_the_cut_keeps_what_follows_the_lead_ins_pause():
    out = tts_mod.cut_after_lead_in(_shape((2.0, 0.5), (0.3, 0.0), (0.5, 0.5)), 1000, 2.5)
    assert out is not None and out.size == 600
    assert out[0] == 0.0 and out[200] == 0.5


def test_the_cut_ignores_a_pause_inside_the_lead_in_and_trailing_silence():
    audio = _shape((0.5, 0.5), (0.4, 0.0), (1.1, 0.5), (0.3, 0.0), (0.5, 0.5), (0.5, 0.0))
    out = tts_mod.cut_after_lead_in(audio, 1000, 2.5)
    assert out.size == 1100          # from 2.2 s: 0.1 s of pause, 0.5 s of sound, the tail
    assert tts_mod.cut_after_lead_in(_shape((2.0, 0.5), (0.5, 0.0)), 1000, 2.5) is None


def test_a_pause_shorter_than_the_lead_ins_end_is_not_it():
    audio = _shape((2.0, 0.5), (0.1, 0.0), (0.5, 0.5))
    assert tts_mod.cut_after_lead_in(audio, 1000, 2.5) is None
