"""Health telemetry, the last-breath sidecar and the self-check decisions.

A satellite that dies while idle for days leaves nothing behind: the log
ring is RAM, the dashboard only says "offline", and the ring glows the
no-server orange. This module is everything that makes the NEXT death
self-explaining, kept out of ``client.py`` so it is importable and
testable on any box (the client imports sounddevice at module scope).

Three pieces, all pure or injectable:

* :class:`Sampler` — one health sample per call: process memory and fds,
  CPU, load, free memory, SoC temperature and throttle flags, uptime and
  boot id, plus whatever the client hands in about itself (mic frames per
  second, the wake loop's heartbeat age, queue depths, the WebSocket
  state). Every host fact is optional: a missing ``/proc`` file, a missing
  tool or a refused probe yields ``None``, never an exception.
* :class:`LastHealth` — the ``~/.domovoi/last-health.json`` sidecar,
  written atomically (temp file + ``os.replace``) so a power cut mid-write
  leaves the previous copy, and the ``offline_diag`` list a client fills
  with one entry per failed connect while it is disconnected. The next
  boot's ``hello`` carries the previous life's copy as ``prev_health``.
* :func:`decide` — the self-check table. Given what the sample says and
  what the client has already tried, it answers with the actions to take
  (reopen the mic, exit so systemd restarts the unit, ask the network to
  recover, reboot) and the reason for each. No side effects here: the
  client logs the reason at ERROR, writes it to the sidecar, then acts.

Nothing in this module runs as root. The probes are read-only (``/proc``,
``/sys``, ``vcgencmd``, ``nmcli -t ... device show``, ``ip route``) and
the recovery actions are the ones the sudoers file already allows.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("satellite.health")

#: A ``health`` frame is capped at this many JSON bytes; keys are dropped in
#: :data:`_DROP_ORDER` until it fits. The core refuses a larger one.
SAMPLE_MAX_BYTES = 4096
#: ``prev_health`` in the hello is capped at this many JSON bytes.
PREV_HEALTH_MAX_BYTES = 16 * 1024
#: How many failed-connect entries the sidecar keeps (oldest dropped).
OFFLINE_DIAG_MAX = 50

#: Keys dropped first when a sample is over :data:`SAMPLE_MAX_BYTES`, least
#: diagnostic first. The numbers that explain a death stay.
_DROP_ORDER = (
    "throttled_flags", "nm_state", "wifi", "log_spool", "log_ring",
    "load5", "load15", "gateway", "swap_free_kb", "mem_free_kb",
)

# Decoded bits of `vcgencmd get_throttled` (Raspberry Pi firmware).
_THROTTLE_BITS = {
    0: "under_voltage",
    1: "arm_freq_capped",
    2: "throttled",
    3: "soft_temp_limit",
    16: "under_voltage_occurred",
    17: "arm_freq_capped_occurred",
    18: "throttled_occurred",
    19: "soft_temp_limit_occurred",
}


# ─── Reading the host ─────────────────────────────────────────────────────


def _read_text(path: str | os.PathLike[str]) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, ValueError):
        return None


def _kb(value: str) -> int | None:
    try:
        return int(value.strip().split()[0])
    except (ValueError, IndexError):
        return None


def read_proc_status(path: str | os.PathLike[str] = "/proc/self/status") -> dict[str, int | None]:
    """``VmRSS``, ``VmHWM`` and ``Threads`` from ``/proc/self/status``."""
    out: dict[str, int | None] = {"rss_kb": None, "hwm_kb": None, "threads": None}
    text = _read_text(path)
    if text is None:
        return out
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "VmRSS":
            out["rss_kb"] = _kb(value)
        elif key == "VmHWM":
            out["hwm_kb"] = _kb(value)
        elif key == "Threads":
            out["threads"] = _kb(value)
    return out


def read_meminfo(path: str | os.PathLike[str] = "/proc/meminfo") -> dict[str, int | None]:
    out: dict[str, int | None] = {"mem_free_kb": None, "mem_avail_kb": None, "swap_free_kb": None}
    text = _read_text(path)
    if text is None:
        return out
    wanted = {"MemFree": "mem_free_kb", "MemAvailable": "mem_avail_kb", "SwapFree": "swap_free_kb"}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        name = wanted.get(key.strip())
        if name:
            out[name] = _kb(value)
    return out


def read_loadavg(path: str | os.PathLike[str] = "/proc/loadavg") -> tuple[float, float, float] | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        a, b, c = text.split()[:3]
        return float(a), float(b), float(c)
    except (ValueError, IndexError):
        return None


def read_uptime(path: str | os.PathLike[str] = "/proc/uptime") -> float | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return float(text.split()[0])
    except (ValueError, IndexError):
        return None


def read_boot_id(path: str | os.PathLike[str] = "/proc/sys/kernel/random/boot_id") -> str | None:
    text = _read_text(path)
    if text is None:
        return None
    value = text.strip()
    return value[:64] or None


def read_soc_temp(path: str | os.PathLike[str] = "/sys/class/thermal/thermal_zone0/temp") -> float | None:
    """Millidegrees in the sysfs file; degrees C out, one decimal."""
    text = _read_text(path)
    if text is None:
        return None
    try:
        return round(int(text.strip()) / 1000.0, 1)
    except ValueError:
        return None


def count_fds(path: str | os.PathLike[str] = "/proc/self/fd") -> int | None:
    try:
        return len(os.listdir(path))
    except OSError:
        return None


def read_system_cpu_ticks(path: str | os.PathLike[str] = "/proc/stat") -> tuple[int, int] | None:
    """``(busy, total)`` jiffies from the aggregate ``cpu`` line."""
    text = _read_text(path)
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("cpu "):
            try:
                nums = [int(x) for x in line.split()[1:]]
            except ValueError:
                return None
            if len(nums) < 4:
                return None
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            total = sum(nums)
            return total - idle, total
    return None


def read_process_cpu_ticks(path: str | os.PathLike[str] = "/proc/self/stat") -> int | None:
    """``utime + stime`` jiffies. The comm field may hold spaces and
    parentheses, so the fields are taken after the LAST ``)``."""
    text = _read_text(path)
    if text is None:
        return None
    try:
        rest = text[text.rindex(")") + 2:].split()
        # rest[0] is `state` (field 3); utime/stime are fields 14/15.
        return int(rest[11]) + int(rest[12])
    except (ValueError, IndexError):
        return None


def clock_ticks_per_sec() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK"))  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        return 100


class CpuMeter:
    """Percentages from two consecutive readings; ``None`` on the first."""

    def __init__(self, ticks_per_sec: int | None = None) -> None:
        self.ticks_per_sec = ticks_per_sec or clock_ticks_per_sec()
        self._sys: tuple[int, int] | None = None
        self._proc: int | None = None
        self._wall: float | None = None

    def update(
        self,
        sys_ticks: tuple[int, int] | None,
        proc_ticks: int | None,
        wall: float,
    ) -> tuple[float | None, float | None]:
        """Returns ``(process_pct, system_pct)``."""
        proc_pct: float | None = None
        sys_pct: float | None = None
        if self._wall is not None and wall > self._wall:
            elapsed = wall - self._wall
            if proc_ticks is not None and self._proc is not None and proc_ticks >= self._proc:
                proc_pct = round((proc_ticks - self._proc) / self.ticks_per_sec / elapsed * 100.0, 1)
            if sys_ticks is not None and self._sys is not None:
                d_busy = sys_ticks[0] - self._sys[0]
                d_total = sys_ticks[1] - self._sys[1]
                if d_total > 0 and d_busy >= 0:
                    sys_pct = round(d_busy / d_total * 100.0, 1)
        self._sys = sys_ticks
        self._proc = proc_ticks
        self._wall = wall
        return proc_pct, sys_pct


def run_tool(argv: list[str], timeout: float = 5.0) -> str | None:
    """Run a read-only tool with stdout/stderr captured and closed. ``None``
    when it is missing, fails, or times out — never an exception."""
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def parse_throttled(text: str | None) -> int | None:
    """``throttled=0x50005`` -> 0x50005."""
    if not text:
        return None
    _, _, value = text.strip().partition("=")
    try:
        return int(value.strip(), 16)
    except ValueError:
        return None


def decode_throttled(flags: int | None) -> list[str]:
    if flags is None:
        return []
    return [name for bit, name in _THROTTLE_BITS.items() if flags & (1 << bit)]


def read_throttled(run: Callable[..., str | None] = run_tool) -> int | None:
    return parse_throttled(run(["vcgencmd", "get_throttled"], timeout=5.0))


def parse_nm_state(text: str | None) -> dict[str, Any] | None:
    """``GENERAL.STATE:100 (connected)`` -> ``{"code": 100, "state": "connected"}``."""
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("GENERAL.STATE:"):
            value = line.partition(":")[2].strip()
            code_s, _, rest = value.partition(" ")
            try:
                code: int | None = int(code_s)
            except ValueError:
                code = None
            state = rest.strip("() ") or value
            return {"code": code, "state": state[:40]}
    return None


def nm_connected(nm: Any) -> bool | None:
    """NetworkManager's device state -> connected (True), not (False), or
    no opinion (None). Only 100 (activated) is connected: 110 is
    deactivating and 120 is FAILED — reading ">= 100" as connected hid
    exactly the wedged-radio state from the link checks. 0 (unknown) and
    10 (unmanaged: NetworkManager does not drive this radio) say nothing."""
    if not isinstance(nm, dict):
        return None
    code = nm.get("code")
    if not isinstance(code, int) or isinstance(code, bool):
        return None
    if code == 100:
        return True
    if code in (0, 10):
        return None
    return False


def read_nm_state(run: Callable[..., str | None] = run_tool, device: str = "wlan0") -> dict[str, Any] | None:
    return parse_nm_state(
        run(["nmcli", "-t", "-f", "GENERAL.STATE", "device", "show", device], timeout=5.0)
    )


def parse_default_gateway(text: str | None) -> str | None:
    """The ``via`` address of the first ``default`` route in ``ip route``."""
    if not text:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "default" and parts[1] == "via":
            return parts[2][:64]
    return None


def read_default_gateway(run: Callable[..., str | None] = run_tool) -> str | None:
    return parse_default_gateway(run(["ip", "route"], timeout=5.0))


#: The gateway is asked on these TCP ports. A home router runs DNS (53) and
#: a web UI (80, or 443 only); an accept OR a refusal on any of them proves
#: it is there. Only silence on all three (and no ICMP echo) says it is not.
GATEWAY_PORTS: tuple[int, ...] = (53, 80, 443)


def host_answers(
    host: str, ports: tuple[int, ...] = GATEWAY_PORTS, timeout: float = 1.0,
    connect: Callable[..., Any] = socket.create_connection,
) -> bool:
    """Does the host answer a TCP SYN on any of ``ports``? A refusal (RST)
    counts: the host is up and reachable, which is the question. Only a
    timeout or an unreachable network says no."""
    for port in ports:
        try:
            with connect((host, port), timeout=timeout):
                return True
        except ConnectionRefusedError:
            return True
        except OSError:
            continue
    return False


def icmp_echo(host: str, timeout: float = 1.0) -> bool | None:
    """One ICMP echo through an unprivileged datagram socket (Linux, when
    the process's group is in ``net.ipv4.ping_group_range``, which systemd
    opens to everyone by default). True on a reply, False on none, None
    when this host cannot ask (no ping socket here) — never an exception.

    The second opinion behind :func:`host_answers`: a router that drops
    SYNs to its own closed ports still answers ping, and "the gateway is
    silent" is what licenses a reboot."""
    import struct

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except (OSError, AttributeError, ValueError):
        return None
    try:
        sock.settimeout(timeout)
        # type 8 (echo request), code 0; the kernel fills in the checksum
        # and the identifier for a ping socket.
        sock.sendto(struct.pack("!BBHHH", 8, 0, 0, 0, 1) + b"domovoi", (host, 0))
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            sock.settimeout(left)
            data, _ = sock.recvfrom(256)
            if data[:1] == b"\x00":          # echo reply
                return True
    except socket.timeout:
        return False
    except OSError:
        return False
    finally:
        sock.close()


