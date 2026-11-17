"""The remote speech-to-text backend (domovoi/clients/whisper_remote.py).

On a Mac the core's VM has no GPU; the Mac runs a whisper.cpp server on its
own loopback (mac-installer Phase 8a) and ``whisper_remote_url`` points the
core at it. What is pinned here:

* off by default: with no URL nothing changes, ``stt_status`` and
  ``whisper_runtime`` are exactly what they were;
* only a machine on this network is accepted, never one with credentials;
* the two decode paths are kept: a short English command goes to the 10 s
  window (``audio_ctx`` 500, no temperature fallback, no timestamps) and is
  judged by the same verdict as the local short window; anything doubtful,
  long, multilingual or with the setting off takes the full window;
* a failed request is heard by the local Whisper, the service is left
  alone for the retry period, then tried again, and the status says which
  is hearing the turns (never silent);
* with no local Whisper either, the turn gets SttUnavailableError (the
  streaming layer's spoken explanation).

No network: every request goes to an ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import io
import math
import wave

import httpx
import pytest

from domovoi.clients import whisper as whisper_mod
from domovoi.clients import whisper_remote as wr
from domovoi.clients.whisper import SttUnavailableError
from domovoi.config import settings

URL = "http://host.lima.internal:6371"


def _pcm(seconds: float) -> bytes:
    return bytes(int(seconds * 16000) * 2)


def _verbose(text: str, *, avg_logprob: float = -0.2, no_speech: float = 0.01, tokens: int = 5) -> dict:
    return {"task": "transcribe", "language": "english", "text": text,
            "segments": [{"id": 0, "text": f" {text}", "tokens": list(range(tokens)),
                          "avg_logprob": avg_logprob, "no_speech_prob": no_speech}]}


def _fields(request: httpx.Request) -> dict[str, str]:
    """The multipart form fields of a request (the text ones)."""
    body = request.content
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    out: dict[str, str] = {}
    for part in body.split(b"--" + boundary):
        head, _, value = part.partition(b"\r\n\r\n")
        if b'name="' not in head or b"filename=" in head:
            continue
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        out[name] = value.rstrip(b"\r\n").decode()
    return out


def _wav_of(request: httpx.Request) -> tuple[int, int, int, int]:
    body = request.content
    start = body.index(b"RIFF")
    with wave.open(io.BytesIO(body[start:]), "rb") as wf:
        return wf.getframerate(), wf.getnchannels(), wf.getsampwidth(), wf.getnframes()


class _Server:
    """A whisper.cpp server stand-in: answers queued per request."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        a = self.answers.pop(0) if self.answers else _verbose("unexpected request")
        if isinstance(a, Exception):
            raise a
        if isinstance(a, httpx.Response):
            return a
        return httpx.Response(200, json=a)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


class _Local:
    """The local Whisper (a FasterWhisperClient stand-in)."""

    cpu_threads = 4
    short_window = object()

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def transcribe(self, pcm: bytes) -> str:
        self.calls.append("transcribe")
        return "local text"

    async def transcribe_with_window(self, pcm: bytes):
        self.calls.append("with_window")
        return "local text", 10

    async def transcribe_full_window(self, pcm: bytes):
        self.calls.append("full")
        return "local full", 30

    async def transcribe_wav_bytes(self, wav: bytes) -> str:
        self.calls.append("wav")
        return "local wav"


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture(autouse=True)
def _short_window_on(monkeypatch):
    monkeypatch.setattr(settings, "whisper_short_window_enabled", True)


# ─── the URL ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "http://host.lima.internal:6371", "http://192.168.1.20:6371/", "http://localhost:6371",
    "http://127.0.0.1:6371", "https://nas:8443/stt",
])
def test_a_house_address_is_accepted(url) -> None:
    assert wr.check_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "http://stt.example.com:6371",          # the internet
    "http://8.8.8.8:6371",                  # a public address
    "http://alex:secret@host.lima.internal:6371",
    "ftp://host.lima.internal/",
    "host.lima.internal:6371",
    "",
])
def test_anything_else_is_refused(url) -> None:
    with pytest.raises(ValueError):
        wr.check_url(url)


# ─── the answer ──────────────────────────────────────────────────────────


def test_verbose_json_is_read_token_weighted() -> None:
    doc = {"text": "ignored", "segments": [
        {"text": " Pause", "tokens": [1, 2, 3], "avg_logprob": -0.1, "no_speech_prob": 0.05},
        {"text": " the music.", "tokens": [4], "avg_logprob": -0.5, "no_speech_prob": 0.9},
    ]}
    r = wr.parse_reply(doc)
    assert r.text == "Pause the music."
    assert r.tokens == 4
    assert r.avg_logprob == pytest.approx((-0.1 * 3 - 0.5) / 4)
    assert r.no_speech_prob == 0.05         # the window's own: the first segment's


