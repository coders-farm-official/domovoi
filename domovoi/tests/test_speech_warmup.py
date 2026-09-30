"""The voice encoder and the Piper voices load before the first turn, off
the event loop.

The first turn after a restart used to pay two loads: Resemblyzer's voice
encoder inside voice identification (identify_ms 633 against ~35 warm) and
the room's Piper voice inside the first reply (tts_first_ms 638 against
44-99). Worse, the encoder loaded synchronously ON THE EVENT LOOP
(``RealVoiceEmbedder._ensure_encoder`` ran before the worker thread), so
every room's socket stood still for it. Pinned here, with doubles for
resemblyzer and piper and no database:

* the encoder loads in a worker thread, on an embed as on a preload, and
  once between a preload and a turn racing it;
* a Piper voice loads once between a preload and a turn racing it;
* the boot warm-up loads the encoder and the default voice (only a Piper
  one), off the loop, and does nothing under stubs;
* a room's ``voice_status`` warms that room's voice, and the core's boot
  schedules the warm-up.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import types

import numpy as np
import pytest

from domovoi import speech_warmup
from domovoi.clients import tts as tts_mod
from domovoi.clients.voice_embedder import RealVoiceEmbedder
from domovoi.config import settings

PCM_2S = np.full(32_000, 800, dtype=np.int16).tobytes()


# ─── the voice encoder ───────────────────────────────────────────────────


@pytest.fixture
def fake_resemblyzer(monkeypatch):
    """A `resemblyzer` whose VoiceEncoder records the thread it was built
    on and takes a while to build, like the real one."""
    seen: dict = {"built_on": [], "embedded": 0}

    class VoiceEncoder:
        def __init__(self) -> None:
            seen["built_on"].append(threading.get_ident())
            time.sleep(0.2)

        def embed_utterance(self, wav):
            seen["embedded"] += 1
            v = np.ones(256, dtype=np.float32)
            return v / np.linalg.norm(v)

    monkeypatch.setitem(sys.modules, "resemblyzer", types.SimpleNamespace(VoiceEncoder=VoiceEncoder))
    monkeypatch.setattr(settings, "voice_profile_min_utterance_sec", 1.0)
    return seen


def test_the_first_embed_loads_the_encoder_off_the_event_loop(fake_resemblyzer) -> None:
    emb = RealVoiceEmbedder()

    async def _turn():
        loop_thread = threading.get_ident()
        # The loop keeps turning while the encoder loads: a tick every 20 ms.
        ticks = 0

        async def _ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        ticker = asyncio.create_task(_ticker())
        vec = await emb.embed(PCM_2S)
        ticker.cancel()
        return loop_thread, vec, ticks

    loop_thread, vec, ticks = asyncio.run(_turn())
    assert vec is not None and vec.shape == (256,)
    assert fake_resemblyzer["built_on"] and loop_thread not in fake_resemblyzer["built_on"]
    assert ticks >= 3          # 200 ms of load, and the loop kept turning through it


def test_a_preload_and_a_racing_turn_load_the_encoder_once(fake_resemblyzer) -> None:
    emb = RealVoiceEmbedder()

    async def _race():
        return await asyncio.gather(emb.preload(), emb.embed(PCM_2S), emb.embed(PCM_2S))

    ready, a, b = asyncio.run(_race())
    assert ready is True and a is not None and b is not None
    assert len(fake_resemblyzer["built_on"]) == 1
    # The preload ran one forward pass of its own (the warm-up).
    assert fake_resemblyzer["embedded"] == 3


def test_a_missing_resemblyzer_degrades_to_no_embedding(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "resemblyzer", None)    # import fails
    monkeypatch.setattr(settings, "voice_profile_min_utterance_sec", 1.0)
    assert asyncio.run(RealVoiceEmbedder().embed(PCM_2S)) is None


# ─── Piper voices ────────────────────────────────────────────────────────


@pytest.fixture
def fake_piper(monkeypatch, tmp_path):
    """A `piper` whose PiperVoice.load counts loads and the thread they
    ran on; the voice renders a short tone."""
    seen: dict = {"loads": [], "synth": 0}

    class _Chunk:
        sample_rate = 22_050
        audio_int16_bytes = np.full(2205, 500, dtype=np.int16).tobytes()

    class PiperVoice:
        @staticmethod
        def load(path):
            seen["loads"].append((path, threading.get_ident()))
            time.sleep(0.2)
            return PiperVoice()

        def synthesize(self, text, syn_config=None):
            seen["synth"] += 1
            yield _Chunk()

    class SynthesisConfig:
        def __init__(self, length_scale=1.0) -> None:
            self.length_scale = length_scale

    monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(
        PiperVoice=PiperVoice, SynthesisConfig=SynthesisConfig))
    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    monkeypatch.setattr(tts_mod, "_piper_voice_path", lambda ref: tmp_path / f"{ref}.onnx")
    monkeypatch.setattr(speech_warmup, "_voice_tasks", {})
    return seen


def test_a_voice_loads_once_between_a_preload_and_a_racing_reply(fake_piper) -> None:
    threads = [
        threading.Thread(target=tts_mod.preload_piper_voice_sync, args=("en_US-amy-medium",)),
        threading.Thread(target=tts_mod._synth_piper_sync, args=("hi", "en_US-amy-medium", 1.0)),
    ]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(fake_piper["loads"]) == 1
    assert tts_mod.piper_voice_loaded("en_US-amy-medium")
    assert fake_piper["synth"] == 2


@pytest.fixture
def real_mode(monkeypatch, fake_piper, fake_resemblyzer):
    """Out of stub mode for the warm-up only: the real embedder class over
    the fake resemblyzer, and a voice registry that says what it's told."""
    from domovoi.clients import voice_embedder

    monkeypatch.setattr(settings, "use_stubs", False)
    emb = RealVoiceEmbedder()
    monkeypatch.setattr(voice_embedder, "_client", emb)
    registry: dict = {None: ("piper", "en_US-lessac-medium")}

    async def _resolve(name):
        return registry.get(name, registry[None])

    monkeypatch.setattr("domovoi.streaming.resolve_voice", _resolve)
    return types.SimpleNamespace(embedder=emb, registry=registry, piper=fake_piper,
                                 encoder=fake_resemblyzer)