def probe_service(
    host: str, port: int, timeout: float = 2.0,
    connect: Callable[..., Any] = socket.create_connection,
) -> str:
    """``"accepted"``, ``"refused"`` (the host answered with an RST: the
    path to it works, the service is down) or ``"silent"``."""
    try:
        with connect((host, port), timeout=timeout):
            return "accepted"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "silent"


def service_reachable(
    host: str, port: int, timeout: float = 2.0,
    connect: Callable[..., Any] = socket.create_connection,
) -> bool:
    """Does the service accept a TCP connection? A refusal is a no here."""
    return probe_service(host, port, timeout, connect) == "accepted"


def classify_connect_failure(exc: BaseException) -> str:
    """One word for why a connect failed, for ``offline_diag``."""
    name = type(exc).__name__
    text = str(exc).lower()
    if "did not prove" in text:
        # `_verify_server_identity` failed before the socket: the server
        # was unreachable over HTTP, or answered as somebody else.
        return "identity_unverified"
    if isinstance(exc, socket.gaierror) or "name or service not known" in text or "getaddrinfo" in text:
        return "dns"
    if isinstance(exc, ConnectionRefusedError) or "refused" in text:
        return "tcp_refused"
    if isinstance(exc, TimeoutError) or "timed out" in text or "timeout" in text:
        return "tcp_timeout"
    if "ssl" in name.lower() or "tls" in text or "certificate" in text:
        return "tls"
    if "handshake" in text or "invalidstatus" in name.lower() or "invalid status" in text:
        return "handshake"
    code = getattr(exc, "code", None)
    if code is None:
        rcvd = getattr(exc, "rcvd", None)
        code = getattr(rcvd, "code", None)
    if isinstance(code, int):
        return f"close_{code}"
    if "connectionclosed" in name.lower():
        return "closed"
    if isinstance(exc, OSError):
        return f"os_{exc.errno}" if getattr(exc, "errno", None) else "os_error"
    return name.lower()[:40]


