"""Approvals a room device can check (1.4.0, "every house"): the core side.

A device that asks every server it finds never sends its code. For its
request the core keeps no code; the code a person types is held for ONE
exchange (satellite/pairing_proof.py) on the device's next visit, and the
core binds the room — and hands the device its half of the proof — only
after the device's confirmation checks out in constant time.

The end-to-end cases run the REAL device side (``satellite.house_pairing
.visit``) against the REAL core session over an in-memory socket, on the
test database. The rest drive the repository and ``_validate_pairing``
directly.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi import join_problems, streaming
from domovoi import server_identity as core_identity
from domovoi.admin_auth import token_sha256
from domovoi.config import settings
from domovoi.db.repositories import (
    SatelliteApprovalRepository,
    SatellitePairingRepository,
)
from domovoi.db.session import engine, session_scope
from domovoi.streaming import StreamSession
from domovoi.tests.conftest import requires_db
from satellite import house_pairing, pairing_proof
from satellite.discovery import House

_MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
assert (_MIGRATIONS / "V062__satellite_approval_proof.sql").is_file()

pytestmark = requires_db

ROOM = "kitchen"
CODE = "481502"
WRONG = "930071"
MASTER = "c" * 64


@pytest_asyncio.fixture(autouse=True)
async def _clean(tmp_path, monkeypatch):
    # The core's identity key lives in a tmp dir, never the developer's.
    monkeypatch.setattr(core_identity, "identity_path", lambda: tmp_path / "server-identity.json")
    core_identity.reset_cache()
    streaming.APPROVAL_HELLO_LIMITER.reset()
    streaming.APPROVAL_REFRESH_LIMITER.reset()
    monkeypatch.setattr(streaming, "APPROVAL_CONFIRM_TIMEOUT_SEC", 3.0)
    join_problems.clear()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE satellite_pairings, satellite_approvals CASCADE"))
    yield
    core_identity.reset_cache()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE satellite_pairings, satellite_approvals CASCADE"))


def _fingerprint() -> str:
    return core_identity.load_or_create().fingerprint


# ─── an in-memory socket between the device and the core ──────────────────


class _CoreSide:
    """What StreamSession sees: send_text / receive / close, a peer."""

    def __init__(self, to_device: asyncio.Queue, from_device: asyncio.Queue) -> None:
        self._to_device = to_device
        self._from_device = from_device
        self.client = type("C", (), {"host": "192.168.1.41"})()
        self.headers: dict[str, str] = {}
        self.sent: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))
        await self._to_device.put(data)

    async def receive(self) -> dict:
        item = await self._from_device.get()
        if item is None:
            return {"type": "websocket.disconnect"}
        return {"type": "websocket.receive", "text": item}

    async def close(self, code: int = 1000) -> None:
        await self._to_device.put(None)


class _DeviceSide:
    def __init__(self, to_core: asyncio.Queue, from_core: asyncio.Queue) -> None:
        self._to_core = to_core
        self._from_core = from_core

    async def send(self, data: str) -> None:
        await self._to_core.put(data)

    async def recv(self) -> str:
        item = await self._from_core.get()
        if item is None:
            raise ConnectionError("closed")
        return item


def _bridge(sessions: list, *, swap_token: str | None = None, swap_key: str | None = None):
    """A ``connect`` for house_pairing: each call opens a socket to a fresh
    core StreamSession for the room in the URL, which reads the hello and
    runs ``_validate_pairing`` — then says ``ready`` if it accepted, as the
    real session does. ``swap_token``: something in between replaces the
    hello's pairing token before the core sees it; ``swap_key``: its
    support key."""

    @asynccontextmanager
    async def connect(url: str):
        room = url.rsplit("/", 1)[-1]
        to_core: asyncio.Queue = asyncio.Queue()
        to_device: asyncio.Queue = asyncio.Queue()
        core_ws = _CoreSide(to_device, to_core)
        sess = StreamSession(core_ws, room)  # type: ignore[arg-type]
        sessions.append(sess)

        async def serve() -> None:
            hello = json.loads(await to_core.get())
            if swap_token is not None:
                hello["pairing_token"] = swap_token
            if swap_key is not None:
                hello["support_key"] = swap_key
            if await sess._validate_pairing(hello):
                await core_ws.send_text(json.dumps({"type": "ready", "room_id": room}))
            await to_device.put(None)

        task = asyncio.create_task(serve())
        try:
            yield _DeviceSide(to_core, to_device)
        finally:
            await to_core.put(None)
            try:
                await asyncio.wait_for(task, 10)
            except (asyncio.TimeoutError, Exception):
                task.cancel()

    return connect


def _house(version: int = 0) -> House:
    """``version`` 0: built by hand, as a device from before the signed
    version (it runs version 1); 2: a house that signed ``pairing_proof=2``."""
    return House(url="ws://192.168.1.20:6370", fingerprint=_fingerprint(), proof=True,
                 name="Domovoi", via="name", proof_version=version)


async def _visit(code: str = CODE, *, budget=None, tmp=None, sessions=None,
                 version: int = 0, swap_token: str | None = None,
                 support_key: str | None = None, swap_key: str | None = None):
    house = _house(version)
    sessions = [] if sessions is None else sessions
    budget = budget or house_pairing.ProofBudget(tmp / "budget.json")
    token = house_pairing.house_token(MASTER, house.fingerprint)
    hello = {"type": "hello", "approval_code": code}
    if support_key is not None:
        hello["support_key"] = support_key
    result = await house_pairing.visit(
        house, room_id=ROOM, code=code, hello=hello,
        token=token, connect=_bridge(sessions, swap_token=swap_token, swap_key=swap_key),
        budget=budget,
    )
    return result, token, sessions


async def _type_code(code: str) -> str:
    async with session_scope() as s:
        return await SatelliteApprovalRepository(s).approve(ROOM, code)


async def _row(room: str = ROOM):
    async with session_scope() as s:
        return (await s.execute(text(
            "SELECT * FROM satellite_approvals WHERE satellite_id = :r"), {"r": room}
        )).mappings().first()


async def _pairing(room: str = ROOM):
    async with session_scope() as s:
        return await SatellitePairingRepository(s).get_pairing(room)


# ─── the whole thing, device and core ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_device_parks_without_ever_sending_its_code(tmp_path) -> None:
    result, token, sessions = await _visit(tmp=tmp_path)
    assert result.kind == "parked"
    row = await _row()
    assert row["proof"] is True and row["code"] is None
    assert row["token_hash"] == token_sha256(token)
    # What the core sent back: waiting, with the proof marker, and no code.
    last = sessions[0].ws.sent[-1]
    assert last["reason"] == "awaiting_approval" and last["proof"] is True
    assert "code" not in last
    # The code is nowhere in the database.
    async with session_scope() as s:
        dump = (await s.execute(text("SELECT row_to_json(a)::text FROM satellite_approvals a"))).scalar()
    assert CODE not in dump


@pytest.mark.asyncio
async def test_the_right_code_typed_approves_and_the_device_checks_the_proof(tmp_path) -> None:
    await _visit(tmp=tmp_path)
    assert await _type_code(CODE) == "entered"
    assert (await _row())["entered_code"] == CODE
    result, token, sessions = await _visit(tmp=tmp_path)
    assert result.kind == "approved"
    pairing = await _pairing()
    assert pairing is not None and pairing[0] == token_sha256(token)
    assert await _row() is None
    kinds = [f.get("type") for f in sessions[0].ws.sent]
    assert kinds.index("approval_challenge") < kinds.index("approval_proof")


@pytest.mark.asyncio
async def test_a_token_bound_device_is_approved_by_this_core(tmp_path) -> None:
    """L9 (1.5.0): a device that saw ``pairing_proof=2`` signed says so in
    its hello and binds its token into the exchange; this core runs the
    same transcript, approves it, and binds that token."""
    await _visit(tmp=tmp_path, version=2)
    assert await _type_code(CODE) == "entered"
    result, token, sessions = await _visit(tmp=tmp_path, version=2)
    assert result.kind == "approved"
    pairing = await _pairing()
    assert pairing is not None and pairing[0] == token_sha256(token)


@pytest.mark.asyncio
async def test_a_version_one_device_is_still_approved(tmp_path) -> None:
    """An older device (no ``pairing_proof`` in its hello) still runs the
    version-1 exchange with this core."""
    await _visit(tmp=tmp_path, version=1)
    assert await _type_code(CODE) == "entered"
    result, token, _sessions = await _visit(tmp=tmp_path, version=1)
    assert result.kind == "approved"
    pairing = await _pairing()
    assert pairing is not None and pairing[0] == token_sha256(token)


@pytest.mark.asyncio
async def test_a_swapped_token_fails_the_token_bound_exchange(tmp_path) -> None:
    """L9: something in between forwards the exchange but puts its own
    token in the hello. In version 2 the two sides then compute different
    transcripts: the device's confirmation fails, nothing is bound, and the
    device gets no proof."""
    other = "f" * 64
    await _visit(tmp=tmp_path, version=2, swap_token=other)
    assert await _type_code(CODE) == "entered"
    result, _token, sessions = await _visit(tmp=tmp_path, version=2, swap_token=other)
    assert result.kind == "mismatch"
    assert await _pairing() is None
    assert all(f.get("type") != "approval_proof" for f in sessions[0].ws.sent)


@pytest.mark.asyncio
async def test_an_unknown_exchange_version_is_refused() -> None:
    sess = _sess()
    dev = pairing_proof.DeviceExchange(CODE)
    ok = await sess._validate_pairing({
        "type": "hello", "room_id": ROOM, "pairing_token": MASTER,
        "approval_pake": dev.message, "pairing_proof": 9,
    })
    assert ok is False
    assert sess.ws.sent[-1]["reason"] == "approval_pake_invalid"


@pytest.mark.asyncio
async def test_a_wrong_code_typed_binds_nothing_and_gets_no_proof(tmp_path) -> None:
    """The core gives its half of the proof only after the device's half
    checks out — a device that does not hold the typed code gets nothing
    to test codes against, and nothing is bound."""
    await _visit(tmp=tmp_path)
    assert await _type_code(WRONG) == "entered"
    result, _token, sessions = await _visit(tmp=tmp_path)
    assert result.kind == "mismatch"
    assert await _pairing() is None
    assert all(f.get("type") != "approval_proof" for f in sessions[0].ws.sent)
    row = await _row()
    assert row["proof_failures"] == 1 and row["entered_code"] is None
    listed = [r async for r in _alist()]
    assert listed[0]["code_rejected"] is True and listed[0]["waiting_for_device"] is False


async def _alist():
    async with session_scope() as s:
        for r in await SatelliteApprovalRepository(s).list_pending():
            yield r


@pytest.mark.asyncio
async def test_a_typed_code_serves_one_exchange_only(tmp_path) -> None:
    """A rogue device holding the request gets ONE guess per code typed:
    after a failed run the code is gone, the next visit just parks."""
    await _visit(tmp=tmp_path)
    await _type_code(CODE)
    first, _t, _s = await _visit(WRONG, tmp=tmp_path)   # a device that guessed
    assert first.kind == "mismatch"
    second, _t, _s = await _visit(CODE, tmp=tmp_path)   # the right code, too late
    assert second.kind == "parked"
    assert await _pairing() is None


@pytest.mark.asyncio
async def test_a_proof_for_another_server_key_is_refused(tmp_path, monkeypatch) -> None:
    """The exchange binds the server's fingerprint: a device that believes it
    is talking to key B cannot be approved by a server whose key is A, even
    one that knows the code — a proof cannot be carried to another server."""
    await _visit(tmp=tmp_path)
    await _type_code(CODE)
    other = House(url="ws://192.168.1.20:6370", fingerprint="SHA256:" + "Z" * 43,
                  proof=True, name="x", via="name")
    sessions: list = []
    result = await house_pairing.visit(
        other, room_id=ROOM, code=CODE, hello={"type": "hello"},
        token=house_pairing.house_token(MASTER, _fingerprint()),
        connect=_bridge(sessions), budget=house_pairing.ProofBudget(tmp_path / "b.json"),
    )
    assert result.kind == "mismatch"
    assert await _pairing() is None


@pytest.mark.asyncio
async def test_withdraw_drops_only_the_requests_own_row(tmp_path) -> None:
    await _visit(tmp=tmp_path)
    house = _house()
    wrong = await house_pairing.withdraw(house, room_id=ROOM, token="d" * 64,
                                         connect=_bridge([]))
    assert wrong is False and await _row() is not None
    ok = await house_pairing.withdraw(house, room_id=ROOM,
                                      token=house_pairing.house_token(MASTER, house.fingerprint),
                                      connect=_bridge([]))
    assert ok is True and await _row() is None


@pytest.mark.asyncio
async def test_a_withdraw_never_touches_a_pairing(tmp_path) -> None:
    house = _house()
    token = house_pairing.house_token(MASTER, house.fingerprint)
    async with session_scope() as s:
        await SatellitePairingRepository(s).pair(ROOM, token_sha256(token))
    ok = await house_pairing.withdraw(house, room_id=ROOM, token=token, connect=_bridge([]))
    assert ok is False and await _pairing() is not None


# ─── the core's own rules ─────────────────────────────────────────────────


def _sess() -> StreamSession:
    return StreamSession(_CoreSide(asyncio.Queue(), asyncio.Queue()), ROOM)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_proof_hello_never_pairs_on_trust_even_when_lenient(monkeypatch) -> None:
    monkeypatch.setattr(settings, "satellite_pairing_strict", False)
    sess = _sess()
    ex = pairing_proof.DeviceExchange(CODE)
    assert await sess._validate_pairing({"pairing_token": MASTER, "approval_pake": ex.message}) is False
    assert await _pairing() is None


@pytest.mark.asyncio
async def test_a_proof_path_failure_fails_closed_when_lenient(monkeypatch) -> None:
    monkeypatch.setattr(settings, "satellite_pairing_strict", False)

    async def boom(*a, **k):
        raise RuntimeError("database gone")

    monkeypatch.setattr(SatelliteApprovalRepository, "request", boom)
    sess = _sess()
    ex = pairing_proof.DeviceExchange(CODE)
    assert await sess._validate_pairing({"pairing_token": MASTER, "approval_pake": ex.message}) is False
    assert sess.ws.sent[-1]["reason"] == "approval_unavailable"


@pytest.mark.asyncio
async def test_a_garbage_exchange_message_is_refused() -> None:
    sess = _sess()
    one = "0" * (pairing_proof.ELEMENT_HEX_LEN - 1) + "1"          # the identity
    minus_one = pairing_proof.encode(pairing_proof._P - 1)           # order 2
    for bad in ("00", one, minus_one, "zz" * 256, 5, None):
        assert await sess._validate_pairing({"pairing_token": MASTER, "approval_pake": bad}) is False
        assert sess.ws.sent[-1]["reason"] == "approval_pake_invalid"
    assert await _row() is None


@pytest.mark.asyncio
async def test_a_paired_room_takes_the_ordinary_token_check() -> None:
    async with session_scope() as s:
        await SatellitePairingRepository(s).pair(ROOM, token_sha256(MASTER))
    sess = _sess()
    ex = pairing_proof.DeviceExchange(CODE)
    assert await sess._validate_pairing({"pairing_token": MASTER, "approval_pake": ex.message}) is True
    other = _sess()
    assert await other._validate_pairing({"pairing_token": "e" * 64, "approval_pake": ex.message}) is False


@pytest.mark.asyncio
async def test_refreshes_of_its_own_request_do_not_spend_the_new_name_budget() -> None:
    ex = pairing_proof.DeviceExchange(CODE)
    for _ in range(streaming.APPROVAL_HELLO_MAX_PER_WINDOW + 5):
        sess = _sess()
        await sess._validate_pairing({"pairing_token": MASTER, "approval_pake": ex.message})
        assert sess.ws.sent[-1]["reason"] == "awaiting_approval"


# ─── the repository ───────────────────────────────────────────────────────


async def _request(token_hash="a" * 64, **kw):
    async with session_scope() as s:
        return await SatelliteApprovalRepository(s).request(ROOM, token_hash=token_hash, **kw)


@pytest.mark.asyncio
async def test_a_proof_request_keeps_no_code_even_if_one_is_offered() -> None:
    assert await _request(code=CODE, proof=True) == ("parked", None)
    assert (await _row())["code"] is None


@pytest.mark.asyncio
async def test_typed_codes_must_be_six_digits_for_a_proof_request() -> None:
    await _request(proof=True)
    assert await _type_code("1234") == "malformed"
    assert await _type_code("12345a") == "malformed"
    assert await _type_code(CODE) == "entered"


@pytest.mark.asyncio
async def test_only_the_requests_device_can_take_the_typed_code() -> None:
    await _request(proof=True)
    await _type_code(CODE)
    async with session_scope() as s:
        repo = SatelliteApprovalRepository(s)
        assert await repo.take_entered_code(ROOM, "b" * 64) is None
        assert await repo.take_entered_code(ROOM, "a" * 64) == CODE
        assert await repo.take_entered_code(ROOM, "a" * 64) is None


@pytest.mark.asyncio
async def test_a_typed_code_goes_stale() -> None:
    await _request(proof=True)
    await _type_code(CODE)
    async with session_scope() as s:
        await s.execute(text("UPDATE satellite_approvals SET entered_at = now() - interval '11 minutes'"))
    async with session_scope() as s:
        assert await SatelliteApprovalRepository(s).take_entered_code(ROOM, "a" * 64) is None





@pytest.mark.asyncio
async def test_the_list_says_a_typed_code_is_waiting_for_the_device() -> None:
    await _request(proof=True)
    rows = [r async for r in _alist()]
    assert rows[0]["proof"] is True and rows[0]["has_code"] is True
    assert rows[0]["waiting_for_device"] is False
    await _type_code(CODE)
    rows = [r async for r in _alist()]
    assert rows[0]["waiting_for_device"] is True
    assert CODE not in str(rows[0])


@pytest.mark.asyncio
async def test_a_legacy_request_still_compares_codes_in_the_core() -> None:
    """Older devices (and phones in room mode) send their code: unchanged."""
    await _request(code=CODE)
    assert await _type_code(WRONG) == "mismatch"
    assert await _type_code(CODE) == "approved"
    assert await _pairing() is not None


# ─── a run with no guess in it spends no typed code (SAT-14 / SAT-15) ──────


async def _challenge_then(after: str | None, tmp_path) -> dict:
    """Park, have a code typed, open the run — then the device sends
    ``after`` (a frame type) or goes away (None). The core's last frame."""
    await _visit(tmp=tmp_path)
    await _type_code(CODE)
    token = house_pairing.house_token(MASTER, _fingerprint())
    device = pairing_proof.DeviceExchange(CODE)
    sessions: list = []
    async with _bridge(sessions)(f"ws://192.168.1.20:6370/v1/stream/{ROOM}") as ws:
        await ws.send(json.dumps({"type": "hello", "room_id": ROOM, "pairing_token": token,
                                  "approval_pake": device.message}))
        frame = json.loads(await ws.recv())
        assert frame["type"] == "approval_challenge"
        if after is not None:
            await ws.send(json.dumps({"type": after, "reason": "budget"}))
    return sessions[0].ws.sent[-1]


