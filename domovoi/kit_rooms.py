"""Rooms from the offline kit: what a server installed from a kit serves to
a new room (release 1.5.0, offline kit P6; docs/OFFLINE_KIT.md "Adding
rooms with no internet").

An online server fetches each room's software when an admin asks (the
Windows Setup, the Windows laptop's Python and wheels, a Linux computer's
Python, the Raspberry Pi card's packages, Debian's installer for the room
stick) and a Linux laptop takes its wheels from PyPI. A house with no
internet can do none of that, so the kit carries them under ``rooms/``
(domovoi/kit_builder/parts.py ``rooms_parts``) and, at install,
``provision.sh --offline-kit`` stages them off the stick (each file checked
against kit.json) and runs :func:`install`, which puts each one where the
server already serves it from, checked again against the pin in this
checkout (the release the kit is), never trusting the stick's word:

(on the stick -> under ``~/.domovoi/satellite_media/cache/``, held to)

- ``wake/<model>`` and ``rooms/satellite/oww_models/<model>`` ->
  ``oww_models/``, ``fetchers.OWW_MODEL_FILES``;
- ``rooms/DomovoiSatelliteSetup.exe`` -> ``satellite_setup/`` with its
  record, the release's ``SHA256SUMS`` on the stick (and "MZ");
- ``rooms/windows/<ver>/<file>`` -> ``windows/<ver>/``,
  ``satellite/windows-runtime.json``;
- ``rooms/linux-python/<file>`` -> ``linux-python/``,
  ``satellite/locks/linux-python.txt``;
- ``rooms/linux-wheels/<file>`` -> ``linux-wheels/``, a hash some
  ``satellite/locks/pc-*.txt`` lists for that package;
- ``rooms/<Debian's netinst>`` -> ``usb_stick/<version>/``,
  ``domovoi/usb_stick/debian-image.json``;
- ``rooms/satellite/{wheels,debs,dtbo,xvf_host}/...`` -> the same buckets,
  no pin (Pi OS's packages and the satellite's requirement ranges): "from
  the house that made this kit".

The Setup's record says ``offline kit vX.Y.Z``; the Setup is not
code-signed (owner decision 2026-10-21), so, as online, what vouches for it
is the release's checksum list, here the copy on the stick.

What the server then holds is served exactly as a fetched copy would be;
nothing else changes. :func:`install` writes a record
(``~/.domovoi/offline_kit/rooms-from-kit.json``: the kit's version, its
tick boxes, what was placed and what the kit lacked), and :func:`note`
reads it to say plainly, where a card would otherwise only say "internet
access is off", that the kit this server came from has no such file and
what brings it.

Command line (provision.sh): ``python -m domovoi.kit_rooms install DIR
--version V --includes "ai rooms"`` and ``python -m domovoi.kit_rooms done
V`` (status 0 when the record is that version's).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RECORD = "rooms-from-kit.json"
SETUP_ASSET = "DomovoiSatelliteSetup.exe"
#: The openWakeWord models a room needs, as the kit names them (wake/).
OWW_ON_KIT = ("embedding_model.onnx", "melspectrogram.onnx", "silero_vad.onnx", "hey_jarvis_v0.1.onnx")
#: What each piece is called in a message.
PIECES = {
    "wake": "the rooms' wake-word models",
    "windows-setup": "the Windows room Setup",
    "windows-runtime": "the Windows room's Python and packages",
    "linux-python": "a Linux room's own Python",
    "linux-wheels": "a Linux room's Python packages",
    "pi": "the Raspberry Pi card's packages",
    "debian": "Debian's installer for the room stick",
}
_PI_BUCKETS = ("wheels", "debs", "dtbo", "xvf_host")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}\Z")


def record_path() -> Path:
    return Path("~/.domovoi/offline_kit").expanduser() / RECORD


def read_record() -> dict[str, Any] | None:
    try:
        doc = json.loads(record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("version"), str) else None


def note(what: str, *, ready: bool) -> str | None:
    """The plain words for a room's file this server lacks, when it was
    installed from an offline kit; None when it holds it or wasn't. Shown
    beside (or instead of) "internet access is off"."""
    if ready:
        return None
    rec = read_record()
    if rec is None:
        return None
    version = rec["version"]
    if "rooms" not in (rec.get("includes") or []):
        return (f"This server was set up from an offline kit (Domovoi {version}) made without Rooms ticked, "
                f"so it has no {what}. Make a kit with Rooms ticked on an online Domovoi server and update "
                "from it (Settings → System → About & updates), or allow internet for a while.")
    return (f"The offline kit this server was set up from (Domovoi {version}) has no {what}. Make the kit "
            "again on an online Domovoi server and update from it, or allow internet for a while.")


def done(version: str) -> bool:
    rec = read_record()
    return rec is not None and rec.get("version") == version


# ─── placing files ───────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _place(src: Path, dest: Path) -> None:
    """``src`` to ``dest``: moved when it can be (the stage is this run's
    own, so no second copy of 1.5 GB), else copied; atomically either way."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.kit"
    try:
        try:
            os.replace(src, tmp)
        except OSError:
            shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


