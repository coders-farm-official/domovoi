"""B2: per-voice clips are tagged with the voice that ACTUALLY rendered them.

Before: every clip's sidecar recorded the voice it was asked for, so a
clip a fallback rung rendered (Piper standing in for an unreachable
Microsoft Edge voice after an offline first boot, the system voice for a
broken Piper model) matched its marker and stuck forever. Now the sidecar
records ``synthesize_detailed``'s real engine and voice, so such a clip is
rendered again on the first pass where the real engine works.

And Edge clips are never attempted while Edge isn't usable (the internet
answer is ``never``, or the probe says offline): a missing clip gets a
Piper stand-in at once, an existing one is kept.

DB-free: the registry and greeting bank are faked, the TTS client is the
REAL router with its three engine helpers replaced, and MP3 encoding is
replaced by a deterministic stand-in.
"""

from __future__ import annotations

import contextlib
import sys
import types
from pathlib import Path

import pytest

from domovoi import canned_sounds as cs
from domovoi import connectivity, egress
from domovoi.clients import tts as tts_mod
from domovoi.clients.tts import RealTTSClient, SynthResult
from domovoi.config import settings

PIPER_DEFAULT = "en_US-lessac-medium"
GUY = {"id": 1, "name": "Guy", "engine": "edge", "model_ref": "en-US-GuyNeural"}
RYAN = {"id": 2, "name": "Ryan", "engine": "piper", "model_ref": "en_US-ryan-high"}
GREETINGS = [("Hey!", "generic")]


class Engines:
    """The three engine helpers, switchable per pass. ``None`` = the engine
    fails; ``"fail"`` = calling it fails the test."""

    def __init__(self) -> None:
        self.edge: object = None
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
        tone = {"edge": b"\x01\x02", "piper": b"\x03\x04", "system": b"\x05\x06"}[name]
        return tts_mod._pcm_to_wav_bytes(tone * 400, 22_050)

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(tts_mod, "_synth_edge_sync", lambda t, v, s: self._run("edge", self.edge, v))
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
    client = RealTTSClient(
        preferred_engine="piper", edge_voice="en-US-AriaNeural",
        piper_voice=PIPER_DEFAULT, speed=1.0,
    )
    monkeypatch.setattr(tts_mod, "_client", client)
    # MP3 encoding: keep the first bytes of the PCM so the file says which
    # engine rendered it, without needing lameenc.
    monkeypatch.setattr(cs, "_wav_to_mp3", lambda wav: b"MP3" + wav[44:48] if len(wav) > 48 else None)
    # edge_tts must never be reached by anything in here.
    fake = types.ModuleType("edge_tts")
    fake.Communicate = lambda *a, **k: pytest.fail("edge_tts.Communicate must not be called")
    monkeypatch.setitem(sys.modules, "edge_tts", fake)
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
    state = {"voices": [GUY]}

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


# ─── edge_usable ───────────────────────────────────────────────────────────


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
def test_edge_usable(probe, answer, probe_state, usable):
    probe(probe_state)
    with egress.override_policy(answer):
        assert cs.edge_usable() is usable


# ─── An Edge voice across offline and online passes ────────────────────────


async def test_an_edge_voice_offline_gets_piper_stand_ins_then_edge_once_online(
    engines, sounds, registry, probe
):
    with egress.override_policy("always"):
        # Pass 1: the box is offline. No Edge attempt at all — the stand-in
        # is the default Piper voice, and the sidecar SAYS Piper.
        probe(False)
        engines.edge = "fail"
        await cs.regenerate_if_needed()
        assert engines.taken() == [("piper", PIPER_DEFAULT)] * _clip_count()
        marks = _clips(sounds, "guy")
        assert len(marks) == _clip_count()
        assert set(marks.values()) == {f"piper|{PIPER_DEFAULT}"}

        # Pass 2, still offline: the stand-ins are current text, so they
        # are kept — nothing renders.
        await cs.regenerate_if_needed()
        assert engines.taken() == []

        # Pass 3: online. Every stand-in disagrees with the voice's marker
        # and is rendered again, in Edge.
        probe(True)
        engines.edge = "ok"
        await cs.regenerate_if_needed()
        assert engines.taken() == [("edge", "en-US-GuyNeural")] * _clip_count()
        assert set(_clips(sounds, "guy").values()) == {"edge|en-US-GuyNeural"}
        sample = (sounds / "voices" / "guy" / "sample.mp3").read_bytes()
        assert sample == b"MP3" + b"\x01\x02\x01\x02"  # Edge's audio

        # Pass 4: up to date.
        await cs.regenerate_if_needed()
        assert engines.taken() == []


async def test_under_never_edge_is_never_called_and_stand_ins_stay(engines, sounds, registry, probe):
    engines.edge = "fail"
    probe(True)  # the probe's own view doesn't matter under never
    with egress.override_policy("never"):
        await cs.regenerate_if_needed()
        await cs.regenerate_if_needed()
    assert engines.taken() == [("piper", PIPER_DEFAULT)] * _clip_count()
    assert set(_clips(sounds, "guy").values()) == {f"piper|{PIPER_DEFAULT}"}