@pytest.mark.asyncio
async def test_a_declined_exchange_keeps_the_typed_code_and_counts_nothing(tmp_path) -> None:
    """A device that declines a challenge (busy with another house, out of
    budget, holding this server back) made no guess. The core used to
    treat it as a failed run: the typed code gone, a failure counted, the
    card saying the device rejected the code."""
    last = await _challenge_then("approval_decline", tmp_path)
    assert last.get("reason") == "approval_declined"
    row = await _row()
    assert row["entered_code"] == CODE and row["proof_failures"] == 0
    result, _token, _s = await _visit(tmp=tmp_path)     # and the next visit uses it
    assert result.kind == "approved"


@pytest.mark.asyncio
async def test_a_device_that_goes_away_mid_exchange_spent_no_guess(tmp_path) -> None:
    await _challenge_then(None, tmp_path)
    row = await _row()
    assert row["entered_code"] == CODE and row["proof_failures"] == 0


@pytest.mark.asyncio
async def test_a_device_out_of_budget_declines_and_the_code_waits(tmp_path) -> None:
    await _visit(tmp=tmp_path)
    await _type_code(CODE)
    spent = house_pairing.ProofBudget(tmp_path / "spent.json", max_attempts=1)
    assert spent.spend()
    result, _token, _s = await _visit(budget=spent, tmp=tmp_path)
    assert result.kind == "declined"
    row = await _row()
    assert row["entered_code"] == CODE and row["proof_failures"] == 0
    assert await _pairing() is None


