"""Long documents in Chat: map/reduce jobs (web/backend/api/chat_doc_jobs.py).

The model is a fake at the ``chat_stream`` boundary that can be held
mid-part; the documents are made here (doc_fixtures.py) and read by the
real worker process. What matters most:

* a long summary is read part by part, each part's notes saved as it
  finishes, then combined, and only real pages are cited;
* progress is streamed, and the job carries on when the reader leaves;
* stop keeps what was read ("Summary of pages 1-8 (stopped)"), carry on
  reads only the rest, and a job a restart lost carries on from its next
  part when the chat is opened;
* a voice turn is waited for between parts: it never waits more than the
  one part being read;
* the household's AI switch stops a job and refuses a new one;
* a phone's private job leaves nothing behind: no rows, no files, and its
  text goes when the phone deletes it, at 24 hours, or at a restart.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import web.backend.api.chat as chat_api
import web.backend.api.chat_doc_jobs as jobs_api
import web.backend.api.chat_documents as docs_api
from domovoi.doc_qa import answer as qa
from domovoi.doc_qa import longdoc
from domovoi.tests import doc_fixtures as fx
from domovoi.tests.conftest import requires_db
from web.backend.db import session_scope
from web.backend.main import app

#: The real voice signal, before the autouse fixture replaces it.
REAL_VOICE_BUSY = jobs_api.voice_busy

#: 12 pages of about 3,000 characters: 3 parts of 4 pages each.
PAGES = 12


def _page(n: int) -> str:
    lines = [f"Clause {n}.{k}: the tenant of flat {n} keeps item {k * n} in good order and reports any "
             f"fault with item {k} within {k + n} days of finding it." for k in range(1, 28)]
    return " ".join(lines)


def _pdf(n: int = PAGES) -> bytes:
    return fx.make_pdf([fx.text_page(_page(i)) for i in range(1, n + 1)])


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


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


def _q(c, sql: str, params: dict[str, Any] | None = None, write: bool = False) -> list[dict[str, Any]]:
    """A query on the test database, run on the client's event loop."""
    async def run():
        async with session_scope() as s:
            res = await s.execute(text(sql), params or {})
            rows = [dict(r) for r in res.mappings()] if res.returns_rows else []
            if write:
                await s.commit()
            return rows
    return c.portal.call(run)


class FakeModel:
    """Writes notes naming the first passage's label (plus a made-up page
    the app must drop); the reduce cites pages 1 and 5 and a made-up 99.
    ``hold`` (a threading.Event) set: each call waits until it is cleared.
    ``seconds``: how long each call takes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.hold = threading.Event()
        self.seconds = 0.0
        #: Calls before this one go through even while ``hold`` is set.
        self.hold_from = 0
        self.started = threading.Event()
        self.lock = threading.Lock()

    def kind(self, messages) -> str:
        system = messages[0]["content"]
        if longdoc.MAP_RULES in system:
            return "map"
        if longdoc.REDUCE_RULES in system:
            return "reduce"
        return "summary"

    def reply(self, messages) -> str:
        kind, user = self.kind(messages), messages[1]["content"]
        if kind == "map":
            block = user.split("<<<", 1)[1]
            m = re.search(r"\[(p\. \d+)\]", block) or re.search(r"\[([^\]]+)\]", block)
            label = m.group(1)
            return f"- The part starting at {label} covers repairs [{label}]\n- A pool on the roof [p. 99]"
        return "- Tenants report faults quickly [p. 1]\n- Items are kept in order [p. 5]\n- Free parking [p. 99]"

    async def stream(self, messages, model, stats=None, **kw):
        with self.lock:
            self.calls.append({"kind": self.kind(messages), "messages": messages, "t": time.monotonic(),
                               "num_predict": kw.get("num_predict"), "model": model})
        self.started.set()
        t0 = time.monotonic()
        index = len(self.calls) - 1
        while self.hold.is_set() and index >= self.hold_from:
            await asyncio.sleep(0.01)
        if self.seconds:
            await asyncio.sleep(self.seconds)
        text_ = self.reply(messages)
        if stats is not None:
            took = max(time.monotonic() - t0, 0.001)
            stats.update({"prompt_eval_count": len(messages[1]["content"]) // 4, "eval_count": 40,
                          "prompt_eval_duration": int(took * 0.7e9), "eval_duration": int(took * 0.3e9),
                          "done_reason": "stop"})
        for i in range(0, len(text_), 9):
            yield text_[i:i + 9]


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(docs_api, "DOCS_DIR", tmp_path / "documents")
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", tmp_path / "chat_uploads")
    monkeypatch.setattr(jobs_api, "JOBS_DIR", tmp_path / "doc-jobs")
    jobs_api._JOBS.clear()
    jobs_api._private_stats.clear()
    jobs_api._starting.clear()
    yield tmp_path
    jobs_api._JOBS.clear()
    jobs_api._starting.clear()

@pytest.fixture
def model(monkeypatch):
    m = FakeModel()
    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", m.stream)
    return m


@pytest.fixture(autouse=True)
def voice(monkeypatch):
    """The core's voice signal, as a switch the test flips."""
    class _V:
        busy = False
        asked = 0

    async def _busy():
        _V.asked += 1
        return _V.busy

    monkeypatch.setattr(jobs_api, "voice_busy", _busy)
    monkeypatch.setattr(jobs_api, "VOICE_POLL_SEC", 0.02)
    return _V


