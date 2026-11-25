"""Read-only satellite diagnostics on the core: the `diag_request` ->
`diag_result` exchange, the probe allowlist, the cleaning every answer
gets, the per-satellite rate limit and the admin routes' answers.

DB-free on purpose (no `requires_db`), like test_satellite_logs.py.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from domovoi import main as core_main
from domovoi import satellite_abilities as sa
from domovoi import satellite_diagnostics as sd
from domovoi.satellite_abilities import NotDeclared
from domovoi.streaming import StreamSession

TOKEN = "0123456789abcdef" * 4


class FakeWS:
    def __init__(self):
        self.app = SimpleNamespace(state=SimpleNamespace(active_sessions={}))
        self.sent: list[dict] = []
        self.reply: dict | None = None  # when set, answer each diag_request
        self.session: StreamSession | None = None

    async def send_text(self, t):
        frame = json.loads(t)
        self.sent.append(frame)
        if self.reply is not None and self.session is not None:
            answer = {"type": "diag_result", "probe": frame["probe"], **self.reply}
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(self.session._on_control(answer)))


def session(hello: dict | None = None, reply: dict | None = None) -> StreamSession:
    ws = FakeWS()
    s = StreamSession(ws, "office")  # type: ignore[arg-type]
    s.abilities = sa.from_hello(hello if hello is not None else {"diagnostics": True})
    ws.reply, ws.session = reply, s
    return s


def answering(text="ok", ok=True, hello=None) -> StreamSession:
    return session(hello, {"ok": ok, "text": text, "at": 1763800000.5})


# --- the exchange -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_probe_sends_its_name_only_and_returns_the_cleaned_answer():
    s = answering(text=f"log line pairing_token={TOKEN}\n"
                       "Authorization: Bearer abcdefgh12345678\x1b[0m\npassword hunter2")
    r = await s.request_diagnostic("recent_logs")
    assert s.ws.sent == [{"type": "diag_request", "probe": "recent_logs"}]
    assert r["probe"] == "recent_logs" and r["ok"] is True and r["at"] == 1763800000.5
    assert TOKEN not in r["text"] and "abcdefgh12345678" not in r["text"]
    assert "hunter2" not in r["text"]
    assert "\x1b" not in r["text"] and "[redacted]" in r["text"]
    assert s._diag_requests == {}


@pytest.mark.asyncio
async def test_the_answer_is_capped_on_the_core_too():
    s = answering(text="x " * 50_000)
    r = await s.request_diagnostic("system")
    assert len(r["text"]) <= sd.MAX_TEXT_CHARS and r["text"].endswith("[cut short]")


@pytest.mark.asyncio
async def test_an_unknown_probe_is_refused_and_nothing_is_sent():
    s = session()
    for name in ("shell", "rm -rf /", "recent_logs ", ""):
        with pytest.raises(ValueError):
            await s.request_diagnostic(name)
    assert s.ws.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("hello", [
    {"supports": ["restart", "logs"]},                       # a spare device without it
    {"supports": ["room_display"], "board": "android_room"},
    {},                                                      # a Pi on older code: never assumed
])
async def test_no_frame_goes_to_a_device_without_the_ability(hello):
    s = session(hello)
    with pytest.raises(NotDeclared):
        await s.request_diagnostic("versions")
    assert s.ws.sent == []


@pytest.mark.asyncio
async def test_a_result_nobody_asked_for_is_dropped():
    s = session()
    await s._on_control({"type": "diag_result", "probe": "versions", "ok": True, "text": "x"})
    await s._on_control({"type": "diag_result", "probe": ["x"], "ok": True})
    assert s._diag_requests == {}


@pytest.mark.asyncio
async def test_a_silent_satellite_times_out_and_the_next_ask_sends_again():
    s = session()
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await s.request_diagnostic("network", timeout=0.01)
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await s.request_diagnostic("network", timeout=0.01)
    assert len(s.ws.sent) == 2


@pytest.mark.asyncio
async def test_the_device_echo_never_renames_or_reshapes_the_answer():
    s = session()
    task = asyncio.create_task(s.request_diagnostic("audio"))
    await asyncio.sleep(0)
    await s._on_control({"type": "diag_result", "probe": "audio", "ok": "yes", "text": 5, "at": "soon"})
    r = await task
    assert r["probe"] == "audio" and r["ok"] is False and r["text"] == "5"
    assert isinstance(r["at"], float)


# --- the rate limit ---------------------------------------------------------


def test_the_rate_limit_is_per_satellite_and_books_all_or_nothing():
    now = [0.0]
    rl = sd.RateLimiter(max_runs=6, window=60.0, clock=lambda: now[0])
    assert rl.take("office", 6) == 0.0
    assert rl.take("office", 1) > 0
    assert rl.take("kitchen", 6) == 0.0, "another satellite has its own budget"
    now[0] = 61.0
    assert rl.take("office", 6) == 0.0
    assert rl.take("office", 7) == 60.0


# --- the admin routes -------------------------------------------------------


@pytest.fixture
def sessions(monkeypatch):
    found: dict = {}
    monkeypatch.setattr(core_main.app.state, "active_sessions", found, raising=False)
    monkeypatch.setattr(core_main.app.state, "active_satellites", {}, raising=False)
    monkeypatch.setattr(core_main, "DIAG_LIMITER", sd.RateLimiter())
    return found


@pytest.mark.asyncio
async def test_a_shared_rooms_devices_are_checked_by_their_own_id(sessions):
    # 1.5.0: the office's speaker is one device; a second satellite in the
    # office is addressed by its own id and gets the probes itself.
    speaker, second = answering(text="speaker"), answering(text="second")
    sessions["office"] = speaker
    core_main.app.state.active_satellites.update({"office": speaker, "office_b": second})
    body = await core_main.admin_run_satellite_diagnostics("office_b")
    assert body["room_id"] == "office_b"
    assert all(r["text"] == "second" for r in body["results"])
    assert second.ws.sent and speaker.ws.sent == []
    one = await core_main.admin_run_satellite_diagnostic("office_b", "versions")
    assert one["results"][0]["text"] == "second" and speaker.ws.sent == []
    # The room's id still reaches its speaker (an older dashboard).
    body = await core_main.admin_run_satellite_diagnostic("office", "versions")
    assert body["results"][0]["text"] == "speaker"


@pytest.mark.asyncio
async def test_the_snapshot_lists_diagnostics_by_satellite(monkeypatch):
    async def sha():
        return "abc1234"

    monkeypatch.setattr(core_main.git_version, "cached_current_sha", sha)
    state = core_main.app.state
    for name in ("resumable_music", "wifi_status", "current_playlist", "active_dropins",
                 "satellite_full_duplex", "satellite_display", "satellite_voice",
                 "satellite_volume", "satellite_synced_sha", "satellite_sat_type",
                 "satellite_mic_enabled", "satellite_upgrades", "satellite_health",
                 "satellite_last_outage", "satellite_wake_word", "satellite_mic_dsp"):
        if not hasattr(state, name):
            monkeypatch.setattr(state, name, {}, raising=False)
    plain = session(hello={"supports": ["logs"]})
    checks = session()
    # A shared office whose speaker didn't declare diagnostics; its second
    # device did. The snapshot names the device, not the room.
    monkeypatch.setattr(state, "active_sessions", {"office": plain}, raising=False)
    monkeypatch.setattr(state, "active_satellites", {"office": plain, "office_b": checks},
                        raising=False)
    snap = await core_main.admin_snapshot()
    assert snap["satellite_diagnostics"] == ["office_b"]
    assert snap["active_satellites"] == ["office", "office_b"]
    assert snap["active_rooms"] == ["office"]


@pytest.mark.asyncio
async def test_run_all_returns_every_probe_in_order(sessions):
    sessions["office"] = answering(text="fine")
    body = await core_main.admin_run_satellite_diagnostics("office")
    expected = [p for p in sd.PROBES if p != "mic_dsp"]
    assert [r["probe"] for r in body["results"]] == expected
    assert list(body["probes"]) == expected, "no mic_dsp without an XVF3800"
    assert all(r["ok"] and r["text"] == "fine" for r in body["results"])
    assert "mic_dsp" not in [f["probe"] for f in sessions["office"].ws.sent]


@pytest.mark.asyncio
async def test_one_probe_and_its_refusals(sessions):
    sessions["office"] = answering(text="v1")
    body = await core_main.admin_run_satellite_diagnostic("office", "versions")
    assert [r["probe"] for r in body["results"]] == ["versions"]
    with pytest.raises(HTTPException) as e:
        await core_main.admin_run_satellite_diagnostic("office", "shell")
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        await core_main.admin_run_satellite_diagnostic("kitchen", "versions")
    assert e.value.status_code == 404
    sessions["den"] = answering(hello={"supports": ["logs"]})
    with pytest.raises(HTTPException) as e:
        await core_main.admin_run_satellite_diagnostics("den")
    assert e.value.status_code == 409 and sessions["den"].ws.sent == []


@pytest.mark.asyncio
async def test_the_routes_are_rate_limited_per_satellite(sessions):
    sessions["office"] = answering()
    for _ in range(3):
        await core_main.admin_run_satellite_diagnostics("office")
    sent = len(sessions["office"].ws.sent)
    with pytest.raises(HTTPException) as e:
        await core_main.admin_run_satellite_diagnostic("office", "versions")
    assert e.value.status_code == 429 and "Retry-After" in e.value.headers
    assert len(sessions["office"].ws.sent) == sent, "nothing sent once over the limit"
    sessions["kitchen"] = answering()
    await core_main.admin_run_satellite_diagnostic("kitchen", "versions")


@pytest.mark.asyncio
async def test_a_probe_that_gets_no_answer_is_listed_as_failed(sessions):
    s = session()
    sessions["office"] = s

    async def silent(probe, *, timeout=0.01):
        raise TimeoutError

    s.request_diagnostic = silent  # type: ignore[method-assign]
    body = await core_main.admin_run_satellite_diagnostic("office", "network")
    assert body["results"][0]["ok"] is False
    assert "didn't answer" in body["results"][0]["text"]


# --- mic_dsp: only for a satellite that declared it ------------------------


@pytest.mark.asyncio
async def test_run_all_includes_mic_dsp_for_an_xvf3800(sessions):
    sessions["office"] = answering(text="active preset: B", hello={"diagnostics": True, "mic_dsp": True})
    body = await core_main.admin_run_satellite_diagnostics("office")
    assert [r["probe"] for r in body["results"]] == list(sd.PROBES)
    assert body["results"][-1]["probe"] == "mic_dsp"


@pytest.mark.asyncio
async def test_mic_dsp_is_refused_without_the_ability_and_nothing_is_sent(sessions):
    s = answering()
    sessions["office"] = s
    with pytest.raises(HTTPException) as e:
        await core_main.admin_run_satellite_diagnostic("office", "mic_dsp")
    assert e.value.status_code == 409 and s.ws.sent == []
    with pytest.raises(NotDeclared):
        await s.request_diagnostic("mic_dsp")
    assert s.ws.sent == []


# --- M5: a hostile answer can't hold the core's event loop ------------------


def test_the_cleaning_rules_are_bounded_on_a_hostile_answer():
    import time

    for text in ("psk" * (sd.MAX_TEXT_CHARS * 3), "auth-" * 40_000 + "=",
                 "x" * 5_000_000):
        started = time.monotonic()
        sd.clean_text(text)
        assert time.monotonic() - started < 1.0, text[:12]


def test_the_receive_loop_only_cuts_the_answer_to_size():
    raw = sd.raw_result({"ok": True, "text": "y" * 10_000_000, "at": 1.0})
    assert len(raw["text"]) == sd.RAW_TEXT_MAX_CHARS


@pytest.mark.asyncio
async def test_the_cleaning_runs_off_the_event_loop(monkeypatch):
    import time

    def slow_clean(probe, raw):
        time.sleep(0.5)             # a blocking clean: on a worker thread
        return {"probe": probe, "ok": True, "text": "", "at": 0.0}

    monkeypatch.setattr(sd, "clean_result", slow_clean)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    t = asyncio.create_task(ticker())
    s = answering(text="psk" * 3000)
    await s.request_diagnostic("system")
    t.cancel()
    assert ticks >= 10              # the loop kept running meanwhile


@pytest.mark.asyncio
async def test_a_clean_that_overruns_its_deadline_answers_failed(monkeypatch):
    import time

    monkeypatch.setattr(sd, "CLEAN_DEADLINE_S", 0.05)
    monkeypatch.setattr(sd, "clean_result", lambda probe, raw: time.sleep(0.3) or {})
    r = await sd.clean_result_off_loop("system", {"text": "x"})
    assert r["ok"] is False and "in time" in r["text"]