def test_a_plain_answer_has_no_scores_so_its_short_decode_is_never_trusted() -> None:
    r = wr.parse_reply({"text": " Stop. "})
    assert r.text == "Stop."
    assert r.avg_logprob == -math.inf
    assert r.short_decode().verdict == "unsure"


@pytest.mark.parametrize("doc", [{"error": "failed to read audio"}, [], {"segments": [1]}, {"nothing": 1}])
def test_an_unusable_answer_is_an_error(doc) -> None:
    with pytest.raises(wr.RemoteSttError):
        wr.parse_reply(doc)


def test_pcm_goes_out_as_a_16k_mono_16bit_wav() -> None:
    srv = _Server(_verbose("Stop."))
    c = wr.RemoteWhisperClient(URL, transport=srv.transport())
    asyncio.run(c.transcribe(_pcm(1.5)))
    assert _wav_of(srv.requests[0]) == (16000, 1, 2, 24000)
    assert str(srv.requests[0].url) == f"{URL}/inference"


# ─── the two windows ─────────────────────────────────────────────────────


def test_a_short_command_is_decoded_on_the_10s_window() -> None:
    srv = _Server(_verbose("Pause the music."))
    c = wr.RemoteWhisperClient(URL, transport=srv.transport())
    assert asyncio.run(c.transcribe_with_window(_pcm(2.0))) == ("Pause the music.", 10)
    f = _fields(srv.requests[0])
    assert f["audio_ctx"] == "500"
    assert f["temperature_inc"] == "0.0"
    assert f["no_timestamps"] == "true"
    assert f["beam_size"] == "5"
    assert f["response_format"] == "verbose_json"
    assert f["language"] == "en"
    assert len(srv.requests) == 1


@pytest.mark.parametrize("answer", [
    _verbose("you", no_speech=0.9, avg_logprob=-0.3),      # noise heard as "you": unsure
    _verbose("Pause pause pause", avg_logprob=-1.5),        # unlikely
    _verbose("Pause the music.", tokens=200),               # ran to the cap
    {"text": "Pause the music."},                           # no scores at all
])
def test_a_doubtful_short_decode_is_heard_again_on_the_full_window(answer) -> None:
    srv = _Server(answer, _verbose("Pause the music."))
    c = wr.RemoteWhisperClient(URL, transport=srv.transport())
    assert asyncio.run(c.transcribe_with_window(_pcm(2.0))) == ("Pause the music.", 30)
    full = _fields(srv.requests[1])
    assert full["audio_ctx"] == "0"
    assert full["temperature_inc"] == "0.2"
    assert full["no_timestamps"] == "false"


def test_a_long_capture_goes_straight_to_the_full_window() -> None:
    srv = _Server(_verbose("a long story"))
    c = wr.RemoteWhisperClient(URL, transport=srv.transport())
    assert asyncio.run(c.transcribe_with_window(_pcm(12.0))) == ("a long story", 30)
    assert _fields(srv.requests[0])["audio_ctx"] == "0"
    assert len(srv.requests) == 1


def test_a_multilingual_model_never_gets_the_short_window() -> None:
    srv = _Server(_verbose("Stop."))
    c = wr.RemoteWhisperClient(URL, model="large-v3-turbo", transport=srv.transport())
    assert asyncio.run(c.transcribe_with_window(_pcm(1.0)))[1] == 30
    assert _fields(srv.requests[0])["audio_ctx"] == "0"


