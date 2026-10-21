"""Drafts in home-server chats (web/backend/api/chat_drafts.py + chat.py).

The model is a fake at the ``chat_stream`` boundary. What matters most: a
request makes a draft card, follow-ups revise the same draft and say what
changed, every version is kept and reopenable, only the version being
revised goes to the model, a hand edit is a version "edited by you", saving
writes .docx or PDF into Documents → From Chat and checks the file, "print
it" offers the print card without printing, and with the household's AI
features off nothing is drafted but saving still works.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import web.backend.api.chat as chat_api
import web.backend.api.chat_drafts as drafts_api
from domovoi import ai_features
from domovoi.config import settings as core_settings
from domovoi.drafts import ats, prompts
from domovoi.tests.conftest import requires_db
from web.backend.main import app

LETTER = """Jordan Avery
12 Example Street
Toledo, OH 43604

October 21, 2026

Hiring Manager
Acme Logistics

**Re: Warehouse lead**

Dear Hiring Manager,

I'm writing to apply for the warehouse lead job at Acme Logistics. I have led a team of eight for three
years, and I hold a forklift licence.

I would welcome the chance to talk about the role.

Sincerely,

Jordan Avery
"""
SHORTER = LETTER.replace("\nI would welcome the chance to talk about the role.\n", "")
FORMAL = SHORTER.replace("I'm writing", "I am writing")


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


@pytest.fixture(autouse=True)
def documents_dir(tmp_path, monkeypatch):
    d = tmp_path / "Documents"
    monkeypatch.setattr(core_settings, "documents_dir", str(d))
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", tmp_path / "chat_uploads")
    return d


@pytest.fixture
def model(monkeypatch):
    """The fake model: ``model.replies`` are said in turn (the last one
    again when they run out); ``model.calls`` is what it was sent."""
    class _M:
        replies: list[str] = []
        calls: list[dict[str, Any]] = []
        fail = False

    _M.replies, _M.calls, _M.fail = [], [], False

    async def _stream(messages, model, **kw):
        _M.calls.append({"messages": messages, "model": model, **kw})
        if _M.fail:
            raise chat_api.ollama_client.OllamaChatError(500, "boom")
        reply = _M.replies.pop(0) if len(_M.replies) > 1 else (_M.replies[0] if _M.replies else "ok")
        for i in range(0, len(reply), 11):
            yield reply[i:i + 11]

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    return _M


def _sse(body: str) -> list[tuple[str, Any]]:
    out = []
    for frame in body.split("\n\n"):
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            out.append((ev, json.loads(data)))
    return out


def _thread(c) -> int:
    return c.post("/api/chat/threads", json={}).json()["id"]


def _send(c, tid, content):
    r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": content})
    assert r.status_code == 200, r.text
    events = _sse(r.text)
    done = next(d for ev, d in events if ev == "done")
    return done, events


def _three_versions(c, model):
    tid = _thread(c)
    model.replies = ["Here is your cover letter:\n\n" + LETTER + "\nLet me know if you'd like changes.", SHORTER, FORMAL]
    one, events = _send(c, tid, "Write a cover letter for the warehouse lead job at Acme Logistics")
    two, _ = _send(c, tid, "shorter")
    three, _ = _send(c, tid, "make it more formal")
    return tid, (one, two, three), events


# ─── Writing and revising ────────────────────────────────────────────────
@requires_db
def test_a_request_makes_a_draft_card_and_follow_ups_revise_it(model):
    with _client() as c:
        tid, (one, two, three), events = _three_versions(c, model)
        assert ("status", {"state": "drafting", "action": "create"}) in events
        assert "".join(d["text"] for ev, d in events if ev == "delta").startswith("Here is your cover letter")
        d1 = one["draft"]
        assert d1["action"] == "create" and d1["version"] == 1 and d1["kind"] == "cover_letter"
        assert d1["title"] == "Cover letter for the warehouse lead job at Acme Logistics"
        assert one["content"].startswith("First draft of your cover letter: ")
        assert one["retryable"] is False and one["stats"]["model_role"] == "draft"
        assert two["draft"]["id"] == d1["id"] and two["draft"]["version"] == 2
        assert two["content"].startswith("Version 2: cut ") and two["draft"]["change"].startswith("cut ")
        assert three["draft"]["version"] == 3 and three["content"].startswith("Version 3: changed ")

        # Every version kept, with what made it, and reopenable.
        full = c.get(f"/api/chat/drafts/{d1['id']}").json()
        vs = full["versions"]
        assert [v["version"] for v in vs] == [1, 2, 3]
        assert [v["source"] for v in vs] == ["first", "revision", "revision"]
        assert [v["instruction"] for v in vs] == [None, "shorter", "make it more formal"]
        assert vs[0]["body"] == LETTER.strip()             # the remarks around it dropped
        assert vs[0]["changes"] is None
        assert ["-", "I would welcome the chance to talk about the role."] in [
            [op, t.strip()] for op, t in vs[1]["changes"]]
        assert any(op == "+" and "am" in t for op, t in vs[2]["changes"])
        assert full["draft"]["format"] == "docx"
        listed = c.get(f"/api/chat/threads/{tid}/drafts").json()["drafts"]
        assert len(listed) == 1 and listed[0]["version"] == 3 and listed[0]["first_lines"].startswith("Jordan Avery")
        # The thread reads back with the cards on their rows.
        msgs = c.get(f"/api/chat/threads/{tid}/messages").json()["messages"]
        assert [m["draft"]["version"] for m in msgs if m.get("draft")] == [1, 2, 3]


@requires_db
def test_only_the_version_being_revised_goes_to_the_model(model):
    with _client() as c:
        _three_versions(c, model)
    first, second, third = model.calls
    assert first["messages"][0]["content"] == prompts.RULES
    assert "Skeleton:" in first["messages"][1]["content"]
    assert len(second["messages"]) == 2 and second["messages"][0]["content"] == prompts.REVISE_RULES
    assert "I would welcome the chance" in second["messages"][1]["content"]
    assert "Instruction: shorter" in second["messages"][1]["content"]
    # v3 is a revision of v2 alone: v1's sentence is nowhere in it.
    assert "I would welcome the chance" not in third["messages"][1]["content"]
    assert all(c["num_predict"] == drafts_api.DRAFT_MAX_TOKENS for c in model.calls)


@requires_db
def test_a_question_in_a_draft_chat_is_answered_as_a_question(model):
    with _client() as c:
        tid = _thread(c)
        model.replies = [LETTER, "ATS means applicant tracking system."]
        _send(c, tid, "draft a cover letter for Acme")
        done, _ = _send(c, tid, "what does ATS mean?")
        assert done.get("draft") is None and done["content"] == "ATS means applicant tracking system."
    assert model.calls[1]["messages"][0]["content"] != prompts.RULES


@requires_db
def test_a_long_version_is_revised_section_by_section(model, monkeypatch):
    monkeypatch.setattr(drafts_api, "ONE_PASS_CHARS", 300)
    resume = "# Jordan Avery\n\n" + "\n\n".join(
        f"## Section {i}\n" + ("Steady, careful work every day of the week. " * 6) for i in range(4))
    with _client() as c:
        tid = _thread(c)
        model.replies = [resume, "## Section\nShort."]
        _send(c, tid, "write my resume")
        done, _ = _send(c, tid, "shorter")
    assert len(model.calls) - 1 == done["draft"]["parts"] > 1
    assert "Revised section by section" in done["content"]
    assert "part 1 of" in model.calls[1]["messages"][1]["content"]


@requires_db
def test_a_failed_draft_writes_no_version(model):
    with _client() as c:
        tid = _thread(c)
        model.fail = True
        r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "write a letter to my landlord"})
        events = _sse(r.text)
        assert any(ev == "error" for ev, _ in events)
        done = next(d for ev, d in events if ev == "done")
        assert done.get("draft") is None and done["error"]
        assert c.get(f"/api/chat/threads/{tid}/drafts").json()["drafts"] == []


# ─── Editing by hand ─────────────────────────────────────────────────────
@requires_db
def test_a_hand_edit_is_a_version_edited_by_you(model):
    with _client() as c:
        tid, (one, *_), _e = _three_versions(c, model)
        did = one["draft"]["id"]
        r = c.post(f"/api/chat/drafts/{did}/versions", json={"text": FORMAL + "\nP.S. I can start Monday.\n",
                                                             "base_version": 3})
        assert r.status_code == 200, r.text
        v = r.json()["version"]
        assert v["version"] == 4 and v["author"] == "person" and v["source"] == "edited"
        assert v["instruction"] is None and v["change"].startswith("added ")
        stale = c.post(f"/api/chat/drafts/{did}/versions", json={"text": "x", "base_version": 3})
        assert stale.status_code == 409 and stale.json()["code"] == "stale_version" and stale.json()["latest"] == 4
        same = c.post(f"/api/chat/drafts/{did}/versions", json={"text": v["body"], "base_version": 4})
        assert same.json().get("unchanged") is True
        assert [x["version"] for x in c.get(f"/api/chat/drafts/{did}").json()["versions"]] == [1, 2, 3, 4]


# ─── Saving ──────────────────────────────────────────────────────────────
@requires_db
def test_save_writes_into_from_chat_and_checks_the_file(model, documents_dir):
    with _client() as c:
        tid, (one, *_), _e = _three_versions(c, model)
        did = one["draft"]["id"]
        r = c.post(f"/api/chat/drafts/{did}/save", json={})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["format"] == "docx" and s["version"] == 3       # a cover letter's default, the latest version
        assert s["rel_path"] == "From Chat/Cover letter for the warehouse lead job at Acme Logistics v3.docx"
        assert s["check"] == {"ok": True, "line": "text readable · single column", "read": s["check"]["read"],
                              "expected": s["check"]["expected"]}
        saved = documents_dir / s["rel_path"]
        assert saved.is_file() and ats.check(saved.read_bytes(), "docx", FORMAL).ok
        p = c.post(f"/api/chat/drafts/{did}/save", json={"version": 1, "format": "pdf"}).json()
        assert p["rel_path"].endswith(" v1.pdf") and p["check"]["ok"]
        again = c.post(f"/api/chat/drafts/{did}/save", json={"version": 1, "format": "pdf"}).json()
        assert again["rel_path"].endswith(" v1 (1).pdf")
        vs = c.get(f"/api/chat/drafts/{did}").json()["versions"]
        assert [e["format"] for e in vs[0]["saved"]] == ["pdf", "pdf"] and vs[2]["saved"][0]["format"] == "docx"
        # Only saved versions became files.
        assert sorted(f.name for f in (documents_dir / "From Chat").iterdir()) == sorted(
            [saved.name, p["name"], again["name"]])
        # The Documents list shows the folder's files like any other.
        assert c.get("/api/documents?kind=all").status_code == 200


@requires_db
def test_a_letter_saves_as_pdf_by_default_and_another_template_can_be_picked(model, documents_dir):
    with _client() as c:
        tid = _thread(c)
        model.replies = [LETTER]
        done, _ = _send(c, tid, "write a letter to the phone company about my bill")
        did = done["draft"]["id"]
        s = c.post(f"/api/chat/drafts/{did}/save", json={}).json()
        assert s["format"] == "pdf" and s["check"]["ok"]
        bad = c.post(f"/api/chat/drafts/{did}/save", json={"template": "resume-plain"})
        assert bad.status_code == 422
        ok = c.post(f"/api/chat/drafts/{did}/save", json={"template": "letter-personal", "format": "pdf"})
        assert ok.status_code == 200


@requires_db
def test_export_hands_the_file_over_and_keeps_nothing(model, documents_dir):
    with _client() as c:
        tid, (one, *_), _e = _three_versions(c, model)
        r = c.get(f"/api/chat/drafts/{one['draft']['id']}/export?version=2&format=pdf")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert "v2.pdf" in r.headers["content-disposition"] and r.content.startswith(b"%PDF")
        assert ats.check(r.content, "pdf", SHORTER).ok
    assert not (documents_dir / "From Chat").exists()


@requires_db
def test_send_to_home_lands_in_from_chat(documents_dir):
    with _client() as c:
        r = c.post("/api/documents/upload", data={"folder": "From Chat"},
                   files={"files": ("Letter v2.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")})
        assert r.status_code == 200, r.text
        assert r.json()["saved"] == ["From Chat/Letter v2.pdf"]
        assert (documents_dir / "From Chat" / "Letter v2.pdf").is_file()
        bad = c.post("/api/documents/upload", data={"folder": "../elsewhere"},
                     files={"files": ("x.pdf", io.BytesIO(b"%PDF"), "application/pdf")})
        assert bad.status_code == 422


# ─── Printing ────────────────────────────────────────────────────────────
@requires_db
def test_print_it_offers_the_print_card_and_never_prints_by_itself(model, monkeypatch):
    sent: list[dict[str, Any]] = []

    async def _forward(core_path, request, data, filename, **kw):
        sent.append({"path": core_path, "data": data, "filename": filename, **kw})
        from fastapi.responses import JSONResponse

        return JSONResponse({"summary": "1 page", "pages_total": 1})

    from web.backend.api import printers

    monkeypatch.setattr(printers, "forward_print_bytes", _forward)
    with _client() as c:
        tid, (one, *_), _e = _three_versions(c, model)
        calls = len(model.calls)
        done, events = _send(c, tid, "print it")
        assert len(model.calls) == calls                     # no model asked
        assert done["draft"]["action"] == "print" and done["draft"]["version"] == 3
        assert done["content"].startswith("Ready to print “Cover letter")
        assert sent == []                                    # the card asks; nothing printed
        did = one["draft"]["id"]
        r = c.post(f"/api/chat/drafts/{did}/print/preview", json={"version": 2, "options": {"copies": 2}})
        assert r.status_code == 200 and sent[-1]["path"] == "/v1/print/preview"
        assert sent[-1]["data"].startswith(b"%PDF") and sent[-1]["filename"].endswith(" v2.pdf")
        assert sent[-1]["options"] == {"copies": 2} and sent[-1]["title"].endswith(" v2")
        c.post(f"/api/chat/drafts/{did}/print/jobs", json={})
        assert sent[-1]["path"] == "/v1/print/jobs" and sent[-1]["title"].endswith(" v3")


# ─── The household's AI switch ───────────────────────────────────────────
@requires_db
def test_with_ai_off_nothing_is_drafted_but_saving_still_works(model, documents_dir):
    with _client() as c:
        tid, (one, *_), _e = _three_versions(c, model)
        did = one["draft"]["id"]
        with ai_features.override(False):
            r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "shorter"})
            assert r.status_code == 409 and r.headers.get("X-Domovoi-Refusal") == "ai-off"
            assert c.get("/api/chat/drafts").json()["drafts"][0]["id"] == did
            s = c.post(f"/api/chat/drafts/{did}/save", json={"format": "pdf"})
            assert s.status_code == 200 and s.json()["check"]["ok"]
            e = c.post(f"/api/chat/drafts/{did}/versions", json={"text": "Dear Sam,\n\nHello.", "base_version": 3})
            assert e.status_code == 200
        assert len(model.calls) == 3


@requires_db
def test_drafts_go_with_their_thread(model):
    with _client() as c:
        tid, (one, *_), _e = _three_versions(c, model)
        assert c.delete(f"/api/chat/threads/{tid}").status_code == 200
        assert c.get(f"/api/chat/drafts/{one['draft']['id']}").status_code == 404


# ─── Review round 1 (2026-10-21) ─────────────────────────────────────────
#: Messages in a chat with a draft that are NOT about the draft: they route
#: as ordinary messages, the draft untouched (review finding 2).
NOT_REVISIONS = [
    "turn on the kitchen lights", "add milk to my shopping list", "put on some jazz",
    "use the oven timer for ten minutes", "say hello to grandma", "more coffee please", "fix the wifi",
    "change the thermostat to 70", "remove the dentist appointment tomorrow",
    "move my dentist appointment to Friday", "make it louder",
]


@requires_db
@pytest.mark.parametrize("phrase", NOT_REVISIONS)
def test_an_unrelated_message_never_rewrites_the_draft(model, phrase):
    with _client() as c:
        tid = _thread(c)
        model.replies = [LETTER, "Sure."]
        one, _ = _send(c, tid, "write a cover letter for Acme")
        done, _ = _send(c, tid, phrase)
        assert done.get("draft") is None and done["content"] == "Sure."
        assert [v["version"] for v in c.get(f"/api/chat/drafts/{one['draft']['id']}").json()["versions"]] == [1]
    assert model.calls[-1]["messages"][0]["content"] != prompts.REVISE_RULES


@requires_db
def test_real_revisions_still_revise_and_edit_only_words_need_the_draft_turn_just_before(model):
    with _client() as c:
        tid = _thread(c)
        model.replies = [LETTER, SHORTER, "A cover letter is a letter.", FORMAL, "Fine.", "Fine."]
        _send(c, tid, "write a cover letter for Acme")
        two, _ = _send(c, tid, "shorter")                     # right after the draft turn
        assert two["draft"]["version"] == 2
        chat, _ = _send(c, tid, "what is a cover letter?")
        assert chat.get("draft") is None
        three, _ = _send(c, tid, "make the letter more formal")  # names the draft: any time
        assert three["draft"]["version"] == 3
        _send(c, tid, "what is a cover letter?")
        later, _ = _send(c, tid, "more formal")               # edit-only, not right after a draft
        assert later.get("draft") is None


@requires_db
def test_the_cards_revise_box_revises_whatever_it_says(model):
    with _client() as c:
        tid = _thread(c)
        model.replies = [LETTER, SHORTER]
        one, _ = _send(c, tid, "write a cover letter for Acme")
        r = c.post(f"/api/chat/threads/{tid}/messages",
                   json={"content": "less stiff in the second paragraph", "revise_draft": one["draft"]["id"]})
        done = next(d for ev, d in _sse(r.text) if ev == "done")
        assert done["draft"]["version"] == 2 and done["draft"]["action"] == "revise"
        # Review round 2: words a fast path would take (a lookup, the date)
        # are still a revision from the box, and nothing is left pending.
        model.replies = [SHORTER, FORMAL, "Sure."]
        for words in ("look up the weather in Toledo", "what's the date today"):
            r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": words, "revise_draft": one["draft"]["id"]})
            assert next(d for ev, d in _sse(r.text) if ev == "done")["draft"]["action"] == "revise"
        after, _ = _send(c, tid, "tell me a joke")
        assert after.get("draft") is None and after["content"] == "Sure."
        other = _thread(c)
        bad = c.post(f"/api/chat/threads/{other}/messages", json={"content": "x", "revise_draft": one["draft"]["id"]})
        assert bad.status_code == 404


async def _new_draft(body: str = LETTER):
    from sqlalchemy import text as sql

    from web.backend.db import session_scope

    async with session_scope() as s:
        tid = (await s.execute(sql("INSERT INTO chat_threads (title) VALUES ('race') RETURNING id"))).scalar_one()
        await s.commit()
    t = drafts_api.guided_turn(tid, "cover_letter", "letter-block", "Cover letter", "write a cover letter")
    await t.finish(body)
    return tid, t.draft_id


def _revise(tid, did, base, q):
    return drafts_api.DraftTurn(action="revise", thread_id=tid, kind="cover_letter", template="letter-block",
                                title="Cover letter", request=q, draft_id=did, base=base,
                                passes=[[{"role": "system", "content": ""}]])


async def _all_versions(did):
    from web.backend.db import session_scope

    async with session_scope() as s:
        return await drafts_api._versions(s, did)


@requires_db
async def test_two_revisions_finishing_at_once_both_keep_their_versions():
    import asyncio

    tid, did = await _new_draft()
    _, base = await drafts_api.latest_draft(tid)
    a, b = _revise(tid, did, base, "shorter"), _revise(tid, did, base, "more formal")
    res = await asyncio.gather(a.finish(SHORTER), b.finish(FORMAL), return_exceptions=True)
    assert not [r for r in res if isinstance(r, Exception)], res
    vs = await _all_versions(did)
    assert [v["version"] for v in vs] == [1, 2, 3]
    assert sorted(v["based_on"] for v in vs[1:]) == [1, 1]
    assert any("came in while this was written" in r[0] for r in res)


@requires_db
async def test_a_hand_edit_during_a_revision_is_kept_and_the_reply_says_so():
    tid, did = await _new_draft()
    _, base = await drafts_api.latest_draft(tid)
    rev = _revise(tid, did, base, "shorter")
    edit = await drafts_api.edit_draft(did, drafts_api.EditBody(text=LETTER + "\nMY HAND EDIT.\n", base_version=1))
    assert edit["version"]["version"] == 2
    line, card = await rev.finish(SHORTER)
    vs = await _all_versions(did)
    assert [(v["version"], v["author"]) for v in vs] == [(1, "model"), (2, "person"), (3, "model")]
    assert "MY HAND EDIT" in vs[1]["body"] and vs[2]["based_on"] == 1
    assert card["based_on"] == 1 and card["newer"] == 2
    assert "Version 2 came in" in line and "version picker" in line


@requires_db
async def test_two_hand_edits_at_once_give_one_version_and_one_409():
    import asyncio

    tid, did = await _new_draft()
    res = await asyncio.gather(
        drafts_api.edit_draft(did, drafts_api.EditBody(text=LETTER + "\none\n", base_version=1)),
        drafts_api.edit_draft(did, drafts_api.EditBody(text=LETTER + "\ntwo\n", base_version=1)),
        return_exceptions=True)
    assert not [r for r in res if isinstance(r, Exception)], res
    statuses = sorted(getattr(r, "status_code", 200) for r in res)
    assert statuses == [200, 409]
    conflict = next(r for r in res if getattr(r, "status_code", 200) == 409)
    assert json.loads(conflict.body)["code"] == "stale_version"


@requires_db
def test_xml_illegal_characters_are_stripped_and_the_docx_saves(model, documents_dir):
    with _client() as c:
        tid = _thread(c)
        model.replies = [LETTER]
        one, _ = _send(c, tid, "write a cover letter for Acme")
        did = one["draft"]["id"]
        r = c.post(f"/api/chat/drafts/{did}/versions",
                   json={"text": "Dear Sam,\n\nSalary \u0001 expected \ufffe here.\n", "base_version": 1})
        assert r.status_code == 200
        body = r.json()["version"]["body"]
        assert "\u0001" not in body and "\ufffe" not in body and "Salary  expected  here." in body
        s = c.post(f"/api/chat/drafts/{did}/save", json={"format": "docx"})
        assert s.status_code == 200 and s.json()["check"]["ok"]
    # and at render, for text that reached the database another way
    from domovoi.drafts import docx_file, templates as tpl

    assert docx_file.render("Dear Sam,\n\nA \u0002 B \ufffe C\n", tpl.get("letter-personal"))


@requires_db
def test_each_version_records_the_device_that_made_it(model):
    with _client() as c:
        tid = _thread(c)
        model.replies = [LETTER]
        r = c.post(f"/api/chat/threads/{tid}/messages",
                   json={"content": "write a cover letter for Acme", "device_id": "browser-aaaa"})
        did = next(d for ev, d in _sse(r.text) if ev == "done")["draft"]["id"]
        e = c.post(f"/api/chat/drafts/{did}/versions",
                   json={"text": "Dear Sam,\n\nHi.", "base_version": 1, "device_id": "android-bbbb"})
        assert e.status_code == 200
        vs = c.get(f"/api/chat/drafts/{did}").json()["versions"]
        assert [v["device_id"] for v in vs] == ["browser-aaaa", "android-bbbb"]
        assert "device_name" in vs[1]


@requires_db
async def test_a_version_names_its_device_and_the_person_whose_device_it_is():
    from sqlalchemy import text as sql

    from web.backend.db import session_scope

    async with session_scope() as s:
        pid = (await s.execute(sql("INSERT INTO people (name) VALUES ('Sam D4') RETURNING id"))).scalar_one()
        await s.execute(sql("INSERT INTO devices (device_id, name, platform, owner_person_id) "
                            "VALUES ('android-d4own', 'Sam''s phone', 'android', :p)"), {"p": pid})
        await s.commit()
    try:
        tid, did = await _new_draft()
        await drafts_api.edit_draft(did, drafts_api.EditBody(text=LETTER + "\nMine.\n", base_version=1,
                                                             device_id="android-d4own"))
        v = (await _all_versions(did))[1]
        assert (v["device_id"], v["device_name"], v["person_id"]) == ("android-d4own", "Sam's phone", pid)
    finally:
        async with session_scope() as s:
            await s.execute(sql("DELETE FROM devices WHERE device_id = 'android-d4own'"))
            await s.execute(sql("DELETE FROM people WHERE id = :p"), {"p": pid})
            await s.commit()


@requires_db
async def test_a_first_draft_whose_version_fails_is_not_retried_as_a_revision(monkeypatch):
    """Review round 2 (nit): a first draft's error is raised as it is, not
    retried with the draft id its rolled-back transaction made ("draft gone")."""
    from sqlalchemy import text as sql
    from sqlalchemy.exc import IntegrityError as DbIntegrityError

    from web.backend.db import session_scope

    async with session_scope() as s:
        tid = (await s.execute(sql("INSERT INTO chat_threads (title) VALUES ('nit') RETURNING id"))).scalar_one()
        await s.commit()
    # The version's row fails its person reference: an IntegrityError after
    # the draft's own row was written in the same transaction.
    monkeypatch.setattr(drafts_api, "_OWNER", "(SELECT -1)")
    t = drafts_api.guided_turn(tid, "cover_letter", "letter-block", "Cover letter", "write a cover letter")
    with pytest.raises(DbIntegrityError):
        await t.finish(LETTER)
