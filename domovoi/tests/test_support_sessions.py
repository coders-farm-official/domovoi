"""Satellite support sessions (1.5.0): the core's rules.

domovoi/support_sessions.py and domovoi/support_routes.py; the satellite's
half is satellite/tests/test_support_shell.py. These pin, with the owner's
answers of 22 Nov:

* the ability is never assumed (a Pi without the key gets no frames) and a
  spare PC is refused even if it declared it;
* opening needs the security tier, a password sign-in within 5 minutes AND
  the password typed again; a phone-grant session never opens one;
* "Allow support sessions" is off by default; on needs the fresh sign-in,
  off ends an open session;
* the ticket is single use, 30 seconds, bound to its session;
* the limits: 30 minutes with warnings at 5 and 1, 10 minutes idle (output
  doesn't count) with a warning at 9, 16 KB frames and the rate, 4 KB input;
* every end: revoke, disconnect, voice, a household device, the switch,
  unpairing, a core restart, the admin; the spoken lines at open and end;
* the transcript: asciicast, hidden input, redaction, the 8 MB cap, the
  30-day / 200 MB sweep, and plain text that stays text;
* stray satellite frames are dropped and a run of them closes the socket.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from domovoi import admin_auth, satellite_abilities, support_sessions as ss
from domovoi.db.session import engine, session_scope
from domovoi.tests.auth_testkit import (
    STRONG_PW,
    _db,  # noqa: F401 - fixture
    bearer,
    claim_admin,
    core_client,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db

RW = {"X-Requested-With": "domovoi-tests"}
SAT = "kitchen-pi"
REPO = Path(__file__).resolve().parents[2]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ═══ DB-free ══════════════════════════════════════════════════════════════


def test_the_ability_is_never_assumed() -> None:
    assert "support_shell" in satellite_abilities.DECLARED_ONLY
    assert not satellite_abilities.from_hello({}).allows("support_shell")
    assert not satellite_abilities.LEGACY.allows("support_shell")
    assert satellite_abilities.from_hello({"support_shell": True}).allows("support_shell")
    assert satellite_abilities.from_hello({"supports": ["support_shell"]}).allows("support_shell")
    assert not satellite_abilities.from_hello({"supports": ["restart"]}).allows("support_shell")


async def test_no_frame_reaches_a_device_that_did_not_declare_it() -> None:
    from domovoi.streaming import StreamSession

    sent: list[str] = []

    async def send_text(t: str) -> None:
        sent.append(t)

    sess = object.__new__(StreamSession)
    sess.room_id = SAT
    sess.ws = SimpleNamespace(send_text=send_text)
    sess.abilities = satellite_abilities.from_hello({})
    with pytest.raises(satellite_abilities.NotDeclared):
        await sess.send_support({"type": "support_open", "session_id": "x"})
    assert sent == []
    sess.abilities = satellite_abilities.from_hello({"support_shell": True})
    await sess.send_support({"type": "support_open", "session_id": "x"})
    assert len(sent) == 1


def test_a_ticket_is_single_use_bound_and_short_lived(monkeypatch) -> None:
    book = ss.TicketBook()
    sid = "a" * 32
    raw = book.issue(sid, "h" * 64, SAT)
    assert len(raw) == 64
    assert book.redeem(raw, "b" * 32) is None           # another session: refused AND burned
    assert book.redeem(raw, sid) is None
    raw = book.issue(sid, "h" * 64, SAT)
    t = book.redeem(raw, sid)
    assert t is not None and t.satellite_id == SAT and t.admin_hash == "h" * 64
    assert book.redeem(raw, sid) is None                 # once only
    raw = book.issue(sid, "h" * 64, SAT)
    now = time.monotonic()
    monkeypatch.setattr(ss.time, "monotonic", lambda: now + ss.TICKET_TTL_SEC + 1)
    assert book.redeem(raw, sid) is None                 # 30 seconds
    assert ss.TICKET_TTL_SEC == 30.0
    assert book.redeem(None, sid) is None and book.redeem("short", sid) is None


def test_the_limits_are_the_owners() -> None:
    assert ss.MAX_SEC == 30 * 60 and ss.WARN_LEFT_SEC == (300, 60)
    assert ss.IDLE_SEC == 10 * 60 and ss.IDLE_WARN_SEC == 9 * 60
    assert ss.OUTPUT_FRAME_MAX == 16 * 1024 and ss.OUTPUT_RATE_MAX == 64 * 1024
    assert ss.INPUT_FRAME_MAX == 4 * 1024
    assert ss.TRANSCRIPT_MAX_BYTES == 8 * 1024 * 1024
    assert ss.TRANSCRIPTS_TOTAL_MAX_BYTES == 200 * 1024 * 1024 and ss.KEEP_DAYS == 30
    assert ss.ADMIN_RECHECK_SEC <= 5.0
    assert ss.START_LINE == "A support session has started in this room."
    assert ss.END_LINE == "The support session has ended."


def test_redaction() -> None:
    r = ss.Redactor(("eight word household token phrase here",))
    out = r("tok: eight word household token phrase here\r\n")
    assert "household token phrase" not in out and ss.REMOVED in out
    assert "hunter2" not in r("psk=hunter2\n") and "psk=" in r("psk=hunter2\n")
    assert "s3cret" not in r("ADMIN_PASSWORD = s3cret\r\n")
    assert "abc123" not in r('api_key: "abc123"\n')
    assert "xyz" not in r("export MY_TOKEN=xyz\n")
    tok = "A" * 40
    assert tok not in r(f'"pairing_token": "{tok}"\n')
    assert r("ls -la\r\n") == "ls -la\r\n"
    # A private key split over frames is blanked whole.
    first = r("x\n-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAsecret1\n")
    second = r("BBBBsecret2\n-----END OPENSSH PRIVATE KEY-----\nafter\n")
    joined = first + second
    assert "secret1" not in joined and "secret2" not in joined
    assert "after" in joined and "[private key removed]" in joined


def test_the_transcript_is_asciicast_hides_hidden_input_and_caps(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path)
    t = ss.Transcript("c" * 32, cols=80, rows=24, title="t", known_secrets=("setup-code-words",))
    t.input(b"ls\r", echo_on=True)
    t.output(b"file \xe2\x9c\x93\r\n")
    t.input(b"my-password\r", echo_on=False)
    t.input(b"more\r", echo_on=False)
    t.output(b"the setup-code-words\r\n")
    t.close()
    lines = (tmp_path / ("c" * 32 + ".cast")).read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert header["version"] == 2 and header["width"] == 80 and header["height"] == 24
    events = [json.loads(x) for x in lines[1:]]
    assert [e[1] for e in events] == ["i", "o", "i", "i", "o"]
    assert events[2][2] == "[hidden input: 12 bytes]"      # a count, never the bytes
    # Echo off past the first line is no longer a password prompt: logged.
    assert events[3][2] == "more\r"
    whole = "\n".join(lines)
    assert "my-password" not in whole
    assert "setup-code-words" not in whole
    if os.name == "posix":
        assert (tmp_path / ("c" * 32 + ".cast")).stat().st_mode & 0o777 == 0o600

    monkeypatch.setattr(ss, "TRANSCRIPT_MAX_BYTES", 2048)
    t = ss.Transcript("d" * 32, cols=80, rows=24, title="t")
    with pytest.raises(ss.LogFull):
        for _ in range(100):
            t.output(b"x" * 100)
    assert t.truncated
    with pytest.raises(ss.LogFull):
        t.input(b"y", echo_on=True)                        # nothing more is logged, or sent
    assert (tmp_path / ("d" * 32 + ".cast")).stat().st_size <= 2048


def test_plain_text_takes_escapes_out_and_keeps_markup_as_text() -> None:
    cast = "\n".join([
        json.dumps({"version": 2}),
        json.dumps([0.1, "o", "\x1b[1;31m<script>alert(1)</script>\x1b[0m\r\n"]),
        json.dumps([0.2, "o", "\x1b]0;title\x07done\r\n"]),
        json.dumps([0.3, "i", "ls\r"]),
    ])
    out = ss.plain_text(cast)
    assert out == "<script>alert(1)</script>\ndone\n"
    assert "\x1b" not in out


def test_the_viewer_never_renders_a_transcript_as_html() -> None:
    src = (REPO / "web" / "static" / "support_session.jsx").read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" not in src and "innerHTML" not in src
    # xterm with the fit add-on only: no links, no clipboard add-on.
    import re

    assert src.count("loadAddon(") == 1 and "new FitAddon()" in src
    assert set(re.findall(r"/vendor/xterm/[\w.-]+", src)) == {
        "/vendor/xterm/xterm.css", "/vendor/xterm/xterm.min.js", "/vendor/xterm/addon-fit.min.js"}
    assert "windowOptions:" not in src


# ═══ DB-backed: the manager ══════════════════════════════════════════════


class FakeSat:
    def __init__(self, room_id: str = SAT, *, satellite_id: str | None = None, **hello) -> None:
        self.room_id = room_id
        self.satellite_id = satellite_id or room_id
        self.frames: list[dict] = []
        self.said: list[str] = []
        self.closed: list[int] = []
        self.abilities = satellite_abilities.from_hello(hello or {"support_shell": True})
        self.support_nonce = "ab" * 16
        self.board = None
        self.dropin_peer = None
        self.wake_recording = None

        async def close(code: int = 1000) -> None:
            self.closed.append(code)

        self.ws = SimpleNamespace(close=close)

    async def send_support(self, frame: dict) -> None:
        if not self.abilities.allows("support_shell"):
            raise satellite_abilities.NotDeclared(self.room_id, "support_shell", frame["type"])
        self.frames.append(frame)

    async def announce(self, words: str, kind: str = "notice") -> None:
        self.said.append(words)

    def kinds(self) -> list[str]:
        return [f["type"] for f in self.frames]


class FakeBrowser:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []

    async def receive_text(self) -> str:
        msg = await self.inbox.get()
        if msg is None:
            raise RuntimeError("closed")
        return msg

    async def send_text(self, t: str) -> None:
        self.sent.append(json.loads(t))

    def of(self, kind: str) -> list[dict]:
        return [m for m in self.sent if m.get("t") == kind]


async def until(cond, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.01)


async def sql(stmt: str, **params):
    async with engine.begin() as conn:
        result = await conn.execute(text(stmt), params)
        return result.all() if result.returns_rows else []


@pytest.fixture
async def world(_db, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path / "support-sessions")
    await sql("DELETE FROM support_sessions")
    async with session_scope() as s:
        token = await admin_auth.create_session(s, label="test sign-in")
    sat = FakeSat()
    app = SimpleNamespace(state=SimpleNamespace(active_sessions={SAT: sat},
                                                active_satellites={SAT: sat}))
    mgr = ss.SupportSessions(app)
    yield SimpleNamespace(mgr=mgr, sat=sat, token=token, hash=admin_auth.token_sha256(token),
                          app=app, tmp=tmp_path)
    for sess in list(mgr.by_id.values()):
        await mgr.end(sess, "browser_closed", announce=False)
    await sql("DELETE FROM support_sessions")


async def open_one(w, *, answer: str | None = None):
    sess, ticket = await w.mgr.create(satellite_id=SAT, room_id="kitchen", admin_hash=w.hash,
                                      cols=80, rows=24)
    assert w.mgr.redeem(ticket, sess.id) is sess
    browser = FakeBrowser()
    task = asyncio.create_task(w.mgr.run(browser, sess))
    await until(lambda: "support_open" in w.sat.kinds())
    opening = w.sat.frames[-1]["signed"]["doc"]
    assert opening["max_sec"] == ss.MAX_SEC and opening["idle_sec"] == ss.IDLE_SEC
    assert opening["satellite_id"] == SAT and opening["nonce"] == w.sat.support_nonce
    if answer is None:
        await w.mgr.satellite_frame(w.sat, {"type": "support_opened", "session_id": sess.id})
        await until(lambda: sess.state == "open")
    else:
        await w.mgr.satellite_frame(w.sat, {"type": "support_refused", "session_id": sess.id,
                                            "reason": answer})
    return sess, browser, task


async def row(sid: str):
    (r,) = await sql("SELECT * FROM support_sessions WHERE id = :id", id=sid)
    return r


@requires_db
async def test_a_session_opens_relays_logs_and_ends(world) -> None:
    w = world
    sess, browser, task = await open_one(w)
    await until(lambda: w.sat.said == [ss.START_LINE])         # owner answer 2
    await until(lambda: browser.of("opened"))
    await browser.inbox.put(json.dumps({"t": "in", "d": b64(b"uptime\r")}))
    await until(lambda: "support_input" in w.sat.kinds())
    assert base64.b64decode(w.sat.frames[-1]["signed"]["doc"]["data"]) == b"uptime\r"
    await w.mgr.satellite_frame(w.sat, {"type": "support_output", "session_id": sess.id,
                                        "seq": 1, "data": b64(b" 10:00 up 3 days\r\n")})
    await until(lambda: browser.of("out"))
    await browser.inbox.put(json.dumps({"t": "resize", "cols": 120, "rows": 40}))
    await until(lambda: "support_resize" in w.sat.kinds())
    assert await w.mgr.end_by_id(sess.id, "admin_ended")
    await asyncio.wait_for(task, 3)
    assert w.sat.kinds()[-1] == "support_close"
    await until(lambda: w.sat.said[-1] == ss.END_LINE)
    assert browser.of("end")[0]["reason"] == "admin_ended"
    r = await row(sess.id)
    assert r.end_reason == "admin_ended" and r.closed_at is not None
    assert r.bytes_in == 7 and r.bytes_out == len(b" 10:00 up 3 days\r\n")
    assert r.opener_label == "test sign-in" and r.opener_hash_prefix == w.hash[:12]
    cast = ss.read_transcript(sess.id)
    assert "uptime" in cast and "10:00 up 3 days" in cast


@requires_db
async def test_one_session_per_satellite(world) -> None:
    w = world
    await w.mgr.create(satellite_id=SAT, room_id="kitchen", admin_hash=w.hash, cols=80, rows=24)
    with pytest.raises(ss.Refused) as e:
        await w.mgr.create(satellite_id=SAT, room_id="kitchen", admin_hash=w.hash, cols=80, rows=24)
    assert e.value.status == 409


@requires_db
async def test_a_refusal_ends_it_quietly(world) -> None:
    w = world
    sess, browser, task = await open_one(w, answer="the room is in a drop-in call")
    await asyncio.wait_for(task, 3)
    assert (await row(sess.id)).end_reason == "refused"
    assert w.sat.said == []


@requires_db
async def test_the_time_limit_and_its_warnings(world) -> None:
    w = world
    sess, browser, task = await open_one(w)
    base = time.monotonic()
    sess.last_input = base + ss.MAX_SEC        # never idle in this test
    await w.mgr.tick(sess, now=sess.deadline - 299)
    await w.mgr.tick(sess, now=sess.deadline - 59)
    await until(lambda: len(browser.of("warn")) == 2)
    assert [m["text"] for m in browser.of("warn")] == [
        "5 minutes left in this support session", "1 minute left in this support session"]
    assert "support_input" not in w.sat.kinds()          # never typed into the shell
    await w.mgr.tick(sess, now=sess.deadline + 0.1)
    await asyncio.wait_for(task, 3)
    assert (await row(sess.id)).end_reason == "time_limit"


@requires_db
async def test_idle_counts_input_not_output(world) -> None:
    w = world
    sess, browser, task = await open_one(w)
    start = sess.last_input
    for i in range(3):
        await w.mgr.satellite_frame(w.sat, {"type": "support_output", "session_id": sess.id,
                                            "seq": i, "data": b64(b"top\r\n")})
    assert sess.last_input == start
    await w.mgr.tick(sess, now=start + ss.IDLE_WARN_SEC + 1)
    await until(lambda: browser.of("warn"))
    assert sess.state == "open"
    await w.mgr.tick(sess, now=start + ss.IDLE_SEC + 1)
    await asyncio.wait_for(task, 3)
    assert (await row(sess.id)).end_reason == "idle"


@requires_db
async def test_output_and_input_caps(world) -> None:
    w = world
    sess, browser, task = await open_one(w)
    big = b64(b"x" * (ss.OUTPUT_FRAME_MAX + 1))
    await w.mgr.satellite_frame(w.sat, {"type": "support_output", "session_id": sess.id, "data": big})
    assert sess.bytes_out == 0 and w.sat.support_strays == 1
    ok = b64(b"y" * ss.OUTPUT_FRAME_MAX)
    for _ in range(int(ss.OUTPUT_RATE_MAX * ss.OUTPUT_RATE_SLACK) // ss.OUTPUT_FRAME_MAX):
        await w.mgr.satellite_frame(w.sat, {"type": "support_output", "session_id": sess.id, "data": ok})
    accepted = sess.bytes_out
    await w.mgr.satellite_frame(w.sat, {"type": "support_output", "session_id": sess.id, "data": ok})
    assert sess.bytes_out == accepted and w.sat.support_strays == 2     # over the rate
    # Input over 4 KB is not sent.
    await w.mgr.browser_message(sess, json.dumps({"t": "in", "d": b64(b"z" * (ss.INPUT_FRAME_MAX + 1))}))
    assert "support_input" not in w.sat.kinds()


@requires_db
async def test_hidden_input_while_echo_is_off(world) -> None:
    w = world
    sess, browser, task = await open_one(w)
    await w.mgr.satellite_frame(w.sat, {"type": "support_echo", "session_id": sess.id, "on": False})
    await w.mgr.browser_message(sess, json.dumps({"t": "in", "d": b64(b"hunter2\r")}))
    assert base64.b64decode(w.sat.frames[-1]["signed"]["doc"]["data"]) == b"hunter2\r"  # still typed
    await w.mgr.end_by_id(sess.id, "admin_ended")
    cast = ss.read_transcript(sess.id)
    assert "hunter2" not in cast and "[hidden input: 8 bytes]" in cast


@requires_db
async def test_the_log_full_ends_it(world, monkeypatch) -> None:
    w = world
    monkeypatch.setattr(ss, "TRANSCRIPT_MAX_BYTES", 4096)
    sess, browser, task = await open_one(w)
    for i in range(40):
        if sess.state != "open":
            break
        await w.mgr.satellite_frame(w.sat, {"type": "support_output", "session_id": sess.id,
                                            "data": b64(b"q" * 200)})
    await asyncio.wait_for(task, 3)
    r = await row(sess.id)
    assert r.end_reason == "log_full" and r.truncated


@requires_db
async def test_revoking_the_admin_session_ends_it(world) -> None:
    w = world
    sess, browser, task = await open_one(w)
    await sql("DELETE FROM admin_sessions WHERE token_hash = :h", h=w.hash)
    await w.mgr.tick(sess, now=time.monotonic() + ss.ADMIN_RECHECK_SEC + 0.1)
    await asyncio.wait_for(task, 3)
    assert (await row(sess.id)).end_reason == "admin_session_ended"


@requires_db
async def test_input_after_a_revoke_is_never_sent(world) -> None:
    w = world
    sess, browser, task = await open_one(w)
    await sql("DELETE FROM admin_sessions WHERE token_hash = :h", h=w.hash)
    await w.mgr.browser_message(sess, json.dumps({"t": "in", "d": b64(b"rm -rf ~\r")}))
    assert "support_input" not in w.sat.kinds()
    assert (await row(sess.id)).end_reason == "admin_session_ended"


@requires_db
async def test_a_satellite_disconnect_ends_it_without_a_line(world) -> None:
    w = world
    sess, browser, task = await open_one(w)
    await until(lambda: w.sat.said)
    await w.mgr.connection_dropped(w.sat)
    await asyncio.wait_for(task, 3)
    assert (await row(sess.id)).end_reason == "satellite_disconnected"
    assert w.sat.said == [ss.START_LINE]


@requires_db
async def test_the_shell_exiting_ends_it(world) -> None:
    w = world
    sess, browser, task = await open_one(w)
    await w.mgr.satellite_frame(w.sat, {"type": "support_closed", "session_id": sess.id,
                                        "reason": "exit", "exit_code": 0})
    await asyncio.wait_for(task, 3)
    assert (await row(sess.id)).end_reason == "exit"


@requires_db
async def test_a_closed_browser_ends_it(world) -> None:
    w = world
    sess, browser, task = await open_one(w)
    await browser.inbox.put(None)
    await asyncio.wait_for(task, 3)
    assert (await row(sess.id)).end_reason == "browser_closed"


@requires_db
async def test_the_room_ends_it_by_voice(world) -> None:
    from domovoi.handlers.support_session import NONE_OPEN, SupportSessionHandler
    from domovoi.models import Context

    w = world
    sess, browser, task = await open_one(w)
    w.app.state.support_sessions = w.mgr
    handler = SupportSessionHandler()
    m = handler.fast_paths[0].pattern.match("end support session")
    assert m and handler.fast_paths[0].pattern.match("stop the support session")
    assert handler.requires_network == "no"
    ctx = Context(room_id="kitchen", app=w.app)
    reply = await handler._from_match(m, ctx, None)
    assert reply.text == ss.VOICE_END_REPLY
    await asyncio.wait_for(task, 3)
    assert (await row(sess.id)).end_reason == "voice"
    await asyncio.sleep(0.05)
    assert ss.END_LINE not in w.sat.said           # the reply was the room's answer
    reply = await handler._from_match(m, ctx, None)
    assert reply.text == NONE_OPEN


@requires_db
async def test_stray_frames_are_dropped_and_a_run_closes_the_socket(world) -> None:
    w = world
    other = FakeSat()
    sess, browser, task = await open_one(w)
    # A frame from another connection for this session: dropped.
    await w.mgr.satellite_frame(other, {"type": "support_output", "session_id": sess.id,
                                        "data": b64(b"spoof")})
    assert browser.of("out") == [] and other.support_strays == 1
    for _ in range(ss.STRAY_FRAME_LIMIT):
        await w.mgr.satellite_frame(w.sat, {"type": "support_output", "session_id": "f" * 32,
                                            "data": b64(b"x")})
    assert w.sat.closed == [1008]
    await asyncio.wait_for(task, 3)
    assert (await row(sess.id)).end_reason == "limits_broken"


@requires_db
async def test_a_pending_session_that_never_attaches_ends(world, monkeypatch) -> None:
    w = world
    monkeypatch.setattr(ss, "TICKET_TTL_SEC", -4.95)
    sess, ticket = await w.mgr.create(satellite_id=SAT, room_id="kitchen", admin_hash=w.hash,
                                      cols=80, rows=24)
    await until(sess.ended.is_set)
    assert (await row(sess.id)).end_reason == "never_attached"
    assert w.sat.frames == []                       # the satellite heard nothing


@requires_db
async def test_a_core_restart_marks_rows_left_open(world) -> None:
    await sql("INSERT INTO support_sessions (id, satellite_id) VALUES (:id, :s)", id="e" * 32, s=SAT)
    assert await ss.mark_left_open() == 1
    r = await row("e" * 32)
    assert r.end_reason == "server_restarted" and r.closed_at is not None


@requires_db
async def test_the_sweep_keeps_30_days_and_200_mb(world, monkeypatch) -> None:
    w = world
    d = w.tmp / "support-sessions"
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    old = d / ("1" * 32 + ".cast")
    old.write_text("x")
    os.utime(old, (now - 31 * 86400, now - 31 * 86400))
    monkeypatch.setattr(ss, "TRANSCRIPTS_TOTAL_MAX_BYTES", 3000)
    monkeypatch.setattr(ss, "TRANSCRIPT_MAX_BYTES", 1000)
    files = []
    for i, ch in enumerate("234"):
        p = d / (ch * 32 + ".cast")
        p.write_bytes(b"y" * 900)
        os.utime(p, (now - 100 + i, now - 100 + i))
        files.append(p)
    await sql("INSERT INTO support_sessions (id, satellite_id, opened_at, closed_at) VALUES "
              "(:id, :s, now() - interval '31 days', now() - interval '31 days')", id="1" * 32, s=SAT)
    result = await ss.prune(now=now)
    assert not old.exists() and result["removed_age"] == 1
    assert not files[0].exists() and files[1].exists() and files[2].exists()  # oldest first
    assert await sql("SELECT 1 FROM support_sessions WHERE id = :id", id="1" * 32) == []


# ═══ DB-backed: the routes ═══════════════════════════════════════════════


async def login() -> str:
    admin_auth.LOGIN_BACKOFF.reset()
    async with web_client() as c:
        r = await c.post("/api/auth/login", json={"password": STRONG_PW, "label": "dashboard"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
async def house(_db, tmp_path, monkeypatch):  # noqa: F811
    from domovoi.main import app as core_app

    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path / "support-sessions")
    await sql("DELETE FROM support_sessions")
    await sql("DELETE FROM satellites WHERE satellite_id = :s", s=SAT)
    await sql("INSERT INTO rooms (room_id, name) VALUES (:s, 'Kitchen') ON CONFLICT DO NOTHING", s=SAT)
    await sql("INSERT INTO satellites (satellite_id, room_id) VALUES (:s, :s)", s=SAT)
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    sat = FakeSat()
    core_app.state.active_sessions = {SAT: sat}
    core_app.state.active_satellites = {SAT: sat}
    core_app.state.support_sessions = ss.SupportSessions(core_app)
    yield SimpleNamespace(token=token, sat=sat, app=core_app)
    mgr = core_app.state.support_sessions
    for sess in list(mgr.by_id.values()):
        await mgr.end(sess, "browser_closed", announce=False)
    core_app.state.active_sessions = {}
    core_app.state.active_satellites = {}
    await sql("DELETE FROM support_sessions")
    await sql("DELETE FROM satellites WHERE satellite_id = :s", s=SAT)
    await sql("DELETE FROM rooms WHERE room_id = :s", s=SAT)


async def core_post(path: str, token: str | None = None, **kw):
    headers = dict(RW)
    if token:
        headers.update(bearer(token))
    headers.update(kw.pop("headers", {}))
    async with core_client() as c:
        return await c.post(path, headers=headers, **kw)


async def age_sign_in(token: str, minutes: float) -> None:
    await sql("UPDATE admin_sessions SET signed_in_at = now() - make_interval(secs => :s) "
              "WHERE token_hash = :h", s=minutes * 60, h=admin_auth.token_sha256(token))


OPEN = f"/v1/admin/satellites/{SAT}/support/open"
ALLOW = f"/v1/admin/satellites/{SAT}/support/allow"
DISALLOW = f"/v1/admin/satellites/{SAT}/support/disallow"


@requires_db
async def test_the_switch_is_off_by_default_and_on_needs_a_fresh_sign_in(house) -> None:
    (r,) = await sql("SELECT support_allowed FROM satellites WHERE satellite_id = :s", s=SAT)
    assert r.support_allowed is False
    resp = await core_post(OPEN, house.token, json={"password": STRONG_PW})
    assert resp.status_code == 409 and "off" in resp.json()["detail"]
    await age_sign_in(house.token, 6)
    assert (await core_post(ALLOW, house.token)).status_code == 403
    await age_sign_in(house.token, 1)
    assert (await core_post(ALLOW, house.token)).status_code == 200
    (r,) = await sql("SELECT support_allowed FROM satellites WHERE satellite_id = :s", s=SAT)
    assert r.support_allowed is True
    assert (await core_post(ALLOW)).status_code == 401


@requires_db
async def test_opening_needs_freshness_and_the_password_again(house) -> None:
    assert (await core_post(ALLOW, house.token)).status_code == 200
    assert (await core_post(OPEN, house.token, json={"password": "wrong"})).status_code == 403
    # The wrong password is charged to the login backoff, like a sign-in.
    assert (await core_post(OPEN, house.token, json={"password": STRONG_PW})).status_code == 429
    admin_auth.LOGIN_BACKOFF.reset()
    assert (await core_post(OPEN, house.token, json={})).status_code == 403
    admin_auth.LOGIN_BACKOFF.reset()
    await age_sign_in(house.token, 6)
    r = await core_post(OPEN, house.token, json={"password": STRONG_PW})
    assert r.status_code == 403 and "sign in with the password again" in r.json()["detail"]
    await age_sign_in(house.token, 1)
    r = await core_post(OPEN, house.token, json={"password": STRONG_PW})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["ticket"]) == 64 and body["ticket_ttl_sec"] == 30 and body["max_sec"] == 1800
    # Only the signed switch (sent when it was turned on); no open until
    # the socket attaches.
    assert [f["type"] for f in house.sat.frames] == ["support_allow"]
    assert house.sat.frames[0]["signed"]["doc"]["allowed"] is True
    r = await core_post(OPEN, house.token, json={"password": STRONG_PW})
    assert r.status_code == 409                          # one per satellite


@requires_db
async def test_a_phone_grant_session_can_never_open_one(house) -> None:
    assert (await core_post(ALLOW, house.token)).status_code == 200
    async with session_scope() as s:
        grant = await s.execute(text(
            "INSERT INTO admin_phone_grants (grant_hash, device_id, label, expires_at) "
            "VALUES ('g', 'android-1', 'Pixel', now() + interval '1 day') RETURNING id"))
        gid = grant.scalar_one()
        phone = await admin_auth.create_session(s, label="phone: Pixel", phone_grant_id=gid)
    r = await core_post(OPEN, phone, json={"password": STRONG_PW})
    assert r.status_code == 403
    assert (await core_post(ALLOW, phone)).status_code == 403


@requires_db
async def test_a_device_that_did_not_declare_it_or_a_spare_pc_is_refused(house) -> None:
    assert (await core_post(ALLOW, house.token)).status_code == 200
    house.app.state.active_sessions[SAT] = house.app.state.active_satellites[SAT] = FakeSat(supports=["restart"])
    assert (await core_post(OPEN, house.token, json={"password": STRONG_PW})).status_code == 409
    pc = FakeSat(supports=["support_shell"])
    pc.board = "linux_pc"
    house.app.state.active_sessions[SAT] = house.app.state.active_satellites[SAT] = pc
    assert (await core_post(OPEN, house.token, json={"password": STRONG_PW})).status_code == 409
    busy = FakeSat()
    busy.dropin_peer = object()
    house.app.state.active_sessions[SAT] = house.app.state.active_satellites[SAT] = busy
    assert (await core_post(OPEN, house.token, json={"password": STRONG_PW})).status_code == 409
    del house.app.state.active_sessions[SAT]
    del house.app.state.active_satellites[SAT]
    assert (await core_post(OPEN, house.token, json={"password": STRONG_PW})).status_code == 404


async def open_via_routes(house) -> tuple[ss.Session, asyncio.Task, FakeBrowser]:
    assert (await core_post(ALLOW, house.token)).status_code == 200
    r = await core_post(OPEN, house.token, json={"password": STRONG_PW})
    assert r.status_code == 200, r.text
    mgr = house.app.state.support_sessions
    sess = mgr.redeem(r.json()["ticket"], r.json()["session_id"])
    browser = FakeBrowser()
    task = asyncio.create_task(mgr.run(browser, sess))
    await until(lambda: "support_open" in house.sat.kinds())
    await mgr.satellite_frame(house.sat, {"type": "support_opened", "session_id": sess.id})
    await until(lambda: sess.state == "open")
    return sess, task, browser


@requires_db
async def test_turning_the_switch_off_ends_the_session(house) -> None:
    sess, task, _ = await open_via_routes(house)
    async with web_client() as c:
        await c.post("/api/auth/logout", headers={**RW, **bearer(house.token)})
    other = await login()
    assert (await core_post(DISALLOW, other)).status_code == 200
    await asyncio.wait_for(task, 3)
    assert sess.reason == "switch_off"


@requires_db
async def test_any_household_device_can_end_it(house) -> None:
    sess, task, _ = await open_via_routes(house)
    end = f"/v1/admin/support-sessions/{sess.id}/end"
    assert (await core_post(end)).status_code == 401
    dt = await db_device_token()
    r = await core_post(end, headers={admin_auth.DEVICE_TOKEN_HEADER: dt})
    assert r.status_code == 200 and r.json()["reason"] == "household_ended"
    await asyncio.wait_for(task, 3)
    assert sess.reason == "household_ended"
    assert (await core_post(end, headers={admin_auth.DEVICE_TOKEN_HEADER: dt})).status_code == 404


@requires_db
async def test_unpairing_ends_it(house) -> None:
    sess, task, _ = await open_via_routes(house)
    async with core_client() as c:
        r = await c.delete(f"/v1/admin/satellites/{SAT}/pairing", headers={**RW, **bearer(house.token)})
    assert r.status_code == 200, r.text
    await asyncio.wait_for(task, 3)
    assert sess.reason == "unpaired"


@requires_db
async def test_the_audit_list_and_transcript_are_bearer_only(house) -> None:
    sess, task, _ = await open_via_routes(house)
    await house.app.state.support_sessions.end_by_id(sess.id, "admin_ended")
    async with core_client() as c:
        r = await c.get("/v1/admin/support-sessions", headers=bearer(house.token))
        assert r.status_code == 200 and r.json()["sessions"][0]["id"] == sess.id
        assert r.json()["sessions"][0]["end_text"] == ss.REASONS["admin_ended"]
        dt = await db_device_token()
        r = await c.get("/v1/admin/support-sessions", headers={admin_auth.DEVICE_TOKEN_HEADER: dt})
        assert r.status_code in (401, 403)
        r = await c.get(f"/v1/admin/support-sessions/{sess.id}/transcript", headers=bearer(house.token))
        assert r.status_code == 200 and r.json()["cast"].startswith("{")
        r = await c.get("/v1/admin/support-sessions/active", headers={admin_auth.DEVICE_TOKEN_HEADER: dt})
        assert r.status_code == 200 and r.json() == {"sessions": []}
        r = await c.delete(f"/v1/admin/support-sessions/{sess.id}", headers={**RW, **bearer(house.token)})
        assert r.status_code == 200
    assert not (ss.transcript_dir() / f"{sess.id}.cast").exists()


@requires_db
async def test_the_terminal_socket_needs_its_ticket_and_a_dashboard_origin(house) -> None:
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from web.backend.main import app as web_app

    # The dashboard's socket refuses before anything reaches the core.
    client = TestClient(web_app)     # no lifespan: nothing to start
    if True:
        for headers, protocols in (
            ({"origin": "http://evil.example"}, ["domovoi-support", "ticket." + "a" * 64]),
            ({}, ["domovoi-support", "ticket." + "a" * 64]),
            ({"origin": "http://testserver"}, ["domovoi-support"]),
        ):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(f"/api/support-sessions/{'a' * 32}/terminal",
                                              headers=headers, subprotocols=protocols) as ws:
                    ws.receive_text()
    # The core's socket burns a wrong ticket and refuses it.
    client = TestClient(house.app)
    if True:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/v1/admin/support-sessions/{'a' * 32}/terminal",
                                          headers={"x-support-ticket": "b" * 64}) as ws:
                ws.receive_text()


# ═══ Rooms (V073): a satellite whose id is not its room's ═════════════════

ROOM = "supportroom"
SAT2 = "supportroom-pi-2"


@requires_db
async def test_a_satellite_whose_id_is_not_its_rooms(_db, tmp_path, monkeypatch) -> None:  # noqa: F811
    from domovoi import room_devices
    from domovoi.handlers.support_session import SupportSessionHandler
    from domovoi.main import app as core_app
    from domovoi.models import Context

    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path / "support-sessions")
    await sql("DELETE FROM support_sessions")
    await sql("DELETE FROM satellites WHERE satellite_id = :s", s=SAT2)
    # No satellite row may share the room's id (another test may have made one).
    await sql("DELETE FROM satellites WHERE satellite_id = :s", s=ROOM)
    await sql("INSERT INTO rooms (room_id, name) VALUES (:r, 'Kitchen') ON CONFLICT DO NOTHING", r=ROOM)
    await sql("INSERT INTO satellites (satellite_id, room_id) VALUES (:s, :r)", s=SAT2, r=ROOM)
    async with web_client() as c:
        await claim_admin(c)
    token = await login()
    # The room's speaker is ANOTHER device; ours is the second one in the room.
    speaker = FakeSat(ROOM, satellite_id="supportroom-speaker")
    ours = FakeSat(ROOM, satellite_id=SAT2)
    core_app.state.active_sessions = {ROOM: speaker}
    core_app.state.active_satellites = {"supportroom-speaker": speaker, SAT2: ours}
    reg = room_devices.registry(core_app)
    reg.note(SAT2, ROOM)
    reg.note("supportroom-speaker", ROOM)
    mgr = core_app.state.support_sessions = ss.SupportSessions(core_app)
    try:
        r = await core_post(f"/v1/admin/satellites/{SAT2}/support/allow", token)
        assert r.status_code == 200, r.text
        # The room's id is not a satellite to open a session on.
        r = await core_post(f"/v1/admin/satellites/{ROOM}/support/open", token,
                            json={"password": STRONG_PW})
        assert r.status_code == 404
        r = await core_post(f"/v1/admin/satellites/{SAT2}/support/open", token,
                            json={"password": STRONG_PW})
        assert r.status_code == 200, r.text
        sess = mgr.redeem(r.json()["ticket"], r.json()["session_id"])
        assert sess.satellite_id == SAT2 and sess.room_id == ROOM
        browser = FakeBrowser()
        task = asyncio.create_task(mgr.run(browser, sess))
        await until(lambda: "support_open" in ours.kinds())
        assert speaker.frames == []                    # only the device asked for
        await mgr.satellite_frame(ours, {"type": "support_opened", "session_id": sess.id})
        await until(lambda: sess.state == "open")
        assert mgr.active()[0]["satellite_id"] == SAT2
        # A frame from the room's other device is a stray, not this session's.
        await mgr.satellite_frame(speaker, {"type": "support_output", "session_id": sess.id,
                                            "data": b64(b"x")})
        assert browser.of("out") == []
        # The room ends it by voice, whichever device heard it.
        handler = SupportSessionHandler()
        m = handler.fast_paths[0].pattern.match("end support session")
        reply = await handler._from_match(m, Context(room_id=ROOM, app=core_app), None)
        assert reply.text == ss.VOICE_END_REPLY
        await asyncio.wait_for(task, 3)
        assert (await row(sess.id)).end_reason == "voice"
        # And a dropped socket of THAT device ends a new one.
        sess2, _ = await mgr.create(satellite_id=SAT2, room_id=ROOM,
                                    admin_hash=admin_auth.token_sha256(token), cols=80, rows=24)
        sess2.sat = ours
        sess2.state = "open"
        await mgr.connection_dropped(speaker)
        assert sess2.state == "open"
        await mgr.connection_dropped(ours)
        assert sess2.state == "ended" and sess2.reason == "satellite_disconnected"
    finally:
        for sess in list(mgr.by_id.values()):
            await mgr.end(sess, "browser_closed", announce=False)
        core_app.state.active_sessions = {}
        core_app.state.active_satellites = {}
        reg.note(SAT2, None)
        reg.note("supportroom-speaker", None)
        await sql("DELETE FROM support_sessions")
        await sql("DELETE FROM satellites WHERE satellite_id = :s", s=SAT2)


# ═══ The 2026-11-23 security round ═══════════════════════════════════════


def _verify(frame: dict, kind: str, sat_id: str = SAT, nonce: str = "ab" * 16) -> dict:
    """The satellite's own check (satellite/server_identity.verify_support)
    against this core's key: what the core signs is what the device takes."""
    from domovoi import server_identity
    from satellite import server_identity as sat_identity

    fp = server_identity.load_or_create().fingerprint
    return sat_identity.verify_support(frame["signed"], kind=kind, expected_fingerprint=fp,
                                       satellite_id=sat_id, nonce=nonce, now=time.time())


