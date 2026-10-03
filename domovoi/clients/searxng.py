"""SearxNG client for the DoubleCheckHandler claim verification flow.

SearxNG is the privacy-respecting metasearch proxy that we run locally
(see ``domovoi/docker-compose.yml`` ``searxng`` service). The
core's DoubleCheckHandler queries it whenever the user asks
"are you sure?" / "double check that" — extracts a claim from the bot's
last response, hits this client, gets back ranked search results, then
re-prompts Ollama to decide confirmed / refuted / ambiguous.

Why a separate client (rather than handlers calling httpx directly):
keeps the SearxNG-specific JSON shape isolated, lets us swap in a stub
for tests, mirrors the structure of every other external integration
in the core (mpd / ollama / whisper / tts).

JSON shape we care about (subset — SearxNG returns more):

    {
      "results": [
        {
          "title": "...",
          "url": "https://...",
          "content": "snippet...",
          "engine": "google",
          "score": 1.23
        },
        ...
      ]
    }
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple, Protocol

from domovoi import egress
from domovoi.config import settings

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    engine: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "engine": self.engine,
        }


SearchStatus = Literal["ok", "no_results", "unreachable", "internet_off", "error"]


class SearchOutcome(NamedTuple):
    """What a search came back with, and why when it came back empty.

    ``status``:

    * ``ok`` — at least one result;
    * ``no_results`` — SearXNG answered with nothing usable;
    * ``unreachable`` — nothing listens at ``SEARXNG_URL`` (the container
      isn't running, or the URL is wrong);
    * ``internet_off`` — the box is set to stay off the internet
      (``INTERNET_ACCESS=never``); no request was made. SearXNG itself is
      local, but every search it runs goes to public search engines;
    * ``error`` — any other HTTP or JSON failure.

    Callers that only want results keep using ``search()``; the voice
    replies use the status so "I checked online" is only ever said after a
    search really ran (handlers/double_check.py)."""

    results: list[SearchResult]
    status: SearchStatus


class SearxNGClient(Protocol):
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        ...

    async def search_detailed(self, query: str, max_results: int = 5) -> SearchOutcome:
        ...


class SearxNGStubClient:
    """Deterministic stub for tests — returns N fake results derived
    from the query string. The DoubleCheck verdict path can run under
    USE_STUBS without the core ever needing a live SearxNG."""

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        return (await self.search_detailed(query, max_results)).results

    async def search_detailed(self, query: str, max_results: int = 5) -> SearchOutcome:
        if not query.strip():
            return SearchOutcome([], "no_results")
        return SearchOutcome(
            [
                SearchResult(
                    title=f"Stub result {i + 1} for {query}",
                    url=f"https://stub.example/r/{i + 1}",
                    content=f"Stub content for {query} #{i + 1}.",
                    engine="stub",
                )
                for i in range(max_results)
            ],
            "ok",
        )


class RealSearxNGClient:
    """httpx-backed SearxNG JSON API client. Lazy import so USE_STUBS
    runs don't need httpx (it's already a transitive dep, but the
    pattern is consistent with the other clients)."""

    def __init__(self, base_url: str) -> None:
        # Strip trailing slash so the search-path concat is stable
        # whether the env var ends in / or not.
        self._base_url = base_url.rstrip("/")

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        return (await self.search_detailed(query, max_results)).results

    async def search_detailed(self, query: str, max_results: int = 5) -> SearchOutcome:
        if not query.strip():
            return SearchOutcome([], "no_results")
        try:
            # SearXNG is on this box, but it forwards every query to public
            # search engines: under INTERNET_ACCESS=never nothing is sent.
            egress.require_internet("web search")
        except egress.InternetTurnedOff:
            return SearchOutcome([], "internet_off")
        import httpx

        url = f"{self._base_url}/search"
        params = {
            "q": query,
            "format": "json",
            # Pin a US English locale so unit-sensitive results (weather,
            # measurements) come back imperial/Fahrenheit rather than a
            # region-neutral metric default the summarizer can then mislabel
            # (a Celsius high reported as °F — the Phoenix "40°F" bug).
            "language": "en-US",
        }
        try:
            # Modest timeout — DoubleCheck is user-facing voice, can't
            # hold the WebSocket too long. SearxNG aggregates multiple
            # engines so timeouts here mean either everything's slow or
            # the container's not running; either way we give up and
            # let the caller produce a graceful "I couldn't check" reply.
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            # Nothing listening (the container isn't running) or the
            # address doesn't answer: the search never ran.
            log.warning("SearxNG unreachable at %s for %r: %s", self._base_url, query, e)
            return SearchOutcome([], "unreachable")
        except Exception as e:
            log.warning("SearxNG query failed for %r: %s", query, e)
            return SearchOutcome([], "error")

        raw_results = payload.get("results", []) if isinstance(payload, dict) else []
        out: list[SearchResult] = []
        for r in raw_results[:max_results]:
            if not isinstance(r, dict):
                continue
            url_v = r.get("url") or ""
            title = r.get("title") or ""
            content = r.get("content") or ""
            engine = r.get("engine") or ""
            if not url_v or not title:
                continue
            out.append(
                SearchResult(
                    title=str(title),
                    url=str(url_v),
                    content=str(content),
                    engine=str(engine),
                )
            )
        return SearchOutcome(out, "ok" if out else "no_results")

    @property
    def base_url(self) -> str:
        return self._base_url


_client: SearxNGClient | None = None


def get_searxng_client() -> SearxNGClient:
    global _client
    if _client is not None:
        return _client
    if settings.use_stubs:
        _client = SearxNGStubClient()
    else:
        _client = RealSearxNGClient(base_url=settings.searxng_url)
    return _client
