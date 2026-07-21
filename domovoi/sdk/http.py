"""HttpFactory — pre-branded outbound HTTP clients (design §4.10).

Every plugin's outbound requests carry the product UA (several upstream
services — MusicBrainz among them — require a descriptive UA), and the
factory is the single place a future outbound-proxy/timeout policy
lands.
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
        headers are merged over it). httpx is imported lazily — it ships
        with plugin/web extras, not the minimal core install."""
        import httpx

        headers = {"User-Agent": self.user_agent}
        headers.update(kwargs.pop("headers", None) or {})
        kwargs.setdefault("timeout", 15.0)
        return httpx.AsyncClient(headers=headers, **kwargs)
