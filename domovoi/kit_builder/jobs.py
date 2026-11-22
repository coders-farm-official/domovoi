"""The core's half of the offline kit: the background job that makes the
kit image, the images on disk, and what the routes serve.

The pattern is the room stick's (domovoi/usb_stick/jobs.py): one job at a
time on a thread, its state in memory (it survives a page reload, not a
restart), refusals with one plain reason each before anything starts
(plan.plan), random 32-character ids for the finished images, served with
a Raspberry Pi Imager list and manifest at ``/v1/offline-kit/<id>/…``
without a login (Imager and a browser download can't send one; nothing on
a kit is a secret: it is a public release and public models). Added here:
**cancel** (the job stops at the next chunk it copies or downloads and
removes what it made), and **auto-delete**: a kit image is kept for
:data:`KEEP_SEC` (3 days; the card says so) and only the newest one; the
old one goes when a new kit starts, which also frees its space for it.

Stages, in the card's words:

1. ``download``: each part's missing files, by pin, through the outbound
   gate (parts.Part.acquire): "Downloading the Windows parts… 1.1 of 2.4 GB".
2. ``write``: the FAT32 image (fatimage.FatImage) with every file streamed
   in by ``write_kit_to``: "Making the kit… 8.4 of 14.1 GB".
3. ``check``: the image's SHA-256, for Imager's list.

Layout: ``~/.domovoi/offline_kit/builds/<id>/`` holds
``domovoi-offline-kit-<version>.img`` and ``build.json``; a build in
progress is ``builds/.<id>/``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

from domovoi.offline_kit import Component, Source
from domovoi.offline_kit import format as fmt
from domovoi.offline_kit.write import check_components, write_kit_to

from . import fatimage, guide, host, plan
from .parts import Ctx, Part, Release

log = logging.getLogger(__name__)

KEEP_SEC = 3 * 24 * 3600
BUILD_JSON = "build.json"
FILES_PATH = "/v1/offline-kit/"
OS_LIST = "os-list.json"
MANIFEST = "domovoi-offline-kit.rpi-imager-manifest"
IMAGER_DEVICE_TAG = "domovoi-offline-kit"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{32}\Z")


class Cancelled(Exception):
    pass


def image_name(version: str) -> str:
    return f"domovoi-offline-kit-{version}.img"


def builds_root() -> Path:
    return host.kit_root() / "builds"


def valid_id(build_id: object) -> bool:
    return isinstance(build_id, str) and bool(_ID_RE.match(build_id))


# ─── builds on disk ──────────────────────────────────────────────────────

def _read_build(d: Path) -> dict[str, Any] | None:
    if not valid_id(d.name) or d.is_symlink() or not d.is_dir():
        return None
    try:
        doc = json.loads((d / BUILD_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("id") != d.name or doc.get("format") != 1:
        return None
    if not isinstance(doc.get("size"), int) or not isinstance(doc.get("sha256"), str):
        return None
    return doc


def list_builds() -> list[dict[str, Any]]:
    root = builds_root()
    out = []
    if root.is_dir():
        for d in root.iterdir():
            doc = _read_build(d)
            if doc is not None:
                out.append(doc)
    out.sort(key=lambda b: b.get("made") or 0, reverse=True)
    return out


def build_info(build_id: str) -> dict[str, Any] | None:
    return _read_build(builds_root() / build_id) if valid_id(build_id) else None


def image_file(build_id: str) -> Path | None:
    doc = build_info(build_id)
    if doc is None:
        return None
    path = builds_root() / build_id / image_name(doc["domovoi_version"])
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != doc["size"]:
            return None
    except OSError:
        return None
    return path


def delete_build(build_id: str) -> bool:
    if build_info(build_id) is None:
        return False
    shutil.rmtree(builds_root() / build_id, ignore_errors=True)
    return True


def builds_bytes() -> int:
    return sum(b["size"] for b in list_builds())


def prune(now: float | None = None, *, keep_newest: bool = True, mine: str | None = None) -> list[str]:
    """Delete kit images older than KEEP_SEC, every one but the newest,
    and half-made builds no job owns. The ids removed."""
    now = now or time.time()
    gone = []
    for i, doc in enumerate(list_builds()):
        if now - float(doc.get("made", 0)) > KEEP_SEC or (i > 0 and keep_newest) or not keep_newest:
            shutil.rmtree(builds_root() / doc["id"], ignore_errors=True)
            gone.append(doc["id"])
    root = builds_root()
    if root.is_dir():
        for d in root.iterdir():
            if d.name.startswith(".") and d.is_dir() and not d.is_symlink() and d.name != mine:
                with _lock:
                    owned = _job.get("work") == d.name
                if not owned:
                    shutil.rmtree(d, ignore_errors=True)
    return gone


def public_build(doc: dict[str, Any]) -> dict[str, Any]:
    base = f"{str(doc.get('core') or '').rstrip('/')}{FILES_PATH}{doc['id']}/"
    return {
        "id": doc["id"], "made": doc["made"], "expires": doc["made"] + KEEP_SEC,
        "domovoi_version": doc["domovoi_version"], "tag": doc["tag"], "size": doc["size"],
        "sha256": doc["sha256"], "targets": doc["targets"], "includes": doc["includes"],
        "installers": doc.get("installers", {}), "household": doc.get("household", []),
        "image_url": base + image_name(doc["domovoi_version"]),
        "os_list_url": base + OS_LIST, "manifest_url": base + MANIFEST,
    }


def os_list(doc: dict[str, Any]) -> dict[str, Any]:
    """Raspberry Pi Imager's list for one kit (its custom repository
    format, as the room stick's): one device, one image."""
    pub = public_build(doc)
    return {
        "imager": {"devices": [{
            "name": "A USB stick for the offline kit",
            "description": "Everything on the stick will be replaced",
            "tags": [IMAGER_DEVICE_TAG], "icon": "", "matching_type": "exclusive",
        }]},
        "os_list": [{
            "name": f"Domovoi offline kit {doc['domovoi_version']}",
            "description": ("Set up Domovoi in a house with no internet. Don't sell this stick: some "
                            "files on it may only be shared for free."),
            "icon": "", "url": pub["image_url"],
            "extract_size": doc["size"], "extract_sha256": doc["sha256"],
            "image_download_size": doc["size"], "image_download_sha256": doc["sha256"],
            "release_date": time.strftime("%Y-%m-%d", time.gmtime(doc["made"])),
            "devices": [IMAGER_DEVICE_TAG],
        }],
    }


