"""B1: Microsoft Edge is never a silent fallback.

The TTS router used to try ``edge`` as the second rung after a failed
``piper``, so a broken Piper voice on an online box quietly sent the reply
text to Microsoft. Now Edge appears in the chain ONLY when it is the
preferred engine for the call and the internet answer allows it, and the
two paths that reach the internet on their own — the Edge synth and the
one-time Piper voice download — refuse under ``INTERNET_ACCESS=never``.
``synthesize_detailed`` reports which rung actually spoke.

Everything here is DB-free and runs no real TTS: the per-engine helpers
are replaced, and the "must not be called" ones fail the test.
"""

from __future__ import annotations

import sys
import types

import pytest

from domovoi import egress, net_safety
from domovoi.clients import tts as tts_mod
from domovoi.clients.tts import (
    RealTTSClient,
    SynthResult,
    TTSClient,
    TTSStubClient,
    engine_order,
)

WAV = tts_mod._pcm_to_wav_bytes(b"\x01\x02" * 200, 22_050)
ANSWERS = ("", "always", "sometimes", "never")


def _client(preferred: str = "piper", *, piper_voice: str = "en_US-lessac-medium") -> RealTTSClient:
    return RealTTSClient(
        preferred_engine=preferred,
        edge_voice="en-US-AriaNeural",
        piper_voice=piper_voice,
        speed=1.0,
    )


def _must_not_run(name: str):
    def boom(*_a, **_k):
        pytest.fail(f"{name} must not be called")

    return boom


def _spy(monkeypatch, *, edge=WAV, piper=WAV, system=WAV) -> list[tuple[str, str]]:
    """Replace the three engine helpers. A value is what the helper returns;
    an Exception instance is raised; the string "fail" fails the test."""
    calls: list[tuple[str, str]] = []

    def make(name, outcome, with_voice=True):
        def fn(text, voice=None, speed=None):
            calls.append((name, voice if with_voice else ""))
            if outcome == "fail":
                pytest.fail(f"the {name} engine must not be called")
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        if with_voice:
            return fn
        return lambda text: fn(text)

    monkeypatch.setattr(tts_mod, "_synth_edge_sync", make("edge", edge))
    monkeypatch.setattr(tts_mod, "_synth_piper_sync", make("piper", piper))
    monkeypatch.setattr(tts_mod, "_synth_system_sync", make("system", system, with_voice=False))
    return calls


# ─── The order table (CONTRACT 7.1) ────────────────────────────────────────


@pytest.mark.parametrize("answer", ["", "always", "sometimes"])
def test_order_when_the_internet_is_allowed(answer):
    with egress.override_policy(answer):
        assert engine_order("edge") == ["edge", "piper", "system"]
        assert engine_order("piper") == ["piper", "system"]
        assert engine_order("system") == ["system", "piper"]
        # An unknown or empty engine name is Piper, never Edge.
        assert engine_order("") == ["piper", "system"]
        assert engine_order(None) == ["piper", "system"]
        assert engine_order("espeak") == ["piper", "system"]
        assert engine_order(" EDGE ") == ["edge", "piper", "system"]


def test_order_under_never():
    with egress.override_policy("never"):
        assert engine_order("edge") == ["piper", "system"]
        assert engine_order("piper") == ["piper", "system"]
        assert engine_order("system") == ["system", "piper"]
        assert engine_order("") == ["piper", "system"]


@pytest.mark.parametrize("answer", ANSWERS)
@pytest.mark.parametrize("preferred", ["edge", "piper", "system", "", "bogus"])
def test_edge_appears_only_when_preferred_and_allowed(answer, preferred):
    with egress.override_policy(answer):
        order = engine_order(preferred)
        assert ("edge" in order) == (preferred == "edge" and answer != "never")
        # Never a fallback: if it is there at all, it is first.
        if "edge" in order:
            assert order[0] == "edge"
        assert len(order) == len(set(order))


def test_the_client_uses_the_shared_order():
    with egress.override_policy("always"):
        assert _client()._engine_order("piper") == ["piper", "system"]
        assert _client()._engine_order("edge") == ["edge", "piper", "system"]


def test_never_logs_the_edge_skip_once(monkeypatch, caplog):
    monkeypatch.setattr(tts_mod, "_edge_skip_logged", False)
    caplog.set_level("INFO", logger=tts_mod.log.name)
    with egress.override_policy("never"):
        engine_order("edge")
        engine_order("edge")
        engine_order("edge")
    lines = [r for r in caplog.records if "Microsoft Edge voice is skipped" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].levelname == "INFO"


# ─── Piper failing never falls back to Edge (the bug) ──────────────────────


@pytest.mark.parametrize("answer", ["", "always", "sometimes"])
@pytest.mark.parametrize(
    "piper_failure", [RuntimeError("bad uploaded model"), None, tts_mod._pcm_to_wav_bytes(b"", 22_050)]
)
def test_piper_failing_on_an_online_box_falls_to_system_not_edge(monkeypatch, answer, piper_failure):
    calls = _spy(monkeypatch, edge="fail", piper=piper_failure)
    with egress.override_policy(answer):
        result = _client("piper")._synth_detailed_blocking("hello there")
    assert result.engine == "system"
    assert result.voice is None
    assert result.wav == WAV
    assert [c[0] for c in calls] == ["piper", "system"]