def _thread_with_long_pdf(c, n: int = PAGES) -> tuple[int, str]:
    r = c.post("/api/chat/documents", files={"file": ("lease.pdf", io.BytesIO(_pdf(n)), "application/pdf")})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    tid = c.post("/api/chat/threads", json={}).json()["id"]
    return tid, tok


def _wait(pred, timeout: float = 30.0, step: float = 0.05):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("timed out")


class Bg:
    """A request run in a thread: the test client hands back a stream only
    once it has ended, so a test that acts while a job runs sends it here."""

    def __init__(self, fn) -> None:
        self.out = None
        self.t = threading.Thread(target=self._run, args=(fn,), daemon=True)
        self.t.start()

    def _run(self, fn) -> None:
        self.out = fn()

    def join(self, timeout: float = 30.0):
        self.t.join(timeout)
        assert not self.t.is_alive(), "the request never ended"
        return self.out


def _send_bg(c, tid: int, tok: str, content: str = "summarize") -> Bg:
    return Bg(lambda: c.post(f"/api/chat/threads/{tid}/messages",
                             json={"content": content, "documents": [{"token": tok}]}))


def _the_job() -> "jobs_api.Job":
    return _wait(lambda: next(iter(jobs_api._JOBS.values()), None))


def _assistant_rows(c, tid: int) -> list[dict[str, Any]]:
    return _q(c, "SELECT id, content, doc, stats, error FROM chat_messages WHERE thread_id = :t "
                 "AND role = 'assistant' ORDER BY id", {"t": tid})


def _job_row(c, job_id: str) -> dict[str, Any] | None:
    rows = _q(c, "SELECT * FROM chat_doc_jobs WHERE id = :id", {"id": job_id})
    return rows[0] if rows else None


def _parts(c, tok: str) -> list[dict[str, Any]]:
    return c.get(f"/api/chat/documents/{tok}/parts").json()["parts"]


# ─── Map/reduce in a home-server chat ───────────────────────────────────
@requires_db
def test_a_long_summary_is_read_in_parts_then_combined(model):
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        chunks = longdoc.plan_chunks([{"name": "lease.pdf", "parts": _parts(c, tok)}])
        assert len(chunks) == 3
        r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "Summarize this", "documents": [{"token": tok}]})
        assert r.status_code == 200, r.text
        events = _sse(r.text)
        assert _job_row(c, events[0][1]["id"]) is None          # done: the reply has it now
    kinds = [e for e, _ in events]
    assert kinds[0] == "job" and kinds[-1] == "done" and "progress" in kinds
    job = events[0][1]
    assert job["parts"] == 3 and job["state"] == "running" and len(job["id"]) == 32
    lines = [d["line"] for e, d in events if e == "progress"]
    assert any(ln.startswith("reading part 2 of 3") for ln in lines)
    assert any(ln.startswith("writing the summary") for ln in lines)
    assert [x["kind"] for x in model.calls] == ["map", "map", "map", "reduce"]
    # Each part sees only its own pages, in the marked block, capped short.
    first = model.calls[0]["messages"][1]["content"]
    assert "[p. 1]" in first and "[p. 4]" in first and "[p. 5]" not in first and "<<<PASSAGES" in first
    assert model.calls[0]["num_predict"] == longdoc.MAP_NUM_PREDICT
    # The reduce reads the parts' checked notes: the made-up page is gone.
    reduce_in = model.calls[3]["messages"][1]["content"]
    assert "[p. 1]" in reduce_in and "[p. 5]" in reduce_in and "[p. 9]" in reduce_in
    assert "p. 99" not in reduce_in and "pool on the roof" not in reduce_in
    done = events[-1][1]
    assert done["role"] == "assistant" and "[p. 99]" not in done["content"] and "[p. 5]" in done["content"]
    assert "Free parking" not in done["content"]          # a point with only a made-up page goes
    assert done["doc"]["mode"] == "summary" and done["doc"]["found"]
    assert [s["label"] for s in done["doc"]["sources"]] == ["p. 1", "p. 5"]
    assert all(s["cited"] and s["token"] == tok for s in done["doc"]["sources"])
    assert {"code": "read_in_parts", "text": "Read in 3 parts. Points that span parts may be missed."} \
        in done["doc"]["notes"]
    assert done["doc"]["job"]["parts"] == 3 and done["doc"]["job"]["read"] == 3
    assert done["stats"]["model_role"] == "documents" and done["stats"]["steps"] == 4
    assert done["stats"]["prompt_chars"] > 30000 and done["stats"]["wall_ms"] > 0


