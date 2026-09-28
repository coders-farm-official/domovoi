"""Command recordings kept for tuning when to stop listening — opt-in per room.

Why this exists. The early-endpointing design (design notes 2026-09-28)
has two options that decide from the SOUND when a person has finished
speaking: an end-of-turn model, and a small streaming recognizer for short
commands. Both can cut somebody off mid-sentence, and the only honest way
to know how often is to replay real household commands through them. The
owner's decision the same day: collect that audio opt-in per room, keep
each recording 14 days, and let only an admin turn it on or read it.

What is kept, and when:

* **Only for a room an admin opted in** (a row in ``command_capture_rooms``,
  V016) **and only while an admin credential exists** — ``--reset-admin``
  pauses every room until the server is claimed again. Every room starts
  off, and so does every room created later.
* **Only command turns**: triggers ``wake_word`` and ``followup``, the two
  the design would ever end early (option B, condition 1). Never a
  wake-word training clip (``wake_clip`` — its own pipeline, never routed),
  never a chat-mode turn (``chat``, or any turn the conversational bypass
  answers: open conversation, not a command, and the design never ends
  those early), never a barge-in (the capture opens while the satellite is
  still talking, so it carries the reply's own audio, and the design never
  commits on one), never ``push_to_talk`` (a button ends it, not silence),
  and never a turn captured while the room is in a drop-in call (the
  speaker is playing the OTHER room, whose people never opted in).
* **Only turns that reached the router** with a transcript. A blank
  capture, a self-echo, a turn whose speech-to-text is down, is not a
  command anyone gave.

Each recording is two files under ``<command_captures_dir>/<room>/``:
``<id>.wav`` (the full capture exactly as the satellite streamed it, 16 kHz
mono int16) and ``<id>.json`` (the sidecar: room, time, trigger, duration,
why the capture ended and its frame counts as the satellite reported them,
the room's listen settings, the final transcript, the handler and path it
routed to, and the turn's stage timings; later an admin's label). No
person, no session, no voice embedding: who spoke is not what is being
tuned. Files are written owner-only (0600 in a 0700 directory on POSIX).

Nothing serves that directory. The web process reads it only through the
admin routes in ``web/backend/api/captures.py``, and :func:`root_problem`
refuses to write at all when the directory sits inside (or around) one of
the directories something DOES serve — a media library, the sounds or
wake-model channels, the repo.

What removes them: the core's pruner (``workers/command_capture_pruner.py``)
deletes anything past :func:`retention_days`, enforces the disk cap oldest
first, and removes whatever is left for a room that is no longer opted in;
opting a room out deletes its recordings in the same request; an admin can
delete one at a time; retiring a room deletes them too. Deletion only ever
touches capture-shaped names (``<id>.wav`` / ``<id>.json`` / their
``.tmp``), so a misconfigured directory can never cost anyone a file this
module did not write.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import secrets
import sys
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import session_scope

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
PCM_BYTES_PER_MS = 32

# The triggers whose captures may be kept (see the module docstring for why
# each of the others is left out).
CAPTURE_TRIGGERS = frozenset({"wake_word", "followup"})

# What an admin can say about a recording, and how the dashboard says it.
LABELS: dict[str, str] = {
    "cut_off": "cut off too early",
    "fine": "fine",
    "waited_too_long": "waited too long",
}

# The owner's retention, and its ceiling.
MAX_RETENTION_DAYS = 14

SIDECAR_VERSION = 1

# <UTC yyyymmddThhmmss><ms>Z-<8 hex>: sorts chronologically as a string,
# carries its own time (retention needs no file read), and never contains
# a path separator or a dot.
_ID_RE = re.compile(r"^(\d{8}T\d{6})(\d{3})Z-[0-9a-f]{8}$")

# A room id that is already a boring directory name is used as-is; anything
# else gets a hashed name that starts with "_", which no as-is name can.
_ROOM_DIR_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

# What the satellite may report about how its capture ended.
_END_REASON_RE = re.compile(r"^[a-z_]{1,40}$")
_CAPTURE_STAT_KEYS = (
    "frames",
    "voiced_frames",
    "trailing_silent_frames",
    "silence_limit_frames",
)

# A half-written file older than this is debris from a crash.
_STALE_TMP_SEC = 3600.0


# ─── Naming and containment ──────────────────────────────────────────────


def captures_root() -> Path:
    return Path(settings.command_captures_dir).expanduser().resolve(strict=False)


def room_dir_name(room_id: str) -> str:
    if _ROOM_DIR_RE.fullmatch(room_id) and room_id not in _WINDOWS_RESERVED:
        return room_id
    return "_" + hashlib.sha256(room_id.encode("utf-8")).hexdigest()[:16]


def room_dir(room_id: str) -> Path:
    """The room's directory, directly inside the root. A room directory that
    is a symlink to somewhere else resolves outside and is refused."""
    root = captures_root()
    d = (root / room_dir_name(room_id)).resolve(strict=False)
    if d.parent != root:
        raise ValueError(f"capture directory for {room_id!r} escapes {root}")
    return d


def is_capture_id(capture_id: str) -> bool:
    return bool(_ID_RE.fullmatch(capture_id or ""))


def new_capture_id(now: datetime) -> str:
    now = now.astimezone(timezone.utc)
    return (
        now.strftime("%Y%m%dT%H%M%S")
        + f"{now.microsecond // 1000:03d}Z-"
        + secrets.token_hex(4)
    )


def capture_time(capture_id: str) -> datetime | None:
    m = _ID_RE.fullmatch(capture_id or "")
    if not m:
        return None
    try:
        base = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    return base.replace(tzinfo=timezone.utc) + timedelta(milliseconds=int(m.group(2)))


def capture_paths(room_id: str, capture_id: str) -> tuple[Path, Path] | None:
    """``(wav, sidecar)`` for one recording, or None when the id is not a
    capture id or either path would land anywhere but the room directory."""
    if not is_capture_id(capture_id):
        return None
    try:
        d = room_dir(room_id)
    except ValueError:
        return None
    wav = (d / f"{capture_id}.wav").resolve(strict=False)
    side = (d / f"{capture_id}.json").resolve(strict=False)
    if wav.parent != d or side.parent != d:
        return None
    return wav, side


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


# Directories that something serves (the dashboard's file libraries and
# caches, the satellites' sound and wake-model channels, the repo the
# static mount and the satellite code channel read from) or that belong to
# the plugin sandbox. The recordings must be in none of them, and none of
# them may be inside the recordings.
_SERVED_SETTINGS = (
    "music_dir",
    "documents_dir",
    "pictures_dir",
    "podcasts_dir",
    "audiobooks_dir",
    "cover_art_dir",
    "video_posters_dir",
    "image_thumbs_dir",
    "sounds_dir",
    "wake_models_dir",
    "wake_clips_dir",
    "voice_models_dir",
    "repo_dir",
)


def root_problem() -> str | None:
    """Why the configured directory must not hold recordings, or None.

    Checked before every write and every sweep, not once at boot: the
    setting is read live, and a wrong answer here is household speech on a
    route that hands files to anybody on the network."""
    raw = str(settings.command_captures_dir or "").strip()
    if not raw or not Path(raw).expanduser().is_absolute():
        # The core writes and the dashboard deletes; each would resolve a
        # relative path against its own working directory, and an opt-out
        # would then delete nothing the core had kept.
        return f"COMMAND_CAPTURES_DIR must be an absolute path (it is {raw!r})"
    root = captures_root()
    if root == Path(root.anchor) or len(root.parts) <= 1:
        return f"{root} is a whole filesystem root"
    home = Path.home().expanduser().resolve(strict=False)
    config_dir = (home / ".domovoi").resolve(strict=False)
    if root in (home, config_dir):
        return f"{root} is a whole home or config directory — give recordings a directory of their own"
    served: list[tuple[str, Path]] = [
        (attr, Path(str(getattr(settings, attr))).expanduser().resolve(strict=False))
        for attr in _SERVED_SETTINGS
        if getattr(settings, attr, None)
    ]
    served.append(("the plugin directory", (config_dir / "plugins").resolve(strict=False)))
    for name, p in served:
        if _within(root, p):
            return f"{root} is inside {name} ({p}), which is served or shared"
        if _within(p, root):
            return f"{name} ({p}) is inside {root}"
    return None


# ─── What the turn hands over ─────────────────────────────────────────────


def eligible(trigger: str | None, *, in_call: bool) -> bool:
    """May a turn with this trigger be kept at all (before asking the
    database whether its room is opted in)?"""
    return trigger in CAPTURE_TRIGGERS and not in_call


def capture_meta(frame: dict[str, Any], *, in_call: bool) -> dict[str, Any]:
    """What an ``utterance_end`` frame says about its capture, cleaned: the
    satellite's end reason (a short snake_case word, else None) and its
    frame counts (non-negative ints, else left out), plus the wall-clock
    time and whether the room was in a call. An old satellite sends none of
    the fields; its recordings say ``end_reason: null``."""
    reason = frame.get("end_reason")
    if not (isinstance(reason, str) and _END_REASON_RE.fullmatch(reason)):
        reason = None
    stats: dict[str, int] = {}
    for key in _CAPTURE_STAT_KEYS:
        v = frame.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10_000_000:
            stats[key] = v
    return {
        "captured_at": datetime.now(timezone.utc),
        "end_reason": reason,
        "capture": stats or None,
        "in_call": bool(in_call),
    }


def listen_settings(config: dict[str, Any] | None) -> dict[str, Any]:
    """The ``listen.*`` keys of a satellite's reported config (config_status),
    unprefixed: the silence timeout, VAD level and record cap that ended
    the capture. Empty when the satellite hasn't reported."""
    out: dict[str, Any] = {}
    for key, value in (config or {}).items():
        if (
            isinstance(key, str)
            and key.startswith("listen.")
            and isinstance(value, (int, float, str, bool))
        ):
            out[key[len("listen."):]] = value
    return out


