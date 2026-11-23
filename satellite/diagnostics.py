"""Read-only diagnostics: a fixed set of named probes the dashboard may run.

The safe alternative to a remote terminal (owner decision 2026-11-22). The
core sends ``diag_request {probe}`` with a probe NAME only; this module
looks the name up in :data:`PROBES` and runs that probe, or answers that it
doesn't know it. There is no command input of any kind: no argument, no
path, no pattern from the core ever reaches a subprocess or a file read.

What a probe may do: read files and run tools the satellite's own account
can, with no sudo (``/proc``, ``/sys``, ``ip route``, ``iwgetid -r``,
``systemctl show``), open a TCP connection to the server and the gateway,
and look up the server's name. Each answer is cut to
:data:`MAX_TEXT_CHARS`, has its control characters taken out, and goes
through :func:`scrub`, which replaces the pairing token and anything
secret-looking (tokens, keys, passwords, long hex or base64 runs). The
Wi-Fi network's name is never sent: the network probe shows a short hash.

Kept out of ``client.py`` so it is testable on any box (the client imports
the audio stack at module scope). Everything a probe touches comes in
through :class:`Context`, so a test passes fakes.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from satellite import health

#: Every probe, in the order the dashboard lists them, with what it shows.
#: The core keeps the same names (domovoi/satellite_diagnostics.py).
PROBES: dict[str, str] = {
    "recent_logs": "the last lines of this satellite's own log",
    "network": "Wi-Fi signal (network name hashed), address, gateway, DNS and latency to the server",
    "system": "disk free, memory, load, CPU temperature and uptime",
    "audio": "the audio devices found",
    "versions": "satellite code, operating system and Python versions",
    "service": "service restarts and the last exit reason, where the system logs them",
    "mic_dsp": "the XVF3800's active DSP preset and the five values it holds (only with an XVF3800)",
}

#: Most characters one answer may carry, after scrubbing.
MAX_TEXT_CHARS = 4000
#: How many log lines `recent_logs` returns (the newest), and the most of
#: the ring it reads to find them.
LOG_LINES = 40
LOG_READ_BYTES = 32 * 1024
#: A probe name from the core is echoed back only when it looks like one.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")
# Every run below is bounded and the assignment rule starts only at a
# word's start, so the work is linear in the text: an answer of one keyword
# repeated thousands of times can't make a rule backtrack for minutes. The
# core runs the same rules (domovoi/satellite_diagnostics.py).
_SECRET_KEY = re.compile(
    r"(?i)token|password|passwd|passphrase|secret|api[_-]?key|apikey|psk|cookie|"
    r"auth|credential|private[_-]?key")
_ASSIGNMENT = re.compile(
    r"(?<![\w-])([\w-]{1,80}[\"']?)([ \t]{0,8}[=:][ \t]{0,8})(?!\[redacted\])"
    r"(\"[^\"\n]{0,512}\"|'[^'\n]{0,512}'|[^\n]{1,512})"
)


def _assignment(m: re.Match[str]) -> str:
    """key=value / key: value / "key": "value" whose key names a secret:
    the value goes to the end of the line (all of a multi-word secret)."""
    if not _SECRET_KEY.search(m.group(1)):
        return m.group(0)
    return m.group(1) + m.group(2) + "[redacted]"


_SECRET_RULES: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [redacted]"),
    (_ASSIGNMENT, _assignment),
    (re.compile(r"(?i)\b(password|passwd|passphrase|secret|psk)([ \t]{1,8})(?!\[redacted\])([^\n]{1,512})"),
     r"\1\2[redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
     "[redacted private key]"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[redacted]"),
    (re.compile(r"(?<![\w/.-])[A-Za-z0-9+/_-]{40,}={0,2}(?![\w/])"), "[redacted]"),
]


def scrub(text: str, known: tuple[str, ...] = (), limit: int = MAX_TEXT_CHARS) -> str:
    """``text`` as it may leave the device: control characters out (tab
    and newline kept), each ``known`` secret value replaced, then anything
    secret-looking, then cut to ``limit`` characters."""
    out = _CONTROL.sub("", str(text or "")[: limit * 8]).replace("\r", "")
    if len(out) > limit * 2:
        out = out[: limit * 2]      # bound the rules' work before they run
    for value in known:
        if value and len(value) >= 6:
            out = out.replace(value, "[redacted]")
    for pattern, repl in _SECRET_RULES:
        out = pattern.sub(repl, out)
    if len(out) > limit:
        out = out[: limit - 12].rstrip() + "\n[cut short]"
    return out


def ssid_hash(ssid: str, salt: str = "") -> str:
    """A network name as the probe shows it: never the name, a short keyed
    hash. The key is the house's (the server fingerprint every satellite of
    it pins), so two rooms of one house compare, and a table of common
    network names made elsewhere doesn't apply."""
    import hmac

    return "#" + hmac.new(salt.encode("utf-8", "replace"), ssid.encode("utf-8", "replace"),
                          hashlib.sha256).hexdigest()[:8]