@requires_db
def test_the_job_carries_on_when_the_reader_leaves(model):
    """The stream is only a window on the job: closing it (the page left,
    the phone locked) leaves the job reading, and its reply is written."""
    model.hold.set()
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        docs = [{"token": tok, "name": "lease.pdf", "parts": docs_api.load_cache(tok)["parts"]}]

        async def read_one_event_and_leave():
            job = await jobs_api.start_thread_job(tid, None, "summarize", docs)
            gen = jobs_api.thread_job_events(job)
            first = await gen.__anext__()
            await gen.aclose()
            return job.id, first, len(job.listeners)

        job_id, first, listening = c.portal.call(read_one_event_and_leave)
        assert first.startswith("event: job") and listening == 0
        _wait(lambda: model.calls)
        model.hold.clear()
        rows = _wait(lambda: _assistant_rows(c, tid))
        assert rows[0]["doc"]["job"]["id"] == job_id and rows[0]["doc"]["job"]["read"] == 3
        listed = c.get(f"/api/chat/threads/{tid}/messages").json()
        assert listed["jobs"] == [] and listed["messages"][-1]["doc"]["job"]["id"] == job_id
    assert [x["kind"] for x in model.calls] == ["map", "map", "map", "reduce"]


@requires_db
def test_each_part_s_notes_are_saved_and_progress_shows_in_the_message_list(model):
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        model.hold.set()
        bg = _send_bg(c, tid, tok)
        job = _the_job()
        _wait(lambda: model.calls)
        jobs = c.get(f"/api/chat/threads/{tid}/messages").json()["jobs"]
        assert len(jobs) == 1 and jobs[0]["state"] == "running" and jobs[0]["line"].startswith("reading part 1 of 3")
        row = _job_row(c, job.id)
        assert row["state"] == "running" and row["notes"] == [None, None, None]
        assert c.get(f"/api/chat/doc-jobs/{job.id}").json()["part"] == 1
        # Part 1 through, part 2 held: part 1's notes are in the row.
        model.hold_from = 1
        _wait(lambda: len(model.calls) >= 2)
        row = _wait(lambda: (lambda r: r if r["notes"][0] else None)(_job_row(c, job.id)))
        assert "[p. 1]" in row["notes"][0] and "p. 99" not in row["notes"][0] and row["notes"][1] is None
        assert row["stats"]["steps"] == 1 and row["stats"]["prompt_chars"] > 10000
        model.hold.clear()
        r = bg.join()
        assert [e for e, _ in _sse(r.text)][-1] == "done"
        assert _job_row(c, job.id) is None


@requires_db
def test_stop_keeps_the_partial_notes_and_carry_on_reads_only_the_rest(model):
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        # Let part 1 through, hold part 2.
        model.hold_from = 1
        model.hold.set()
        bg = _send_bg(c, tid, tok)
        job = _the_job()
        _wait(lambda: len(model.calls) >= 2)
        stopped = c.post(f"/api/chat/doc-jobs/{job.id}/stop").json()
        model.hold.clear()
        events = _sse(bg.join().text)
        assert stopped["state"] == "stopped" and stopped["read"] == 1 and stopped["covers"] == "pages 1-4"
        assert [e for e, _ in events][-1] == "done"
        rows = _assistant_rows(c, tid)
        assert len(rows) == 1 and events[-1][1]["id"] == rows[0]["id"]
        reply = rows[0]
        assert reply["content"].startswith("**Summary of pages 1-4 (stopped)**")
        assert "[p. 1]" in reply["content"] and "p. 99" not in reply["content"]
        assert reply["doc"]["job"]["state"] == "stopped"
        assert any(n["code"] == "stopped" and "part 1 of 3" in n["text"] for n in reply["doc"]["notes"])
        row = _job_row(c, job.id)
        assert row["state"] == "stopped" and row["notes"][0] and row["notes"][1:] == [None, None]
        listed = c.get(f"/api/chat/threads/{tid}/messages").json()["jobs"]
        assert listed[0]["state"] == "stopped" and listed[0]["read"] == 1
        # Carry on: parts 2 and 3, then the reduce; part 1 isn't read again.
        model.hold_from = 0
        before = len(model.calls)
        r = c.post(f"/api/chat/doc-jobs/{job.id}/resume")
        assert r.status_code == 200 and r.json()["state"] == "running"
        _wait(lambda: len(_assistant_rows(c, tid)) == 2)
        again = [x["kind"] for x in model.calls[before:]]
        assert again == ["map", "map", "reduce"]
        assert "[p. 5]" in model.calls[before]["messages"][1]["content"]
        _wait(lambda: _job_row(c, job.id) is None)
        final = _assistant_rows(c, tid)[-1]
        assert final["doc"]["job"]["read"] == 3 and "stopped" not in final["content"]
        assert c.post(f"/api/chat/doc-jobs/{job.id}/resume").status_code in (404, 409)