def build_sidecar(
    *,
    room_id: str,
    pcm_len: int,
    trigger: str | None,
    meta: dict[str, Any],
    listen: dict[str, Any],
    transcript: str,
    matched_handler: str | None,
    matched_path: str | None,
    interrupted: bool,
    timings: dict[str, Any] | None,
) -> dict[str, Any]:
    captured_at: datetime = meta.get("captured_at") or datetime.now(timezone.utc)
    return {
        "version": SIDECAR_VERSION,
        "id": new_capture_id(captured_at),
        "room_id": room_id,
        "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "trigger": trigger,
        "duration_ms": max(0, int(pcm_len)) // PCM_BYTES_PER_MS,
        "sample_rate": SAMPLE_RATE,
        "end_reason": meta.get("end_reason"),
        "capture": meta.get("capture"),
        "listen": listen,
        "transcript": transcript,
        "matched_handler": matched_handler,
        "matched_path": matched_path,
        "interrupted": bool(interrupted),
        "timings": timings or {},
        "label": None,
        "labeled_at": None,
    }


# ─── The opt-in (V016) ────────────────────────────────────────────────────


def is_missing_table(exc: Exception) -> bool:
    orig = getattr(exc, "orig", None)
    if type(orig).__name__ == "UndefinedTableError":
        return True
    msg = str(exc).lower()
    return "command_capture_rooms" in msg and "does not exist" in msg


