"""Friend Houses plugin mail: the lock order (``plugin_mail`` module
docstring, "Lock order"), against the test database with real concurrent
connections.

A plugin sending to a friend while that friend's mail is being stored used
to deadlock: ``store_mail`` held the friend row ``FOR UPDATE`` and wanted
the pair; ``send`` held the pair and, inserting the sender row, needed a
``KEY SHARE`` lock on the friend row for the foreign key. Postgres aborted
one side. These tests force that interleaving with events, and also run
the two unforced in a loop; a transient database failure while storing must
end in a retryable NACK, never an ACK."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from domovoi.db.session import SessionLocal, session_scope
from domovoi.friend_houses import plugin_mail as pm
from domovoi.friend_houses import service as svc
from domovoi.tests.conftest import requires_db
from domovoi.tests.fh_plugin_mail_kit import (  # noqa: F401  (fixtures)
    add_friend,
    fake_docker,
    house,
    inbound,
    q,
)
from domovoi.tests.friend_houses_testkit import no_tasks_left_behind  # noqa: F401  (autouse)

pytestmark = requires_db

# How long one side waits for the other to take its first lock. With the
# lock order in place the other side is waiting in the database instead,
# so this runs out and the side goes on.
HOLD_SEC = 0.5


# A guard for the shared test database: no statement of these tests waits
# on a lock longer than this, so a failing run can't hold one for long.
LOCK_TIMEOUT = "15s"
STATEMENT_TIMEOUT = "30s"


@asynccontextmanager
async def _guarded_session():
    """plugin_mail's ``_db()`` for these tests: its own connection with a
    lock and a statement timeout, rolled back on ANY exit but a clean one
    (a cancel included) and always closed."""
    s = SessionLocal()
    try:
        await s.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
        await s.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
        yield s
        await s.commit()
    except BaseException:
        try:
            await asyncio.shield(s.rollback())
        except BaseException:  # noqa: BLE001 — closing below drops the connection anyway
            pass
        raise
    finally:
        try:
            await asyncio.shield(s.close())
        except BaseException:  # noqa: BLE001
            pass


@pytest.fixture
def guarded_db(monkeypatch):
    monkeypatch.setattr(pm, "_db", _guarded_session)


async def _run_both(*coros, timeout: float = 30.0) -> list:
    """Run the coroutines concurrently; their results (exceptions as
    values). Whatever is still running at the timeout, or when the test
    fails, is cancelled and waited for (bounded), so its connection is
    rolled back and closed before the test ends."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            raise AssertionError(f"{len(pending)} of the concurrent calls still waited after {timeout} s")
        return [t.exception() or t.result() for t in tasks]
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.wait(tasks, timeout=10)


async def _reset_budgets() -> None:
    await q("UPDATE friend_houses_plugin_sender SET buckets = '{}'")
    await q("UPDATE friend_houses_state SET plugin_buckets = '{}' WHERE id = 1")


async def _cleanup() -> None:
    async with session_scope() as s:
        for table in ("friend_houses_plugin_inbox", "friend_houses_plugin_outbox",
                      "friend_houses_plugin_sender", "friend_houses_plugin_pairs", "friend_houses_friends"):
            await s.execute(text(f"DELETE FROM {table}"))


async def test_send_and_store_mail_for_one_friend_never_deadlock_forced(house, tmp_path, monkeypatch,
                                                                        guarded_db) -> None:
    """store_mail takes its first lock, then send takes its first lock,
    and only then does each go for its second: the interleaving that
    deadlocked. Each round must finish with both mails in place."""
    friend = await add_friend(tmp=tmp_path)
    store_first = asyncio.Event()
    send_first = asyncio.Event()
    real_note_epoch, real_quota_ok = pm.note_epoch, pm.quota_ok

    async def note_epoch(s, f, epoch):
        ok = await real_note_epoch(s, f, epoch)
        store_first.set()          # store_mail holds the friend row
        try:
            await asyncio.wait_for(send_first.wait(), HOLD_SEC)
        except asyncio.TimeoutError:
            pass                   # send waits in the database: go on
        return ok

    async def quota_ok(s, table, friend_id, slug, size):
        if table == "outbox":
            send_first.set()       # send holds what it locks first
            try:
                await asyncio.wait_for(store_first.wait(), HOLD_SEC)
            except asyncio.TimeoutError:
                pass
        return await real_quota_ok(s, table, friend_id, slug, size)

    monkeypatch.setattr(pm, "note_epoch", note_epoch)
    monkeypatch.setattr(pm, "quota_ok", quota_ok)
    try:
        for i in range(1, 9):
            store_first.clear()
            send_first.clear()
            await _reset_budgets()
            event, _ = inbound(friend, house.keys, {"n": i}, seq=i)


            async def send_after_store():
                await asyncio.wait_for(store_first.wait(), 5)
                return await pm.send("games", "fa", b'{"n": 1}')

            results = await _run_both(pm.store_mail(event), send_after_store())
            errors = [r for r in results if isinstance(r, BaseException)]
            assert not errors, f"round {i}: {errors!r}"
            assert results[0] == "stored"
        assert await q("SELECT count(*) FROM friend_houses_plugin_inbox") == [(8,)]
        assert await q("SELECT count(*) FROM friend_houses_plugin_outbox") == [(8,)]
        assert await q("SELECT in_mark FROM friend_houses_plugin_pairs") == [(8,)]
    finally:
        await _cleanup()