@requires_db
def test_a_job_a_restart_lost_is_interrupted_and_carries_on_when_asked_with_its_model(model):
    """Review round 1: viewing the chat doesn't restart a lost job; it says
    "interrupted — carry on?", and carrying on keeps the model the message
    named."""
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        model.hold.set()
        bg = Bg(lambda: c.post(f"/api/chat/threads/{tid}/messages",
                               json={"content": "summarize", "documents": [{"token": tok}], "model": "llama3.2:3b-big"}))
        job = _the_job()
        _wait(lambda: model.calls)
        # The process "restarts": its jobs are gone; the row says part 1 was read.
        c.portal.call(jobs_api.shutdown)
        bg.join()
        _q(c, "UPDATE chat_doc_jobs SET notes = CAST(:n AS JSONB) WHERE id = :id",
           {"id": job.id, "n": json.dumps(["- Saved notes [p. 2]", None, None])}, write=True)
        model.hold.clear()
        model.calls.clear()
        for _ in range(2):
            jobs = c.get(f"/api/chat/threads/{tid}/messages").json()["jobs"]
            assert jobs[0]["id"] == job.id and jobs[0]["read"] == 1 and jobs[0]["state"] == "interrupted"
            assert jobs[0]["line"] == "this summary was interrupted — carry on?"
        assert c.get(f"/api/chat/doc-jobs/{job.id}").json()["state"] == "interrupted"
        time.sleep(0.3)
        assert model.calls == [] and _assistant_rows(c, tid) == []      # nothing restarted it
        assert c.post(f"/api/chat/doc-jobs/{job.id}/resume").json()["state"] == "running"
        _wait(lambda: _assistant_rows(c, tid))
    assert [x["kind"] for x in model.calls] == ["map", "map", "reduce"]
    assert {x["model"] for x in model.calls} == {"llama3.2:3b-big"}
    assert "Saved notes [p. 2]" in model.calls[-1]["messages"][1]["content"]


@requires_db
def test_an_interrupted_job_with_ai_off_says_stopped(model, monkeypatch):
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        model.hold.set()
        bg = _send_bg(c, tid, tok)
        _the_job()
        _wait(lambda: model.calls)
        c.portal.call(jobs_api.shutdown)
        bg.join()
        model.hold.clear()
        monkeypatch.setattr(docs_api, "ai_turned_off", lambda: True)
        j = c.get(f"/api/chat/threads/{tid}/messages").json()["jobs"][0]
        assert j["state"] == "stopped" and j["line"] == "stopped (AI is off)"


@requires_db
def test_deleting_the_chat_stops_its_job(model):
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        model.hold.set()
        bg = _send_bg(c, tid, tok)
        job = _the_job()
        _wait(lambda: model.calls)
        assert c.delete(f"/api/chat/threads/{tid}").status_code == 200
        _wait(lambda: job.task.done())
        model.hold.clear()
        bg.join()
        assert _job_row(c, job.id) is None and job.id not in jobs_api._JOBS
    assert len(model.calls) == 1


# ─── Voice turns come first ─────────────────────────────────────────────
@requires_db
def test_a_voice_turn_during_a_job_waits_at_most_one_part(model, voice):
    """The job asks before every part; a voice turn that starts while part
    1 is being read holds part 2 back until it ends. The model is never
    asked for a part while the turn is in progress, so the turn's own
    model call waits at most for the one part already being read."""
    model.seconds = 0.3
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        bg = _send_bg(c, tid, tok)
        job = _the_job()
        _wait(lambda: model.calls, step=0.002)
        voice.busy = True               # a question in the kitchen, mid part 1
        turn_started = time.monotonic()
        # The part in flight finishes; then the job waits for the turn.
        _wait(lambda: job.waiting, timeout=15)
        assert job.view()["line"].startswith("waiting for a voice question")
        time.sleep(0.5)                 # the turn goes on a while longer
        calls_during_turn = [x for x in model.calls if x["t"] > turn_started]
        voice.busy = False
        turn_ended = time.monotonic()
        events = _sse(bg.join().text)
    assert calls_during_turn == []
    part1_ends = model.calls[0]["t"] + model.seconds
    # The part being read when the turn began finished within its own time.
    assert part1_ends - turn_started <= model.seconds + 0.05
    assert model.calls[1]["t"] >= turn_ended - 0.05
    done = [d for e, d in events if e == "done"][0]
    assert done["stats"]["voice_waits"] == 1 and done["stats"]["voice_wait_ms"] > 0
    assert any(d.get("waiting_for_voice") for e, d in events if e == "progress")


@pytest.mark.asyncio
async def test_the_core_signal_marks_a_turn_for_its_whole_length():
    from domovoi import voice_activity as va

    va._reset_for_tests()
    assert va.snapshot()["busy"] is False and va.snapshot()["since_last_ms"] is None
    async with va.turn("satellite"):
        async with va.turn("phone"):
            s = va.snapshot()
            assert s["busy"] and s["turns"] == 2 and s["oldest_ms"] >= 0
        assert va.busy()
    assert not va.busy() and va.snapshot()["since_last_ms"] >= 0
    with pytest.raises(RuntimeError):
        async with va.turn():
            raise RuntimeError("a turn that fails still ends")
    assert not va.busy()
    # A turn left open by a bug stops counting after MAX_TURN_SEC.
    va._open[999] = ("stuck", time.monotonic() - va.MAX_TURN_SEC - 1)
    assert not va.busy()
    va._reset_for_tests()