async def room_opted_in(room_id: str) -> bool:
    """True when ``room_id`` is opted in AND an admin credential exists.
    Any failure — V016 not applied, the database away — answers False: when
    in doubt, nothing is kept."""
    try:
        async with session_scope() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT 1 FROM command_capture_rooms "
                        "WHERE room_id = :r "
                        "AND EXISTS (SELECT 1 FROM admin_auth WHERE id = 1)"
                    ),
                    {"r": room_id},
                )
            ).first()
    except Exception as e:  # noqa: BLE001 — every failure means "don't keep it"
        log.debug("command captures: opt-in check failed for %s: %s", room_id, e)
        return False
    return row is not None


async def opted_in_rooms(session: Any | None = None) -> dict[str, datetime]:
    """``{room_id: enabled_at}`` for every opted-in room; {} when V016 is not
    applied. Any other failure raises, so a sweep that relies on the answer
    never mistakes "the database is away" for "nobody is opted in"."""

    async def _read(s: Any) -> dict[str, datetime]:
        rows = await s.execute(
            text("SELECT room_id, enabled_at FROM command_capture_rooms")
        )
        return {r[0]: r[1] for r in rows.all()}

    try:
        if session is not None:
            return await _read(session)
        async with session_scope() as s:
            return await _read(s)
    except Exception as e:
        if is_missing_table(e):
            return {}
        raise


