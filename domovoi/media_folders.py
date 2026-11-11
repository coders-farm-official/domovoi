"""Where the household's media folders really are, and the host channels
that open and move them (owner, 2026-11-11; docs/WINDOWS_HOST.md, "Media
folders"; Settings → Household → Media folders).

The core sees every media folder as a Linux path (``music_dir`` and the
rest). On a Linux server that path IS the folder. On the Windows install
it is a mount point inside Domovoi's private WSL distro
(``/srv/media/music``, a drvfs line in ``/etc/fstab`` that provision.sh
writes) of a Windows folder the household chose in Setup (default
``C:\\Users\\Public\\Domovoi\\Music``): a path nobody can open, and editing
it to another Linux path would do nothing useful. So this module answers
"where is it really" per host kind, and runs the two requests only the
host can carry out.

Host kinds (:func:`host`):

* ``linux``: no host report. The path is the folder; Settings edits
  ``music_dir`` / ``videos_dir`` as before, now checked here
  (:func:`linux_dir_problem`: the folder exists and the service user can
  read it). No "open": a server process starts no file manager.
* ``windows``: DomovoiHost's report, ``/var/lib/domovoi-windows/media.json``
  (``windows/common/MediaFolders.cs``), believed only while root owns it
  and its folder and nobody else can write either, as ``limits.json`` and
  ``side.json`` are. It names the media root, each folder's Windows path,
  what the host can do (``can_open``, ``can_change``) and what became of
  the last move. A DomovoiHost from before it writes no report: then the
  Domovoi block of ``/etc/fstab`` (root's, written by provision.sh) still
  names the Windows folders, so the page shows them, without open or move.
* ``mac`` (to come, wf/mac-provision-arm64: a Lima VM with a virtiofs
  share): the same report schema at ``/var/lib/domovoi-mac/media.json``
  with ``"host_kind": "mac"`` and POSIX paths; add it to
  :data:`REPORT_FILES` and its launcher reads the same request files.

The requests (files in the core's home, ``~/.domovoi/media_folders``,
which the host reads AS the service user and treats as data only):

* ``open.json`` ``{"v": 1, "id": "<32 hex>", "kind": "music", "at": ...}``:
  "Open on the Domovoi PC". It names a media KIND, never a path. The tray
  on the PC (it runs on the signed-in person's desktop; DomovoiHost runs
  as a service account in session 0 and can open no window) polls
  ``GET /v1/windows/media/open`` over loopback, claims the request, maps
  the kind to a folder from the host's own Windows-side list and opens
  File Explorer there only if that folder passes its own checks.
* ``request.json`` ``{"v": 1, "root": "D:\\\\Media", "id": ..., "at": ...}``:
  move the media root. Checked here for shape, then again by DomovoiHost,
  which validates the folder on Windows (a local NTFS drive, not a system
  or profile folder, no links, Domovoi's account can write there), makes
  the five folders, rewrites the distro's media block in ``/etc/fstab``
  and restarts Domovoi's Linux system so the mounts and the rooms' music
  players pick it up (at most one restart per 10 minutes, never during an
  update: the hardware-limits rules). Files are never moved: the old
  folder stays as it is (see docs/WINDOWS_HOST.md for why).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import stat
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

log = logging.getLogger(__name__)

#: (kind, label, Settings field). Order is the page's order.
KINDS: tuple[tuple[str, str, str], ...] = (
    ("music", "Music", "music_dir"),
    ("videos", "Videos", "videos_dir"),
    ("pictures", "Pictures", "pictures_dir"),
    ("documents", "Documents", "documents_dir"),
    ("audiobooks", "Audiobooks", "audiobooks_dir"),
    ("podcasts", "Podcasts", "podcasts_dir"),
)
_FIELD = {k: f for k, _, f in KINDS}

#: The paths a Linux server edits in Settings (config_schema's FieldSpecs).
LINUX_EDITABLE = ("music_dir", "videos_dir")

#: The folders under a Windows media root (windows/common/Provision.cs
#: MediaPaths.Folders), by kind.
HOST_FOLDERS = {"music": "Music", "pictures": "Pictures", "videos": "Videos",
                "documents": "Documents", "audiobooks": "Audiobooks"}

#: Where each host kind's report is. The Mac adds ("mac", "/var/lib/domovoi-mac/media.json").
REPORT_FILES: tuple[tuple[str, str], ...] = (("windows", "/var/lib/domovoi-windows/media.json"),)
_REPORT_MAX_BYTES = 8192
FSTAB = "/etc/fstab"
FSTAB_BEGIN = "# BEGIN Domovoi media folders (scripts/linux/provision.sh rewrites this block)"
FSTAB_END = "# END Domovoi media folders"
MOUNT_BASE = "/srv/media"

#: What the place is called in the page's words, per host kind.
PLACE = {"windows": "the Domovoi PC", "mac": "the Domovoi Mac", "linux": "the Domovoi server"}

#: A move's states as DomovoiHost reports them (MediaFolders.cs).
CHANGE_STATES = ("none", "waiting", "busy", "applying", "done", "refused", "failed", "stale")
_DONE_STATES = ("done", "refused", "failed", "stale")
#: The tray's outcomes for an open (windows/tray/MediaOpen.cs).
OPEN_OUTCOMES = ("opened", "missing", "refused", "failed")

#: An open request older than this is dropped, never acted on: nobody is
#: waiting at the PC any more.
OPEN_TTL_SEC = 60
#: The longest root accepted (DomovoiHost's MediaFolders.MaxRootLength):
#: the folders and their files go under it.
MAX_ROOT = 200

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_AT_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:\\")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
#: A path segment that looks like an 8.3 short name (MediaPaths.Problem).
_SHORT_RE = re.compile(r"~[0-9]")
#: Names Windows keeps for devices, with or without an extension.
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)}
                      | {f"LPT{i}" for i in range(1, 10)})

_now = time.time

UPDATE_RUNNING = ("an update or restart of Domovoi is running; move the media folders once it has finished")


class Refused(ValueError):
    """A request this host can't take, with the reason in plain words."""


