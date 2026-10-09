"""Satellite health telemetry, outage reports and retained logs — the
core's side (the satellite's is ``satellite/health.py``).

Three things arrive over a room's stream, each only because the core's
``ready`` listed the feature (``CORE_FEATURES`` in ``domovoi/streaming.py``):

* ``health`` — one bounded sample a minute. Validated here
  (:func:`validate_sample`: known keys, coerced types, a byte cap), kept
  in ``app.state.satellite_health[room]`` — and NOT dropped when the room
  disconnects, because the last sample before an outage is the whole
  point — and persisted to ``satellite_health`` (V022), 24 h per room.
* ``prev_health`` in the ``hello`` — the record the previous life of the
  satellite's process left behind: its last sample, the failed connects
  it saw while disconnected (``offline_diag``), the self-check actions it
  took and ``last_exit_reason``. Bounded by :func:`validate_outage`, kept
  as the room's last outage in memory and in ``satellite_outages``.
* ``log_push`` — log lines, either spooled while the satellite was
  disconnected (``offline: true``) or the last 30 s of its ring. Appended
  to a per-room file under :data:`settings.satellite_logs_dir`
  (``~/.domovoi/satellite-logs/<room>.log``), capped at 1 MB per room with
  one rotation, each line prefixed with the server's receive time and an
  ``[offline]`` marker for spooled lines. Rate-limited per room.

Everything here is best-effort: a malformed frame is dropped with a debug
line, never answered with ``error`` (which would end the Pi's turn), and
a missing table or an unwritable directory costs a warning, not a session.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: A `health` frame beyond this many JSON bytes is dropped.
SAMPLE_MAX_BYTES = 4096 + 128
#: `prev_health` beyond this many JSON bytes is slimmed to what fits.
OUTAGE_MAX_BYTES = 16 * 1024
#: Retained log: the per-room file is rotated to `.log.1` at this size.
RETAINED_LOG_MAX_BYTES = 1024 * 1024
#: One `log_push` frame carries at most this much text.
LOG_PUSH_MAX_BYTES = 32 * 1024
#: Per-room push budget: a bucket of this many bytes...
LOG_PUSH_BUCKET_BYTES = 2 * 1024 * 1024
#: ...refilled at this rate (a 32 KB push every 30 s is 64 KB a minute).
LOG_PUSH_REFILL_BYTES_PER_SEC = 64 * 1024 / 60.0
#: Outage reports kept per room.
OUTAGES_KEPT_PER_ROOM = 20
#: Health samples kept per room (hours).
HEALTH_KEEP_HOURS = 24
#: On a session's first sample and every this many after, rows older than
#: HEALTH_KEEP_HOURS are pruned (all rooms).
PRUNE_EVERY_SAMPLES = 60
#: A session writes at most one health row per this many seconds; faster
#: frames only refresh the in-memory latest sample.
HEALTH_MIN_PERSIST_SEC = 20.0
#: Every this many samples per room, one INFO line goes to the journal.
LOG_EVERY_SAMPLES = 10


# ─── validation ───────────────────────────────────────────────────────────


def _num(v: Any) -> float | int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v if v == v and abs(v) < 1e15 else None  # no NaN/inf
    return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def _str(v: Any, limit: int = 120) -> str | None:
    if isinstance(v, str):
        return v[:limit]
    return None


def _bool(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def _wifi(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict):
        return None
    return {
        "rx_mbits": _num(v.get("rx_mbits")),
        "tx_mbits": _num(v.get("tx_mbits")),
        "ssid": _str(v.get("ssid"), 64),
    }


def _nm(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict):
        return None
    return {"code": _int(v.get("code")), "state": _str(v.get("state"), 40)}


def _counts(v: Any) -> dict[str, int] | None:
    if not isinstance(v, dict):
        return None
    out = {}
    for k in ("bytes", "lines", "dropped_lines", "trims"):
        n = _int(v.get(k))
        if n is not None:
            out[k] = n
    return out or None


def _flags(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [s[:40] for s in v if isinstance(s, str)][:16]


#: Every key a `health` sample may carry, with its coercer. Unknown keys
#: are dropped: the shape is the core's to define, not the sender's.
SAMPLE_FIELDS: dict[str, Any] = {
    "seq": _int, "ts": _num,
    "rss_kb": _int, "hwm_kb": _int, "threads": _int, "fds": _int,
    "cpu_pct": _num, "sys_cpu_pct": _num,
    "load1": _num, "load5": _num, "load15": _num,
    "mem_free_kb": _int, "mem_avail_kb": _int, "swap_free_kb": _int,
    "soc_temp_c": _num, "throttled": _int, "throttled_flags": _flags,
    "uptime_s": _num, "proc_uptime_s": _num, "boot_id": lambda v: _str(v, 64),
    "nm_state": _nm, "gateway": lambda v: _str(v, 64),
    "gateway_ok": _bool, "core_ok": _bool, "core_host_ok": _bool, "default_route": _bool,
    "mic_expected": _bool, "mic_fps": _num, "mic_q": _int, "playback_q": _int,
    "send_q": _int, "wake_loop_age_s": _num, "idle": _bool,
    "ws": lambda v: _str(v, 8), "ws_down_for_s": _num, "reconnects": _int,
    "wifi": _wifi, "log_ring": _counts, "log_spool": _counts,
}


def validate_sample(ctrl: dict[str, Any]) -> dict[str, Any] | None:
    """A `health` frame -> the sample to keep, or None when it is not one
    worth keeping (not a dict, over the byte cap, nothing recognisable)."""
    if not isinstance(ctrl, dict):
        return None
    try:
        if len(json.dumps(ctrl, separators=(",", ":"))) > SAMPLE_MAX_BYTES:
            return None
    except (TypeError, ValueError):
        return None
    out: dict[str, Any] = {}
    for key, coerce in SAMPLE_FIELDS.items():
        if key in ctrl:
            out[key] = coerce(ctrl[key])
    if not any(v is not None for k, v in out.items() if k not in ("seq", "ts")):
        return None
    return out


def _bound_json(value: Any, depth: int = 0) -> Any:
    """Strings cut, lists capped, nesting limited: a report is data from
    a device, and this is the shape it may take."""
    if depth > 4:
        return None
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value if value == value and abs(value) < 1e15 else None
    if isinstance(value, list):
        return [_bound_json(v, depth + 1) for v in value[:60]]
    if isinstance(value, dict):
        return {
            str(k)[:64]: _bound_json(v, depth + 1)
            for k, v in list(value.items())[:64]
        }
    return str(value)[:200]


#: What a slimmed outage report keeps first: the fields that say why.
_OUTAGE_SCALARS = ("last_exit_reason", "prev_boot_id", "boot_id", "written_at")

#: The shape of one `offline_diag` entry (satellite/client.py
#: `_note_connect_failure`), coerced like a sample's fields.
_DIAG_FIELDS: dict[str, Any] = {
    "ts": _num, "failure": lambda v: _str(v, 40), "detail": lambda v: _str(v, 200),
    "gateway": lambda v: _str(v, 64), "gateway_ok": _bool, "core_ok": _bool,
    "core_host_ok": _bool, "default_route": _bool, "nm_state": _nm, "wifi": _wifi,
    "probe_age_s": _num, "action": lambda v: _str(v, 40),
}


def _shape_outage(doc: dict[str, Any]) -> dict[str, Any]:
    """The fields the dashboard draws, in the types it draws them: a string
    where it prints text, a number where it builds a date. Anything else a
    report carries stays as bounded JSON. A malformed record (a buggy or
    hostile client on the room's own session) then renders as blanks rather
    than taking the room's drawer down."""
    for key in _OUTAGE_SCALARS:
        if key in doc:
            value = doc[key]
            doc[key] = _num(value) if key == "written_at" else _str(value, 500)
    if "sample" in doc:
        sample = doc["sample"]
        doc["sample"] = (
            {k: c(sample[k]) for k, c in SAMPLE_FIELDS.items() if k in sample}
            if isinstance(sample, dict) else None
        )
    if "actions" in doc:
        actions = doc["actions"] if isinstance(doc["actions"], list) else []
        doc["actions"] = [
            {"ts": _num(a.get("ts")), "action": _str(a.get("action"), 40) or "",
             "reason": _str(a.get("reason"), 500) or ""}
            for a in actions if isinstance(a, dict)
        ]
    if "offline_diag" in doc:
        diag = doc["offline_diag"] if isinstance(doc["offline_diag"], list) else []
        doc["offline_diag"] = [
            {k: c(e.get(k)) for k, c in _DIAG_FIELDS.items() if k in e}
            for e in diag if isinstance(e, dict)
        ]
    return doc


def validate_outage(report: Any) -> dict[str, Any] | None:
    """`prev_health` from a hello -> the report to keep, never more than
    :data:`OUTAGE_MAX_BYTES` of JSON whatever key the sender made large,
    or None when it is not an object."""
    if not isinstance(report, dict):
        return None
    doc = _bound_json(report)
    assert isinstance(doc, dict)
    doc = _shape_outage(doc)

    def size(d: dict[str, Any]) -> int:
        return len(json.dumps(d, default=str))

    if size(doc) <= OUTAGE_MAX_BYTES:
        return doc
    # Keep what explains the outage: drop the sample, then the oldest
    # diagnosis entries, until it fits with the marker in place — when the
    # rest of the report fits at all.
    trimmed = {k: v for k, v in doc.items() if k not in ("sample", "offline_diag")}
    trimmed["truncated"] = True
    diag = doc.get("offline_diag")
    if size(dict(trimmed, offline_diag=[])) <= OUTAGE_MAX_BYTES:
        kept_diag = list(diag) if isinstance(diag, list) else []
        trimmed["offline_diag"] = kept_diag
        while kept_diag and size(trimmed) > OUTAGE_MAX_BYTES:
            kept_diag.pop(0)
        if not isinstance(diag, list):
            trimmed.pop("offline_diag")
        return trimmed
    # Something else is large (any key, up to 64 x 60 x 500 characters
    # after _bound_json): rebuild from the fields that say why, then the
    # newest actions and diagnosis entries while they fit.
    slim: dict[str, Any] = {"truncated": True}
    for key in _OUTAGE_SCALARS:
        value = doc.get(key)
        if value is None or isinstance(value, (str, int, float, bool)):
            slim[key] = value
    for key in ("actions", "offline_diag"):
        items = doc.get(key)
        if not isinstance(items, list):
            continue
        kept: list[Any] = []
        for item in reversed(items[-20:]):
            slim[key] = [item] + kept
            if size(slim) > OUTAGE_MAX_BYTES:
                break
            kept.insert(0, item)
        slim[key] = kept
    return slim


def same_outage(a: Any, b: Any) -> bool:
    """Whether two outage reports are the same record (the satellite sends
    it again on every hello until a `ready` listing `health` acknowledges
    it). Keyed on when it was written and which boot wrote it."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    if a.get("written_at") is None and b.get("written_at") is None:
        return a == b
    return all(a.get(k) == b.get(k) for k in ("written_at", "prev_boot_id", "last_exit_reason"))


# ─── what the journal says ────────────────────────────────────────────────


def _mb(kb: Any) -> str:
    return f"{kb / 1024:.0f}" if isinstance(kb, (int, float)) else "?"


def format_health_line(room: str, s: dict[str, Any]) -> str:
    wifi = s.get("wifi") or {}
    throttled = s.get("throttled")
    return (
        f"satellite health room={room} rss={_mb(s.get('rss_kb'))}MB "
        f"hwm={_mb(s.get('hwm_kb'))}MB cpu={s.get('cpu_pct')}% "
        f"temp={s.get('soc_temp_c')}C "
        f"throttled={hex(throttled) if isinstance(throttled, int) else None} "
        f"wifi_rx={wifi.get('rx_mbits')} mic_fps={s.get('mic_fps')} "
        f"fds={s.get('fds')} up={s.get('uptime_s')}s reconnects={s.get('reconnects')}"
    )


def outage_summary(report: dict[str, Any], last_n: int = 3) -> str:
    diag = report.get("offline_diag")
    tail = diag[-last_n:] if isinstance(diag, list) else []
    parts = []
    for e in tail:
        if not isinstance(e, dict):
            continue
        parts.append(
            f"{e.get('failure')} gw={e.get('gateway_ok')} core={e.get('core_ok')}"
            + (f" action={e.get('action')}" if e.get("action") else "")
        )
    actions = report.get("actions")
    acted = ""
    if isinstance(actions, list) and actions:
        last = actions[-1]
        if isinstance(last, dict):
            acted = f"; last action {last.get('action')}: {last.get('reason')}"
    return (
        f"exit={report.get('last_exit_reason')!r} prev_boot={report.get('prev_boot_id')} "
        f"offline_diag={len(diag) if isinstance(diag, list) else 0}"
        + (" [" + " | ".join(parts) + "]" if parts else "")
        + acted
    )


def is_quiet_exit(reason: Any) -> bool:
    """A requested shutdown (a restart from the dashboard, a config
    change) is the previous life ending on purpose: logged at INFO."""
    return isinstance(reason, str) and reason.startswith("shutdown:")


# ─── the retained log ─────────────────────────────────────────────────────

_ROOM_SAFE = re.compile(r"[^A-Za-z0-9_.-]")
# DOS device names: on a Windows core `NUL.log` is the null device.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(10)} | {f"lpt{i}" for i in range(10)}
)


def room_file_stem(room_id: str) -> str:
    """A file name for the room's retained log, inside the logs directory
    whatever the room id holds (`..`, `/`, `\\`, NUL, a drive letter).

    A room id that is already safe keeps its own name (`kitchen.log`).
    Anything else — a replaced character, a cut, a dot-only name, a
    Windows device name — gets a hash of the full id appended, so two
    different rooms can never share a file: `living room` and
    `living_room` are different rooms and must not read each other's
    lines."""
    safe = _ROOM_SAFE.sub("_", room_id)[:100]
    altered = (
        safe != room_id
        or not safe
        or set(safe) <= {"."}
        or safe.split(".")[0].lower() in _WINDOWS_RESERVED
    )
    if not altered:
        return safe
    digest = hashlib.sha256(room_id.encode("utf-8", errors="surrogatepass")).hexdigest()[:12]
    # Dots go too: Windows reads `con.anything` as the device, and a name
    # made of dots and underscores is no better a name for being kept.
    return f"{safe.replace('.', '_') or '_'}-{digest}"


class RetainedLog:
    """Per-room log files on disk, 1 MB each plus one rotation."""

    def __init__(self, directory: Path, max_bytes: int = RETAINED_LOG_MAX_BYTES) -> None:
        self.dir = Path(directory)
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()

    def path(self, room_id: str) -> Path:
        return self.dir / f"{room_file_stem(room_id)}.log"

    def append(self, room_id: str, text: str, *, offline: bool, now: float | None = None) -> int:
        """Append ``text`` (one or more lines) with the server's timestamp
        prefix. Returns the number of lines written."""
        stamp = datetime.fromtimestamp(now or time.time(), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        marker = " [offline] " if offline else " "
        lines = text.splitlines()
        if not lines:
            return 0
        payload = "".join(f"{stamp}{marker}{line}\n" for line in lines).encode(
            "utf-8", errors="replace"
        )
        path = self.path(room_id)
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if size + len(payload) > self.max_bytes and size > 0:
                os.replace(path, path.with_name(path.name + ".1"))
            with open(path, "ab") as f:
                f.write(payload)
        return len(lines)

    def tail(self, room_id: str, max_bytes: int) -> str:
        """The newest ``max_bytes`` of the current file, whole lines."""
        path = self.path(room_id)
        with self._lock:
            try:
                size = path.stat().st_size
                with open(path, "rb") as f:
                    if size > max_bytes:
                        f.seek(size - max_bytes)
                        data = f.read()
                        nl = data.find(b"\n")
                        data = data[nl + 1:] if nl >= 0 else data
                    else:
                        data = f.read()
            except OSError:
                return ""
        return data.decode("utf-8", errors="replace")

    def info(self, room_id: str) -> dict[str, Any]:
        path = self.path(room_id)
        rotated = path.with_name(path.name + ".1")
        with self._lock:
            try:
                size = path.stat().st_size
                mtime: float | None = path.stat().st_mtime
            except OSError:
                size, mtime = 0, None
            try:
                rotated_size = rotated.stat().st_size
            except OSError:
                rotated_size = 0
        return {
            "bytes": size,
            "rotated_bytes": rotated_size,
            "max_bytes": self.max_bytes,
            "updated_at": (
                datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat() if mtime else None
            ),
        }


class LogPushLimiter:
    """A token bucket of bytes per room: a burst for a spool drain, a
    trickle for the live push, and nothing for a device that floods."""

    def __init__(
        self,
        capacity: float = LOG_PUSH_BUCKET_BYTES,
        refill_per_sec: float = LOG_PUSH_REFILL_BYTES_PER_SEC,
    ) -> None:
        self.capacity = float(capacity)
        self.refill = float(refill_per_sec)
        self._rooms: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, room_id: str, nbytes: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            level, at = self._rooms.get(room_id, (self.capacity, now))
            level = min(self.capacity, level + (now - at) * self.refill)
            if nbytes > level:
                self._rooms[room_id] = (level, now)
                return False
            self._rooms[room_id] = (level - nbytes, now)
            return True


# ─── app.state plumbing ───────────────────────────────────────────────────


def state_dict(state: Any, name: str) -> dict[str, Any]:
    """``app.state.<name>``, created empty when the lifespan did not (the
    stream tests build a bare SimpleNamespace app)."""
    d = getattr(state, name, None)
    if not isinstance(d, dict):
        d = {}
        setattr(state, name, d)
    return d


def state_obj(state: Any, name: str, factory: Any) -> Any:
    obj = getattr(state, name, None)
    if obj is None:
        obj = factory()
        setattr(state, name, obj)
    return obj


def default_retained_log() -> RetainedLog:
    from domovoi.config import settings

    return RetainedLog(Path(settings.satellite_logs_dir))


def snapshot_digest(
    latest: dict[str, Any], outages: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Per room, WHEN the latest sample and the last outage report arrived
    (and the sample's sequence number) — what the Open admin snapshot and
    the `satellites.health` channel carry instead of the data itself."""
    out: dict[str, dict[str, Any]] = {}
    for room, entry in latest.items():
        if isinstance(entry, dict):
            sample = entry.get("sample")
            out.setdefault(str(room), {})["received_at"] = entry.get("received_at")
            out[str(room)]["seq"] = sample.get("seq") if isinstance(sample, dict) else None
    for room, entry in outages.items():
        if isinstance(entry, dict):
            out.setdefault(str(room), {})["outage_reported_at"] = entry.get("reported_at")
    return out


# ─── history ──────────────────────────────────────────────────────────────


def summarize_history(rows: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    """24 h of samples -> what the dashboard draws: count, span and the
    min/max/last of the numbers that explain a death."""
    if not rows:
        return {"samples": 0}
    keys = ("rss_kb", "cpu_pct", "soc_temp_c", "mic_fps", "fds", "mem_avail_kb")
    agg: dict[str, dict[str, Any]] = {k: {"min": None, "max": None, "last": None} for k in keys}
    throttled_any = 0
    reconnects_max: int | None = None
    for _, s in rows:
        if not isinstance(s, dict):
            continue
        for k in keys:
            v = s.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                a = agg[k]
                a["min"] = v if a["min"] is None else min(a["min"], v)
                a["max"] = v if a["max"] is None else max(a["max"], v)
                a["last"] = v
        t = s.get("throttled")
        if isinstance(t, int) and t:
            throttled_any |= t
        r = s.get("reconnects")
        if isinstance(r, int):
            reconnects_max = r if reconnects_max is None else max(reconnects_max, r)
    first, last = rows[0][0], rows[-1][0]
    return {
        "samples": len(rows),
        "from": first.isoformat() if hasattr(first, "isoformat") else str(first),
        "to": last.isoformat() if hasattr(last, "isoformat") else str(last),
        "throttled_any": throttled_any,
        "reconnects": reconnects_max,
        **agg,
    }