# ─── The sampler ───────────────────────────────────────────────────────────


@dataclass
class Sampler:
    """Builds one health sample per call. Paths and tools are injectable so
    the parsing is tested against fake files on any box."""

    proc_root: str = "/proc"
    sys_root: str = "/sys"
    run: Callable[..., str | None] = run_tool
    connect: Callable[..., Any] = socket.create_connection
    # The ICMP second opinion on the gateway (None: not asked at all).
    ping: Callable[..., bool | None] | None = icmp_echo
    cpu: CpuMeter = field(default_factory=CpuMeter)
    started_at: float = field(default_factory=time.monotonic)
    seq: int = 0
    # The device whose link state is asked of NetworkManager.
    wifi_device: str = "wlan0"
    # `vcgencmd` is absent on anything but a Pi, `nmcli` on a hand-built
    # unit: after TOOL_MISS_LATCH misses in a row a tool is left alone for
    # TOOL_RETRY_SEC, so such a board does not spawn a failing process every
    # minute. Not for ever: one nmcli that timed out while NetworkManager
    # was busy with a wedged radio (exactly the outage this is for) used to
    # silence the NetworkManager state for the rest of the process.
    _misses: dict[str, int] = field(default_factory=dict)
    _skip_until: dict[str, float] = field(default_factory=dict)

    TOOL_MISS_LATCH = 3
    TOOL_RETRY_SEC = 30 * 60.0

    def _p(self, rel: str) -> str:
        return os.path.join(self.proc_root, rel)

    def _ask(self, tool: str, now: float, read: Callable[[], Any]) -> Any:
        """``read()`` unless ``tool`` is resting after repeated misses."""
        if now < self._skip_until.get(tool, 0.0):
            return None
        value = read()
        if value is None:
            n = self._misses.get(tool, 0) + 1
            self._misses[tool] = n
            if n >= self.TOOL_MISS_LATCH:
                self._skip_until[tool] = now + self.TOOL_RETRY_SEC
                self._misses[tool] = 0
        else:
            self._misses[tool] = 0
        return value

    def host(self) -> dict[str, Any]:
        """The host-side half of a sample."""
        now = time.monotonic()
        status = read_proc_status(self._p("self/status"))
        mem = read_meminfo(self._p("meminfo"))
        load = read_loadavg(self._p("loadavg"))
        proc_pct, sys_pct = self.cpu.update(
            read_system_cpu_ticks(self._p("stat")),
            read_process_cpu_ticks(self._p("self/stat")),
            now,
        )
        throttled: int | None = self._ask("vcgencmd", now, lambda: read_throttled(self.run))
        nm: dict[str, Any] | None = self._ask(
            "nmcli", now, lambda: read_nm_state(self.run, self.wifi_device)
        )
        routes = self.run(["ip", "route"], timeout=5.0)
        gateway = parse_default_gateway(routes)
        return {
            "rss_kb": status["rss_kb"],
            "hwm_kb": status["hwm_kb"],
            "threads": status["threads"],
            "fds": count_fds(self._p("self/fd")),
            "cpu_pct": proc_pct,
            "sys_cpu_pct": sys_pct,
            "load1": load[0] if load else None,
            "load5": load[1] if load else None,
            "load15": load[2] if load else None,
            "mem_free_kb": mem["mem_free_kb"],
            "mem_avail_kb": mem["mem_avail_kb"],
            "swap_free_kb": mem["swap_free_kb"],
            "soc_temp_c": read_soc_temp(os.path.join(self.sys_root, "class/thermal/thermal_zone0/temp")),
            "throttled": throttled,
            "throttled_flags": decode_throttled(throttled),
            "uptime_s": read_uptime(self._p("uptime")),
            "proc_uptime_s": round(now - self.started_at, 1),
            "boot_id": read_boot_id(self._p("sys/kernel/random/boot_id")),
            "nm_state": nm,
            "gateway": gateway,
            # Whether there is a default route at all: False is a fact (the
            # link lost its lease or its association), None means `ip`
            # could not be asked. A wedged radio looks like False here and
            # gateway_ok None, never like gateway_ok False.
            "default_route": None if routes is None else gateway is not None,
        }

    def probes(
        self, gateway: str | None, core_host: str | None, core_port: int | None,
        *, probe_core: bool,
    ) -> dict[str, bool | None]:
        """The reachability half: ``None`` when there was nothing to ask.

        ``gateway_ok`` is True when the gateway answers TCP on any of
        :data:`GATEWAY_PORTS` (accept or refusal) or, failing that, an ICMP
        echo; False only when all of them were silent. ``core_host_ok`` is
        True when the core's host answered at all (accept or refusal: the
        path to it works even if the core is down), ``core_ok`` only when
        the core accepted."""
        gateway_ok: bool | None = None
        if gateway:
            gateway_ok = host_answers(gateway, connect=self.connect)
            if not gateway_ok and self.ping is not None:
                if self.ping(gateway) is True:
                    gateway_ok = True
        core_ok: bool | None = None
        core_host_ok: bool | None = None
        if probe_core and core_host and core_port:
            outcome = probe_service(core_host, core_port, connect=self.connect)
            core_ok = outcome == "accepted"
            core_host_ok = outcome != "silent"
        return {"gateway_ok": gateway_ok, "core_ok": core_ok, "core_host_ok": core_host_ok}

    def sample(
        self,
        process: dict[str, Any],
        *,
        core_host: str | None = None,
        core_port: int | None = None,
        probe_core: bool = True,
    ) -> dict[str, Any]:
        """One bounded sample: host facts, probes, then whatever the client
        says about itself (``process``: mic fps, heartbeat age, queue depths,
        WebSocket state, reconnect count, Wi-Fi link, log stats)."""
        self.seq += 1
        doc: dict[str, Any] = {"seq": self.seq, "ts": time.time()}
        try:
            doc.update(self.host())
        except Exception as e:  # noqa: BLE001 — a sampler must never crash the thread
            log.debug("health: host sample failed: %s", e)
        try:
            doc.update(self.probes(doc.get("gateway"), core_host, core_port, probe_core=probe_core))
        except Exception as e:  # noqa: BLE001
            log.debug("health: probes failed: %s", e)
            doc.setdefault("gateway_ok", None)
            doc.setdefault("core_ok", None)
            doc.setdefault("core_host_ok", None)
        for key, value in process.items():
            doc[key] = value
        return bound_sample(doc)


