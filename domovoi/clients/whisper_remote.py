"""Speech-to-text on another machine in the house, before this one's own.

Why: on a Mac the core runs inside a Linux VM that has no GPU, while the
Mac itself can run Whisper on its graphics chip (Metal). mac-installer
Phase 8a runs a whisper.cpp server on the Mac, as its own background
service on the Mac's loopback, reached from the VM like Ollama is
(``host.lima.internal``). ``whisper_remote_url`` names it. Empty, the
default, leaves everything exactly as it was: this module is not used.

What it is not: a way to send audio anywhere. Only an address on this
network is accepted (:func:`domovoi.egress.is_local_host`), whatever the
internet answer, and the request is made through the egress-checked client.

Two parts:

* :class:`RemoteWhisperClient` speaks whisper.cpp's server API
  (``POST /inference``, multipart, ``response_format=verbose_json``;
  ``GET /health``). It keeps the core's two decode paths
  (clients/whisper.py): a capture of up to
  :data:`~domovoi.clients.whisper.SHORT_WINDOW_MAX_AUDIO_SEC` on an
  English-only model is decoded on a 10 s window first (whisper.cpp's
  ``audio_ctx``: 50 encoder positions a second, 500 for 10 s, the same
  trick :class:`~domovoi.clients.whisper.ShortWindowDecoder` plays on
  CTranslate2), judged by the same :class:`~domovoi.clients.whisper.ShortDecode`
  verdict, and anything blank, unsure or long goes to the full 30 s window
  with the temperature fallback.
* :class:`FallbackWhisperClient` puts it in front of the local Whisper,
  which still loads at boot. A request that fails (refused, timed out,
  an error answer, an unreadable one) is heard locally instead, and the
  service is left alone for ``whisper_remote_retry_sec`` so a stopped
  service costs one wait rather than one per turn. Nothing is hidden:
  :meth:`FallbackWhisperClient.engine_doc` says which one is hearing
  turns and why (``stt_status()["engine"]``), the per-turn record names
  it (device ``host``), and the switch is logged both ways.
"""

from __future__ import annotations

import io
import logging
import math
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import numpy as np

from domovoi import egress
from domovoi.clients.whisper import (
    FULL_WINDOW_SEC,
    SAMPLE_RATE,
    SAMPLE_WIDTH_BYTES,
    SHORT_MAX_NEW_TOKENS,
    SHORT_WINDOW_MAX_AUDIO_SEC,
    SHORT_WINDOW_SEC,
    BEAM_SIZE,
    ShortDecode,
    SttUnavailableError,
    wav_to_float32,
)
from domovoi.config import settings

log = logging.getLogger(__name__)

# Whisper's encoder: 1500 positions for 30 s of audio.
AUDIO_CTX_PER_SEC = 50
SHORT_AUDIO_CTX = SHORT_WINDOW_SEC * AUDIO_CTX_PER_SEC
# faster-whisper's temperature fallback steps (0.0, 0.2, ... 1.0), which
# the full path keeps; the short path has none (its verdict sends anything
# doubtful to the full path instead).
FULL_TEMPERATURE_INC = 0.2
CONNECT_TIMEOUT_SEC = 0.5
HEALTH_TIMEOUT_SEC = 1.0
_MAX_ERROR_CHARS = 300


class RemoteSttError(RuntimeError):
    """The service didn't produce a transcript: refused, timed out, an
    error status, or an answer that isn't whisper.cpp's JSON."""


