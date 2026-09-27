"""``GET /api/health`` passes the core's speech-recognition state through.

The core's ``/v1/health`` has said what speech recognition is running on
(``stt``: ok / fallback / unavailable / stub / not_loaded) since the
Whisper boot work; the web's health probe fetched that same URL and threw
the body away. Home's "needs attention" wants exactly that signal ("the
kitchen can't hear you right now") without the costly hardware probe, so
the web now forwards it as ``stt``.

It is a signal and never a cause: ``status`` stays about the database and
the core being reachable, because the core deliberately boots without STT
rather than not at all.

The core hop is replaced by a scripted client (the handler builds its own
``httpx.AsyncClient``); no core process, no ``requires_db``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from web.backend.main import app as web_app


class _Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _scripted_core(monkeypatch, answer) -> list[str]:
    """Replace the handler's client with one that answers ``answer`` (a
    ``_Resp``, or an exception to raise as if the core were down)."""
    seen: list[str] = []

    class _Client:
        def __init__(self, **_kw) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            seen.append(url)
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return seen


async def _health(monkeypatch, answer) -> tuple[dict, list[str]]:
    client = AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test")
    seen = _scripted_core(monkeypatch, answer)   # after the test's own client exists
    async with client:
        r = await client.get("/api/health")
    assert r.status_code == 200, r.text
    return r.json(), seen


@pytest.mark.parametrize("state", ["ok", "fallback", "unavailable", "not_loaded"])
@pytest.mark.asyncio
async def test_the_core_stt_state_is_passed_through(monkeypatch, state) -> None:
    body, seen = await _health(monkeypatch, _Resp(200, {"status": "ok", "stt": state}))
    assert seen and seen[0].endswith("/v1/health")
    assert body["domovoi_reachable"] is True
    assert body["stt"] == state


@pytest.mark.asyncio
async def test_stt_off_does_not_make_the_box_degraded(monkeypatch) -> None:
    body, _ = await _health(monkeypatch, _Resp(200, {"status": "ok", "stt": "unavailable"}))
    # `status` is the database AND the core: whatever the DB said, the core
    # half is reachable, and stt never enters into it.
    assert body["domovoi_reachable"] is True
    assert body["status"] == ("ok" if body["db_reachable"] else "degraded")


@pytest.mark.parametrize(
    "answer",
    [
        _Resp(503, {"detail": "db unreachable"}),     # core up, not healthy
        _Resp(200, {"status": "ok"}),                 # a core from before `stt`
        _Resp(200, ValueError("not json")),           # something else on the port
        _Resp(200, {"status": "ok", "stt": 3}),       # not a state
        httpx.ConnectError("refused"),                # core down
    ],
    ids=["core-503", "older-core", "not-json", "not-a-string", "core-down"],
)
@pytest.mark.asyncio
async def test_no_state_is_null_never_a_guess(monkeypatch, answer) -> None:
    body, _ = await _health(monkeypatch, answer)
    assert body["stt"] is None
    assert "stt" in body


@pytest.mark.asyncio
async def test_a_core_that_is_down_is_still_degraded(monkeypatch) -> None:
    body, _ = await _health(monkeypatch, httpx.ConnectError("refused"))
    assert body["domovoi_reachable"] is False
    assert body["status"] == "degraded"