def test_every_local_rung_failing_returns_the_empty_wav_not_edge(monkeypatch):
    _spy(monkeypatch, edge="fail", piper=None, system=None)
    with egress.override_policy("always"):
        result = _client("piper")._synth_detailed_blocking("hi")
    assert result == SynthResult(wav=result.wav, engine="", voice=None)
    assert result.wav.startswith(b"RIFF")
    assert tts_mod._wav_is_silent(result.wav)


def test_system_preferred_falls_back_to_piper_not_edge(monkeypatch):
    calls = _spy(monkeypatch, edge="fail", system=None)
    with egress.override_policy("always"):
        result = _client("system")._synth_detailed_blocking("hi")
    assert result.engine == "piper"
    assert result.voice == "en_US-lessac-medium"
    assert [c[0] for c in calls] == ["system", "piper"]


# ─── Edge as the household's own choice ────────────────────────────────────


@pytest.mark.parametrize("answer", ["", "always", "sometimes"])
def test_tts_engine_edge_online_speaks_edge(monkeypatch, answer):
    calls = _spy(monkeypatch, piper="fail", system="fail")
    with egress.override_policy(answer):
        result = _client("edge")._synth_detailed_blocking("hi")
    assert result == SynthResult(wav=WAV, engine="edge", voice="en-US-AriaNeural")
    assert calls == [("edge", "en-US-AriaNeural")]


def test_a_per_call_edge_voice_is_used_when_allowed(monkeypatch):
    calls = _spy(monkeypatch, piper="fail")
    with egress.override_policy("always"):
        result = _client("piper")._synth_detailed_blocking(
            "hi", engine="edge", voice="en-US-GuyNeural"
        )
    assert result.engine == "edge" and result.voice == "en-US-GuyNeural"
    assert calls == [("edge", "en-US-GuyNeural")]


def test_edge_failing_falls_back_to_piper_with_its_own_voice(monkeypatch):
    calls = _spy(monkeypatch, edge=None, system="fail")
    with egress.override_policy("always"):
        result = _client("edge")._synth_detailed_blocking("hi", voice="en-US-GuyNeural")
    assert result == SynthResult(wav=WAV, engine="piper", voice="en_US-lessac-medium")
    assert calls == [("edge", "en-US-GuyNeural"), ("piper", "en_US-lessac-medium")]


def test_edge_preferred_under_never_speaks_piper_without_trying_edge(monkeypatch):
    calls = _spy(monkeypatch, edge="fail")
    with egress.override_policy("never"):
        result = _client("edge")._synth_detailed_blocking("hi", engine="edge", voice="en-US-GuyNeural")
    # Piper with ITS OWN voice: the Edge voice id is not a Piper model.
    assert result == SynthResult(wav=WAV, engine="piper", voice="en_US-lessac-medium")
    assert calls == [("piper", "en_US-lessac-medium")]


# ─── The two paths that reach the internet themselves ─────────────────────


@pytest.fixture
def edge_tts_must_not_run(monkeypatch):
    fake = types.ModuleType("edge_tts")
    fake.Communicate = _must_not_run("edge_tts.Communicate")
    monkeypatch.setitem(sys.modules, "edge_tts", fake)
    return fake


def test_the_edge_synth_refuses_under_never(edge_tts_must_not_run):
    with egress.override_policy("never"):
        with pytest.raises(egress.InternetTurnedOff) as info:
            tts_mod._synth_edge_sync("hello", "en-US-AriaNeural", 1.0)
    # Every existing "unsafe URL" handler still treats it as a refusal.
    assert isinstance(info.value, net_safety.UnsafeOutboundURL)


def test_the_real_edge_rung_is_never_reached_under_never(monkeypatch, edge_tts_must_not_run):
    # Only Piper is replaced: the real _synth_edge_sync stays in place, and
    # edge_tts.Communicate fails the test if anything reaches it.
    monkeypatch.setattr(tts_mod, "_synth_piper_sync", lambda text, voice, speed: WAV)
    with egress.override_policy("never"):
        result = _client("edge")._synth_detailed_blocking("hi")
    assert result.engine == "piper"


@pytest.fixture
def fake_piper(monkeypatch, tmp_path):
    """A stand-in ``piper`` package so ``_synth_piper_sync`` reaches the
    voice-path resolution (and its download) without a real model."""

    class _Voice:
        def synthesize(self, text, syn_config=None):
            yield types.SimpleNamespace(sample_rate=22_050, audio_int16_bytes=b"\x01\x02" * 100)

    loaded: list[str] = []

    class _PiperVoice:
        @staticmethod
        def load(path):
            loaded.append(path)
            return _Voice()

    fake = types.ModuleType("piper")
    fake.PiperVoice = _PiperVoice
    fake.SynthesisConfig = lambda **kw: types.SimpleNamespace(**kw)
    monkeypatch.setitem(sys.modules, "piper", fake)
    monkeypatch.setattr(tts_mod.settings, "voice_models_dir", str(tmp_path / "voices"))
    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    return loaded


