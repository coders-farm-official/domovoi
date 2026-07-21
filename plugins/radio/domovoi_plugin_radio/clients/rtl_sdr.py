"""RTL-SDR FM tuner manager.

Wraps the standard ``rtl_fm | ffmpeg`` pipeline for receiving FM radio
on a USB SDR dongle and exposing the demodulated audio as an HTTP MP3
stream the satellites pull through the room MPD's ``play_url``.

Single-tuner v1 — one ``rtl_fm`` process at a time, one HTTP listener.
Tuning a different frequency kills the previous subprocess pair and
spawns a new one. The class manages one dongle; a second dongle would be
a second instance, no API change.

Hardware-gated by ``RADIO_SDR_ENABLED`` AND an ``rtl_test`` probe at
startup. Missing dongle → :meth:`probe` returns False, :meth:`tune`
raises, and the handler surfaces a friendly "FM tuner isn't enabled"
message rather than crashing. The live instance is shared through
``sdk.state["sdr_tuner"]`` (the plugin's namespaced app-state slice) —
never a module global.

Windows note: rtl-sdr needs a one-time Zadig WinUSB swap on the dongle's
USB interface; without it the OS claims the dongle as a generic DVB-T
receiver and ``rtl_fm`` can't open it.

The HTTP listener uses ffmpeg's ``-listen 1`` server mode — a tiny
single-connection HTTP server, exactly what one tuner → one connected
satellite needs, with no Icecast dependency.

Every piece of ProactorEventLoop engineering below is load-bearing on
Windows and preserved verbatim from the battle-tested implementation:
the byte bridge (no fileno() on Proactor pipes), stderr drains (64 KB
pipe buffers wedge silently), exit watchers, transport closing (loop-
closed finalizer traces), the libusb release sleep, and the bind-probe
listener wait.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
from typing import Optional

log = logging.getLogger(__name__)


def _close_proc_transport(proc: asyncio.subprocess.Process | None) -> None:
    """Explicitly close a finished subprocess's transport.

    On Python 3.12 + Windows ProactorEventLoop the transport backing an
    ``asyncio.subprocess.Process`` lingers after exit; at interpreter
    shutdown its ``__del__`` calls ``loop.call_soon`` on a closed loop
    and Python emits "Exception ignored in __del__: RuntimeError: Event
    loop is closed". Cosmetic but noisy — closing while the loop is
    alive handles it deterministically. ``_transport`` is private but
    stable since 3.7.
    """
    if proc is None:
        return
    try:
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            transport.close()
    except Exception:
        pass


class SdrTuner:
    """Manages a single ``rtl_fm | ffmpeg`` pipeline. Owns its
    subprocess pair and HTTP port. Re-entrant: ``tune`` while a pipeline
    runs cleanly kills the old one first."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        device_index: int = 0,
        http_port: int = 6391,
        stream_base: str = "http://127.0.0.1",
    ) -> None:
        self._enabled = enabled
        self._device_index = device_index
        self._http_port = http_port
        self._stream_base = stream_base
        self._rtl: asyncio.subprocess.Process | None = None
        self._ffmpeg: asyncio.subprocess.Process | None = None
        # Byte shuttle rtl_fm.stdout → ffmpeg.stdin: chaining via
        # ``stdin=rtl.stdout`` only works on POSIX loops; the Proactor
        # StreamReader has no fileno(). ~400 KB/s through the loop is
        # trivial.
        self._bridge_task: asyncio.Task | None = None
        # stderr drains: without them the 64 KB stderr=PIPE buffers fill
        # and the subprocess blocks — a silent pipeline freeze. They also
        # surface the real reason either process dies.
        self._rtl_stderr_task: asyncio.Task | None = None
        self._ffmpeg_stderr_task: asyncio.Task | None = None
        # Exit watchers: a silent crash (dongle lost, bind failure) used
        # to just produce silence; these log the returncode.
        self._rtl_exit_task: asyncio.Task | None = None
        self._ffmpeg_exit_task: asyncio.Task | None = None
        # (No per-tune URL token: port rotation hit Hyper-V firewall
        # blocks on ephemeral ports, and ?t=… cache-busters trip
        # ffmpeg's literal-path matcher. Per-tune state churn is handled
        # by mpd stop+clear+add+play instead.)
        self._current_freq_mhz: float | None = None
        self._lock = asyncio.Lock()

    # ─── Probe ──────────────────────────────────────────────────────────

    async def probe(self) -> bool:
        """Spawn ``rtl_test -t`` and check it can talk to the hardware.
        True iff the dongle is reachable; otherwise False with a clear
        disabled-with-reason log line."""
        if not self._enabled:
            log.info("sdr tuner: disabled by config (RADIO_SDR_ENABLED=false)")
            return False

        if shutil.which("rtl_test") is None:
            log.warning(
                "sdr tuner: `rtl_test` not on PATH; cannot probe for dongle. "
                "(Install rtl-sdr — on Windows also run Zadig once to swap "
                "the dongle to WinUSB.) Set RADIO_SDR_ENABLED=false to "
                "silence this."
            )
            return False

        proc: asyncio.subprocess.Process | None = None
        try:
            # `-t` runs tuner/I-Q tests and exits after a few seconds;
            # hard-bound at 5 s in case the dongle is wedged.
            proc = await asyncio.create_subprocess_exec(
                "rtl_test",
                "-d", str(self._device_index),
                "-t",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except FileNotFoundError:
            log.warning(
                "sdr tuner: rtl_test disappeared between which() and exec; "
                "aborting probe"
            )
            _close_proc_transport(proc)
            return False
        except asyncio.TimeoutError:
            log.warning(
                "sdr tuner: rtl_test hung (no response in 5 s); is the "
                "dongle wedged?"
            )
            try:
                if proc is not None:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception:
                pass
            _close_proc_transport(proc)
            return False
        finally:
            # Even on success, close the transport now rather than at
            # interpreter shutdown (finalizers race the closed loop).
            if proc is not None and proc.returncode is not None:
                _close_proc_transport(proc)

        # rtl_test prints "Found N device(s)" to stderr on success and
        # "No supported devices found" when not.
        text = (stderr or b"").decode(errors="replace")
        if "No supported devices found" in text or "usb_open error" in text:
            log.info(
                "sdr tuner: probe found no usable dongle at device index %d",
                self._device_index,
            )
            return False

        log.info(
            "sdr tuner: probe ok (device=%d, http port=%d)",
            self._device_index, self._http_port,
        )
        return True

    # ─── tune / stop ────────────────────────────────────────────────────

    async def tune(self, frequency_mhz: float) -> str:
        """Tune to ``frequency_mhz`` and return the HTTP URL the caller
        feeds to the room MPD. Raises ``RuntimeError`` when disabled or
        the subprocess pair fails to start."""
        if not self._enabled:
            raise RuntimeError("sdr tuner: disabled by config")
        if not (87.0 <= frequency_mhz <= 108.0):
            raise ValueError(f"frequency {frequency_mhz} mhz out of FM band 87-108")

        async with self._lock:
            await self._stop_locked()
            # libusb takes a moment to release the dongle handle after
            # rtl_fm dies; without this the second spawn can fail with
            # "claim_interface error" silently.
            await asyncio.sleep(0.3)
            await self._start_locked(frequency_mhz)
            # Wait for ffmpeg's listening socket before handing the URL
            # to MPD: MPD tries exactly once, and a fetch inside the
            # ~10-50 ms pre-bind window gets TCP RST → the whole tune
            # ends in silence.
            await self._wait_for_listener_bound(timeout=5.0)
            self._current_freq_mhz = frequency_mhz
            return self.stream_url

    async def _wait_for_listener_bound(self, timeout: float) -> bool:
        """Poll until something is listening on the stream port.

        Implemented via attempted ``bind()`` rather than a TCP connect —
        connecting would consume ffmpeg's ``-listen 1`` slot. Bind
        succeeds (port free → ffmpeg not ready, keep waiting) or fails
        with EADDRINUSE (ffmpeg is listening).
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                test_sock.bind(("0.0.0.0", self._http_port))
                test_sock.close()
                await asyncio.sleep(0.05)
                continue
            except OSError:
                test_sock.close()
                return True
        log.warning(
            "sdr tuner: ffmpeg listener didn't bind within %.1fs — "
            "MPD play_url may race", timeout,
        )
        return False

    async def stop(self) -> None:
        """Kill the current tuner pipeline (idempotent)."""
        async with self._lock:
            await self._stop_locked()
            self._current_freq_mhz = None

    @property
    def stream_url(self) -> str:
        """URL ffmpeg's ``-listen 1`` instance serves. The bind is
        always 0.0.0.0:<port>; the host part here is what MPD dials, so
        it must resolve from MPD's network perspective (a LAN hostname
        when MPD runs in Docker — see RADIO_SDR_STREAM_BASE help).

        Deliberately no query string: ffmpeg's built-in HTTP server
        matches the literal path and silently rejects cache-buster
        params.
        """
        base = self._stream_base.rstrip("/")
        return f"{base}:{self._http_port}/fm.mp3"

    @property
    def current_frequency_mhz(self) -> Optional[float]:
        return self._current_freq_mhz

    # ─── Internal subprocess management ─────────────────────────────────

    async def _start_locked(self, frequency_mhz: float) -> None:
        """Start the rtl_fm | ffmpeg pair. Caller holds ``self._lock``."""
        freq_hz = int(frequency_mhz * 1_000_000)
        # rtl_fm wideband FM, 200 kHz sample rate, 48 kHz output — the
        # standard "listen to commercial FM" config. Output: s16 PCM.
        rtl_cmd = [
            "rtl_fm",
            "-d", str(self._device_index),
            "-M", "wbfm",
            "-f", str(freq_hz),
            "-s", "200000",
            "-r", "48000",
            "-",                             # stdout
        ]
        # ffmpeg consumes the raw PCM, encodes MP3, serves it on the
        # configured port. Default loglevel kept (NOT error) so the
        # startup banner makes unhealthy pipelines diagnosable.
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-f", "s16le",
            "-ar", "48000",
            "-ac", "1",
            "-i", "-",                       # stdin from rtl_fm's stdout
            "-c:a", "libmp3lame",
            "-b:a", "128k",
            "-f", "mp3",
            "-listen", "1",
            f"http://0.0.0.0:{self._http_port}/fm.mp3",
        ]

        try:
            self._rtl = await asyncio.create_subprocess_exec(
                *rtl_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise RuntimeError("sdr tuner: rtl_fm not on PATH") from None

        try:
            self._ffmpeg = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            self._rtl.kill()
            await self._rtl.wait()
            self._rtl = None
            raise RuntimeError("sdr tuner: ffmpeg not on PATH") from None

        self._bridge_task = asyncio.create_task(
            self._bridge_stdout_to_stdin(), name="sdr-pcm-bridge"
        )
        self._rtl_stderr_task = asyncio.create_task(
            self._drain_stderr(self._rtl.stderr, "rtl_fm"), name="rtl_fm-stderr"
        )
        self._ffmpeg_stderr_task = asyncio.create_task(
            self._drain_stderr(self._ffmpeg.stderr, "ffmpeg"), name="ffmpeg-stderr"
        )
        self._rtl_exit_task = asyncio.create_task(
            self._watch_process_exit(self._rtl, "rtl_fm"), name="rtl_fm-exit-watch"
        )
        self._ffmpeg_exit_task = asyncio.create_task(
            self._watch_process_exit(self._ffmpeg, "ffmpeg"),
            name="ffmpeg-exit-watch",
        )

        log.info(
            "sdr tuner: tuned to %.1f MHz, serving %s",
            frequency_mhz, self.stream_url,
        )

    async def _watch_process_exit(
        self, proc: asyncio.subprocess.Process, label: str
    ) -> None:
        """Await ``proc.wait()`` and log the exit. Cancelled silently by
        ``_stop_locked`` during normal teardown; anything else means the
        process died on its own."""
        try:
            rc = await proc.wait()
            log.warning(
                "sdr tuner: %s exited unexpectedly (returncode=%s)", label, rc
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("sdr tuner: %s exit-watcher failed: %s", label, e)

    async def _drain_stderr(self, stream, label: str) -> None:
        """Read a subprocess's stderr line-by-line into the log."""
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    log.info("sdr tuner: %s: %s", label, text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("sdr tuner: %s stderr drain failed: %s", label, e)

    async def _bridge_stdout_to_stdin(self) -> None:
        """Shuttle PCM bytes from rtl_fm to ffmpeg. Exits cleanly when
        either side closes; logs unexpected errors, never re-raises
        (cancellation by ``_stop_locked`` is normal)."""
        rtl = self._rtl
        ffm = self._ffmpeg
        if rtl is None or ffm is None or rtl.stdout is None or ffm.stdin is None:
            log.warning("sdr tuner: bridge task started but proc handles missing")
            return
        total_bytes = 0
        reason = "unknown"
        try:
            while True:
                chunk = await rtl.stdout.read(65536)
                if not chunk:
                    reason = "rtl_fm stdout EOF"
                    break
                total_bytes += len(chunk)
                ffm.stdin.write(chunk)
                await ffm.stdin.drain()
        except asyncio.CancelledError:
            reason = "cancelled by _stop_locked"
            raise
        except (BrokenPipeError, ConnectionResetError) as e:
            # ffmpeg exited (e.g. listener closed by a satellite
            # disconnect) — normal end-of-stream.
            reason = f"ffmpeg stdin broken: {type(e).__name__}"
        except Exception as e:
            reason = f"unexpected: {e}"
            log.warning("sdr tuner: bridge task error: %s", e)
        finally:
            log.info(
                "sdr tuner: bridge task exiting (%s, pumped %d bytes)",
                reason, total_bytes,
            )
            try:
                if ffm.stdin is not None and not ffm.stdin.is_closing():
                    ffm.stdin.close()
            except Exception:
                pass

    async def _stop_locked(self) -> None:
        """Tear down the subprocess pair. Caller holds ``self._lock``.
        The byte bridge is cancelled FIRST so it doesn't keep writing
        into a closing ffmpeg stdin."""
        for task_attr in (
            "_bridge_task",
            "_rtl_stderr_task",
            "_ffmpeg_stderr_task",
            "_rtl_exit_task",
            "_ffmpeg_exit_task",
        ):
            task = getattr(self, task_attr, None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                setattr(self, task_attr, None)

        for proc_name in ("_ffmpeg", "_rtl"):
            proc = getattr(self, proc_name)
            if proc is None:
                continue
            if proc.returncode is None:
                try:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass
                except ProcessLookupError:
                    pass
            # Close the transport while the loop is alive; retunes would
            # otherwise accumulate lingering transports that all throw
            # finalizer warnings at shutdown.
            _close_proc_transport(proc)
            setattr(self, proc_name, None)
