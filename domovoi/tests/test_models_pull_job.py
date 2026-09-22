"""Unit tests for the Models page's background pull task —
``web/backend/api/models.py::_run_pull`` (F-011).

Ollama answers ``POST /api/pull`` with HTTP 200 and then reports a failure
as an error LINE on the stream (``{"error": "pull model manifest: file does
not exist"}`` for a model that isn't in the registry). The job runner used to
read only ``status`` off each line, so a failed pull ran off the end of the
stream and was recorded ``done / 100%``. These tests drive ``_run_pull``
directly with the stream, the installed list and the three ``model_jobs``
writers stubbed — no Ollama, no Postgres, no lifespan — so they can never
hide behind ``requires_db``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import web.backend.api.models as models_api
from domovoi.clients import ollama as ollama_client


@pytest.fixture
def job_writes(monkeypatch):
    """Capture every ``model_jobs`` write the task would make."""
    calls: list[tuple[str, Any]] = []

    async def _set_running(job_id):
        calls.append(("running", job_id))

    async def _update_progress(job_id, pct, status_text):
        calls.append(("progress", (pct, status_text)))

    async def _finish(job_id, status, pct=None, error=None):
        calls.append(("finish", {"status": status, "pct": pct, "error": error}))

    monkeypatch.setattr(models_api, "_set_running", _set_running)
    monkeypatch.setattr(models_api, "_update_progress", _update_progress)
    monkeypatch.setattr(models_api, "_finish", _finish)
    return calls


def _stream(lines: list[dict[str, Any]], monkeypatch) -> None:
    async def _pull(model, base_url=None, connect_timeout=10.0):
        for line in lines:
            yield line

    monkeypatch.setattr(ollama_client, "pull_model", _pull)


def _installed(names: list[str], monkeypatch) -> None:
    async def _list(base_url=None, timeout=5.0):
        return [{"name": n} for n in names]

    monkeypatch.setattr(ollama_client, "list_models", _list)


def _finish_of(calls) -> dict[str, Any]:
    finishes = [c[1] for c in calls if c[0] == "finish"]
    assert len(finishes) == 1, calls
    return finishes[0]


# ─── The F-011 case: an error line on a 200 stream ──────────────────────────


def test_an_error_line_on_the_stream_marks_the_job_failed(job_writes, monkeypatch):
    _stream(
        [
            {"status": "pulling manifest"},
            {"error": "pull model manifest: file does not exist"},
        ],
        monkeypatch,
    )
    # Even if the tags said it was there, the error line wins.
    _installed(["nope:1b"], monkeypatch)

    asyncio.run(models_api._run_pull(7, "nope:1b"))

    fin = _finish_of(job_writes)
    assert fin["status"] == "failed"
    assert fin["error"] == "pull model manifest: file does not exist"
    assert fin["pct"] is None
    # The manifest line was still recorded as progress before the failure.
    assert ("progress", (None, "pulling manifest")) in job_writes


def test_an_error_line_stops_reading_the_stream(job_writes, monkeypatch):
    """Nothing after the error is consumed or persisted — no stray 'success'
    from a malformed stream can flip the outcome back."""
    _stream(
        [
            {"error": "boom"},
            {"status": "success"},
        ],
        monkeypatch,
    )
    _installed(["x:latest"], monkeypatch)

    asyncio.run(models_api._run_pull(1, "x"))

    assert _finish_of(job_writes)["status"] == "failed"
    assert not any(c == ("progress", (None, "success")) for c in job_writes)


# ─── Happy path, and the post-stream installed-list confirmation ───────────


def test_a_clean_stream_with_the_model_installed_is_done(job_writes, monkeypatch):
    _stream(
        [
            {"status": "pulling manifest"},
            {"status": "downloading abc", "completed": 50, "total": 100},
            {"status": "downloading abc", "completed": 100, "total": 100},
            {"status": "verifying sha256 digest"},
            {"status": "success"},
        ],
        monkeypatch,
    )
    _installed(["llama3.2:3b", "qwen3:8b"], monkeypatch)

    asyncio.run(models_api._run_pull(3, "llama3.2:3b"))

    fin = _finish_of(job_writes)
    assert fin == {"status": "done", "pct": 100, "error": None}
    assert job_writes[0] == ("running", 3)
    assert ("progress", (50, "downloading abc")) in job_writes
    assert ("progress", (100, "downloading abc")) in job_writes


def test_a_clean_stream_but_model_absent_from_tags_is_failed(job_writes, monkeypatch):
    """Belt and braces: a stream that ends without an error line but leaves
    nothing on disk must not be recorded as done."""
    _stream([{"status": "pulling manifest"}], monkeypatch)
    _installed(["something-else:latest"], monkeypatch)

    asyncio.run(models_api._run_pull(4, "ghost:7b"))

    fin = _finish_of(job_writes)
    assert fin["status"] == "failed"
    assert "ghost:7b" in fin["error"]
    assert "installed list" in fin["error"]


def test_unreachable_tags_after_a_clean_stream_do_not_fail_the_job(job_writes, monkeypatch):
    """An empty tags list means Ollama couldn't be asked (list_models degrades
    to []) — can't verify, so a clean stream still counts, matching the
    set_active posture."""
    _stream([{"status": "success"}], monkeypatch)
    _installed([], monkeypatch)

    asyncio.run(models_api._run_pull(5, "llama3.2:3b"))

    assert _finish_of(job_writes)["status"] == "done"


def test_untagged_pull_name_matches_the_latest_tag(job_writes, monkeypatch):
    """'llama3.2' is listed by Ollama as 'llama3.2:latest'."""
    _stream([{"status": "success"}], monkeypatch)
    _installed(["llama3.2:latest"], monkeypatch)

    asyncio.run(models_api._run_pull(6, "llama3.2"))

    assert _finish_of(job_writes)["status"] == "done"


def test_normalize_model_name():
    n = models_api._normalize_model_name
    assert n("llama3.2") == "llama3.2:latest"
    assert n("llama3.2:3b") == "llama3.2:3b"
    assert n("Library/Llama3.2:3B") == "llama3.2:3b"
    assert n("registry.ollama.ai/library/qwen3:8b") == "qwen3:8b"
    assert n("hf.co/user/repo") == "hf.co/user/repo:latest"
    assert n("hf.co/user/repo:Q4") == "hf.co/user/repo:q4"


# ─── Transport / cancel paths keep their outcomes ───────────────────────────


def test_a_transport_error_is_failed_with_the_message(job_writes, monkeypatch):
    async def _pull(model, base_url=None, connect_timeout=10.0):
        raise ConnectionError("Ollama is down")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(ollama_client, "pull_model", _pull)
    _installed(["x:latest"], monkeypatch)

    asyncio.run(models_api._run_pull(8, "x"))

    fin = _finish_of(job_writes)
    assert fin["status"] == "failed"
    assert fin["error"] == "Ollama is down"


def test_a_cancel_flag_between_lines_marks_cancelled(job_writes, monkeypatch):
    async def _pull(model, base_url=None, connect_timeout=10.0):
        yield {"status": "pulling manifest"}
        models_api._cancelled.add(9)
        yield {"status": "downloading", "completed": 1, "total": 2}

    monkeypatch.setattr(ollama_client, "pull_model", _pull)
    _installed(["x:latest"], monkeypatch)

    asyncio.run(models_api._run_pull(9, "x"))

    assert _finish_of(job_writes)["status"] == "cancelled"
    assert 9 not in models_api._cancelled
    assert 9 not in models_api._pull_tasks
