"""Spare-device satellites, S0 (2026-10-12): the hello's `supports` / `board`.

The gate on this work is "Pi satellites behave exactly as before", so most
of what follows proves that a hello WITHOUT `supports` gets today's frames,
today's caches and today's dashboard row, and that a device which declared
a narrower list never receives a frame it left out. The devices are the
shared fixtures in docs/uml/satellite-fixtures.json (a 2-Mic HAT Pi, an
XVF3800 Pi, a `linux_pc` and an `android_room`), which the satellite
client's tests and, later, the Android room mode's JVM tests read too.

DB-free throughout: the inventory writes are recorded, not executed (the
real SQL is covered in test_satellite_supports_db.py).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import dropin_common
from domovoi import satellite_abilities as sa
from domovoi import satellite_health as sh
from domovoi import streaming
from domovoi.handlers.chat_mode import ChatModeHandler
from domovoi.handlers.wifi import WifiHandler
from domovoi.models import Context
from domovoi.satellite_config_schema import EDITABLE_FIELDS
from domovoi.streaming import CORE_FEATURES, StreamSession
from domovoi.timer_delivery import AnnounceNotStarted, TimerDelivery

REPO = Path(__file__).resolve().parents[2]
FIXTURES = json.loads((REPO / "docs" / "uml" / "satellite-fixtures.json").read_text(encoding="utf-8"))
DEVICES: dict[str, dict[str, Any]] = FIXTURES["devices"]
PI_DEVICES = ("pi_2mic_hat", "pi_xvf3800")
SPARE_DEVICES = ("linux_pc", "windows_pc", "android_room")
GATES: dict[str, list[str]] = FIXTURES["frame_gates"]


# ─── doubles ──────────────────────────────────────────────────────────────


def _state() -> SimpleNamespace:
    """Every per-room registry the hello and the disconnect touch."""
    return SimpleNamespace(
        active_sessions={}, active_dropins={},
        satellite_full_duplex={}, satellite_synced_sha={}, satellite_sat_type={},
        satellite_mic_enabled={}, wifi_status={}, satellite_volume={}, satellite_voice={},
        satellite_config={}, satellite_display={},
    )


class FakeWS:
    """A socket that plays `frames` in, then disconnects; records every
    text frame the core sends."""

    def __init__(self, frames: list[dict[str, Any]] | None = None, state: Any = None) -> None:
        self.app = SimpleNamespace(state=state or _state())
        self._in = list(frames or [])
        self.sent: list[dict[str, Any]] = []
        self.closed: int | None = None

    async def accept(self) -> None:
        return None

    async def receive(self) -> dict[str, Any]:
        if self._in:
            return {"type": "websocket.receive", "text": json.dumps(self._in.pop(0))}
        return {"type": "websocket.disconnect"}

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        self.closed = code

    def types(self) -> list[str]:
        return [f.get("type") for f in self.sent]


@pytest.fixture
def paired(monkeypatch):
    """A hello that passed pairing with its token (pairing itself is
    covered in test_satellite_pairing)."""

    async def _accept(self, ctrl):
        self.token_authenticated = True
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)


@pytest.fixture
def inventory(monkeypatch):
    """Record the `satellites` writes a hello makes instead of running them."""
    writes: list[tuple[str, str, str]] = []

    class Repo:
        def __init__(self, _s):
            pass

        async def upsert_type(self, room, sat_type):
            writes.append(("sat_type", room, sat_type))

        async def upsert_board(self, room, board):
            writes.append(("board", room, board))

        async def room_voice(self, room):
            return None  # the server keeps no voice for the room (a read)

    @asynccontextmanager
    async def scope():
        yield None

    monkeypatch.setattr(streaming, "session_scope", scope)
    monkeypatch.setattr(streaming, "SatellitesRepository", Repo)
    return writes


async def _hello(device: str, ws: FakeWS | None = None) -> tuple[StreamSession, FakeWS]:
    hello = DEVICES[device]["hello"]
    ws = ws or FakeWS()
    session = StreamSession(ws, hello["room_id"])  # type: ignore[arg-type]
    await session._on_control(dict(hello))
    ws.sent.clear()
    return session, ws


# ─── the fixture file itself ──────────────────────────────────────────────


def test_the_fixtures_name_every_ability_the_core_knows_and_no_other():
    assert set(FIXTURES["abilities"]) == set(sa.ABILITIES)
    for name, spec in FIXTURES["abilities"].items():
        assert spec["assumed_without_supports"] is (name in sa.ASSUMED_WITHOUT_SUPPORTS), name
        key = spec["hello_key"]
        assert (key is None) is (name not in sa.BOOLEAN_KEYS.values()), name
        if key is not None:
            assert sa.BOOLEAN_KEYS[key] == name
    assert FIXTURES["config_section_ability"] == sa.CONFIG_SECTION_ABILITY
    assert FIXTURES["pi_action_ability"] == sa.PI_ACTION_ABILITY
    for frame, needs in GATES.items():
        assert needs and all(n in sa.ABILITIES for n in needs), frame
    assert set(DEVICES) == {*PI_DEVICES, *SPARE_DEVICES}


def test_the_core_s_ready_features_are_the_fixtures():
    for device in DEVICES.values():
        assert device["ready"]["features"] == list(CORE_FEATURES)
        assert device["ready"]["protocol_version"] == streaming.PROTOCOL_VERSION == "0.1"


@pytest.mark.parametrize("device", sorted(DEVICES))
def test_each_fixture_hello_declares_what_the_fixture_expects(device):
    d = DEVICES[device]
    expect = d["expect"]
    ab = sa.from_hello(d["hello"], room_id=d["hello"]["room_id"])
    assert ab.declared is expect["declared"]
    assert ab.declared_list() == expect["supports_shown"]
    assert list(ab.unknown) == expect["unknown_abilities"]
    assert sorted(n for n in sa.ABILITIES if ab.allows(n)) == expect["allowed"]
    assert sorted(n for n in sa.ABILITIES if not ab.allows(n)) == expect["not_allowed"]
    sent = [f for f, needs in GATES.items() if all(ab.allows(n) for n in needs)]
    assert sent == expect["frames_sent"]
    assert [f for f in GATES if f not in sent] == expect["frames_never"]
    assert sa.clean_board(d["hello"].get("board")) == expect["board"]
    kept = [a for a, need in sa.PI_ACTION_ABILITY.items() if ab.allows(need)]
    assert kept == expect["pi_actions_kept"]
    if ab.allows("config"):
        hidden = sorted({
            f.name.split(".", 1)[0] for f in EDITABLE_FIELDS if not ab.allows_config_key(f.name)
        })
        assert hidden == expect["config_hidden_sections"]
    else:
        assert expect["config_hidden_sections"] is None


@pytest.mark.parametrize("device", sorted(DEVICES))
def test_each_fixture_health_frame_is_kept_as_the_fixture_says(device):
    health = DEVICES[device]["health"]
    assert sh.validate_sample(health["frame"]) == health["stored"]


# ─── a hello without `supports` is today's hello ──────────────────────────


@pytest.mark.parametrize(
    "fd,pause,capture,announce",
    list(itertools.product((False, True), repeat=4)),
    ids=lambda v: str(int(v)),
)
def test_a_hello_without_supports_allows_exactly_what_the_core_always_sent(fd, pause, capture, announce):
    """Every combination of today's boolean keys: every push the core has
    always made stays allowed, and the negotiated behaviours follow their
    booleans exactly as the old `bool(ctrl.get(...))` reads did."""
    hello = {"type": "hello", "supports_full_duplex": fd, "speech_pause": pause,
             "capture_control": capture, "announce_after_session_end": announce}
    ab = sa.from_hello(hello)
    assert ab.declared is False and ab.declared_list() is None
    for name in ("restart", "code_sync", "plugin_payloads", "config", "wifi", "mic_gain",
                 "leds", "display", "logs", "wake_clips", "wake_models", "sounds", "voice",
                 "time_sync", "chat_mode"):
        assert ab.allows(name), name
    # Announcements go out whether or not the flag is there (the WARNING
    # for a satellite without it is today's, tested in test_streaming).
    assert ab.allows("announce_after_session_end")
    assert ab.allows("full_duplex") is fd
    assert ab.allows("speech_pause") is pause
    assert ab.allows("capture_control") is capture
    assert ab.allows_chat() is fd, "chat mode was always gated on full duplex alone"
    assert all(ab.allows_config_key(f.name) for f in EDITABLE_FIELDS)


def test_an_empty_or_malformed_supports_is_a_device_that_declared_nothing(caplog):
    for raw in ([], None, "restart", {"restart": True}, [1, None, {"x": 1}]):
        ab = sa.from_hello({"type": "hello", "supports": raw, "speech_pause": True})
        assert ab.declared is True
        assert not ab.allows("restart") and not ab.allows("code_sync")
        assert ab.allows("speech_pause"), "the boolean keys still count"
    assert "not a list" in caplog.text


def test_unknown_names_are_ignored_and_never_granted():
    ab = sa.from_hello({"supports": ["restart", "teleport", "RESTART", "x" * 200]})
    assert ab.names == {"restart"}
    assert ab.unknown == ("teleport",)
    assert not ab.allows("teleport")


def test_board_is_a_bounded_slug():
    assert sa.clean_board("linux_pc") == "linux_pc"
    assert sa.clean_board(" android_room ") == "android_room"
    assert sa.clean_board("raspberry_pi_zero_2_w") == "raspberry_pi_zero_2_w"
    for bad in ("", "a b", "<script>", "x" * 65, 7, None, ["linux_pc"]):
        assert sa.clean_board(bad) is None


# ─── the whole connection, through StreamSession.run ──────────────────────


@pytest.mark.parametrize("device", sorted(DEVICES))
async def test_a_session_sends_only_ready_and_caches_what_the_hello_declared(device, paired, inventory):
    d = DEVICES[device]
    hello = d["hello"]
    room = hello["room_id"]
    ws = FakeWS([hello])
    session = StreamSession(ws, room)  # type: ignore[arg-type]
    seen: dict[str, Any] = {}
    real_send = ws.send_text

    async def spy(text):
        # Read the caches the moment `ready` goes out (the disconnect
        # clears them afterwards).
        state = ws.app.state
        seen.update(
            full_duplex=state.satellite_full_duplex.get(room),
            registered=state.active_sessions.get(room) is session,
        )
        await real_send(text)

    ws.send_text = spy  # type: ignore[method-assign]
    await session.run()

    # bot_name and server_names (this host's `<hostname>.local`, 2026-10-12)
    # depend on the machine the test runs on; the rest is the fixture.
    ready = {k: v for k, v in ws.sent[0].items() if k not in ("bot_name", "server_names")}
    assert ready == d["ready"]
    assert isinstance(ws.sent[0]["server_names"], list)
    assert ws.types() == ["ready"], "a hello is answered with `ready` and nothing else"
    assert seen == {"full_duplex": session.abilities.allows("full_duplex"), "registered": True}
    assert seen["full_duplex"] is ("full_duplex" in d["expect"]["allowed"])
    assert session._speech_hints is ("speech_pause" in d["expect"]["allowed"])
    assert session._capture_control is ("capture_control" in d["expect"]["allowed"])
    assert session.board == d["expect"]["board"]
    expected_writes = [("sat_type", room, "voice")]
    if d["expect"]["board"] is not None:
        expected_writes.append(("board", room, d["expect"]["board"]))
    assert inventory == expected_writes, "a Pi writes its type and never a board"


async def test_a_pi_hello_logs_the_announcement_warning_only_when_the_flag_is_missing(paired, inventory, caplog):
    caplog.set_level(logging.WARNING, logger="domovoi.streaming")
    hello = dict(DEVICES["pi_2mic_hat"]["hello"])
    await StreamSession(FakeWS(), "kitchen")._on_control(hello)  # type: ignore[arg-type]
    assert "without the announcement fix" not in caplog.text
    hello.pop("announce_after_session_end")
    await StreamSession(FakeWS(), "kitchen")._on_control(hello)  # type: ignore[arg-type]
    assert "without the announcement fix" in caplog.text
    caplog.clear()
    # A device that declared a list is simply never sent announcements.
    await StreamSession(FakeWS(), "x")._on_control({"type": "hello", "supports": []})  # type: ignore[arg-type]
    assert "without the announcement fix" not in caplog.text


async def test_a_hello_without_board_never_writes_one_and_a_bad_board_is_dropped(paired, inventory, caplog):
    await StreamSession(FakeWS(), "den")._on_control({"type": "hello"})  # type: ignore[arg-type]
    await StreamSession(FakeWS(), "den")._on_control({"type": "hello", "board": "rm -rf /"})  # type: ignore[arg-type]
    assert inventory == []
    assert "not a short slug" in caplog.text


# ─── every gated frame, for every device ──────────────────────────────────


async def _trigger(session: StreamSession, ws: FakeWS, frame: str) -> Any:
    """Ask the session to send `frame` the way the core does. Returns the
    exception it raised, if any."""
    try:
        if frame == "restart":
            await session.request_restart()
        elif frame == "upgrade":
            await session.request_upgrade(expected_sha="abc1234", reconnect_timeout=90)
        elif frame == "set_config":
            await session.send_config({"wake.threshold": 0.6})
        elif frame == "get_logs":
            try:
                await session.request_logs(max_bytes=4096, timeout=0.01)
            except (TimeoutError, asyncio.TimeoutError):
                pass  # sent, and nobody answers in a test
        elif frame == "start_wake_recording":
            await session.start_wake_recording(
                wake_word_id=1, slug="hey_test", clip_seconds=2.0, target_count=3)
        elif frame == "stop_wake_recording":
            await session.stop_wake_recording()
        elif frame == "set_wake_word":
            await session.request_set_wake_word(slug="hey_test")
        elif frame == "wake_models_changed":
            await session.notify_wake_models_changed()
        elif frame == "sounds_changed":
            await session.notify_sounds_changed()
        elif frame == "set_display":
            await session.set_display("on")
        elif frame == "chat_start":
            await session.send_chat_start()
        elif frame == "announcement":
            async def fake_announce(text, defer):
                await ws.send_text(json.dumps({"type": "announcement"}))

            session._announce = fake_announce  # type: ignore[method-assign]
            await session.announce("dinner's ready")
        else:  # pragma: no cover
            raise AssertionError(frame)
    except Exception as e:  # noqa: BLE001
        return e
    return None


SENT_BY_A_METHOD = [f for f in GATES if f not in ("dropin_start", "end_capture")]


@pytest.mark.parametrize("frame", SENT_BY_A_METHOD)
@pytest.mark.parametrize("device", sorted(DEVICES))
async def test_a_gated_frame_goes_only_to_a_device_that_declared_it(device, frame, paired, inventory):
    session, ws = await _hello(device)
    error = await _trigger(session, ws, frame)
    allowed = frame in DEVICES[device]["expect"]["frames_sent"]
    if allowed:
        assert error is None, error
        assert frame in ws.types()
    else:
        assert frame not in ws.types(), f"{device} was sent {frame}"
        assert ws.sent == [], "nothing at all went out"
        if error is not None:
            assert isinstance(error, (sa.NotDeclared, AnnounceNotStarted)), error
        if frame == "start_wake_recording":
            assert session.wake_recording is None, "nothing armed for a refused recording"


@pytest.mark.parametrize("device", sorted(DEVICES))
async def test_the_upgrade_frame_says_no_payloads_only_to_a_device_without_them(device, paired, inventory):
    session, ws = await _hello(device)
    extra = DEVICES[device]["expect"]["upgrade_extra"]
    error = await _trigger(session, ws, "upgrade")
    if extra is None:
        assert isinstance(error, sa.NotDeclared) and ws.sent == []
        return
    frame = ws.sent[0]
    base = {"type", "expected_sha", "manifest_path", "files_base", "reconnect_timeout_sec"}
    assert {k: v for k, v in frame.items() if k not in base} == extra


@pytest.mark.parametrize("device", sorted(DEVICES))
async def test_drop_in_and_early_commit_follow_the_declared_behaviours(device, paired, inventory):
    session, ws = await _hello(device)
    state = ws.app.state
    peer = SimpleNamespace(room_id="hall")
    state.active_sessions.update({session.room_id: session, "hall": peer})
    state.satellite_full_duplex["hall"] = True
    feasible = dropin_common.dropin_feasibility(SimpleNamespace(state=state), session.room_id, "hall")
    expect = DEVICES[device]["expect"]["frames_sent"]
    assert (feasible == dropin_common.OK) is ("dropin_start" in expect)
    assert (session._capture_control and session._speech_hints) is ("end_capture" in expect)


async def test_a_config_push_with_a_setting_the_device_left_out_sends_nothing(paired, inventory):
    session, ws = await _hello("linux_pc")
    with pytest.raises(sa.NotDeclared):
        await session.send_config({"wake.threshold": 0.6, "leds.enabled": False})
    assert ws.sent == []
    await session.send_config({"wake.threshold": 0.6, "sounds.sync_enabled": True})
    assert ws.types() == ["set_config"]


async def test_stop_recording_clears_the_state_and_sends_nothing_to_a_device_without_clips(paired, inventory):
    session, ws = await _hello("android_room")
    session.wake_recording = object()  # type: ignore[assignment]
    await session.stop_wake_recording()
    assert session.wake_recording is None and ws.sent == []


# ─── announcements and the timer delivery ─────────────────────────────────


async def test_a_device_without_announcements_is_never_a_target_for_another_room(paired, inventory):
    phone, _ = await _hello("android_room")
    phone.abilities = sa.from_hello({"supports": ["wake_models"]})
    pi, _ = await _hello("pi_2mic_hat")
    delivery = TimerDelivery.__new__(TimerDelivery)
    delivery._warned_unpaired = set()
    assert delivery._may_hear("kitchen", "garage", set(), pi) is True
    assert delivery._may_hear("nursery", "garage", set(), phone) is False
    assert delivery._may_hear("nursery", "nursery", set(), phone) is True, "the origin row still exists"
    with pytest.raises(AnnounceNotStarted) as raised:
        await phone.announce("Your 10 minute timer is done.")
    assert raised.value.reason == "unsupported"


# ─── handlers refuse what the room's device didn't declare ────────────────


def _ctx_for(session: StreamSession, *, full_duplex: bool = True) -> Context:
    app = SimpleNamespace(state=SimpleNamespace(
        active_sessions={session.room_id: session},
        satellite_full_duplex={session.room_id: full_duplex},
    ))
    return Context(session_id=uuid4(), room_id=session.room_id, online=True, app=app)


class _NoDB:
    def __getattr__(self, name):
        raise AssertionError(f"a declined turn touched the DB (.{name})")


async def test_chat_mode_is_declined_for_a_full_duplex_device_without_chat_mode(paired, inventory):
    phone, _ = await _hello("android_room")
    resp = await ChatModeHandler()._enter(_ctx_for(phone), _NoDB())  # type: ignore[arg-type]
    assert resp.text == "Chat mode isn't available on this room's device."
    # A full-duplex Pi gets past the gate (and then fails softly on the DB).
    pi, _ = await _hello("pi_xvf3800")
    resp = await ChatModeHandler()._enter(_ctx_for(pi), _NoDB())  # type: ignore[arg-type]
    assert resp.text != "Chat mode isn't available on this room's device."


async def test_wifi_is_refused_for_a_device_that_keeps_its_own(paired, inventory):
    # A voice switch in a room whose device keeps no voice is no longer
    # refused: the server keeps the room's voice (test_room_voice.py).
    pc, _ = await _hello("linux_pc")
    resp = WifiHandler()._reassociate_response(_ctx_for(pc))
    assert resp.pi_action is None and "its own Wi-Fi" in resp.text
    pi, _ = await _hello("pi_2mic_hat")
    assert WifiHandler()._reassociate_response(_ctx_for(pi)).pi_action == "reassociate_wifi"


@pytest.mark.parametrize("device", sorted(DEVICES))
async def test_a_pi_action_the_device_left_out_is_dropped_from_response_end(device, paired, inventory):
    """The backstop under the handlers: whatever a handler returns, a
    response_end to the device carries no pi_action it didn't declare."""
    session, _ = await _hello(device)
    kept = DEVICES[device]["expect"]["pi_actions_kept"]
    for action, arg in (("reassociate_wifi", None), ("set_voice", "aria"), ("restart", None)):
        expected = (action, arg) if action in kept else (None, None)
        assert session._declared_pi_action(action, arg) == expected
    assert session._declared_pi_action(None, None) == (None, None)
    assert session._declared_pi_action("future_action", "x") == ("future_action", "x")