@pytest.mark.asyncio
async def test_a_wrong_confirmation_still_uses_the_code_up(tmp_path) -> None:
    """The one run that IS a guess: a confirmation that does not check out."""
    await _visit(tmp=tmp_path)
    await _type_code(WRONG)
    result, _t, _s = await _visit(tmp=tmp_path)
    assert result.kind == "mismatch"
    row = await _row()
    assert row["entered_code"] is None and row["proof_failures"] == 1


@pytest.mark.asyncio
async def test_a_stale_typed_code_is_forgotten_not_kept() -> None:
    await _request(proof=True)
    await _type_code(CODE)
    async with session_scope() as s:
        await s.execute(text("UPDATE satellite_approvals SET entered_at = now() - interval '11 minutes'"))
    async with session_scope() as s:
        await SatelliteApprovalRepository(s).list_pending()
    row = await _row()
    assert row["entered_code"] is None and row["entered_at"] is None


# ─── CORE-41: the limiter before the arithmetic, and off the loop ─────────


@pytest.mark.asyncio
async def test_bad_exchange_messages_are_throttled_before_any_arithmetic(monkeypatch) -> None:
    """CORE-41 PoC: the element check (a 2048-bit modular exponentiation)
    ran on the event loop for every hello BEFORE the per-source limiter —
    and a message that failed it never reached the limiter at all, so one
    address could make the core do that work as fast as it could send."""
    calls: list[int] = []
    real = pairing_proof.decode
    monkeypatch.setattr(pairing_proof, "decode", lambda t: calls.append(1) or real(t))
    junk = pairing_proof.encode(pairing_proof._P - 1)          # well-formed, wrong subgroup
    for _ in range(streaming.APPROVAL_HELLO_MAX_PER_WINDOW + 10):
        sess = _sess()
        await sess._validate_pairing({"pairing_token": MASTER, "approval_pake": junk})
    assert len(calls) <= streaming.APPROVAL_HELLO_MAX_PER_WINDOW
    assert sess.ws.sent[-1]["reason"] == "approval_throttled"