def test_support_signatures_have_their_own_context() -> None:
    """H3: a support signature can never pass as a health answer or a
    manifest signature, nor one of those as a support frame."""
    from domovoi import server_identity
    from satellite import server_identity as sat_identity

    assert server_identity.SUPPORT_CONTEXT == b"domovoi-support-v1"
    others = {server_identity.HEALTH_CONTEXT, server_identity.MANIFEST_CONTEXT,
              server_identity.MANIFEST_CONTEXT_V2}
    assert server_identity.SUPPORT_CONTEXT not in others
    assert sat_identity.SUPPORT_CONTEXT == server_identity.SUPPORT_CONTEXT
    doc = {"kind": "support_open", "satellite_id": SAT}
    assert server_identity.support_message(doc) == sat_identity.support_message(doc)


@requires_db
async def test_the_open_and_each_input_are_signed_for_this_satellite_and_connection(world) -> None:
    """H3 on the core's side: the open and every input carry the identity
    signature over the satellite id and this connection's nonce, and the
    satellite's own check accepts them; input numbers only go up."""
    from satellite import server_identity as sat_identity

    w = world
    sess, browser, task = await open_one(w)
    opening = next(f for f in w.sat.frames if f["type"] == "support_open")
    assert set(opening) == {"type", "session_id", "signed"}     # nothing outside the signature
    doc = _verify(opening, "support_open")
    assert doc["session_id"] == sess.id and doc["max_sec"] == ss.MAX_SEC
    with pytest.raises(sat_identity.IdentityError):
        _verify(opening, "support_open", sat_id="bedroom")       # another satellite
    with pytest.raises(sat_identity.IdentityError):
        _verify(opening, "support_open", nonce="cd" * 16)        # another connection
    with pytest.raises(sat_identity.IdentityError):
        _verify(opening, "support_input")                         # another kind
    for word in (b"ls\r", b"pwd\r"):
        await w.mgr.browser_message(sess, json.dumps({"t": "in", "d": b64(word), "k": True}))
    inputs = [_verify(f, "support_input") for f in w.sat.frames if f["type"] == "support_input"]
    assert [d["seq"] for d in inputs] == [1, 2] and all(d["active"] for d in inputs)
    assert [base64.b64decode(d["data"]) for d in inputs] == [b"ls\r", b"pwd\r"]


