"""The Windows side of a Windows install, as the Linux side sees it, and the
one "Update Domovoi" across both (design note WINDOWS-ONE-UPDATE-2026-10-22;
docs/WINDOWS_HOST.md, "Updates and the release channel").

A Windows install is two halves. The Linux side (this checkout, inside the
WSL distro) updates as on any Linux server: Settings > About & updates
pulls, and the update unit applies, as root. The Windows side (DomovoiHost,
the tray icon, the satellite Setup this server hands out) changes only when
a newer ``DomovoiSetup.exe`` repairs the install. This module joins them:

* **What runs there.** DomovoiHost writes ``/var/lib/domovoi-windows/side.json``
  as root at every bring-up (``windows/common/WindowsSide.cs``): its version
  and build commit. :func:`read_side` believes the file only while root owns
  it and its folder and nobody else can write either, so the service user,
  the core, the dashboard and the plugins can't fake it. No file: a
  Linux-only install, with no Windows rows and no Windows step.
* **What the Linux side expects.** The newest release tag the RUNNING code
  includes (:func:`target_release`; on ``stable`` without ``-rc`` tags): the
  Windows side updates on releases only. On ``main`` the Linux side follows
  every commit while the Windows side follows the latest release.
* **The step.** Needed when that release is newer than the Windows side,
  internet access isn't ``never`` (the Setup comes from the release on
  GitHub, an admin-started download like the pull) and the tray hasn't said
  that release has no signed Setup. An admin asks for it from the dashboard
  (:func:`make_request`); the tray on the PC polls :func:`tray_state` over
  loopback (``GET /v1/windows/update``), runs it behind one UAC prompt, and
  reports with :func:`record_result` (``POST /v1/windows/update/result``).
  The tray's poll is also how the card knows a tray is there at all
  (:func:`note_tray`, memory only).
* **The tray's own "Update Domovoi".** It has no admin password, so its
  Linux part is authorised by the UAC prompt instead: the elevated helper
  leaves a request in ``C:\\ProgramData\\Domovoi\\host\\in`` (administrators
  only), and DomovoiHost runs ``python -m domovoi.windows_side linux-update``
  here as the service user: :func:`linux_update`, the dashboard's own steps
  in its order.

Files (the core's home, ``~/.domovoi/windows_update``; both processes run as
the service user): ``request.json`` (the admin's request), ``result.json``
(the tray's last outcome), ``unsigned.json`` (releases the tray found no
signed Setup for).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SIDE_FILE = "/var/lib/domovoi-windows/side.json"
_SIDE_MAX_BYTES = 4096
_STATE_MAX_BYTES = 64 * 1024

#: The tray's outcomes (windows/tray/Updater.cs Outcome).
OUTCOMES = (
    "ok", "nothing_new", "declined", "download_failed", "no_signed_setup", "bad_signature",
    "not_newer", "no_key", "repair_failed", "linux_failed", "internet_off", "not_installed", "failed",
)

#: How recently the tray must have polled to count as running (it polls
#: every 30 s).
TRAY_FRESH_SEC = 120

_VERSION_RE = re.compile(r"^v?([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})(?:-([0-9A-Za-z.]{1,40}))?(?:\+[0-9A-Za-z.]*)?$")
_TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]{1,40})?$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[0-9a-f]{16}$")
# A Windows path the tray names for a log: a drive, then no control or
# reserved characters.
_LOG_RE = re.compile(r'^[A-Za-z]:\\[^<>"|?*\x00-\x1f]{1,250}$')

# Seconds since the epoch; module attributes so the tests can move them.
_now = time.time
_TRAY: dict[str, Any] = {}


# ─── versions ───────────────────────────────────────────────────────────


def valid_version(v: object) -> bool:
    return isinstance(v, str) and bool(_VERSION_RE.match(v))


def _ident(a: str, b: str) -> int:
    ma, mb = re.match(r"^([A-Za-z]*)([0-9]*)$", a), re.match(r"^([A-Za-z]*)([0-9]*)$", b)
    if ma and mb:
        if ma.group(1) != mb.group(1):
            return -1 if ma.group(1) < mb.group(1) else 1
        na, nb = ma.group(2), mb.group(2)
        if not na or not nb:
            return (len(na) > len(nb)) - (len(na) < len(nb))
        return (int(na) > int(nb)) - (int(na) < int(nb))
    return (a > b) - (a < b)


def compare_versions(a: str, b: str) -> int:
    """<0, 0, >0 the way SemVer orders releases: 1.0.0-rc2 < 1.0.0-rc10 <
    1.0.0 < 1.0.1 (``windows/common/ReleaseSignature.cs`` ReleaseVersion,
    the same rules). Raises ValueError for something that isn't one."""
    x, y = _VERSION_RE.match(a or ""), _VERSION_RE.match(b or "")
    if not x or not y:
        raise ValueError(f"not a version: {a if not x else b}")
    for i in (1, 2, 3):
        c = (int(x.group(i)) > int(y.group(i))) - (int(x.group(i)) < int(y.group(i)))
        if c:
            return c
    px, py = x.group(4), y.group(4)
    if px is None and py is None:
        return 0
    if px is None:
        return 1
    if py is None:
        return -1
    ix, iy = px.split("."), py.split(".")
    for i in range(min(len(ix), len(iy))):
        c = _ident(ix[i], iy[i])
        if c:
            return c
    return (len(ix) > len(iy)) - (len(ix) < len(iy))


