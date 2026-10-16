"""The core's half of the room stick: the Debian download, the build, the
images it serves.

Old computers (USB stick), 2026-10-16. Settings → Satellites → add a spare
device → **Old computer (USB stick)**: an admin names the room and presses
"Prepare the stick". The core then, in one background job
(:func:`start`):

1. downloads Debian's netinst the first time (``debian-image.json``, from
   cdimage.debian.org only, through the outbound gate,
   domovoi/net_safety.py) into
   ``~/.domovoi/satellite_media/cache/usb_stick/<version>/`` and keeps it
   only if its size and SHA-256 are the pin's; refused under
   ``INTERNET_ACCESS=never`` unless it is already there;
2. makes the room's files (``image.stick_files``: the room code, the
   satellite bundle it serves now, the spoken-code clips) and puts them
   into a copy of the image with xorriso (``repack.py``);
3. keeps the result in ``~/.domovoi/satellite_media/usb_stick/<id>/`` with
   its ``build.json`` (size, SHA-256, room), at most :data:`MAX_BUILDS` of
   them and one per room (an older stick for the same room goes).

It refuses up front, with a reason the card shows (:func:`refusal`): a job
already running, no xorriso on this host, internet "never" without the
image cached, not enough disk. A failure stays on the card until the next
try (the job's ``message``).

``<id>`` is a random 32-character token: the image, its Raspberry Pi
Imager list and manifest are served at ``/v1/usb-stick/<id>/…`` without a
login (Imager and the browser's download can't send one), like the
satellite code bundle. Nothing on a stick is a secret: Debian's public
image, the public satellite code, and the room code (the server's address
and public fingerprint; the computer still waits for an admin to approve
its code). The token only keeps the list of rooms off the network.

Test seams (never set on a household's server):

* ``DOMOVOI_USB_STICK_REPACK`` — a command (a program path, or a JSON
  list) run as ``<command> --iso … --files … --out … --epoch …`` instead of
  ``repack.repack`` in this process: the QEMU kit's
  ``repack_in_docker.py``, for a core on a Windows box with no xorriso.
* ``DOMOVOI_USB_STICK_DEBUG=1`` — sticks carry the QEMU kit's serial
  markers (``image.Options.debug``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from domovoi.usb_stick import image, repack

log = logging.getLogger(__name__)

MAX_BUILDS = 3
#: Room for the image's copy beyond Debian's own size (our ~1 MB, the
#: files tar while it is made) and for the rest of the server.
MARGIN_BYTES = 256 * 1024 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
BUILD_JSON = "build.json"
FILES_PATH = "/v1/usb-stick/"
OS_LIST = "os-list.json"
MANIFEST = "domovoi-room-stick.rpi-imager-manifest"
IMAGER_DEVICE_TAG = "domovoi-room-stick"


def _media_root() -> Path:
    from domovoi.satellite_media import cache

    return cache.CACHE_ROOT.parent


def builds_root() -> Path:
    return _media_root() / "usb_stick"


def iso_path(pin: dict[str, Any]) -> Path:
    from domovoi.satellite_media import cache

    return cache.CACHE_ROOT / "usb_stick" / pin["version"] / pin["name"]


def valid_id(build_id: object) -> bool:
    return isinstance(build_id, str) and bool(_ID_RE.match(build_id))


# ─── what this server can do ─────────────────────────────────────────────

def repack_command() -> list[str] | None:
    """The test seam's command (module docstring), or None."""
    raw = (os.environ.get("DOMOVOI_USB_STICK_REPACK") or "").strip()
    if not raw:
        return None
    if raw.startswith("["):
        try:
            cmd = json.loads(raw)
        except ValueError:
            return None
        return [str(c) for c in cmd] if isinstance(cmd, list) and cmd else None
    return [raw]


def can_repack() -> bool:
    return repack_command() is not None or repack.find_xorriso() is not None