def bound_sample(doc: dict[str, Any], limit: int = SAMPLE_MAX_BYTES) -> dict[str, Any]:
    """Drop the least diagnostic keys until the JSON fits ``limit``."""
    out = dict(doc)
    for key in _DROP_ORDER:
        if len(json.dumps(out, separators=(",", ":"), default=str)) <= limit:
            return out
        out.pop(key, None)
    while len(json.dumps(out, separators=(",", ":"), default=str)) > limit and len(out) > 2:
        # Anything else oversized is a string somebody made long; cut the
        # longest value rather than guess which key matters.
        longest = max(out, key=lambda k: len(json.dumps(out[k], default=str)))
        if longest in ("seq", "ts"):
            break
        out.pop(longest)
    return out


# ─── The last-breath sidecar ───────────────────────────────────────────────


def write_json_atomic(path: Path, doc: dict[str, Any]) -> bool:
    """Temp file beside the target, fsync, ``os.replace``. False on any
    failure (a read-only or full config dir), never an exception."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        data = json.dumps(doc, indent=1, default=str)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError) as e:
        log.debug("health: could not write %s: %s", path, e)
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        return False


def _load_bounded(path: Path, max_bytes: int) -> dict[str, Any] | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if size > max_bytes:
        # Too big to carry whole: keep the small fields and the tail of the
        # offline diagnosis, which is what the core wants most.
        try:
            doc = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return {"truncated": True, "bytes": size}
        if not isinstance(doc, dict):
            return {"truncated": True, "bytes": size}
        diag = doc.get("offline_diag")
        slim = {k: v for k, v in doc.items() if k not in ("offline_diag", "sample")}
        slim["truncated"] = True
        if isinstance(diag, list):
            slim["offline_diag"] = diag[-10:]
        return slim
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


class LastHealth:
    """``~/.domovoi/last-health.json`` and the previous life's copy.

    At start-up :meth:`take_previous` moves whatever the last process
    left into ``last-health.prev.json`` and returns it (bounded to
    :data:`PREV_HEALTH_MAX_BYTES`), so this process starts its own file
    fresh and the previous one is carried in every ``hello`` until a
    ``ready`` arrives, when :meth:`clear_previous` deletes it.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.prev_path = self.path.with_name(self.path.stem + ".prev.json")
        self.offline_diag: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self.last_exit_reason: str | None = None
        # The self-restart budget's state (RestartBudget.summary()): how
        # many restarts the last hour spent, and any check it held back.
        self.restart_budget: dict[str, Any] | None = None
        self.last_written: float | None = None
        self.writes = 0

    def take_previous(self) -> dict[str, Any] | None:
        newest: Path | None = None
        newest_m = -1.0
        for candidate in (self.path, self.prev_path):
            try:
                m = candidate.stat().st_mtime
            except OSError:
                continue
            if m > newest_m:
                newest, newest_m = candidate, m
        if newest is None:
            return None
        doc = _load_bounded(newest, PREV_HEALTH_MAX_BYTES)
        try:
            if newest is self.path:
                os.replace(self.path, self.prev_path)
            else:
                self.path.unlink(missing_ok=True)
        except OSError as e:
            log.debug("health: could not set aside %s: %s", newest, e)
        if doc is not None:
            doc.setdefault("prev_boot_id", doc.get("boot_id"))
        return doc

    def clear_previous(self) -> None:
        try:
            self.prev_path.unlink(missing_ok=True)
        except OSError as e:
            log.debug("health: could not delete %s: %s", self.prev_path, e)

    def note_offline(self, entry: dict[str, Any]) -> None:
        self.offline_diag.append(entry)
        if len(self.offline_diag) > OFFLINE_DIAG_MAX:
            del self.offline_diag[: len(self.offline_diag) - OFFLINE_DIAG_MAX]

    def note_action(self, action: str, reason: str) -> None:
        self.actions.append({"ts": time.time(), "action": action, "reason": reason[:500]})
        if len(self.actions) > 20:
            del self.actions[: len(self.actions) - 20]

    def document(self, sample: dict[str, Any] | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "written_at": time.time(),
            "boot_id": (sample or {}).get("boot_id"),
            "sample": sample,
            "offline_diag": list(self.offline_diag),
            "actions": list(self.actions),
            "last_exit_reason": self.last_exit_reason,
            "restart_budget": self.restart_budget,
        }
        if extra:
            doc.update(extra)
        return doc

    def write(self, sample: dict[str, Any] | None, extra: dict[str, Any] | None = None) -> bool:
        ok = write_json_atomic(self.path, self.document(sample, extra))
        if ok:
            self.last_written = time.monotonic()
            self.writes += 1
        return ok


