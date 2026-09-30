"""The wake model's reset costs milliseconds, not a second — and the capture
no longer waits for it.

Garage and office, 2026-09-30: on every capture of 1.95 s or less the core
started its speculative transcript 0.3-0.4 s after the satellite's
`speech_pause` should have reached it; both longer captures were on time,
and the vt.py loopback (no satellite code) never showed it. The cause was
openWakeWord's `Model.reset()`, which the wake path runs after the
acknowledgement and BEFORE the capture opens: it refills the model's
feature buffer by running the speech-embedding model over 4 s of random
noise — 41 embedding windows, about 24 `predict()` calls' worth of work.
35 ms on a desktop; on a Pi Zero 2 W, whose `predict()` barely keeps up
with the 80 ms it is given, well over a second, with the mic queueing the
person's first words meanwhile. The capture then sent them in one burst,
so a pause inside that burst reached the core late; a pause after it did
not.

`_reset_wake_model` runs the library's own reset but answers its one
"embed 4 s of noise" call with the noise features the model computed when
it was loaded (`_prime_wake_reset`). Pinned here:

* with the real openWakeWord (when installed): a primed reset computes no
  embedding window at all, and leaves the model in exactly the state a
  full reset with the same noise leaves it — the same predictions after;
* every other call to the embedding goes through untouched, the model's
  own method is back in place afterwards (even when reset raises), and a
  stand-in that isn't shaped like openWakeWord just gets `reset()`;
* `_load_wake_model` primes the model it loads, and the mic-thread paths
  reset through `_reset_wake_model` only — including the one between the
  acknowledgement and the capture.
"""

from __future__ import annotations

import inspect
import sys
import types

import numpy as np
import pytest

from satellite.tests._client_import import import_client

client = import_client()

NOISE = client._WAKE_RESET_NOISE_SAMPLES


# ─── an openWakeWord-shaped stand-in ──────────────────────────────────────


