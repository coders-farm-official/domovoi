"""The TTS chain is local: piper -> system, or system -> piper.

The Microsoft Edge online voices (edge-tts) were dropped on 2026-10-12 (the
owner's decision: Microsoft's service had started refusing edge-tts, every
reply went to Microsoft, and edge-tts is LGPL). An ``engine`` of "edge" left
in an old setting or registry row is spoken by Piper with its own voice,
never the Edge voice id, and nothing in the chain reaches the internet but
the one-time Piper voice download, which refuses under
``INTERNET_ACCESS=never``. ``synthesize_detailed`` reports which rung
actually spoke.

Everything here is DB-free and runs no real TTS: the per-engine helpers
are replaced, and the "must not be called" ones fail the test.
"""

from __future__ import annotations

import pytest

from domovoi import egress
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


def _client(preferred: str = "piper", *, piper_voice: str = "en_US-kristin-medium") -> RealTTSClient:
    return RealTTSClient(preferred_engine=preferred, piper_voice=piper_voice, speed=1.0)


def _must_not_run(name: str):
    def boom(*_a, **_k):
        pytest.fail(f"{name} must not be called")

    return boom


def _spy(monkeypatch, *, piper=WAV, system=WAV) -> list[tuple[str, str]]:
    """Replace the two engine helpers. A value is what the helper returns;
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

    monkeypatch.setattr(tts_mod, "_synth_piper_sync", make("piper", piper))
    monkeypatch.setattr(tts_mod, "_synth_system_sync", make("system", system, with_voice=False))
    return calls


# ─── The order table ────────────────────────────────────────────────────


@pytest.mark.parametrize("answer", ANSWERS)
def test_the_order_is_local_on_every_answer(answer):
    with egress.override_policy(answer):
        assert engine_order("piper") == ["piper", "system"]
        assert engine_order("system") == ["system", "piper"]
        # The dropped edge, an unknown or an empty engine name: Piper.
        assert engine_order("edge") == ["piper", "system"]
        assert engine_order(" EDGE ") == ["piper", "system"]
        assert engine_order("") == ["piper", "system"]
        assert engine_order(None) == ["piper", "system"]
        assert engine_order("espeak") == ["piper", "system"]
    assert tts_mod.ENGINES == ("piper", "system")
    assert not hasattr(tts_mod, "_synth_edge_sync")


def test_the_client_uses_the_shared_order():
    assert _client()._engine_order("piper") == ["piper", "system"]
    assert _client()._engine_order("edge") == ["piper", "system"]


# ─── Fallbacks ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("answer", ANSWERS)
@pytest.mark.parametrize(
    "piper_failure", [RuntimeError("bad uploaded model"), None, tts_mod._pcm_to_wav_bytes(b"", 22_050)]
)
def test_piper_failing_falls_to_the_system_voice(monkeypatch, answer, piper_failure):
    calls = _spy(monkeypatch, piper=piper_failure)
    with egress.override_policy(answer):
        result = _client("piper")._synth_detailed_blocking("hello there")
    assert result.engine == "system"
    assert result.voice is None
    assert result.wav == WAV
    assert [c[0] for c in calls] == ["piper", "system"]


def test_every_rung_failing_returns_the_empty_wav(monkeypatch):
    _spy(monkeypatch, piper=None, system=None)
    result = _client("piper")._synth_detailed_blocking("hi")
    assert result == SynthResult(wav=result.wav, engine="", voice=None)
    assert result.failures == ("piper: no audio", "system: no audio")
    assert result.wav.startswith(b"RIFF")
    assert tts_mod._wav_is_silent(result.wav)


def test_system_preferred_falls_back_to_piper(monkeypatch):
    calls = _spy(monkeypatch, system=None)
    result = _client("system")._synth_detailed_blocking("hi")
    assert result.engine == "piper"
    assert result.voice == "en_US-kristin-medium"
    assert [c[0] for c in calls] == ["system", "piper"]


@pytest.mark.parametrize("answer", ANSWERS)
def test_an_old_edge_engine_speaks_piper_with_its_own_voice(monkeypatch, answer):
    """A TTS_ENGINE=edge left in an old .env, or an Edge registry row the
    boot has not removed yet: Piper with ITS OWN voice (an Edge voice id is
    not a Piper model, and nothing goes to Microsoft)."""
    calls = _spy(monkeypatch, system="fail")
    with egress.override_policy(answer):
        result = _client("edge")._synth_detailed_blocking("hi", engine="edge", voice="en-US-GuyNeural")
    assert result == SynthResult(wav=WAV, engine="piper", voice="en_US-kristin-medium")
    assert calls == [("piper", "en_US-kristin-medium")]


def test_a_per_call_piper_voice_is_used(monkeypatch):
    calls = _spy(monkeypatch)
    result = _client("piper")._synth_detailed_blocking("hi", engine="piper", voice="en_US-ljspeech-medium")
    assert result == SynthResult(wav=WAV, engine="piper", voice="en_US-ljspeech-medium")
    assert calls == [("piper", "en_US-ljspeech-medium")]


@pytest.fixture
def fake_piper(monkeypatch, tmp_path):
    """A stand-in for the ONNX voice (``tts.PiperVoice``) so
    ``_synth_piper_sync`` reaches the voice-path resolution (and its
    download) without a real model."""

    loaded: list[str] = []

    class _Voice:
        sample_rate = 22_050

        def __init__(self, path):
            loaded.append(str(path))

        def synthesize_pcm(self, sentences, speed):
            return b"" * 100

    monkeypatch.setattr(tts_mod, "PiperVoice", _Voice)
    monkeypatch.setattr(tts_mod, "_phonemize", lambda text, dialect="en": ["hˈaɪ"])
    monkeypatch.setattr(tts_mod.settings, "voice_models_dir", str(tmp_path / "voices"))
    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    return loaded


def test_the_piper_download_is_refused_under_never_and_system_speaks(monkeypatch, fake_piper, tmp_path):
    monkeypatch.setattr(tts_mod, "_piper_http_client", _must_not_run("the Piper download client"))
    monkeypatch.setattr(tts_mod, "_synth_system_sync", lambda text: WAV)
    client = _client("piper", piper_voice="en_US-kristin-medium")
    with egress.override_policy("never"):
        result = client._synth_detailed_blocking("hello")
    assert result.engine == "system"
    assert result.voice is None
    assert fake_piper == []  # no model was loaded
    assert not any((tmp_path / "voices").glob("*.onnx*"))  # nothing written


def test_the_piper_download_still_runs_when_allowed(monkeypatch, fake_piper, tmp_path):
    import hashlib
    import ipaddress

    import httpx

    from domovoi import net_safety

    model, config = b"model-bytes", b"{}"
    blob = hashlib.sha1(b"blob %d\0" % len(config) + config).hexdigest()
    monkeypatch.setitem(
        tts_mod.PIPER_PINS, "en_US-kristin-medium",
        (hashlib.sha256(model).hexdigest(), len(model), blob, len(config)),
    )
    fetched: list[str] = []

    def handler(request):
        fetched.append(str(request.url))
        body = config if request.url.path.endswith(".onnx.json") else model
        return httpx.Response(200, content=body)

    monkeypatch.setattr(net_safety, "resolve_host", lambda h: [ipaddress.ip_address("93.184.216.34")])
    monkeypatch.setattr(
        tts_mod, "_piper_http_client",
        lambda: egress.sync_client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(tts_mod, "_synth_system_sync", _must_not_run("the system engine"))
    with egress.override_policy("always"):
        result = _client("piper", piper_voice="en_US-kristin-medium")._synth_detailed_blocking("hi")
    assert result.engine == "piper" and result.voice == "en_US-kristin-medium"
    assert sorted(u.rsplit("/", 1)[-1] for u in fetched) == [
        "en_US-kristin-medium.onnx", "en_US-kristin-medium.onnx.json",
    ]
    assert all(
        f"/rhasspy/piper-voices/resolve/{tts_mod.PIPER_PINNED_REVISION}/" in u for u in fetched
    )


def test_a_voice_already_on_disk_loads_under_never(monkeypatch, fake_piper, tmp_path):
    monkeypatch.setattr(tts_mod, "_piper_http_client", _must_not_run("the Piper download client"))
    vdir = tmp_path / "voices"
    vdir.mkdir()
    (vdir / "en_US-kristin-medium.onnx").write_bytes(b"m")
    (vdir / "en_US-kristin-medium.onnx.json").write_text("{}")
    with egress.override_policy("never"):
        result = _client("piper", piper_voice="en_US-kristin-medium")._synth_detailed_blocking("hi")
    assert result.engine == "piper"


def test_piper_voice_on_disk(monkeypatch, tmp_path):
    vdir = tmp_path / "voices"
    monkeypatch.setattr(tts_mod.settings, "voice_models_dir", str(vdir))
    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    assert not tts_mod.piper_voice_on_disk("en_US-kristin-medium")
    assert not vdir.exists()  # judging creates nothing
    vdir.mkdir()
    (vdir / "en_US-kristin-medium.onnx").write_bytes(b"m")
    assert not tts_mod.piper_voice_on_disk("en_US-kristin-medium")  # no .onnx.json yet
    (vdir / "en_US-kristin-medium.onnx.json").write_text("{}")
    assert tts_mod.piper_voice_on_disk("en_US-kristin-medium")
    uploaded = tmp_path / "mine.onnx"
    uploaded.write_bytes(b"m")
    assert tts_mod.piper_voice_on_disk(str(uploaded))
    tts_mod._piper_voice_cache["en_US-ljspeech-medium"] = object()
    assert tts_mod.piper_voice_on_disk("en_US-ljspeech-medium")


# ─── synthesize / synthesize_detailed ──────────────────────────────────────


async def test_synthesize_detailed_reports_the_rung_and_synthesize_returns_its_wav(monkeypatch):
    other = tts_mod._pcm_to_wav_bytes(b"\x03\x04" * 50, 16_000)
    _spy(monkeypatch, piper=None, system=other)
    client = _client("piper")
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
        ("always", "edge", "en-US-GuyNeural", ("piper", None)),
        ("never", "edge", "en-US-GuyNeural", ("piper", None)),
        ("always", "piper", "en_US-kristin-medium", ("piper", "en_US-kristin-medium")),
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