# ─── The self-check table ─────────────────────────────────────────────────


@dataclass
class CheckInput:
    """What the self-checks look at. Times are monotonic seconds."""

    now: float
    # The capture stream should be delivering frames: voice input started
    # and the voice gate open (an approval refusal closes it on purpose).
    # NOT "the stream object exists": a reopen that failed leaves it None,
    # and that is a stream that should be live and is not.
    mic_expected: bool = False
    # Seconds the callback has delivered no frames while expected; None
    # when frames are flowing (or nothing is expected).
    mic_silent_for: float | None = None
    # When the stream was last reopened for silence (None: never, or frames
    # flowed since).
    mic_reopened_at: float | None = None
    # A reopen older than this is tried again rather than counted as having
    # failed (the exit after it may have been refused by the restart budget).
    mic_reopen_retry: float = 5 * 60.0
    mic_thread_alive: bool = True
    # Age of the wake loop's heartbeat; None until the wake word is armed.
    wake_heartbeat_age: float | None = None
    # No turn open, no call, no chat, no recording: the wake loop MUST be
    # ticking, and RSS has no reason to be high.
    idle: bool = True
    # How long the client has been idle (monotonic seconds; None: unknown).
    # The heartbeat is not stamped during a turn, a chat or a call, so right
    # after a long one its age is the length of that session; the age that
    # counts is never older than the idle stretch.
    idle_for: float | None = None
    rss_mb: float | None = None
    rss_limit_mb: float = 300.0
    fds: int | None = None
    fd_limit: int = 512
    # Seconds since a socket to the core was last open; None while one is.
    no_server_for: float | None = None
    nm_connected: bool | None = None
    gateway_ok: bool | None = None
    # A default route exists (False: none; None: `ip` could not be asked).
    default_route: bool | None = None
    # The core's host answered at all (accept or refusal) at the last probe.
    core_host_ok: bool | None = None
    last_wifi_recovery: float | None = None
    # The last reboot asked for (helper) or said to be needed (no helper).
    last_reboot_note: float | None = None
    escalate_after: float = 5 * 60.0
    reboot_after: float = 15 * 60.0
    recovery_cooldown: float = 5 * 60.0
    reboot_helper: bool = False
    mic_silence_limit: float = 30.0
    heartbeat_limit: float = 60.0


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    # Which self-check decided it (the persistence filter's key).
    check: str = ""
    # What one more observation is: "tick" (every 30 s check) or "sample"
    # (a new health sample, every 60 s — RSS, fds and the network facts
    # only change when one is taken).
    basis: str = "tick"


