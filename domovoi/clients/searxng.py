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
from typing import Any, Protocol

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


class SearxNGClient(Protocol):
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        ...


class SearxNGStubClient:
    """Deterministic stub for tests — returns N fake results derived
    from the query string. The DoubleCheck verdict path can run under
    USE_STUBS without the core ever needing a live SearxNG."""

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not query.strip():
            return []
        return [
            SearchResult(
                title=f"Stub result {i + 1} for {query}",
                url=f"https://stub.example/r/{i + 1}",
                content=f"Stub content for {query} #{i + 1}.",
                engine="stub",
            )
            for i in range(max_results)
        ]


class RealSearxNGClient:
    """httpx-backed SearxNG JSON API client. Lazy import so USE_STUBS
    runs don't need httpx (it's already a transitive dep, but the
    pattern is consistent with the other clients)."""

    def __init__(self, base_url: str) -> None:
        # Strip trailing slash so the search-path concat is stable
        # whether the env var ends in / or not.
        self._base_url = base_url.rstrip("/")

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not query.strip():
            return []
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
        except Exception as e:
            log.warning("SearxNG query failed for %r: %s", query, e)
            return []

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
        return out


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
