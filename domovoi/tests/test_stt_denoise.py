"""Noise cleanup before speech to text (1.5.0, noisy rooms, owner answer
Q9): an A/B experiment, off by default (domovoi/stt_denoise.py).

Pinned here, DB-free:
  * off by default, in Settings, the dashboard's registry and
    .env.example; off hands Whisper the very bytes captured;
  * on, Whisper hears the cleaned audio, on every decode of a satellite's
    command (``StreamSession._whisper_call``: first, speculative and the
    second hearing) and the phone app's (``phone_voice._transcribe``);
    nothing else in the core calls it (wake detection runs on the
    satellite; voice ID and the recordings read the raw capture);
  * it fails safe: a filter that raises or returns junk, an unknown
    mode, a pool that won't run it: the raw audio;
  * RNNoise was dropped after the A/B (it clipped command starts): the
    modes are off | spectral, an old stored ``rnnoise`` reads as off, and
    pyrnnoise is no product dependency;
  * the setting round trip through ``POST /v1/admin/config`` (live value,
    ``.env``, the read back, a bad value refused).
"""

from __future__ import annotations

import asyncio
import re
import types
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import config_env_writer, phone_voice, stt_denoise
from domovoi.config import STT_DENOISE_MODES, Settings, settings
from domovoi.config_schema import FIELD_BY_NAME
from domovoi.streaming import StreamSession

REPO = Path(__file__).resolve().parents[2]


def _noisy_pcm(seconds: float = 2.0, seed: int = 3) -> bytes:
    rng = np.random.default_rng(seed)
    t = np.arange(int(16_000 * seconds)) / 16_000
    x = 0.3 * np.sin(2 * np.pi * 300 * t) * (t > 0.5) + 0.05 * rng.standard_normal(len(t))
    return (x * 32767).astype("<i2").tobytes()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(stt_denoise, "_cache", None)
    monkeypatch.setattr(stt_denoise, "_warned", set())


class _Whisper:
    """Records the audio each decode was handed."""

    def __init__(self) -> None:
        self.heard: list[tuple[str, bytes]] = []

    async def transcribe_with_window(self, pcm: bytes):
        self.heard.append(("short", pcm))
        return "pause the music", 10

    async def transcribe_full_window(self, pcm: bytes):
        self.heard.append(("full", pcm))
        return "pause the music", 30

    async def transcribe(self, pcm: bytes) -> str:
        self.heard.append(("plain", pcm))
        return "pause the music"


def _session() -> StreamSession:
    ws = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(satellite_config={})))
    return StreamSession(ws, "kitchen")  # type: ignore[arg-type]


# ─── off by default ───────────────────────────────────────────────────────


def test_off_by_default_everywhere() -> None:
    assert Settings.model_fields["stt_denoise"].default == "off"
    assert STT_DENOISE_MODES[0] == "off"
    f = FIELD_BY_NAME["stt_denoise"]
    assert (f.type, f.tier, f.section, f.group) == ("choice", "hot", "advanced", "Speech-to-text")
    assert f.choices == list(STT_DENOISE_MODES)
    assert set(f.choice_labels or {}) == set(STT_DENOISE_MODES)
    example = (REPO / "domovoi" / ".env.example").read_text(encoding="utf-8")
    assert "# STT_DENOISE=off" in example


