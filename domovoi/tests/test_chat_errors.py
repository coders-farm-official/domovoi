"""What the text chat says when the model can't answer — DB-free.

Owner report 2026-10-11: a picture in chat got, in red, "Client error '404
Not Found' for url 'http://localhost:11434/api/chat' For more information
check: https://developer.mozilla.org/..." — the vision model wasn't
installed, and the raw httpx exception reached the user. Now every failure
reads as a plain sentence with a code (web/backend/api/chat.py ``_failure``)
and the exception goes to the log only.

The Ollama error shapes below are the ones a real Ollama 0.34.2 sent on
2026-10-11 (a private server on this box): a missing model is ``404
{"error": "model 'qwen2.5vl:7b' not found"}``; an image handed to a
text-only model is ``400 {"error": {"code": 400, "message": "Multimodal
data provided, but model does not support multimodal requests.", ...}}``;
an unreachable server is httpx's ConnectError "All connection attempts
failed".
"""

from __future__ import annotations

import json

import httpx
import pytest

import web.backend.api.chat as chat_api
from domovoi.clients import ollama
from domovoi.config import settings

# Nothing a user reads may carry these: internal hosts and ports, URLs,
# library help links, exception class names, status lines.
LEAKS = ("localhost", "127.0.0.1", "11434", "http", "mozilla", "api/chat",
         "Error", "Exception", "404", "400", "500", "Client error")


def _assert_plain(text: str) -> None:
    assert text and text[0].isupper() and text.endswith(".")
    for leak in LEAKS:
        assert leak not in text, (leak, text)


_REAL_CLIENT = httpx.AsyncClient


def _patch_ollama(monkeypatch, handler) -> None:
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: _REAL_CLIENT(transport=httpx.MockTransport(handler), timeout=None))


async def _drain(model: str = "m") -> list[str]:
    return [d async for d in ollama.chat_stream([{"role": "user", "content": "hi"}], model=model)]


# ─── the client keeps Ollama's own words, typed ─────────────────────────────


async def test_a_missing_model_raises_with_ollamas_words(monkeypatch):
    _patch_ollama(monkeypatch, lambda r: httpx.Response(
        404, json={"error": "model 'qwen2.5vl:7b' not found"}))
    with pytest.raises(ollama.OllamaChatError) as e:
        await _drain("qwen2.5vl:7b")
    assert e.value.status == 404
    assert e.value.detail == "model 'qwen2.5vl:7b' not found"


async def test_a_nested_error_body_gives_its_message(monkeypatch):
    body = {"error": {"code": 400, "type": "invalid_request_error",
                      "message": "Multimodal data provided, but model does not support multimodal requests."}}
    _patch_ollama(monkeypatch, lambda r: httpx.Response(400, json=body))
    with pytest.raises(ollama.OllamaChatError) as e:
        await _drain()
    assert e.value.status == 400
    assert e.value.detail.startswith("Multimodal data provided")


async def test_an_error_line_mid_stream_raises_without_a_status(monkeypatch):
    lines = [json.dumps({"message": {"content": "Hel"}, "done": False}),
             json.dumps({"error": "model requires more system memory (9.1 GiB) than is available (4.0 GiB)"})]
    _patch_ollama(monkeypatch, lambda r: httpx.Response(200, content="\n".join(lines).encode()))
    got: list[str] = []
    with pytest.raises(ollama.OllamaChatError) as e:
        async for d in ollama.chat_stream([{"role": "user", "content": "hi"}], model="m"):
            got.append(d)
    assert got == ["Hel"]
    assert e.value.status is None and "memory" in e.value.detail


async def test_a_plain_text_error_body_is_kept_cut(monkeypatch):
    _patch_ollama(monkeypatch, lambda r: httpx.Response(502, content=b"bad gateway " * 100))
    with pytest.raises(ollama.OllamaChatError) as e:
        await _drain()
    assert e.value.status == 502 and len(e.value.detail) <= 500


# ─── what the chat makes of each ────────────────────────────────────────────


@pytest.mark.parametrize("exc,role,code", [
    (ollama.OllamaChatError(404, "model 'qwen2.5vl:7b' not found"), "vision", "vision_model_missing"),
    (ollama.OllamaChatError(404, "model 'llama3.2:3b' not found"), "chat", "model_missing"),
    (ollama.OllamaChatError(None, 'model "x" not found, try pulling it first'), "override", "model_missing"),
    (ollama.OllamaChatError(400, "Multimodal data provided, but model does not support multimodal requests."),
     "vision", "vision_unsupported"),
    (ollama.OllamaChatError(500, "this model is missing data required for image input"), "vision", "vision_unsupported"),
    (ollama.OllamaChatError(500, "model requires more system memory (9.1 GiB) than is available (4.0 GiB)"),
     "chat", "model_memory"),
    (ollama.OllamaChatError(500, "llama runner process has terminated: exit status 2"), "chat", "model_error"),
    (ollama.OllamaChatError(None, "unexpected EOF"), "chat", "model_error"),
    (httpx.ConnectError("All connection attempts failed"), "chat", "model_unreachable"),
    (httpx.ConnectTimeout("timed out connecting to http://localhost:11434"), "vision", "model_unreachable"),
    (httpx.ReadTimeout("read timed out"), "chat", "model_timeout"),
    (httpx.PoolTimeout("pool timed out"), "chat", "model_timeout"),
    (httpx.RemoteProtocolError("peer closed connection without sending complete message body"),
     "chat", "model_unreachable"),
    (httpx.ReadError("[WinError 10054] An existing connection was forcibly closed"), "chat", "model_unreachable"),
    (ConnectionRefusedError(10061, "refused"), "chat", "model_unreachable"),
    (httpx.HTTPStatusError(
        "Client error '404 Not Found' for url 'http://localhost:11434/api/chat'\nFor more information "
        "check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404",
        request=httpx.Request("POST", "http://localhost:11434/api/chat"),
        response=httpx.Response(404)), "chat", "model_error"),
    (RuntimeError("model exploded at http://127.0.0.1:11434"), "chat", "model_error"),
    (ValueError("anything else"), "vision", "model_error"),
])
def test_every_failure_reads_plainly(exc, role, code):
    got_code, text = chat_api._failure(exc, "qwen2.5vl:7b" if role == "vision" else "llama3.2:3b", role)
    assert got_code == code
    _assert_plain(text)