@requires_db
def test_the_core_answers_voice_activity_and_an_intent_counts_as_a_turn(monkeypatch):
    from domovoi import main as core_main
    from domovoi import voice_activity as va
    from domovoi.models import Response

    va._reset_for_tests()
    seen: list[bool] = []

    async def _route(intent, ctx, s):
        seen.append(va.busy())
        return Response(text="ok", session_id=None)

    monkeypatch.setattr(core_main, "route", _route)
    with TestClient(core_main.app, headers={"X-Requested-With": "domovoi-tests"}) as c:
        r = c.get("/v1/voice/activity")
        # {busy} and nothing else over the device tier (review round 1).
        assert r.status_code == 200 and r.json() == {"busy": False}
        c.post("/v1/intent", json={"transcript": "what time is it", "room_id": "kitchen"})
    assert seen == [True] and not va.busy()


def test_a_core_that_cant_be_asked_counts_as_not_busy(monkeypatch):
    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:9")
    assert asyncio.run(REAL_VOICE_BUSY()) is False


# ─── The household's AI switch ──────────────────────────────────────────
@requires_db
def test_turning_ai_off_stops_a_job_at_the_next_part_and_refuses_new_ones(model, monkeypatch):
    off = {"v": False}
    monkeypatch.setattr(docs_api, "ai_turned_off", lambda: off["v"])
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        model.seconds = 0.2
        bg = _send_bg(c, tid, tok)
        _wait(lambda: model.calls, step=0.002)
        off["v"] = True
        done = [d for e, d in _sse(bg.join().text) if e == "done"][0]
        assert len(model.calls) == 1
        assert done["content"].startswith("**Summary of pages 1-4 (stopped)**")
        assert any(n["code"] == "ai_off" for n in done["doc"]["notes"])
        job_id = done["doc"]["job"]["id"]
        assert c.post(f"/api/chat/doc-jobs/{job_id}/resume").status_code == 409
        r2 = c.post("/api/chat/doc-jobs", files={"file": ("x.pdf", io.BytesIO(_pdf(2)), "application/pdf")},
                    data={"question": "summarize"})
        assert r2.status_code == 409
        r3 = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "summarize again"})
        assert r3.status_code == 409
        jobs = c.get(f"/api/chat/threads/{tid}/messages").json()["jobs"]
        assert jobs[0]["state"] == "stopped"          # not restarted while AI is off
    assert len(model.calls) == 1


# ─── Estimates from measured speeds ─────────────────────────────────────
@requires_db
def test_the_estimate_comes_from_measured_speeds_and_the_speed_route_says_it(model):
    with _client() as c:
        s0 = c.get("/api/chat/doc-jobs/speed?chars=40000").json()
        assert s0["ai"] is True and s0["chunk_tokens"] == longdoc.MAP_CHUNK_TOKENS and s0["parts"] == 3
        tid, tok = _thread_with_long_pdf(c)
        model_name = chat_api.core_settings.ollama_model
        _q(c, "DELETE FROM chat_messages WHERE model = :m AND role = 'assistant'", {"m": model_name}, write=True)
        assert c.get("/api/chat/doc-jobs/speed?chars=40000").json()["estimate_s"] is None
        _q(c, "INSERT INTO chat_messages (thread_id, role, content, model, stats) VALUES "
              "(:t, 'assistant', 'x', :m, CAST(:s AS JSONB))",
           {"t": tid, "m": model_name,
            "s": json.dumps({"prompt_tokens": 3000, "prompt_ms": 30000, "output_tokens": 150,
                             "generate_ms": 15000, "prompt_chars": 12000})}, write=True)
        s1 = c.get("/api/chat/doc-jobs/speed?chars=40000").json()
        assert s1["speed"]["prefill_tps"] == 100.0 and s1["speed"]["decode_tps"] == 10.0
        expect = longdoc.estimate_seconds(longdoc.even_chunks(40000), longdoc.Speed(100.0, 10.0, 4.0))
        assert s1["estimate_s"] == pytest.approx(expect, abs=0.1)
        # A job this slow gets the heads-up first (documents plan D3,
        # test_chat_heads_up.py); this device said not to warn about it.
        r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "summarize", "documents": [{"token": tok}],
                                                              "heads_up": {"skip": ["long"]}})
        job = _sse(r.text)[0][1]
        assert job["estimate_s"] is not None and job["estimate_s"] > 3 * 30
        assert "left" in job["line"]
        # The slow speed stays out of the tests after this one (a job that
        # slow gets the heads-up card first).
        _q(c, "DELETE FROM chat_messages WHERE model = :m AND role = 'assistant'", {"m": model_name}, write=True)