def free_bytes(path: Path) -> int:
    """Free space on the disk ``path`` is (or would be) on."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 0


def iso_present(pin: dict[str, Any]) -> bool:
    """Whether Debian's image looks cached: a plain file of the pinned size.
    Cheap, for the card and the refusals; the build holds it to the
    SHA-256 (:func:`image.iso_matches`) before it is used."""
    path = iso_path(pin)
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size == pin["size"]
    except OSError:
        return False


def need_bytes(pin: dict[str, Any], cached: bool) -> int:
    """What one more stick takes on this server's disk: its image (Debian's
    size plus ours) and, the first time, Debian's image itself."""
    return pin["size"] + MARGIN_BYTES + (0 if cached else pin["size"])


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def refusal(pin: dict[str, Any]) -> dict[str, str] | None:
    """Why "Prepare the stick" can't start now (``{reason, message}``), or
    None. Checked before anything is downloaded or written."""
    from domovoi import egress

    with _job_lock:
        if _job["running"]:
            return {"reason": "busy", "message": "A stick is being prepared right now; wait for it to finish."}
    if not can_repack():
        return {"reason": "no_xorriso", "message": (
            "This server can't make USB sticks yet: it needs the xorriso package (a Linux system "
            "package). The next Domovoi update or restart from the dashboard adds it; on a server "
            "you set up yourself: sudo apt install xorriso.")}
    cached = iso_present(pin)
    if not cached and egress.internet_turned_off():
        return {"reason": "internet_off", "message": (
            f"Internet access is off for this server (Settings → Internet), so it can't download "
            f"Debian {pin['version']} ({_gb(pin['size'])}), which every stick starts from. "
            "Turn internet access on, at least while the stick is prepared.")}
    free, need = free_bytes(builds_root()), need_bytes(pin, cached)
    if free < need:
        return {"reason": "disk", "message": (
            f"This server's disk has {_gb(free)} free; preparing a stick needs about {_gb(need)}"
            + ("" if cached else f" (Debian's image, {_gb(pin['size'])}, is kept for the next stick)")
            + ". Remove an older stick's image below, or free some space.")}
    return None


# ─── the builds on disk ──────────────────────────────────────────────────

