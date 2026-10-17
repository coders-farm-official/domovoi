"""openWakeWord's per-chunk melspectrogram reads only the samples it uses.

The office satellite (Pi Zero 2 W) idled at 73-93 % of a core while doing
nothing but listening for its wake word (2026-10-17). Part of that is the
wake model itself; a quarter or more of it was openWakeWord 0.6.0 copying
its whole 10 s audio buffer (160,000 Python ints in a deque) into a list on
every 80 ms chunk, only to slice the last 1,760 samples off it.
`_lighten_wake_streaming` takes those samples from the end of the deque
instead. Pinned here:

* with the real openWakeWord (when installed): identical scores, chunk for
  chunk, against the library's own method — through the buffer filling up,
  past 10 s, and across a reset;
* on a stand-in: the samples handed to the melspectrogram are exactly the
  library's slice at every fill level, the buffer is never iterated from
  the front, and the short-buffer error is the library's;
* a model that is not shaped as expected keeps the library's method;
* `_load_wake_model` applies it to the model it loads.
"""

from __future__ import annotations

import inspect
from collections import deque

import numpy as np
import pytest

from satellite.tests._client_import import import_client

client = import_client()


class _CountingDeque(deque):
    """A deque that counts front-to-back iterations (what `list(d)` does)."""

    forward = 0

    def __iter__(self):
        type(self).forward += 1
        return super().__iter__()


class _Pre:
    """Shaped like openwakeword.utils.AudioFeatures for this one method."""

    def __init__(self, maxlen=16000 * 10):
        self.raw_data_buffer = _CountingDeque(maxlen=maxlen)
        self.melspectrogram_buffer = np.ones((76, 32))
        self.melspectrogram_max_len = 10 * 97
        self.seen: list[list[int]] = []

    def _get_melspectrogram(self, x):
        self.seen.append(list(x))
        return np.zeros((max(1, (len(x) - 400) // 160 + 1), 32))

    def _streaming_melspectrogram(self, n_samples):      # the library's (0.6.0)
        if len(self.raw_data_buffer) < 400:
            raise ValueError("The number of input frames must be at least 400 samples @ 16khz (25 ms)!")
        self.melspectrogram_buffer = np.vstack(
            (self.melspectrogram_buffer,
             self._get_melspectrogram(list(self.raw_data_buffer)[-n_samples - 160 * 3:]))
        )
        if self.melspectrogram_buffer.shape[0] > self.melspectrogram_max_len:
            self.melspectrogram_buffer = self.melspectrogram_buffer[-self.melspectrogram_max_len:, :]


class _Model:
    def __init__(self, **kw):
        self.preprocessor = _Pre(**kw)


def test_the_samples_are_the_library_s_slice_at_every_fill_level():
    ours, theirs = _Model(maxlen=8000), _Model(maxlen=8000)
    assert client._lighten_wake_streaming(ours) is True
    rng = np.random.default_rng(7)
    for i in range(12):                        # fills past the 8000-sample cap
        chunk = rng.integers(-32768, 32767, 1280).tolist()
        for m in (ours, theirs):
            m.preprocessor.raw_data_buffer.extend(chunk)
            m.preprocessor._streaming_melspectrogram(1280)
    assert ours.preprocessor.seen == theirs.preprocessor.seen
    assert np.array_equal(ours.preprocessor.melspectrogram_buffer,
                          theirs.preprocessor.melspectrogram_buffer)


def test_the_buffer_is_never_copied_from_the_front():
    m = _Model()
    client._lighten_wake_streaming(m)
    m.preprocessor.raw_data_buffer.extend(range(160_000))
    _CountingDeque.forward = 0
    for _ in range(25):
        m.preprocessor._streaming_melspectrogram(1280)
    assert _CountingDeque.forward == 0
    assert len(m.preprocessor.seen[-1]) == 1280 + 480


def test_a_short_buffer_raises_the_library_s_error():
    m = _Model()
    client._lighten_wake_streaming(m)
    m.preprocessor.raw_data_buffer.extend(range(399))
    with pytest.raises(ValueError, match="at least 400 samples"):
        m.preprocessor._streaming_melspectrogram(1280)


def test_a_model_of_another_shape_keeps_the_library_s_method():
    class Odd:
        preprocessor = type("P", (), {"raw_data_buffer": [1, 2, 3]})()

    assert client._lighten_wake_streaming(Odd()) is False
    assert client._lighten_wake_streaming(object()) is False
    plain = _Model()
    del plain.preprocessor.melspectrogram_max_len
    assert client._lighten_wake_streaming(plain) is False
    assert "_streaming_melspectrogram" not in plain.preprocessor.__dict__


def test_the_loader_applies_it():
    src = inspect.getsource(client.Satellite._load_wake_model)
    assert "_lighten_wake_streaming(oww)" in src


def _real_model():
    oww = pytest.importorskip("openwakeword.model")
    # The library fills its feature buffer with np.random noise at load and
    # at every reset: the same seed gives both models the same filler.
    np.random.seed(11)
    try:
        return oww.Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    except Exception as e:  # noqa: BLE001 — no bundled models / onnxruntime here
        pytest.skip(f"openWakeWord could not load a model here: {e}")


def test_identical_scores_with_the_real_openwakeword():
    a, b = _real_model(), _real_model()
    assert client._lighten_wake_streaming(b) is True
    rng = np.random.default_rng(3)
    # Past the 10 s buffer (125 chunks), then a reset, then more.
    for i in range(170):
        if i == 140:
            np.random.seed(12)
            a.reset()
            np.random.seed(12)
            b.reset()
        chunk = (rng.standard_normal(1280) * (200 + 50 * (i % 7))).astype(np.int16)
        pa, pb = a.predict(chunk), b.predict(chunk)
        assert pa == pb, f"chunk {i}"
    assert np.array_equal(a.preprocessor.melspectrogram_buffer, b.preprocessor.melspectrogram_buffer)
    assert np.array_equal(a.preprocessor.feature_buffer, b.preprocessor.feature_buffer)
