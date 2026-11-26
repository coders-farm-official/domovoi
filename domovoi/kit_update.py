"""Updating an installed house from a newer offline kit (release 1.5.0, P5;
docs/OFFLINE_KIT.md, "Updating from a kit"; design-notes OFFLINE-KIT §8).

An admin plugs the newer kit's stick into any computer and, in the
dashboard (Settings → System → About & updates → Update from an offline
kit), chooses the stick's folder. The browser then:

1. sends ``kit.json`` (:func:`begin`). The server checks it before a byte
   of anything else moves: the kit's format, that its release is **newer**
   than the one this server runs (no downgrades), that it is for **this
   machine** (a PC kit for an x86_64 server, a Mac kit for the Mac's
   arm64 VM), that the server follows the stable channel and can update
   itself (the update unit), and that the disk has room for what comes. It
   answers with the files it lacks: the release's signed manifest, the
   source bundle, the Python packages this server doesn't have at the
   kit's versions, the container images Docker doesn't hold by their
   pinned digests (and the music player image), Ubuntu's packages when
   one the update installs is missing, and the phone app when the kit's
   is newer. Models, voices and the rest are not sent: an update with
   internet doesn't download them either.
2. sends each file in chunks (:func:`receive`), each at the offset the
   server already holds, so a reload, a dropped Wi-Fi or a restart picks
   up where it stopped (the user chooses the folder again). A finished
   file is held to kit.json's size and SHA-256; one that isn't is thrown
   away and named. The release's manifest is checked against Domovoi's
   release key as soon as it and its signature are here.
3. asks to apply (:func:`prepare_apply`, then the route starts the update
   unit): split files joined, the release signature checked again, the
   kit's commit fetched from the bundle and held to the signed manifest,
   to the checkout (it must be built on the running commit) and to the
   allowed signers as a pull is; ``origin/<channel>`` moved to it (never
   backwards) and the checkout fast-forwarded; then ``request`` names the
   commit for the update unit, and the unit runs with the uploaded folder
   as ``DOMOVOI_OFFLINE_SOURCE`` (scripts/linux/apply-update.sh takes the
   wheels, images and Ubuntu packages from there, each checked against the
   commit's own pins, instead of the internet).
4. after the update (:func:`settle`, at the next status read and at the
   core's start): the kit's APKs go in through
   ``android_app.accept_upload``, as an admin's upload, and the uploaded
   copy is removed. A failed update keeps the files for another try;
   **cancel** removes them at any time but while the update runs.

Nothing here reaches the internet, so it works with internet access set
to "never". Everything lives in ``~/.domovoi/offline_kit/update/``:
``files/`` (the kit's layout, only the files sent), ``session.json`` and
``request``. Blocking: the routes run these off the event loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from domovoi.kit_builder import host
from domovoi.offline_kit import format as fmt
from domovoi.offline_kit.sshsig import RELEASE_KEY

log = logging.getLogger(__name__)

#: The release key the kit's signed manifest must verify with (tests point
#: it at the throwaway fixture key).
release_key = RELEASE_KEY

#: The biggest chunk one request may carry.
CHUNK_MAX = 16 * 1024 * 1024
#: What the disk check keeps free beyond what the update needs.
HEADROOM = 512 * 1024 * 1024
#: Ubuntu packages apply-update.sh installs when a box lacks them
#: (ADDED_PACKAGES there; a test holds the two together): when one is
#: missing, the kit's Ubuntu packages are sent too.
ADDED_PACKAGES = ("xorriso", "tesseract-ocr", "tesseract-ocr-eng", "dosfstools", "mtools")
#: The update unit's request file: one line, ``commit=<40 hex>``.
REQUEST = "request"

Run = Callable[..., subprocess.CompletedProcess]
_run: Run = subprocess.run
_LOCK = threading.RLock()

#: The machine kinds a kit is made for, by this host's machine.
_MACHINES = {
    "x86_64": ("pc-x86_64", "x86_64", "amd64"),
    "amd64": ("pc-x86_64", "x86_64", "amd64"),
    "aarch64": ("mac-arm64", "aarch64", "arm64"),
    "arm64": ("mac-arm64", "aarch64", "arm64"),
}


class Refusal(Exception):
    """The update can't go on: ``reason`` (a code) and ``message`` (plain
    words for the card). ``status`` is the HTTP status the route answers."""

    def __init__(self, reason: str, message: str, status: int = 409, **extra: Any):
        super().__init__(message)
        self.reason, self.message, self.status, self.extra = reason, message, status, extra

    def detail(self) -> dict[str, Any]:
        return {"reason": self.reason, "message": self.message, **self.extra}


# ─── Where things are ────────────────────────────────────────────────────

def root() -> Path:
    """``~/.domovoi/offline_kit/update``: apply-update.sh looks here too."""
    return host.kit_root() / "update"


def files_dir() -> Path:
    return root() / "files"


def _state_path() -> Path:
    return root() / "session.json"


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(json.dumps(doc, indent=1, sort_keys=True).encode("utf-8"))
    os.replace(tmp, path)


def _load() -> dict[str, Any] | None:
    try:
        doc = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("id"), str) else None


def _save(state: dict[str, Any]) -> None:
    _write_json(_state_path(), state)


def _remove_files() -> None:
    shutil.rmtree(files_dir(), ignore_errors=True)
    try:
        (root() / REQUEST).unlink(missing_ok=True)
    except OSError:
        pass


# ─── This machine and this server ────────────────────────────────────────

def host_target() -> tuple[str, str, str] | None:
    """(kit target, Python wheel machine, Debian arch) for this machine, or
    None for a machine no kit is made for."""
    return _MACHINES.get(platform.machine().lower())


def _version_key(v: Any) -> tuple | None:
    """A release version (1.5.0, 1.5.0-rc1) as something to compare: a
    pre-release sorts before its release."""
    m = re.fullmatch(r"([0-9]{1,9})\.([0-9]{1,9})\.([0-9]{1,9})(?:-([0-9A-Za-z.]{1,40}))?", str(v or ""))
    if not m:
        return None
    pre = m.group(4)
    pre_key: tuple = (1,) if pre is None else (0, *[(0, int(p), "") if p.isdigit() else (1, 0, p)
                                                    for p in pre.split(".")])
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre_key)


def running_version() -> str | None:
    """The release this server runs (its checkout's pyproject.toml, in the
    tags' spelling)."""
    from domovoi import android_app, git_version

    return android_app.semver(git_version._pyproject_version() or "")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _installed() -> set[tuple[str, str]]:
    """(name, version) of every package in the venv this core runs on."""
    from importlib import metadata

    out: set[tuple[str, str]] = set()
    for d in metadata.distributions():
        name = d.metadata.get("Name") if d.metadata else None
        if name and d.version:
            out.add((_norm(name), d.version))
    return out


_WHEEL_RE = re.compile(r"^([A-Za-z0-9_.]+?)-([0-9][^-]*?)(?:-[^/]+)?\.(?:whl|tar\.gz|zip)\Z")


def _package_of(filename: str) -> tuple[str, str] | None:
    m = _WHEEL_RE.match(filename)
    return (_norm(m.group(1)), m.group(2)) if m else None


def _image_present(ref: str, digest: str) -> bool:
    """Whether Docker holds the image compose pins (``repo@sha256:digest``)."""
    repo = ref.split("@", 1)[0].rsplit(":", 1)[0] if "@" in ref else ref.rsplit(":", 1)[0]
    try:
        r = _run(["docker", "image", "inspect", f"{repo}@sha256:{digest}"], capture_output=True,
                 text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def missing_packages() -> list[str]:
    """The ADDED_PACKAGES dpkg doesn't have installed ([] off Debian)."""
    if not shutil.which("dpkg-query"):
        return []
    out = []
    for p in ADDED_PACKAGES:
        try:
            r = _run(["dpkg-query", "-W", "-f=${Status}", p], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if "install ok installed" not in (r.stdout or ""):
            out.append(p)
    return out


def _phone_app_wanted(doc: dict[str, Any]) -> bool:
    """Whether the kit's phone app is newer than (or missing beside) the
    one this server hands out."""
    code = doc.get("android_version_code")
    if not isinstance(code, int):
        return False
    from domovoi import android_app

    try:
        m = android_app.load_manifest()
    except android_app.ManifestError:
        return True
    rel = m.get("release") or {}
    have = rel.get("version_code")
    if not isinstance(have, int) or code > have:
        return True
    return code == have and not android_app._complete(m)


def _venv_bytes() -> int:
    """The venv's size: the update keeps a copy of it to roll back to when
    the kit changes the Python packages."""
    total = 0
    for dirpath, _dirs, files in os.walk(sys.prefix):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def _free_bytes(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def update_unit_ready() -> str | None:
    """Why this server can't run an update from a kit, or None: the update
    unit (Linux, and Windows' Domovoi system) must be installed."""
    from domovoi import self_restart

    if self_restart.restart_mode() != "update":
        return ("This server can't update itself from a kit: its update service isn't installed. "
                "On Linux: sudo bash scripts/linux/install-update-unit.sh (docs/LINUX_HOST.md).")
    return None


def _channel_problem() -> str | None:
    from domovoi import git_version

    track = git_version.tracking()
    if track.get("channel") not in (None, "stable"):
        return (f"Updates from a kit follow the stable channel, and this server follows "
                f"{track['channel']}. Switch to stable in About & updates first.")
    return None


# ─── What the kit must send ──────────────────────────────────────────────

def _logical_entries(doc: dict[str, Any]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    seen: set[str] = set()
    for comp in doc["components"]:
        for entry in comp["files"]:
            if entry["path"] not in seen:
                seen.add(entry["path"])
                yield comp, entry


def _rooms_file(kind: str, path: str) -> bool:
    """A file of the kit's Rooms (P6), or one of the wake-word models every
    kit carries that a room's card serves: the update refreshes them
    (apply-update.sh, offline_rooms)."""
    from domovoi import kit_rooms

    return (kind == "rooms" and path.startswith("rooms/")) or (
        path.startswith("wake/") and path[len("wake/"):] in kit_rooms.OWW_ON_KIT)


def plan_need(doc: dict[str, Any], target: tuple[str, str, str]) -> list[dict[str, Any]]:
    """The files (as stored on the stick: parts, not joined files) this
    server needs from the kit, in the order to send them: the release's
    signed manifest, the source, then the rest."""
    from domovoi import kit_rooms

    kit_target, py_machine, deb_arch = target
    manifest = fmt.RELEASE_MANIFESTS[kit_target]["manifest"]
    first = {manifest: 0, f"{manifest}.sig": 1, "source/domovoi.bundle": 2}
    installed = _installed()
    want_apt = bool(missing_packages())
    want_phone = _phone_app_wanted(doc)
    picked: list[tuple[int, str, dict[str, Any]]] = []
    for comp, entry in _logical_entries(doc):
        path, kind = entry["path"], comp["kind"]
        why = None
        if path in first:
            why = "release" if path != "source/domovoi.bundle" else "source"
        elif path.startswith(f"python/cp314-{py_machine}/"):
            pkg = _package_of(path.rsplit("/", 1)[-1])
            if pkg is None or pkg not in installed:
                why = "python"
        elif kind == "container-image" and path.startswith("images/"):
            digest = comp.get("version") or ""
            if not fmt.SHA256_RE.match(digest) or not _image_present(comp.get("name") or "", digest):
                why = "images"
        elif want_apt and path.startswith(f"apt/{deb_arch}/ubuntu/"):
            why = "packages"
        elif want_phone and kind == "phone-app" and path.startswith("phone/") and path.endswith(".apk"):
            why = "phone"
        elif _rooms_file(kind, path) and not kit_rooms.held(path, entry["sha256"], entry["size"]):
            why = "rooms"
        if why:
            picked.append((first.get(path, 3), path, {"entry": entry, "why": why}))
    # The Windows room Setup is held to the release's SHA256SUMS on the stick.
    if any(p == f"rooms/{kit_rooms.SETUP_ASSET}" for _o, p, _i in picked):
        for _comp, entry in _logical_entries(doc):
            if entry["path"] == "release/SHA256SUMS":
                picked.append((3, entry["path"], {"entry": entry, "why": "rooms"}))
    picked.sort(key=lambda t: (t[0], t[1]))
    need = []
    for _o, _p, item in picked:
        entry = item["entry"]
        for stored in entry.get("parts") or [entry]:
            need.append({"path": stored["path"], "size": stored["size"], "sha256": stored["sha256"],
                         "file": entry["path"], "why": item["why"]})
    return need


def _received(path: str, size: int, joined: str | None = None) -> int:
    """How much of ``path`` the server holds; all of it once its split
    file (``joined``) has been put together."""
    done = files_dir() / path
    if done.is_file() or (joined and joined != path and (files_dir() / joined).is_file()):
        return size
    try:
        return min(size, (files_dir() / (path + ".partial")).stat().st_size)
    except OSError:
        return 0


def _disk_needed(need: list[dict[str, Any]]) -> int:
    """What the update takes on this disk: the files still to come, the
    copy the update unit makes of the packages and images it installs, and
    a copy of the venv when the Python packages change."""
    to_come = sum(n["size"] - _received(n["path"], n["size"], n.get("file")) for n in need)
    staged = sum(n["size"] for n in need if n["why"] in ("python", "images", "packages", "rooms"))
    venv = _venv_bytes() if any(n["why"] == "python" for n in need) else 0
    return to_come + staged + venv + HEADROOM


# ─── The view ────────────────────────────────────────────────────────────

def _public(state: dict[str, Any] | None) -> dict[str, Any]:
    if state is None:
        return {"session": None, "running_version": running_version()}
    need = state.get("need") or []
    files = [{"path": n["path"], "size": n["size"], "received": _received(n["path"], n["size"], n.get("file"))}
             for n in need]
    view = {k: state.get(k) for k in ("id", "version", "tag", "commit", "target", "state", "error",
                                      "created_at", "applied_at", "finished_at", "apps")}
    view.update(files=files, bytes_total=sum(f["size"] for f in files),
                bytes_received=sum(f["received"] for f in files),
                files_left=sum(1 for f in files if f["received"] < f["size"]),
                chunk_max=CHUNK_MAX,
                sends={w: sum(n["size"] for n in need if n["why"] == w)
                       for w in ("release", "source", "python", "images", "packages", "phone", "rooms")})
    return {"session": view, "running_version": running_version()}


def status() -> dict[str, Any]:
    """The card's view: the session (what was sent, its state, the last
    error), after :func:`settle`."""
    with _LOCK:
        settle()
        return _public(_load())


# ─── 1. kit.json ─────────────────────────────────────────────────────────

def _read_doc(raw: bytes) -> dict[str, Any]:
    if len(raw) > fmt.MAX_KIT_JSON:
        raise Refusal("kit_damaged", "This kit.json is bigger than any kit's. Make the stick again.")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refusal("kit_damaged", "This kit.json isn't readable. Choose the kit's own folder "
                                     "(the one with kit.json in it), or make the stick again.") from None
    try:
        problems, _notes = fmt.validate(doc)
    except fmt.KitProblem as e:
        raise Refusal("kit_newer_format", e.problems[0]) from None
    if problems:
        raise Refusal("kit_damaged", f"This kit's kit.json is not right ({problems[0]}). Make the "
                                     "stick again.")
    return doc


def _check_doc(doc: dict[str, Any]) -> tuple[str, str, str]:
    """The refusals kit.json alone settles, in the order the card says
    them. Returns this machine's target."""
    kit_v, run_v = doc["domovoi_version"], running_version()
    kk, rk = _version_key(kit_v), _version_key(run_v)
    if kk is None or rk is None:
        raise Refusal("version_unknown", f"This server can't tell which release it runs, so it can't "
                                         f"compare it with the kit's {kit_v}.")
    if kk <= rk:
        if kk == rk:
            msg = f"This kit is {kit_v}; this server already runs {run_v}."
        else:
            msg = f"This kit is {kit_v}; this server runs {run_v}, which is newer. Kits never downgrade."
        raise Refusal("not_newer", msg)
    target = host_target()
    if target is None:
        raise Refusal("wrong_arch", f"No kit is made for this server's machine ({platform.machine()}).")
    if target[0] not in doc["targets"]:
        def a(t: str) -> str:
            return fmt.TARGETS[t][0].lower() + fmt.TARGETS[t][1:]

        have = " and ".join(a(t) for t in doc["targets"])
        raise Refusal("wrong_arch", f"This kit is for {have}; this server is {a(target[0])} "
                                    f"({target[1]}). Make a kit with “{fmt.TARGETS[target[0]]}” ticked.")
    paths = {e["path"] for _c, e in _logical_entries(doc)}
    manifest = fmt.RELEASE_MANIFESTS[target[0]]["manifest"]
    for p in (manifest, f"{manifest}.sig", "source/domovoi.bundle"):
        if p not in paths:
            raise Refusal("kit_incomplete", f"This kit has no {p}, so it can't update a server. Make "
                                            "the stick again.")
    for why in (_channel_problem(), update_unit_ready()):
        if why:
            raise Refusal("cannot_update", why)
    return target


def begin(raw: bytes) -> dict[str, Any]:
    """Start (or pick up) an update from the kit whose kit.json is ``raw``.
    Raises :class:`Refusal`; answers the view with the files to send."""
    doc = _read_doc(raw)
    with _LOCK:
        settle()
        state = _load()
        if state and state.get("state") == "applying":
            raise Refusal("busy", "An update from a kit is under way. Wait for it to finish.")
        target = _check_doc(doc)
        resume = bool(state and state.get("commit") == doc["commit"]
                      and state.get("state") in ("uploading", "ready", "failed")
                      and (files_dir() / fmt.KIT_JSON).is_file()
                      and (files_dir() / fmt.KIT_JSON).read_bytes() == raw)
        need = plan_need(doc, target)
        if not resume:
            _remove_files()
        else:
            # Files already received stay; one the kit no longer needs goes.
            keep = {n["path"] for n in need}
            for n in state.get("need") or []:
                if n["path"] not in keep:
                    for p in (files_dir() / n["path"], files_dir() / (n["path"] + ".partial")):
                        p.unlink(missing_ok=True)
        needed, free = _disk_needed(need), _free_bytes(root())
        if needed > free:
            raise Refusal("disk", f"This update needs {_gb(needed)} free on the server. It has {_gb(free)}. "
                                  "Make room (Settings → System → Health shows what takes it), then try again.",
                          needed=needed, free=free)
        files_dir().mkdir(parents=True, exist_ok=True)
        (files_dir() / fmt.KIT_JSON).write_bytes(raw)
        state = {
            "id": state["id"] if resume and state else secrets.token_hex(16),
            "commit": doc["commit"], "version": doc["domovoi_version"], "tag": doc["tag"],
            "target": target[0], "state": "uploading", "error": None,
            "created_at": (state or {}).get("created_at") if resume else int(time.time()),
            "need": need, "release_ok": bool(resume and state and state.get("release_ok")),
        }
        _save(state)
        log.info("offline kit update: Domovoi %s (%s), %d files, %s to send", doc["domovoi_version"],
                 doc["commit"][:12], len(need), _gb(sum(n["size"] for n in need)))
        return _public(state)


# ─── 2. the files ────────────────────────────────────────────────────────

def _session(sid: str) -> dict[str, Any]:
    state = _load()
    if state is None or not secrets.compare_digest(str(state.get("id")), str(sid)):
        raise Refusal("no_session", "This update isn't going on any more. Choose the kit's folder again.",
                      status=404)
    return state


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def receive(sid: str, path: str, offset: int, body: bytes) -> dict[str, Any]:
    """Write ``body`` at ``offset`` of ``path``. The offset must be what the
    server holds (409 ``offset`` with ``received`` otherwise, so the page
    picks up from there). A finished file is held to kit.json's size and
    SHA-256 (422 ``pin``: it is thrown away)."""
    if len(body) > CHUNK_MAX:
        raise Refusal("too_big", f"A piece may be at most {CHUNK_MAX} bytes.", status=413)
    with _LOCK:
        state = _session(sid)
        if state.get("state") not in ("uploading", "failed"):
            raise Refusal("state", state.get("error") or "This update isn't taking files now.", status=409)
        item = next((n for n in state.get("need") or [] if n["path"] == path), None)
        if item is None:
            raise Refusal("not_needed", f"This update doesn't need {path}.", status=404)
        size = item["size"]
        done, partial = files_dir() / path, files_dir() / (path + ".partial")
        have = _received(path, size, item.get("file"))
        if have == size:
            return {"path": path, "received": size, "complete": True}
        if offset != have:
            raise Refusal("offset", f"The server has {have} bytes of {path}; send from there.",
                          received=have)
        if offset + len(body) > size:
            raise Refusal("pin", f"{path} is bigger than kit.json says ({size} bytes). The stick may be "
                                 "damaged: make it again.", status=422)
        partial.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(partial, "r+b" if partial.exists() else "wb") as f:
                f.seek(offset)
                f.write(body)
                f.truncate(offset + len(body))
        except OSError as e:
            raise Refusal("disk", f"The server couldn't keep {path} ({e.strerror or e}). It may be out "
                                  "of disk space.", status=507) from None
        received = offset + len(body)
        if received < size:
            return {"path": path, "received": received, "complete": False}
        if _hash(partial) != item["sha256"]:
            partial.unlink(missing_ok=True)
            raise Refusal("pin", f"{path} doesn't match the kit (its SHA-256 is not the one kit.json "
                                 "names), so it was thrown away. The stick may be damaged: make it "
                                 "again, or send it once more.", status=422, received=0)
        os.replace(partial, done)
        if state.get("state") == "failed":
            state["state"] = "uploading"
        _check_release_early(state)
        _save(state)
        return {"path": path, "received": size, "complete": True,
                "files_left": sum(1 for n in state["need"] if not (files_dir() / n["path"]).is_file())}


def _release_problem(state: dict[str, Any]) -> str | None:
    """Why the kit's signed release manifest doesn't vouch for kit.json, or
    None: Domovoi's release key, and the same version, tag and commit."""
    from domovoi.offline_kit import checker

    doc = json.loads((files_dir() / fmt.KIT_JSON).read_text(encoding="utf-8"))
    report = checker.Report(files_dir())
    checker._check_release(report, files_dir(), {**doc, "targets": [state["target"]]}, release_key)
    return report.errors[0] if report.errors else None


def _check_release_early(state: dict[str, Any]) -> None:
    """As soon as the manifest and its signature are here: a kit that isn't
    a Domovoi release stops before gigabytes follow it."""
    if state.get("release_ok"):
        return
    m = fmt.RELEASE_MANIFESTS[state["target"]]["manifest"]
    if not ((files_dir() / m).is_file() and (files_dir() / f"{m}.sig").is_file()):
        return
    problem = _release_problem(state)
    if problem:
        _remove_files()
        state.update(state="refused", error=f"This kit is not a Domovoi release: {problem} Nothing was "
                                            "changed; the files sent were removed.")
        _save(state)
        raise Refusal("signature", state["error"], status=422)
    state["release_ok"] = True


# ─── 3. apply ────────────────────────────────────────────────────────────

def _git(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    from domovoi import git_run, git_version
    from domovoi.config import settings

    env = {**git_version._GIT_ENV, "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0"}

    return git_run.run(["git", "-c", "advice.graftFileDeprecated=false", *args], cwd=settings.repo_dir,
                       timeout=timeout, env=env)


def move_checkout(commit: str, tag: str, bundle: Path) -> dict[str, Any]:
    """Fetch the kit's release out of ``bundle`` into the checkout and
    fast-forward to it, by the rules a pull follows (git_version.pull):
    the commit must be the signed manifest's, built on what runs now, and
    signed by an allowed signer while signing is enforced. ``origin/<channel>``
    moves to it, never backwards, so the update unit's branch rule holds.
    Returns ``{prev_sha, signature}``; raises :class:`Refusal`."""
    from domovoi import git_version

    ref = "refs/domovoi/offline-kit"
    r = _git("fetch", "--no-tags", "--no-write-fetch-head", "--no-auto-gc", str(bundle),
             f"+refs/tags/{tag}:{ref}")
    if r.returncode != 0:
        raise Refusal("bundle", f"The kit's source (source/domovoi.bundle) has no {tag} this server can "
                                f"read: {(r.stderr or '').strip()[-200:]} Make the stick again.")
    try:
        got = git_version._full_sha(ref)
        if got != commit:
            raise Refusal("bundle", f"The kit's source says {tag} is {str(got)[:12]}, but the signed "
                                    f"release is {commit[:12]}. Make the stick again.")
        head = git_version._full_sha("HEAD")
        if head is None or head == commit or not git_version._ancestor(head, commit):
            raise Refusal("not_newer", f"This kit's release {tag} isn't built on the release this server "
                                       "runs, so it can't update from it.")
        signature = git_version.verify_commit(commit)
        if signature.get("status") != "verified" and signature.get("enforced"):
            raise Refusal("signature", f"The kit's commit {commit[:12]} is {signature.get('detail')}, and "
                                       "signed updates are enforced on this server, so nothing was changed.")
        channel = git_version.tracking().get("channel") or "stable"
        remote = f"refs/remotes/origin/{channel}"
        old = git_version._full_sha(remote)
        if old is None or (old != commit and git_version._ancestor(old, commit)):
            u = _git("update-ref", "-m", f"offline kit {tag}", remote, commit, *([old] if old else []))
            if u.returncode != 0:
                raise Refusal("checkout", f"origin/{channel} couldn't be moved to {tag}: "
                                          f"{(u.stderr or '').strip()[-200:]}")
        elif old != commit and not git_version._ancestor(commit, old):
            raise Refusal("checkout", f"This server has seen another origin/{channel} that {tag} is not "
                                      "on, so it can't update from this kit.")
        baseline = git_version._rollback_baseline()
        proc, _notice = git_version._fast_forward(commit)
        if proc.returncode != 0:
            raise Refusal("checkout", "The checkout couldn't be moved to the kit's release: "
                                      + git_version._git_failure(proc, "git merge failed"))
        git_version.invalidate_sha_cache()
        git_version.invalidate_signature_cache()
        if baseline:
            git_version._write_prev_sha(baseline)
        return {"prev_sha": baseline, "signature": signature}
    except git_version._CheckoutBusy:
        raise Refusal("busy", "A restart or update is under way. Try again once it has finished.") from None
    finally:
        _git("update-ref", "-d", ref)


def _join_split(doc: dict[str, Any], state: dict[str, Any]) -> None:
    from domovoi.offline_kit import reassemble

    wanted = {n["file"] for n in state["need"] if n["file"] != n["path"]}
    for entry in fmt.split_files(doc):
        if entry["path"] not in wanted or (files_dir() / entry["path"]).is_file():
            continue
        try:
            reassemble(files_dir(), entry, files_dir() / entry["path"])
        except (OSError, ValueError) as e:
            raise Refusal("pin", f"{entry['path']} can't be put back together: {e}", status=422) from None
        for part in entry["parts"]:
            (files_dir() / part["path"]).unlink(missing_ok=True)


def prepare_apply(sid: str) -> dict[str, Any]:
    """Everything before the update unit starts: every file here, the
    release signature, the checkout at the kit's commit, the request for
    the unit. Raises :class:`Refusal`. The route then starts the unit."""
    with _LOCK:
        state = _session(sid)
        if state.get("state") == "applying":
            raise Refusal("busy", "An update from this kit is under way.")
        missing = [n["path"] for n in state["need"] if not (files_dir() / n["path"]).is_file()
                   and not (n["file"] != n["path"] and (files_dir() / n["file"]).is_file())]
        if missing:
            raise Refusal("not_complete", f"{len(missing)} file(s) of the kit are still to be sent, "
                                          f"starting with {missing[0]}.", missing=len(missing))
        why = update_unit_ready()
        if why:
            raise Refusal("cannot_update", why)
        problem = _release_problem(state)
        if problem:
            raise Refusal("signature", f"This kit is not a Domovoi release: {problem}", status=422)
        doc = json.loads((files_dir() / fmt.KIT_JSON).read_text(encoding="utf-8"))
        _join_split(doc, state)
        moved = move_checkout(state["commit"], state["tag"], files_dir() / "source" / "domovoi.bundle")
        req = root() / REQUEST
        tmp = req.with_name(f".{REQUEST}.tmp")
        tmp.write_bytes(f"commit={state['commit']}\n".encode("ascii"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, req)
        state.update(state="applying", error=None, applied_at=int(time.time()),
                     prev_sha=moved.get("prev_sha"))
        _save(state)
        return _public(state)


def restart_failed(sid: str, error: str) -> None:
    """The update unit couldn't be started: the checkout is already at the
    kit's release, so the next Restart (or update) applies it."""
    with _LOCK:
        state = _load()
        if state and state.get("id") == sid:
            state.update(error=f"The update service didn't start ({error}). Press Restart in About & "
                               "updates to apply the kit's release.")
            _save(state)


# ─── 4. after the update ─────────────────────────────────────────────────

_FAILED = {"failed", "rolled_back", "rollback_failed", "refused", "aborted"}


def _accept_apps() -> dict[str, Any]:
    """The kit's APKs, each through android_app.accept_upload (as an
    admin's upload: kept only if it is one of the release's files, signed
    with the release key)."""
    from domovoi import android_app

    apks = sorted((files_dir() / "phone").glob("*.apk")) if (files_dir() / "phone").is_dir() else []
    if not apks:
        return {"kept": 0, "refused": []}
    try:
        m = android_app.load_manifest()
    except android_app.ManifestError as e:
        return {"kept": 0, "refused": [str(e)]}
    kept, refused = 0, []
    for apk in apks:
        target = android_app.upload_target(m)
        try:
            shutil.copyfile(apk, target)
            android_app.accept_upload(m, target)
            kept += 1
        except android_app.ApkRefused as e:
            refused.append(f"{apk.name}: {e}")
        except OSError as e:
            target.unlink(missing_ok=True)
            refused.append(f"{apk.name}: {e.strerror or e}")
    return {"kept": kept, "refused": refused}


def settle() -> None:
    """After the update unit ran for an applying session: on success the
    phone app goes in and the uploaded copy goes; on failure the session
    says why and keeps its files for another try."""
    from domovoi import git_version

    with _LOCK:
        state = _load()
        if not state or state.get("state") != "applying":
            return
        last = git_version.read_last_update() or {}
        if last.get("to_sha") != state.get("commit") or last.get("status") == "running":
            return
        if last.get("status") == "ok":
            state["apps"] = _accept_apps()
            _remove_files()
            state.update(state="done", error=None, finished_at=int(time.time()), need=[])
            log.info("offline kit update: Domovoi %s applied; the uploaded kit was removed", state["version"])
        elif last.get("status") in _FAILED:
            (root() / REQUEST).unlink(missing_ok=True)
            state.update(state="failed", finished_at=int(time.time()),
                         error=f"The update to {state['version']} didn't go through: "
                               f"{last.get('error') or last.get('status')}. The kit's files are kept: "
                               "apply again, or cancel to remove them.")
        else:
            return
        _save(state)


def settle_at_start() -> None:
    try:
        settle()
    except Exception as e:  # noqa: BLE001 — never blocks the core's start
        log.warning("offline kit update: could not finish up after the update: %s", e)


def cancel() -> dict[str, Any]:
    """Remove the uploaded kit and the session. Refused while the update
    unit is applying it."""
    with _LOCK:
        settle()
        state = _load()
        if state and state.get("state") == "applying":
            raise Refusal("busy", "The update from this kit is under way; it can't be cancelled now.")
        shutil.rmtree(root(), ignore_errors=True)
        return _public(None)
