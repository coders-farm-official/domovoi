"""The honest heads-up in home-server chats (chat.py, chat_documents.py; documents plan D3).

The model is a fake at the ``chat_stream`` boundary; documents are made in
the test (doc_fixtures.py) and read by the real worker. What matters: the
card comes from the measured rules, not the model (it is never asked for
one); a card is the reply until the person picks an option, and is
answered once; "calculate exactly" sends the listing rules and the app
adds up the checked numbers; a kind shows once per chat; this device's
"don't warn me" and master switch silence a card but keep the notes that
guard against a wrong answer; a blurry picture to read gets its card.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter

import web.backend.api.chat as chat_api
import web.backend.api.chat_documents as docs_api
from domovoi.doc_qa import headsup
from domovoi.tests import doc_fixtures as fx
from domovoi.tests.conftest import requires_db
from web.backend.main import app

_SENTENCES = [
    "Both parties keep a signed copy with their records.", "Questions go to the building office in writing.",
    "The office answers within two working days.", "Visitors sign the book at the front desk after ten.",
    "The garden behind the building is shared by all residents.", "Bicycles are kept in the basement storage room.",
    "Recycling is collected on Tuesdays and the bins go back by noon.", "Keys stay the property of the owner.",
    "The laundry room is on the ground floor near the lobby.", "Notices are posted beside the mailboxes.",
    "Heating is checked each autumn by a contractor.", "Hallways stay clear of shoes and boxes.",
    "The roof terrace closes at eleven at night.", "Parcels wait at the front desk for a week.",
    "Quiet hours run from ten at night to seven in the morning.", "The lift is serviced on the first Monday.",
]


def _filler(n: int, k: int = 12) -> str:
    """Page n's own sentences (no line repeats across pages, so the
    extractor's running-header strip keeps them)."""
    return " ".join(f"{_SENTENCES[(n * 5 + i) % len(_SENTENCES)][:-1]} on page {n} item {i}." for i in range(k))


PAGES = ([f"The monthly rent is $1,200, due on the first day of each month. {_filler(1)}",
          f"The security deposit is $1,500, returned within thirty days. {_filler(2)}"]
         + [f"Page {n} covers the shared garden and the laundry room. {_filler(n)}" for n in range(3, 13)])
CALC_Q = "What is the total of the rent and the deposit?"


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(docs_api, "DOCS_DIR", tmp_path / "documents")
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", tmp_path / "chat_uploads")


@pytest.fixture
def model(monkeypatch):
    class _M:
        reply = "The rent is $1,200 [p. 1]."
        calls: list[dict[str, Any]] = []

    _M.calls = []

    async def _stream(messages, model, **kw):
        _M.calls.append({"messages": messages, "model": model})
        for i in range(0, len(_M.reply), 9):
            yield _M.reply[i:i + 9]

    async def _tags(*a, **kw):
        return None              # "Ollama couldn't be asked": a picture turn goes ahead

    async def _no_system(*a, **kw):
        return None

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    monkeypatch.setattr(chat_api.ollama_client, "tags_or_none", _tags)
    monkeypatch.setattr(chat_api.ollama_client, "model_system_or_none", _no_system)
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


def _done(r) -> dict:
    assert r.status_code == 200, r.text
    return next(d for ev, d in _sse(r.text) if ev == "done")


def _thread_with_doc(c, pages=PAGES, image_pages=()) -> tuple[int, str]:
    pdf = fx.make_pdf([fx.text_page(p) for p in pages], image_pages=image_pages)
    up = c.post("/api/chat/documents", files={"file": ("lease.pdf", io.BytesIO(pdf), "application/pdf")})
    assert up.status_code == 200, up.text
    tid = c.post("/api/chat/threads", json={}).json()["id"]
    return tid, up.json()["token"]


def _send(c, tid, content, docs=(), heads_up=None, images=()):
    body: dict[str, Any] = {"content": content, "documents": [{"token": t} for t in docs],
                            "images": [{"token": t, "name": "p.png"} for t in images]}
    if heads_up is not None:
        body["heads_up"] = heads_up
    return _done(c.post(f"/api/chat/threads/{tid}/messages", json=body))


def _pick(c, tid, card, option, heads_up=None):
    body: dict[str, Any] = {"message_id": card["id"], "option": option}
    if heads_up is not None:
        body["heads_up"] = heads_up
    return c.post(f"/api/chat/threads/{tid}/heads-up", json=body)


# ─── A card instead of a shaky answer ───────────────────────────────────
@requires_db
def test_a_sum_over_the_document_gets_its_card_and_no_model_is_asked(model):
    with _client() as c:
        tid, tok = _thread_with_doc(c)
        card = _send(c, tid, CALC_Q, [tok])
        assert model.calls == []
        hu = card["doc"]["heads_up"]
        assert card["doc"]["mode"] == "heads_up" and hu["kind"] == "calc" and hu["chosen"] is None
        assert [o["id"] for o in hu["options"]] == ["calculate", "try_anyway"]
        assert [o["label"] for o in hu["options"]] == ["calculate exactly", "try anyway"]
        assert hu["never_label"] == "don't warn me about this again"
        assert card["content"] == hu["text"] and card["model"] is None
        assert card["stats"]["model_role"] == "heads_up" and card["retryable"] is False