def test_the_short_window_setting_off_means_the_full_window(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_short_window_enabled", False)
    srv = _Server(_verbose("Stop."))
    c = wr.RemoteWhisperClient(URL, transport=srv.transport())
    assert asyncio.run(c.transcribe_with_window(_pcm(1.0)))[1] == 30


def test_the_full_window_by_name_whatever_the_length() -> None:
    srv = _Server(_verbose("Volume up."))
    c = wr.RemoteWhisperClient(URL, transport=srv.transport())
    assert asyncio.run(c.transcribe_full_window(_pcm(1.0))) == ("Volume up.", 30)
    assert whisper_mod.can_decode_full_window(c)


def test_the_timeout_grows_with_the_audio() -> None:
    c = wr.RemoteWhisperClient(URL, timeout_sec=2.0)
    assert c.request_timeout(_pcm(2.0)) == pytest.approx(2.5)
    assert c.request_timeout(_pcm(20.0)) == pytest.approx(7.0)


@pytest.mark.parametrize("failure", [
    httpx.Response(500, text="boom"),
    httpx.Response(503, json={"status": "loading model"}),
    httpx.Response(200, text="not json"),
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("slow"),
])
def test_every_failure_is_a_remote_error(failure) -> None:
    srv = _Server(failure)
    c = wr.RemoteWhisperClient(URL, transport=srv.transport())
    with pytest.raises(wr.RemoteSttError):
        asyncio.run(c.transcribe_with_window(_pcm(1.0)))


# ─── the fallback ────────────────────────────────────────────────────────


def _pair(srv: _Server, local=None, retry: float = 30.0):
    clock = _Clock()
    remote = wr.RemoteWhisperClient(URL, transport=srv.transport())
    return wr.FallbackWhisperClient(remote, local, retry_sec=retry, clock=clock, wall=lambda: 0.0), clock


def test_the_host_hears_the_turn_when_it_answers() -> None:
    local = _Local()
    fb, _ = _pair(_Server(_verbose("Stop.")), local)
    assert asyncio.run(fb.transcribe_with_window(_pcm(1.0))) == ("Stop.", 10)
    assert local.calls == []
    doc = fb.engine_doc()
    assert (doc["active"], doc["host_state"], doc["host_calls"], doc["local_calls"]) == ("host", "ok", 1, 0)
    assert fb.cpu_threads is None and fb.short_window is fb.remote


def test_a_failed_request_is_heard_locally_then_the_host_is_left_alone_until_the_retry() -> None:
    local = _Local()
    srv = _Server(httpx.ConnectError("refused"), _verbose("Back again."))
    fb, clock = _pair(srv, local, retry=30.0)

    assert asyncio.run(fb.transcribe_with_window(_pcm(1.0))) == ("local text", 10)
    doc = fb.engine_doc()
    assert (doc["active"], doc["host_state"], doc["local_calls"]) == ("local", "down", 1)
    assert "refused" in doc["last_error"]
    assert doc["down_since"] is not None
    assert fb.cpu_threads == 4 and fb.short_window is local.short_window

    # Within the retry period the host isn't asked at all.
    clock.t += 10
    assert asyncio.run(fb.transcribe_full_window(_pcm(1.0))) == ("local full", 30)
    assert len(srv.requests) == 1

    # After it, the host is tried again and takes over.
    clock.t += 25
    assert asyncio.run(fb.transcribe_with_window(_pcm(1.0))) == ("Back again.", 10)
    doc = fb.engine_doc()
    assert (doc["active"], doc["host_state"], doc["down_since"]) == ("host", "ok", None)


def test_a_second_failure_restarts_the_wait() -> None:
    srv = _Server(httpx.ConnectError("x"), httpx.ConnectError("y"))
    fb, clock = _pair(srv, _Local(), retry=30.0)
    asyncio.run(fb.transcribe(_pcm(1.0)))
    clock.t += 31
    asyncio.run(fb.transcribe(_pcm(1.0)))
    assert len(srv.requests) == 2
    clock.t += 29
    asyncio.run(fb.transcribe(_pcm(1.0)))
    assert len(srv.requests) == 2           # still within the new wait


def test_no_local_whisper_and_no_host_is_unavailable() -> None:
    fb, _ = _pair(_Server(httpx.ConnectError("refused")), None)
    with pytest.raises(SttUnavailableError):
        asyncio.run(fb.transcribe_with_window(_pcm(1.0)))
    assert fb.engine_doc()["local_standby"] is False


def test_a_wav_is_heard_by_either() -> None:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(_pcm(1.0))
    local = _Local()
    fb, _ = _pair(_Server(_verbose("Hello."), httpx.ConnectError("refused")), local, retry=0.0)
    assert asyncio.run(fb.transcribe_wav_bytes(buf.getvalue())) == "Hello."
    assert asyncio.run(fb.transcribe_wav_bytes(buf.getvalue())) == "local wav"


def test_the_switch_is_logged_both_ways(caplog) -> None:
    srv = _Server(httpx.ConnectError("refused"), _verbose("ok"))
    fb, _ = _pair(srv, _Local(), retry=0.0)
    with caplog.at_level("INFO", logger="domovoi.clients.whisper_remote"):
        asyncio.run(fb.transcribe(_pcm(1.0)))
        asyncio.run(fb.transcribe(_pcm(1.0)))
    text = caplog.text
    assert "didn't answer" in text and "own Whisper hears the turns" in text
    assert "answers again" in text


# ─── boot: load_whisper_client ───────────────────────────────────────────


@pytest.fixture
def real_boot(monkeypatch):
    """A boot with real clients, the local Whisper replaced by _Local."""
    monkeypatch.setattr(whisper_mod, "_client", None)
    monkeypatch.setattr(whisper_mod, "_status", None)
    monkeypatch.setattr(whisper_mod, "_remote", None)
    monkeypatch.setattr(whisper_mod, "_remote_error", None)
    monkeypatch.setattr(settings, "use_stubs", False)
    monkeypatch.setattr(settings, "whisper_model", "small.en")
    monkeypatch.setattr(settings, "whisper_device", "cpu")
    monkeypatch.setattr(settings, "whisper_compute_type", "auto")
    monkeypatch.setattr("domovoi.bootstrap.register_nvidia_dlls", lambda: ())
    local = _Local()
    monkeypatch.setattr(whisper_mod, "FasterWhisperClient", lambda **kw: local)
    return local


def test_no_url_means_nothing_changes(real_boot, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_remote_url", "")
    client = whisper_mod.load_whisper_client()
    assert client is real_boot
    assert "engine" not in whisper_mod.stt_status()
    assert "engine" not in whisper_mod.whisper_runtime()
    assert whisper_mod.engine_status() is None


def test_a_url_puts_the_host_in_front_of_the_loaded_local_whisper(real_boot, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_remote_url", URL)
    monkeypatch.setattr(wr.RemoteWhisperClient, "probe", lambda self, timeout=1.0: None)
    client = whisper_mod.load_whisper_client()
    assert isinstance(client, wr.FallbackWhisperClient)
    assert client.local is real_boot
    assert whisper_mod.get_whisper_client() is client
    st = whisper_mod.stt_status()
    assert st["state"] == "ok"                      # the local ladder's state, as before
    assert st["engine"]["active"] == "host" and st["engine"]["host_state"] == "ok"
    rt = whisper_mod.whisper_runtime()
    assert (rt["engine"], rt["model"], rt["device"], rt["compute_type"], rt["cpu_threads"]) == (
        "host", "small.en", "host", "remote", None)
    assert rt["short_window"] is True


def test_a_host_down_at_boot_is_reported_and_the_local_whisper_hears(real_boot, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_remote_url", URL)
    monkeypatch.setattr(wr.RemoteWhisperClient, "probe", lambda self, timeout=1.0: "ConnectError: refused")
    client = whisper_mod.load_whisper_client()
    st = whisper_mod.stt_status()
    assert st["engine"]["active"] == "local" and st["engine"]["host_state"] == "down"
    rt = whisper_mod.whisper_runtime()
    assert (rt["engine"], rt["device"], rt["cpu_threads"]) == ("local", "cpu", 4)
    assert asyncio.run(client.transcribe_with_window(_pcm(1.0))) == ("local text", 10)


def test_a_refused_url_is_not_used_and_says_why(real_boot, monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_remote_url", "http://stt.example.com:6371")
    client = whisper_mod.load_whisper_client()
    assert client is real_boot
    eng = whisper_mod.stt_status()["engine"]
    assert (eng["active"], eng["host_state"]) == ("local", "refused")
    assert "isn't on this network" in eng["last_error"]


def test_the_host_still_hears_when_the_local_whisper_failed_to_load(real_boot, monkeypatch) -> None:
    def boom(**kw):
        raise RuntimeError("no model")

    monkeypatch.setattr(whisper_mod, "FasterWhisperClient", boom)
    monkeypatch.setattr(settings, "whisper_cpu_fallback_model", "")
    monkeypatch.setattr(settings, "whisper_remote_url", URL)
    monkeypatch.setattr(wr.RemoteWhisperClient, "probe", lambda self, timeout=1.0: None)
    client = whisper_mod.load_whisper_client()
    assert isinstance(client, wr.FallbackWhisperClient) and client.local is None
    assert whisper_mod.get_whisper_client() is client
    st = whisper_mod.stt_status()
    assert st["state"] == "unavailable"             # the local ladder, honestly
    assert st["engine"]["active"] == "host" and st["engine"]["local_standby"] is False


def test_the_settings_are_off_by_default() -> None:
    from domovoi.config import Settings

    fields = Settings.model_fields
    assert fields["whisper_remote_url"].default == ""
    assert fields["whisper_remote_model"].default == "small.en"


# ─── slice 2: the household's switch, the probe, Health ──────────────────


def test_the_household_switch_sends_every_turn_to_the_local_whisper(monkeypatch) -> None:
    local = _Local()
    srv = _Server(_verbose("Stop."))
    fb, _ = _pair(srv, local)
    monkeypatch.setattr(settings, "whisper_remote_enabled", False)
    assert asyncio.run(fb.transcribe_with_window(_pcm(1.0))) == ("local text", 10)
    assert srv.requests == [], "switched off: the service isn't asked"
    doc = fb.engine_doc()
    assert (doc["active"], doc["use_host"]) == ("local", False)
    monkeypatch.setattr(settings, "whisper_remote_enabled", True)
    assert asyncio.run(fb.transcribe_with_window(_pcm(1.0))) == ("Stop.", 10)


def test_switched_off_with_no_local_whisper_the_host_still_hears(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_remote_enabled", False)
    fb, _ = _pair(_Server(_verbose("Stop.")), None)
    assert asyncio.run(fb.transcribe(_pcm(1.0))) == "Stop."
    assert fb.engine_doc()["active"] == "host"


def test_the_probe_marks_the_host_down_and_up_between_turns(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_remote_enabled", True)
    srv = _Server(httpx.ConnectError("refused"), httpx.Response(200, json={"status": "ok"}))
    fb, _ = _pair(srv, _Local())
    asyncio.run(fb.refresh())
    assert fb.engine_doc()["host_state"] == "down"
    asyncio.run(fb.refresh())
    assert fb.engine_doc()["host_state"] == "ok"
    assert [str(r.url) for r in srv.requests] == [f"{URL}/health"] * 2


def test_the_probe_leaves_a_switched_off_host_alone(monkeypatch) -> None:
    monkeypatch.setattr(settings, "whisper_remote_enabled", False)
    srv = _Server()
    fb, _ = _pair(srv, _Local())
    asyncio.run(fb.refresh())
    assert srv.requests == []


def test_no_service_means_no_probe_task(monkeypatch) -> None:
    monkeypatch.setattr(whisper_mod, "_remote", None)

    async def go():
        return wr.schedule_probe()

    assert asyncio.run(go()) is None


def test_the_probe_task_starts_and_stops_with_a_service(monkeypatch) -> None:
    fb, _ = _pair(_Server(), _Local())
    monkeypatch.setattr(whisper_mod, "_remote", fb)

    async def go():
        task = wr.schedule_probe()
        assert task is not None and not task.done()
        await wr.cancel_probe()
        assert task.done()

    asyncio.run(go())


def test_the_core_health_says_who_hears_only_with_a_service(monkeypatch) -> None:
    from domovoi import main as core_main

    fb, _ = _pair(_Server(), _Local())
    fb.mark_down("refused")
    monkeypatch.setattr(whisper_mod, "_remote", fb)
    monkeypatch.setattr(whisper_mod, "_remote_error", None)
    eng = whisper_mod.engine_status()
    assert eng["active"] == "local" and eng["host_state"] == "down"

    # The web keeps only the known keys of what the core sends.
    keys = {"active", "host_state", "local_standby", "use_host", "down_since"}
    assert keys <= set(eng)
    assert hasattr(core_main, "admin_stt_engine")


def test_the_switch_endpoint_refuses_without_a_service(monkeypatch) -> None:
    from fastapi import HTTPException

    from domovoi import main as core_main

    monkeypatch.setattr(whisper_mod, "_remote", None)
    monkeypatch.setattr(whisper_mod, "_remote_error", None)
    with pytest.raises(HTTPException) as e:
        asyncio.run(core_main.admin_stt_engine(core_main._SttEngineBody(use_host=False)))
    assert e.value.status_code == 409


def test_the_switch_endpoint_saves_and_applies(monkeypatch) -> None:
    from domovoi import config_env_writer
    from domovoi import main as core_main

    fb, _ = _pair(_Server(), _Local())
    monkeypatch.setattr(whisper_mod, "_remote", fb)
    saved: list[dict] = []
    monkeypatch.setattr(config_env_writer, "write_env_values", lambda changes, env_path=None: saved.append(changes))
    monkeypatch.setattr(settings, "whisper_remote_enabled", True)
    out = asyncio.run(core_main.admin_stt_engine(core_main._SttEngineBody(use_host=False)))
    assert saved == [{"whisper_remote_enabled": False}]
    assert settings.whisper_remote_enabled is False
    assert out["use_host"] is False and out["engine"]["active"] == "local"