class Busy(Refused):
    """A move asked for while an update or Restart of Domovoi runs."""


# ─── paths ───────────────────────────────────────────────────────────────


def normalize_windows(path: str) -> str:
    p = (path or "").strip().replace("/", "\\")
    while len(p) > 3 and p.endswith("\\"):
        p = p[:-1]
    return p


def windows_path_problem(path: object) -> str | None:
    """Why ``path`` can't be a Windows media folder, or None: the shape
    rules of MediaPaths.Problem (windows/common/Provision.cs). What the
    folder IS on that PC (a system folder, a link, another drive type) only
    DomovoiHost can tell."""
    if not isinstance(path, str):
        return "no folder given"
    p = normalize_windows(path)
    if not p:
        return "no folder given"
    if len(p) > MAX_ROOT:
        return f"the path is longer than {MAX_ROOT} characters"
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        return "control characters in the path"
    if p.startswith("\\\\"):
        return "a network folder can't be used; pick a folder on a drive of the Domovoi PC"
    if not _DRIVE_RE.match(p):
        return "pick a folder on a drive of the Domovoi PC, such as D:\\Media"
    rest = p[3:]
    if not rest:
        return "pick a folder, not a whole drive"
    if any(c in rest for c in '"*?<>|:'):
        return "not a valid Windows folder name"
    for seg in rest.split("\\"):
        if not seg:
            return "the path has an empty folder name (\\\\)"
        if seg in (".", ".."):
            return "no . or .. in the path"
        if seg.endswith(" ") or seg.endswith("."):
            return "a folder name can't end with a space or a dot"
        # An 8.3 short name (PROGRA~1) could name a system folder the
        # long-name checks don't recognise.
        if _SHORT_RE.search(seg):
            return "write the folder's full name, not a short name with ~ and a digit"
        if seg.split(".")[0].strip().upper() in _RESERVED:
            return f"{seg} is a name Windows keeps for a device"
    return None


def mac_path_problem(path: object) -> str | None:
    """The Mac host's paths (to come): absolute POSIX, nothing odd."""
    if not isinstance(path, str) or not path.startswith("/") or len(path) > MAX_ROOT:
        return "not an absolute folder path"
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return "control characters in the path"
    if any(seg in (".", "..") for seg in path.split("/")):
        return "no . or .. in the path"
    return None


