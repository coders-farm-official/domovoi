"""ICY (SHOUTcast / Icecast) metadata client.

Internet radio streams following the ICY convention interleave a
metadata block into the audio response: every ``icy-metaint`` bytes of
audio is followed by one length byte ``L`` and ``L * 16`` bytes of
metadata text — a sequence of ``key='value';`` pairs, of which the only
one we care about is ``StreamTitle``::

    StreamTitle='Radiohead - Creep';StreamUrl='';

Compared to the audio-fingerprint path (ffmpeg grab → identify), this is
essentially free: one HTTP GET per station per poll, a few KB of audio
consumed before the metadata block lands, one regex on a short string.

Public surface:

* :func:`parse_stream_title` — pure parser; trivially unit-testable.
* :func:`parse_metadata_block` — full key→value mapping (debug/tests).
* ``IcyClient.fetch(url)`` → :class:`IcyPollResult`.

Stations without ``icy-metaint`` are reported ``supported=False`` so the
poller can flip the tristate ``icy_supported`` to FALSE after a handful
of consecutive misses. Stations that DO advertise the header but yield
an empty title come back ``supported=True`` / ``stream_title=None`` —
"ICY works but nothing identifiable right now".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from domovoi_plugin_radio import USER_AGENT

log = logging.getLogger(__name__)


# StreamTitle='<arbitrary characters>'; — single-quoted, semicolon-
# terminated. Non-greedy + anchored on the explicit `';` terminator so a
# title containing an apostrophe doesn't get truncated.
_STREAM_TITLE_RE = re.compile(r"StreamTitle='(.*?)';", re.DOTALL)

# Maximum bytes of audio to read before giving up on finding a metadata
# block. Advertised icy-metaint is usually 8–32 KB; past ~256 KB the
# header lied or the connection is bursting audio without interleave.
_MAX_AUDIO_READ_BYTES = 256 * 1024


@dataclass
class IcyPollResult:
    """One poll's outcome — enough to update both the now-playing cache
    and the tristate ``icy_supported`` flag without re-parsing.

    ``supported`` is True iff the station returned an ``icy-metaint``
    header (regardless of whether a title was extractable).
    """

    supported: bool
    stream_title: str | None = None
    raw_metadata: str | None = None
    error: str | None = None


class IcyClient(Protocol):
    async def fetch(self, url: str) -> IcyPollResult: ...


# ─── Pure parsers ────────────────────────────────────────────────────────


def parse_stream_title(blob: bytes | str) -> str | None:
    """Extract the ``StreamTitle`` value from a raw ICY metadata block.

    Tolerates the two encodings stations actually use in the wild —
    UTF-8 (current installs) and Latin-1 (old SHOUTcast 1.x servers) — and
    strips the NUL padding the spec mandates on the trailing chunk.
    Returns ``None`` when there's no recognizable StreamTitle or the
    value is empty after stripping.
    """
    if not blob:
        return None
    if isinstance(blob, bytes):
        blob = blob.rstrip(b"\x00")
        if not blob:
            return None
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            text = blob.decode("latin-1", errors="replace")
    else:
        text = blob

    m = _STREAM_TITLE_RE.search(text)
    if m is None:
        return None
    title = m.group(1).strip()
    return title or None


def parse_metadata_block(blob: bytes) -> dict[str, str]:
    """Parse a full ICY metadata block into ``key → value``. The poller
    only consumes StreamTitle; this exists for tests and debugging."""
    if not blob:
        return {}
    blob = blob.rstrip(b"\x00")
    if not blob:
        return {}
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        text = blob.decode("latin-1", errors="replace")

    out: dict[str, str] = {}
    for match in re.finditer(r"([A-Za-z][A-Za-z0-9_]*)='(.*?)';", text, re.DOTALL):
        out[match.group(1)] = match.group(2)
    return out


# ─── Real client ─────────────────────────────────────────────────────────


class RealIcyClient:
    """httpx-backed ICY fetcher.

    1. Open a streaming GET with ``Icy-MetaData: 1``.
    2. Read ``icy-metaint``; missing ⇒ ``supported=False``.
    3. Consume exactly ``metaint`` audio bytes (discarded).
    4. Read the length byte; ``0`` ⇒ ``supported=True`` with no title.
    5. Otherwise read ``L * 16`` metadata bytes, parse, return.

    Connection lifetimes are short (~32 KB per poll, then close).
    """

    def __init__(self, *, timeout_sec: float = 6.0) -> None:
        self._timeout = timeout_sec

    async def fetch(self, url: str) -> IcyPollResult:
        headers = {
            "Icy-MetaData": "1",
            "User-Agent": USER_AGENT,
            # Some Icecast deployments only emit ICY headers when the
            # request looks like a media player.
            "Accept": "*/*",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            ) as client:
                async with client.stream("GET", url, headers=headers) as resp:
                    # 2xx can still lack ICY — check the header, not the
                    # status. 4xx/5xx mean the stream itself is broken.
                    if resp.status_code >= 400:
                        return IcyPollResult(
                            supported=False, error=f"http {resp.status_code}"
                        )

                    metaint = _read_metaint(resp.headers)
                    if metaint is None:
                        return IcyPollResult(
                            supported=False, error="no icy-metaint header"
                        )

                    blob = await _read_metadata_block(resp, metaint)
                    if blob is None:
                        # Advertised metaint but no block within budget —
                        # treat as "supported but empty"; flipping the
                        # tristate off one bad poll is premature.
                        return IcyPollResult(
                            supported=True,
                            stream_title=None,
                            error="metadata block unreachable",
                        )
                    title = parse_stream_title(blob)
                    raw = blob.rstrip(b"\x00").decode("utf-8", errors="replace")
                    return IcyPollResult(
                        supported=True, stream_title=title, raw_metadata=raw
                    )
        except httpx.HTTPError as e:
            # Network blip / DNS / reset / timeout — don't flip
            # icy_supported off the back of one transport error.
            return IcyPollResult(supported=False, error=f"http error: {e}")
        except Exception as e:
            log.debug("icy fetch failed for %s: %s", url, e)
            return IcyPollResult(supported=False, error=f"unexpected: {e}")


class IcyStubClient:
    """Deterministic stub. Tests inject per-URL results
    (``stub.responses[url] = IcyPollResult(...)``) or a default."""

    def __init__(self, default_result: IcyPollResult | None = None) -> None:
        self.responses: dict[str, IcyPollResult] = {}
        self.calls: list[str] = []
        self.default_result = default_result or IcyPollResult(supported=False)

    async def fetch(self, url: str) -> IcyPollResult:
        self.calls.append(url)
        return self.responses.get(url, self.default_result)


# ─── Internal helpers ────────────────────────────────────────────────────


def _read_metaint(headers: httpx.Headers) -> int | None:
    raw = headers.get("icy-metaint") or headers.get("Icy-MetaInt")
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


async def _read_metadata_block(resp: httpx.Response, metaint: int) -> bytes | None:
    """Consume ``metaint`` audio bytes, then read the metadata block.

    Returns the metadata bytes (without the length prefix), ``b""`` when
    the station reports "no change" (length byte 0), or ``None`` when
    the interleave point never arrived within the audio budget.
    """
    audio_remaining = metaint
    total_consumed = 0
    async for chunk in resp.aiter_raw(chunk_size=4096):
        total_consumed += len(chunk)
        if total_consumed > _MAX_AUDIO_READ_BYTES and audio_remaining > 0:
            return None
        if len(chunk) < audio_remaining:
            audio_remaining -= len(chunk)
            continue

        # The audio/metadata boundary lands inside this chunk.
        meta_offset = audio_remaining
        audio_remaining = 0

        while len(chunk) <= meta_offset:
            # Length byte didn't make it into this chunk; grab another.
            try:
                more = await resp.aiter_raw(chunk_size=1).__anext__()
            except StopAsyncIteration:
                return None
            chunk = chunk + more

        length_byte = chunk[meta_offset]
        meta_offset += 1
        meta_len = length_byte * 16

        if meta_len == 0:
            return b""

        meta_bytes = chunk[meta_offset:meta_offset + meta_len]
        needed = meta_len - len(meta_bytes)
        while needed > 0:
            try:
                more = await resp.aiter_raw(chunk_size=needed).__anext__()
            except StopAsyncIteration:
                return None
            meta_bytes = meta_bytes + more
            needed = meta_len - len(meta_bytes)

        return meta_bytes

    return None    # stream closed before the first interleave point


_client: IcyClient | None = None


def get_icy_client(
    *, use_stubs: bool = False, timeout_sec: float = 6.0
) -> IcyClient:
    global _client
    if _client is None:
        _client = (
            IcyStubClient() if use_stubs else RealIcyClient(timeout_sec=timeout_sec)
        )
    return _client


def _set_icy_client_for_tests(client: IcyClient | None) -> None:
    global _client
    _client = client