# ─── the core's endpoints: 409, nothing sent; a Pi as before ──────────────


@pytest.fixture
def core(monkeypatch, tmp_path):
    from domovoi import main as core_main
    from domovoi.tests.auth_testkit import install_fake_db

    install_fake_db(monkeypatch, admin=True, device_token="house-token", sessions={"bearer-1"})
    logged: list[str] = []

    @asynccontextmanager
    async def scope():
        yield None

    class IntentLog:
        def __init__(self, _s):
            pass

        async def log(self, **kw):
            logged.append(kw["transcript"])

    import domovoi.db.repositories as repos

    monkeypatch.setattr(core_main, "session_scope", scope)
    monkeypatch.setattr(repos, "IntentLogRepository", IntentLog)

    async def unfrozen(*a, **kw):
        return None

    monkeypatch.setattr(core_main.container_freeze, "require_unfrozen", unfrozen)
    app = core_main.app
    extra = dict(vars(_state()), resumable_music={}, current_playlist={},
                 satellite_health={}, satellite_last_outage={},
                 satellite_retained_log=sh.RetainedLog(tmp_path))
    for name, value in extra.items():
        monkeypatch.setattr(app.state, name, value, raising=False)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return SimpleNamespace(app=app, client=client, logged=logged)


ADMIN = {"Authorization": "Bearer bearer-1"}


