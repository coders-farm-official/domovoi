"""Model-management client + catalog tests — DB-free.

Covers the new Ollama HTTP helpers in ``domovoi.clients.ollama``
(list/ps/delete/pull + pct math) against an in-process ``httpx.MockTransport``
so no real Ollama server is needed, plus a structural check of the shipped
curated catalog JSON. These don't touch Postgres and are safe under the
USE_STUBS suite (the DB-backed hardware/models API + JS fit math are exercised
by the integration run).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from domovoi.clients import ollama


def _patch_client(monkeypatch, handler):
    """Redirect ``httpx.AsyncClient(...)`` (constructed inside the helpers)
    onto a MockTransport that runs ``handler`` for every request."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real(transport=httpx.MockTransport(handler), timeout=None)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


# ─── pct math ────────────────────────────────────────────────────────────────


def test_pct_from_progress():
    assert ollama.pct_from_progress({"completed": 50, "total": 100}) == 50
    assert ollama.pct_from_progress({"completed": 0, "total": 100}) == 0
    assert ollama.pct_from_progress({"completed": 100, "total": 100}) == 100
    # No totals (manifest / verify phase) → None.
    assert ollama.pct_from_progress({"status": "pulling manifest"}) is None
    assert ollama.pct_from_progress({"completed": 5}) is None
    # Zero / bad totals never divide-by-zero.
    assert ollama.pct_from_progress({"completed": 5, "total": 0}) is None
    # Over-100 clamps.
    assert ollama.pct_from_progress({"completed": 200, "total": 100}) == 100


# ─── list_models / ps ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_models_parses_tags(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [
            {"name": "llama3.2:3b", "size": 2000000000,
             "details": {"quantization_level": "Q4_K_M"}},
        ]})

    _patch_client(monkeypatch, handler)
    models = await ollama.list_models(base_url="http://x")
    assert len(models) == 1
    assert models[0]["name"] == "llama3.2:3b"


@pytest.mark.asyncio
async def test_list_models_degrades_on_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    _patch_client(monkeypatch, handler)
    assert await ollama.list_models(base_url="http://x") == []


@pytest.mark.asyncio
async def test_ps_parses_loaded(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ps"
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:14b"}]})

    _patch_client(monkeypatch, handler)
    loaded = await ollama.ps(base_url="http://x")
    assert loaded[0]["name"] == "qwen2.5:14b"


# ─── delete ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_model_sends_name(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200)

    _patch_client(monkeypatch, handler)
    await ollama.delete_model("llama3.2:3b", base_url="http://x")
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/api/delete"
    assert seen["body"] == {"model": "llama3.2:3b"}


@pytest.mark.asyncio
async def test_delete_model_raises_on_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    _patch_client(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await ollama.delete_model("nope", base_url="http://x")


# ─── pull (streaming) ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pull_model_streams_chunks(monkeypatch):
    lines = [
        {"status": "pulling manifest"},
        {"status": "downloading", "completed": 25, "total": 100},
        {"status": "downloading", "completed": 100, "total": 100},
        {"status": "success"},
    ]
    body = "\n".join(json.dumps(x) for x in lines) + "\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pull"
        assert json.loads(request.content.decode())["model"] == "llama3.2:3b"
        return httpx.Response(200, content=body.encode())

    _patch_client(monkeypatch, handler)
    got = [chunk async for chunk in ollama.pull_model("llama3.2:3b", base_url="http://x")]
    assert [c["status"] for c in got] == [
        "pulling manifest", "downloading", "downloading", "success",
    ]
    assert ollama.pct_from_progress(got[1]) == 25


@pytest.mark.asyncio
async def test_reset_ollama_client_clears_singleton():
    # Under USE_STUBS this returns the stub; reset must clear the cache so a
    # later get rebuilds (the reapply hook for a live model switch).
    first = ollama.get_ollama_client()
    ollama.reset_ollama_client()
    second = ollama.get_ollama_client()
    # New instance after reset (identity differs).
    assert first is not second


# ─── catalog JSON ────────────────────────────────────────────────────────────


def test_catalog_json_valid_and_shaped():
    path = (
        Path(__file__).resolve().parents[2]
        / "web" / "backend" / "model_catalog.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data.get("ollama"), list) and data["ollama"]
    assert isinstance(data.get("whisper"), list) and data["whisper"]
    for m in data["ollama"]:
        assert m.get("name")
        assert m.get("role") in ("qa", "tool", "both", "embedding")
        assert isinstance(m.get("est_vram_gb"), (int, float))
        assert m.get("desc")
    for m in data["whisper"]:
        assert m.get("name")
        assert m.get("role") == "stt"
        assert isinstance(m.get("est_vram_gb"), (int, float))