#: Actions that end this process (the restart budget applies to them).
TERMINAL_ACTIONS = frozenset({"exit", "reboot"})


def gateway_silent(ci: CheckInput) -> bool:
    """The gateway does not answer: every probe was silent, or there is no
    default route at all while NetworkManager does not say connected (a
    radio that lost its association has no gateway to probe)."""
    if ci.gateway_ok is True:
        return False
    if ci.gateway_ok is False:
        return True
    return ci.default_route is False and ci.nm_connected is not True


def decide(ci: CheckInput) -> list[Decision]:
    """The self-check table: every check that fires, in priority order
    (the process-ending ones first). No side effects; the client filters
    the list through :class:`Persistence` and the :class:`RestartBudget`
    and acts on what is left — the first terminal action it may take ends
    the list, and one the budget refuses is logged and skipped so the
    recoverable actions behind it still run."""
    out: list[Decision] = []

    # 1 + 2: the microphone and the thread that reads it.
    if ci.mic_expected:
        if not ci.mic_thread_alive:
            out.append(Decision(
                "exit", "the mic thread is dead; nothing reads the microphone",
                check="mic_thread",
            ))
        if ci.mic_silent_for is not None and ci.mic_silent_for >= ci.mic_silence_limit:
            if (
                ci.mic_reopened_at is None
                or ci.now - ci.mic_reopened_at >= ci.mic_reopen_retry
            ):
                out.append(Decision(
                    "reopen_mic",
                    f"no mic frames for {ci.mic_silent_for:.0f}s while the capture "
                    "stream should be live; reopening the stream",
                    check="mic_silent",
                ))
            else:
                out.append(Decision(
                    "exit",
                    f"still no mic frames {ci.mic_silent_for:.0f}s after reopening the "
                    "capture stream; exiting so systemd restarts the unit",
                    check="mic_silent",
                ))
        age = ci.wake_heartbeat_age
        if age is not None and ci.idle_for is not None:
            age = min(age, ci.idle_for)
        if ci.idle and age is not None and age >= ci.heartbeat_limit:
            out.append(Decision(
                "exit",
                f"the wake loop has not ticked for {age:.0f}s while idle",
                check="wake_loop",
            ))

    # 3: memory while idle.
    if ci.idle and ci.rss_mb is not None and ci.rss_mb > ci.rss_limit_mb:
        out.append(Decision(
            "exit",
            f"RSS {ci.rss_mb:.0f} MB is above the {ci.rss_limit_mb:.0f} MB limit while idle",
            check="rss", basis="sample",
        ))

    # 4: file descriptors.
    if ci.fds is not None and ci.fds > ci.fd_limit:
        out.append(Decision(
            "exit", f"{ci.fds} open file descriptors (limit {ci.fd_limit})",
            check="fds", basis="sample",
        ))

    # 5: no server. Never touch anything while the gateway answers, or
    # while the core's own host answers (a refusal from it proves the path):
    # a core that is down is the core's problem, not this Pi's.
    if (
        ci.no_server_for is not None and ci.no_server_for >= ci.escalate_after
        and ci.gateway_ok is not True and ci.core_host_ok is not True
    ):
        silent = gateway_silent(ci)
        if ci.nm_connected is False or silent:
            due = (
                ci.last_wifi_recovery is None
                or ci.now - ci.last_wifi_recovery >= ci.recovery_cooldown
            )
            if due:
                out.append(Decision(
                    "wifi_recover",
                    f"no server for {ci.no_server_for / 60:.0f} min and the link is down "
                    f"(nm_connected={ci.nm_connected}, gateway_ok={ci.gateway_ok}, "
                    f"default_route={ci.default_route})",
                    check="no_server_link", basis="sample",
                ))
        if ci.no_server_for >= ci.reboot_after and silent:
            reason = (
                f"no server for {ci.no_server_for / 60:.0f} min and the gateway does "
                "not answer; the network stack needs a reboot"
            )
            paced = (
                ci.last_reboot_note is None
                or ci.now - ci.last_reboot_note >= ci.reboot_after
            )
            if paced:
                out.append(Decision(
                    "reboot" if ci.reboot_helper else "reboot_needed", reason,
                    check="no_server_reboot", basis="sample",
                ))

    # Terminal first, in table order; the recoverable ones after.
    return (
        [d for d in out if d.action in TERMINAL_ACTIONS]
        + [d for d in out if d.action not in TERMINAL_ACTIONS]
    )