async def _connect(core, device: str) -> tuple[StreamSession, FakeWS]:
    ws = FakeWS(state=core.app.state)
    session, ws = await _hello(device, ws)
    core.app.state.active_sessions[session.room_id] = session
    return session, ws


@pytest.mark.parametrize("device", sorted(DEVICES))
async def test_restart_and_upgrade_endpoints(device, core, paired, inventory):
    session, ws = await _connect(core, device)
    room = session.room_id
    allowed = DEVICES[device]["expect"]["frames_sent"]
    async with core.client as c:
        r1 = await c.post("/v1/admin/satellite/restart", json={"room_id": room}, headers=ADMIN)
        r2 = await c.post("/v1/admin/satellite/upgrade", json={"room_id": room}, headers=ADMIN)
    for r, frame in ((r1, "restart"), (r2, "upgrade")):
        if frame in allowed:
            assert r.status_code == 200, r.text
            assert frame in ws.types()
        else:
            assert r.status_code == 409, r.text
            assert "didn't declare" in r.json()["detail"]
            assert frame not in ws.types()


async def test_settings_endpoints_hide_and_refuse_what_the_device_left_out(core, paired, inventory):
    pi, pi_ws = await _connect(core, "pi_2mic_hat")
    pc, pc_ws = await _connect(core, "linux_pc")
    phone, phone_ws = await _connect(core, "android_room")
    async with core.client as c:
        pi_fields = (await c.get(f"/v1/admin/satellite/{pi.room_id}/config", headers=ADMIN)).json()["fields"]
        pc_fields = (await c.get(f"/v1/admin/satellite/{pc.room_id}/config", headers=ADMIN)).json()["fields"]
        r = await c.get(f"/v1/admin/satellite/{phone.room_id}/config", headers=ADMIN)
        assert r.status_code == 409
        r = await c.post(f"/v1/admin/satellite/{phone.room_id}/config",
                         json={"changes": {"wake.threshold": 0.6}}, headers=ADMIN)
        assert r.status_code == 409 and phone_ws.sent == []
        r = await c.post(f"/v1/admin/satellite/{pc.room_id}/config",
                         json={"changes": {"wake.threshold": 0.6, "leds.enabled": False}},
                         headers=ADMIN)
    assert [f["name"] for f in pi_fields] == [f.name for f in EDITABLE_FIELDS], "a Pi sees every setting"
    assert not any(f["name"].split(".")[0] in ("leds", "mic_gain", "wifi", "display") for f in pc_fields)
    body = r.json()
    assert body["sent"] == ["wake.threshold"]
    assert "didn't declare 'leds'" in body["rejected"]["leds.enabled"]
    assert pc_ws.sent == [{"type": "set_config", "changes": {"wake.threshold": 0.6}}]


