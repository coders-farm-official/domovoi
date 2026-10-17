"""Voice history (owner, 2026-10-17): "Keep voice history", "Delete all
voice history", and "forget me" / forget person deleting what that person
said (domovoi/voice_history.py, workers/voice_history_pruner.py, V030).

* the setting: its values, how a hand-written .env value reads (never
  shorter than asked), its registry row and its admin gate;
* the retention run deletes exactly what is older than the setting, from
  every store it names (both logs, idle sessions, old exchanges in a live
  session, the satellites' logs on the server), and nothing else —
  memories, voice samples, notes, the music played, chat threads stay;
* "forever" deletes nothing;
* "Delete all" runs as a job: once, resumable, its failures kept;
* "forget me" (the router's turn) and People → forget person delete that
  person's own turns only — never a turn that merely names them;
* the routes' tiers; V030 on a populated lane; the batching under a large
  table, with a timing check; and the router still writing both rows.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import voice_history
from domovoi.config import (
    VOICE_HISTORY_KEEP_CHOICES,
    normalize_voice_history_keep,
    settings,
    voice_history_keep_days,
)
from domovoi.config_schema import FIELD_BY_NAME, coerce_and_validate
from domovoi.db.session import engine
from domovoi.satellite_health import RetainedLog
from domovoi.tests.conftest import requires_db

DAY = timedelta(days=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── The setting (DB-free) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "read_as"),
    [
        ("30", "30"), ("90", "90"), ("365", "365"), ("forever", "forever"),
        ("", "90"), (None, "90"), ("  FOREVER ", "forever"), ("always", "forever"),
        ("0", "forever"), ("-5", "forever"),
        ("1 year", "365"), ("1y", "365"), ("year", "365"), ("90d", "90"), ("30 days", "30"),
        # A whole number of days rounds UP: never shorter than asked.
        ("7", "30"), ("45", "90"), ("91", "365"), ("366", "forever"), (60, "90"),
        # Words that could mean "keep none" are not read as forever OR as
        # zero: they are the default, and the log says so.
        ("never", "90"), ("none", "90"), ("off", "90"), ("banana", "90"),
    ],
)
def test_the_setting_reads_a_hand_written_value_never_shorter(raw, read_as) -> None:
    assert normalize_voice_history_keep(raw) == read_as


def test_keep_days() -> None:
    assert [voice_history_keep_days(v) for v in VOICE_HISTORY_KEEP_CHOICES] == [30, 90, 365, None]
    assert type(settings).model_fields["voice_history_keep"].default == "90"


def test_the_setting_is_in_the_privacy_group_with_its_four_choices() -> None:
    spec = FIELD_BY_NAME["voice_history_keep"]
    assert (spec.label, spec.group, spec.type, spec.section) == (
        "Keep voice history", "Privacy", "choice", "common",
    )
    assert spec.choices == ["30", "90", "365", "forever"]
    assert spec.choice_labels == {
        "30": "30 days", "90": "90 days", "365": "1 year", "forever": "Forever",
    }
    # A save prunes at once (the reapply hook), it doesn't wait a day.
    assert spec.tier == "reapply"
    for v in spec.choices:
        assert coerce_and_validate(spec, v) == v
    with pytest.raises(ValueError):
        coerce_and_validate(spec, "60")


def test_a_saved_setting_runs_the_retention_through_its_reapply_hook() -> None:
    from domovoi import main, reapply

    main._register_core_reapply_hooks()
    hooks = reapply._HOOKS["voice_history_keep"]
    assert list(hooks.values()) == [voice_history.schedule_retention_now]


def test_the_worker_is_registered_and_stub_suppressed() -> None:
    from domovoi.workers.voice_history_pruner import VoiceHistoryPruner

    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert 'WORKERS.add_worker(VoiceHistoryPruner(), owner="core")' in src
    assert "voice_history.use_retained_log(app.state.satellite_retained_log)" in src
    assert VoiceHistoryPruner.interval_setting == "voice_history_pruner_interval_sec"
    assert settings.voice_history_pruner_interval_sec == 3600.0
    assert VoiceHistoryPruner.stub_suppressed is True


# ─── The satellites' logs on the server (DB-free) ───────────────────────────


def _stamp(dt: datetime) -> float:
    return dt.timestamp()


def test_retained_log_drops_lines_before_the_cutoff(tmp_path) -> None:
    rl = RetainedLog(tmp_path)
    now = _now()
    rl.append("kitchen", "heard: an old question\nreply: an old answer", offline=False,
              now=_stamp(now - 40 * DAY))
    rl.append("kitchen", "heard: a new question", offline=False, now=_stamp(now - DAY))
    rl.append("office", "heard: only old", offline=True, now=_stamp(now - 50 * DAY))
    (tmp_path / "kitchen.log.1").write_text(
        (now - 60 * DAY).strftime("%Y-%m-%dT%H:%M:%SZ") + " heard: rotated away\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a log\n", encoding="utf-8")

    assert rl.drop_before(now - 30 * DAY) == 4
    assert (tmp_path / "kitchen.log").read_text(encoding="utf-8").endswith(" heard: a new question\n")
    assert "old" not in (tmp_path / "kitchen.log").read_text(encoding="utf-8")
    assert not (tmp_path / "office.log").exists()
    assert not (tmp_path / "kitchen.log.1").exists()
    assert (tmp_path / "notes.txt").exists()
    assert rl.drop_before(now - 30 * DAY) == 0
    rl.append("kitchen", "heard: after the trim", offline=False)
    assert rl.tail("kitchen", 4096).count("\n") == 2


# ─── DB-backed ──────────────────────────────────────────────────────────────


@pytest.fixture
def vh(db_session, tmp_path, monkeypatch):
    """A clean history: the per-test truncate plus V030's job table, the
    satellites' logs in a tmp dir, the setting at its default, no pause
    between batches."""

    async def _clean():
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE voice_history_jobs, chat_threads, media_plays "
                                    "RESTART IDENTITY CASCADE"))

    rl = RetainedLog(tmp_path / "satellite-logs")
    monkeypatch.setattr(voice_history, "BATCH_PAUSE_SEC", 0.0)
    monkeypatch.setattr(settings, "voice_history_keep", "90")
    voice_history.use_retained_log(rl)
    yield _clean, rl
    voice_history.use_retained_log(None)


async def _exec(sql: str, **params) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


async def _scalar(sql: str, **params):
    async with engine.begin() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def _person(name: str) -> int:
    async with engine.begin() as conn:
        return int((await conn.execute(
            text("INSERT INTO people (name) VALUES (:n) RETURNING id"), {"n": name})).scalar_one())


async def _session(*, started: datetime, last: datetime, context: dict | None = None,
                   room: str = "kitchen") -> str:
    sid = str(uuid4())
    await _exec(
        "INSERT INTO sessions (id, room_id, started_at, last_activity, context) "
        "VALUES (CAST(:id AS uuid), :room, :st, :la, CAST(:ctx AS jsonb))",
        id=sid, room=room, st=started, la=last, ctx=json.dumps(context or {}),
    )
    return sid


async def _turn(at: datetime, said: str, *, person: int | None = None, room: str = "kitchen",
                session: str | None = None) -> None:
    """One routed turn as the router writes it: a row in each log."""
    await _exec(
        "INSERT INTO conversation_log (session_id, room_id, person_id, user_text, assistant_text, at) "
        "VALUES (CAST(:sid AS uuid), :room, :pid, :said, 'ok', :at)",
        sid=session, room=room, pid=person, said=said, at=at,
    )
    await _exec(
        "INSERT INTO intents_log (room_id, transcript, person_id, matched_path, at) "
        "VALUES (:room, :said, :pid, 'fast', :at)",
        room=room, pid=person, said=said, at=at,
    )


async def _said() -> list[str]:
    async with engine.begin() as conn:
        conv = sorted(r[0] for r in (await conn.execute(text("SELECT user_text FROM conversation_log"))).all())
        intents = sorted(r[0] for r in (await conn.execute(text(
            "SELECT transcript FROM intents_log WHERE transcript NOT LIKE '[ui] %'"))).all())
    assert conv == intents, (conv, intents)
    return conv


async def _context(sid: str) -> dict | None:
    async with engine.begin() as conn:
        row = (await conn.execute(text("SELECT context FROM sessions WHERE id = CAST(:id AS uuid)"),
                                  {"id": sid})).first()
    return None if row is None else dict(row[0])


def _entry(role: str, said: str, at: datetime) -> dict:
    return {"role": role, "text": said, "at": at.isoformat()}


async def _keepers(now: datetime) -> None:
    """What voice history must never touch, all of it far older than any
    setting: a memory, a voice sample, a note, a play, a chat thread."""
    long_ago = now - 1000 * DAY
    pid = await _person("Keeper")
    await _exec("INSERT INTO memories (person_id, body, source, created_at) "
                "VALUES (:p, 'likes jazz', 'implicit', :t)", p=pid, t=long_ago)
    await _exec("INSERT INTO voice_profiles (person_id, embedding, model, enrolled_at) "
                "VALUES (:p, :e, 'resemblyzer-v1', :t)", p=pid, e=b"\x00" * 1024, t=long_ago)
    await _exec("INSERT INTO voice_notes (room_id, text, created_at) VALUES ('kitchen', 'buy milk', :t)",
                t=long_ago)
    await _exec("INSERT INTO media_plays (room_id, source, title, started_at) "
                "VALUES ('kitchen', 'library', 'Song', :t)", t=long_ago)
    await _exec("INSERT INTO chat_threads (title, created_at, updated_at) VALUES ('a chat', :t, :t)",
                t=long_ago)


async def _keepers_left() -> tuple:
    out = []
    async with engine.begin() as conn:
        for t in ("memories", "voice_profiles", "voice_notes", "media_plays", "chat_threads", "people"):
            out.append((await conn.execute(text(f"SELECT COUNT(*) FROM {t}"))).scalar())
    return tuple(out)


@requires_db
@pytest.mark.asyncio
async def test_the_retention_deletes_exactly_what_is_older_than_the_setting(vh) -> None:
    clean, rl = vh
    await clean()
    now = _now()
    await _keepers(now)
    # The two logs, either side of 90 days (an hour of margin each way).
    for days, said in ((400, "a year ago"), (90.05, "just past"), (89.95, "just inside"), (1, "yesterday")):
        await _turn(now - days * DAY, said)
    # A session idle since before the cutoff goes whole.
    idle = await _session(started=now - 200 * DAY, last=now - 95 * DAY,
                          context={"recent_turns": [_entry("user", "old", now - 95 * DAY)],
                                   "last_played_track": {"title": "x"}})
    # One still in use keeps its newer exchanges, its other keys and its last answer.
    live = await _session(
        started=now - 200 * DAY, last=now - DAY,
        context={"recent_turns": [
            _entry("user", "150d", now - 150 * DAY), _entry("assistant", "150d answer", now - 150 * DAY),
            {"role": "user", "text": "undated"},
            _entry("user", "2d", now - 2 * DAY), _entry("assistant", "2d answer", now - 2 * DAY),
        ], "last_assistant_response": "2d answer", "plugins": {"radio": {"k": 1}}},
    )
    # One in use whose exchanges are all older: its last answer goes with them.
    stale = await _session(
        started=now - 300 * DAY, last=now - DAY,
        context={"recent_turns": [_entry("user", "old", now - 120 * DAY)],
                 "last_assistant_response": "old answer", "conversational_mode": True},
    )
    # A session that began inside the window is not touched.
    young = await _session(started=now - 10 * DAY, last=now - 10 * DAY,
                           context={"recent_turns": [_entry("user", "10d", now - 10 * DAY)],
                                    "last_assistant_response": "10d answer"})
    rl.append("kitchen", "heard: a year ago", offline=False, now=_stamp(now - 400 * DAY))
    rl.append("kitchen", "heard: yesterday", offline=False, now=_stamp(now - DAY))

    job = await voice_history.run_retention()

    assert await _said() == ["just inside", "yesterday"]
    assert job is not None and job["state"] == "done" and job["kind"] == "retention"
    assert job["deleted"] == {
        "conversation_log": 2, "intents_log": 2, "sessions_deleted": 1,
        "sessions_trimmed": 2, "satellite_log_lines": 1,
    }
    assert await _context(idle) is None
    assert await _context(live) == {
        "recent_turns": [_entry("user", "2d", now - 2 * DAY), _entry("assistant", "2d answer", now - 2 * DAY)],
        "last_assistant_response": "2d answer", "plugins": {"radio": {"k": 1}},
    }
    assert await _context(stale) == {"recent_turns": [], "conversational_mode": True}
    assert (await _context(young))["recent_turns"] == [_entry("user", "10d", now - 10 * DAY)]
    assert "yesterday" in rl.tail("kitchen", 4096) and "a year ago" not in rl.tail("kitchen", 4096)
    assert await _keepers_left() == (1, 1, 1, 1, 1, 1)
    # Recorded, and a day before the next one: the worker's next tick deletes nothing.
    await _turn(now - 100 * DAY, "slipped in")
    assert await voice_history.run_retention() is None
    assert "slipped in" in await _said()
    # The dashboard's numbers.
    from domovoi.db.session import SessionLocal

    async with SessionLocal() as s:
        st = await voice_history.status(s)
    assert (st["keep"], st["keep_label"], st["keep_days"], st["turns"]) == ("90", "90 days", 90, 3)
    assert st["last_cleanup"]["deleted"]["conversation_log"] == 2
    assert st["delete_all"] is None


@requires_db
@pytest.mark.asyncio
async def test_each_setting_moves_the_cutoff(vh, monkeypatch) -> None:
    clean, _ = vh
    now = _now()
    for keep, left in (("365", ["100d", "20d"]), ("90", ["20d"]), ("30", ["20d"])):
        await clean()
        await _exec("TRUNCATE conversation_log, intents_log")
        for days in (400, 100, 20):
            await _turn(now - days * DAY, f"{days}d")
        monkeypatch.setattr(settings, "voice_history_keep", keep)
        await voice_history.run_retention(force=True)
        assert sorted(await _said()) == sorted(left), keep


@requires_db
@pytest.mark.asyncio
async def test_prune_before_is_exact_at_the_boundary(vh) -> None:
    clean, _ = vh
    await clean()
    cutoff = _now() - 5 * DAY
    await _turn(cutoff - timedelta(milliseconds=1), "a moment before")
    await _turn(cutoff, "at the cutoff")
    await _turn(cutoff + timedelta(milliseconds=1), "a moment after")
    counts = await voice_history.prune_before(cutoff)
    assert counts["conversation_log"] == counts["intents_log"] == 1
    assert await _said() == ["a moment after", "at the cutoff"]


@requires_db
@pytest.mark.asyncio
async def test_forever_deletes_nothing(vh, monkeypatch) -> None:
    clean, rl = vh
    await clean()
    now = _now()
    monkeypatch.setattr(settings, "voice_history_keep", "forever")
    await _turn(now - 3000 * DAY, "years ago")
    idle = await _session(started=now - 3000 * DAY, last=now - 3000 * DAY,
                          context={"recent_turns": [_entry("user", "years ago", now - 3000 * DAY)]})
    rl.append("kitchen", "heard: years ago", offline=False, now=_stamp(now - 3000 * DAY))

    from domovoi.workers.voice_history_pruner import VoiceHistoryPruner

    assert await voice_history.run_retention(force=True) is None
    assert (await VoiceHistoryPruner().tick())["retention"] is None
    assert await _said() == ["years ago"]
    assert await _context(idle) is not None
    assert "years ago" in rl.tail("kitchen", 4096)
    assert await _scalar("SELECT COUNT(*) FROM voice_history_jobs") == 0


@requires_db
@pytest.mark.asyncio
async def test_delete_all_runs_once_as_a_job_and_keeps_what_comes_after(vh) -> None:
    clean, rl = vh
    await clean()
    now = _now()
    await _keepers(now)
    pid = await _person("Sam")
    sid = await _session(started=now - 5 * DAY, last=now - timedelta(minutes=1),
                         context={"recent_turns": [_entry("user", "a minute ago", now - timedelta(minutes=1))],
                                  "last_assistant_response": "sure"})
    await _turn(now - 10 * DAY, "ten days ago", person=pid, session=sid)
    await _turn(now - timedelta(minutes=1), "a minute ago", session=sid)
    rl.append("office", "heard: a minute ago", offline=False, now=_stamp(now - timedelta(seconds=60)))

    job = await voice_history.request_delete_all()
    again = await voice_history.request_delete_all()
    assert again["id"] == job["id"] and job["state"] == "queued" and job["kind"] == "delete_all"
    # Said after the press: not part of it.
    await _turn(job["cutoff"] + timedelta(seconds=1), "after the press")

    ran = await voice_history.run_pending_jobs()

    assert [j["id"] for j in ran] == [job["id"]] and ran[0]["state"] == "done"
    assert await _said() == ["after the press"]
    assert await _context(sid) is None or (await _context(sid)).get("recent_turns") == []
    assert not (Path(rl.dir) / "office.log").exists()
    assert await _keepers_left() == (1, 1, 1, 1, 1, 2)
    row = (await _rows_of_jobs())[0]
    assert row["finished_at"] is not None and row["error"] is None
    assert row["deleted"]["conversation_log"] == 2 and row["deleted"]["intents_log"] == 2
    # Done: a new press is a new job; nothing is left open.
    assert await voice_history.run_pending_jobs() == []
    assert (await voice_history.request_delete_all())["id"] != job["id"]


async def _rows_of_jobs() -> list[dict]:
    async with engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT id, kind, cutoff, requested_at, started_at, finished_at, deleted, error "
            "FROM voice_history_jobs ORDER BY id"))).all()
    return [voice_history._job(r) for r in rows]


@requires_db
@pytest.mark.asyncio
async def test_a_delete_all_cut_short_by_a_restart_finishes_at_the_next_tick(vh) -> None:
    from domovoi.workers.voice_history_pruner import VoiceHistoryPruner

    clean, _ = vh
    await clean()
    now = _now()
    await _turn(now - DAY, "before")
    # As a restart leaves it: started, never finished, some progress written.
    await _exec("INSERT INTO voice_history_jobs (kind, cutoff, started_at, deleted) "
                "VALUES ('delete_all', :c, :s, CAST('{\"conversation_log\": 7}' AS jsonb))",
                c=now - timedelta(seconds=5), s=now - timedelta(seconds=4))
    out = await VoiceHistoryPruner().tick()
    assert [j["state"] for j in out["delete_all"]] == ["done"]
    assert await _said() == []


@requires_db
@pytest.mark.asyncio
async def test_a_failed_delete_all_keeps_its_error_and_stays_open(vh, monkeypatch) -> None:
    clean, _ = vh
    await clean()
    await _turn(_now() - DAY, "still here")
    job = await voice_history.request_delete_all()
    real = voice_history.prune_before

    async def broken(*a, **kw):
        raise RuntimeError("database went away")

    monkeypatch.setattr(voice_history, "prune_before", broken)
    ran = await voice_history.run_pending_jobs()
    assert [j["state"] for j in ran] == ["failed"]
    [row] = await _rows_of_jobs()
    assert row["state"] == "failed" and "database went away" in row["error"]
    assert await _said() == ["still here"]
    monkeypatch.setattr(voice_history, "prune_before", real)
    ran = await voice_history.run_pending_jobs()
    assert [(j["id"], j["state"]) for j in ran] == [(job["id"], "done")]
    [row] = await _rows_of_jobs()
    assert row["state"] == "done" and row["error"] is None
    assert await _said() == []


@requires_db
@pytest.mark.asyncio
async def test_a_failed_retention_run_is_retried_at_the_next_tick(vh, monkeypatch) -> None:
    clean, _ = vh
    await clean()
    await _turn(_now() - 200 * DAY, "old")

    async def broken(*a, **kw):
        raise RuntimeError("boom")

    real = voice_history.prune_before
    monkeypatch.setattr(voice_history, "prune_before", broken)
    with pytest.raises(RuntimeError):
        await voice_history.run_retention()
    [failed] = await _rows_of_jobs()
    assert (failed["state"], failed["error"]) == ("failed", "RuntimeError: boom")
    assert failed["finished_at"] is not None  # closed: the next run has its own cutoff
    monkeypatch.setattr(voice_history, "prune_before", real)
    job = await voice_history.run_retention()  # not "a day after the failed one"
    assert job is not None and job["deleted"]["conversation_log"] == 1


# ─── "forget me" and forget person ──────────────────────────────────────────


async def _forget_scene() -> dict:
    now = _now()
    sarah, bob = await _person("Sarah"), await _person("Bob")
    a = await _session(started=now - 3 * DAY, last=now - DAY,
                       context={"recent_turns": [_entry("user", "sarah's words", now - DAY)],
                                "last_assistant_response": "to sarah", "last_played_track": {"t": 1}})
    b = await _session(started=now - 3 * DAY, last=now - DAY, room="office",
                       context={"recent_turns": [_entry("user", "bob's words", now - DAY)]})
    await _turn(now - 2 * DAY, "sarah: my favourite band is weezer", person=sarah, session=a)
    await _turn(now - DAY, "sarah: what's the weather", person=sarah, session=a)
    await _turn(now - DAY, "bob: remind Sarah to buy milk", person=bob, session=b, room="office")
    await _turn(now - DAY, "someone: is Sarah home", session=b, room="office")
    await _exec("INSERT INTO memories (person_id, body, source) VALUES (:p, 'weezer', 'implicit')", p=sarah)
    return {"sarah": sarah, "bob": bob, "a": a, "b": b}


@requires_db
@pytest.mark.asyncio
async def test_forget_me_deletes_only_the_speakers_own_turns(vh, db_session, monkeypatch) -> None:
    from domovoi.handlers.voice_profile import VoiceProfileHandler
    from domovoi.models import Context, Intent
    from domovoi.router import route
    from uuid import UUID

    clean, _ = vh
    await clean()
    scene = await _forget_scene()

    async def own_voice(self, ctx, session):
        return "person", []  # whose voice it is has its own tests (test_voice_claims.py)

    monkeypatch.setattr(VoiceProfileHandler, "_forget_scope", own_voice)
    ctx = Context(room_id="kitchen", online=True, person_id=scene["sarah"], presence_tier="low",
                  session_id=UUID(scene["a"]))
    response = await route(Intent(transcript="forget me", room_id="kitchen"), ctx, db_session)
    await db_session.commit()

    assert "what you've said to me" in response.text
    # Hers are gone; the turns that only NAME her stay; the request itself
    # is written like every turn (both rows), with nobody's name on it.
    assert await _said() == ["bob: remind Sarah to buy milk", "forget me", "someone: is Sarah home"]
    async with engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT person_id FROM conversation_log WHERE user_text = 'forget me' "
            "UNION ALL SELECT person_id FROM intents_log WHERE transcript = 'forget me'"))).all()
    assert [r[0] for r in rows] == [None, None]
    assert await _scalar("SELECT COUNT(*) FROM people WHERE id = :p", p=scene["sarah"]) == 0
    assert await _scalar("SELECT COUNT(*) FROM memories") == 0
    # The room she spoke in starts its short-term conversation afresh (the
    # forget-me exchange is the only one); Bob's room keeps his.
    ctx_a = await _context(scene["a"])
    assert [e["text"] for e in ctx_a["recent_turns"]] == ["forget me", response.text]
    assert ctx_a["last_played_track"] == {"t": 1}
    assert [e["text"] for e in (await _context(scene["b"]))["recent_turns"]] == ["bob's words"]


@requires_db
@pytest.mark.asyncio
async def test_people_forget_person_deletes_only_their_turns(vh) -> None:
    from domovoi.tests.auth_testkit import web_client

    clean, _ = vh
    await clean()
    scene = await _forget_scene()
    async with web_client() as c:   # before setup: the LAN grace lets it through
        assert (await c.delete(f"/api/people/{scene['sarah']}")).status_code == 204
        assert (await c.delete("/api/people/999999")).status_code == 404
    assert await _said() == ["bob: remind Sarah to buy milk", "someone: is Sarah home"]
    ctx_a = await _context(scene["a"])
    assert "recent_turns" not in ctx_a and "last_assistant_response" not in ctx_a
    assert ctx_a["last_played_track"] == {"t": 1}
    assert [e["text"] for e in (await _context(scene["b"]))["recent_turns"]] == ["bob's words"]
    assert await _scalar("SELECT COUNT(*) FROM people") == 1


@requires_db
@pytest.mark.asyncio
async def test_a_forget_that_fails_leaves_their_turns(vh, monkeypatch) -> None:
    """The turns and the person go in one transaction: a failure keeps both."""
    from domovoi.tests.auth_testkit import web_client
    from web.backend.api import people as people_api

    clean, _ = vh
    await clean()
    scene = await _forget_scene()
    real = voice_history.delete_person_history

    async def then_fail(s, pid):
        await real(s, pid)
        raise RuntimeError("the delete broke")

    monkeypatch.setattr(people_api.voice_history, "delete_person_history", then_fail)
    async with web_client() as c:
        with pytest.raises(RuntimeError):
            await c.delete(f"/api/people/{scene['sarah']}")
    assert len(await _said()) == 4
    assert await _scalar("SELECT COUNT(*) FROM people") == 2


# ─── The router invariant ───────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_the_router_still_writes_both_rows_per_turn(vh, db_session) -> None:
    from domovoi.models import Context, Intent
    from domovoi.router import route

    clean, _ = vh
    await clean()
    for said in ("set a timer for 5 minutes", "what time is it", "tell me a joke"):
        await route(Intent(transcript=said, room_id="kitchen"), Context(room_id="kitchen", online=True),
                    db_session)
        await db_session.commit()
    assert await _said() == ["set a timer for 5 minutes", "tell me a joke", "what time is it"]
    assert await _scalar("SELECT COUNT(*) FROM intents_log") == 3
    assert await _scalar("SELECT COUNT(*) FROM conversation_log") == 3


# ─── Who may do what ────────────────────────────────────────────────────────

BROWSER = {"X-Requested-With": "domovoi-tests"}


def _core(**kw) -> AsyncClient:
    from domovoi.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", **kw)


def _web(**kw) -> AsyncClient:
    from web.backend.main import app

    headers = {**BROWSER, **kw.pop("headers", {})}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers, **kw)


@pytest.mark.parametrize("hop", ["core", "web"])
@pytest.mark.asyncio
async def test_the_setting_needs_an_admin_bearer(hop, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import COOKIE, HEADER, install_fake_db

    path, body = (
        ("/v1/admin/config", {"changes": {"voice_history_keep": "30"}}) if hop == "core"
        else ("/api/config/editable", {"changes": {"voice_history_keep": "30"}})
    )
    send = (lambda c, **kw: c.post(path, json=body, **kw)) if hop == "core" else (
        lambda c, **kw: c.patch(path, json=body, **kw))
    client = _core if hop == "core" else _web
    # Whatever a stray domovoi/.env says (a throwaway run in this checkout
    # writes one), the refusals below must leave it as it was.
    monkeypatch.setattr(settings, "voice_history_keep", "90")
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="household")
    async with client() as c:
        assert (await send(c)).status_code == 401
        assert (await send(c, headers={HEADER: "household"})).status_code in (401, 403)
    async with client(cookies={COOKIE: "admin-token"}) as c:
        assert (await send(c)).status_code == 403
    install_fake_db(monkeypatch, admin=False)
    async with client() as c:   # before setup: closed (security tier)
        assert (await send(c)).status_code == 501
    assert settings.voice_history_keep == "90"


@requires_db
@pytest.mark.asyncio
async def test_an_admin_saves_the_setting_and_it_applies_at_once(vh, tmp_path, monkeypatch) -> None:
    from domovoi import config_env_writer, main
    from domovoi.tests.auth_testkit import bearer, install_fake_db

    clean, _ = vh
    await clean()
    env = tmp_path / ".env"
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", env)
    main.app.state.config_apply_lock = asyncio.Lock()
    main._register_core_reapply_hooks()
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    await _turn(_now() - 45 * DAY, "45 days ago")
    await _turn(_now() - 5 * DAY, "5 days ago")
    async with _core() as c:
        r = await c.post("/v1/admin/config", json={"changes": {"voice_history_keep": "30"}},
                         headers=bearer("admin-token"))
        assert r.status_code == 200, r.text
        assert r.json()["applied"] == ["voice_history_keep"] and r.json()["rejected"] == {}
        r = await c.post("/v1/admin/config", json={"changes": {"voice_history_keep": "60"}},
                         headers=bearer("admin-token"))
        assert "voice_history_keep" in r.json()["rejected"]
    await asyncio.gather(*list(voice_history._BACKGROUND))
    assert settings.voice_history_keep == "30"
    assert "VOICE_HISTORY_KEEP=30" in env.read_text(encoding="utf-8")
    assert "45 days ago" not in await _said()
    assert "5 days ago" in await _said()


@pytest.mark.parametrize("hop", ["core", "web"])
@pytest.mark.asyncio
async def test_delete_all_needs_an_admin(hop, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import COOKIE, HEADER, install_fake_db

    path = "/v1/admin/voice-history/delete-all" if hop == "core" else "/api/voice-history/delete-all"
    client = _core if hop == "core" else _web

    async def forbidden():
        raise AssertionError("a refused delete-all wrote a job")

    monkeypatch.setattr(voice_history, "request_delete_all", forbidden)
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="household")
    async with client() as c:
        assert (await c.post(path)).status_code == 401
        assert (await c.post(path, headers={HEADER: "household"})).status_code in (401, 403)
    async with client(cookies={COOKIE: "admin-token"}) as c:
        assert (await c.post(path)).status_code == 403


@pytest.mark.asyncio
async def test_the_web_hop_forwards_an_admins_delete_all(monkeypatch) -> None:
    from domovoi.tests.auth_testkit import bearer, install_fake_db
    from web.backend.api import voice_history as web_vh

    seen: list = []

    async def fake_post(path, body=None, timeout=30.0, headers=None):
        seen.append((path, headers.get("Authorization")))
        return 200, {"job": {"id": 4, "state": "queued"}}

    monkeypatch.setattr(web_vh, "post_admin", fake_post)
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    async with _web() as c:
        r = await c.post("/api/voice-history/delete-all", headers=bearer("admin-token"))
    assert r.status_code == 200 and r.json() == {"job": {"id": 4, "state": "queued"}}
    assert seen == [("/v1/admin/voice-history/delete-all", "Bearer admin-token")]


@requires_db
@pytest.mark.asyncio
async def test_an_admins_delete_all_through_the_core(vh, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import bearer, install_fake_db

    clean, _ = vh
    await clean()
    await _turn(_now() - DAY, "yesterday")
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    async with _core() as c:
        r = await c.post("/v1/admin/voice-history/delete-all", headers=bearer("admin-token"))
        assert r.status_code == 200, r.text
        r2 = await c.post("/v1/admin/voice-history/delete-all", headers=bearer("admin-token"))
    await asyncio.gather(*list(voice_history._BACKGROUND))
    job = r.json()["job"]
    assert job["kind"] == "delete_all" and r2.json()["job"]["id"] in (job["id"], job["id"] + 1)
    # The request itself is recorded, with no speech in it.
    assert await _scalar("SELECT COUNT(*) FROM conversation_log") == 0
    assert await _scalar(
        "SELECT COUNT(*) FROM intents_log WHERE transcript = '[ui] delete all voice history'") >= 1
    assert await _scalar("SELECT COUNT(*) FROM intents_log WHERE transcript = 'yesterday'") == 0


@requires_db
@pytest.mark.asyncio
async def test_the_status_is_a_paired_devices_read(vh, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import COOKIE, HEADER, install_fake_db
    from web.backend import domovoi_client

    clean, _ = vh
    await clean()
    await _turn(_now() - 3 * DAY, "three days ago")
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="household")
    domovoi_client.set_cached_snapshot({"voice_history_keep": "365"})
    try:
        async with _web() as c:
            assert (await c.get("/api/voice-history")).status_code == 401
            r = await c.get("/api/voice-history", headers={HEADER: "household"})
        async with _web(cookies={COOKIE: "admin-token"}) as c:
            assert (await c.get("/api/voice-history")).status_code == 200
    finally:
        domovoi_client.set_cached_snapshot(None)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["keep"], body["keep_label"], body["keep_days"], body["turns"]) == ("365", "1 year", 365, 1)
    assert [c["label"] for c in body["choices"]] == ["30 days", "90 days", "1 year", "Forever"]
    assert "three days ago" not in json.dumps(body)


# ─── V030 on a populated lane ───────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_v030_applies_again_cleanly_on_a_populated_lane(vh) -> None:
    clean, _ = vh
    await clean()
    now = _now()
    sid = await _session(started=now - DAY, last=now, context={"recent_turns": []})
    await _turn(now, "kept", session=sid)
    await voice_history.request_delete_all()
    sql = (Path(voice_history.__file__).parent / "db" / "migrations"
           / "V030__voice_history_retention.sql").read_text(encoding="utf-8")
    statements = [
        st for st in sql.split(";")
        if any(ln.strip() and not ln.strip().startswith("--") for ln in st.splitlines())
    ]
    assert len(statements) == 3
    for _ in range(2):
        async with engine.begin() as conn:
            for st in statements:
                await conn.exec_driver_sql(st)
    assert await _said() == ["kept"]
    assert await _scalar("SELECT COUNT(*) FROM voice_history_jobs") == 1
    assert await _scalar(
        "SELECT COUNT(*) FROM pg_indexes WHERE indexname = 'idx_sessions_last_activity'") == 1
    with pytest.raises(Exception):
        await _exec("INSERT INTO voice_history_jobs (kind, cutoff) VALUES ('something', NOW())")


# ─── Batching under a large table ───────────────────────────────────────────

LARGE_ROWS = 120_000


@requires_db
@pytest.mark.asyncio
async def test_the_batches_stay_short_on_a_large_table(vh, capsys) -> None:
    """120,000 old turns (240,000 rows) and 1,000 kept: every batch is one
    short statement, the event loop keeps ticking the whole time, and the
    newer rows are untouched."""
    clean, _ = vh
    await clean()
    now = _now()
    cutoff = now - 30 * DAY
    for table, col in (("conversation_log", "user_text"), ("intents_log", "transcript")):
        extra = ", assistant_text" if table == "conversation_log" else ""
        extra_v = ", 'an answer'" if table == "conversation_log" else ""
        await _exec(
            f"INSERT INTO {table} (room_id, {col}{extra}, at) "
            f"SELECT 'kitchen', 'old turn ' || g{extra_v}, CAST(:old AS timestamptz) - make_interval(secs => g) "
            "FROM generate_series(1, :n) AS g",
            old=cutoff - DAY, n=LARGE_ROWS,
        )
        await _exec(
            f"INSERT INTO {table} (room_id, {col}{extra}, at) "
            f"SELECT 'kitchen', 'new turn ' || g{extra_v}, CAST(:new AS timestamptz) FROM generate_series(1, 1000) AS g",
            new=now - DAY,
        )
    async with engine.begin() as conn:
        await conn.execute(text("ANALYZE conversation_log"))
        await conn.execute(text("ANALYZE intents_log"))
        # Each batch finds its rows through the time index and deletes them
        # through the primary key, never by scanning the whole table (the
        # IN (SELECT ...) form did, every batch: quadratic on a backlog).
        for table in ("conversation_log", "intents_log"):
            rows = (await conn.execute(
                text("EXPLAIN " + voice_history._DELETE_BATCH[table].text),
                {"cutoff": cutoff, "n": voice_history.BATCH_ROWS})).all()
            plan = " | ".join(r[0] for r in rows)
            assert f"Index Scan using idx_{table}_at" in plan, plan
            assert f"Seq Scan on {table}" not in plan, plan

    gaps: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            t = time.perf_counter()
            gaps.append(t - last)
            last = t

    marks: list[float] = []

    async def progress(_counts) -> None:
        marks.append(time.perf_counter())

    beat = asyncio.create_task(heartbeat())
    started = time.perf_counter()
    counts = await voice_history.prune_before(cutoff, progress=progress, pause_sec=0.0)
    total = time.perf_counter() - started
    stop.set()
    await beat

    batch_times = [b - a for a, b in zip([started, *marks], marks)]
    assert counts["conversation_log"] == counts["intents_log"] == LARGE_ROWS
    assert await _scalar("SELECT COUNT(*) FROM conversation_log") == 1000
    assert await _scalar("SELECT COUNT(*) FROM intents_log") == 1000
    expected_batches = 2 * (LARGE_ROWS // voice_history.BATCH_ROWS)
    assert len(marks) >= expected_batches
    with capsys.disabled():
        print(
            f"\n[voice history batching] {2 * LARGE_ROWS} rows in {total:.1f} s, "
            f"{len(batch_times)} batches, slowest {max(batch_times) * 1000:.0f} ms, "
            f"median {sorted(batch_times)[len(batch_times) // 2] * 1000:.0f} ms, "
            f"worst loop gap {max(gaps) * 1000:.0f} ms"
        )
    # Each batch is one short statement; the loop never waits on one.
    assert max(batch_times) < 2.0
    assert max(gaps) < 0.25
    assert total < 120