async def opted_in_rooms_or_empty() -> dict[str, datetime]:
    """For display only (the satellites list's "recording" marker): the
    rooms that record right now — opted in AND an admin credential exists,
    the same two conditions :func:`room_opted_in` gives the core, so the
    marker neither hides a room that records nor claims one that
    ``--reset-admin`` paused. {} on any failure (and the core, asking the
    same database, keeps nothing then either)."""
    try:
        async with session_scope() as s:
            rows = await s.execute(
                text(
                    "SELECT room_id, enabled_at FROM command_capture_rooms "
                    "WHERE EXISTS (SELECT 1 FROM admin_auth WHERE id = 1)"
                )
            )
            return {r[0]: r[1] for r in rows.all()}
    except Exception as e:  # noqa: BLE001
        log.debug("command captures: opt-in list unavailable: %s", e)
        return {}


async def forget_room(room_id: str) -> int:
    """Opt ``room_id`` out and delete what was kept for it — the retire-a-
    room path. Row first, files second (see :func:`keep` for why that
    order). Returns the number of recordings deleted."""
    try:
        async with session_scope() as s:
            await s.execute(
                text("DELETE FROM command_capture_rooms WHERE room_id = :r"),
                {"r": room_id},
            )
    except Exception as e:  # noqa: BLE001
        if not is_missing_table(e):
            log.warning("command captures: could not opt %s out: %s", room_id, e)
    return await asyncio.to_thread(delete_room, room_id)


# ─── Files ────────────────────────────────────────────────────────────────


def _private_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass


def _write_private(path: Path, data: bytes) -> None:
    """Write-then-rename, owner-only from the first byte."""
    tmp = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def _dump(sidecar: dict[str, Any]) -> bytes:
    return (json.dumps(sidecar, ensure_ascii=False, indent=1, default=str) + "\n").encode("utf-8")


class CaptureDirRefused(RuntimeError):
    """The configured directory is one :func:`root_problem` refuses."""


def write_capture(room_id: str, pcm: bytes, sidecar: dict[str, Any]) -> str:
    """Write one recording (WAV first, sidecar last, so a listing never sees
    a sidecar whose audio isn't there yet). Returns its id."""
    problem = root_problem()
    if problem:
        raise CaptureDirRefused(problem)
    _private_dir(captures_root())
    d = room_dir(room_id)
    _private_dir(d)
    capture_id = str(sidecar["id"])
    paths = capture_paths(room_id, capture_id)
    if paths is None:
        raise ValueError(f"bad capture id {capture_id!r}")
    wav, side = paths
    _write_private(wav, wav_bytes(pcm))
    _write_private(side, _dump(sidecar))
    return capture_id


def _capture_files(d: Path) -> Iterator[tuple[str, Path]]:
    """Every capture-shaped file in a room directory, with its id."""
    try:
        entries = list(d.iterdir())
    except OSError:
        return
    for p in entries:
        name = p.name
        stem = name
        for suffix in (".tmp",):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        if stem.endswith(".wav") or stem.endswith(".json"):
            cid = stem.rsplit(".", 1)[0]
            if is_capture_id(cid) and p.is_file() and not p.is_symlink():
                yield cid, p


def _room_dirs(root: Path) -> Iterator[Path]:
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for p in entries:
        if p.is_dir() and not p.is_symlink():
            yield p


def _unlink(p: Path) -> bool:
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        log.warning("command captures: could not delete %s: %s", p.name, e)
        return False


def _rmdir_if_empty(d: Path) -> None:
    try:
        d.rmdir()
    except OSError:
        pass