async def test_the_settings_endpoint_refuses_what_the_satellite_would_refuse(core, paired, inventory):
    """Round 2: a value the satellite refuses (an ALSA name that runs a
    command, a string over 120 characters, a line break) is refused here
    with the reason, instead of "sent" and then silently dropped."""
    pi, pi_ws = await _connect(core, "pi_xvf3800")
    async with core.client as c:
        r = await c.post(f"/v1/admin/satellite/{pi.room_id}/config", json={"changes": {
            "music.alsa_device": 'file:"|id",raw',
            "audio.output_mixer_card": "0;reboot",
            "audio.output_mixer_control": 'PCM"',
            "audio.input_device": "x" * 121,
            "audio.output_device": "Array\nmore",
            "wake.threshold": 0.6,
        }}, headers=ADMIN)
    body = r.json()
    assert body["sent"] == ["wake.threshold"], body
    assert "plain ALSA device name" in body["rejected"]["music.alsa_device"]
    assert "card number or name" in body["rejected"]["audio.output_mixer_card"]
    assert "mixer control name" in body["rejected"]["audio.output_mixer_control"]
    assert "120 characters" in body["rejected"]["audio.input_device"]
    # The core's own text check (any setting, since 2026-10-13: "can't hold
    # a control character") answers before the satellite limit's ("no
    # control characters"); either says why.
    assert "control character" in body["rejected"]["audio.output_device"]
    assert pi_ws.sent == [{"type": "set_config", "changes": {"wake.threshold": 0.6}}]


