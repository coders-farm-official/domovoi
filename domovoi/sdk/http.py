"""HttpFactory — pre-branded outbound HTTP clients (design §4.10).

Every plugin's outbound requests carry the product UA (several upstream
services — MusicBrainz among them — require a descriptive UA), and the
factory is the single place a future outbound-proxy/timeout policy
lands.

The internet answer is enforced here (SDK 1.4): every client the factory
hands out runs :func:`domovoi.egress.async_request_hook` first, for every
request and every redirect hop, so under ``INTERNET_ACCESS=never`` a
plugin's request to a host that is not on this network raises
``egress.InternetTurnedOff`` before anything is sent. A plugin that builds
its own raw ``httpx`` client is outside this gate — its author's job
(docs/PLUGIN_DEVELOPMENT.md).

The household's AI switch is enforced here too (SDK 1.7): while AI
features are off (``domovoi/ai_features.py``) every request to this box's
Ollama (port 11434, or the configured ``OLLAMA_URL``) but its read-only
listings raises ``ai_features.AITurnedOffConnectError`` before anything is
sent — denied by host and port, so the OpenAI-compatible ``/v1/...`` paths
are covered as well as the native ones — so a plugin's
summary or answer step fails cleanly, the way it fails when Ollama is
down. ``sdk.core_config.ai_features`` says which it is up front.

Voice goes first (S3-7): the house's Ollama answers one request at a time,
so a request that makes it generate (``/api/chat``, ``/api/generate`` and
the OpenAI-compatible completions) waits while a voice turn is under way,
and is stopped and sent again after the turn if one starts before Ollama
has answered (:func:`domovoi.voice_first.ahead_of_voice`). A streamed
answer is only ever stopped before its first byte. A plugin's own voice
handler, called inside the turn, is never held up.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

USER_AGENT_TEMPLATE = (
    "domovoi/{version} (+https://github.com/coders-farm-official/domovoi)"
)


#: Paths on the house's Ollama that make it generate an answer.
GENERATING_PATHS = ("/api/chat", "/api/generate", "/v1/chat/completions", "/v1/completions")

_CLIENT_CLASS: Any = None


def generates(method: str, url: str) -> bool:
    """Whether a request makes the house's Ollama generate an answer."""
    from domovoi import ai_features

    if method.upper() != "POST" or not ai_features.is_ollama(url):
        return False
    try:
        path = urlsplit(url).path
    except ValueError:
        return False
    while "//" in path:
        path = path.replace("//", "/")
    return path.rstrip("/").endswith(GENERATING_PATHS)


def _client_class() -> Any:
    """``httpx.AsyncClient`` whose model requests let voice go first; built
    on first use (httpx is imported lazily)."""
    global _CLIENT_CLASS
    if _CLIENT_CLASS is None:
        import httpx

        class VoiceFirstAsyncClient(httpx.AsyncClient):
            async def send(self, request: Any, **kwargs: Any) -> Any:
                if not generates(request.method, str(request.url)):
                    return await super().send(request, **kwargs)
                from domovoi import voice_first

                send = super().send
                return await voice_first.ahead_of_voice(
                    lambda: send(request, **kwargs), label="a plugin's model request",
                )

        _CLIENT_CLASS = VoiceFirstAsyncClient
    return _CLIENT_CLASS


class HttpFactory:
    def __init__(self, version: str = "1.0.0") -> None:
        self.user_agent = USER_AGENT_TEMPLATE.format(version=version)

    def client(self, **kwargs: Any):
        """A new ``httpx.AsyncClient`` with the product UA preset (caller
        headers are merged over it) and the internet-answer hook installed
        FIRST in ``event_hooks["request"]`` (the caller's own hooks run
        after it). httpx is imported lazily — it ships with plugin/web
        extras, not the minimal core install."""
        from domovoi import ai_features, egress

        headers = {"User-Agent": self.user_agent}
        headers.update(kwargs.pop("headers", None) or {})
        kwargs.setdefault("timeout", 15.0)
        return egress.async_client(
            client_class=_client_class(), headers=headers, **ai_features.with_request_hook(kwargs),
        )