def _delete_ids(d: Path, ids: set[str]) -> int:
    """Delete every file of the given captures in ``d``; returns how many
    recordings lost their audio."""
    gone = 0
    for cid, p in list(_capture_files(d)):
        if cid in ids:
            if _unlink(p) and p.name.endswith(".wav"):
                gone += 1
    return gone


def delete_room(room_id: str) -> int:
    """Delete every recording kept for ``room_id`` and its directory when
    that leaves it empty. Returns the number of recordings deleted."""
    try:
        d = room_dir(room_id)
    except ValueError:
        return 0
    if not d.is_dir():
        return 0
    ids = {cid for cid, _ in _capture_files(d)}
    gone = _delete_ids(d, ids)
    _rmdir_if_empty(d)
    return gone


def delete_capture(room_id: str, capture_id: str) -> bool:
    paths = capture_paths(room_id, capture_id)
    if paths is None:
        return False
    wav, side = paths
    had_audio = _unlink(wav)
    had_side = _unlink(side)
    for p in (wav, side):
        _unlink(p.with_name(p.name + ".tmp"))
    return had_audio or had_side


def retention_days() -> int:
    try:
        days = int(settings.command_capture_retention_days)
    except (TypeError, ValueError):
        days = MAX_RETENTION_DAYS
    return max(1, min(MAX_RETENTION_DAYS, days))


def cap_bytes() -> int:
    try:
        mb = int(settings.command_capture_max_mb)
    except (TypeError, ValueError):
        mb = 500
    return max(1, mb) * 1024 * 1024


@dataclass
class _Rec:
    cid: str
    when: datetime
    room_dir: Path
    wav: Path | None = None
    side: Path | None = None
    size: int = 0


def _inventory(root: Path) -> list[_Rec]:
    """Every recording under the root, oldest first, with its size on disk
    (audio + sidecar; stray .tmp files are not recordings and are left to
    :func:`purge_expired`)."""
    recs: dict[tuple[Path, str], _Rec] = {}
    for d in _room_dirs(root):
        for cid, p in _capture_files(d):
            if p.name.endswith(".tmp"):
                continue
            when = capture_time(cid)
            if when is None:
                continue
            rec = recs.setdefault((d, cid), _Rec(cid=cid, when=when, room_dir=d))
            try:
                rec.size += p.stat().st_size
            except OSError:
                pass
            if p.suffix == ".wav":
                rec.wav = p
            else:
                rec.side = p
    return sorted(recs.values(), key=lambda r: (r.when, r.cid))


def _delete_rec(rec: _Rec) -> bool:
    """Delete one inventoried recording's files by the paths the inventory
    already holds (no second directory listing per recording, which made a
    large cap reduction quadratic). True when it still had its audio."""
    had_audio = rec.wav is not None and _unlink(rec.wav)
    if rec.side is not None:
        _unlink(rec.side)
    return had_audio


def purge_expired(now: datetime | None = None) -> int:
    """Delete recordings older than :func:`retention_days`, recordings missing
    half their pair for more than an hour, and stale half-written files.
    Returns the number of recordings deleted.

    A directory :func:`root_problem` refuses is still held to the retention
    — recording stops there, forgetting must not (the directory can turn
    unsafe AFTER it filled: a media library later configured around it) —
    but only for complete pairs whose sidecar names its own id and room,
    i.e. files this module provably wrote; nothing else there is touched."""
    now = now or datetime.now(timezone.utc)
    root = captures_root()
    cutoff = now - timedelta(days=retention_days())
    if root_problem():
        return _purge_expired_verified(root, cutoff)
    orphan_cutoff = now - timedelta(seconds=_STALE_TMP_SEC)
    gone = 0
    for rec in _inventory(root):
        expired = rec.when < cutoff
        orphan = (rec.wav is None or rec.side is None) and rec.when < orphan_cutoff
        if expired or orphan:
            gone += int(_delete_rec(rec))
    stale_before = time.time() - _STALE_TMP_SEC
    for d in _room_dirs(root):
        for _cid, p in list(_capture_files(d)):
            if p.name.endswith(".tmp"):
                try:
                    if p.stat().st_mtime < stale_before:
                        _unlink(p)
                except OSError:
                    pass
        _rmdir_if_empty(d)
    return gone


