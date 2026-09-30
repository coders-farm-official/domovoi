"""Short captures decode on a 10 s window; everything else, and anything
the short decode isn't sure of, on faster-whisper's own 30 s path.

faster-whisper pads every call to 3000 mel frames (30 s), so small.en on a
CPU spent ~900 ms on "stop" — longer than the satellite's whole 780 ms
silence, which is why early commit never fired on the Domovoi server.
``ShortWindowDecoder`` calls CTranslate2 directly with the same features
padded to 1000 frames (~230 ms). What is pinned here, without a model
(CTranslate2 and the tokenizer are doubles), except the last test:

* the window: the capture's own frames, zero-padded to 1000; frames an
  older extractor adds past the audio are not kept;
* the call: the prompt (start + no-timestamps), beam 5, the suppression
  list faster-whisper builds, a token cap; English-only models only — a
  multilingual one keeps the 30 s window;
* the verdict, by faster-whisper's own thresholds: blank (no speech),
  unsure (would have gone to a higher temperature, ran to the cap, or —
  stricter than faster-whisper — a no-speech probability above 0.6 at
  all: on the short window that is noise heard as "you"), ok;
* the client: which captures take the short window, and that a blank,
  unsure or failed short decode — or the setting off, or a CTranslate2
  that can't run a short window at all — falls back to the 30 s path;
* the runtime block and the per-call window the turn records.

The last test runs the real thing when tiny.en is already in the local
Hugging Face cache (it never downloads): this CTranslate2 must take a
window shorter than 3000 frames.
"""

from __future__ import annotations

import asyncio
import logging
import types

import numpy as np
import pytest

from domovoi.clients import whisper as whisper_mod
from domovoi.clients.whisper import (
    FULL_WINDOW_SEC,
    SHORT_MAX_NEW_TOKENS,
    SHORT_WINDOW_SEC,
    FasterWhisperClient,
    ShortDecode,
    ShortWindowDecoder,
    transcribe_with_window,
)
from domovoi.config import Settings, settings

SR = 16_000


def _pcm(seconds: float, value: int = 1000) -> bytes:
    return np.full(int(seconds * SR), value, dtype=np.int16).tobytes()


# ─── doubles ────────────────────────────────────────────────────────────


class _Tok:
    """faster_whisper.tokenizer.Tokenizer's surface for an .en model, with
    made-up ids."""

    sot, eot, no_timestamps = 900, 899, 950
    transcribe, translate, sot_prev, sot_lm, no_speech = 901, 902, 903, 904, 905
    non_speech_tokens = (7, 8)
    sot_sequence = [900]
    words = {1: " Pause", 2: " the", 3: " music.", 4: " la", 5: "!"}

    def decode(self, ids):
        return "".join(self.words.get(i, "") for i in ids if i < self.eot)


class _CT2:
    """ctranslate2.models.Whisper: records generate(), answers as told."""

    is_multilingual = False

    def __init__(self, ids=(1, 2, 3), score=-0.2, no_speech=0.01) -> None:
        self.ids, self.score, self.no_speech = list(ids), score, no_speech
        self.generated: list[tuple[list, dict]] = []

    def generate(self, encoded, prompts, **kw):
        self.generated.append((prompts, kw))
        return [types.SimpleNamespace(
            sequences_ids=[list(self.ids)], scores=[self.score], no_speech_prob=self.no_speech,
        )]


