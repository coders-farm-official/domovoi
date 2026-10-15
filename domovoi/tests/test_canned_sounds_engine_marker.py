"""B2: per-voice clips are tagged with the voice that ACTUALLY rendered them.

Before: every clip's sidecar recorded the voice it was asked for, so a
clip a fallback rung rendered (the system voice for a broken Piper model)
matched its marker and stuck forever. Now the sidecar records
``synthesize_detailed``'s real engine and voice, so such a clip is
rendered again on the first pass where the real engine works. (The
Microsoft Edge voices, and their stand-in rules, were dropped on
2026-10-12.)

DB-free: the registry and greeting bank are faked, the TTS client is the
REAL router with its two engine helpers replaced, and MP3 encoding is
replaced by a deterministic stand-in.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from domovoi import canned_sounds as cs
from domovoi import connectivity, egress
from domovoi.clients import tts as tts_mod
from domovoi.clients.tts import RealTTSClient, SynthResult
from domovoi.config import settings

PIPER_DEFAULT = "en_US-lessac-medium"
RYAN = {"id": 2, "name": "Ryan", "engine": "piper", "model_ref": "en_US-ryan-high"}
GREETINGS = [("Hey!", "generic")]


class Engines:
    """The two engine helpers, switchable per pass. ``None`` = the engine
    fails; ``"fail"`` = calling it fails the test."""

    def __init__(self) -> None:
        self.piper: object = "ok"
        self.system: object = "ok"
        self.calls: list[tuple[str, str]] = []

    def _run(self, name: str, mode: object, voice: str) -> bytes | None:
        self.calls.append((name, voice))
        if mode == "fail":
            pytest.fail(f"the {name} engine must not be called")
        if mode is None:
            return None
        # Distinct audio per engine, so a test can tell which one spoke.
        tone = {"piper": b"\x03\x04", "system": b"\x05\x06"}[name]
        return tts_mod._pcm_to_wav_bytes(tone * 400, 22_050)

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(tts_mod, "_synth_piper_sync", lambda t, v, s: self._run("piper", self.piper, v))
        monkeypatch.setattr(tts_mod, "_synth_system_sync", lambda t: self._run("system", self.system, ""))

    def taken(self) -> list[tuple[str, str]]:
        out, self.calls = self.calls, []
        return out


@pytest.fixture
def engines(monkeypatch, tmp_path):
    e = Engines()
    e.install(monkeypatch)
    # No Piper model is on disk unless a test puts one there.
    monkeypatch.setattr(settings, "voice_models_dir", str(tmp_path / "voice-models"))
    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    client = RealTTSClient(preferred_engine="piper", piper_voice=PIPER_DEFAULT, speed=1.0)
    monkeypatch.setattr(tts_mod, "_client", client)
    # MP3 encoding: keep the first bytes of the PCM so the file says which
    # engine rendered it, without needing lameenc.
    monkeypatch.setattr(cs, "_wav_to_mp3", lambda wav: b"MP3" + wav[44:48] if len(wav) > 48 else None)
    return e


@pytest.fixture
def sounds(tmp_path, monkeypatch):
    root = tmp_path / "sounds"
    monkeypatch.setattr(cs, "_SOUNDS_DIR", root)
    monkeypatch.setattr(cs, "_VOICES_ROOT", root / "voices")
    monkeypatch.setattr(cs, "_SETUP_DIR", root / "setup")
    return root


@pytest.fixture
def registry(monkeypatch):
    """A fake voices registry + greeting bank behind ``session_scope``."""
    state = {"voices": [RYAN]}

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield None

    class _Voices:
        def __init__(self, _s):
            pass

        async def all(self):
            return list(state["voices"])

    class _Greetings:
        def __init__(self, _s):
            pass

        async def all_enabled(self):
            return list(GREETINGS)

    monkeypatch.setattr(cs, "session_scope", fake_scope)
    monkeypatch.setattr(cs, "VoicesRepository", _Voices)
    monkeypatch.setattr(cs, "ClientGreetingsRepository", _Greetings)
    return state


class _Probe:
    def __init__(self, online: bool) -> None:
        self.online = online


@pytest.fixture
def probe(monkeypatch):
    """Sets the process-wide probe; restored after the test."""
    monkeypatch.setattr(connectivity, "_current_probe", None)

    def set_online(online: bool | None) -> None:
        connectivity.set_current_probe(None if online is None else _Probe(online))

    return set_online


def _clips(root: Path, voice: str) -> dict[str, str]:
    """``{clip name: first sidecar line}`` for one voice."""
    vdir = root / "voices" / voice
    out = {}
    for sidecar in sorted(vdir.rglob("*.voice")):
        out[sidecar.relative_to(vdir).as_posix()] = sidecar.read_text(encoding="utf-8").splitlines()[0]
    return out


def _clip_count() -> int:
    return len(cs._CANNED) + 1 + len(GREETINGS)  # canned + sample + greetings


# ─── internet_usable ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("answer", "probe_state", "usable"),
    [
        ("", None, True),
        ("", True, True),
        ("", False, False),
        ("always", True, True),
        ("always", False, False),
        ("sometimes", None, True),
        ("never", None, False),
        ("never", True, False),
    ],
)
def test_internet_usable(probe, answer, probe_state, usable):
    """Whether a Piper voice not on disk yet could be fetched."""
    probe(probe_state)
    with egress.override_policy(answer):
        assert cs.internet_usable() is usable
    assert not hasattr(cs, "edge_usable")


# ─── A Piper voice rendered by the wrong rung ──────────────────────────────


async def test_a_broken_piper_voice_is_rendered_again_once_it_works(engines, sounds, registry, probe):
    registry["voices"] = [RYAN]
    with egress.override_policy("always"):
        probe(True)
        engines.piper = None  # Ryan's model is missing / broken
        await cs.regenerate_if_needed()
        assert set(_clips(sounds, "ryan").values()) == {"system|"}

        engines.piper = "ok"
        engines.taken()
        await cs.regenerate_if_needed()
        assert engines.taken() == [("piper", "en_US-ryan-high")] * _clip_count()
        assert set(_clips(sounds, "ryan").values()) == {"piper|en_US-ryan-high"}

        await cs.regenerate_if_needed()
        assert engines.taken() == []


async def test_a_piper_voice_not_on_disk_under_never_is_not_re_rendered_every_pass(
    engines, sounds, registry, probe, tmp_path
):
    registry["voices"] = [RYAN]
    engines.piper = None  # what the real rung does: no model, download refused
    probe(None)
    with egress.override_policy("never"):
        # Pass 1: the clips are missing, so they render — the system voice
        # speaks, and the sidecars say so.
        await cs.regenerate_if_needed()
        assert {c[0] for c in engines.taken()} == {"piper", "system"}
        assert set(_clips(sounds, "ryan").values()) == {"system|"}

        # Pass 2 (the next boot): the model still can't be fetched, so the
        # system-voice clips are kept rather than rendered again.
        await cs.regenerate_if_needed()
        assert engines.taken() == []

        # The model is copied onto the box: the next pass renders in Ryan.
        models = tmp_path / "voice-models"
        models.mkdir(parents=True, exist_ok=True)
        (models / "en_US-ryan-high.onnx").write_bytes(b"m")
        # (a config on a phoneme set the converter writes: one on another
        # set could never speak, and would keep its system-voice clips)
        (models / "en_US-ryan-high.onnx.json").write_text(
            '{"espeak": {"voice": "en"}, "phoneme_type": "espeak", "phoneme_id_map": {"_": [0]}}')
        engines.piper = "ok"
        await cs.regenerate_if_needed()
        assert engines.taken() == [("piper", "en_US-ryan-high")] * _clip_count()
        assert set(_clips(sounds, "ryan").values()) == {"piper|en_US-ryan-high"}


async def test_a_render_where_every_rung_failed_writes_nothing(engines, sounds, registry, probe):
    registry["voices"] = [RYAN]
    engines.piper = None
    engines.system = None
    probe(True)
    await cs.regenerate_if_needed()
    assert _clips(sounds, "ryan") == {}
    assert not list((sounds / "voices" / "ryan").rglob("*.mp3"))


# ─── Setup clips (WAV, in the household's default voice) ──────────────────


@pytest.mark.parametrize("engine", ["piper", "edge"])
async def test_setup_clips_for_a_piper_household_are_tagged_piper(engines, sounds, probe, monkeypatch, engine):
    """An old TTS_ENGINE=edge household gets them in its Piper voice too."""
    monkeypatch.setattr(settings, "tts_engine", engine)
    monkeypatch.setattr(settings, "tts_piper_voice", PIPER_DEFAULT)
    with egress.override_policy("always"):
        written, _ = await cs.render_setup_clips()
    assert written == len(cs.SETUP_LINES)
    marks = {p.read_text(encoding="utf-8").splitlines()[0] for p in (sounds / "setup").glob("*.voice")}
    assert marks == {f"piper|{PIPER_DEFAULT}"}


# ─── The marker helpers ────────────────────────────────────────────────────


async def test_a_client_without_synthesize_detailed_keeps_the_requested_marker(sounds, monkeypatch):
    class _OldDouble:
        async def synthesize(self, text, *, engine=None, voice=None):
            return tts_mod._pcm_to_wav_bytes(b"\x07\x08" * 100, 16_000)

    monkeypatch.setattr(tts_mod, "_client", _OldDouble())
    monkeypatch.setattr(cs, "_wav_to_mp3", lambda wav: b"MP3")
    out = await cs._synth_clip_mp3("hi", "piper", "en_US-ryan-high")
    assert out == (b"MP3", "piper|en_US-ryan-high")


async def test_synth_clip_mp3_returns_the_actual_marker(monkeypatch):
    class _Detailed:
        async def synthesize_detailed(self, text, *, engine=None, voice=None):
            return SynthResult(tts_mod._pcm_to_wav_bytes(b"\x01\x01" * 100, 16_000), "system", None)

    monkeypatch.setattr(tts_mod, "_client", _Detailed())
    monkeypatch.setattr(cs, "_wav_to_mp3", lambda wav: b"MP3")
    assert await cs._synth_clip_mp3("hi", "piper", "en_US-ryan-high") == (b"MP3", "system|")
    wav, marker = await cs._synth_clip_wav("hi", "piper", "en_US-ryan-high")
    assert marker == "system|" and wav.startswith(b"RIFF")


def test_needs_regen_is_unchanged_by_the_actual_marker(tmp_path):
    mp3, sidecar = tmp_path / "a.mp3", tmp_path / "a.voice"
    mp3.write_bytes(b"x")
    sidecar.write_text(f"piper|{PIPER_DEFAULT}\n{cs._hash('hello')}\n", encoding="utf-8")
    # A stand-in disagrees with its own voice's marker, agrees with its renderer's.
    assert cs._needs_regen(mp3, sidecar, "piper|en_US-ryan-high", "hello") is True
    assert cs._needs_regen(mp3, sidecar, f"piper|{PIPER_DEFAULT}", "hello") is False
