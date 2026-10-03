"""Honest replies when a web search never ran (fix: the misleading
"I checked online but couldn't find ..." while SearXNG isn't running).

Covers:

* ``RealSearxNGClient.search_detailed`` — every status, against an
  ``httpx.MockTransport`` (no network); under ``INTERNET_ACCESS=never``
  no request is made at all;
* the DoubleCheck replies for each status, for both the question path
  (proactive offer / auto-search) and the claim path ("is it true that"):
  "I checked online" is said only after a search really ran;
* the turned-off wording in double_check, the router's volatile-question
  reply and the news handler, and that the wording is unchanged when the
  internet question is unanswered.

DB-free except the one router test (``requires_db``).
"""

from __future__ import annotations

import logging

import httpx
import pytest

from domovoi import egress
from domovoi.clients.searxng import (
    RealSearxNGClient,
    SearchOutcome,
    SearchResult,
    SearxNGStubClient,
)
from domovoi.handlers import double_check as dc
from domovoi.handlers.double_check import DoubleCheckHandler
from domovoi.models import Context, Intent
from domovoi.tests.conftest import requires_db

CHECKED_ONLINE = "i checked online"


def _patch_transport(monkeypatch, handler) -> list[httpx.Request]:
    """Every httpx.AsyncClient the client builds talks to ``handler``."""
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def _recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_recording)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


def _json(results: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"results": results})


# ─── search_detailed: every status ───────────────────────────────────────


@pytest.mark.asyncio
async def test_search_detailed_ok(monkeypatch) -> None:
    seen = _patch_transport(monkeypatch, lambda r: _json([
        {"title": "Eiffel Tower", "url": "https://example.org/e", "content": "Paris", "engine": "x"},
        {"title": "", "url": "https://example.org/no-title"},
    ]))
    out = await RealSearxNGClient("http://127.0.0.1:6888/").search_detailed("eiffel tower")
    assert out.status == "ok"
    assert [r.title for r in out.results] == ["Eiffel Tower"]
    assert len(seen) == 1
    assert seen[0].url.host == "127.0.0.1"
    assert seen[0].url.params["q"] == "eiffel tower"


@pytest.mark.asyncio
async def test_search_detailed_no_results(monkeypatch) -> None:
    _patch_transport(monkeypatch, lambda r: _json([]))
    client = RealSearxNGClient("http://127.0.0.1:6888")
    out = await client.search_detailed("nothing at all")
    assert out == SearchOutcome([], "no_results")
    assert await client.search("nothing at all") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ConnectError, httpx.ConnectTimeout])
async def test_search_detailed_unreachable(monkeypatch, exc) -> None:
    def refuse(request):
        raise exc("All connection attempts failed", request=request)

    _patch_transport(monkeypatch, refuse)
    out = await RealSearxNGClient("http://127.0.0.1:6888").search_detailed("weather")
    assert out == SearchOutcome([], "unreachable")


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="<html>not json</html>"),
])
async def test_search_detailed_error(monkeypatch, response) -> None:
    _patch_transport(monkeypatch, lambda r: response)
    out = await RealSearxNGClient("http://127.0.0.1:6888").search_detailed("weather")
    assert out == SearchOutcome([], "error")


@pytest.mark.asyncio
async def test_search_detailed_internet_off_makes_no_request(monkeypatch) -> None:
    """SearXNG is on this box, but it forwards every query to public
    search engines: under never nothing is sent, not even to SearXNG."""
    def must_not_run(request):
        raise AssertionError(f"no request under never, got {request.url}")

    seen = _patch_transport(monkeypatch, must_not_run)
    client = RealSearxNGClient("http://127.0.0.1:6888")
    with egress.override_policy("never"):
        out = await client.search_detailed("weather")
        assert await client.search("weather") == []
    assert out == SearchOutcome([], "internet_off")
    assert seen == []


@pytest.mark.asyncio
async def test_search_detailed_empty_query_sends_nothing(monkeypatch) -> None:
    seen = _patch_transport(monkeypatch, lambda r: _json([]))
    out = await RealSearxNGClient("http://127.0.0.1:6888").search_detailed("   ")
    assert out.status == "no_results" and seen == []