def test_the_piper_download_is_refused_under_never_and_system_speaks(monkeypatch, fake_piper, tmp_path):
    import requests

    monkeypatch.setattr(requests, "get", _must_not_run("requests.get (the Piper download)"))
    monkeypatch.setattr(tts_mod, "_synth_edge_sync", _must_not_run("the edge engine"))
    monkeypatch.setattr(tts_mod, "_synth_system_sync", lambda text: WAV)
    client = _client("piper", piper_voice="en_US-ryan-high")
    with egress.override_policy("never"):
        result = client._synth_detailed_blocking("hello")
    assert result.engine == "system"
    assert result.voice is None
    assert fake_piper == []  # no model was loaded
    assert not any((tmp_path / "voices").glob("*.onnx*"))  # nothing written


def test_the_piper_download_still_runs_when_allowed(monkeypatch, fake_piper, tmp_path):
    import requests

    fetched: list[str] = []

    class _Resp:
        def raise_for_status(self):
            return None

        def iter_content(self, _n):
            yield b"model-bytes"

    def get(url, **_kw):
        fetched.append(url)
        return _Resp()

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(tts_mod, "_synth_system_sync", _must_not_run("the system engine"))
    with egress.override_policy("always"):
        result = _client("piper", piper_voice="en_US-ryan-high")._synth_detailed_blocking("hi")
    assert result.engine == "piper" and result.voice == "en_US-ryan-high"
    assert [u.rsplit("/", 1)[-1] for u in fetched] == [
        "en_US-ryan-high.onnx", "en_US-ryan-high.onnx.json",
    ]
    assert all(u.startswith("https://huggingface.co/rhasspy/piper-voices/") for u in fetched)


def test_a_voice_already_on_disk_loads_under_never(monkeypatch, fake_piper, tmp_path):
    import requests

    monkeypatch.setattr(requests, "get", _must_not_run("requests.get"))
    vdir = tmp_path / "voices"
    vdir.mkdir()
    (vdir / "en_US-ryan-high.onnx").write_bytes(b"m")
    (vdir / "en_US-ryan-high.onnx.json").write_text("{}")
    with egress.override_policy("never"):
        result = _client("piper", piper_voice="en_US-ryan-high")._synth_detailed_blocking("hi")
    assert result.engine == "piper"


def test_piper_voice_on_disk(monkeypatch, tmp_path):
    vdir = tmp_path / "voices"
    monkeypatch.setattr(tts_mod.settings, "voice_models_dir", str(vdir))
    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    assert not tts_mod.piper_voice_on_disk("en_US-ryan-high")
    assert not vdir.exists()  # judging creates nothing
    vdir.mkdir()
    (vdir / "en_US-ryan-high.onnx").write_bytes(b"m")
    assert not tts_mod.piper_voice_on_disk("en_US-ryan-high")  # no .onnx.json yet
    (vdir / "en_US-ryan-high.onnx.json").write_text("{}")
    assert tts_mod.piper_voice_on_disk("en_US-ryan-high")
    uploaded = tmp_path / "mine.onnx"
    uploaded.write_bytes(b"m")
    assert tts_mod.piper_voice_on_disk(str(uploaded))
    tts_mod._piper_voice_cache["en_US-amy-medium"] = object()
    assert tts_mod.piper_voice_on_disk("en_US-amy-medium")


# ─── synthesize / synthesize_detailed ──────────────────────────────────────


async def test_synthesize_detailed_reports_the_rung_and_synthesize_returns_its_wav(monkeypatch):
    other = tts_mod._pcm_to_wav_bytes(b"\x03\x04" * 50, 16_000)
    _spy(monkeypatch, edge="fail", piper=None, system=other)
    client = _client("piper")
    with egress.override_policy("always"):
        detailed = await client.synthesize_detailed("hi **there**")
        plain = await client.synthesize("hi **there**")
    assert detailed == SynthResult(wav=other, engine="system", voice=None)
    assert plain == other


def test_the_protocol_has_synthesize_detailed():
    assert hasattr(TTSClient, "synthesize_detailed")
    assert hasattr(TTSStubClient, "synthesize_detailed")
    assert hasattr(RealTTSClient, "synthesize_detailed")


@pytest.mark.parametrize(
    ("answer", "engine", "voice", "expected"),
    [
        ("always", "edge", "en-US-GuyNeural", ("edge", "en-US-GuyNeural")),
        ("never", "edge", "en-US-GuyNeural", ("piper", None)),
        ("always", "piper", "en_US-ryan-high", ("piper", "en_US-ryan-high")),
        ("always", "system", None, ("system", None)),
        ("always", None, None, ("piper", None)),
    ],
)
async def test_the_stub_reports_the_first_rung(answer, engine, voice, expected):
    with egress.override_policy(answer):
        result = await TTSStubClient().synthesize_detailed("hi", engine=engine, voice=voice)
        plain = await TTSStubClient().synthesize("hi", engine=engine, voice=voice)
    assert (result.engine, result.voice) == expected
    assert result.wav == plain and plain.startswith(b"RIFF")