class Persistence:
    """A decision is acted on only when the same check has decided the same
    action on ``required`` consecutive observations — two ticks (30 s
    apart) for the mic and the wake loop, two samples (60 s apart) for RSS,
    fds and the network. One stale reading (a heartbeat read in the instant
    between a long turn ending and the wake loop's first pass, a sample
    taken mid-spike) is never enough. An observation where the check does
    not decide it resets its count."""

    def __init__(self, required: int = 2) -> None:
        self.required = max(1, int(required))
        self._streak: dict[tuple[str, str], int] = {}
        self._seen_seq: dict[tuple[str, str], Any] = {}

    def filter(self, decisions: list[Decision], sample_seq: Any = None) -> list[Decision]:
        keys = set()
        out: list[Decision] = []
        for d in decisions:
            key = (d.check, d.action)
            keys.add(key)
            if d.basis == "sample":
                if key not in self._streak or self._seen_seq.get(key) != sample_seq:
                    self._streak[key] = self._streak.get(key, 0) + 1
                    self._seen_seq[key] = sample_seq
            else:
                self._streak[key] = self._streak.get(key, 0) + 1
            if self._streak[key] >= self.required:
                out.append(d)
        for key in list(self._streak):
            if key not in keys:
                del self._streak[key]
                self._seen_seq.pop(key, None)
        return out

    def streak(self, check: str, action: str) -> int:
        return self._streak.get((check, action), 0)

    def forget(self, check: str) -> None:
        """Start a check's count over (after acting on it)."""
        for key in [k for k in self._streak if k[0] == check]:
            del self._streak[key]
            self._seen_seq.pop(key, None)