async def test_a_windows_pc_s_device_names_are_checked_by_the_windows_rule(core, paired, inventory):
    """Merged with eeb09c9: the core's ALSA check would refuse a real
    Windows name; on board windows_pc the satellite's Windows rule applies."""
    pc, pc_ws = await _connect(core, "windows_pc")
    async with core.client as c:
        r = await c.post(f"/v1/admin/satellite/{pc.room_id}/config", json={"changes": {
            "music.alsa_device": "Speakers (Realtek(R) Audio)",
            "audio.input_device": "x & calc.exe",
            "audio.output_device": 'file:"|id",raw',
        }}, headers=ADMIN)
    body = r.json()
    assert body["sent"] == ["music.alsa_device"], body
    assert "a Windows audio device" in body["rejected"]["audio.input_device"]
    assert "a Windows audio device" in body["rejected"]["audio.output_device"]
    assert pc_ws.sent == [{"type": "set_config", "changes": {"music.alsa_device": "Speakers (Realtek(R) Audio)"}}]


async def test_logs_endpoint_skips_the_live_pull_for_a_device_without_logs(core, paired, inventory):
    phone, ws = await _connect(core, "android_room")
    async with core.client as c:
        r = await c.get(f"/v1/admin/satellite/{phone.room_id}/logs?max_bytes=4096", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True and body["live"] is None
    assert body["live_supported"] is False
    assert ws.sent == [], "no get_logs went out"


async def test_wake_endpoints_refuse_before_touching_anything(core, paired, inventory):
    phone, ws = await _connect(core, "android_room")
    bare_ws = FakeWS(state=core.app.state)
    bare = StreamSession(bare_ws, "attic")  # type: ignore[arg-type]
    await bare._on_control({"type": "hello", "supports": []})
    core.app.state.active_sessions["attic"] = bare
    bare_ws.sent.clear()
    async with core.client as c:
        r1 = await c.post("/v1/admin/wake/record/start",
                          json={"room_id": phone.room_id, "wake_word_id": 1}, headers=ADMIN)
        r2 = await c.post("/v1/admin/wake/push",
                          json={"room_id": "attic", "wake_word_id": 1}, headers=ADMIN)
    assert r1.status_code == 409, r1.text
    assert r2.status_code == 409, r2.text
    assert ws.sent == [] and phone.wake_recording is None
    assert bare_ws.sent == []


async def test_display_endpoint_needs_video_and_display(core, paired, inventory):
    pc, ws = await _connect(core, "linux_pc")
    core.app.state.satellite_sat_type[pc.room_id] = "video"
    async with core.client as c:
        r = await c.post("/v1/admin/satellite/display",
                         json={"room_id": pc.room_id, "action": "on"}, headers=ADMIN)
    assert r.status_code == 409 and "screen control" in r.json()["detail"]
    assert ws.sent == []


async def test_the_snapshot_lists_supports_and_board_only_for_devices_that_sent_them(core, paired, inventory, monkeypatch):
    from domovoi import main as core_main

    async def sha():
        return "abc1234"

    monkeypatch.setattr(core_main.git_version, "cached_current_sha", sha)
    for device in DEVICES:
        await _connect(core, device)
    async with core.client as c:
        snap = (await c.get("/v1/admin/snapshot")).json()
    assert snap["satellite_supports"] == {
        DEVICES[d]["hello"]["room_id"]: DEVICES[d]["expect"]["supports_shown"] for d in SPARE_DEVICES
    }
    assert snap["satellite_board"] == {"study": "linux_pc", "den": "windows_pc", "nursery": "android_room"}
    assert snap["satellite_full_duplex"] == {
        "kitchen": False, "living-room": True, "study": False, "den": False, "nursery": True,
    }


async def test_a_pi_s_ignored_settings_reach_the_snapshot_and_the_row(core, paired, inventory, monkeypatch):
    """2026-10-13: a Pi that replaced a stored setting with its profile's
    default says so in its hello; the dashboard's row shows it."""
    from domovoi import main as core_main
    from web.backend.api import satellites as web_sats

    async def sha():
        return "abc1234"

    async def no_np(*a, **kw):
        return None

    monkeypatch.setattr(core_main.git_version, "cached_current_sha", sha)
    monkeypatch.setattr(web_sats, "_now_playing_for", no_np)
    note = "music device setting ignored: not a plain ALSA name"
    hello = dict(DEVICES["pi_xvf3800"]["hello"], settings_ignored=[note, 7, "x" * 500, "bad\nline"])
    ws = FakeWS(state=core.app.state)
    session = StreamSession(ws, hello["room_id"])  # type: ignore[arg-type]
    await session._on_control(hello)
    core.app.state.active_sessions[session.room_id] = session
    await _connect(core, "pi_2mic_hat")
    async with core.client as c:
        snap = (await c.get("/v1/admin/snapshot")).json()
    assert snap["satellite_settings_ignored"] == {hello["room_id"]: [note, "x" * 160]}
    row = {"room_id": hello["room_id"], "control_port": 6650, "http_port": 8050, "last_connected_at": None}
    sat = await web_sats._satellite_for(row, {**snap, "active_rooms": [hello["room_id"]]}, {}, {}, {})
    assert sat.settings_ignored == [note, "x" * 160]
    offline = await web_sats._satellite_for(row, {**snap, "active_rooms": []}, {}, {}, {})
    assert offline.settings_ignored is None


def test_the_health_sample_keeps_ignored_settings_as_short_strings():
    kept = sh.validate_sample({"rss_kb": 1, "settings_ignored": ["music device setting ignored", 3, "", "y" * 300]})
    assert kept["settings_ignored"] == ["music device setting ignored", "y" * 160]
    assert sh.validate_sample({"rss_kb": 1, "settings_ignored": "not a list"})["settings_ignored"] is None


# ─── the dashboard's row ──────────────────────────────────────────────────


@pytest.mark.parametrize("device", sorted(DEVICES))
async def test_the_dashboard_row_shows_board_and_supports(device, monkeypatch):
    from web.backend.api import satellites as web_sats

    async def no_np(*a, **kw):
        return None

    monkeypatch.setattr(web_sats, "_now_playing_for", no_np)
    d = DEVICES[device]
    room = d["hello"]["room_id"]
    ab = sa.from_hello(d["hello"])
    snapshot = {"active_rooms": [room]}
    if ab.declared:
        snapshot["satellite_supports"] = {room: ab.declared_list()}
    board = sa.clean_board(d["hello"].get("board"))
    if board:
        snapshot["satellite_board"] = {room: board}
    row = {"room_id": room, "control_port": 6650, "http_port": 8050, "last_connected_at": None}
    # A Pi adopted over USB has a board on file; it still has no list.
    meta = {room: {"board": "raspberry_pi_zero_2_w"}} if device in PI_DEVICES else {}
    sat = await web_sats._satellite_for(row, snapshot, {}, meta, {})
    assert sat.supports == d["expect"]["supports_shown"]
    assert sat.board == (board or "raspberry_pi_zero_2_w")
    offline = await web_sats._satellite_for(row, {"active_rooms": []}, {}, meta, {})
    assert offline.supports is None, "supports is what the CONNECTED device said"


# ─── review follow-ups (mutants that survived) ────────────────────────────


@pytest.mark.parametrize("bad", [101, -1, True, "50", float("nan"), 1e300],
                         ids=["101", "-1", "true", "str50", "nan", "1e300"])
def test_a_battery_percent_outside_0_to_100_or_not_a_number_is_dropped(bad):
    alone = sh.validate_sample({"rss_kb": 1, "battery": {"percent": bad}})
    assert alone["battery"] is None, "nothing usable left: no battery at all"
    kept = sh.validate_sample({"rss_kb": 1, "battery": {"percent": bad, "charging": True}})
    assert kept["battery"] == {"percent": None, "charging": True, "on_power": None}
    for good in (0, 64, 100):
        assert sh.validate_sample({"rss_kb": 1, "battery": {"percent": good}})["battery"]["percent"] == good


async def test_wake_models_changed_skips_a_device_without_wake_models(paired, inventory):
    ws = FakeWS()
    bare = StreamSession(ws, "attic")  # type: ignore[arg-type]
    await bare._on_control({"type": "hello", "supports": ["restart"]})
    ws.sent.clear()
    await bare.notify_wake_models_changed()
    assert ws.sent == []
    pi, pi_ws = await _hello("pi_2mic_hat")
    await pi.notify_wake_models_changed()
    assert pi_ws.types() == ["wake_models_changed"]