# ─── A phone's private job ──────────────────────────────────────────────
def _counts(c) -> dict[str, int]:
    return {t: _q(c, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
            for t in ("chat_messages", "chat_doc_jobs", "chat_threads")}


@requires_db
def test_a_private_job_streams_its_summary_and_leaves_nothing_behind(model, dirs):
    seen_files: list[list[str]] = []
    orig = jobs_api.runner.extract

    async def spy(path, kind, **kw):
        seen_files.append([p.name for p in path.parent.iterdir()])
        return await orig(path, kind, **kw)

    jobs_api.runner.extract = spy
    model.seconds = 0.05
    try:
        with _client() as c:
            before = _counts(c)
            r = c.post("/api/chat/doc-jobs", files={"file": ("lease.pdf", io.BytesIO(_pdf()), "application/pdf")},
                       data={"question": "summarize the lease"})
            assert r.status_code == 200, r.text
            events = _sse(r.text)
            # The upload was there while it was read, and went straight after.
            assert seen_files == [["input.pdf"]]
            assert not (dirs / "doc-jobs").exists() or list((dirs / "doc-jobs").iterdir()) == []
            kinds = [e for e, _ in events]
            assert kinds[0] == "job" and kinds[-1] == "done" and "progress" in kinds
            done = events[-1][1]
            assert done["private"] and done["state"] == "done" and done["thread_id"] is None
            res = done["result"]
            assert "[p. 5]" in res["content"] and "[p. 99]" not in res["content"]
            assert [s["label"] for s in res["doc"]["sources"]] == ["p. 1", "p. 5"]
            assert res["doc"]["sources"][0]["token"] == "" and "message" not in res
            assert _counts(c) == before
            # The job ended: its document's text went with it (only the
            # summary waits for the phone), and the view still says what
            # it covered.
            assert all(d["parts"] == [] for d in jobs_api._JOBS[done["id"]].docs)
            assert done["covers"] == "the whole document"
            # The phone came back for it, then deleted it: its text goes.
            got = c.get(f"/api/chat/doc-jobs/{done['id']}").json()
            assert got["result"]["content"] == res["content"]
            assert c.delete(f"/api/chat/doc-jobs/{done['id']}").status_code == 200
            assert done["id"] not in jobs_api._JOBS
            assert c.get(f"/api/chat/doc-jobs/{done['id']}").status_code == 404
            assert c.delete(f"/api/chat/doc-jobs/{done['id']}").status_code == 404
            # A speed measured by a private job is kept in memory only.
            assert jobs_api._private_stats and _counts(c) == before
    finally:
        jobs_api.runner.extract = orig
    assert [x["kind"] for x in model.calls] == ["map", "map", "map", "reduce"]


@requires_db
def test_a_private_job_the_phone_left_keeps_its_summary_for_the_phone(model):
    """The phone locked mid-job: the job finishes, and the summary waits
    for the phone (in memory) until it comes back for it."""
    model.hold.set()
    with _client() as c:
        bg = Bg(lambda: c.post("/api/chat/doc-jobs", files={"file": ("lease.pdf", io.BytesIO(_pdf()),
                                                                     "application/pdf")}, data={"question": "summarize"}))
        job = _the_job()
        model.hold.clear()
        bg.join()
        v = _wait(lambda: (lambda x: x if x["state"] == "done" else None)(c.get(f"/api/chat/doc-jobs/{job.id}").json()))
        assert v["result"]["content"] and v["private"]
        events = _sse(c.get(f"/api/chat/doc-jobs/{job.id}/events").text)
        assert [e for e, _ in events] == ["job", "done"]


@requires_db
def test_a_private_job_can_be_stopped_and_is_forgotten_after_a_day(model, dirs):
    with _client() as c:
        model.hold.set()
        bg = Bg(lambda: c.post("/api/chat/doc-jobs", files={"file": ("lease.pdf", io.BytesIO(_pdf()),
                                                                     "application/pdf")}, data={"question": "summarize"}))
        job = _the_job()
        _wait(lambda: model.calls)
        stopped = c.post(f"/api/chat/doc-jobs/{job.id}/stop").json()
        model.hold.clear()
        bg.join()
        assert stopped["state"] == "stopped" and stopped["read"] == 0
        assert stopped["result"]["content"].startswith("Stopped before Domovoi had read the first part")
        assert c.post(f"/api/chat/doc-jobs/{job.id}/resume").status_code == 409
        assert jobs_api.sweep(now=job.created + jobs_api.PRIVATE_TTL_SEC + 1) >= 1
        assert job.id not in jobs_api._JOBS
        assert c.get(f"/api/chat/doc-jobs/{job.id}").status_code == 404


def test_a_restart_removes_a_private_job_s_leftover_upload(dirs):
    left = dirs / "doc-jobs" / ("a" * 32)
    left.mkdir(parents=True)
    (left / "input.pdf").write_bytes(b"%PDF-")
    jobs_api.boot()
    assert not left.exists()


def test_the_sweep_spares_an_upload_still_being_read(dirs):
    """Another phone's upload, mid-read, isn't anyone's job yet: a second
    phone's start (which sweeps) must not take it from under the reader."""
    reading = dirs / "doc-jobs" / ("b" * 32)
    reading.mkdir(parents=True)
    (reading / "input.pdf").write_bytes(b"%PDF-")
    jobs_api._reading.add("b" * 32)
    try:
        jobs_api.sweep()
        assert reading.exists()
    finally:
        jobs_api._reading.discard("b" * 32)
    jobs_api.sweep()
    assert not reading.exists()


@requires_db
def test_a_second_summarize_follows_the_job_reading_and_the_house_reads_two_at_most(model):
    """Review round 1: one job a thread (six sends, one job, one summary),
    and at most MAX_RUNNING in the house (a third chat is told to wait)."""
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        model.hold.set()
        bgs = [_send_bg(c, tid, tok) for _ in range(6)]
        _wait(lambda: model.calls)
        time.sleep(0.5)
        live = [j for j in jobs_api._JOBS.values() if j.state in jobs_api.LIVE]
        assert len(live) == 1
        tid2, tok2 = _thread_with_long_pdf(c)
        bg2 = _send_bg(c, tid2, tok2)
        _wait(lambda: len([j for j in jobs_api._JOBS.values() if j.state in jobs_api.LIVE]) == 2)
        tid3, tok3 = _thread_with_long_pdf(c)
        r3 = c.post(f"/api/chat/threads/{tid3}/messages", json={"content": "summarize", "documents": [{"token": tok3}]})
        done3 = [d for e, d in _sse(r3.text) if e == "done"][0]
        assert done3["content"] == jobs_api.BUSY_TEXT
        r4 = c.post("/api/chat/doc-jobs", files={"file": ("x.pdf", io.BytesIO(_pdf(2)), "application/pdf")})
        assert r4.status_code == 429
        model.hold.clear()
        outs = [b.join(60) for b in bgs]
        bg2.join(60)
        dones = [[d for e, d in _sse(o.text) if e == "done"][0] for o in outs]
        assert len({d["id"] for d in dones}) == 1          # all six streams: the one reply
        assert len(_assistant_rows(c, tid)) == 1


@requires_db
def test_private_jobs_refuse_what_chat_can_t_read_and_too_many_at_once(model, dirs):
    with _client() as c:
        r = c.post("/api/chat/doc-jobs", files={"file": ("old.doc", io.BytesIO(b"x"), "application/msword")})
        assert r.status_code == 415 and r.json()["code"] == "old_format"
        r = c.post("/api/chat/doc-jobs", files={"file": ("bad.pdf", io.BytesIO(b"not a pdf"), "application/pdf")})
        assert r.status_code == 422 and r.json()["detail"]
        assert not (dirs / "doc-jobs").exists() or list((dirs / "doc-jobs").iterdir()) == []
        # Finished jobs don't count against the house's limit (review round 1).
        for i in range(jobs_api.MAX_RUNNING + 3):
            jobs_api._JOBS[f"{i:032x}"] = type("J", (), {"private": True, "created": time.time(), "task": None,
                                                        "state": "done", "listeners": set(),
                                                        "last_seen": time.time(), "thread_id": None})()
        assert jobs_api.running_count() == 0
        class _Busy:
            def done(self):
                return False
        for i in range(jobs_api.MAX_RUNNING):
            jobs_api._JOBS[f"{i + 100:032x}"] = type("J", (), {"private": True, "created": time.time(),
                                                              "task": _Busy(), "state": "running",
                                                              "listeners": {1}, "last_seen": time.time(),
                                                              "thread_id": None})()
        r = c.post("/api/chat/doc-jobs", files={"file": ("x.pdf", io.BytesIO(_pdf(2)), "application/pdf")})
        assert r.status_code == 429
        jobs_api._JOBS.clear()


@requires_db
def test_job_routes_check_the_id(model):
    with _client() as c:
        assert c.get("/api/chat/doc-jobs/not-an-id").status_code == 404
        assert c.get(f"/api/chat/doc-jobs/{'0' * 32}").status_code == 404
        assert c.post(f"/api/chat/doc-jobs/{'0' * 32}/stop").status_code == 404
        assert c.delete(f"/api/chat/doc-jobs/{'0' * 32}").status_code == 404


@requires_db
def test_a_document_that_fits_one_part_is_summarized_in_one_call(model):
    """Longer than one pass, shorter than one part: one call, no notes stage."""
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c, n=4)
        r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "summarize", "documents": [{"token": tok}]})
        done = [d for e, d in _sse(r.text) if e == "done"][0]
    assert [x["kind"] for x in model.calls] == ["summary"]
    assert qa.RULES_SUMMARY in model.calls[0]["messages"][0]["content"]
    assert done["doc"]["job"]["parts"] == 1
    assert not any(n["code"] == "read_in_parts" for n in done["doc"]["notes"])