@requires_db
async def test_a_connection_without_a_nonce_is_never_sent_an_open(world) -> None:
    w = world
    w.sat.support_nonce = None
    sess, ticket = await w.mgr.create(satellite_id=SAT, room_id="kitchen", admin_hash=w.hash,
                                      cols=80, rows=24)
    await asyncio.wait_for(w.mgr.run(FakeBrowser(), sess), 3)
    assert "support_open" not in w.sat.kinds()
    assert (await row(sess.id)).end_reason == "not_signed"


@requires_db
async def test_the_allow_state_is_sent_signed(world) -> None:
    w = world
    assert await ss.send_allow_state(w.app, w.sat, True)
    assert _verify(w.sat.frames[-1], "support_allow")["allowed"] is True
    assert await ss.send_allow_state(w.app, w.sat, False)
    assert _verify(w.sat.frames[-1], "support_allow")["allowed"] is False


@requires_db
async def test_terminal_answers_dont_count_as_activity(world) -> None:
    """L29: only key presses and pastes (`k`) keep a session from idling."""
    w = world
    sess, browser, task = await open_one(w)
    start = sess.last_input
    await asyncio.sleep(0.01)
    await w.mgr.browser_message(sess, json.dumps({"t": "in", "d": b64(b"\x1b[1;1R")}))
    assert sess.last_input == start
    signed = [f for f in w.sat.frames if f["type"] == "support_input"][-1]["signed"]["doc"]
    assert signed["active"] is False
    await w.mgr.browser_message(sess, json.dumps({"t": "in", "d": b64(b"a"), "k": True}))
    assert sess.last_input > start