class _Report:
    def __init__(self) -> None:
        self.placed: dict[str, int] = {}
        self.refused: list[str] = []
        self.missing: list[str] = []

    def put(self, piece: str, src: Path, dest: Path) -> None:
        _place(src, dest)
        self.placed[piece] = self.placed.get(piece, 0) + 1

    def refuse(self, path: str, why: str) -> None:
        self.refused.append(f"{path}: {why}")
        log.warning("offline kit rooms: %s refused: %s", path, why)


def _pinned(rep: _Report, piece: str, src: Path, dest: Path, sha256: str, size: int | None, path: str) -> bool:
    if not _regular(src):
        return False
    if (size is not None and src.stat().st_size != size) or _sha256(src) != sha256:
        rep.refuse(path, "not the pinned file")
        return False
    rep.put(piece, src, dest)
    return True


def _files(root: Path, prefix: str) -> list[tuple[str, Path]]:
    base = root / prefix
    if not base.is_dir():
        return []
    return [(f"{prefix}/{f.relative_to(base).as_posix()}", f) for f in sorted(base.rglob("*")) if _regular(f)]


def install(root: Path, *, version: str, includes: list[str], code_dir: Path) -> dict[str, Any]:
    """Put the rooms' files from ``root`` (laid out as on the stick) where
    this server serves them, each held to its pin in ``code_dir`` (the
    checkout). Never raises for one bad file: it is refused, named, and
    left out. Writes the record and returns it."""
    from domovoi import satellite_linux, satellite_setup, satellite_windows
    from domovoi.kit_builder import wheels
    from domovoi.satellite_media import cache, fetchers
    from domovoi.usb_stick import image as stick_image

    root = Path(root)
    code_dir = Path(code_dir)
    rep = _Report()

    # Wake-word models (base, not a tick box): served at /v1/wake-base/.
    # Four are in wake/ on every kit; the rest come with Rooms (a Pi's card
    # needs every pinned one).
    oww = cache.CACHE_ROOT / "oww_models"
    for name, (sha, size) in fetchers.OWW_MODEL_FILES.items():
        where = f"wake/{name}" if name in OWW_ON_KIT else f"rooms/satellite/oww_models/{name}"
        _pinned(rep, "wake", root / where, oww / name, sha, size, where)
    if fetchers.missing_oww_models(oww):
        rep.missing.append("wake")

    # The Windows room Setup, held to the release's SHA256SUMS on the stick.
    src = root / "rooms" / SETUP_ASSET
    if _regular(src):
        try:
            sums = (root / "release" / "SHA256SUMS").read_text(encoding="utf-8")
        except OSError:
            sums = ""
        want = satellite_setup.parse_sums(sums)
        with open(src, "rb") as f:
            mz = f.read(2) == b"MZ"
        sha = _sha256(src)
        if want is None:
            rep.refuse(f"rooms/{SETUP_ASSET}", "the release's SHA256SUMS on the stick doesn't list it")
        elif sha != want or not mz:
            rep.refuse(f"rooms/{SETUP_ASSET}", "not the release's file")
        else:
            size = src.stat().st_size
            dest = cache.bucket(satellite_setup.CACHE_BUCKET)
            rep.put("windows-setup", src, dest / SETUP_ASSET)
            (dest / satellite_setup.RECORD_NAME).write_text(json.dumps(
                {"sha256": sha, "size": size, "release": f"offline kit v{version}", "fetched_at": time.time()},
                indent=2), encoding="utf-8")
    if satellite_setup.cached_file() is None:
        rep.missing.append("windows-setup")

    # The Windows room's Python and wheels.
    try:
        wlock = satellite_windows.load_lock(code_dir / "satellite" / satellite_windows.LOCK_NAME)
        wver = str(wlock["python"]["version"])
        for e in satellite_windows.entries(wlock):
            name = e["file"]
            _pinned(rep, "windows-runtime", root / "rooms" / "windows" / wver / name,
                    satellite_windows.bucket(wlock) / name, e["sha256"], int(e["size"]),
                    f"rooms/windows/{wver}/{name}")
        if not satellite_windows.status(wlock)["ready"]:
            rep.missing.append("windows-runtime")
    except (satellite_windows.LockError, KeyError, TypeError) as e:
        rep.refuse("rooms/windows/", f"this checkout's Windows lock can't be read: {e}")
        rep.missing.append("windows-runtime")

    # A Linux room's own Python.
    try:
        llock = satellite_linux.load_lock(code_dir / "satellite" / satellite_linux.LOCK_REL)
        for e in satellite_linux.entries(llock):
            _pinned(rep, "linux-python", root / "rooms" / "linux-python" / e["file"],
                    satellite_linux.bucket() / e["file"], e["sha256"], e["size"], f"rooms/linux-python/{e['file']}")
        if not satellite_linux.status(llock)["ready"]:
            rep.missing.append("linux-python")
    except satellite_linux.LockError as e:
        rep.refuse("rooms/linux-python/", f"this checkout's Linux Python lock can't be read: {e}")
        rep.missing.append("linux-python")

    # A Linux room's wheels: kept when some spare-computer lock allows the
    # file's bytes for its package.
    allowed: dict[str, set[str]] = {}
    for lock_file in sorted((code_dir / "satellite" / "locks").glob("pc-*.txt")):
        for lk in wheels.parse_lock(lock_file.read_text(encoding="utf-8")):
            allowed.setdefault(satellite_linux.normalize(lk.name), set()).update(lk.hashes)
    wdir = satellite_linux.wheel_dir()
    for path, src in _files(root, "rooms/linux-wheels"):
        name = src.name
        if "/" in path[len("rooms/linux-wheels/"):] or not name.endswith(".whl") or not _NAME_RE.match(name):
            rep.refuse(path, "not a wheel's name")
            continue
        if _sha256(src) not in allowed.get(satellite_linux.normalize(name.split("-", 1)[0]), set()):
            rep.refuse(path, "no lock in this release allows it")
            continue
        rep.put("linux-wheels", src, wdir / name)
    locks_dir = code_dir / "satellite" / "locks"
    if not any(satellite_linux.wheels_status(locks_dir).values()):
        rep.missing.append("linux-wheels")

    # Debian's installer for the room stick.
    try:
        pin = stick_image.load_pin()
        _pinned(rep, "debian", root / "rooms" / pin["name"],
                cache.CACHE_ROOT / "usb_stick" / pin["version"] / pin["name"], pin["sha256"], pin["size"],
                f"rooms/{pin['name']}")
        if not stick_image.iso_matches(cache.CACHE_ROOT / "usb_stick" / pin["version"] / pin["name"], pin):
            rep.missing.append("debian")
    except stick_image.PinError as e:
        rep.refuse("rooms/", f"the room stick's Debian pin can't be read: {e}")
        rep.missing.append("debian")

    # The Raspberry Pi card's cache: no pin (Pi OS's packages); only the
    # known buckets, plain names, regular files.
    pi = 0
    for path, src in _files(root, "rooms/satellite"):
        rel = path[len("rooms/satellite/"):]
        parts = rel.split("/")
        if parts[0] == "oww_models":
            continue   # pinned, placed above
        if parts[0] not in _PI_BUCKETS or len(parts) > 3 or not all(_NAME_RE.match(p) for p in parts):
            rep.refuse(path, "not a Raspberry Pi cache file")
            continue
        rep.put("pi", src, cache.CACHE_ROOT.joinpath(*parts))
        pi += 1
    if pi:
        cache.stamp("wheels")
        cache.stamp("debs")
    from domovoi.satellite_media import boards

    if not any((cache.CACHE_ROOT / "wheels" / boards.PI02W.python_version).glob("*.whl")):
        rep.missing.append("pi")

    doc = {
        "version": version, "includes": sorted(set(includes)), "installed_at": time.time(),
        "placed": rep.placed, "refused": rep.refused, "missing": rep.missing,
    }
    record_path().parent.mkdir(parents=True, exist_ok=True)
    record_path().write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def summary(doc: dict[str, Any]) -> str:
    """One line for the install's log."""
    placed = sum(doc.get("placed", {}).values())
    out = f"{placed} room files from the offline kit"
    if doc.get("missing"):
        out += "; not on the kit: " + ", ".join(PIECES.get(m, m) for m in doc["missing"])
    if doc.get("refused"):
        out += f"; {len(doc['refused'])} refused (not the pinned file)"
    return out


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m domovoi.kit_rooms")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("root", type=Path)
    i.add_argument("--version", required=True)
    i.add_argument("--includes", default="")
    i.add_argument("--code-dir", type=Path, default=None)
    d = sub.add_parser("done")
    d.add_argument("version")
    args = ap.parse_args(argv)
    if args.cmd == "done":
        return 0 if done(args.version) else 1
    code_dir = args.code_dir
    if code_dir is None:
        from domovoi.config import settings

        code_dir = Path(settings.repo_dir)
    doc = install(args.root, version=args.version, includes=args.includes.split(), code_dir=code_dir)
    for line in doc["refused"]:
        print("refused " + line)
    print(summary(doc))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
