""""Print it" said into the app's chat mic in a home-server chat with a draft
(handlers/printing.py, documents plan D4): the chat's latest draft version,
confirmed first like every voice print, and printed only on "yes"."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from domovoi import net_safety
from domovoi.config import settings
from domovoi.db.session import engine, session_scope
from domovoi.models import Context, Intent
from domovoi.printing import discovery, scans, store
from domovoi.router import route
from domovoi.tests.conftest import requires_db
from domovoi.tests.printing_fakes import FakeIppPrinter, fake_lan

LETTER = "Dear Mr. Okafor,\n\nThe heater in 3B has stopped working.\n\nThank you,\n\nSam Lee\n"


@pytest.fixture
async def phone(db_session, tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    docs.mkdir()
    monkeypatch.setattr(settings, "documents_dir", str(docs))
    monkeypatch.setattr(net_safety, "allow_entries", lambda: ())
    lan = fake_lan(monkeypatch)
    discovery.reset_state()
    scans.reset_state()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE print_jobs, scan_jobs, printer_room_defaults, printers RESTART IDENTITY CASCADE"))
    await store.reset_limits()
    async with session_scope() as s:
        tid = (await s.execute(text("INSERT INTO chat_threads (title) VALUES ('Heater') RETURNING id"))).scalar_one()
        did = (await s.execute(text(
            "INSERT INTO chat_drafts (thread_id, kind, template, title) VALUES (:t, 'letter', 'letter-personal', "
            "'Letter to my landlord') RETURNING id"), {"t": tid})).scalar_one()
        for v, body in ((1, LETTER + "\nP.S. Old line.\n"), (2, LETTER)):
            await s.execute(text("INSERT INTO chat_draft_versions (draft_id, version, body) VALUES (:d, :v, :b)"),
                            {"d": did, "v": v, "b": body})
        await s.commit()
    session: dict[str, object] = {}

    async def say(utterance: str, thread: int | None = tid):
        ctx = Context(room_id="phone-test", session_id=session.get("id"), online=True, device_kind="phone",
                      chat_thread_id=thread)
        resp = await route(Intent(transcript=utterance, room_id="phone-test"), ctx, db_session)
        session["id"] = resp.session_id
        return resp

    say.lan = lan
    say.thread = tid
    yield say
    async with session_scope() as s:
        await s.execute(text("DELETE FROM chat_threads WHERE id = :t"), {"t": tid})
        await s.commit()


async def _printer(phone, fake):
    from domovoi.printing import devices, lan_target

    phone.lan(fake.port)
    t = lan_target.vet("ipp", "127.0.0.1", fake.port, "/ipp/print")
    return await devices.add_device(ipp_target=t, escl_target=None, name="office printer", room_id=None,
                                    room_label=None, added_via="typed")


@requires_db
async def test_print_it_in_a_chat_with_a_draft_confirms_then_prints_the_latest_version(phone):
    fake = await FakeIppPrinter().start()
    try:
        await _printer(phone, fake)
        r = await phone("print it")
        assert r.text == "That's Letter to my landlord v2, 1 page, to the office printer. Print it?"
        assert r.expect_followup and await store.list_jobs() == []
        r = await phone("yes")
        assert r.text == "Printing."
        for _ in range(300):
            jobs = await store.list_jobs()
            if jobs and jobs[0]["state"] in ("done", "failed"):
                break
            await asyncio.sleep(0.05)
        assert jobs[0]["state"] == "done" and jobs[0]["title"] == "Letter to my landlord v2"
        assert jobs[0]["source"] == "voice"
        r = await phone("print the letter")
        assert r.text.startswith("That's Letter to my landlord v2")
        assert (await phone("no")).text == "OK, not printing."
    finally:
        await fake.close()


@requires_db
async def test_print_it_with_no_chat_or_no_draft_still_asks_which_document(phone):
    r = await phone("print it", thread=None)
    assert r.text.startswith("Which document?")