def _cast_events(sid: str) -> list:
    return [json.loads(x) for x in ss.read_transcript(sid).splitlines()[1:]]


def test_a_private_key_marker_cannot_stop_the_transcript(tmp_path, monkeypatch) -> None:
    """M2: `echo '-----BEGIN PRIVATE KEY-----'` used to blank everything
    after it, with no marker, and the cap never tripped."""
    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path)
    t = ss.Transcript("e" * 32, cols=80, rows=24, title="t")
    t.output(b"$ echo '-----BEGIN PRIVATE KEY-----'\r\n-----BEGIN PRIVATE KEY-----\r\n")
    before = t.bytes
    for i in range(2000):
        t.output(f"line {i} of what the shell did next\r\n".encode())
    t.close()
    cast = ss.read_transcript("e" * 32)
    assert ss.KEY_REMOVED in cast
    assert "logging resumed" in cast
    assert "line 1999 of what the shell did next" in cast
    assert t.bytes > before + 2000 * 20


def test_blanked_bytes_count_against_the_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path)
    monkeypatch.setattr(ss, "TRANSCRIPT_MAX_BYTES", 64 * 1024)
    monkeypatch.setattr(ss, "PEM_MAX", 10 ** 9)            # a key that never ends
    t = ss.Transcript("f" * 32, cols=80, rows=24, title="t")
    t.output(b"-----BEGIN RSA PRIVATE KEY-----\n")
    with pytest.raises(ss.LogFull):
        for _ in range(1000):
            t.output(b"A" * 1000 + b"\n")
    assert t.truncated