# ─── Review round 1: regression tests ───────────────────────────────────
@requires_db
def test_an_expired_private_job_is_refused_and_forgotten_even_with_no_new_job(model):
    with _client() as c:
        r = c.post("/api/chat/doc-jobs", files={"file": ("lease.pdf", io.BytesIO(_pdf()), "application/pdf")},
                   data={"question": "summarize my medical letter"})
        assert r.status_code == 200
        job = next(j for j in jobs_api._JOBS.values() if j.private)
        job.created = time.time() - 3 * 24 * 3600
        assert c.get(f"/api/chat/doc-jobs/{job.id}").status_code == 404
        assert job.id not in jobs_api._JOBS and job.result is None and job.docs == []


@requires_db
def test_a_private_job_nobody_watches_goes_after_ten_minutes(model):
    model.hold.set()
    with _client() as c:
        bg = Bg(lambda: c.post("/api/chat/doc-jobs", files={"file": ("lease.pdf", io.BytesIO(_pdf()),
                                                                     "application/pdf")}, data={"question": "summarize"}))
        job = _the_job()
        _wait(lambda: model.calls)
        # The phone dropped: no listener; ten minutes pass.
        job.listeners.clear()
        job.last_seen = time.time() - jobs_api.UNATTACHED_TTL_SEC - 1
        assert jobs_api.sweep() >= 1
        assert job.id not in jobs_api._JOBS
        _wait(lambda: job.task.done())
        model.hold.clear()
        bg.join()