class _Features:
    """Shaped like openwakeword.utils.AudioFeatures: `reset()` refills the
    feature buffer with `self._get_embeddings(<4 s of noise>)`, which costs
    one "window" per 8 mel frames of it (41 for 4 s)."""

    def __init__(self) -> None:
        self.windows = 0
        self.calls: list[tuple] = []
        self.raw_data_buffer = [1, 2, 3]
        self.feature_buffer = self._get_embeddings(np.random.randint(-1000, 1000, NOISE).astype(np.int16))

    def _get_embeddings(self, x, window_size: int = 76, step_size: int = 8):
        self.calls.append(x.shape)
        n = max(1, (len(x) // 160 - window_size) // step_size + 1)
        self.windows += n
        return np.full((n, 96), float(len(x)), dtype=np.float32)

    def reset(self) -> None:
        self.raw_data_buffer = []
        self.feature_buffer = self._get_embeddings(np.random.randint(-1000, 1000, NOISE).astype(np.int16))


class _Model:
    def __init__(self) -> None:
        self.preprocessor = _Features()
        self.prediction_buffer = {"hey_jarvis": [0.9]}
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1
        self.prediction_buffer = {}
        self.preprocessor.reset()


def test_a_primed_reset_computes_no_embedding_and_resets_everything_else() -> None:
    m = _Model()
    assert client._prime_wake_reset(m) is True
    loaded = m.preprocessor.feature_buffer.copy()
    m.preprocessor.windows = 0
    m.preprocessor.feature_buffer = np.zeros((3, 96), dtype=np.float32)   # it moved on

    client._reset_wake_model(m)

    assert m.resets == 1, "the library's own reset ran"
    assert m.preprocessor.windows == 0, "and embedded nothing"
    assert m.prediction_buffer == {} and m.preprocessor.raw_data_buffer == []
    np.testing.assert_array_equal(m.preprocessor.feature_buffer, loaded)
    # A copy: what the model does to its buffer next can't reach the cache.
    m.preprocessor.feature_buffer[0, 0] = -1.0
    client._reset_wake_model(m)
    np.testing.assert_array_equal(m.preprocessor.feature_buffer, loaded)


def test_an_unprimed_model_or_a_stand_in_gets_the_full_reset() -> None:
    m = _Model()
    m.preprocessor.windows = 0
    client._reset_wake_model(m)
    assert m.resets == 1 and m.preprocessor.windows == 41

    class _Plain:
        resets = 0

        def reset(self) -> None:
            self.resets += 1

    plain = _Plain()
    assert client._prime_wake_reset(plain) is False
    client._reset_wake_model(plain)
    assert plain.resets == 1


def test_any_other_embedding_call_goes_through_and_the_method_comes_back() -> None:
    m = _Model()
    client._prime_wake_reset(m)
    pre = m.preprocessor

    def reset_that_embeds_something_else() -> None:
        m.resets += 1
        pre.feature_buffer = pre._get_embeddings(np.zeros(16_000, dtype=np.int16))

    m.reset = reset_that_embeds_something_else
    pre.windows = 0
    client._reset_wake_model(m)
    assert pre.windows == 4 and pre.feature_buffer.shape == (4, 96)   # 1 s: 4 windows
    assert "_get_embeddings" not in vars(pre)
    assert pre._get_embeddings.__func__ is _Features._get_embeddings


def test_the_method_comes_back_when_reset_raises() -> None:
    m = _Model()
    client._prime_wake_reset(m)

    def broken() -> None:
        raise RuntimeError("boom")

    m.reset = broken
    with pytest.raises(RuntimeError):
        client._reset_wake_model(m)
    assert "_get_embeddings" not in vars(m.preprocessor)


def test_a_model_not_shaped_as_expected_is_not_primed() -> None:
    m = _Model()
    m.preprocessor.feature_buffer = [[0.0] * 96]      # not an array
    assert client._prime_wake_reset(m) is False
    assert not hasattr(m, "_domovoi_noise_features")


# ─── the real openWakeWord ────────────────────────────────────────────────


def _real_model():
    pytest.importorskip("onnxruntime")
    model_mod = pytest.importorskip("openwakeword.model")
    try:
        return model_mod.Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    except Exception as e:   # the bundled models aren't downloaded here
        pytest.skip(f"openWakeWord models unavailable: {e}")


def test_with_the_real_model_a_primed_reset_equals_a_full_one_and_embeds_nothing() -> None:
    np.random.seed(20260930)
    m = _real_model()
    assert client._prime_wake_reset(m) is True
    pre = m.preprocessor
    real_embed = pre.embedding_model_predict
    windows: list[int] = []

    def counting(x):
        windows.append(int(x.shape[0]))
        return real_embed(x)

    pre.embedding_model_predict = counting
    rng = np.random.default_rng(7)
    audio = [(rng.normal(0, 3000, 1280)).astype(np.int16) for _ in range(30)]

    # A full reset, drawing the same noise the model was loaded with.
    np.random.seed(20260930)
    m.reset()
    assert sum(windows) == 41, "what reset() costs: 41 embedding windows"
    full = [m.predict(a)["hey_jarvis"] for a in audio]
    full_buffer = pre.feature_buffer.copy()

    windows.clear()
    client._reset_wake_model(m)
    assert windows == [], "the primed reset embeds nothing"
    fast = [m.predict(a)["hey_jarvis"] for a in audio]

    np.testing.assert_allclose(fast, full, rtol=0, atol=1e-6)
    np.testing.assert_allclose(pre.feature_buffer, full_buffer, rtol=0, atol=1e-6)
    assert all(w == 1 for w in windows), "predict() still embeds its own chunks"


# ─── where it is used ─────────────────────────────────────────────────────


def test_every_mic_thread_reset_goes_through_the_cheap_one() -> None:
    src = inspect.getsource(client.Satellite)
    assert "oww.reset()" not in src
    for name in (
        "_wait_for_wake", "_await_response_with_wake_barge", "_dropin_wake_watch",
    ):
        assert "_reset_wake_model(oww)" in inspect.getsource(getattr(client.Satellite, name)), name
    # The one that cost the capture: after the acknowledgement, before the
    # capture opens.
    wake = inspect.getsource(client.Satellite._wait_for_wake)
    assert wake.index("self._acknowledge_wake(") < wake.rindex("_reset_wake_model(oww)")


def test_the_loaded_model_is_primed(monkeypatch) -> None:
    built = []

    class _Loaded(_Model):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            built.append(kwargs)

    fake = types.ModuleType("openwakeword.model")
    fake.Model = _Loaded
    monkeypatch.setitem(sys.modules, "openwakeword", types.ModuleType("openwakeword"))
    monkeypatch.setitem(sys.modules, "openwakeword.model", fake)
    monkeypatch.setattr(client, "_effective_wake_word", lambda cfg: "hey_jarvis")
    monkeypatch.setattr(client, "_effective_wake_threshold", lambda cfg: 0.5)
    monkeypatch.setattr(client, "_effective_wake_model_path", lambda cfg: None)
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace()

    oww = sat._load_wake_model()

    assert built == [{"wakeword_models": ["hey_jarvis"], "inference_framework": "onnx"}]
    assert isinstance(oww._domovoi_noise_features, np.ndarray)
    oww.preprocessor.windows = 0
    client._reset_wake_model(oww)
    assert oww.preprocessor.windows == 0
