"""radio-browser.info client — internet-station discovery.

radio-browser.info is a free, no-auth, community-maintained directory of
~50 k internet radio stations. The Stations page uses this client to
power the "search and favorite" surface; the plugin persists a search
hit's ``url_resolved`` into ``plugin_radio.radio_stations`` with
``source='online'`` and the sampler/streamer takes it from there.

Relevant subset of the JSON shape returned by
``GET /json/stations/search``::

    [
      {
        "stationuuid": "abcdef...",
        "name": "KEXP 90.3 FM",
        "url_resolved": "https://kexp-mp3-128.streamguys1.com/kexp128.mp3",
        "countrycode": "US",
        "language": "english",
        "tags": "alternative,indie,seattle",
        "codec": "MP3",
        "bitrate": 128,
        "homepage": "https://www.kexp.org"
      },
      ...
    ]

A station is "usable" if it has both a ``stationuuid`` (our
``external_id``) and a non-empty ``url_resolved`` (something ffmpeg can
connect to). Anything missing either is filtered out at parse time so
callers never defensively check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from domovoi_plugin_radio import USER_AGENT

log = logging.getLogger(__name__)


# radio-browser publishes a DNS round-robin of mirrors. Pinning a single
# host is simpler than the DNS-lookup-then-pick dance and works fine for
# home-server QPS. If de1 disappears, swap to nl1 or at1.
_DEFAULT_BASE_URL = "https://de1.api.radio-browser.info"


@dataclass
class RadioBrowserStation:
    """One result row from radio-browser search, normalized to the
    fields the plugin persists in ``radio_stations``."""

    external_id: str          # radio-browser's stationuuid
    name: str
    stream_url: str           # url_resolved — the playable HTTP/Icecast URL
    country_code: str | None = None
    language: str | None = None
    tags: list[str] | None = None
    codec: str | None = None
    bitrate: int | None = None
    homepage: str | None = None
    favicon: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "name": self.name,
            "stream_url": self.stream_url,
            "country_code": self.country_code,
            "language": self.language,
            "tags": self.tags or [],
            "codec": self.codec,
            "bitrate": self.bitrate,
            "homepage": self.homepage,
            "favicon": self.favicon,
        }


class RadioBrowserClient(Protocol):
    async def search(
        self,
        name: str | None = None,
        country_code: str | None = None,
        tag: str | None = None,
        language: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> list[RadioBrowserStation]: ...


class RadioBrowserStubClient:
    """Deterministic stub — returns fake stations derived from the
    search args so pages/tests render realistic-shaped data offline."""

    async def search(
        self,
        name: str | None = None,
        country_code: str | None = None,
        tag: str | None = None,
        language: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> list[RadioBrowserStation]:
        if not (name or tag or language):
            return []
        seed = name or tag or language or "stub"
        rows = [
            RadioBrowserStation(
                external_id=f"stub-{seed}-{i}",
                name=f"Stub {seed} {i + 1}",
                stream_url=f"http://stub.example/{seed}/{i + 1}.mp3",
                country_code=country_code or "US",
                language=language or "english",
                tags=[tag] if tag else ["stub"],
                codec="MP3",
                bitrate=128,
            )
            for i in range(min(limit + offset, 3))
        ]
        return rows[offset:]


class RealRadioBrowserClient:
    """httpx-backed radio-browser client (httpx imported lazily so the
    stub path never pulls it into the import graph)."""

    def __init__(self, base_url: str = _DEFAULT_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")

    async def search(
        self,
        name: str | None = None,
        country_code: str | None = None,
        tag: str | None = None,
        language: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> list[RadioBrowserStation]:
        # No-args query would return the full 50 k-row dump — guard
        # against accidental wide-net hits.
        if not (name or country_code or tag or language):
            return []

        import httpx

        url = f"{self._base_url}/json/stations/search"
        params: dict[str, Any] = {
            "limit": max(1, min(limit, 100)),
            "offset": max(0, offset),
            # Prefer reachable / recently-checked stations, ordered by
            # what real listeners actually click on.
            "hidebroken": "true",
            "order": "clickcount",
            "reverse": "true",
        }
        if name:
            params["name"] = name
        if country_code:
            params["countrycode"] = country_code
        if tag:
            params["tag"] = tag
        if language:
            params["language"] = language

        try:
            # Generous timeout — mirrors are sometimes slow on first
            # request as DNS rotates; longer than 10 s and the search
            # box feels broken.
            async with httpx.AsyncClient(
                timeout=10.0, headers={"User-Agent": USER_AGENT}
            ) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception as e:
            log.warning("radio-browser search failed (params=%s): %s", params, e)
            return []

        if not isinstance(payload, list):
            log.warning(
                "radio-browser returned non-list payload: %r",
                type(payload).__name__,
            )
            return []

        out: list[RadioBrowserStation] = []
        for row in payload:
            station = _parse_row(row)
            if station is not None:
                out.append(station)
        return out


def _parse_row(row: Any) -> RadioBrowserStation | None:
    """Normalize one search-result row. Rows missing a stationuuid or a
    non-empty url_resolved are dropped quietly — persisting them would
    just break at sampler time."""
    if not isinstance(row, dict):
        return None
    external_id = (row.get("stationuuid") or "").strip()
    name = (row.get("name") or "").strip()
    stream_url = (row.get("url_resolved") or row.get("url") or "").strip()
    if not external_id or not name or not stream_url:
        return None

    bitrate_raw = row.get("bitrate")
    try:
        bitrate = int(bitrate_raw) if bitrate_raw is not None else None
    except (TypeError, ValueError):
        bitrate = None

    # Tags arrive as one comma-separated string; split + strip + drop
    # empties so the TEXT[] column gets clean values.
    tags_raw = row.get("tags") or ""
    tags = [t.strip() for t in str(tags_raw).split(",") if t.strip()]

    return RadioBrowserStation(
        external_id=external_id,
        name=name,
        stream_url=stream_url,
        country_code=(row.get("countrycode") or None),
        language=(row.get("language") or None),
        tags=tags or None,
        codec=(row.get("codec") or None),
        bitrate=bitrate,
        homepage=(row.get("homepage") or None),
        favicon=(row.get("favicon") or None),
    )


_client: RadioBrowserClient | None = None


def get_radio_browser_client(*, use_stubs: bool = False) -> RadioBrowserClient:
    global _client
    if _client is None:
        _client = RadioBrowserStubClient() if use_stubs else RealRadioBrowserClient()
    return _client


def _set_radio_browser_client_for_tests(client: RadioBrowserClient | None) -> None:
    global _client
    _client = client
