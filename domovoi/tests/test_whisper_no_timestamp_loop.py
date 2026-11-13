"""The 30 s decode can't loop forever (incident 2026-11-12/13).

With timestamps on, faster-whisper moves through the audio by the last
timestamp the model wrote. A window that came back as two "0.00"
timestamps moved by 0, and at temperature 0 the same window decoded the
same way again, forever: the speech thread never returned and the room's
turns all timed out until the core restarted. ``WhisperClient._run_model``
now decodes without timestamps and suppresses the timestamp tokens.

DB-free. The library test runs where faster-whisper is installed and skips
saying so elsewhere.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import numpy as np
import pytest

from domovoi.clients import whisper as W

HAVE_FW = importlib.util.find_spec("faster_whisper") is not None


class _Vocab:
    def __init__(self, notimestamps: int = 50363) -> None:
        self.notimestamps = notimestamps

    def token_to_id(self, token: str) -> int:
        assert token == "<|notimestamps|>"
        return self.notimestamps

    def get_vocab_size(self) -> int:
        return self.notimestamps + 1          # like the real file: it stops at the timestamps


class _FakeModel:
    def __init__(self, vocab=None) -> None:
        self.calls: list[dict] = []
        self.hf_tokenizer = vocab if vocab is not None else _Vocab()

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return iter([SimpleNamespace(text=" turn it up ")]), None


def _client(model) -> W.FasterWhisperClient:
    c = object.__new__(W.FasterWhisperClient)      # no model load
    c._model = model
    return c


def test_the_full_window_decodes_without_timestamps() -> None:
    m = _FakeModel()
    assert _client(m)._run_model(np.zeros(16000 * 12, np.float32)) == "turn it up"
    (kw,) = m.calls
    assert kw["without_timestamps"] is True
    assert kw["beam_size"] == 5


def test_every_timestamp_token_is_suppressed_and_the_default_kept() -> None:
    m = _FakeModel(_Vocab(notimestamps=50363))
    _client(m)._run_model(np.zeros(16000, np.float32))
    ids = m.calls[0]["suppress_tokens"]
    assert ids[0] == -1                       # the library's own non-speech set stays
    assert ids[1:] == list(range(50364, 50364 + 1501))      # <|0.00|> .. <|30.00|>
    assert 50363 not in ids                   # <|notimestamps|> itself is the prompt's


def test_the_suppression_list_is_worked_out_once() -> None:
    c = _client(_FakeModel())
    assert c._suppress_tokens() is c._suppress_tokens()


def test_an_unreadable_vocabulary_still_decodes(caplog) -> None:
    class Broken:
        def token_to_id(self, token):
            raise KeyError(token)

    m = _FakeModel(Broken())
    assert _client(m)._run_model(np.zeros(16000, np.float32)) == "turn it up"
    assert m.calls[0]["suppress_tokens"] == [-1]
    assert m.calls[0]["without_timestamps"] is True
    assert "timestamp tokens" in caplog.text


@pytest.mark.skipif(not HAVE_FW, reason="faster-whisper isn't installed")
def test_the_count_matches_the_library_tokenizer() -> None:
    """faster-whisper's own idea of the last timestamp, for an English-only
    and a multilingual vocabulary, is where the suppression list ends."""
    from faster_whisper.tokenizer import Tokenizer

    for notimestamps, multilingual in ((50362, False), (50364, True)):
        vocab = _Vocab(notimestamps)
        vocab.token_to_id = lambda token, n=notimestamps: {  # noqa: E731
            "<|notimestamps|>": n, "<|endoftext|>": 50256 if not multilingual else 50257,
        }.get(token, 0)
        ids = _client(_FakeModel(vocab))._suppress_tokens()
        lib = Tokenizer.__new__(Tokenizer)
        lib.tokenizer = vocab
        assert ids[1] == lib.timestamp_begin
        assert ids[-1] == lib.timestamp_begin + 1500       # <|30.00|>
        assert len(ids) == 1 + W.TIMESTAMP_TOKENS


@pytest.mark.skipif(not HAVE_FW, reason="faster-whisper isn't installed")
def test_the_library_moves_by_zero_on_two_zero_timestamps() -> None:
    """The hazard itself, in faster-whisper's splitter: the tokens
    ``<|0.00|><|0.00|>`` leave ``seek`` where it was. Suppressing the
    timestamp tokens is what keeps the model from writing them."""
    from faster_whisper.transcribe import WhisperModel

    model = object.__new__(WhisperModel)
    model.input_stride = 2
    model.time_precision = 0.02
    tb = 50364
    tok = SimpleNamespace(timestamp_begin=tb)
    _segs, seek, _single = model._split_segments_by_timestamps(
        tok, [tb, tb], time_offset=0.0, segment_size=1200, segment_duration=12.0, seek=0,
    )
    assert seek == 0                           # no progress: the loop
    _segs, seek, _single = model._split_segments_by_timestamps(
        tok, [440, 257, 50256], time_offset=0.0, segment_size=1200, segment_duration=12.0, seek=0,
    )
    assert seek == 1200                        # plain text moves on by the window
