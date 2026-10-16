"""A failed web search never logs the question (delta re-check, after S5):
httpx's HTTPStatusError text carries the request URL, and the user's
question is its ``q`` parameter. The log line says the status code only,
or the kind of error. DB-free, no network (httpx.MockTransport)."""

from __future__ import annotations

import logging

import httpx
import pytest

from domovoi.clients.searxng import RealSearxNGClient, SearchOutcome

QUESTION = "is my neighbour home tonight"


def _answer(monkeypatch, handler) -> None:
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


@pytest.mark.parametrize("response,said", [
    (httpx.Response(500, text="boom"), "HTTP 500"),
    (httpx.Response(429, text="slow down"), "HTTP 429"),
    (httpx.Response(200, text="<html>not json</html>"), "JSONDecodeError"),
])
async def test_a_failed_search_logs_the_status_not_the_question(monkeypatch, caplog, response, said) -> None:
    _answer(monkeypatch, lambda request: response)
    with caplog.at_level(logging.DEBUG, logger="domovoi.clients.searxng"):
        out = await RealSearxNGClient("http://127.0.0.1:6888").search_detailed(QUESTION)
    assert out == SearchOutcome([], "error")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert said in text
    for piece in ("neighbour", "neighbor", "tonight", "is+my", "q="):
        assert piece not in text, text