@pytest.mark.asyncio
async def test_the_exchange_arithmetic_runs_off_the_event_loop(tmp_path, monkeypatch) -> None:
    ran: list[str] = []
    real = asyncio.to_thread

    async def spy(fn, *a, **k):
        ran.append(getattr(fn, "__name__", repr(fn)))
        return await real(fn, *a, **k)

    monkeypatch.setattr(streaming.asyncio, "to_thread", spy)
    await _visit(tmp=tmp_path)
    await _type_code(CODE)
    result, _t, _s = await _visit(tmp=tmp_path)
    assert result.kind == "approved"
    assert "decode" in ran and "ServerExchange" in ran


@pytest.mark.asyncio
async def test_withdrawals_are_throttled_per_source() -> None:
    for _ in range(streaming.APPROVAL_HELLO_MAX_PER_WINDOW + 5):
        sess = _sess()
        await sess._validate_pairing({"pairing_token": "d" * 64, "approval_withdraw": True})
    assert sess.ws.sent[-1]["reason"] == "approval_throttled"


# ─── the support key rides the approval (pairing_proof 3, 1.5.0) ───────────

KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
KEY_B = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="


@pytest_asyncio.fixture
async def _no_satellite_row():
    async def wipe() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM satellites WHERE satellite_id = :r"), {"r": ROOM})
    await wipe()
    yield
    await wipe()