@pytest.mark.asyncio
async def test_stub_reports_ok() -> None:
    out = await SearxNGStubClient().search_detailed("anything", max_results=2)
    assert out.status == "ok" and len(out.results) == 2
    assert len(await SearxNGStubClient().search("anything", max_results=3)) == 3


# ─── DoubleCheck replies per status ──────────────────────────────────────


class _Detailed:
    """A SearXNG client double that reports a fixed outcome."""

    def __init__(self, status: str, results: list[SearchResult] | None = None) -> None:
        self.outcome = SearchOutcome(results or [], status)
        self.base_url = "http://127.0.0.1:6888"

    async def search(self, query, max_results=5):
        return self.outcome.results

    async def search_detailed(self, query, max_results=5):
        return self.outcome


class _OldStyle:
    """A double with only ``search`` (the pre-2026-10 protocol)."""

    async def search(self, query, max_results=5):
        return []


_UNSEARCHED = {
    "unreachable": "I can't search the web right now — my search helper isn't running.",
    "error": "I couldn't reach my search helper just now. Try again in a moment.",
}


def _ctx() -> Context:
    return Context(room_id="kitchen", online=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unreachable", "error"])
async def test_question_path_never_claims_a_search_that_did_not_run(monkeypatch, status) -> None:
    monkeypatch.setattr(dc, "get_searxng_client", lambda: _Detailed(status))
    resp = await DoubleCheckHandler()._answer_question_from_web("weather tomorrow", _ctx(), None)
    assert resp.text == _UNSEARCHED[status]
    assert CHECKED_ONLINE not in resp.text.lower()
    assert resp.data["search_status"] == status


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["unreachable", "error"])
async def test_claim_path_never_claims_a_search_that_did_not_run(monkeypatch, status) -> None:
    monkeypatch.setattr(dc, "get_searxng_client", lambda: _Detailed(status))
    resp = await DoubleCheckHandler()._verify_claim_directly(
        "the eiffel tower is in paris", _ctx(), None,
    )
    assert resp.text == _UNSEARCHED[status]
    assert "couldn't find anything" not in resp.text.lower()
    assert "verdict" not in resp.data


@pytest.mark.asyncio
async def test_internet_off_replies_on_both_paths(monkeypatch) -> None:
    monkeypatch.setattr(dc, "get_searxng_client", lambda: _Detailed("internet_off"))
    handler = DoubleCheckHandler()
    with egress.override_policy("never"):
        q = await handler._answer_question_from_web("weather tomorrow", _ctx(), None)
        c = await handler._verify_claim_directly("x is true", _ctx(), None)
    want = "I'm set to stay off the internet, so I can't check that."
    assert q.text == want and c.text == want
    assert CHECKED_ONLINE not in q.text.lower()


@pytest.mark.asyncio
async def test_no_results_keeps_todays_wording(monkeypatch) -> None:
    """A search that ran and found nothing is the one case "I checked
    online" is true — the wording is unchanged."""
    monkeypatch.setattr(dc, "get_searxng_client", lambda: _Detailed("no_results"))
    handler = DoubleCheckHandler()
    q = await handler._answer_question_from_web("weather tomorrow", _ctx(), None)
    c = await handler._verify_claim_directly("x is true", _ctx(), None)
    assert q.text == "I checked online but couldn't find a clear answer to that."
    assert c.text == "I couldn't find anything about that to confirm or deny it."


@pytest.mark.asyncio
async def test_a_client_without_search_detailed_still_works(monkeypatch) -> None:
    monkeypatch.setattr(dc, "get_searxng_client", lambda: _OldStyle())
    resp = await DoubleCheckHandler()._answer_question_from_web("q", _ctx(), None)
    assert resp.text == "I checked online but couldn't find a clear answer to that."
    assert resp.data["search_status"] == "no_results"