def test_the_vision_refusal_names_the_model_and_where_to_fix_it():
    code, text = chat_api._failure(ollama.OllamaChatError(404, "model 'qwen2.5vl:7b' not found"),
                                   "qwen2.5vl:7b", "vision")
    assert text == (
        "Pictures need the vision model qwen2.5vl:7b, which isn't installed on this Domovoi. "
        "An admin can install it in Settings → Models, or pick another vision model there."
    )
    assert text == chat_api.vision_missing_text("qwen2.5vl:7b")
    assert chat_api.vision_missing_text("") == (
        "Pictures need a vision model, and none is set on this Domovoi. "
        "An admin can pick one in Settings → Models."
    )


def test_a_missing_text_model_is_named_too():
    code, text = chat_api._failure(ollama.OllamaChatError(404, "not found"), "llama3.2:3b", "chat")
    assert code == "model_missing"
    assert "llama3.2:3b isn't installed" in text and "Settings → Models" in text


@pytest.mark.parametrize("raw,role,model,code", [
    # The owner's own row, as an older server kept it.
    ("Client error '404 Not Found' for url 'http://localhost:11434/api/chat'\nFor more information check: "
     "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404", "vision", "qwen2.5vl:7b",
     "vision_model_missing"),
    ("Client error '404 Not Found' for url 'http://localhost:11434/api/chat'", "chat", "llama3.2:3b",
     "model_missing"),
    ("All connection attempts failed", "chat", "m", "model_unreachable"),
    ("timed out", "chat", "m", "model_timeout"),
    ("the model returned nothing", "chat", "m", "empty_reply"),
    ("Server error '500 Internal Server Error' for url 'http://localhost:11434/api/chat'", "chat", "m",
     "model_error"),
])
def test_an_older_rows_raw_error_reads_plainly(raw, role, model, code):
    got_code, text = chat_api._legacy_failure(raw, model, role)
    assert got_code == code
    _assert_plain(text)


def test_an_older_row_is_scrubbed_on_read_and_a_new_one_is_left_alone(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(settings, "ollama_vision_model", "qwen2.5vl:7b")
    at = datetime(2026, 10, 11, tzinfo=timezone.utc)
    raw = "Client error '404 Not Found' for url 'http://localhost:11434/api/chat'"
    old = chat_api._msg_dict({"role": "assistant", "error": raw, "model": "qwen2.5vl:7b",
                              "created_at": at, "stats": None})
    assert old["error"] == chat_api.vision_missing_text("qwen2.5vl:7b")
    assert old["stats"] == {"error_code": "vision_model_missing"}
    new = chat_api._msg_dict({"role": "assistant", "error": "Plain words.", "model": "m",
                              "created_at": at, "stats": json.dumps({"error_code": "model_error"})})
    assert new["error"] == "Plain words." and new["stats"] == {"error_code": "model_error"}
    # A user row is never touched, nor a reply without an error.
    user = chat_api._msg_dict({"role": "user", "error": None, "created_at": at, "stats": None})
    assert user["stats"] is None


# ─── what's installed ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name,installed,expected", [
    ("qwen2.5vl:7b", [{"name": "llama3.2:3b"}, {"name": "qwen2.5vl:7b"}], True),
    ("qwen2.5vl:7b", [{"name": "llama3.2:3b"}, {"name": "qwen2.5:7b"}], False),
    ("llava", [{"model": "llava:latest"}], True),
    ("LLaVA:Latest", ["llava:latest"], True),
    ("", [{"name": "llava:latest"}], False),
    ("llava", [], False),
])
def test_model_installed(name, installed, expected):
    assert ollama.model_installed(name, installed) is expected


async def test_tags_or_none_tells_down_from_empty(monkeypatch):
    _patch_ollama(monkeypatch, lambda r: httpx.Response(200, json={"models": []}))
    assert await ollama.tags_or_none("http://x") == []

    def down(request):
        raise httpx.ConnectError("All connection attempts failed", request=request)

    _patch_ollama(monkeypatch, down)
    assert await ollama.tags_or_none("http://x") is None
    # list_models keeps its "empty when down" contract.
    assert await ollama.list_models("http://x") == []