@requires_db
def test_calculate_exactly_lists_the_numbers_and_the_app_adds_them(model):
    with _client() as c:
        tid, tok = _thread_with_doc(c)
        card = _send(c, tid, CALC_Q, [tok])
        model.reply = "- $1,200 [p. 1] monthly rent\n- $1,500 [p. 2] security deposit"
        done = _done(_pick(c, tid, card, "calculate"))
        rules = model.calls[0]["messages"][0]["content"]
        assert "Don't calculate anything" in rules and "Answer only from the passages" not in rules
        assert done["content"].startswith("**Total: $2,700 (1,200 + 1,500 = 2,700), from the 2 entries I found**")
        codes = [n["code"] for n in done["doc"]["notes"]]
        assert "calc_worked" in codes and "heads_up_calc" not in codes
        assert done["doc"]["calc"] == "sum" and done["doc"]["heads_up_chosen"] == {"kind": "calc", "option": "calculate"}
        assert {s["label"] for s in done["doc"]["sources"] if s["cited"]} == {"p. 1", "p. 2"}
        msgs = c.get(f"/api/chat/threads/{tid}/messages").json()["messages"]
        assert msgs[-2]["doc"]["heads_up"]["chosen"] == "calculate"


@requires_db
def test_try_anyway_answers_as_before_with_the_arithmetic_note(model):
    with _client() as c:
        tid, tok = _thread_with_doc(c)
        card = _send(c, tid, CALC_Q, [tok])
        model.reply = "Together they come to $2,700 [p. 1] [p. 2]."
        done = _done(_pick(c, tid, card, "try_anyway"))
        assert "Answer only from the passages" in model.calls[0]["messages"][0]["content"]
        assert done["content"].startswith("Together they come to $2,700")
        assert "heads_up_calc" in [n["code"] for n in done["doc"]["notes"]]


@requires_db
def test_a_kind_shows_once_per_chat(model):
    with _client() as c:
        tid, tok = _thread_with_doc(c)
        card = _send(c, tid, CALC_Q, [tok])
        _done(_pick(c, tid, card, "try_anyway"))
        again = _send(c, tid, "What is the total of the deposit and the rent?")
        assert again["doc"]["mode"] == "question" and "heads_up" not in again["doc"]
        assert "heads_up_calc" in [n["code"] for n in again["doc"]["notes"]]
        # A new chat about the same kind of question gets the card again.
        tid2, tok2 = _thread_with_doc(c)
        assert _send(c, tid2, CALC_Q, [tok2])["doc"]["mode"] == "heads_up"


@requires_db
def test_dont_warn_me_and_the_master_switch_leave_the_guarding_note(model):
    with _client() as c:
        tid, tok = _thread_with_doc(c)
        skipped = _send(c, tid, CALC_Q, [tok], heads_up={"skip": ["calc"]})
        assert skipped["doc"]["mode"] == "question" and len(model.calls) == 1
        assert "heads_up_calc" in [n["code"] for n in skipped["doc"]["notes"]]
        tid2, tok2 = _thread_with_doc(c)
        off = _send(c, tid2, "Rewrite this in plain English", [tok2], heads_up={"off": True})
        assert off["doc"]["mode"] == "question" and not [n for n in off["doc"]["notes"] if n["code"].startswith("heads_up")]


@requires_db
def test_a_whole_document_rewrite_is_told_what_the_model_sees(model):
    with _client() as c:
        tid, tok = _thread_with_doc(c)
        card = _send(c, tid, "Rewrite this in plain English", [tok])
        hu = card["doc"]["heads_up"]
        assert hu["kind"] == "rewrite" and [o["id"] for o in hu["options"]] == ["try_anyway"]
        assert card["content"].startswith("lease.pdf is 12 pages. Domovoi sees about ")
        model.reply = "The rent is $1,200 a month [p. 1]."
        done = _done(_pick(c, tid, card, "try_anyway"))
        # Its words match nothing, so the model gets the start of the
        # document, and the note says the rest wasn't in the answer.
        assert "[p. 1]" in model.calls[0]["messages"][-1]["content"]
        assert "heads_up_rewrite" in [n["code"] for n in done["doc"]["notes"]]


@requires_db
def test_a_card_is_answered_once_and_only_while_it_is_the_last_message(model):
    with _client() as c:
        tid, tok = _thread_with_doc(c)
        card = _send(c, tid, CALC_Q, [tok])
        bad = _pick(c, tid, card, "home_server")
        assert bad.status_code == 409 and bad.json()["code"] == "bad_option"
        _done(_pick(c, tid, card, "try_anyway"))
        twice = _pick(c, tid, card, "calculate")
        assert twice.status_code == 409 and twice.json()["code"] == "already_answered"
        tid2, tok2 = _thread_with_doc(c)
        card2 = _send(c, tid2, CALC_Q, [tok2])
        _send(c, tid2, "When is the rent due?")
        stale = _pick(c, tid2, card2, "calculate")
        assert stale.status_code == 409 and stale.json()["code"] == "stale"
        msgs = c.get(f"/api/chat/threads/{tid2}/messages").json()["messages"]
        not_card = _pick(c, tid2, msgs[-1], "try_anyway")
        assert not_card.status_code == 409 and not_card.json()["code"] == "not_a_card"
        assert c.post(f"/api/chat/threads/{tid2}/heads-up", json={"message_id": 99999999, "option": "x"}).status_code == 404