async def test_send_and_store_mail_for_one_friend_in_a_loop(house, tmp_path, guarded_db) -> None:
    """Unforced: sends and stores for the same friend racing, many times."""
    friend = await add_friend(tmp=tmp_path)
    try:
        for i in range(1, 41):
            await _reset_budgets()
            event, _ = inbound(friend, house.keys, {"n": i}, seq=i)
            results = await _run_both(pm.store_mail(event), pm.send("games", "fa", b'{"n": 1}'),
                                      pm.send("games", "fa", b'{"n": 2}'))
            errors = [r for r in results if isinstance(r, BaseException)]
            assert not errors, f"round {i}: {errors!r}"
            if i % 20 == 0:   # stay under the pair's quotas
                await q("DELETE FROM friend_houses_plugin_inbox")
                await q("DELETE FROM friend_houses_plugin_outbox")
        assert await q("SELECT in_mark FROM friend_houses_plugin_pairs") == [(40,)]
    finally:
        await _cleanup()


class _Deadlock(Exception):
    sqlstate = "40P01"


def _db_error(code: str = "40P01") -> DBAPIError:
    orig = _Deadlock("deadlock detected")
    orig.sqlstate = code
    return DBAPIError("INSERT ...", {}, orig)


async def _lane_answer(event: dict, monkeypatch) -> str:
    """Run one event through the receiving lane; the line it answers."""
    lines: list[str] = []

    async def answer(line: str) -> None:
        lines.append(line)

    monkeypatch.setattr(svc, "_answer_listener", answer)
    pm.on_event(event)
    for _ in range(300):
        if lines:
            break
        await asyncio.sleep(0.01)
    assert len(lines) == 1, lines
    return lines[0]


async def test_a_storing_failure_is_a_retryable_nack_and_the_resend_is_stored(house, tmp_path,
                                                                                monkeypatch) -> None:
    """A database failure while storing (every try): the lane answers
    ``unavailable`` (the sender keeps the mail and tries again), nothing is
    stored and the pair's mark doesn't move, so the resend, the same mail
    with the same seq, is stored and then ACKed."""
    friend = await add_friend(tmp=tmp_path)
    event, env = inbound(friend, house.keys, {"n": 1}, seq=7)
    real_quota_ok = pm.quota_ok
    tries = {"n": 0}

    async def failing(s, table, *a):
        if table == "inbox":
            tries["n"] += 1
            raise _db_error()
        return await real_quota_ok(s, table, *a)

    monkeypatch.setattr(pm, "quota_ok", failing)
    line = await _lane_answer(event, monkeypatch)
    assert line == f"refused {env.msg_id.hex()} unavailable {pm.LANE_FULL_RETRY_SEC}"
    assert tries["n"] == pm.STORE_TRIES      # retried inside first
    assert await q("SELECT count(*) FROM friend_houses_plugin_inbox") == [(0,)]
    assert await q("SELECT in_mark FROM friend_houses_plugin_pairs") == [(0,)]
    monkeypatch.setattr(pm, "quota_ok", real_quota_ok)
    assert await _lane_answer(event, monkeypatch) == f"stored {env.msg_id.hex()}"
    assert await q("SELECT seq FROM friend_houses_plugin_inbox") == [(7,)]
    # And a third copy (a lost ACK) is a duplicate: ACKed, not stored twice.
    assert await _lane_answer(event, monkeypatch) == f"stored {env.msg_id.hex()}"
    assert await q("SELECT count(*) FROM friend_houses_plugin_inbox") == [(1,)]


async def test_a_deadlock_while_storing_is_retried_inside(house, tmp_path, monkeypatch) -> None:
    """One deadlock (or serialization failure) is retried at once, inside
    the lane: the mail is stored and ACKed with no NACK round trip."""
    friend = await add_friend(tmp=tmp_path)
    event, env = inbound(friend, house.keys, {"n": 1}, seq=3)
    real_quota_ok = pm.quota_ok
    tries = {"n": 0}

    async def once(s, table, *a):
        if table == "inbox":
            tries["n"] += 1
            if tries["n"] == 1:
                raise _db_error("40001")
        return await real_quota_ok(s, table, *a)

    monkeypatch.setattr(pm, "quota_ok", once)
    assert await _lane_answer(event, monkeypatch) == f"stored {env.msg_id.hex()}"
    assert tries["n"] == 2
    assert await q("SELECT seq FROM friend_houses_plugin_inbox") == [(3,)]


@pytest.mark.parametrize("code", ["23505", None])
async def test_other_storing_failures_are_not_retried_inside(house, tmp_path, monkeypatch, code) -> None:
    friend = await add_friend(tmp=tmp_path)
    event, env = inbound(friend, house.keys, {"n": 1}, seq=3)
    tries = {"n": 0}

    async def failing(s, table, *a):
        tries["n"] += 1
        raise _db_error(code) if code else RuntimeError("no")

    monkeypatch.setattr(pm, "quota_ok", failing)
    line = await _lane_answer(event, monkeypatch)
    assert line == f"refused {env.msg_id.hex()} unavailable {pm.LANE_FULL_RETRY_SEC}"
    assert tries["n"] == 1
