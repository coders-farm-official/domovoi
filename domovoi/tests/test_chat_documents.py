"""Documents in home-server chats (web/backend/api/chat_documents.py + chat.py).

The model is a fake at the ``chat_stream`` boundary; the documents are
made in the test (doc_fixtures.py) and read by the real worker process.
What matters most: an answer cites only passages the app gave, "I
couldn't find that" is said without asking the model when nothing
matches, the model sees passages in a marked block and never the whole of
a long document, an attachment's files go with its thread, and every
refusal is a sentence.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import web.backend.api.chat as chat_api
import web.backend.api.chat_documents as docs_api
from domovoi.doc_qa import answer as qa
from domovoi.tests import doc_fixtures as fx
from domovoi.tests.conftest import requires_db
from web.backend.main import app

LEASE = [
    "The monthly rent is $1,200, due on the first day of each month, paid by bank transfer.",
    "The security deposit is $1,500. It is returned within thirty days after the tenant moves out.",
    "Pets are not allowed without written permission from the landlord. Smoking is not allowed.",
]


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


@pytest.fixture(autouse=True)
def docs_dir(tmp_path, monkeypatch):
    d = tmp_path / "documents"
    monkeypatch.setattr(docs_api, "DOCS_DIR", d)
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", tmp_path / "chat_uploads")
    return d


@pytest.fixture
def model(monkeypatch):
    """The fake model: ``model.reply`` is what it says; ``model.calls`` what
    it was sent."""
    class _M:
        reply = "The deposit is $1,500 [p. 2]."
        calls: list[dict[str, Any]] = []

    _M.calls = []

    async def _stream(messages, model, **kw):
        _M.calls.append({"messages": messages, "model": model})
        for i in range(0, len(_M.reply), 7):
            yield _M.reply[i:i + 7]

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    return _M


def _sse(text_body: str) -> list[tuple[str, Any]]:
    out = []
    for frame in text_body.split("\n\n"):
        ev, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if data:
            out.append((ev, json.loads(data)))
    return out


def _pdf(pages=LEASE) -> bytes:
    return fx.make_pdf([fx.text_page(p) for p in pages])


def _upload(c, name="lease.pdf", data=None, ctype="application/pdf"):
    return c.post("/api/chat/documents", files={"file": (name, io.BytesIO(data if data is not None else _pdf()), ctype)})


def _send(c, tid, content="", documents=()):
    r = c.post(f"/api/chat/threads/{tid}/messages",
               json={"content": content, "documents": [{"token": t} for t in documents]})
    assert r.status_code == 200, r.text
    return next(d for ev, d in _sse(r.text) if ev == "done"), [d["text"] for ev, d in _sse(r.text) if ev == "delta"]


# ─── Upload ──────────────────────────────────────────────────────────────
@requires_db
def test_upload_reads_the_document_and_says_what_it_is(docs_dir):
    with _client() as c:
        r = _upload(c)
        assert r.status_code == 200, r.text
        m = r.json()
        assert m["kind"] == "pdf" and m["pages"] == 3 and m["name"] == "lease.pdf"
        assert m["summary"].startswith("lease.pdf · 3 pages · ") and m["words"] > 30
        assert len(m["token"]) == 32
        assert (docs_dir / f"{m['token']}.pdf").is_file() and (docs_dir / f"{m['token']}-parts.json").is_file()
        parts = c.get(f"/api/chat/documents/{m['token']}/parts").json()["parts"]
        assert [p["label"] for p in parts] == ["p. 1", "p. 2", "p. 3"] and parts[1]["page"] == 2
        f = c.get(f"/api/chat/documents/{m['token']}/file")
        assert f.status_code == 200 and f.headers["content-type"] == "application/pdf"
        assert f.headers["content-disposition"].startswith("inline")
        assert f.headers["x-content-type-options"] == "nosniff"


@requires_db
@pytest.mark.parametrize("name,maker,kind", [
    ("lease.docx", fx.make_docx, "docx"), ("lease.odt", fx.make_odt, "odt"),
    ("budget.xlsx", fx.make_xlsx, "xlsx"),
    ("bank.csv", lambda: b"Date,Amount\n2026-01-01,12.50\n2026-01-02,7\n", "csv"),
    ("notes.txt", lambda: b"Plant beans in May.\n", "txt"), ("notes.md", lambda: b"# Garden\n\nBeans.\n", "md"),
])
def test_every_format_is_taken(name, maker, kind):
    with _client() as c:
        r = _upload(c, name, maker(), "application/octet-stream")
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == kind and r.json()["pages"] is None
        f = c.get(f"/api/chat/documents/{r.json()['token']}/file")
        assert f.headers["content-disposition"].startswith("attachment")


@requires_db
@pytest.mark.parametrize("name,code,words", [
    ("old.doc", "old_format", "Save it as .docx or PDF"),
    ("old.rtf", "old_format", "Save it as .docx or PDF"),
    ("tool.exe", "unsupported_format", "Chat reads PDF"),
])
def test_old_and_unknown_formats_are_refused_with_what_to_do(name, code, words, docs_dir):
    with _client() as c:
        r = _upload(c, name, b"whatever")
        assert r.status_code == 415 and r.json()["code"] == code and words in r.json()["detail"]
    assert not docs_dir.exists() or not any(docs_dir.iterdir())


@requires_db
@pytest.mark.parametrize("name,data,words", [
    ("bomb.docx", fx.zip_bomb_docx(24), "zip bomb"),
    ("laughs.docx", fx.make_docx(document_xml=fx.BILLION_LAUGHS), "entity declarations"),
    ("external.docx", fx.make_docx(document_xml=fx.EXTERNAL_ENTITY), "entity declarations"),
    ("fake.pdf", b"<html>hello</html>", "isn't a PDF"),
    ("scan.pdf", fx.make_pdf([[]], image_pages=(0,)), "pictures of text"),
], ids=["zip-bomb", "billion-laughs", "external-entity", "not-a-pdf", "scan-only"])
def test_hostile_or_unreadable_files_are_refused_and_nothing_is_kept(name, data, words, docs_dir):
    with _client() as c:
        r = _upload(c, name, data)
        assert r.status_code == 422 and r.json()["code"] == "unreadable"
        assert words in r.json()["detail"]
    assert not any(p for p in docs_dir.iterdir() if p.is_file())


@requires_db
def test_the_byte_cap(monkeypatch, docs_dir):
    monkeypatch.setattr(docs_api.doc_extract, "MAX_FILE_BYTES", 1000)
    with _client() as c:
        r = _upload(c, "big.txt", b"a " * 2000)
        assert r.status_code == 413 and r.json()["code"] == "file_too_large"
    assert not any(docs_dir.iterdir())


def test_the_transport_cap_lets_a_100_mb_document_through():
    from domovoi.transport_guard import body_limit_for

    assert body_limit_for("/api/chat/documents") > 100 * 1024 * 1024
    assert body_limit_for("/api/chat/documents/extract") > 100 * 1024 * 1024
    assert body_limit_for("/api/chat/threads/1/messages") == 1024 * 1024


# ─── Answering ───────────────────────────────────────────────────────────
@requires_db
def test_an_attachment_with_no_question_is_answered_without_the_model(model):
    with _client() as c:
        tok = _upload(c).json()["token"]
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        done, deltas = _send(c, tid, "", [tok])
        assert done["content"] == "I've read lease.pdf (3 pages). Ask me anything about it."
        assert done["doc"]["mode"] == "attached" and model.calls == []
        msgs = c.get(f"/api/chat/threads/{tid}/messages").json()["messages"]
        assert msgs[0]["documents"][0]["token"] == tok and msgs[0]["content"] == ""
        title = next(t for t in c.get("/api/chat/threads").json()["threads"] if t["id"] == tid)["title"]
        assert title == "About lease.pdf"


@requires_db
def test_a_question_is_answered_from_the_found_passages_with_checked_citations(model):
    model.reply = "The deposit is $1,500 [p. 2]. Pets need permission [p. 9]."
    with _client() as c:
        tok = _upload(c).json()["token"]
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        done, _ = _send(c, tid, "How much is the security deposit?", [tok])
        assert done["content"] == "The deposit is $1,500 [p. 2]. Pets need permission."
        doc = done["doc"]
        assert doc["found"] and doc["mode"] == "question"
        assert [s["label"] for s in doc["sources"]] == ["p. 2"]
        src = doc["sources"][0]
        assert src["cited"] and src["page"] == 2 and src["token"] == tok and src["name"] == "lease.pdf"
        assert "1,500" in src["match"]
        # The model was given the rules and a marked block, not the document.
        sent = model.calls[-1]["messages"]
        assert sent[0]["role"] == "system" and qa.RULES_QUESTION in sent[0]["content"]
        assert len(sent) == 2 and sent[1]["role"] == "user"
        assert "<<<" in sent[1]["content"] and "[p. 2]" in sent[1]["content"]
        assert "Pets are not allowed" not in sent[1]["content"]
        assert done["stats"]["model_role"] == "documents"
        # Read back the same way.
        last = c.get(f"/api/chat/threads/{tid}/messages").json()["messages"][-1]
        assert last["doc"] == doc


@requires_db
def test_nothing_found_says_so_without_asking_the_model(model):
    with _client() as c:
        tok = _upload(c).json()["token"]
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        _send(c, tid, "", [tok])
        done, deltas = _send(c, tid, "Who won the 1998 World Cup?")
        assert done["content"] == qa.NOT_FOUND and deltas == [qa.NOT_FOUND]
        assert done["doc"]["found"] is False and done["doc"]["sources"] == []
        assert done["doc"]["notes"][0]["code"] == "not_found"
        assert "lease.pdf" in done["doc"]["notes"][0]["text"]
        assert model.calls == []


@requires_db
def test_a_model_that_says_the_passages_dont_answer_reads_as_couldnt_find(model):
    model.reply = "The passages don't mention cats at all."
    with _client() as c:
        tok = _upload(c).json()["token"]
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        done, _ = _send(c, tid, "Are cats allowed as pets?", [tok])
        assert len(model.calls) == 1
        assert done["content"] == qa.NOT_FOUND and done["doc"]["found"] is False
        assert all(not s["cited"] for s in done["doc"]["sources"])


@requires_db
def test_a_follow_up_uses_the_previous_question(model):
    model.reply = "The late fee is $50 [p. 1]."
    pages = ["The monthly rent is $1,200. A late fee of $50 applies after five days.", LEASE[1], LEASE[2]]
    with _client() as c:
        tok = _upload(c, data=_pdf(pages)).json()["token"]
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        _send(c, tid, "What is the late fee?", [tok])
        done, _ = _send(c, tid, "and how much is it?")
        assert done["doc"]["found"] and done["doc"]["sources"][0]["label"] == "p. 1"
        assert "Earlier question: What is the late fee?" in model.calls[-1]["messages"][1]["content"]


@requires_db
def test_a_summary_reads_a_short_document_whole_and_a_long_one_in_a_job(model):
    model.reply = "- Rent is $1,200 [p. 1]\n- Deposit $1,500 [p. 2]"
    with _client() as c:
        tok = _upload(c).json()["token"]
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        done, _ = _send(c, tid, "Summarize this", [tok])
        assert done["doc"]["mode"] == "summary" and len(model.calls) == 1
        assert qa.RULES_SUMMARY in model.calls[0]["messages"][0]["content"]
        assert all(f"[p. {n}]" in model.calls[0]["messages"][1]["content"] for n in (1, 2, 3))
        long_pages = [(" ".join(LEASE) + " ") * 12 for _ in range(4)]
        tok2 = _upload(c, "long.pdf", _pdf(long_pages)).json()["token"]
        tid2 = c.post("/api/chat/threads", json={}).json()["id"]
        done2, _ = _send(c, tid2, "give me an overview", [tok2])
        # Longer than one pass: a job (chat_doc_jobs.py, D2) reads it; this
        # one fits a single part, so one call writes the summary.
        assert len(model.calls) == 2
        assert done2["doc"]["mode"] == "summary" and done2["doc"]["job"]["parts"] == 1
        assert done2["doc"]["found"] is True


@requires_db
def test_a_long_document_question_sends_only_a_few_passages(model):
    pages = [f"Page {n} is about the garden, the hedges and the fences of the estate. " * 25 for n in range(1, 21)]
    pages[13] = "The boiler is serviced every October by the landlord's engineer. " * 5
    model.reply = "Every October [p. 14]."
    with _client() as c:
        meta = _upload(c, "manual.pdf", _pdf(pages)).json()
        assert any(n["code"] == "long_document" for n in meta["notes"])
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        done, _ = _send(c, tid, "When is the boiler serviced?", [meta["token"]])
        user = model.calls[-1]["messages"][1]["content"]
        assert len(user) < qa.RETRIEVE_CHARS + 1500
        assert done["doc"]["sources"][0]["label"] == "p. 14" and done["doc"]["sources"][0]["cited"]


@requires_db
def test_unknown_tokens_are_ignored_and_a_message_needs_words_or_a_document(model):
    with _client() as c:
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "", "documents": [{"token": "0" * 32}]})
        assert r.status_code == 422
        r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "", "documents": [{"token": "../x"}]})
        assert r.status_code == 422


@requires_db
def test_deleting_the_thread_removes_its_documents(model, docs_dir):
    with _client() as c:
        tok = _upload(c).json()["token"]
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        _send(c, tid, "", [tok])
        assert any(docs_dir.glob(f"{tok}*"))
        assert c.delete(f"/api/chat/threads/{tid}").status_code == 200
        assert not any(docs_dir.glob(f"{tok}*"))
        assert c.get(f"/api/chat/documents/{tok}/file").status_code == 404


@requires_db
def test_thread_documents_list(model):
    with _client() as c:
        a = _upload(c).json()["token"]
        b = _upload(c, "budget.xlsx", fx.make_xlsx()).json()["token"]
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        _send(c, tid, "", [a])
        _send(c, tid, "what is the rent", [b, a])
        got = c.get(f"/api/chat/threads/{tid}/documents").json()["documents"]
        assert [d["token"] for d in got] == [a, b]
        assert c.get("/api/chat/threads/999999/documents").status_code == 404


# ─── Ask about this document (Documents page) ────────────────────────────
@requires_db
def test_ask_about_a_document_in_the_library(model, tmp_path, monkeypatch):
    lib = tmp_path / "Documents"
    (lib / "Home").mkdir(parents=True)
    (lib / "Home" / "lease.pdf").write_bytes(_pdf())
    (lib / "id_rsa.pem").write_bytes(b"secret")
    monkeypatch.setattr(chat_api.core_settings, "documents_dir", str(lib))
    with _client() as c:
        r = c.post("/api/chat/threads/from-document", json={"rel_path": "Home/lease.pdf"})
        assert r.status_code == 200, r.text
        t, d = r.json()["thread"], r.json()["document"]
        assert t["title"] == "About lease.pdf" and d["pages"] == 3
        msgs = c.get(f"/api/chat/threads/{t['id']}/messages").json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[1]["content"] == "I've read lease.pdf (3 pages). Ask me anything about it."
        done, _ = _send(c, t["id"], "How much is the deposit?")
        assert done["doc"]["found"]
        assert c.post("/api/chat/threads/from-document", json={"rel_path": "id_rsa.pem"}).status_code == 404
        assert c.post("/api/chat/threads/from-document", json={"rel_path": "../outside.pdf"}).status_code == 400
        assert c.post("/api/chat/threads/from-document", json={"rel_path": "nope.pdf"}).status_code == 404


# ─── Extract only (a phone that can't read PDF text) ─────────────────────
@requires_db
def test_extract_keeps_nothing(docs_dir, tmp_path):
    with _client() as c:
        r = c.post("/api/chat/documents/extract", files={"file": ("lease.pdf", io.BytesIO(_pdf()), "application/pdf")})
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "pdf" and [p["label"] for p in body["parts"]] == ["p. 1", "p. 2", "p. 3"]
        assert "token" not in body
    assert not docs_dir.exists() or not any(docs_dir.iterdir())


# ─── The household's AI switch ───────────────────────────────────────────
@requires_db
def test_with_ai_off_the_server_takes_no_document_for_a_chat(monkeypatch, docs_dir, tmp_path):
    monkeypatch.setattr(docs_api, "ai_turned_off", lambda: True)
    with _client() as c:
        r = _upload(c)
        assert r.status_code == 409
        r = c.post("/api/chat/threads/from-document", json={"rel_path": "lease.pdf"})
        assert r.status_code == 409
        # Reading text for a phone asks no model: still there.
        r = c.post("/api/chat/documents/extract", files={"file": ("lease.pdf", io.BytesIO(_pdf()), "application/pdf")})
        assert r.status_code == 200
    assert not docs_dir.exists() or not any(docs_dir.iterdir())
