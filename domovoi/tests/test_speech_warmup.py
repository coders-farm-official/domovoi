"""The voice encoder and the Piper voices load before the first turn, off
the event loop.

The first turn after a restart used to pay two loads: the voice encoder
(Resemblyzer's, then) inside voice identification (identify_ms 633 against ~35 warm) and
the room's Piper voice inside the first reply (tts_first_ms 638 against
44-99). Worse, the encoder loaded synchronously ON THE EVENT LOOP
(``RealVoiceEmbedder._ensure_encoder`` ran before the worker thread), so
every room's socket stood still for it. Pinned here, with doubles for
the speaker encoder and piper and no database:

* the encoder loads in a worker thread, on an embed as on a preload, and
  once between a preload and a turn racing it; without its model file a
  turn gets no embedding at once, and the file is fetched in the
  background (never under ``INTERNET_ACCESS=never``);
* a Piper voice loads once between a preload and a turn racing it;
* the boot warm-up loads the encoder and the default voice (only a Piper
  one), off the loop, and does nothing under stubs;
* a room's ``voice_status`` warms that room's voice, and the core's boot
  schedules the warm-up.
"""

from __future__ import annotations

import asyncio
import json
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
def fake_encoder(monkeypatch):
    """A speaker encoder (domovoi/clients/speaker_encoder.py) whose model is
    on disk, that records the thread it was built on and takes a while to
    build, like the real one."""
    from domovoi.clients import speaker_encoder

    seen: dict = {"built_on": [], "embedded": 0, "threads": []}

    class VoiceEncoder:
        def __init__(self, path=None, threads=2) -> None:
            seen["built_on"].append(threading.get_ident())
            seen["threads"].append(threads)
            time.sleep(0.2)

        def embed_utterance(self, wav):
            seen["embedded"] += 1
            v = np.ones(speaker_encoder.EMBEDDING_DIM, dtype=np.float32)
            return v / np.linalg.norm(v)

    monkeypatch.setattr(speaker_encoder, "SpeakerEncoder", VoiceEncoder)
    monkeypatch.setattr(speaker_encoder, "model_on_disk", lambda path=None: True)
    monkeypatch.setattr(settings, "voice_profile_min_utterance_sec", 1.0)
    return seen


def test_the_first_embed_loads_the_encoder_off_the_event_loop(fake_encoder) -> None:
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
    assert vec is not None and vec.shape == (192,)
    assert fake_encoder["built_on"] and loop_thread not in fake_encoder["built_on"]
    assert ticks >= 3          # 200 ms of load, and the loop kept turning through it


def test_a_preload_and_a_racing_turn_load_the_encoder_once(fake_encoder) -> None:
    emb = RealVoiceEmbedder()

    async def _race():
        return await asyncio.gather(emb.preload(), emb.embed(PCM_2S), emb.embed(PCM_2S))

    ready, a, b = asyncio.run(_race())
    assert ready is True and a is not None and b is not None
    assert len(fake_encoder["built_on"]) == 1
    # The preload ran one forward pass of its own (the warm-up); each embed
    # runs one.
    assert fake_encoder["embedded"] == 3
    assert fake_encoder["threads"] == [settings.voice_profile_encoder_threads]


def test_without_its_model_a_turn_gets_no_embedding_and_the_model_is_fetched_behind_it(monkeypatch) -> None:
    """No model file: the turn isn't held for a 25 MB download. It gets no
    embedding at once (voice ID names nobody), and one fetch starts on the
    downloads executor; a second turn meanwhile starts no other."""
    from domovoi import egress
    from domovoi.clients import speaker_encoder

    fetched: list[int] = []
    gate = threading.Event()

    def download(dest_dir=None):
        fetched.append(threading.get_ident())
        gate.wait(5)
        raise speaker_encoder.ModelUnavailable("fixture: no network")

    monkeypatch.setattr(speaker_encoder, "model_on_disk", lambda path=None: False)
    monkeypatch.setattr(speaker_encoder, "download_model", download)
    monkeypatch.setattr(speaker_encoder, "_last_failure", None)
    monkeypatch.setattr(egress, "internet_turned_off", lambda: False)
    monkeypatch.setattr(settings, "voice_profile_min_utterance_sec", 1.0)
    emb = RealVoiceEmbedder()

    async def _turns():
        loop_thread = threading.get_ident()
        first = await emb.embed(PCM_2S)
        second = await emb.embed(PCM_2S)
        task = emb._fetching
        gate.set()
        ok = await task
        return loop_thread, first, second, ok

    loop_thread, first, second, ok = asyncio.run(_turns())
    assert first is None and second is None
    assert len(fetched) == 1 and fetched[0] != loop_thread
    assert ok is False