# ─── the Windows side DomovoiHost reported ──────────────────────────────


def side_file() -> Path:
    return Path(os.environ.get("DOMOVOI_WINDOWS_SIDE_FILE") or SIDE_FILE)


def _root_only(st: os.stat_result) -> bool:
    """Owned by root and writable by nobody else. Where there are no POSIX
    owners (a Windows development box) nothing is root's, so nothing
    passes; the tests replace this."""
    if not hasattr(os, "getuid"):
        return False
    return st.st_uid == 0 and not (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def read_side() -> dict | None:
    """``{version, build_commit, written_at}`` as DomovoiHost last wrote it,
    or None: no file (a Linux-only install), or one this module can't
    trust or read. Never raises."""
    path = side_file()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("windows side: can't open %s: %s", path, e)
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not _root_only(st) or not _root_only(os.stat(path.parent)):
            log.warning("windows side: %s isn't root's alone; not believing it", path)
            return None
        if st.st_size > _SIDE_MAX_BYTES:
            return None
        data = os.read(fd, _SIDE_MAX_BYTES + 1)
    except OSError as e:
        log.warning("windows side: can't read %s: %s", path, e)
        return None
    finally:
        os.close(fd)
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != 1 or not valid_version(doc.get("version")):
        return None
    commit = doc.get("build_commit")
    written = doc.get("written_at")
    return {
        "version": doc["version"],
        "build_commit": commit if isinstance(commit, str) and _COMMIT_RE.match(commit) else None,
        "written_at": written if isinstance(written, str) and len(written) <= 40 else None,
    }


# ─── the release the Linux side includes ────────────────────────────────

_TARGET_CACHE: dict[tuple[str, str | None], dict | None] = {}


def target_release(running_sha: str, channel: str | None) -> dict | None:
    """Blocking. ``{tag, version, commit}``: the newest release tag the
    running code includes (``git describe``), not counting release
    candidates on ``stable``; None before the first release or when git
    can't say. Cached per running commit (it doesn't change until a
    restart; a fetch can only add tags after it, never before)."""
    from domovoi import git_version

    sha = (running_sha or "").removesuffix("-dirty")
    if not re.match(r"^[0-9a-f]{7,64}$", sha):
        return None
    key = (sha, "stable" if channel == "stable" else None)
    if key in _TARGET_CACHE:
        return _TARGET_CACHE[key]
    args = ["describe", "--tags", "--abbrev=0", "--match", "v[0-9]*"]
    if channel == "stable":
        args += ["--exclude", "v*-*"]
    try:
        proc = git_version._run(*args, sha)
        tag = proc.stdout.strip() if proc.returncode == 0 else ""
        commit = None
        if _TAG_RE.match(tag):
            p2 = git_version._run("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
            commit = p2.stdout.strip() if p2.returncode == 0 else None
    except Exception as e:  # noqa: BLE001 — no git, a timeout: nothing known
        log.warning("windows side: can't tell which release this code includes: %s", e)
        return None
    found = None
    if _TAG_RE.match(tag):
        found = {"tag": tag, "version": tag[1:],
                 "commit": commit if commit and _COMMIT_RE.match(commit) else None}
    _TARGET_CACHE[key] = found
    return found


# ─── the request, the result, the tray ──────────────────────────────────


def state_dir() -> Path:
    return Path(os.environ.get("DOMOVOI_WINDOWS_UPDATE_DIR") or (Path.home() / ".domovoi" / "windows_update"))


def _read_json(name: str) -> Any:
    path = state_dir() / name
    try:
        if path.stat().st_size > _STATE_MAX_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _write_json(name: str, value: Any) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{name}.{os.getpid()}.tmp"
    tmp.write_bytes(json.dumps(value, sort_keys=True).encode("utf-8"))
    os.replace(tmp, d / name)


def _remove(name: str) -> None:
    try:
        (state_dir() / name).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("windows side: can't remove %s: %s", name, e)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_request() -> dict | None:
    doc = _read_json("request.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("id"), str) or not _ID_RE.match(doc["id"]):
        return None
    return {"id": doc["id"], "on_this_pc": doc.get("on_this_pc") is True,
            "at": doc.get("at") if isinstance(doc.get("at"), str) else None}


def make_request(on_this_pc: bool) -> dict:
    """A new request for the Windows step (replacing any earlier one: the
    tray acts on each id once)."""
    req = {"id": secrets.token_hex(8), "on_this_pc": bool(on_this_pc), "at": _iso(_now())}
    _write_json("request.json", req)
    # The outcome of the last one is history now.
    _remove("result.json")
    return req


def clear_request() -> None:
    _remove("request.json")


def read_result() -> dict | None:
    doc = _read_json("result.json")
    if not isinstance(doc, dict) or doc.get("outcome") not in OUTCOMES:
        return None
    return {k: doc.get(k) for k in ("outcome", "detail", "log", "version", "at", "request_id", "tag")}


def _unsigned() -> list[str]:
    doc = _read_json("unsigned.json")
    return [t for t in doc if isinstance(t, str) and _TAG_RE.match(t)][:50] if isinstance(doc, list) else []


def _clean_text(s: object, limit: int) -> str | None:
    if not isinstance(s, str):
        return None
    out = "".join(c for c in s if c.isprintable())[:limit].strip()
    return out or None


def record_result(body: dict, target_tag: str | None) -> dict:
    """The tray's outcome of a Windows step (or of its own "Update Domovoi"):
    kept for the card, and the request it answers is done. A
    ``no_signed_setup`` marks the release, so the card stops offering it.
    Only known outcomes and plain text are kept: any process on the PC can
    reach this route (the shared-PC boundary), so it holds nothing the card
    would act on by itself."""
    outcome = body.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError("unknown outcome")
    req = read_request()
    rid = body.get("request_id")
    log_path = body.get("log")
    version = body.get("version")
    doc = {
        "outcome": outcome,
        "detail": _clean_text(body.get("detail"), 400),
        "log": log_path if isinstance(log_path, str) and _LOG_RE.match(log_path) else None,
        "version": version if valid_version(version) else None,
        "at": _iso(_now()),
        "request_id": rid if isinstance(rid, str) and _ID_RE.match(rid) else None,
        "tag": target_tag,
    }
    _write_json("result.json", doc)
    if req is not None and (doc["request_id"] == req["id"] or doc["request_id"] is None):
        clear_request()
    if outcome == "no_signed_setup" and target_tag:
        tags = _unsigned()
        if target_tag not in tags:
            _write_json("unsigned.json", [target_tag, *tags][:50])
    return doc


def note_tray(version: object) -> None:
    """The tray polled: it runs, as this version."""
    _TRAY["at"] = _now()
    _TRAY["version"] = version if valid_version(version) else None


def tray_seen() -> dict:
    at = _TRAY.get("at")
    return {
        "running": at is not None and _now() - at < TRAY_FRESH_SEC,
        "seen_at": _iso(at) if at is not None else None,
        "version": _TRAY.get("version"),
    }


# ─── the state, for the card and for the tray ───────────────────────────


async def _facts() -> dict:
    from domovoi import egress, git_version, self_restart

    side = await asyncio.to_thread(read_side)
    running = git_version.boot_sha()
    track = await asyncio.to_thread(git_version.tracking)
    channel = track.get("channel")
    target = await asyncio.to_thread(target_release, running, channel) if side else None
    internet = egress.policy()
    last = await asyncio.to_thread(git_version.read_last_update)
    busy = await asyncio.to_thread(self_restart.underway, last, None)
    unsigned = await asyncio.to_thread(_unsigned)
    why = None
    if side is None:
        why = "no_windows_side"
    elif internet == "never":
        why = "internet_off"
    elif target is None:
        why = "no_release"
    elif compare_versions(target["version"], side["version"]) <= 0:
        why = "up_to_date"
    elif target["tag"] in unsigned:
        why = "unsigned_release"
    request = await asyncio.to_thread(read_request)
    if request is not None and why is not None and not busy:
        # Nothing left to finish (the Windows side caught up, say).
        await asyncio.to_thread(clear_request)
        request = None
    return {
        "side": side, "target": target, "channel": channel, "internet": internet,
        "step_needed": why is None, "why_not": why, "busy": busy, "last": last,
        "request": request, "running_sha": running,
    }


async def tray_state() -> dict:
    """``GET /v1/windows/update``: what the tray needs, and nothing more."""
    f = await _facts()
    last = f["last"] or {}
    return {
        "schema": 1,
        "windows": f["side"],
        "target": f["target"],
        "step_needed": f["step_needed"],
        "why_not": f["why_not"],
        "internet": f["internet"],
        "channel": f["channel"],
        "linux": {
            "busy": f["busy"],
            "last_update": {k: last.get(k) for k in ("status", "started_at", "finished_at", "error")} if f["last"] else None,
        },
        "request": f["request"],
    }


async def card_state() -> dict:
    """``windows_side`` in ``GET /v1/admin/version``: the Version card's
    Windows rows. ``present`` false on a Linux-only install (then nothing
    else matters)."""
    f = await _facts()
    if f["side"] is None:
        return {"present": False}
    return {
        "present": True,
        "version": f["side"]["version"],
        "build_commit": f["side"]["build_commit"],
        "reported_at": f["side"]["written_at"],
        "target": f["target"],
        "step_needed": f["step_needed"],
        "why_not": f["why_not"],
        # The Windows side follows releases on either channel.
        "follows": "releases",
        "request": f["request"],
        "result": await asyncio.to_thread(read_result),
        "tray": tray_seen(),
    }


# ─── the tray's Linux part (run by DomovoiHost) ─────────────────────────


def _applied_sha() -> str | None:
    """The commit the update unit last applied and saw healthy
    (apply-update.sh's applied_sha), or None."""
    from domovoi.config import settings

    try:
        text = (Path(settings.update_state_dir).expanduser() / "applied_sha").read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if _COMMIT_RE.match(text) else None


async def linux_update() -> dict:
    """The dashboard's update, in its order, for an administrator of the PC
    (DomovoiHost runs this as the service user after the elevated helper's
    request): refused while the container guard holds updates or one is
    under way; then the check; a pull when behind (not while the upstream
    is the commit the update unit rolled back); then, with anything new on
    disk, the restart that starts the update unit (backup, sync, migrate,
    health check, roll back). ``{ok, started, nothing_new, error, from_sha,
    to_sha}``; never raises."""
    from domovoi import egress, git_version, self_restart
    from domovoi.container_guard import freeze as container_freeze

    def no(error: str) -> dict:
        return {"ok": False, "started": False, "nothing_new": False, "error": error}

    if egress.internet_turned_off():
        return no(egress.TURNED_OFF_REASON)
    try:
        if await container_freeze.is_frozen():
            return no("the container guard is holding updates until an admin looks at its incident (Settings > Health)")
    except Exception as e:  # noqa: BLE001 — can't tell: hold, like the freeze errs
        return no(f"couldn't read the container guard's incidents: {e}")
    last = await asyncio.to_thread(git_version.read_last_update)
    if await asyncio.to_thread(self_restart.underway, last, None):
        return no("an update or restart is already under way")
    check = await git_version.commits_behind()
    if check.get("error") and not check.get("upstream"):
        return no(f"the update check failed: {check['error']}")
    from_sha = await git_version.current_sha()
    behind = int(check.get("behind") or 0)
    if behind > 0:
        bad = git_version._bad_sha(last)
        if bad and check.get("upstream_sha") == bad:
            return no(f"the latest commit ({bad[:7]}) failed to apply on this server and was rolled back, "
                      "so it isn't offered again; the next one upstream will be")
        pulled = await git_version.pull()
        if not pulled.get("pulled"):
            return no(f"the pull failed: {pulled.get('error') or 'unknown'}")
    to_sha = await git_version.current_sha()
    applied = await asyncio.to_thread(_applied_sha)
    head = to_sha.removesuffix("-dirty")
    if behind == 0 and (applied is None or head == applied or not _COMMIT_RE.match(head)):
        return {"ok": True, "started": False, "nothing_new": True, "error": None, "from_sha": from_sha, "to_sha": to_sha}
    res = await self_restart.restart()
    if not res.get("ok"):
        return no(f"the restart didn't start: {res.get('error') or 'unknown'}")
    # The restart fires a moment later (self_restart's delay): this process
    # waits for it before it exits.
    pending = list(getattr(self_restart, "_PENDING", ()))
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return {"ok": True, "started": True, "nothing_new": False, "error": None, "from_sha": from_sha, "to_sha": to_sha}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["linux-update"]:
        print("usage: python -m domovoi.windows_side linux-update", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    try:
        result = asyncio.run(linux_update())
    except Exception as e:  # noqa: BLE001 — DomovoiHost reads one line, whatever happened
        result = {"ok": False, "started": False, "nothing_new": False, "error": f"the Linux update failed: {e}"}
    print(json.dumps(result), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