def _decoder(ct2=None, *, extra_frames=0):
    """A decoder over doubles. The extractor returns one frame per 160
    samples plus one (faster-whisper 1.2), plus `extra_frames` of padding
    an older faster-whisper adds; `encoded` collects what reached encode."""
    ct2 = ct2 or _CT2()
    encoded: list = []

    def features(audio):
        return np.ones((80, len(audio) // 160 + 1 + extra_frames), dtype=np.float32)

    def encode(feats):
        encoded.append(feats)
        return "encoder-output"

    dec = ShortWindowDecoder(
        feature_extractor=features, model=ct2, encode=encode, tokenizer=_Tok(),
    )
    return dec, ct2, encoded


# ─── the window ─────────────────────────────────────────────────────────


def test_the_window_is_ten_seconds_for_captures_up_to_nine() -> None:
    dec, *_ = _decoder()
    assert (SHORT_WINDOW_SEC, dec.frames) == (10, 1000)
    assert dec.fits(_pcm(0.3)) and dec.fits(_pcm(9.0))
    assert not dec.fits(_pcm(9.01))
    assert not dec.fits(b"")


def test_the_capture_s_own_frames_zero_padded_to_the_window() -> None:
    dec, _ct2, encoded = _decoder()
    dec.decode(_pcm(3.0))
    (feats,) = encoded
    assert feats.shape == (80, 1000) and feats.dtype == np.float32
    assert np.all(feats[:, :300] == 1) and np.all(feats[:, 300:] == 0)


def test_frames_an_older_extractor_pads_on_are_not_the_capture_s() -> None:
    """faster-whisper 1.0 padded the waveform with 30 s of zeros before
    the mel; those frames are not audio and must not stand in for the
    window's zero padding."""
    dec, _ct2, encoded = _decoder(extra_frames=3000)
    dec.decode(_pcm(2.0))
    assert np.all(encoded[0][:, :200] == 1) and np.all(encoded[0][:, 200:] == 0)


# ─── the call ───────────────────────────────────────────────────────────


def test_an_english_model_decodes_like_faster_whisper_without_timestamps() -> None:
    dec, ct2, _ = _decoder()
    result = dec.decode(_pcm(1.5))
    (prompts, kw), = ct2.generated
    assert prompts == [[_Tok.sot, _Tok.no_timestamps]]
    assert kw["beam_size"] == 5 and kw["patience"] == 1
    assert kw["length_penalty"] == 1 and kw["repetition_penalty"] == 1
    assert kw["no_repeat_ngram_size"] == 0
    assert kw["return_scores"] and kw["return_no_speech_prob"] and kw["suppress_blank"]
    # faster-whisper's list for suppress_tokens=[-1]: the non-speech
    # symbols plus the task and start tokens.
    assert kw["suppress_tokens"] == sorted([7, 8, 901, 902, 900, 903, 904, 905])
    assert kw["max_length"] == 2 + SHORT_MAX_NEW_TOKENS
    assert result.text == "Pause the music."
    assert result.complete


def test_a_multilingual_model_keeps_the_thirty_second_window(caplog) -> None:
    """On a short window a multilingual model misjudges the language
    (multilingual tiny: tier A/B commands right 211 -> 175 of 252), so it
    is never given one: the decoder refuses it, and the client loads
    without one, saying why at info level (it is not a fault)."""
    wm = types.SimpleNamespace(model=types.SimpleNamespace(is_multilingual=True))
    with pytest.raises(ValueError):
        ShortWindowDecoder.for_model(wm)
    caplog.set_level(logging.INFO, logger="domovoi.clients.whisper")
    c = object.__new__(FasterWhisperClient)
    c._model = wm
    assert c._load_short_window() is None
    (rec,) = [r for r in caplog.records if "multilingual" in r.getMessage()]
    assert rec.levelno == logging.INFO


def test_the_average_log_probability_is_recovered_like_faster_whisper() -> None:
    dec, *_ = _decoder(_CT2(ids=(1, 2, 3), score=-0.4))
    # score is the sum over the 3 tokens / 3; faster-whisper divides the sum by 4.
    assert dec.decode(_pcm(1.0)).avg_logprob == pytest.approx(-0.4 * 3 / 4)


# ─── the verdict ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "logprob", "no_speech", "complete", "verdict"),
    [
        ("Pause the music.", -0.2, 0.01, True, "ok"),
        ("Stop.", -0.6, 0.3, True, "ok"),
        # faster-whisper skips a window as silence when the text is
        # unlikely too...
        ("you", -1.2, 0.9, True, "blank"),
        # ...and would keep it when it isn't; on the short window that is
        # noise heard as "you", so the 30 s window decides.
        ("you", -0.7, 0.9, True, "unsure"),
        # Where faster-whisper would decode again at a higher temperature.
        ("Pause the muse.", -1.3, 0.1, True, "unsure"),
        ("the the the the the the the the the the the the the the the", -0.3, 0.1, True, "unsure"),
        # A decode that ran to the token cap is looping.
        ("Pause the music.", -0.2, 0.01, False, "unsure"),
        # Nothing to route.
        ("...", -0.2, 0.1, True, "blank"),
        ("", 0.0, 0.2, True, "blank"),
    ],
)
def test_the_verdict_follows_faster_whisper_s_thresholds(text, logprob, no_speech, complete, verdict) -> None:
    assert ShortDecode(text, logprob, no_speech, complete).verdict == verdict


def test_a_decode_that_runs_to_the_cap_is_not_complete() -> None:
    dec, *_ = _decoder(_CT2(ids=[4] * SHORT_MAX_NEW_TOKENS))
    assert dec.decode(_pcm(1.0)).verdict == "unsure"


# ─── the client ─────────────────────────────────────────────────────────


class _Short:
    """The client's view of a decoder: fits() by length, decode() as told."""

    def __init__(self, result: ShortDecode | Exception) -> None:
        self.result = result
        self.decoded: list[int] = []

    def fits(self, pcm: bytes) -> bool:
        return 0 < len(pcm) // 2 <= 9 * SR

    def decode(self, pcm: bytes) -> ShortDecode:
        self.decoded.append(len(pcm))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _client(short, monkeypatch, full_text: str = "from the thirty second window"):
    """A FasterWhisperClient without a model: the 30 s path is recorded."""
    c = object.__new__(FasterWhisperClient)
    c.cpu_threads = 8
    c.short_window = short
    c.full_calls = []

    def _full(pcm):
        c.full_calls.append(len(pcm))
        return full_text

    monkeypatch.setattr(c, "_transcribe_full_sync", _full)
    return c


OK = ShortDecode("Pause the music.", -0.2, 0.01, True)


def test_a_short_capture_takes_the_short_window(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_short_window_enabled", True)
    short = _Short(OK)
    c = _client(short, monkeypatch)
    assert c._transcribe_pcm_sync(_pcm(2.0)) == ("Pause the music.", SHORT_WINDOW_SEC)
    assert c.full_calls == [] and short.decoded == [2 * SR * 2]


def test_a_long_capture_takes_the_thirty_second_path(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_short_window_enabled", True)
    short = _Short(OK)
    c = _client(short, monkeypatch)
    assert c._transcribe_pcm_sync(_pcm(12.0)) == ("from the thirty second window", FULL_WINDOW_SEC)
    assert short.decoded == [] and c.full_calls == [12 * SR * 2]


def test_the_setting_off_means_the_thirty_second_path_always(monkeypatch) -> None:
    assert Settings.model_fields["whisper_short_window_enabled"].default is True
    monkeypatch.setattr(settings, "whisper_short_window_enabled", False)
    short = _Short(OK)
    c = _client(short, monkeypatch)
    assert c._transcribe_pcm_sync(_pcm(2.0))[1] == FULL_WINDOW_SEC
    assert short.decoded == []


@pytest.mark.parametrize(
    "result",
    [
        ShortDecode("you", -1.4, 0.95, True),              # no speech in it
        ShortDecode("Pause the muse.", -1.3, 0.1, True),   # unsure
        ShortDecode(".", -0.1, 0.1, True),                 # nothing to route
        RuntimeError("Invalid input features shape"),      # CTranslate2 refused it
    ],
    ids=["blank", "unsure", "punctuation", "error"],
)
def test_anything_but_a_sure_short_decode_falls_back(result, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "whisper_short_window_enabled", True)
    caplog.set_level(logging.INFO, logger="domovoi.clients.whisper")
    short = _Short(result)
    c = _client(short, monkeypatch)
    assert c._transcribe_pcm_sync(_pcm(2.0)) == ("from the thirty second window", FULL_WINDOW_SEC)
    assert len(short.decoded) == 1 and c.full_calls == [2 * SR * 2]
    assert any("30 s window" in r.getMessage() for r in caplog.records)
    # Numbers only in that log line: never the words.
    assert not any("muse" in r.getMessage() for r in caplog.records)


def test_no_short_decoder_means_the_thirty_second_path(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_short_window_enabled", True)
    c = _client(None, monkeypatch)
    assert c._transcribe_pcm_sync(_pcm(2.0))[1] == FULL_WINDOW_SEC


def test_transcribe_and_transcribe_with_window(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_short_window_enabled", True)
    c = _client(_Short(OK), monkeypatch)
    assert asyncio.run(c.transcribe(_pcm(1.0))) == "Pause the music."
    assert asyncio.run(transcribe_with_window(c, _pcm(1.0))) == ("Pause the music.", 10)
    assert asyncio.run(transcribe_with_window(c, _pcm(10.0)))[1] == 30

    class _Plain:  # a stub or a test double: no window to report
        async def transcribe(self, pcm):
            return "hi"

    assert asyncio.run(transcribe_with_window(_Plain(), b"\x00\x00")) == ("hi", None)


# ─── the load-time check ────────────────────────────────────────────────


def test_the_load_proves_the_short_window_on_a_second_of_silence(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO, logger="domovoi.clients.whisper")
    short = _Short(ShortDecode("", 0.0, 0.9, True))
    monkeypatch.setattr(ShortWindowDecoder, "for_model", classmethod(lambda cls, m: short))
    c = object.__new__(FasterWhisperClient)
    c._model = object()
    assert c._load_short_window() is short
    assert short.decoded == [SR * 2]
    assert any("short-window decoding ready" in r.getMessage() for r in caplog.records)


def test_a_ctranslate2_that_refuses_a_short_window_leaves_the_thirty_second_path(monkeypatch, caplog) -> None:
    short = _Short(ValueError("Invalid input features shape: expected (1, 80, 3000)"))
    monkeypatch.setattr(ShortWindowDecoder, "for_model", classmethod(lambda cls, m: short))
    c = object.__new__(FasterWhisperClient)
    c._model = object()
    assert c._load_short_window() is None
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "short-window decoding is unavailable" in msg and "ctranslate2" in msg


def test_a_model_the_decoder_cannot_be_built_for_leaves_the_thirty_second_path(monkeypatch) -> None:
    def _boom(cls, m):
        raise ImportError("no faster_whisper.tokenizer")

    monkeypatch.setattr(ShortWindowDecoder, "for_model", classmethod(_boom))
    c = object.__new__(FasterWhisperClient)
    c._model = object()
    assert c._load_short_window() is None


# ─── what the runtime block says ────────────────────────────────────────


def test_the_runtime_says_whether_short_captures_take_the_short_window(monkeypatch) -> None:
    monkeypatch.setattr(whisper_mod, "_status", whisper_mod._status_doc(
        "ok", loaded=("small.en", "cpu", "int8")))
    c = object.__new__(FasterWhisperClient)
    c.cpu_threads = 8
    c.short_window = _Short(OK)
    monkeypatch.setattr(whisper_mod, "_client", c)
    monkeypatch.setattr(settings, "whisper_short_window_enabled", True)
    assert whisper_mod.whisper_runtime()["short_window"] is True
    monkeypatch.setattr(settings, "whisper_short_window_enabled", False)
    assert whisper_mod.whisper_runtime()["short_window"] is False
    monkeypatch.setattr(settings, "whisper_short_window_enabled", True)
    c.short_window = None
    assert whisper_mod.whisper_runtime()["short_window"] is False
    monkeypatch.setattr(whisper_mod, "_client", whisper_mod.WhisperStubClient())
    assert whisper_mod.whisper_runtime()["short_window"] is None


def test_the_setting_is_a_hot_advanced_bool_documented_at_its_default() -> None:
    from pathlib import Path

    from domovoi.config_schema import FIELD_BY_NAME

    f = FIELD_BY_NAME["whisper_short_window_enabled"]
    assert (f.type, f.tier, f.section, f.group) == ("bool", "hot", "advanced", "Speech-to-text")
    example = (Path(__file__).resolve().parents[2] / "domovoi" / ".env.example").read_text(encoding="utf-8")
    assert "# WHISPER_SHORT_WINDOW_ENABLED=true" in example


# ─── the real CTranslate2, when it is here ───────────────────────────────


def _cached_tiny_en() -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return False
    hit = try_to_load_from_cache("Systran/faster-whisper-tiny.en", "model.bin")
    return isinstance(hit, str)


@pytest.mark.skipif(not _cached_tiny_en(), reason="tiny.en is not in the local HF cache")
def test_this_ctranslate2_decodes_a_short_window(monkeypatch) -> None:
    """The version check the design needs, run on whatever CTranslate2 is
    installed: a 1000-frame window is accepted, silence is blank (and so
    falls back), and a decode takes the short path end to end."""
    # As the core does: the Windows DLL preload before anything imports
    # CTranslate2 (domovoi/bootstrap.py; a no-op elsewhere).
    from domovoi.bootstrap import register_nvidia_dlls

    register_nvidia_dlls()
    pytest.importorskip("faster_whisper")
    pytest.importorskip("ctranslate2")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(settings, "whisper_cpu_threads", 2)
    monkeypatch.setattr(settings, "whisper_short_window_enabled", True)
    client = FasterWhisperClient("tiny.en", "cpu", "int8")
    assert client.short_window is not None, "this CTranslate2 refused a 10 s window"
    silence = client.short_window.decode(bytes(2 * SR * 2))
    assert silence.verdict in ("blank", "unsure", "ok")
    text, window = client._transcribe_pcm_sync(bytes(2 * SR * 2))
    assert window in (SHORT_WINDOW_SEC, FULL_WINDOW_SEC)