# ─── the job ─────────────────────────────────────────────────────────────

_job: dict[str, Any] = {
    "running": False, "stage": None, "line": None, "started_at": None, "finished_at": None,
    "ok": None, "message": None, "reason": None, "bytes_done": 0, "bytes_todo": 0,
    "build": None, "work": None, "cancelling": False,
}
_lock = threading.Lock()
_cancel = threading.Event()


def job_state() -> dict[str, Any]:
    with _lock:
        return {k: v for k, v in _job.items() if k != "work"}


def _set(**kw: Any) -> None:
    with _lock:
        _job.update(**kw)


def busy() -> bool:
    with _lock:
        return bool(_job["running"])


def cancel() -> bool:
    """Ask the running job to stop. False when none runs."""
    with _lock:
        if not _job["running"]:
            return False
        _job["cancelling"] = True
    _cancel.set()
    return True


def _tick(n: int) -> None:
    if _cancel.is_set():
        raise Cancelled()
    with _lock:
        _job["bytes_done"] += n


def _components(rel: Release, sel: plan.Selection, ps: list[Part], ctx: Ctx) -> tuple[list[Component], dict]:
    comps: list[Component] = []
    installers: dict[str, str] = {}
    for p in ps:
        if _cancel.is_set():
            raise Cancelled()
        srcs = p.acquire(ctx)
        comps.append(Component(kind=p.kind, name=p.name, version=p.version, licence=p.licence,
                               files=tuple(srcs), household_added=p.household_added,
                               pinned_by=None if p.household_added else p.pinned_by))
    for c in comps:
        for s in c.files:
            if s.path in ("release/DomovoiSetup.exe", "release/DomovoiSetup-mac.pkg", "linux/install.sh"):
                data = s.src if isinstance(s.src, bytes) else None
                if data is not None:
                    installers[s.path.split("/")[-1]] = hashlib.sha256(data).hexdigest()
                else:
                    a = rel.assets.get(s.path.split("/")[-1])
                    if a:
                        installers[s.path.split("/")[-1]] = a["sha256"]
    docs = [{"licence": c.licence, "household_added": c.household_added} for c in comps]
    info = guide.GuideInfo(version=rel.version, tag=rel.tag, commit=rel.commit, targets=sel.targets,
                           includes=sel.includes, installers=installers,
                           notices=tuple(fmt.required_notices(docs)),
                           household=tuple(c.name for c in comps if c.household_added))
    comps.insert(0, Component(kind="guide", name="START-HERE", version=rel.version, licence="MIT",
                              files=(Source("START-HERE.txt", guide.text(info)),
                                     Source("START-HERE.html", guide.page(info)))))
    return comps, installers


def _sizes(comps: list[Component]) -> list[tuple[str, int]]:
    files: dict[str, int] = {}
    for c in comps:
        for s in c.files:
            files[s.path] = len(s.src) if isinstance(s.src, bytes) else Path(s.src).stat().st_size
    out = []
    for path, size in files.items():
        split = fmt.part_sizes(size, fmt.PART_SIZE)
        out += list(zip(fmt.part_paths(path, len(split)), split)) if split else [(path, size)]
    n = len(out)
    out += [("kit.json", 4096 + 400 * n), ("SHA256SUMS", 160 * n)]
    return out