async def _pinned(room: str = ROOM):
    async with session_scope() as s:
        row = (await s.execute(text(
            "SELECT support_key FROM satellites WHERE satellite_id = :r"), {"r": room}
        )).first()
    return row.support_key if row is not None else None


async def _approve_with_key(tmp_path, key: str | None, **kw):
    await _visit(tmp=tmp_path, version=3, support_key=key, **kw)
    assert await _type_code(CODE) == "entered"
    return await _visit(tmp=tmp_path, version=3, support_key=key, **kw)


@pytest.mark.asyncio
async def test_the_support_key_is_pinned_at_approval(tmp_path, _no_satellite_row) -> None:
    """A device that saw ``pairing_proof=3`` signed binds its support key
    into the exchange; the approval pins exactly that key."""
    result, token, sessions = await _approve_with_key(tmp_path, KEY_A)
    assert result.kind == "approved"
    assert sessions[0].ws.sent[-2]["type"] == "approval_proof"
    assert (await _pairing())[0] == token_sha256(token)
    assert await _pinned() == KEY_A


@pytest.mark.asyncio
async def test_a_support_key_swapped_in_transit_is_refused(tmp_path, _no_satellite_row) -> None:
    """Something in between forwards the exchange but puts its own key in
    the hello: the transcripts differ, the device's confirmation fails,
    nothing is bound and no key is pinned."""
    result, _token, sessions = await _approve_with_key(tmp_path, KEY_A, swap_key=KEY_B)
    assert result.kind == "mismatch"
    assert await _pairing() is None and await _pinned() is None
    assert all(f.get("type") != "approval_proof" for f in sessions[0].ws.sent)


