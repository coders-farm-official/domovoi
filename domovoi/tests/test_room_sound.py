"""Rooms slice 5b: every room sound through the synced playout
(domovoi/room_sound.py, docs/ROOM_AUDIO_PROTOCOL.md).

Two fake speakers in one room, a fake clock, and the room's music from a
fixed buffer in place of the MPD stream. The satellite ids never equal
their room's, so a mix-up between a device and its room shows up.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from domovoi import room_devices, room_sound, streaming
from domovoi.clients import mpd as mpd_client
from domovoi.room_playout import protocol as P
from domovoi.room_playout.service import RoomPlayout
from domovoi.satellite_abilities import Abilities
from domovoi.streaming import StreamSession

SYNCED = Abilities(declared=True, names=frozenset({
    "wake_claim", "capture_control", "speech_pause", "room_audio",
    "announce_after_session_end",
}))
OLD = Abilities(declared=True, names=frozenset({
    "capture_control", "speech_pause", "announce_after_session_end",
}))
URL = "http://192.168.1.20:8051"


class FakeClock:
    def __init__(self) -> None:
        self.t = 1_000_000_000.0

    def __call__(self) -> int:
        return int(self.t)


class Sink:
    def __init__(self) -> None:
        self.audio: list[P.AudioFrame] = []
        self.controls: list[dict] = []

    def send_audio(self, frame: bytes) -> None:
        self.audio.append(P.decode_audio(frame))

    def send_control(self, msg: dict) -> None:
        self.controls.append(msg)

    def of(self, kind: str) -> list[dict]:
        return [c for c in self.controls if c["type"] == kind]


class WS:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.sent: list[dict] = []
        self.bytes: list[bytes] = []
        self.client = SimpleNamespace(host="192.168.1.41")
        self.headers: dict[str, str] = {}

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.bytes.append(data)

    async def close(self, code: int = 1000) -> None:
        pass

    def of(self, kind: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == kind]


class LiveSource:
    """The room's MPD stream as the decoder sees it: endless, at playback
    rate. A read of everything (the streamer forgetting stale audio after a
    pause or a skip) is counted in ``drops``."""

    def __init__(self) -> None:
        self.pos = 0
        self.drops = 0
        self.closed = False

    @property
    def active(self) -> bool:
        return not self.closed

    @property
    def started(self) -> bool:
        return True

    def read(self, n: int) -> bytes:
        if n >= 1 << 20:
            self.drops += 1
            return b""
        frames = n // P.BYTES_PER_FRAME
        t = (self.pos + np.arange(frames)) / P.RATE
        self.pos += frames
        return (np.stack([np.sin(2 * np.pi * 220 * t)] * 2, axis=1) * 8000).astype("<i2").tobytes()

    def close(self) -> None:
        self.closed = True


class House:
    """A room's devices, the playout, and what each speaker received."""

    def __init__(self, devices: list[tuple[str, str, Abilities, bool]]) -> None:
        state = SimpleNamespace(
            satellite_config={}, active_sessions={}, satellite_voice={},
            resumable_music={}, pending_music_start={},
        )
        self.app = SimpleNamespace(state=state)
        self.clock = FakeClock()
        self.sources: list[LiveSource] = []

        async def source(room_id: str) -> LiveSource:
            src = LiveSource()
            self.sources.append(src)
            return src

        reg = room_devices.registry(self.app)
        self.pl = RoomPlayout(reg, clock=self.clock, source_for_room=source, run_tasks=False)
        state.room_playout = self.pl
        self.s: dict[str, StreamSession] = {}
        self.sinks: dict[str, Sink] = {}
        for sid, room, abilities, attached in devices:
            sess = StreamSession(WS(self.app), sid)  # type: ignore[arg-type]
            sess.room_id = room
            sess.abilities = abilities
            sess._capture_control = True
            reg.register(sess)
            self.s[sid] = sess
            if attached:
                self.sinks[sid] = Sink()
                self.pl.attach(sid, self.sinks[sid])

    def run(self, ms: float) -> None:
        """Let ``ms`` of server time pass, the streamers ticking every 10 ms."""
        for _ in range(int(ms // 10)):
            self.clock.t += 10_000
            for st in list(self.pl.streamers.values()):
                st.tick()


@pytest.fixture(autouse=True)
def _quiet_mpd(monkeypatch):
    async def serving(*a: Any, **kw: Any) -> None:
        return None

    monkeypatch.setattr(mpd_client, "ensure_stream_serving", serving)
    saved = dict(mpd_client._clients)
    # Every test gets its own stub players: a client another test left for
    # "kitchen" may not tell the playout of a pause.
    mpd_client._clients.clear()
    yield
    mpd_client._clients.clear()
    mpd_client._clients.update(saved)


def kitchen(**kw: Any) -> House:
    return House([("k_sat", "kitchen", SYNCED, True), ("k_phone", "kitchen", SYNCED, True)], **kw)


async def start(house: House, sid: str = "k_sat") -> bool:
    return await streaming.send_music_start(house.app, house.s[sid], "kitchen", URL)


# ─── music ────────────────────────────────────────────────────────────────


async def test_music_plays_on_both_speakers_in_sync_with_no_music_start() -> None:
    h = kitchen()
    assert await start(h) is True
    assert not h.s["k_sat"].ws.of("music_start") and not h.s["k_phone"].ws.of("music_start")
    assert "kitchen" not in h.app.state.pending_music_start   # no music_ready to wait for
    h.run(800)
    a, b = h.sinks["k_sat"].audio, h.sinks["k_phone"].audio
    assert len(a) > 20 and a == b                  # the same chunks, the same play times
    # Every chunk is sent about playout_ms before it plays.
    assert all(f.play_at_us - h.clock.t <= h.pl.playout_ms * 1000 for f in a[-3:])
    assert h.pl.music_host["kitchen"] == "kitchen"


async def test_a_turns_auto_resume_does_not_restart_the_stream() -> None:
    h = kitchen()
    await start(h)
    await start(h)
    assert len(h.sources) == 1


async def test_a_plain_stream_moves_to_the_playout_once_the_speaker_joins() -> None:
    h = House([("k_sat", "kitchen", SYNCED, False)])
    await start(h)                                       # room-audio socket not open yet
    assert h.s["k_sat"].ws.of("music_start")[0]["stream_url"]
    assert h.s["k_sat"].plain_music_url
    h.sinks["k_sat"] = Sink()
    h.pl.attach("k_sat", h.sinks["k_sat"])
    await start(h)
    assert h.s["k_sat"].ws.sent[-1] == {"type": "music_stop"}   # its own player stops
    assert h.s["k_sat"].plain_music_url is None
    assert h.pl.playing_music("kitchen")


# ─── timed pause, skip, volume ────────────────────────────────────────────


async def test_pause_resume_and_skip_are_timed_on_every_speaker() -> None:
    h = kitchen()
    await start(h)
    h.run(600)
    player = mpd_client.get_mpd_client_for("kitchen")
    await player.pause()
    flushes = [s.of("flush")[-1] for s in h.sinks.values()]
    assert flushes[0] == flushes[1] and flushes[0]["at_us"] > h.clock.t
    sent = len(h.sinks["k_sat"].audio)
    h.run(600)
    assert len(h.sinks["k_sat"].audio) == sent                     # silent while paused
    await player.resume()
    h.run(700)
    assert len(h.sinks["k_sat"].audio) > sent
    assert h.sinks["k_sat"].audio == h.sinks["k_phone"].audio
    # A skip: what was sent of the old song stops at one time, the new one
    # carries on from there (the decoder's old audio dropped).
    before = len(h.sinks["k_sat"].of("flush"))
    mpd_client._tell_playout("kitchen", "skip")
    skip = [s.of("flush")[-1] for s in h.sinks.values()]
    assert len(h.sinks["k_sat"].of("flush")) == before + 1 and skip[0] == skip[1]
    st = h.pl.streamers["kitchen"]
    assert st.play_at(st.next_index) >= skip[0]["at_us"]
    assert st.music.drops == 2  # type: ignore[union-attr]  # the resume's and the skip's


async def test_a_shared_queue_hosts_pause_pauses_its_listening_room(monkeypatch) -> None:
    from domovoi import music_groups

    h = House([("k_sat", "kitchen", SYNCED, True), ("d_sat", "den", SYNCED, True)])
    monkeypatch.setattr(music_groups, "host_of", lambda r: "den" if r == "kitchen" else r)
    await start(h)
    assert h.pl.music_host["kitchen"] == "den"
    assert h.pl.music_transport("den", "pause") == ["kitchen"]
    assert h.sinks["k_sat"].of("flush")


async def test_volume_changes_together_on_every_device_of_the_room() -> None:
    h = kitchen()
    await room_sound.set_room_volume(h.app, h.s["k_sat"], 40)
    a, b = h.s["k_sat"].ws.of("set_volume"), h.s["k_phone"].ws.of("set_volume")
    assert a[0]["level"] == b[0]["level"] == 40
    assert a[0]["at_us"] == b[0]["at_us"] > 0


# ─── speech: timers, alarms, announcements ────────────────────────────────


class TTS:
    """Half a second of a tone at 22.05 kHz for any sentence."""

    async def synthesize(self, text: str, *, engine: Any = None, voice: Any = None) -> bytes:
        import io
        import wave

        t = np.arange(11025) / 22050
        pcm = (np.sin(2 * np.pi * 300 * t) * 6000).astype("<i2").tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(pcm)
        return buf.getvalue()


@pytest.fixture
def no_quiet_hours(monkeypatch):
    from domovoi import quiet_hours

    async def never(*a: Any, **kw: Any) -> bool:
        return False

    async def voice(_name: Any) -> tuple[None, None]:
        return (None, None)

    monkeypatch.setattr(quiet_hours, "holds_back", never)
    monkeypatch.setattr(streaming, "get_tts_client", lambda: TTS())
    monkeypatch.setattr(streaming, "resolve_voice", voice)


async def test_a_timer_rings_on_every_speaker(no_quiet_hours) -> None:
    h = kitchen()
    await h.s["k_sat"].announce("Your pasta timer is done.", defer_to_capture=True, kind="alarm")
    owner = h.s["k_sat"].ws
    assert owner.of("response_start")[0]["room_audio"] is True
    assert owner.bytes == []
    assert owner.of("response_end")[-1]["play_end_us"] > 0
    assert h.sinks["k_sat"].of("speech") and h.sinks["k_phone"].of("speech")
    h.run(1500)
    a, b = h.sinks["k_sat"].audio, h.sinks["k_phone"].audio
    assert a and a == b
    assert any(np.frombuffer(f.payload, "<i2").any() for f in a)


async def test_a_plugin_announcement_plays_on_every_speaker(no_quiet_hours, monkeypatch) -> None:
    from domovoi.sdk import speech

    h = kitchen()
    monkeypatch.setattr(speech, "_active_sessions_provider", lambda: h.app.state.active_sessions)
    assert await speech.SpeechAPI("demo").announce("kitchen", "The laundry is done.") == ["kitchen"]
    h.run(1500)
    assert h.sinks["k_sat"].audio and h.sinks["k_sat"].audio == h.sinks["k_phone"].audio
    assert h.s["k_phone"].ws.bytes == [] and h.s["k_sat"].ws.bytes == []


async def test_music_stop_keeps_an_answer_playing() -> None:
    h = kitchen()
    await start(h)
    h.run(600)
    st = h.pl.streamers["kitchen"]
    start_us, end_us = h.pl.say("kitchen", b"\x10\x00" * 16000, 16000)   # 1 s of speech
    await h.s["k_sat"]._safe_send_text({"type": "music_stop"})
    assert st.music is None and st._speech
    for sink in h.sinks.values():
        flush = sink.of("flush")[-1]
        # The flush cut the answer's span; it is told again from the flush on.
        assert sink.of("speech")[-1]["end_us"] == int(end_us)
        assert sink.of("speech")[-1]["start_us"] >= flush["at_us"] - 1
    h.run(1500)
    assert h.sinks["k_sat"].audio[-1].play_at_us < end_us + 20_000


# ─── voice turns ──────────────────────────────────────────────────────────


@pytest.fixture
def turns(monkeypatch):
    seen: list[str] = []

    async def fake_turn(self, pcm, **kw):
        seen.append(self.satellite_id)

    monkeypatch.setattr(StreamSession, "_process_utterance_turn", fake_turn)
    monkeypatch.setattr(streaming.settings, "speculative_stt_enabled", False, raising=False)
    return seen


async def test_music_falls_silent_for_a_turn_and_returns_under_the_answer(turns) -> None:
    h = kitchen()
    await start(h)
    h.run(600)
    await h.s["k_sat"]._on_control({"type": "utterance_start", "trigger": "wake_word", "utt": 1})
    for sink in h.sinks.values():
        assert sink.of("gain")[-1]["gain"] == room_sound.LISTEN_GAIN
    assert h.pl.playing_music("kitchen")                 # the timeline runs on
    owner = h.s["k_sat"]
    await owner._safe_send_text({"type": "response_start", "text": "It is noon.",
                                 "audio_sample_rate": 16000})
    await owner._send_pcm(b"\x10\x00" * 3200)
    start_us = h.pl.streamers["kitchen"]._speech[0][0]
    back = [s.of("gain")[-1] for s in h.sinks.values()]
    assert back[0] == back[1] and back[0]["gain"] == 1.0
    assert back[0]["at_us"] == int(h.pl.streamers["kitchen"].play_at(start_us))


async def test_a_lone_room_holds_its_music_for_the_turn_and_resumes_after(turns) -> None:
    h = House([("den_sat", "den", SYNCED, True)])
    assert await streaming.send_music_start(h.app, h.s["den_sat"], "den", URL)
    h.run(600)
    sess = h.s["den_sat"]
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word", "utt": 1})
    assert h.sinks["den_sat"].of("gain")[-1]["gain"] == room_sound.LISTEN_GAIN
    # A lone room answers on its own socket (no playout delay)...
    await sess._safe_send_text({"type": "response_start", "text": "Hi.", "audio_sample_rate": 16000})
    assert "room_audio" not in sess.ws.sent[-1]
    await sess._send_pcm(b"\1\0" * 10)
    assert sess.ws.bytes == [b"\1\0" * 10]
    # ...and the turn's music decision brings the music back.
    await streaming.send_music_start(h.app, sess, "den", URL)
    assert h.sinks["den_sat"].of("gain")[-1]["gain"] == 1.0
    assert len(h.sources) == 1


async def test_a_barge_in_claim_cuts_the_answer_on_every_speaker_music_plays_on() -> None:
    h = kitchen()
    await start(h)
    h.run(600)
    owner = h.s["k_sat"]
    await owner._safe_send_text({"type": "response_start", "text": "A long answer.",
                                 "audio_sample_rate": 16000})
    await owner._send_pcm(b"\x10\x00" * 32000)
    st = h.pl.streamers["kitchen"]
    assert st._speech
    await h.s["k_phone"]._on_control({"type": "wake_claim", "claim": 4, "word": "hey_jarvis",
                                      "score": 0.8, "floor_dbfs": -40.0, "barge_in": True})
    assert st._speech == []
    assert all(s.of("flush") for s in h.sinks.values())
    assert st.music is not None and st.next_index is not None      # music plays on


# ─── single rooms and older devices ───────────────────────────────────────


async def test_an_older_lone_device_keeps_its_own_stream_player() -> None:
    h = House([("den", "den", OLD, False)])
    assert await streaming.send_music_start(h.app, h.s["den"], "den", URL)
    assert h.s["den"].ws.of("music_start")[0]["stream_url"]
    assert "den" in h.app.state.pending_music_start      # the music_ready handshake
    h.app.state.pending_music_start["den"]["task"].cancel()
    assert not h.pl.streamers
    await room_sound.set_room_volume(h.app, h.s["den"], 30)
    assert h.s["den"].ws.of("set_volume") == [{"type": "set_volume", "level": 30}]


async def test_older_devices_in_a_shared_room_with_no_synced_speaker_keep_the_old_path() -> None:
    h = House([("k_old", "kitchen", OLD, False), ("k_old2", "kitchen", OLD, False)])
    assert await start(h, "k_old")
    assert h.s["k_old"].ws.of("music_start")
    h.app.state.pending_music_start["kitchen"]["task"].cancel()
    await h.s["k_old"]._safe_send_text({"type": "response_start", "text": "Hi.",
                                        "audio_sample_rate": 16000})
    assert "room_audio" not in h.s["k_old"].ws.sent[-1]


async def test_an_older_device_beside_a_synced_one_stays_silent() -> None:
    h = House([("k_old", "kitchen", OLD, False), ("k_sat", "kitchen", SYNCED, True)])
    assert await start(h, "k_old")
    assert not h.s["k_old"].ws.of("music_start")         # it is not the only speaker
    assert h.pl.playing_music("kitchen")
    await h.s["k_old"]._safe_send_text({"type": "response_start", "text": "Hi.",
                                        "audio_sample_rate": 16000})
    assert h.s["k_old"].ws.sent[-1]["room_audio"] is True


async def test_a_bridged_device_s_music_opt_in_gets_the_plain_stream() -> None:
    h = House([("k_sat", "kitchen", SYNCED, True), ("k_bridge", "kitchen", OLD, False)])
    h.s["k_bridge"].unsynced_music_opt_in = True
    await start(h)
    assert h.s["k_bridge"].ws.of("music_start")
    assert not h.s["k_sat"].ws.of("music_start")