class RequestBucket:
    """At most ``burst`` probe requests at once, refilled at ``per_sec``:
    the device's own limit, whatever the server sends (the core has its
    own). :meth:`take` is True when one may run."""

    def __init__(self, burst: int = 8, per_sec: float = 0.3,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.burst = burst
        self.per_sec = per_sec
        self._clock = clock
        self._tokens = float(burst)
        self._at = clock()

    def take(self) -> bool:
        now = self._clock()
        self._tokens = min(float(self.burst), self._tokens + (now - self._at) * self.per_sec)
        self._at = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True


@dataclass
class Context:
    """What the probes read, injected so a test can fake every piece."""

    server_url: str = ""
    log_tail: Callable[[int], str] = lambda _n: ""
    synced_sha: str | None = None
    board: str = ""
    audio_devices: Callable[[], Any] | None = None
    run: Callable[..., str | None] = health.run_tool
    connect: Callable[..., Any] = socket.create_connection
    resolve: Callable[..., Any] = socket.getaddrinfo
    local_address: Callable[[str, int], str | None] = lambda h, p: local_address(h, p)
    disk_path: str = field(default_factory=lambda: str(Path.home()))
    disk_usage: Callable[[str], Any] = shutil.disk_usage
    proc_root: str = "/proc"
    sys_root: str = "/sys"
    service_unit: str = "domovoi-satellite.service"
    started_at: float = field(default_factory=time.time)
    known_secrets: tuple[str, ...] = ()
    #: The XVF3800's DSP control (satellite/mic_dsp.XvfDsp), or None.
    mic_dsp: Any = None
    #: The key the network name is hashed with (the pinned server fingerprint).
    hash_salt: str = ""
    now: Callable[[], float] = time.time
    monotonic: Callable[[], float] = time.monotonic


def local_address(host: str, port: int) -> str | None:
    """The address this device reaches ``host`` from: a UDP "connect"
    sends nothing, it only picks the route."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((host, port))
            return str(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        return None


def _server(ctx: Context) -> tuple[str, int]:
    parsed = urlparse(ctx.server_url or "")
    port = parsed.port or (443 if parsed.scheme in ("https", "wss") else 6370)
    return parsed.hostname or "", port


def _fmt_kb(kb: int | None) -> str:
    if kb is None:
        return "unknown"
    return f"{kb / 1024:.0f} MB" if kb < 10 * 1024 * 1024 else f"{kb / 1024 / 1024:.1f} GB"


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


# ── the probes ──────────────────────────────────────────────────────────


def probe_recent_logs(ctx: Context) -> str:
    lines = (ctx.log_tail(LOG_READ_BYTES) or "").splitlines()[-LOG_LINES:]
    if not lines:
        return "the log is empty"
    text = "\n".join(lines)
    # The client logs the Wi-Fi network's name; it never leaves the device.
    ssid = (ctx.run(["iwgetid", "-r"], timeout=3.0) or "").strip()
    if len(ssid) >= 2:
        text = text.replace(ssid, "[wifi network]")
    return text


def _wifi_signal(ctx: Context) -> str | None:
    try:
        text = Path(ctx.proc_root, "net", "wireless").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 4 and parts[0].endswith(":"):
            quality = parts[2].rstrip(".")
            level = parts[3].rstrip(".")
            return f"{parts[0][:-1]}: link quality {quality}, signal {level} dBm"
    return None


def probe_network(ctx: Context) -> str:
    host, port = _server(ctx)
    out: list[str] = []
    ssid = (ctx.run(["iwgetid", "-r"], timeout=3.0) or "").strip()
    out.append(f"wifi network: {ssid_hash(ssid, ctx.hash_salt) if ssid else 'none or unknown'} (name hashed)")
    out.append(f"wifi signal: {_wifi_signal(ctx) or 'unknown'}")
    out.append(f"address: {(ctx.local_address(host, port) if host else None) or 'unknown'}")
    gateway = health.parse_default_gateway(ctx.run(["ip", "route"], timeout=3.0))
    if gateway:
        ok = health.host_answers(gateway, timeout=1.0, connect=ctx.connect)
        out.append(f"gateway: {gateway} ({'answers' if ok else 'silent'})")
    else:
        out.append("gateway: unknown")
    if not host:
        out.append("server: not configured")
        return "\n".join(out)
    t0 = ctx.monotonic()
    try:
        infos = ctx.resolve(host, port, proto=socket.IPPROTO_TCP)
        addrs = sorted({str(i[4][0]) for i in infos})[:3]
        out.append(f"dns: {host} -> {', '.join(addrs)} in {(ctx.monotonic() - t0) * 1000:.0f} ms")
    except OSError as e:
        out.append(f"dns: {host} did not resolve ({type(e).__name__})")
        return "\n".join(out)
    times: list[float] = []
    failed = ""
    for _ in range(3):
        t0 = ctx.monotonic()
        try:
            with ctx.connect((host, port), timeout=2.0):
                times.append((ctx.monotonic() - t0) * 1000)
        except OSError as e:
            failed = type(e).__name__
    if times:
        times.sort()
        out.append(f"latency to the server: {times[len(times) // 2]:.0f} ms "
                   f"(median of {len(times)} TCP connects to port {port})")
    else:
        out.append(f"latency to the server: no connection to port {port} ({failed})")
    return "\n".join(out)


def probe_system(ctx: Context) -> str:
    out: list[str] = []
    try:
        du = ctx.disk_usage(ctx.disk_path)
        out.append(f"disk free: {_fmt_kb(du.free // 1024)} of {_fmt_kb(du.total // 1024)} "
                   f"({du.free * 100 // max(du.total, 1)}% free)")
    except OSError:
        out.append("disk free: unknown")
    mem = health.read_meminfo(os.path.join(ctx.proc_root, "meminfo"))
    out.append(f"memory available: {_fmt_kb(mem.get('mem_avail_kb'))}, "
               f"swap free: {_fmt_kb(mem.get('swap_free_kb'))}")
    load = health.read_loadavg(os.path.join(ctx.proc_root, "loadavg"))
    out.append("load: " + (" ".join(f"{x:.2f}" for x in load) if load else "unknown")
               + f" ({os.cpu_count() or '?'} cores)")
    temp = health.read_soc_temp(os.path.join(ctx.sys_root, "class", "thermal", "thermal_zone0", "temp"))
    out.append(f"cpu temperature: {f'{temp:.1f} °C' if temp is not None else 'unknown'}")
    out.append(f"system uptime: {_dur(health.read_uptime(os.path.join(ctx.proc_root, 'uptime')))}")
    out.append(f"satellite running for: {_dur(ctx.now() - ctx.started_at)}")
    return "\n".join(out)


def probe_audio(ctx: Context) -> str:
    if ctx.audio_devices is None:
        return "no audio stack on this device"
    devices = ctx.audio_devices()
    rows: list[str] = []
    for i, d in enumerate(list(devices)[:24]):
        get = d.get if isinstance(d, dict) else (lambda k, _d=d: getattr(_d, k, None))
        name = str(get("name") or f"device {i}")[:80]
        ins = int(get("max_input_channels") or 0)
        outs = int(get("max_output_channels") or 0)
        kinds = ", ".join(k for k, n in (("in", ins), ("out", outs)) if n) or "none"
        rows.append(f"{i}: {name} ({kinds}; {ins} in / {outs} out)")
    return "\n".join(rows) if rows else "no audio devices found"


def probe_versions(ctx: Context) -> str:
    return "\n".join([
        f"satellite code: {ctx.synced_sha or 'as installed (never synced)'}",
        f"board: {ctx.board or 'raspberry pi'}",
        f"os: {platform.platform(terse=True)}",
        f"python: {platform.python_version()} ({sys.implementation.name})",
    ])


_SERVICE_PROPS = ("NRestarts", "Result", "ExecMainStatus", "ExecMainExitTimestamp",
                  "ActiveEnterTimestamp", "ActiveState", "SubState")


def probe_service(ctx: Context) -> str:
    out = [f"this process running for: {_dur(ctx.now() - ctx.started_at)}"]
    if sys.platform == "win32":
        out.append("restarts: not recorded on Windows (see the Logs tab)")
        return "\n".join(out)
    argv = ["systemctl", "show", ctx.service_unit, *[f"--property={p}" for p in _SERVICE_PROPS]]
    text = ctx.run(argv, timeout=3.0)
    if not text or "NRestarts=" not in text:
        text = ctx.run(["systemctl", "--user", *argv[1:]], timeout=3.0)
    if not text or "NRestarts=" not in text:
        out.append("restarts: unknown (no systemd unit this account can read)")
        return "\n".join(out)
    props = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    out.append(f"state: {props.get('ActiveState', '?')} ({props.get('SubState', '?')}) "
               f"since {props.get('ActiveEnterTimestamp') or '?'}")
    out.append(f"restarts by systemd: {props.get('NRestarts', '?')}")
    result = props.get("Result", "")
    status = props.get("ExecMainStatus", "")
    out.append("last exit: " + ("clean" if result in ("", "success") else result)
               + (f", status {status}" if status and status != "0" else "")
               + (f", at {props['ExecMainExitTimestamp']}" if props.get("ExecMainExitTimestamp") else ""))
    return "\n".join(out)


class NotAvailable(Exception):
    """The probe has nothing to read on this device."""


def probe_mic_dsp(ctx: Context) -> str:
    if ctx.mic_dsp is None:
        raise NotAvailable("no XVF3800 DSP control on this satellite")
    current = ctx.mic_dsp.read_current()
    if current is None:
        return "the array is busy with a preset change: run it again"
    out = [f"active preset: {current.get('preset') or 'unknown'}"]
    for param, values in (current.get("readback") or {}).items():
        shown = ", ".join(f"{v:g}" for v in values) if values else "no answer"
        out.append(f"{param}: {shown}")
    return "\n".join(out)


_RUNNERS: dict[str, Callable[[Context], str]] = {
    "recent_logs": probe_recent_logs,
    "network": probe_network,
    "system": probe_system,
    "audio": probe_audio,
    "versions": probe_versions,
    "service": probe_service,
    "mic_dsp": probe_mic_dsp,
}
assert set(_RUNNERS) == set(PROBES)


def run_probe(name: Any, ctx: Context) -> dict[str, Any]:
    """The ``diag_result`` for one ``diag_request``: ``{probe, ok, text,
    at}``. An unknown name is refused (``ok`` false) and nothing runs; a
    probe that fails answers ``ok`` false with the error's kind, never its
    traceback."""
    probe = name if isinstance(name, str) and _NAME_RE.match(name) else "?"
    at = round(ctx.now(), 3)
    runner = _RUNNERS.get(probe)
    if runner is None:
        return {"probe": probe, "ok": False, "text": "unknown probe: refused", "at": at}
    try:
        text, ok = runner(ctx), True
    except NotAvailable as e:
        text, ok = str(e), False
    except Exception as e:  # noqa: BLE001 — a probe never takes the client down
        text, ok = f"the probe failed ({type(e).__name__})", False
    return {"probe": probe, "ok": ok, "text": scrub(text, ctx.known_secrets), "at": at}