@pytest.mark.asyncio
async def test_a_support_key_added_or_stripped_in_transit_is_refused(tmp_path,
                                                                     _no_satellite_row) -> None:
    result, _t, _s = await _approve_with_key(tmp_path, None, swap_key=KEY_B)
    assert result.kind == "mismatch" and await _pinned() is None
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE satellite_approvals CASCADE"))
    # A fresh device budget: a failed run holds the house back for a while.
    (tmp_path / "again").mkdir()
    result, _t, _s = await _approve_with_key(tmp_path / "again", KEY_A, swap_key="")
    assert result.kind == "mismatch" and await _pairing() is None


@pytest.mark.asyncio
async def test_a_re_pair_re_pins_the_support_key(tmp_path, _no_satellite_row) -> None:
    result, _t, _s = await _approve_with_key(tmp_path, KEY_A)
    assert result.kind == "approved" and await _pinned() == KEY_A
    # Reset the room's pairing (a reflashed card): the next approval pins
    # the key the device holds now.
    async with session_scope() as s:
        await s.execute(text("DELETE FROM satellite_pairings WHERE satellite_id = :r"),
                        {"r": ROOM})
    result, _t, _s = await _approve_with_key(tmp_path, KEY_B)
    assert result.kind == "approved" and await _pinned() == KEY_B
    # A device approved by an older exchange binds no key: the old pin goes.
    async with session_scope() as s:
        await s.execute(text("DELETE FROM satellite_pairings WHERE satellite_id = :r"),
                        {"r": ROOM})
    await _visit(tmp=tmp_path, version=2, support_key=KEY_A)
    assert await _type_code(CODE) == "entered"
    result, _t, _s = await _visit(tmp=tmp_path, version=2, support_key=KEY_A)
    assert result.kind == "approved" and await _pinned() is None


@pytest.mark.asyncio
async def test_a_code_approved_request_pins_its_hellos_key(_no_satellite_row) -> None:
    """The older way (the device's code in its hello, compared in the
    core): the key in the parked hello is pinned when the code is typed."""
    await _request(code=CODE, support_key=KEY_A)
    assert (await _row())["support_key"] == KEY_A
    await _request(code=CODE, support_key="not a key")      # a refresh: latest hello's
    assert (await _row())["support_key"] is None
    await _request(code=CODE, support_key=KEY_A)
    assert await _type_code(CODE) == "approved"
    assert await _pinned() == KEY_A
    # A proof request keeps no key on its row: the run that approves it
    # passes the one it bound.
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE satellite_approvals CASCADE"))
    await _request(token_hash="b" * 64, proof=True, support_key=KEY_A)
    assert (await _row())["support_key"] is None