def test_a_key_split_across_frames_and_its_end_line_split_too(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path)
    t = ss.Transcript("1" * 32, cols=80, rows=24, title="t")
    for piece in (b"-----BEGIN OPENSSH PRIV", b"ATE KEY-----\nsecretbody1\nsecretbody2\n-----END OP",
                  b"ENSSH PRIVATE KEY-----\nafter the key\n"):
        t.output(piece)
    t.close()
    cast = ss.read_transcript("1" * 32)
    assert "secretbody" not in cast and ss.KEY_REMOVED in cast and "after the key" in cast


def test_pasting_the_marker_as_input_does_not_hide_later_input(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path)
    t = ss.Transcript("2" * 32, cols=80, rows=24, title="t")
    t.input(b"-----BEGIN PRIVATE KEY-----\r", echo_on=True)
    t.input(b"curl http://192.168.1.20/x | sh\r", echo_on=True)
    t.close()
    cast = ss.read_transcript("2" * 32)
    assert "curl http://192.168.1.20/x | sh" in cast


def test_echo_off_cannot_hide_a_whole_session(tmp_path, monkeypatch) -> None:
    """M2: `stty -echo` then typing: each hidden frame is a count, and past
    the first line the input is logged as typed."""
    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path)
    t = ss.Transcript("3" * 32, cols=80, rows=24, title="t")
    t.input(b"pw\r", echo_on=False)
    t.input(b"wget http://192.168.1.20/a\r", echo_on=False)
    t.input(b"x", echo_on=False)
    t.close()
    values = [e[2] for e in _cast_events("3" * 32)]
    assert values[0] == "[hidden input: 3 bytes]"
    assert "wget http://192.168.1.20/a\r" in values