def check_url(url: str) -> str:
    """The base URL, normalised (no trailing slash), or ValueError: http or
    https, a host on this network, no user name or password in it."""
    raw = (url or "").strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"whisper_remote_url must be http://HOST:PORT, not {raw!r}")
    if parts.username or parts.password:
        raise ValueError("whisper_remote_url must not carry a user name or password")
    if not egress.is_local_host(parts.hostname):
        raise ValueError(
            f"whisper_remote_url names {parts.hostname}, which isn't on this "
            "network: household audio is only sent to a machine in the house"
        )
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def pcm_to_wav(pcm_bytes: bytes) -> bytes:
    """16 kHz mono int16 PCM as a WAV file (what whisper.cpp's server reads)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(SAMPLE_WIDTH_BYTES)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def float32_to_pcm(audio: np.ndarray) -> bytes:
    """The reverse of clients.whisper.pcm_to_float32, clipped."""
    return (np.clip(audio, -1.0, 32767 / 32768) * 32768.0).astype("<i2").tobytes()


@dataclass(frozen=True)
class RemoteReply:
    """One whisper.cpp answer, reduced to what the core judges it by."""

    text: str
    # Token-weighted over the segments; -inf when the answer has none.
    avg_logprob: float
    # The first segment's (the window's own); 0.0 when the answer has none.
    no_speech_prob: float
    tokens: int

    def short_decode(self) -> ShortDecode:
        return ShortDecode(
            text=self.text,
            avg_logprob=self.avg_logprob,
            no_speech_prob=self.no_speech_prob,
            complete=self.tokens < SHORT_MAX_NEW_TOKENS - 1,
        )


def parse_reply(doc: Any) -> RemoteReply:
    """whisper.cpp's ``verbose_json`` answer: ``{"text", "segments": [{"text",
    "tokens", "avg_logprob", "no_speech_prob"}, ...]}``. A plain ``{"text"}``
    answer (another response format) parses too, with no scores, so its
    short decode is never trusted. Raises RemoteSttError for anything
    else, an ``{"error": ...}`` answer included."""
    if not isinstance(doc, dict):
        raise RemoteSttError("the answer isn't a JSON object")
    if "error" in doc and "text" not in doc:
        raise RemoteSttError(f"the service said: {str(doc.get('error'))[:_MAX_ERROR_CHARS]}")
    segments = doc.get("segments")
    if isinstance(segments, list) and segments:
        texts: list[str] = []
        weighted, weight, tokens = 0.0, 0, 0
        no_speech: float | None = None
        for seg in segments:
            if not isinstance(seg, dict):
                raise RemoteSttError("a segment isn't a JSON object")
            texts.append(str(seg.get("text") or "").strip())
            n = len(seg.get("tokens") or [])
            tokens += n
            lp = seg.get("avg_logprob")
            if isinstance(lp, (int, float)) and not isinstance(lp, bool) and math.isfinite(lp):
                weighted += float(lp) * max(n, 1)
                weight += max(n, 1)
            ns = seg.get("no_speech_prob")
            if no_speech is None and isinstance(ns, (int, float)) and not isinstance(ns, bool):
                no_speech = float(ns)
        return RemoteReply(
            text=" ".join(t for t in texts if t).strip(),
            avg_logprob=weighted / weight if weight else -math.inf,
            no_speech_prob=no_speech if no_speech is not None else 0.0,
            tokens=tokens,
        )
    text = doc.get("text")
    if not isinstance(text, str):
        raise RemoteSttError("the answer has no text")
    return RemoteReply(text=text.strip(), avg_logprob=-math.inf, no_speech_prob=0.0, tokens=0)


class RemoteWhisperClient:
    """whisper.cpp's server as a Whisper client (same methods as
    :class:`~domovoi.clients.whisper.FasterWhisperClient`). Raises
    :class:`RemoteSttError` when the service doesn't answer properly; the
    :class:`FallbackWhisperClient` in front of it turns that into a local
    decode. ``transport`` is for tests (an ``httpx.MockTransport``)."""

    def __init__(
        self,
        url: str,
        *,
        model: str = "small.en",
        language: str = "en",
        timeout_sec: float = 2.0,
        transport: Any = None,
    ) -> None:
        self.url = check_url(url)
        self.model = (model or "").strip() or "unknown"
        self.language = (language or "").strip() or "en"
        self.timeout_sec = max(0.1, float(timeout_sec))
        # The short window, as locally: English-only models only.
        self.short_window_capable = self.model.lower().endswith(".en")
        self._transport = transport

    def _client(self, timeout: float) -> Any:
        import httpx

        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT_SEC, timeout)),
            "follow_redirects": False,
            "trust_env": False,         # never through a proxy: it is a house-only address
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return egress.async_client(**kwargs)

    def request_timeout(self, pcm_bytes: bytes) -> float:
        audio_sec = len(pcm_bytes) / (SAMPLE_RATE * SAMPLE_WIDTH_BYTES)
        return self.timeout_sec + 0.25 * audio_sec

    async def infer(self, pcm_bytes: bytes, *, short: bool) -> RemoteReply:
        """One request. ``short``: the 10 s window, no temperature fallback,
        no timestamps; else the full window with the fallback."""
        import httpx

        fields = {
            "response_format": "verbose_json",
            "language": self.language,
            "beam_size": str(BEAM_SIZE),
            "temperature": "0.0",
            "temperature_inc": "0.0" if short else str(FULL_TEMPERATURE_INC),
            "audio_ctx": str(SHORT_AUDIO_CTX if short else 0),
            "no_timestamps": "true" if short else "false",
        }
        files = {"file": ("capture.wav", pcm_to_wav(pcm_bytes), "audio/wav")}
        try:
            async with self._client(self.request_timeout(pcm_bytes)) as http:
                r = await http.post(f"{self.url}/inference", data=fields, files=files)
        except httpx.HTTPError as e:
            raise RemoteSttError(f"{type(e).__name__}: {e}"[:_MAX_ERROR_CHARS]) from e
        except (RuntimeError, ValueError) as e:      # an egress refusal, among others
            raise RemoteSttError(f"{type(e).__name__}: {e}"[:_MAX_ERROR_CHARS]) from e
        if r.status_code != 200:
            raise RemoteSttError(f"HTTP {r.status_code}: {r.text[:_MAX_ERROR_CHARS]}")
        try:
            doc = r.json()
        except ValueError as e:
            raise RemoteSttError("the answer isn't JSON") from e
        return parse_reply(doc)

    def fits_short(self, pcm_bytes: bytes) -> bool:
        return (
            self.short_window_capable
            and bool(settings.whisper_short_window_enabled)
            and 0 < len(pcm_bytes) // SAMPLE_WIDTH_BYTES <= int(SHORT_WINDOW_MAX_AUDIO_SEC * SAMPLE_RATE)
        )

    async def transcribe_with_window(self, pcm_bytes: bytes) -> tuple[str, int]:
        if self.fits_short(pcm_bytes):
            decoded = (await self.infer(pcm_bytes, short=True)).short_decode()
            verdict = decoded.verdict
            if verdict == "ok":
                return decoded.text, SHORT_WINDOW_SEC
            log.info(
                "Whisper (host): short-window decode came back %s (%.1f s of audio, "
                "avg_logprob %.2f, no_speech %.2f); using the 30 s window",
                verdict, len(pcm_bytes) / (SAMPLE_RATE * SAMPLE_WIDTH_BYTES),
                decoded.avg_logprob, decoded.no_speech_prob,
            )
        return await self.transcribe_full_window(pcm_bytes)

    async def transcribe_full_window(self, pcm_bytes: bytes) -> tuple[str, int]:
        return (await self.infer(pcm_bytes, short=False)).text, FULL_WINDOW_SEC

    async def transcribe(self, pcm_bytes: bytes) -> str:
        text, _window = await self.transcribe_with_window(pcm_bytes)
        return text

    async def transcribe_wav_bytes(self, wav_bytes: bytes) -> str:
        pcm = float32_to_pcm(wav_to_float32(wav_bytes))
        text, _window = await self.transcribe_full_window(pcm)
        return text

    async def probe_async(self, timeout: float = HEALTH_TIMEOUT_SEC) -> str | None:
        """:meth:`probe`, without blocking the loop."""
        import httpx

        try:
            async with self._client(timeout) as http:
                r = await http.get(f"{self.url}/health")
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            return f"{type(e).__name__}: {e}"[:_MAX_ERROR_CHARS]
        if r.status_code != 200:
            return f"HTTP {r.status_code}: {r.text[:_MAX_ERROR_CHARS]}"
        return None

    def probe(self, timeout: float = HEALTH_TIMEOUT_SEC) -> str | None:
        """``GET /health`` once, blocking (boot). None when it says ok,
        else why not."""
        import httpx

        kwargs: dict[str, Any] = {"timeout": httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT_SEC, timeout)),
                                  "follow_redirects": False, "trust_env": False}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        try:
            with egress.sync_client(**kwargs) as http:
                r = http.get(f"{self.url}/health")
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            return f"{type(e).__name__}: {e}"[:_MAX_ERROR_CHARS]
        if r.status_code != 200:
            return f"HTTP {r.status_code}: {r.text[:_MAX_ERROR_CHARS]}"
        return None


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


class FallbackWhisperClient:
    """The remote service first, this machine's Whisper when it fails.

    ``local`` is the client the boot ladder loaded (or None when it loaded
    nothing: then a failed request has nowhere to go and the turn gets
    :class:`SttUnavailableError`, the spoken explanation). ``clock`` and
    ``wall`` are for tests."""

    def __init__(
        self,
        remote: RemoteWhisperClient,
        local: Any,
        *,
        retry_sec: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.remote = remote
        self.local = local
        self.retry_sec = max(0.0, float(retry_sec))
        self._clock = clock
        self._wall = wall
        self._state = "unknown"          # unknown | ok | down
        self._retry_at = 0.0
        self._down_since: float | None = None
        self.last_error: str | None = None
        self.host_calls = 0
        self.local_calls = 0
        self.last_engine: str | None = None   # host | local: who heard the latest call

    # ─── state ──────────────────────────────────────────────────────────

    @staticmethod
    def owner_wants_host() -> bool:
        """The household's switch (``whisper_remote_enabled``, hot)."""
        return bool(settings.whisper_remote_enabled)

    def _host_usable(self) -> bool:
        # Switched off by the household: the host is left alone, unless
        # there is no local Whisper at all (then it is the only ear left;
        # engine_doc says so).
        if not self.owner_wants_host() and self.local is not None:
            return False
        return self._state != "down" or self._clock() >= self._retry_at

    @property
    def active(self) -> str:
        """Who would hear the next call: ``host`` or ``local``."""
        if self._host_usable() or self.local is None:
            return "host"
        return "local"

    def mark_down(self, why: str) -> None:
        if self._state != "down":
            self._down_since = self._wall()
            log.warning(
                "Speech recognition: the speech service at %s didn't answer (%s). "
                "%s for the next %.0f s, then the service is tried again.",
                self.remote.url, why,
                "This machine's own Whisper hears the turns" if self.local is not None
                else "There is no local Whisper to hear the turns instead",
                self.retry_sec,
            )
        self._state = "down"
        self._retry_at = self._clock() + self.retry_sec
        self.last_error = why

    def mark_up(self) -> None:
        if self._state == "down":
            log.info("Speech recognition: the speech service at %s answers again; it hears the turns", self.remote.url)
        self._state = "ok"
        self._down_since = None

    def probe_at_boot(self) -> None:
        why = self.remote.probe()
        if why is None:
            self.mark_up()
            log.info("Speech recognition: turns are heard by the speech service at %s (%s)",
                     self.remote.url, self.remote.model)
        else:
            self.mark_down(why)

    def engine_doc(self) -> dict[str, Any]:
        """For ``stt_status()["engine"]`` and the dashboard."""
        return {
            "configured": "host",
            "url": self.remote.url,
            "model": self.remote.model,
            "active": self.active,
            "host_state": self._state,
            "down_since": _iso(self._down_since),
            "last_error": self.last_error,
            "local_standby": self.local is not None,
            "host_calls": self.host_calls,
            "local_calls": self.local_calls,
            "use_host": self.owner_wants_host(),
        }

    async def refresh(self) -> None:
        """The background probe's one look (``GET /health``): marks the host
        up or down, so Health follows it between turns and a turn after an
        outage doesn't wait out a timeout first. Never raises."""
        if not self.owner_wants_host():
            return
        try:
            why = await self.remote.probe_async()
        except Exception as e:  # noqa: BLE001 - a probe never takes anything down
            why = f"{type(e).__name__}: {e}"
        if why is None:
            self.mark_up()
        else:
            self.mark_down(why)

    # What whisper_runtime() reads off the client.
    @property
    def short_window(self) -> Any:
        if self.last_engine == "local" or (self.last_engine is None and self.active == "local"):
            return getattr(self.local, "short_window", None)
        return self.remote if self.remote.short_window_capable else None

    @property
    def cpu_threads(self) -> int | None:
        if self.last_engine == "local" or (self.last_engine is None and self.active == "local"):
            return getattr(self.local, "cpu_threads", None)
        return None

    # ─── calls ──────────────────────────────────────────────────────────

    async def _call(self, host: Callable[[], Awaitable[Any]], local: Callable[[], Awaitable[Any]]) -> Any:
        if self._host_usable():
            try:
                result = await host()
            except RemoteSttError as e:
                self.mark_down(str(e))
            else:
                self.mark_up()
                self.host_calls += 1
                self.last_engine = "host"
                return result
        if self.local is None:
            raise SttUnavailableError(
                f"the speech service at {self.remote.url} isn't answering and "
                "no local Whisper is loaded"
            )
        self.local_calls += 1
        self.last_engine = "local"
        return await local()

    async def transcribe_with_window(self, pcm_bytes: bytes) -> tuple[str, int | None]:
        async def local() -> tuple[str, int | None]:
            fn = getattr(self.local, "transcribe_with_window", None)
            if callable(fn):
                return await fn(pcm_bytes)
            return await self.local.transcribe(pcm_bytes), None

        return await self._call(lambda: self.remote.transcribe_with_window(pcm_bytes), local)

    async def transcribe_full_window(self, pcm_bytes: bytes) -> tuple[str, int | None]:
        async def local() -> tuple[str, int | None]:
            fn = getattr(self.local, "transcribe_full_window", None)
            if callable(fn):
                return await fn(pcm_bytes)
            return await self.local.transcribe(pcm_bytes), None

        return await self._call(lambda: self.remote.transcribe_full_window(pcm_bytes), local)

    async def transcribe(self, pcm_bytes: bytes) -> str:
        text, _window = await self.transcribe_with_window(pcm_bytes)
        return text

    async def transcribe_wav_bytes(self, wav_bytes: bytes) -> str:
        return await self._call(
            lambda: self.remote.transcribe_wav_bytes(wav_bytes),
            lambda: self.local.transcribe_wav_bytes(wav_bytes),
        )