class RestartBudget:
    """At most ``limit`` self-inflicted restarts (an exit for systemd, or a
    reboot) per rolling ``window_s``, of which at most ``reboot_limit``
    reboots — across process lives, so it lives on disk
    (``~/.domovoi/self-restarts.json``). Past it a self-check only logs:
    a check that fires wrongly can cost three restarts an hour, never a
    unit that restarts itself for ever. Wall-clock timestamps, because a
    reboot resets the monotonic clock; an entry stamped more than one
    window in the future (a clock that jumped back) is dropped."""

    def __init__(
        self, path: Path, *, limit: int = 3, window_s: float = 3600.0,
        reboot_limit: int = 1, clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.limit = int(limit)
        self.window_s = float(window_s)
        self.reboot_limit = int(reboot_limit)
        self.clock = clock
        self.suppressed = 0
        self.last_suppressed: dict[str, Any] | None = None
        self._entries: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        entries = doc.get("restarts") if isinstance(doc, dict) else None
        if not isinstance(entries, list):
            return []
        out = []
        for e in entries[-50:]:
            if isinstance(e, dict) and isinstance(e.get("ts"), (int, float)):
                out.append({
                    "ts": float(e["ts"]),
                    "action": str(e.get("action") or "exit")[:16],
                    "reason": str(e.get("reason") or "")[:300],
                })
        return out

    def recent(self) -> list[dict[str, Any]]:
        now = self.clock()
        return [
            e for e in self._entries
            if now - self.window_s < e["ts"] <= now + self.window_s
        ]

    def allows(self, action: str) -> bool:
        recent = self.recent()
        if len(recent) >= self.limit:
            return False
        if action == "reboot":
            if sum(1 for e in recent if e["action"] == "reboot") >= self.reboot_limit:
                return False
        return True

    def record(self, action: str, reason: str) -> bool:
        """Count one restart, on disk before it happens. False when the
        file could not be written (the count still holds in this process)."""
        self._entries = self.recent() + [
            {"ts": self.clock(), "action": action, "reason": reason[:300]}
        ]
        return write_json_atomic(self.path, {"restarts": self._entries})

    def note_suppressed(self, action: str, reason: str) -> None:
        self.suppressed += 1
        self.last_suppressed = {"ts": self.clock(), "action": action, "reason": reason[:300]}

    def summary(self) -> dict[str, Any]:
        recent = self.recent()
        return {
            "limit": self.limit,
            "reboot_limit": self.reboot_limit,
            "window_s": self.window_s,
            "recent": recent,
            "spent": len(recent) >= self.limit,
            "suppressed": self.suppressed,
            "last_suppressed": self.last_suppressed,
        }