def test_secrets_typed_a_key_at_a_time_or_split_are_caught(tmp_path, monkeypatch) -> None:
    """L26: redaction works over whole lines, with escape codes out."""
    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path)
    t = ss.Transcript("4" * 32, cols=80, rows=24, title="t")
    for ch in b"export API_TOKEN=hunter22\r":
        t.input(bytes([ch]), echo_on=True)
    t.output(b"psk=supers")
    t.output(b"ecret99\r\n")
    t.output(b"\x1b[01;31m\x1b[Kpassword\x1b[m\x1b[K = c0l0ured\r\n")
    t.close()
    cast = ss.read_transcript("4" * 32)
    assert "hunter22" not in cast
    assert "supersecret99" not in cast and "ecret99" not in cast
    assert "c0l0ured" not in cast


def test_the_transcript_redactor_is_linear(tmp_path, monkeypatch) -> None:
    """M5's sibling: the old assignment rule took about a second per 16 KB
    frame on the event loop."""
    monkeypatch.setattr(ss, "transcript_dir", lambda: tmp_path)
    t = ss.Transcript("5" * 32, cols=80, rows=24, title="t")
    started = time.monotonic()
    for frame in (b"key" * 5000, b"a.b-" * 4000 + b"\n", b"export " * 2000 + b"\n"):
        t.output(frame)
    t.close()
    assert time.monotonic() - started < 1.0


