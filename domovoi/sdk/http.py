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
"""

from __future__ import annotations

from typing import Any

USER_AGENT_TEMPLATE = (
    "domovoi/{version} (+https://github.com/coders-farm-official/domovoi)"
)


class HttpFactory:
    def __init__(self, version: str = "1.0.0") -> None:
        self.user_agent = USER_AGENT_TEMPLATE.format(version=version)

    def client(self, **kwargs: Any):
        """A new ``httpx.AsyncClient`` with the product UA preset (caller
        headers are merged over it) and the internet-answer hook installed
        FIRST in ``event_hooks["request"]`` (the caller's own hooks run
        after it). httpx is imported lazily — it ships with plugin/web
        extras, not the minimal core install."""
        from domovoi import egress

        headers = {"User-Agent": self.user_agent}
        headers.update(kwargs.pop("headers", None) or {})
        kwargs.setdefault("timeout", 15.0)
        return egress.async_client(headers=headers, **kwargs)