def test_without_its_model_and_with_the_internet_off_nothing_is_fetched(monkeypatch) -> None:
    from domovoi import egress
    from domovoi.clients import speaker_encoder

    def download(dest_dir=None):
        raise AssertionError("fetched with the internet turned off")

    monkeypatch.setattr(speaker_encoder, "model_on_disk", lambda path=None: False)
    monkeypatch.setattr(speaker_encoder, "download_model", download)
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    monkeypatch.setattr(settings, "voice_profile_min_utterance_sec", 1.0)
    emb = RealVoiceEmbedder()
    assert asyncio.run(emb.embed(PCM_2S)) is None
    assert emb._fetching is None
    assert asyncio.run(emb.preload()) is False


def test_an_encoder_that_fails_to_load_degrades_to_no_embedding(monkeypatch) -> None:
    from domovoi.clients import speaker_encoder

    class Broken:
        def __init__(self, *a, **k) -> None:
            raise speaker_encoder.ModelUnavailable("the speaker model gives 512 numbers, not 192")

    monkeypatch.setattr(speaker_encoder, "SpeakerEncoder", Broken)
    monkeypatch.setattr(speaker_encoder, "model_on_disk", lambda path=None: True)
    monkeypatch.setattr(settings, "voice_profile_min_utterance_sec", 1.0)
    assert asyncio.run(RealVoiceEmbedder().embed(PCM_2S)) is None


# ─── Piper voices ────────────────────────────────────────────────────────


@pytest.fixture
def fake_piper(monkeypatch, tmp_path):
    """A ``tts.PiperVoice`` that counts loads and the thread they ran on;
    the voice renders a short tone."""
    seen: dict = {"loads": [], "synth": 0}

    class PiperVoice:
        sample_rate = 22_050

        def __init__(self, path):
            seen["loads"].append((str(path), threading.get_ident()))
            time.sleep(0.2)

        def synthesize_pcm(self, sentences, speed):
            seen["synth"] += 1
            return np.full(2205, 500, dtype=np.int16).tobytes()

    monkeypatch.setattr(tts_mod, "PiperVoice", PiperVoice)
    monkeypatch.setattr(tts_mod, "_phonemize", lambda text, dialect="en": ["ˈəʊkˈeɪ."])
    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    monkeypatch.setattr(tts_mod, "_piper_voice_path", lambda ref: tmp_path / f"{ref}.onnx")
    monkeypatch.setattr(speech_warmup, "_voice_tasks", {})
    return seen


def test_a_voice_loads_once_between_a_preload_and_a_racing_reply(fake_piper) -> None:
    threads = [
        threading.Thread(target=tts_mod.preload_piper_voice_sync, args=("en_US-ljspeech-medium",)),
        threading.Thread(target=tts_mod._synth_piper_sync, args=("hi", "en_US-ljspeech-medium", 1.0)),
    ]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(fake_piper["loads"]) == 1
    assert tts_mod.piper_voice_loaded("en_US-ljspeech-medium")
    assert fake_piper["synth"] == 2


@pytest.fixture
def real_mode(monkeypatch, fake_piper, fake_encoder):
    """Out of stub mode for the warm-up only: the real embedder class over
    the fake encoder, and a voice registry that says what it's told."""
    from domovoi.clients import voice_embedder

    monkeypatch.setattr(settings, "use_stubs", False)
    emb = RealVoiceEmbedder()
    monkeypatch.setattr(voice_embedder, "_client", emb)
    registry: dict = {None: ("piper", "en_US-norman-medium")}

    async def _resolve(name):
        return registry.get(name, registry[None])

    monkeypatch.setattr("domovoi.streaming.resolve_voice", _resolve)
    return types.SimpleNamespace(embedder=emb, registry=registry, piper=fake_piper,
                                 encoder=fake_encoder)


def test_the_boot_warm_up_loads_the_encoder_and_the_default_voice_off_the_loop(real_mode) -> None:
    async def _boot():
        await speech_warmup.warm_speech("boot")
        return threading.get_ident()

    loop_thread = asyncio.run(_boot())
    assert real_mode.embedder._encoder is not None
    assert loop_thread not in real_mode.encoder["built_on"]
    (path, load_thread), = real_mode.piper["loads"]
    assert path.endswith("en_US-norman-medium.onnx") and load_thread != loop_thread


def test_a_room_s_own_voice_is_warmed_once(real_mode) -> None:
    real_mode.registry["Kristin"] = ("piper", "en_US-kristin-medium")

    async def _connects():
        # Two satellites report the same voice at once, then one again.
        await asyncio.gather(speech_warmup.warm_room_voice("Kristin"),
                             speech_warmup.warm_room_voice("Kristin"))
        return await speech_warmup.warm_room_voice("Kristin")

    assert asyncio.run(_connects()) is True
    (path, _thread), = real_mode.piper["loads"]
    assert path.endswith("en_US-kristin-medium.onnx")


def test_a_non_piper_voice_is_not_preloaded(real_mode) -> None:
    real_mode.registry[None] = ("system", "")
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