def _purge_expired_verified(root: Path, cutoff: datetime) -> int:
    """:func:`purge_expired` for a refused directory: expired, complete,
    self-describing recordings only. A filesystem root, the home directory
    or the config directory was refused from the start, so nothing of ours
    can be directly under it and it is not scanned at all."""
    home = Path.home().expanduser().resolve(strict=False)
    if root == Path(root.anchor) or root in (home, (home / ".domovoi").resolve(strict=False)):
        return 0
    gone = 0
    for rec in _inventory(root):
        if rec.when >= cutoff or rec.wav is None or rec.side is None:
            continue
        side = _read_sidecar(rec.side)
        rid = side.get("room_id") if side else None
        if (
            side is None
            or side.get("id") != rec.cid
            or not isinstance(rid, str)
            or room_dir_name(rid) != rec.room_dir.name
        ):
            continue
        gone += int(_delete_rec(rec))
    return gone


def enforce_cap(max_bytes: int | None = None) -> int:
    """Delete the oldest recordings, across every room, until what is kept
    fits under the cap. Returns the number deleted."""
    if root_problem():
        return 0
    limit = cap_bytes() if max_bytes is None else max_bytes
    recs = _inventory(captures_root())
    total = sum(r.size for r in recs)
    gone = 0
    for rec in recs:
        if total <= limit:
            break
        _delete_rec(rec)
        total -= rec.size
        gone += 1
    return gone


def purge_rooms_not_in(room_ids: Iterable[str]) -> int:
    """Delete what is kept for any room NOT in ``room_ids`` — belt and
    braces behind the opt-out's own delete (a write that raced it, a row
    removed by hand). Returns the number of recordings deleted."""
    if root_problem():
        return 0
    keep = {room_dir_name(r) for r in room_ids}
    gone = 0
    for d in _room_dirs(captures_root()):
        if d.name in keep:
            continue
        ids = {cid for cid, _ in _capture_files(d)}
        if ids:
            gone += _delete_ids(d, ids)
        _rmdir_if_empty(d)
    return gone


# ─── Reading back (the admin API) ─────────────────────────────────────────