@pytest.mark.asyncio
async def test_the_sweep_runs_on_its_own(monkeypatch, dirs):
    """No request or new job needed: the web's sweeper forgets an expired
    private job (24 hours at most stays true)."""
    monkeypatch.setattr(jobs_api, "SWEEP_EVERY_SEC", 0.05)
    job = jobs_api.Job(job_id="c" * 32, docs=[{"name": "x", "parts": []}], chunks=[], question="", private=True)
    job.created = time.time() - jobs_api.PRIVATE_TTL_SEC - 5
    jobs_api._JOBS[job.id] = job
    jobs_api.start_sweeper()
    try:
        for _ in range(100):
            if job.id not in jobs_api._JOBS:
                break
            await asyncio.sleep(0.02)
        assert job.id not in jobs_api._JOBS
    finally:
        await jobs_api.shutdown()


def test_the_sweep_takes_a_reads_temporary_folder_and_a_half_written_parts_file(dirs):
    import os
    import tempfile
    from pathlib import Path

    old = time.time() - jobs_api.TEMP_MAX_AGE_SEC - 60
    stale = Path(tempfile.mkdtemp(prefix="domovoi-docread-"))
    (stale / "result.json").write_text("{}", encoding="utf-8")
    os.utime(stale, (old, old))
    fresh = Path(tempfile.mkdtemp(prefix="domovoi-docread-"))
    docs_api.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    part = docs_api.DOCS_DIR / ("d" * 32 + "-parts.abcd1234.part")
    part.write_text("{", encoding="utf-8")
    os.utime(part, (old, old))
    try:
        jobs_api.boot()
        assert not stale.exists() and not part.exists() and fresh.exists()
    finally:
        import shutil
        shutil.rmtree(fresh, ignore_errors=True)
        shutil.rmtree(stale, ignore_errors=True)


@requires_db
def test_a_failed_private_job_gives_the_error_and_no_empty_summary(model, monkeypatch):
    async def boom(messages, model=None, stats=None, **kw):
        raise RuntimeError("model not found")
        yield ""  # pragma: no cover

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", boom)
    with _client() as c:
        r = c.post("/api/chat/doc-jobs", files={"file": ("lease.pdf", io.BytesIO(_pdf()), "application/pdf")},
                   data={"question": "summarize"})
        events = _sse(r.text)
    done = [d for e, d in events if e == "done"][0]
    assert done["state"] == "failed" and done["error"]["detail"] and "result" not in done
    assert [e for e, _ in events][-2:] == ["error", "done"]


@requires_db
def test_a_summary_keeps_only_what_the_parts_cited(model, monkeypatch):
    """Review round 1: the combine step can't add a claim (a line with no
    label, a page no part's notes cited), a part whose model cited nothing
    adds nothing and the summary says so, and a greeting isn't a note."""
    orig = model.reply

    def reply(messages):
        kind, user = model.kind(messages), messages[1]["content"]
        if kind == "map" and "[p. 5]" in user:
            return "Hello. I am Domovoi.\n- The tenant may keep a horse.\n- Rent is waived in July."
        if kind == "map":
            return "Hello! Here are the notes.\n" + orig(messages)
        return ("Here is your summary.\n- Tenants report faults quickly [p. 1]\n- A swimming pool [p. 7]\n"
                "- The landlord pays for free parking on the roof.\n- Item 9 is kept [p. 9]")

    monkeypatch.setattr(model, "reply", reply)
    with _client() as c:
        tid, tok = _thread_with_long_pdf(c)
        r = c.post(f"/api/chat/threads/{tid}/messages", json={"content": "summarize", "documents": [{"token": tok}]})
        done = [d for e, d in _sse(r.text) if e == "done"][0]
    reduce_in = model.calls[3]["messages"][1]["content"]
    assert "Hello" not in reduce_in and "horse" not in reduce_in and "July" not in reduce_in
    assert done["content"] == "- Tenants report faults quickly [p. 1]\n- Item 9 is kept [p. 9]"
    assert {"code": "nothing_cited",
            "text": "Domovoi found nothing it could cite in pages 5-8, so it isn't in this summary."} \
        in done["doc"]["notes"]
