"""Stand-ins for ``net_safety.open_raw_stream`` / ``net_safety.iter_raw``.

The player relay (domovoi/player_relay.py) reads a remote stream through
these two helpers (H2, security review 2026-11-23): ``open_stream`` that
asks for identity and refuses any Content-Encoding, and raw reads that
check the coding again. They arrive with the same fix on main. Until main
is merged into a branch that lacks them, :func:`install` puts these
stand-ins (the announced API, nothing more) in their place for a test;
once the real ones exist it does nothing.
"""

from __future__ import annotations

from typing import Any

from domovoi import net_safety


async def open_raw_stream(client: Any, url: str, *, method: str = "GET",
                          headers: dict[str, str] | None = None,
                          max_redirects: int = net_safety.MAX_REDIRECTS) -> Any:
    headers = {**(headers or {}), "Accept-Encoding": "identity"}
    resp = await net_safety.open_stream(client, url, method=method, headers=headers,
                                        max_redirects=max_redirects)
    if net_safety._content_codings(resp):
        await resp.aclose()
        raise net_safety.UnsupportedContentEncoding(url, resp.headers.get("content-encoding", ""))
    return resp


async def iter_raw(response: Any, chunk_size: int = 65536):
    if net_safety._content_codings(response):
        raise net_safety.UnsupportedContentEncoding(
            str(response.url), response.headers.get("content-encoding", ""))
    if getattr(response, "_content", None) is not None:
        # A MockTransport answer built from bytes arrives already read.
        body = response.content
        for i in range(0, len(body), chunk_size):
            yield body[i:i + chunk_size]
        return
    async for chunk in response.aiter_raw(chunk_size):
        yield chunk


def install(monkeypatch: Any) -> None:
    if not hasattr(net_safety, "open_raw_stream"):
        monkeypatch.setattr(net_safety, "open_raw_stream", open_raw_stream, raising=False)
    if not hasattr(net_safety, "iter_raw"):
        monkeypatch.setattr(net_safety, "iter_raw", iter_raw, raising=False)