def test_off_hands_whisper_the_captured_bytes(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stt_denoise", "off")
    pcm = _noisy_pcm()
    assert asyncio.run(stt_denoise.for_stt(pcm)) is pcm
    assert stt_denoise.denoise_pcm(pcm) is pcm
    w, sess = _Whisper(), _session()
    asyncio.run(sess._whisper_call(w, pcm))
    asyncio.run(sess._whisper_call(w, pcm, full_window=True))
    assert [k for k, _ in w.heard] == ["short", "full"]
    assert all(a is pcm for _, a in w.heard)


# ─── on: the command audio, and only it ───────────────────────────────────


def test_spectral_cleans_the_noise_and_keeps_the_voice() -> None:
    pcm = _noisy_pcm()
    out = stt_denoise.denoise_pcm(pcm, "spectral")
    assert len(out) == len(pcm) and out != pcm
    x = np.frombuffer(pcm, "<i2").astype(float)
    y = np.frombuffer(out, "<i2").astype(float)
    noise_in, noise_out = np.std(x[:6000]), np.std(y[:6000])
    assert noise_out < noise_in * 0.6
    tone = slice(12_000, 30_000)
    assert np.corrcoef(x[tone], y[tone])[0, 1] > 0.97


@pytest.mark.parametrize("full", [False, True])
def test_on_every_satellite_decode_hears_the_cleaned_audio(monkeypatch, full) -> None:
    monkeypatch.setattr(settings, "stt_denoise", "spectral")
    pcm = _noisy_pcm()
    w, sess = _Whisper(), _session()
    text, _ms, window = asyncio.run(sess._whisper_call(w, pcm, full_window=full))
    assert text == "pause the music" and window == (30 if full else 10)
    (kind, heard), = w.heard
    assert kind == ("full" if full else "short")
    assert heard == stt_denoise.denoise_pcm(pcm, "spectral") and heard != pcm


def test_on_the_phone_app_hears_the_cleaned_audio(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stt_denoise", "spectral")
    monkeypatch.setattr(settings, "whisper_short_window_recheck", False)
    w = _Whisper()
    monkeypatch.setattr(phone_voice, "get_whisper_client", lambda: w)
    pcm = _noisy_pcm()
    stages: dict[str, int] = {}
    assert asyncio.run(phone_voice._transcribe(pcm, stages)) == "pause the music"
    (_kind, heard), = w.heard
    assert heard == stt_denoise.denoise_pcm(pcm, "spectral") and heard != pcm


def test_only_the_speech_to_text_calls_use_it() -> None:
    """Wake detection runs on the satellite; voice ID, the command
    recordings and the turn history read the raw capture. The cleanup is
    called from exactly the two speech-to-text entry points."""
    calls = []
    for path in (REPO / "domovoi").rglob("*.py"):
        if "tests" in path.parts or path.name == "stt_denoise.py":
            continue
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"stt_denoise\.(\w+)\(", src):
            fn = None
            for d in re.finditer(r"\n\s*(?:async )?def (\w+)\(", src[: m.start()]):
                fn = d.group(1)
            calls.append((path.name, fn, m.group(1)))
    assert sorted(set(calls)) == [
        ("phone_voice.py", "_transcribe", "for_stt"),
        ("streaming.py", "_cleaned", "for_stt"),
        ("streaming.py", "_start", "current_mode"),
    ]
    src = (REPO / "domovoi" / "streaming.py").read_text(encoding="utf-8")
    start = src.index("    async def _whisper_call(")
    assert start < src.index("async def _cleaned(") < src.index("\n    def ", start + 10)


# ─── fails safe ───────────────────────────────────────────────────────────


def test_a_filter_that_raises_gives_the_raw_audio(monkeypatch, caplog) -> None:
    def boom(_x):
        raise RuntimeError("kaput")

    monkeypatch.setitem(stt_denoise._FILTERS, "spectral", boom)
    pcm = _noisy_pcm()
    with caplog.at_level("WARNING", logger="domovoi.stt_denoise"):
        assert stt_denoise.denoise_pcm(pcm, "spectral") is pcm
        assert stt_denoise.denoise_pcm(pcm, "spectral") is pcm
    assert sum("kaput" in r.getMessage() for r in caplog.records) == 1  # once, not per turn


@pytest.mark.parametrize("junk", [
    lambda x: x[:-5],                                  # wrong length
    lambda x: np.full_like(x, np.nan),                # not finite
])
def test_junk_from_a_filter_gives_the_raw_audio(monkeypatch, junk) -> None:
    monkeypatch.setitem(stt_denoise._FILTERS, "spectral", junk)
    pcm = _noisy_pcm()
    assert stt_denoise.denoise_pcm(pcm, "spectral") is pcm


def test_an_old_rnnoise_value_reads_as_off(monkeypatch, tmp_path) -> None:
    assert STT_DENOISE_MODES == ("off", "spectral")
    assert Settings(stt_denoise="rnnoise").stt_denoise == "off"
    assert Settings(stt_denoise=" Spectral ").stt_denoise == "spectral"
    env = tmp_path / ".env"
    env.write_text("STT_DENOISE=rnnoise\n", encoding="utf-8")
    monkeypatch.delenv("STT_DENOISE", raising=False)
    assert Settings(_env_file=str(env)).stt_denoise == "off"
    # A live value that bypassed validation still reads as off.
    monkeypatch.setattr(settings, "stt_denoise", "rnnoise")
    assert stt_denoise.current_mode() == "off"
    pcm = _noisy_pcm()
    assert asyncio.run(stt_denoise.for_stt(pcm)) is pcm
    assert stt_denoise.denoise_pcm(pcm, "rnnoise") is pcm


def test_pyrnnoise_is_no_product_dependency() -> None:
    shipped = [REPO / "pyproject.toml", *REPO.glob("requirements*"), *(REPO / "domovoi").rglob("*.py")]
    for path in shipped:
        if "tests" in path.parts or not path.is_file():
            continue
        assert "pyrnnoise" not in path.read_text(encoding="utf-8", errors="replace").lower(), path


def test_an_unknown_mode_is_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stt_denoise", "deepfilter")
    assert stt_denoise.current_mode() == "off"
    pcm = _noisy_pcm()
    assert asyncio.run(stt_denoise.for_stt(pcm)) is pcm
    assert stt_denoise.denoise_pcm(pcm, "deepfilter") is pcm


def test_a_pool_that_wont_run_it_gives_the_raw_audio(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stt_denoise", "spectral")

    async def refuse(*_a, **_k):
        raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(stt_denoise.executors, "run", refuse)
    pcm = _noisy_pcm()
    assert asyncio.run(stt_denoise.for_stt(pcm)) is pcm


def test_odd_and_tiny_input_passes(monkeypatch) -> None:
    assert stt_denoise.denoise_pcm(b"", "spectral") == b""
    tiny = b"\x01\x00\x02"  # shorter than a frame, odd length
    assert stt_denoise.denoise_pcm(tiny, "spectral") == tiny


# ─── the setting round trip ───────────────────────────────────────────────


TOKEN = "admin-token"


@pytest.fixture
def box(tmp_path, monkeypatch):
    import domovoi.db.repositories as repos
    import domovoi.main as main_mod
    from domovoi.main import app as core_app
    from domovoi.tests.auth_testkit import install_fake_db

    env = tmp_path / ".env"
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", env)
    monkeypatch.delenv("STT_DENOISE", raising=False)
    monkeypatch.setattr(settings, "stt_denoise", "off")
    install_fake_db(monkeypatch, admin=True, sessions={TOKEN})
    core_app.state.config_apply_lock = asyncio.Lock()

    @asynccontextmanager
    async def fake_scope():
        yield object()

    class FakeLog:
        def __init__(self, _s):
            pass

        async def log(self, **kw):
            pass

    monkeypatch.setattr(main_mod, "session_scope", fake_scope)
    monkeypatch.setattr(repos, "IntentLogRepository", FakeLog)
    return {"env": env, "app": core_app}


async def _post(app, changes: dict):
    from domovoi.tests.auth_testkit import bearer

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://test") as c:
        saved = await c.post("/v1/admin/config", json={"changes": changes}, headers=bearer(TOKEN))
        read = await c.get("/v1/admin/config", headers=bearer(TOKEN))
    row = next(f for f in read.json()["fields"] if f["name"] == "stt_denoise")
    return saved, row


async def test_the_setting_round_trips(box) -> None:
    saved, row = await _post(box["app"], {"stt_denoise": "spectral"})
    assert saved.status_code == 200, saved.text
    assert settings.stt_denoise == "spectral" and stt_denoise.current_mode() == "spectral"
    assert row["value"] == "spectral"
    assert "STT_DENOISE=spectral" in box["env"].read_text(encoding="utf-8")
    assert "stt_denoise" not in (saved.json().get("restart_required") or [])

    saved, row = await _post(box["app"], {"stt_denoise": "off"})
    assert saved.status_code == 200 and settings.stt_denoise == "off" and row["value"] == "off"


async def test_a_bad_mode_is_refused(box) -> None:
    saved, row = await _post(box["app"], {"stt_denoise": "rnnoise"})
    assert saved.status_code == 200
    assert saved.json()["rejected"] == {"stt_denoise": "must be one of: off, spectral"}
    assert settings.stt_denoise == "off" and row["value"] == "off"