def build_from_settings(local: Any) -> FallbackWhisperClient:
    """The client for ``whisper_remote_url`` in front of ``local``; probes
    the service once. Raises ValueError for a URL it won't use."""
    remote = RemoteWhisperClient(
        settings.whisper_remote_url,
        model=settings.whisper_remote_model,
        language=settings.whisper_remote_language,
        timeout_sec=settings.whisper_remote_timeout_sec,
    )
    client = FallbackWhisperClient(remote, local, retry_sec=settings.whisper_remote_retry_sec)
    client.probe_at_boot()
    return client


# ─── The background probe ──────────────────────────────────────────────────

_probe_task: Any = None


async def _probe_loop() -> None:
    import asyncio

    from domovoi.clients import whisper as whisper_mod

    while True:
        await asyncio.sleep(max(5.0, float(settings.whisper_remote_probe_sec)))
        client = whisper_mod._remote
        if client is not None:
            await client.refresh()


def schedule_probe() -> Any:
    """Start the background /health probe when a remote service is
    configured (boot). None otherwise: every other install runs no task."""
    import asyncio

    global _probe_task
    from domovoi.clients import whisper as whisper_mod

    if whisper_mod._remote is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if _probe_task is not None and not _probe_task.done():
        return _probe_task
    _probe_task = loop.create_task(_probe_loop(), name="speech-service-probe")
    return _probe_task


async def cancel_probe() -> None:
    """Stop the probe (core shutdown)."""
    import asyncio

    global _probe_task
    task, _probe_task = _probe_task, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