@pytest.mark.asyncio
async def test_a_raising_client_is_an_error_not_a_search(monkeypatch) -> None:
    class _Boom:
        async def search_detailed(self, query, max_results=5):
            raise RuntimeError("socket gone")

    monkeypatch.setattr(dc, "get_searxng_client", lambda: _Boom())
    resp = await DoubleCheckHandler()._answer_question_from_web("q", _ctx(), None)
    assert resp.text == _UNSEARCHED["error"]


@pytest.mark.asyncio
async def test_unreachable_start_hint_is_logged_once(monkeypatch, caplog) -> None:
    monkeypatch.setattr(dc, "_UNREACHABLE_LOGGED", False)
    monkeypatch.setattr(dc, "get_searxng_client", lambda: _Detailed("unreachable"))
    handler = DoubleCheckHandler()
    with caplog.at_level(logging.WARNING, logger="domovoi.handlers.double_check"):
        await handler._answer_question_from_web("a", _ctx(), None)
        await handler._verify_claim_directly("b", _ctx(), None)
    hints = [r for r in caplog.records if "docker compose up -d searxng" in r.getMessage()]
    assert len(hints) == 1
    assert "SearXNG isn't answering at http://127.0.0.1:6888" in hints[0].getMessage()


# ─── The offline wording: turned off vs. no internet right now ──────────


@pytest.mark.asyncio
async def test_fallback_offline_wording() -> None:
    handler = DoubleCheckHandler()
    intent = Intent(transcript="double check that", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=False)
    unset = await handler.fallback_offline(intent, ctx, None)
    assert unset.text == "I can't check that right now — I don't have internet."
    with egress.override_policy("never"):
        off = await handler.fallback_offline(intent, ctx, None)
    assert off.text == "I'm set to stay off the internet, so I can't check that."
    with egress.override_policy("always"):
        down = await handler.fallback_offline(intent, ctx, None)
    assert down.text == unset.text


@pytest.mark.asyncio
async def test_yes_to_an_offer_while_offline_uses_the_same_wording() -> None:
    handler = DoubleCheckHandler()
    ctx = Context(room_id="kitchen", online=False)
    data = {"question": "weather tomorrow", "category": ""}
    unset = await handler._handle_self_doubt_offer(data, True, ctx, None)
    assert unset.text == "I can't check that right now — I don't have internet."
    with egress.override_policy("never"):
        off = await handler._handle_self_doubt_offer(data, True, ctx, None)
    assert off.text == "I'm set to stay off the internet, so I can't check that."


def test_news_offline_lead() -> None:
    from domovoi.handlers.news import _offline_lead

    assert _offline_lead() == "I can't reach the internet right now. "
    with egress.override_policy("never"):
        assert _offline_lead() == "I'm set to stay off the internet, so here's what I already have. "


@pytest.mark.asyncio
async def test_news_fetch_offline_reads_the_cache_with_the_right_lead() -> None:
    from domovoi.handlers.news import NewsHandler
    from domovoi.models import Response

    handler = NewsHandler()

    async def cached(subject, count, ctx, session):
        return Response(text="Here's what I have on mars.")

    handler._read_subject = cached
    ctx = Context(room_id="kitchen", online=False)
    unset = await handler._confirm_subject_fetch("mars", None, ctx, None)
    assert unset.text == "I can't reach the internet right now. Here's what I have on mars."
    with egress.override_policy("never"):
        off = await handler._confirm_subject_fetch("mars", None, ctx, None)
    assert off.text == (
        "I'm set to stay off the internet, so here's what I already have. "
        "Here's what I have on mars."
    )


@requires_db
@pytest.mark.asyncio
async def test_router_volatile_question_wording(db_session) -> None:
    """The router's offline answer to a volatile question: unchanged while
    unanswered, the turned-off sentence under never."""
    from domovoi.router import route

    intent = Intent(transcript="who is the current president", room_id="kitchen")
    ctx = Context(room_id="kitchen", online=False)
    unset = await route(intent, ctx, db_session)
    await db_session.commit()
    assert unset.text == "I'd need the internet to answer that, and we're offline right now."
    with egress.override_policy("never"):
        off = await route(intent, Context(room_id="kitchen", online=False), db_session)
        await db_session.commit()
    assert off.text == "I'd need the internet to answer that, and I'm set to stay off it."