def _read_sidecar(p: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_captures(room_id: str | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every complete, unexpired recording (for one room, or all), newest
    first: the sidecar plus ``bytes`` (the audio's size). A sidecar whose
    room doesn't match the directory it sits in is skipped, not trusted."""
    if root_problem():
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days())
    root = captures_root()
    if room_id is not None:
        try:
            dirs = [room_dir(room_id)]
        except ValueError:
            return []
    else:
        dirs = list(_room_dirs(root))
    out: list[dict[str, Any]] = []
    for rec in _inventory(root):
        if rec.room_dir not in dirs or rec.wav is None or rec.side is None:
            continue
        if rec.when < cutoff:
            continue
        side = _read_sidecar(rec.side)
        if side is None or side.get("id") != rec.cid:
            continue
        rid = side.get("room_id")
        if not isinstance(rid, str) or room_dir_name(rid) != rec.room_dir.name:
            continue
        try:
            side["bytes"] = rec.wav.stat().st_size
        except OSError:
            continue
        out.append(side)
    out.sort(key=lambda s: s["id"], reverse=True)
    return out


def usage(now: datetime | None = None) -> dict[str, Any]:
    """Totals for the dashboard, overall and per room directory: the
    recordings a listing would show (``count``) and what is on disk
    (``bytes``, which is what the cap measures), plus the cap and the
    retention."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days())
    by_dir: dict[str, dict[str, int]] = {}
    total = 0
    count = 0
    if not root_problem():
        for rec in _inventory(captures_root()):
            entry = by_dir.setdefault(rec.room_dir.name, {"count": 0, "bytes": 0})
            entry["bytes"] += rec.size
            total += rec.size
            if rec.wav is not None and rec.side is not None and rec.when >= cutoff:
                entry["count"] += 1
                count += 1
    return {
        "count": count,
        "bytes": total,
        "cap_bytes": cap_bytes(),
        "retention_days": retention_days(),
        "by_dir": by_dir,
    }


def set_label(
    room_id: str, capture_id: str, label: str | None, now: datetime | None = None
) -> dict[str, Any] | None:
    """Record an admin's verdict in the sidecar (None clears it). Returns the
    updated sidecar, or None when the recording is not there."""
    if label is not None and label not in LABELS:
        raise ValueError(f"unknown label {label!r}")
    paths = capture_paths(room_id, capture_id)
    if paths is None:
        return None
    wav, side_path = paths
    if not wav.is_file():
        return None
    side = _read_sidecar(side_path)
    if side is None or side.get("id") != capture_id:
        return None
    side["label"] = label
    side["labeled_at"] = (
        (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        if label is not None
        else None
    )
    _write_private(side_path, _dump(side))
    # Deleted while we were writing: don't leave a sidecar with no audio.
    if not wav.is_file():
        _unlink(side_path)
        return None
    return side


# ─── The core's two entry points ──────────────────────────────────────────


# time.monotonic() of the last retention pass run from the write path.
_last_write_purge: float | None = None


def _write_and_cap(room_id: str, pcm: bytes, sidecar: dict[str, Any]) -> str:
    """Write, then hold the cap — and, at most once per pruner interval, the
    retention too. The pruner does both hourly, but it is a worker, and
    workers are skipped under USE_STUBS: a core that can record must never
    rely on it alone to forget."""
    global _last_write_purge
    capture_id = write_capture(room_id, pcm, sidecar)
    try:
        every = max(60.0, float(settings.command_capture_pruner_interval_sec))
    except (TypeError, ValueError):
        every = 3600.0
    now = time.monotonic()
    if _last_write_purge is None or now - _last_write_purge >= every:
        _last_write_purge = now
        purge_expired()
    enforce_cap()
    return capture_id


_refused_logged = False


async def keep(room_id: str, pcm: bytes, sidecar: dict[str, Any]) -> str | None:
    """Keep one command turn's recording if its room is opted in. Runs after
    the reply is on its way, so nothing here is on anybody's wait.

    The opt-in is asked twice: before writing, and again after. Opting out
    deletes the row first and the files second, so a write that lands after
    that delete is caught by the second question and removed here."""
    global _refused_logged
    if not pcm:
        return None
    if not await room_opted_in(room_id):
        return None
    problem = root_problem()
    if problem:
        if not _refused_logged:
            _refused_logged = True
            log.error(
                "command captures: NOT recording — COMMAND_CAPTURES_DIR is unsafe: %s. "
                "Point it at a directory of its own (default ~/.domovoi/captures).",
                problem,
            )
        return None
    try:
        capture_id = await asyncio.to_thread(_write_and_cap, room_id, pcm, sidecar)
    except (OSError, ValueError, CaptureDirRefused) as e:
        log.warning("command captures: could not keep a recording for %s: %s", room_id, e)
        return None
    if not await room_opted_in(room_id):
        await asyncio.to_thread(delete_capture, room_id, capture_id)
        return None
    # Numbers only — the words stay in the sidecar.
    log.info(
        "command capture kept room=%s id=%s duration_ms=%s end_reason=%s",
        room_id, capture_id, sidecar.get("duration_ms"), sidecar.get("end_reason"),
    )
    return capture_id


async def prune(now: datetime | None = None) -> dict[str, int]:
    """One pass of the pruner: expired, then rooms no longer opted in (only
    when the opt-in list could be read), then the disk cap."""
    try:
        rooms: dict[str, datetime] | None = await opted_in_rooms()
    except Exception as e:  # noqa: BLE001
        log.warning("command captures: opt-in list unreadable, skipping the room sweep: %s", e)
        rooms = None

    def _files() -> dict[str, int]:
        expired = purge_expired(now)
        unlisted = purge_rooms_not_in(rooms) if rooms is not None else 0
        capped = enforce_cap()
        return {"expired": expired, "opted_out": unlisted, "over_cap": capped}

    result = await asyncio.to_thread(_files)
    if any(result.values()):
        log.info(
            "command captures: pruned %d expired, %d from rooms no longer opted in, "
            "%d over the %d MB cap",
            result["expired"], result["opted_out"], result["over_cap"],
            cap_bytes() // (1024 * 1024),
        )
    return result