def build(rel: Release, sel: plan.Selection, ps: list[Part], *, core: str = "",
          run=None) -> dict[str, Any]:
    """Make one kit (blocking: minutes to an hour). Returns build.json.
    Raises on failure or Cancelled, leaving nothing half-made."""
    import subprocess

    downloads = sum(p.download for p in ps)
    _set(stage="download", bytes_done=0, bytes_todo=downloads,
         line="Getting the parts ready")
    ctx = Ctx(targets=sel.targets, progress=_tick, say=lambda s: _set(line=s), run=run or subprocess.run)
    comps, installers = _components(rel, sel, ps, ctx)
    problems = check_components(version=rel.version, commit=rel.commit, components=comps,
                                targets=sel.targets, includes=sel.includes)
    if problems:
        raise RuntimeError("; ".join(problems))
    files = _sizes(comps)
    total = fatimage.image_size(files)
    free = plan.free_bytes(builds_root())
    if free < total + 256 * 1024 * 1024:
        raise RuntimeError(f"Needs {total / 1e9:.1f} GB free on the server for the kit image. "
                           f"It has {free / 1e9:.1f} GB.")
    build_id = secrets.token_urlsafe(24)[:32]
    while not valid_id(build_id):
        build_id = secrets.token_urlsafe(24)[:32]
    root = builds_root()
    root.mkdir(parents=True, exist_ok=True)
    work = root / f".{build_id}"
    _set(work=work.name)
    work.mkdir()
    try:
        img_path = work / image_name(rel.version)
        _set(stage="write", bytes_done=0, bytes_todo=sum(s for _, s in files), line="Making the kit")
        img = fatimage.FatImage(img_path, total)
        doc = write_kit_to(img, version=rel.version, commit=rel.commit, components=comps,
                           targets=sel.targets, includes=sel.includes,
                           android_version_code=_avc(comps), progress=_tick)
        _set(stage="check", bytes_done=0, bytes_todo=total, line="Checking the image")
        h = hashlib.sha256()
        with open(img_path, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
                _tick(len(chunk))
        made = time.time()
        bdoc = {
            "format": 1, "id": build_id, "made": made, "core": core,
            "domovoi_version": rel.version, "tag": rel.tag, "commit": rel.commit,
            "targets": list(sel.targets), "includes": list(sel.includes),
            "household": [c.name for c in comps if c.household_added],
            "installers": installers, "files": sum(len(c["files"]) for c in doc["components"]),
            "size": img_path.stat().st_size, "sha256": h.hexdigest(),
        }
        (work / BUILD_JSON).write_text(json.dumps(bdoc, indent=2) + "\n", encoding="utf-8")
        work.replace(root / build_id)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        _set(work=None)
    return bdoc


def _avc(comps: list[Component]) -> int | None:
    for c in comps:
        if c.kind == "phone-app":
            for s in c.files:
                if s.path == "phone/release.json" and isinstance(s.src, bytes):
                    try:
                        return int(json.loads(s.src)["release"]["version_code"])
                    except (ValueError, KeyError, TypeError):
                        return None
    return None


def start(rel: Release, sel: plan.Selection, ps: list[Part], *, core: str = "",
          on_done: Callable[[dict[str, Any] | None], None] | None = None) -> bool:
    """Start making the kit on a thread. False when one already runs."""
    with _lock:
        if _job["running"]:
            return False
        _job.update(running=True, stage="start", line=None, started_at=time.time(), finished_at=None,
                    ok=None, message=None, reason=None, bytes_done=0, bytes_todo=0, build=None,
                    cancelling=False)
    _cancel.clear()
    # The old kit goes first: one kit at a time, and its space is this one's.
    prune(keep_newest=False)

    def go() -> None:
        doc = None
        try:
            doc = build(rel, sel, ps, core=core)
            ok, msg, reason = True, f"Your offline kit is ready. Domovoi {rel.version}, {doc['size'] / 1e9:.1f} GB.", None
        except Cancelled:
            ok, msg, reason = False, "Stopped. Nothing was kept.", "cancelled"
        except Exception as e:  # noqa: BLE001 — said on the card until the next try
            log.warning("offline kit: %s", e, exc_info=True)
            ok, msg, reason = False, str(e) or type(e).__name__, "failed"
        _set(running=False, stage=None, line=None, finished_at=time.time(), ok=ok, message=msg,
             reason=reason, build=doc["id"] if doc else None, cancelling=False)
        _cancel.clear()
        if on_done:
            on_done(doc)

    threading.Thread(target=go, name="offline-kit-build", daemon=True).start()
    return True