def test_the_boot_warm_up_loads_the_encoder_and_the_default_voice_off_the_loop(real_mode) -> None:
    async def _boot():
        await speech_warmup.warm_speech("boot")
        return threading.get_ident()

    loop_thread = asyncio.run(_boot())
    assert real_mode.embedder._encoder is not None
    assert loop_thread not in real_mode.encoder["built_on"]
    (path, load_thread), = real_mode.piper["loads"]
    assert path.endswith("en_US-lessac-medium.onnx") and load_thread != loop_thread


def test_a_room_s_own_voice_is_warmed_once(real_mode) -> None:
    real_mode.registry["Ryan"] = ("piper", "en_US-ryan-high")

    async def _connects():
        # Two satellites report the same voice at once, then one again.
        await asyncio.gather(speech_warmup.warm_room_voice("Ryan"),
                             speech_warmup.warm_room_voice("Ryan"))
        return await speech_warmup.warm_room_voice("Ryan")

    assert asyncio.run(_connects()) is True
    (path, _thread), = real_mode.piper["loads"]
    assert path.endswith("en_US-ryan-high.onnx")


def test_a_cloud_voice_is_not_preloaded(real_mode) -> None:
    real_mode.registry[None] = ("edge", "en-US-AriaNeural")
    assert asyncio.run(speech_warmup.warm_room_voice(None)) is False
    assert real_mode.piper["loads"] == []


def test_an_unregistered_voice_warms_the_tts_default(real_mode, monkeypatch) -> None:
    """Before the registry is seeded (or with the DB down) resolve_voice
    says (None, None) and a reply uses the TTS client's own default."""
    async def _nothing(name):
        return (None, None)

    monkeypatch.setattr("domovoi.streaming.resolve_voice", _nothing)
    monkeypatch.setattr(settings, "tts_engine", "piper")
    monkeypatch.setattr(settings, "tts_piper_voice", "en_GB-alan-medium")
    assert asyncio.run(speech_warmup.warm_room_voice(None)) is True
    assert real_mode.piper["loads"][0][0].endswith("en_GB-alan-medium.onnx")


def test_a_voice_that_fails_to_load_is_logged_not_raised(real_mode, monkeypatch, caplog) -> None:
    def _boom(ref):
        raise OSError("no such voice")

    monkeypatch.setattr(tts_mod, "preload_piper_voice_sync", _boom)
    assert asyncio.run(speech_warmup.warm_room_voice(None)) is False
    assert any("didn't load" in r.getMessage() for r in caplog.records)


def test_nothing_warms_under_stubs(monkeypatch) -> None:
    monkeypatch.setattr(settings, "use_stubs", True)

    async def _go():
        assert speech_warmup.schedule_speech_warm_up("boot") is None
        assert speech_warmup.schedule_room_voice_warm_up("Ryan") is None
        await speech_warmup.warm_speech("boot")

    asyncio.run(_go())


# ─── wired in ────────────────────────────────────────────────────────────


def test_boot_schedules_the_warm_up(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from domovoi.main import app

    calls: list = []
    monkeypatch.setattr(speech_warmup, "schedule_speech_warm_up", lambda reason="boot": calls.append(reason))
    with TestClient(app):
        pass
    assert calls == ["boot"]


def test_a_room_reporting_its_voice_warms_it(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from domovoi.main import app
    from domovoi.streaming import StreamSession

    async def _accept(self, ctrl):
        return True

    warmed: list = []
    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)
    monkeypatch.setattr(speech_warmup, "schedule_room_voice_warm_up", warmed.append)
    with TestClient(app) as client, client.websocket_connect("/v1/stream/kitchen") as ws:
        ws.send_text(json.dumps({"type": "hello", "room_id": "kitchen"}))
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "voice_status", "voice": "Ryan"}))
        ws.send_text(json.dumps({"type": "voice_status", "voice": ""}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
    assert warmed == ["Ryan", None]