_PATH_PROBLEM = {"windows": windows_path_problem, "mac": mac_path_problem}


def _join(kind: str, folder: str, parts: tuple[str, ...]) -> str:
    if not parts:
        return folder
    sep = "\\" if kind == "windows" else "/"
    return folder.rstrip(sep) + sep + sep.join(parts)


# ─── the host's report ──────────────────────────────────────────────────


def _report_path(kind: str, default: str) -> Path:
    env = os.environ.get(f"DOMOVOI_HOST_MEDIA_REPORT_{kind.upper()}")
    if kind == "windows":
        env = env or os.environ.get("DOMOVOI_HOST_MEDIA_REPORT")
    return Path(env or default)


def _root_only(st: os.stat_result) -> bool:
    """Owned by root and writable by nobody else; nothing passes where
    there are no POSIX owners (the tests replace this)."""
    if not hasattr(os, "getuid"):
        return False
    return st.st_uid == 0 and not (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _read_root_file(path: Path, limit: int, *, check_parent: bool = True) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("media folders: can't open %s: %s", path, e)
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not _root_only(st) or (check_parent and not _root_only(os.stat(path.parent))):
            log.warning("media folders: %s isn't root's alone; not believing it", path)
            return None
        if st.st_size > limit:
            return None
        return os.read(fd, limit + 1)
    except OSError as e:
        log.warning("media folders: can't read %s: %s", path, e)
        return None
    finally:
        os.close(fd)


def _short(v: object, n: int = 200) -> str | None:
    return v if isinstance(v, str) and 0 < len(v) <= n and not any(ord(c) < 32 for c in v) else None


def read_report() -> dict[str, Any] | None:
    """The first host report that can be believed, its fields checked;
    None when there is none. Never raises."""
    for kind, default in REPORT_FILES:
        data = _read_root_file(_report_path(kind, default), _REPORT_MAX_BYTES)
        if data is None:
            continue
        doc = _parse_report(data, kind)
        if doc is not None:
            return doc
    return None


def _parse_report(data: bytes, expected_kind: str) -> dict[str, Any] | None:
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != 1 or doc.get("host_kind") != expected_kind:
        return None
    problem = _PATH_PROBLEM[expected_kind]
    root = doc.get("root")
    if problem(root) is not None:
        return None
    base = doc.get("mount_base")
    if base != MOUNT_BASE:
        return None
    raw = doc.get("folders")
    if not isinstance(raw, dict):
        return None
    folders: dict[str, str] = {}
    for name, path in raw.items():
        if name not in HOST_FOLDERS or problem(path) is not None:
            return None
        folders[name] = normalize_windows(path) if expected_kind == "windows" else path
    change = doc.get("change") if isinstance(doc.get("change"), dict) else {}
    state = change.get("state") if change.get("state") in CHANGE_STATES else "none"
    rid = change.get("request_id")
    croot = change.get("root")
    return {
        "host_kind": expected_kind,
        "root": normalize_windows(root) if expected_kind == "windows" else root,
        "mount_base": base,
        "folders": folders,
        "source": doc.get("source") if doc.get("source") in ("setup", "dashboard") else "setup",
        "can_open": doc.get("can_open") is True,
        "can_change": doc.get("can_change") is True,
        "change": {
            "state": state,
            "request_id": rid if isinstance(rid, str) and _ID_RE.match(rid) else None,
            "root": croot if problem(croot) is None else None,
            "problem": _short(change.get("problem"), 300),
            "previous_root": change.get("previous_root") if problem(change.get("previous_root")) is None else None,
        },
        "written_at": _short(doc.get("written_at"), 40),
    }


def _fstab_unescape(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def read_fstab_folders(path: str | None = None) -> dict[str, str]:
    """The Windows folders the Domovoi block of /etc/fstab mounts at
    /srv/media/<name> (provision.sh's media step), by name; {} when there
    is no block, or the file isn't root's alone. For a DomovoiHost too old
    to report them."""
    data = _read_root_file(Path(path or os.environ.get("DOMOVOI_FSTAB") or FSTAB), 256 * 1024, check_parent=False)
    if data is None:
        return {}
    out: dict[str, str] = {}
    held: dict[str, str] = {}
    inside = False
    for line in data.decode("utf-8", errors="replace").splitlines():
        if line == FSTAB_BEGIN:
            inside = True
            held = {}
            continue
        if line == FSTAB_END:
            # Only a block that ends counts: a BEGIN with no END is a
            # stray marker, not a list of media folders.
            if inside:
                out.update(held)
            inside = False
            continue
        if not inside:
            continue
        f = line.split()
        if len(f) < 3 or f[2] != "drvfs" or not f[1].startswith(MOUNT_BASE + "/"):
            continue
        name = f[1][len(MOUNT_BASE) + 1:]
        if not _NAME_RE.match(name):
            continue
        src = normalize_windows(_fstab_unescape(f[0]))
        if windows_path_problem(src) is None:
            held[name] = src
    return out


def _common_root(folders: dict[str, str]) -> str | None:
    """The media root when every folder is <root>\\<its usual name>."""
    roots = set()
    for name, path in folders.items():
        want = HOST_FOLDERS.get(name)
        head, _, tail = path.rpartition("\\")
        if want is None or tail.lower() != want.lower() or not head:
            return None
        roots.add(head.lower() if head else head)
    if len(roots) != 1:
        return None
    any_path = next(iter(folders.values()))
    return any_path.rpartition("\\")[0]


def host() -> dict[str, Any]:
    """What kind of host this is and what it says of the media folders."""
    report = read_report()
    folders = read_fstab_folders()
    if report is not None:
        # What is mounted wins over a report that says otherwise: a report
        # left behind by a newer DomovoiHost after an older Setup ran (a
        # downgrade puts Setup's own folders back and writes no report).
        mounted = {k: v.lower() for k, v in folders.items()}
        reported = {k: v.lower() for k, v in report["folders"].items()}
        if report["host_kind"] != "windows" or not folders or mounted == reported:
            return {"kind": report["host_kind"], "report": report, "folders": report["folders"],
                    "root": report["root"], "can_open": report["can_open"], "can_change": report["can_change"],
                    "source": "report", "stale": False}
        log.warning("media folders: DomovoiHost's report doesn't match /etc/fstab; showing what is mounted")
        return {"kind": "windows", "report": None, "folders": folders, "root": _common_root(folders),
                "can_open": False, "can_change": False, "source": "fstab", "stale": True}
    if folders:
        return {"kind": "windows", "report": None, "folders": folders, "root": _common_root(folders),
                "can_open": False, "can_change": False, "source": "fstab", "stale": False}
    return {"kind": "linux", "report": None, "folders": {}, "root": None,
            "can_open": False, "can_change": False, "source": None, "stale": False}


def location_for(linux_path: str, h: dict[str, Any]) -> str | None:
    """The host folder a Linux path is in, or None: inside Domovoi's own
    Linux system (no folder on the host), or a Linux server."""
    if h["kind"] == "linux" or not linux_path:
        return None
    try:
        rel = PurePosixPath(linux_path).relative_to(MOUNT_BASE)
    except ValueError:
        return None
    if not rel.parts or ".." in rel.parts:
        return None
    folder = h["folders"].get(rel.parts[0])
    return _join(h["kind"], folder, rel.parts[1:]) if folder else None


# ─── the core's own files ───────────────────────────────────────────────


def state_dir() -> Path:
    return Path(os.environ.get("DOMOVOI_MEDIA_FOLDERS_DIR") or (Path.home() / ".domovoi" / "media_folders"))


def _write(name: str, doc: dict[str, Any]) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    tmp = d / f".{name}.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write((json.dumps(doc) + "\n").encode("utf-8"))
    os.replace(tmp, d / name)


def _read(name: str) -> dict[str, Any] | None:
    try:
        raw = (state_dir() / name).read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _remove(name: str) -> None:
    try:
        (state_dir() / name).unlink()
    except FileNotFoundError:
        pass


def _at() -> str:
    return datetime.fromtimestamp(_now(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age(at: object) -> float | None:
    if not isinstance(at, str) or not _AT_RE.match(at):
        return None
    t = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    return _now() - t


# ─── open on the Domovoi PC ─────────────────────────────────────────────


def _tray_running() -> bool:
    try:
        from domovoi import windows_side

        return bool(windows_side.tray_seen().get("running"))
    except Exception:  # noqa: BLE001 - no tray state: nobody to open it
        return False


_STALE = ("DomovoiHost's report doesn't match what is mounted (an older Domovoi Setup ran since?); "
          "run the newest Domovoi Setup, or the tray's Update Domovoi, to bring it back")


def open_state(h: dict[str, Any]) -> dict[str, Any]:
    """Whether "Open on the Domovoi PC" can work, and why not."""
    if h["kind"] == "linux":
        return {"available": False, "why": "a Linux server opens no windows; copy the path instead"}
    if h.get("stale"):
        return {"available": False, "why": _STALE}
    if not h["can_open"]:
        return {"available": False,
                "why": "this PC's Domovoi for Windows is from before folders could be opened from here; "
                       "the next update of its Windows side brings it"}
    if not _tray_running():
        return {"available": False,
                "why": "Domovoi's icon isn't running on the Domovoi PC (it is what opens the folder); "
                       "sign in on that PC, or start Domovoi from its Start menu"}
    return {"available": True, "why": None}


def request_open(kind: str) -> dict[str, Any]:
    """Ask the tray on the PC to open ``kind``'s folder in File Explorer."""
    h = host()
    st = open_state(h)
    if not st["available"]:
        raise Refused(st["why"])
    field = _FIELD.get(kind)
    if field is None:
        raise Refused("not a media folder")
    from domovoi.config import settings

    linux = str(getattr(settings, field, "") or "")
    try:
        name = PurePosixPath(linux).relative_to(MOUNT_BASE).parts[0]
    except (ValueError, IndexError):
        name = None
    if name not in h["folders"]:
        raise Refused(f"{kind} is kept inside Domovoi's own Linux system, not in a folder on {PLACE[h['kind']]}")
    doc = {"v": 1, "id": secrets.token_hex(16), "kind": name, "at": _at()}
    _write("open.json", doc)
    _remove("open-claim.json")
    log.info("media folders: asked the tray to open the %s folder", name)
    return {"ok": True, "id": doc["id"]}


def pending_open() -> dict[str, Any] | None:
    """The tray's poll: the open request, while it is fresh and unclaimed."""
    doc = _read("open.json")
    if not doc or doc.get("v") != 1 or not _ID_RE.match(str(doc.get("id", ""))):
        return None
    if doc.get("kind") not in HOST_FOLDERS:
        return None
    age = _age(doc.get("at"))
    if age is None or age > OPEN_TTL_SEC or age < -300:
        _remove("open.json")
        return None
    return {"id": doc["id"], "kind": doc["kind"], "at": doc["at"]}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def claim_open(request_id: object) -> tuple[str, str | None]:
    """One tray takes the request: ``("ok", token)``, ``("claimed",
    None)`` (another tray took it) or ``("gone", None)``. The claim is made
    with O_EXCL, so two signed-in accounts' trays never both open a window;
    its result must carry the token (only its hash is kept), as the
    Windows update's claim does."""
    req = pending_open()
    if req is None or req["id"] != request_id:
        return "gone", None
    d = state_dir()
    try:
        fd = os.open(d / "open-claim.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        return "claimed", None
    token = secrets.token_hex(16)
    with os.fdopen(fd, "wb") as f:
        f.write((json.dumps({"id": req["id"], "hash": _token_hash(token), "at": _at()}) + "\n").encode("utf-8"))
    _remove("open.json")
    return "ok", token


def record_open_result(request_id: object, claim_token: object, outcome: object, detail: object) -> dict[str, Any]:
    claim = _read("open-claim.json")
    if (not claim or claim.get("id") != request_id or not isinstance(claim_token, str)
            or not isinstance(claim.get("hash"), str)
            or not hmac.compare_digest(claim["hash"], _token_hash(claim_token))):
        raise PermissionError("not the request this tray claimed")
    if outcome not in OPEN_OUTCOMES:
        raise ValueError("unknown outcome")
    doc = {"id": request_id, "outcome": outcome, "detail": _short(detail, 300), "at": _at()}
    _write("open-result.json", doc)
    _remove("open-claim.json")
    return doc


def _last_open() -> dict[str, Any] | None:
    doc = _read("open-result.json")
    if not doc or doc.get("outcome") not in OPEN_OUTCOMES:
        return None
    return {"id": doc.get("id"), "outcome": doc["outcome"], "detail": _short(doc.get("detail"), 300),
            "at": doc.get("at") if isinstance(doc.get("at"), str) and _AT_RE.match(doc["at"]) else None}


# ─── move the media root ────────────────────────────────────────────────


def _update_running() -> bool:
    try:
        from domovoi import git_version, self_restart

        return self_restart.underway(git_version.read_last_update())
    except Exception:  # noqa: BLE001 - can't tell: DomovoiHost's own check decides
        log.debug("media folders: can't tell whether an update runs", exc_info=True)
        return False


def read_change_request() -> dict[str, Any] | None:
    doc = _read("request.json")
    if not doc or doc.get("v") != 1 or not _ID_RE.match(str(doc.get("id", ""))):
        return None
    if windows_path_problem(doc.get("root")) is not None:
        return None
    return {"id": doc["id"], "root": doc["root"], "at": doc.get("at")}


def change_state(h: dict[str, Any]) -> dict[str, Any]:
    """Whether the media root can be moved from here, and how the last
    move went. Takes back a request the host has finished with."""
    report = h["report"]
    out: dict[str, Any] = {"available": False, "why": None, "state": "none", "problem": None,
                           "requested_root": None, "previous_root": None, "waiting_for_host": False}
    if h["kind"] == "linux":
        out["why"] = "on a Linux server, change each folder's path below"
        return out
    if h.get("stale"):
        out["why"] = _STALE
        return out
    if not h["can_change"]:
        out["why"] = ("this PC's Domovoi for Windows is from before the media folders could be moved from here; "
                      "the next update of its Windows side brings it. Until then, run Domovoi Setup again and "
                      "choose the new folder there")
        return out
    out["available"] = True
    ch = report["change"] if report else {"state": "none", "request_id": None}
    req = read_change_request()
    if req is not None and ch.get("request_id") == req["id"]:
        out.update(state=ch["state"], problem=ch.get("problem"), requested_root=req["root"],
                   previous_root=ch.get("previous_root"))
        if ch["state"] in _DONE_STATES:
            _remove("request.json")
    elif req is not None:
        out.update(state="waiting", requested_root=req["root"], waiting_for_host=True)
    elif ch.get("request_id"):
        out.update(state=ch["state"], problem=ch.get("problem"), requested_root=ch.get("root"),
                   previous_root=ch.get("previous_root"))
    return out


def request_change(root: object, *, update_running=None) -> dict[str, Any]:
    """Ask the host to move the media root to ``root`` (a folder on the
    host). Refused off a host that can, for a path of the wrong shape, for
    the root it already has, and while the host is in the middle of a
    move; ``Busy`` while an update or restart runs."""
    h = host()
    st = change_state(h)
    if not st["available"]:
        raise Refused(st["why"])
    why = windows_path_problem(root)
    if why:
        raise Refused(why)
    p = normalize_windows(root)  # type: ignore[arg-type]
    if h["root"] and p.lower() == h["root"].lower():
        raise Refused("the media folders are already there")
    if st["state"] in ("busy", "applying") or (st["state"] == "waiting" and not st["waiting_for_host"]):
        raise Refused(f"a move to {st['requested_root']} is under way; wait for it to finish")
    if (update_running or _update_running)():
        raise Busy(UPDATE_RUNNING)
    doc = {"v": 1, "root": p, "id": secrets.token_hex(16), "at": _at()}
    _write("request.json", doc)
    log.info("media folders: asked the host to move the media root to %s", p)
    return status()


def cancel_change() -> dict[str, Any]:
    """Withdraw a move the host hasn't started on."""
    h = host()
    st = change_state(h)
    if st["state"] in ("busy", "applying") or (st["state"] == "waiting" and not st["waiting_for_host"]):
        raise Refused("the Domovoi PC is already moving the media folders")
    _remove("request.json")
    return status()


# ─── Linux paths ─────────────────────────────────────────────────────────


def _service_user() -> str:
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return "Domovoi's service user"


def linux_dir_problem(value: str, *, made_in_home: bool = False) -> str | None:
    """Settings' check on ``music_dir`` / ``videos_dir``: on a Linux server
    the folder must exist and Domovoi's service user (this process) must
    be able to read it (``made_in_home``: a missing folder in the service
    user's home passes, the core makes it at startup); on a host that keeps media in its own folders the
    Linux path isn't the household's to change."""
    h = host()
    if h["kind"] != "linux":
        return (f"on this install the media folders are folders on {PLACE[h['kind']]}; "
                "move them in Settings → Household → Media folders")
    p = os.path.expanduser(value.strip())
    if not os.path.isabs(p):
        return "give the folder's full path, starting with /"
    if not os.path.isdir(p):
        # videos_dir: the core makes a missing one at startup, but only in
        # its own home.
        home = os.path.normpath(str(Path.home()))
        try:
            inside = os.path.commonpath([home, os.path.normpath(p)]) == home
        except ValueError:
            inside = False
        if made_in_home and inside:
            return None
        return f"there is no folder at {p}"
    if not os.access(p, os.R_OK | os.X_OK):
        return f"{_service_user()} can't read {p}; give it read access (or pick another folder)"
    return None


# ─── the page ────────────────────────────────────────────────────────────


def _kind_rows(h: dict[str, Any]) -> list[dict[str, Any]]:
    from domovoi.config import settings

    rows = []
    for kind, label, field in KINDS:
        linux = os.path.expanduser(str(getattr(settings, field, "") or ""))
        row: dict[str, Any] = {"kind": kind, "label": label, "field": field, "linux_path": linux}
        if h["kind"] == "linux":
            row.update(location=linux, where="server", editable=field in LINUX_EDITABLE,
                       exists=os.path.isdir(linux), readable=os.access(linux, os.R_OK | os.X_OK))
        else:
            loc = location_for(linux, h)
            # The mount point itself: there and empty when the Windows
            # folder is missing (its drive unplugged, say).
            connected = False
            if loc:
                mount = os.path.join(MOUNT_BASE, PurePosixPath(linux).relative_to(MOUNT_BASE).parts[0])
                connected = os.path.ismount(mount)
            row.update(location=loc, where="host" if loc else "internal", editable=False,
                       exists=bool(loc) and os.path.isdir(linux), connected=connected,
                       readable=bool(loc) and os.access(linux, os.R_OK | os.X_OK))
        rows.append(row)
    return rows


def status() -> dict[str, Any]:
    """``GET /v1/admin/media-folders``: the host kind, each media kind's
    real location, and what can be done about them here."""
    h = host()
    return {
        "host_kind": h["kind"],
        "place": PLACE[h["kind"]],
        "root": h["root"],
        "source": h["source"],
        "kinds": _kind_rows(h),
        "open": {**open_state(h), "last": _last_open()},
        "change": change_state(h),
        "default_root": "C:\\Users\\Public\\Domovoi" if h["kind"] == "windows" else None,
    }


# ─── after a move ────────────────────────────────────────────────────────


def moved_since_last_boot() -> bool:
    """Whether the host's media folders differ from the ones the core saw
    at its last start (a move from the dashboard, or Setup choosing
    another folder): then the rooms' music players are made again, so
    their mounts are the new folder's. Records what it sees now. False on
    a Linux server (a changed music_dir is caught per container,
    mpd_provisioner) and the first time."""
    h = host()
    if h["kind"] == "linux":
        return False
    # /etc/fstab, root's, is rewritten BEFORE the Linux system restarts
    # (by DomovoiHost's move, or provision.sh when Setup picks another
    # folder), so it is right at this start; DomovoiHost's report is
    # written only once Domovoi is up again.
    folders = read_fstab_folders() or h["folders"]
    now = {k: v.lower() for k, v in sorted(folders.items())}
    seen = _read("seen.json")
    try:
        _write("seen.json", {"folders": now})
    except OSError as e:
        log.warning("media folders: can't record the folders seen: %s", e)
    return bool(seen) and seen.get("folders") != now