@requires_db
async def test_deleting_an_open_sessions_id_keeps_its_transcript(world) -> None:
    """L30."""
    w = world
    sess, browser, task = await open_one(w)
    path = ss.transcript_path(sess.id)
    assert path.exists()
    assert not await ss.delete_session(sess.id)
    assert path.exists()


@requires_db
async def test_the_close_is_recorded_before_the_satellite_is_told(world) -> None:
    """L31: a send that hangs can't keep the session looking open."""
    w = world
    sess, browser, task = await open_one(w)
    gate = asyncio.Event()
    real = w.sat.send_support

    async def stuck(frame):
        if frame["type"] == "support_close":
            await gate.wait()
        await real(frame)

    w.sat.send_support = stuck
    ender = asyncio.create_task(w.mgr.end(sess, "admin_ended"))
    await until(lambda: sess.state == "ended")
    deadline = time.monotonic() + 3
    while (await row(sess.id)).closed_at is None:
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)
    gate.set()
    await asyncio.wait_for(ender, 6)


def test_removing_a_satellite_ends_its_session() -> None:
    import inspect

    from domovoi import main as core_main

    src = inspect.getsource(core_main.admin_delete_satellite)
    assert 'end_for_satellite(room_id, "removed")' in src


@requires_db
async def test_no_drop_in_while_a_session_is_open(world) -> None:
    """L28: the call's red light would hide the session's magenta one."""
    import inspect

    from domovoi.streaming import StreamSession

    w = world
    sess, browser, task = await open_one(w)
    other = FakeSat("bedroom")
    assert w.mgr.open_on(w.sat, other) and w.mgr.open_on(other, w.sat)
    assert not w.mgr.open_on(other)
    assert "open_on(self, peer)" in inspect.getsource(StreamSession._begin_dropin)