def _read_build(d: Path) -> dict[str, Any] | None:
    if not valid_id(d.name) or d.is_symlink() or not d.is_dir():
        return None
    try:
        doc = json.loads((d / BUILD_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("id") != d.name or doc.get("version") != 1:
        return None
    if not isinstance(doc.get("size"), int) or not isinstance(doc.get("sha256"), str):
        return None
    return doc


def list_builds() -> list[dict[str, Any]]:
    """Every finished stick image, newest first."""
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
    if not valid_id(build_id):
        return None
    return _read_build(builds_root() / build_id)


def image_file(build_id: str) -> Path | None:
    """The image of a finished build, if it is still the size it was made
    at (the SHA-256 is in its list and manifest; Imager checks it)."""
    doc = build_info(build_id)
    if doc is None:
        return None
    path = builds_root() / build_id / image.IMAGE_NAME
    try:
        st = path.stat()
    except OSError:
        return None
    if path.is_symlink() or not path.is_file() or st.st_size != doc["size"]:
        return None
    return path


def delete_build(build_id: str) -> bool:
    if build_info(build_id) is None:
        return False
    shutil.rmtree(builds_root() / build_id, ignore_errors=True)
    return True


def public_build(doc: dict[str, Any]) -> dict[str, Any]:
    """What the dashboard card shows of one build, with its links."""
    base = f"{doc['core'].rstrip('/')}{FILES_PATH}{doc['id']}/"
    return {
        "id": doc["id"], "room": doc["room"], "room_id": doc["room_id"], "made": doc["made"],
        "size": doc["size"], "sha256": doc["sha256"], "debian": doc["debian"],
        "wifi": doc.get("wifi", False), "spoken_code": doc.get("spoken_code", False),
        "image_url": base + image.IMAGE_NAME,
        "os_list_url": base + OS_LIST,
        "manifest_url": base + MANIFEST,
    }


def os_list(doc: dict[str, Any]) -> dict[str, Any]:
    """Raspberry Pi Imager's list for one stick (its custom-repository
    format, doc/json-schema/os-list-schema.json in rpi-imager): one device,
    "a computer", and one image, named generically. No ``init_format``, so
    Imager offers no OS customisation; ``extract_sha256`` is checked by
    Imager while it writes. Imager sends usage statistics only for
    Raspberry Pi's own list, never for a list like this one."""
    pub = public_build(doc)
    made = time.strftime("%Y-%m-%d", time.gmtime(doc["made"]))
    return {
        "imager": {
            "devices": [{
                "name": "A computer (an old PC or an Intel Mac)",
                "description": "Becomes a Domovoi room: its disk is erased after you type ERASE on it",
                "tags": [IMAGER_DEVICE_TAG],
                "icon": "",
                "matching_type": "exclusive",
            }],
        },
        "os_list": [{
            "name": "Domovoi room stick",
            "description": (f"Debian {doc['debian']} with your Domovoi server's room code. Boot the "
                            "old computer from the stick; it asks before it erases anything."),
            "icon": "",
            "url": pub["image_url"],
            "extract_size": doc["size"],
            "extract_sha256": doc["sha256"],
            "image_download_size": doc["size"],
            "image_download_sha256": doc["sha256"],
            "release_date": made,
            "devices": [IMAGER_DEVICE_TAG],
        }],
    }


def _prune(keep: dict[str, Any]) -> None:
    """One stick per room, at most :data:`MAX_BUILDS`; half-made ones from
    a server that stopped mid-build go too."""
    root = builds_root()
    kept = 1
    for doc in list_builds():
        if doc["id"] == keep["id"]:
            continue
        if doc["room_id"] == keep["room_id"] or kept >= MAX_BUILDS:
            shutil.rmtree(root / doc["id"], ignore_errors=True)
        else:
            kept += 1
    if root.is_dir():
        for d in root.iterdir():
            if d.name.startswith(".") and d.is_dir() and not d.is_symlink():
                with _job_lock:
                    mine = _job.get("work") == d.name
                if not mine:
                    shutil.rmtree(d, ignore_errors=True)


# ─── the job ─────────────────────────────────────────────────────────────

_job: dict[str, Any] = {
    "running": False, "stage": None, "room": None, "room_id": None, "started_at": None,
    "finished_at": None, "ok": None, "message": None, "reason": None,
    "bytes_done": 0, "bytes_todo": 0, "build": None, "work": None,
}
_job_lock = threading.Lock()


def job_state() -> dict[str, Any]:
    with _job_lock:
        return {k: v for k, v in _job.items() if k != "work"}


def _set(**kw: Any) -> None:
    with _job_lock:
        _job.update(**kw)


def status(pin: dict[str, Any]) -> dict[str, Any]:
    """The card's view: Debian's image (cached or not), whether a stick can
    be prepared now (and why not), the job, and the finished sticks."""
    from domovoi import egress

    cached = iso_present(pin)
    return {
        "debian": {"version": pin["version"], "name": pin["name"], "size": pin["size"],
                   "cached": cached, "shim_signatures": pin.get("shim_signatures") or []},
        "refusal": refusal(pin),
        "internet_off": egress.internet_turned_off(),
        "disk_free": free_bytes(builds_root()),
        "need_bytes": need_bytes(pin, cached),
        "job": job_state(),
        "builds": [public_build(b) for b in list_builds()],
    }


def _download(pin: dict[str, Any]) -> None:
    """Debian's image into the cache, held to the pin (each URL in turn)."""
    from domovoi.satellite_media import fetchers

    target = iso_path(pin)
    if image.iso_matches(target, pin):
        return
    refused = fetchers._turned_off(f"Debian {pin['version']}")
    if refused:
        raise RuntimeError(refused[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    # Older pins' images are not needed any more.
    for old in target.parent.parent.iterdir() if target.parent.parent.is_dir() else []:
        if old.is_dir() and old.name != pin["version"] and not old.is_symlink():
            shutil.rmtree(old, ignore_errors=True)
    _set(stage="download", bytes_done=0, bytes_todo=pin["size"])

    def progress(n: int) -> None:
        with _job_lock:
            _job["bytes_done"] += n

    errors = []
    for url in pin["urls"]:
        _set(bytes_done=0)
        try:
            fetchers._fetch_pinned(url, target, sha256=pin["sha256"], size=pin["size"], progress=progress)
            return
        except Exception as e:  # noqa: BLE001 — one URL's failure; the next may work
            log.warning("USB stick: %s refused: %s", url, e)
            errors.append(f"{url.split('/')[2]}: {e}")
    raise RuntimeError(f"Debian {pin['version']} could not be downloaded: " + "; ".join(errors)[:600])


def _repack(iso: Path, files_tar: Path, out: Path, epoch: int) -> None:
    cmd = repack_command()
    if cmd is None:
        repack.repack(iso, files_tar, out, epoch)
        return
    r = subprocess.run([*cmd, "--iso", str(iso), "--files", str(files_tar), "--out", str(out),
                        "--epoch", str(epoch)], capture_output=True, text=True,
                       timeout=repack.TIMEOUT_SEC * 2)
    if r.returncode != 0 or not out.is_file():
        raise repack.RepackError(f"the repack command failed (exit {r.returncode}): {(r.stderr or '')[-600:]}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(pin: dict[str, Any], payload: str, bundle: bytes, options: image.Options,
          clips: dict[str, bytes] | None = None, epoch: int | None = None) -> dict[str, Any]:
    """Make one stick (blocking: a minute or more, plus the download the
    first time). Returns its ``build.json``. Raises on any failure, leaving
    nothing half-made behind."""
    _download(pin)
    iso = iso_path(pin)
    _set(stage="check")
    if not image.iso_matches(iso, pin):
        raise RuntimeError(f"the cached Debian image is not the pinned {pin['name']}")
    epoch = int(epoch or time.time())
    files, info = image.stick_files(pin=pin, payload=payload, bundle=bundle, epoch=epoch,
                                    options=options, clips=clips)
    build_id = secrets.token_urlsafe(24)[:32]
    while not valid_id(build_id):
        build_id = secrets.token_urlsafe(24)[:32]
    root = builds_root()
    root.mkdir(parents=True, exist_ok=True)
    work = root / f".{build_id}"
    _set(stage="build", work=work.name)
    work.mkdir()
    try:
        tar = work / "files.tar"
        tar.write_bytes(files)
        out = work / image.IMAGE_NAME
        _repack(iso, tar, out, epoch)
        tar.unlink(missing_ok=True)
        doc = {
            "version": 1, "id": build_id, "room": info["room"], "room_id": info["room_id"],
            "core": info["core"], "fp": info["fp"], "made": epoch, "debian": info["debian"],
            "wifi": info["wifi"], "spoken_code": info["spoken_code"],
            "bundle_sha256": info["bundle_sha256"],
            "size": out.stat().st_size, "sha256": _sha256(out),
        }
        (work / BUILD_JSON).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        os.replace(work, root / build_id)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        _set(work=None)
    _prune(doc)
    return doc


def start(pin: dict[str, Any], payload: str, bundle_fn: Callable[[], bytes], options: image.Options,
          clips: dict[str, bytes] | None = None) -> tuple[bool, dict[str, str] | None]:
    """Start preparing a stick for the room in ``payload`` on a thread.
    ``(started, refusal)``: a refusal (:func:`refusal`) starts nothing."""
    why = refusal(pin)
    if why is not None:
        return False, why
    enrol = image.parse_room(payload)
    with _job_lock:
        if _job["running"]:
            return False, {"reason": "busy", "message": "A stick is being prepared right now."}
        _job.update(running=True, stage="start", room=enrol.room, room_id=enrol.room_id,
                    started_at=time.time(), finished_at=None, ok=None, message=None, reason=None,
                    bytes_done=0, bytes_todo=0, build=None)

    def run() -> None:
        try:
            doc = build(pin, payload, bundle_fn(), options, clips)
            ok, message, reason, built = True, f"The stick for {doc['room']} is ready.", None, doc["id"]
        except image.StickError as e:
            ok, message, reason, built = False, str(e), e.reason, None
        except repack.RepackError as e:
            ok, message, reason, built = False, str(e), "repack", None
        except Exception as e:  # noqa: BLE001 — said on the card, kept until the next try
            ok, message, reason, built = False, str(e) or type(e).__name__, "failed", None
        _set(running=False, stage=None, finished_at=time.time(), ok=ok, message=message,
             reason=reason, build=built)
        log.info("USB stick: %s", message)

    threading.Thread(target=run, name="usb-stick-build", daemon=True).start()
    return True, None