async def test_an_existing_edge_clip_is_kept_while_edge_is_unusable(engines, sounds, registry, probe):
    # A real Edge render from an earlier, online boot.
    with egress.override_policy("always"):
        probe(True)
        engines.edge = "ok"
        await cs.regenerate_if_needed()
        engines.taken()
        before = (sounds / "voices" / "guy" / "network_issues.mp3").read_bytes()

        # Offline now: nothing to do, the Edge clips are kept as they are.
        probe(False)
        engines.edge = "fail"
        await cs.regenerate_if_needed()
    assert engines.taken() == []
    assert (sounds / "voices" / "guy" / "network_issues.mp3").read_bytes() == before
    assert set(_clips(sounds, "guy").values()) == {"edge|en-US-GuyNeural"}


async def test_a_changed_text_gets_a_stand_in_while_edge_is_unusable(
    engines, sounds, registry, probe, monkeypatch
):
    with egress.override_policy("always"):
        probe(True)
        engines.edge = "ok"
        await cs.regenerate_if_needed()
        engines.taken()

        # The bot is renamed while the box is offline: the sample's text
        # changed, so the sample gets a Piper stand-in in the NEW words
        # rather than keep saying the old name until Edge comes back.
        monkeypatch.setattr(settings, "bot_name", "Kotofey")
        probe(False)
        engines.edge = "fail"
        await cs.regenerate_if_needed()
        assert engines.taken() == [("piper", PIPER_DEFAULT)]
        marks = _clips(sounds, "guy")
        assert marks["sample.voice"] == f"piper|{PIPER_DEFAULT}"
        assert marks["network_issues.voice"] == "edge|en-US-GuyNeural"


async def test_edge_failing_while_online_is_tagged_and_retried(engines, sounds, registry, probe):
    # Edge looks usable but the render fails (a Microsoft hiccup): the
    # router falls back to Piper, and the clip says so.
    with egress.override_policy("always"):
        probe(True)
        engines.edge = None
        await cs.regenerate_if_needed()
        taken = engines.taken()
        assert ("edge", "en-US-GuyNeural") in taken
        assert set(_clips(sounds, "guy").values()) == {f"piper|{PIPER_DEFAULT}"}

        engines.edge = "ok"
        await cs.regenerate_if_needed()
        assert set(_clips(sounds, "guy").values()) == {"edge|en-US-GuyNeural"}


# ─── A Piper voice rendered by the wrong rung ──────────────────────────────


async def test_a_broken_piper_voice_is_rendered_again_once_it_works(engines, sounds, registry, probe):
    registry["voices"] = [RYAN]
    engines.edge = "fail"
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
    engines.edge = "fail"
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
        (models / "en_US-ryan-high.onnx.json").write_text("{}")
        engines.piper = "ok"
        await cs.regenerate_if_needed()
        assert engines.taken() == [("piper", "en_US-ryan-high")] * _clip_count()
        assert set(_clips(sounds, "ryan").values()) == {"piper|en_US-ryan-high"}


async def test_a_render_where_every_rung_failed_writes_nothing(engines, sounds, registry, probe):
    registry["voices"] = [RYAN]
    engines.edge = "fail"
    engines.piper = None
    engines.system = None
    probe(True)
    await cs.regenerate_if_needed()
    assert _clips(sounds, "ryan") == {}
    assert not list((sounds / "voices" / "ryan").rglob("*.mp3"))


# ─── Setup clips (WAV, in the household's default voice) ──────────────────


async def test_setup_clips_for_an_edge_household_under_never(engines, sounds, probe, monkeypatch):
    monkeypatch.setattr(settings, "tts_engine", "edge")
    monkeypatch.setattr(settings, "tts_edge_voice", "en-US-GuyNeural")
    engines.edge = "fail"
    probe(None)
    with egress.override_policy("never"):
        written, problems = await cs.render_setup_clips()
    assert problems == []
    assert written == len(cs.SETUP_LINES)
    marks = {p.name: p.read_text(encoding="utf-8").splitlines()[0]
             for p in (sounds / "setup").glob("*.voice")}
    assert set(marks.values()) == {f"piper|{PIPER_DEFAULT}"}
    assert {c[0] for c in engines.taken()} == {"piper"}

    # The internet answer changes: the stand-ins are rendered again in Edge.
    engines.edge = "ok"
    with egress.override_policy("always"):
        written, problems = await cs.render_setup_clips()
    assert written == len(cs.SETUP_LINES) and problems == []
    marks = {p.name: p.read_text(encoding="utf-8").splitlines()[0]
             for p in (sounds / "setup").glob("*.voice")}
    assert set(marks.values()) == {"edge|en-US-GuyNeural"}
    with egress.override_policy("always"):
        assert await cs.render_setup_clips() == (0, [])


async def test_setup_clips_for_a_piper_household_are_tagged_piper(engines, sounds, probe, monkeypatch):
    monkeypatch.setattr(settings, "tts_engine", "piper")
    monkeypatch.setattr(settings, "tts_piper_voice", PIPER_DEFAULT)
    engines.edge = "fail"
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
    wav, marker = await cs._synth_clip_wav("hi", "edge", "en-US-GuyNeural")
    assert marker == "system|" and wav.startswith(b"RIFF")


def test_needs_regen_is_unchanged_by_the_actual_marker(tmp_path):
    mp3, sidecar = tmp_path / "a.mp3", tmp_path / "a.voice"
    mp3.write_bytes(b"x")
    sidecar.write_text(f"piper|{PIPER_DEFAULT}\n{cs._hash('hello')}\n", encoding="utf-8")
    # A stand-in disagrees with its Edge voice's marker, agrees with Piper's.
    assert cs._needs_regen(mp3, sidecar, "edge|en-US-GuyNeural", "hello") is True
    assert cs._needs_regen(mp3, sidecar, f"piper|{PIPER_DEFAULT}", "hello") is False