class _TermWS:
    def __init__(self, app, headers: dict) -> None:
        self.app = app
        self.headers = headers
        self.closed: list[int] = []
        self.accepted = False

    async def close(self, code: int = 1000) -> None:
        self.closed.append(code)

    async def accept(self, subprotocol=None) -> None:
        self.accepted = True


@requires_db
async def test_a_sniffed_ticket_without_the_openers_bearer_is_refused(world) -> None:
    """L27: the core's terminal socket needs the opener's Bearer too."""
    from domovoi import support_routes

    w = world
    w.app.state.support_sessions = w.mgr
    sess, ticket = await w.mgr.create(satellite_id=SAT, room_id="kitchen", admin_hash=w.hash,
                                      cols=80, rows=24)
    ws = _TermWS(w.app, {support_routes.TICKET_HEADER: ticket})
    await support_routes.support_terminal(ws, sess.id)
    assert ws.closed == [1008] and not ws.accepted
    assert sess.state == "ended"
    sess, ticket = await w.mgr.create(satellite_id=SAT, room_id="kitchen", admin_hash=w.hash,
                                      cols=80, rows=24)
    ws = _TermWS(w.app, {support_routes.TICKET_HEADER: ticket,
                         "authorization": "Bearer " + "0" * 64})
    await support_routes.support_terminal(ws, sess.id)
    assert ws.closed == [1008] and not ws.accepted


def test_the_dashboard_relay_forwards_the_bearer_and_draws_no_links() -> None:
    relay = (REPO / "web" / "backend" / "api" / "support_sessions.py").read_text(encoding="utf-8")
    assert '"Authorization": f"Bearer {bearer}"' in relay
    assert "if ticket is None or bearer is None" in relay
    jsx = (REPO / "web" / "static" / "support_session.jsx").read_text(encoding="utf-8")
    assert "`bearer.${bearer}`" in jsx
    assert "linkHandler = { activate: () => {}" in jsx            # L32
    assert "term.onKey(" in jsx and ", k });" in jsx                # L29