@requires_db
def test_scanned_pages_are_named_under_the_answer(model):
    with _client() as c:
        tid, tok = _thread_with_doc(c, pages=PAGES[:2] + [""], image_pages=(2,))
        done = _send(c, tid, "When is the rent due?", [tok])
        assert done["doc"]["mode"] == "question"
        notes = {n["code"]: n["text"] for n in done["doc"]["notes"]}
        assert notes["heads_up_scanned"] == "Page 3 is a picture of text and wasn't read for this answer."


@requires_db
def test_a_slow_long_summary_gets_its_card_then_go_ahead_starts_the_job(model, monkeypatch):
    from web.backend.api import chat_doc_jobs as jobs
    from domovoi.doc_qa import longdoc

    async def _slow(model=None):
        return longdoc.Speed(70.0, 9.7, 4.75)          # a CPU box (D0)

    monkeypatch.setattr(jobs, "measured_speed", _slow)
    with _client() as c:
        big = [f"Section {n}. {_filler(n, 40)}" for n in range(1, 31)]
        tid, tok = _thread_with_doc(c, pages=big)
        card = _send(c, tid, "Summarize this", [tok])
        hu = card["doc"]["heads_up"]
        assert hu["kind"] == "long" and [o["id"] for o in hu["options"]] == ["go_ahead"]
        assert "parts here, about" in card["content"] and model.calls == []
        r = _pick(c, tid, card, "go_ahead")
        assert r.status_code == 200
        events = [ev for ev, _ in _sse(r.text)]
        assert events[0] == "job"


@requires_db
def test_a_blurry_picture_to_read_gets_its_card(model, monkeypatch):
    monkeypatch.setattr(chat_api.core_settings, "ollama_vision_model", "qwen3-vl:2b")
    img = Image.new("L", (800, 1040), 245)
    d = ImageDraw.Draw(img)
    for y in range(40, 1000, 22):
        d.text((40, y), "The tenant pays the rent on the first day of each month.", fill=20)
    buf = io.BytesIO()
    img.filter(ImageFilter.GaussianBlur(9)).save(buf, "PNG")
    with _client() as c:
        up = c.post("/api/chat/uploads", files={"file": ("letter.png", io.BytesIO(buf.getvalue()), "image/png")})
        assert up.status_code == 200, up.text
        tid = c.post("/api/chat/threads", json={}).json()["id"]
        # (A letter question goes to the letter turn after its card, D6:
        # test_chat_letters.py; a receipt to read is a plain picture turn.)
        card = _send(c, tid, "Read this receipt to me", images=[up.json()["token"]])
        hu = card["doc"]["heads_up"]
        assert hu["kind"] == "blurry" and [o["id"] for o in hu["options"]] == ["choose_another", "send_anyway"]
        assert model.calls == []
        model.reply = "It says the rent is due on the first."
        done = _done(_pick(c, tid, card, "send_anyway"))
        assert done["content"].startswith("It says") and model.calls[0]["model"] == "qwen3-vl:2b"
        assert any(m.get("images") for m in model.calls[0]["messages"])
        # The card is the app's words: the vision model never sees it (review round 1).
        assert all(hu["text"] not in str(m.get("content") or "") for m in model.calls[0]["messages"])
        assert [m["role"] for m in model.calls[0]["messages"]] == ["system", "user"]
        # The same picture with nothing to read in it: no card.
        tid2 = c.post("/api/chat/threads", json={}).json()["id"]
        plain = _send(c, tid2, "Is this a nice colour?", images=[up.json()["token"]])
        assert "heads_up" not in (plain.get("doc") or {})
        # "choose another" records the pick and answers nothing.
        tid3 = c.post("/api/chat/threads", json={}).json()["id"]
        card3 = _send(c, tid3, "Read this receipt", images=[up.json()["token"]])
        n = len(model.calls)
        r = _pick(c, tid3, card3, "choose_another")
        assert r.status_code == 200 and r.json()["chosen"] == "choose_another" and len(model.calls) == n


def test_with_ai_off_the_rules_say_nothing(monkeypatch):
    monkeypatch.setattr(docs_api, "ai_turned_off", lambda: True)
    loaded = [{"name": "lease.pdf", "kind": "pdf", "pages": 12, "words": 4000, "scanned_pages": [3],
               "parts": [{"label": f"p. {i}", "text": t} for i, t in enumerate(PAGES, 1)]}]
    req = headsup.Request(mode="question", question=CALC_Q, docs=docs_api.heads_up_docs(loaded),
                          given=[("p. 1", PAGES[0]), ("p. 2", PAGES[1])], can=docs_api.SERVER_CAN,
                          ai_on=not docs_api.ai_turned_off())
    assert headsup.findings(req) == []
