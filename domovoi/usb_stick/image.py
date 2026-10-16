"""What goes on a Domovoi room stick, and the pinned Debian image under it.

Old computers (USB stick), 2026-10-16 (design-notes USB-STICK-SPIKE.md,
proven in QEMU). A room stick is **Debian's own, unmodified amd64 netinst
image** with about 1 MB of our files added; booted from a USB stick it
installs Debian by itself after one question (type ERASE, naming the disk)
and restarts into a room that installs the satellite with
``satellite/scripts/install-pc.sh --mode headless``.

This module makes our part, from inputs only (stdlib, no network, no
subprocess), so the same inputs give the same bytes:

* :func:`load_pin` — ``debian-image.json``: the netinst's name, version,
  download URLs (cdimage.debian.org only), size and SHA-256.
* :func:`iso_matches` — a file is exactly the pinned image.
* :func:`stick_files` — a tar of everything we add to the image:
  ``/domovoi/`` (the room code in ``stick.json``, the satellite bundle the
  server serves now, the extra initrd ``preseed.gz`` with the installer's
  answers and our two installer hooks, the room's first-boot files, the
  spoken-code clips) and our two boot menus, which replace Debian's
  ``/boot/grub/grub.cfg`` and ``/isolinux/isolinux.cfg`` (configuration,
  not code: Debian's signed shim and GRUB load them, and Debian's own
  kernel and initrd come first, ours second).

``repack.py`` then puts that tar into the image with xorriso (a separate
GPL program on the server's host, never bundled with Domovoi), keeping
Debian's BIOS and UEFI boot records. ``jobs.py`` is the core's half: the
download, the job, the cache, what the routes serve.

The stick's files themselves are in ``stick/`` beside this module:
``installer/`` runs inside Debian's installer (the ERASE page and the
refusals before it, then the copy into the new system), ``room/`` on the
installed room (first boot, the room's screen, the per-boot Secure Boot
check, the spoken code, the update policy). ``test-only/`` goes on a stick
only when ``Options.debug`` is set (the QEMU kit's serial markers).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STICK_DIR = HERE / "stick"
PIN_PATH = HERE / "debian-image.json"
#: The image's name, in every download and in Raspberry Pi Imager's list
#: (generic on purpose: the room's name is not in it).
IMAGE_NAME = "domovoi-room-stick.img"
#: Where the pinned image may be downloaded from: Debian's CD host (the
#: release folder, and the archive it moves to after the next point
#: release). The pin's size and SHA-256 make the mirror irrelevant.
URL_PREFIXES = (
    "https://cdimage.debian.org/debian-cd/",
    "https://cdimage.debian.org/cdimage/archive/",
)
_ISO_NAME_RE = re.compile(r"^debian-1[3-9]\.[0-9]{1,2}\.[0-9]{1,2}-amd64-netinst\.iso$")
_VERSION_RE = re.compile(r"^1[3-9]\.[0-9]{1,2}\.[0-9]{1,2}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MIN_ISO_BYTES = 300 * 1000 * 1000
MAX_ISO_BYTES = 2 * 1000 * 1000 * 1000
#: The spoken code (domovoi/canned_sounds.py SETUP_LINES), as the stick's
#: room plays them (stick/room/domovoi-status).
CLIP_NAMES = ("your_code_is.wav", *(f"digit_{d}.wav" for d in range(10)))
MAX_CLIP_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024

#: Executable files among the stick's own (everything else is 0644).
_EXECUTABLE = frozenset({
    "installer/erase-confirm.sh", "installer/late.sh", "installer/early.sh",
    "installer/udhcpc-default.script",
    "room/firstboot.sh", "room/boot-check.sh", "room/domovoi-status",
    "test-only/debug-serial.sh", "test-only/debug-room.sh",
})
#: The room's files, as /domovoi/room/<name> on the stick.
ROOM_FILES = ("firstboot.sh", "console.py", "boot-check.sh", "domovoi-status", "52domovoi-stick")
ROOM_UNITS = (
    "domovoi-stick-boot-check.service",
    "domovoi-stick-firstboot.service",
    "domovoi-stick-console.service",
)
INSTALLER_FILES = ("early.sh", "erase-confirm.sh", "late.sh", "lib.sh", "templates", "udhcpc-default.script")


class PinError(ValueError):
    """``debian-image.json`` is missing or malformed."""


class StickError(ValueError):
    """An input the stick can't be made from. ``reason`` names which."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# ─── the pinned Debian image ──────────────────────────────────────────────

def load_pin(path: Path | None = None) -> dict[str, Any]:
    """The pin, every field checked. Raises :class:`PinError`."""
    path = Path(path or PIN_PATH)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PinError(f"the Debian image pin is unreadable: {e}") from e
    if not isinstance(doc, dict) or doc.get("pin_version") != 1:
        raise PinError("the Debian image pin is not version 1")
    name, version = doc.get("name"), doc.get("version")
    if not isinstance(name, str) or not _ISO_NAME_RE.match(name):
        raise PinError(f"bad image name {name!r}")
    if not isinstance(version, str) or not _VERSION_RE.match(version) or f"debian-{version}-" not in name:
        raise PinError(f"bad version {version!r}")
    urls = doc.get("urls")
    if not isinstance(urls, list) or not urls:
        raise PinError("the pin names no download URL")
    for url in urls:
        if not isinstance(url, str) or not url.startswith(URL_PREFIXES) or not url.endswith("/" + name):
            raise PinError(f"{url!r} is not the image on Debian's CD host")
        if any(c in url for c in " \t\r\n?#\\") or ".." in url:
            raise PinError(f"{url!r} is not a plain URL")
    size, sha = doc.get("size"), doc.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or not MIN_ISO_BYTES <= size <= MAX_ISO_BYTES:
        raise PinError("bad size")
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise PinError("bad sha256")
    return {
        "pin_version": 1, "name": name, "version": version, "urls": list(urls),
        "size": size, "sha256": sha, "released": doc.get("released"),
        "shim_signatures": list(doc.get("shim_signatures") or []),
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# (path, size, mtime_ns, ino, ctime_ns, sha) -> matched: the 790 MB image is
# hashed once per change of the file, not on every status call.
_checked: dict[tuple[Any, ...], bool] = {}
_checked_lock = threading.Lock()


def iso_matches(path: Path, pin: dict[str, Any]) -> bool:
    """Whether ``path`` is a plain file of exactly the pinned size and
    SHA-256."""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return False
    if path.is_symlink() or not path.is_file() or st.st_size != pin["size"]:
        return False
    key = (str(path), st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns, pin["sha256"])
    with _checked_lock:
        if key in _checked:
            return _checked[key]
    ok = _sha256_file(path) == pin["sha256"]
    with _checked_lock:
        _checked[key] = ok
    return ok


# ─── the household's choices for the installer ────────────────────────────

_TZ_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]{0,40}(/[A-Za-z0-9_+-]{1,40}){0,2}$")
_LOCALE_RE = re.compile(r"^[a-z]{2,3}_[A-Z]{2}\.UTF-8$")
_KEYMAP_RE = re.compile(r"^[a-z]{2,8}([_-][a-z0-9]{1,12})?$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True)
class Options:
    """What the installer is told beyond the room: the household server's
    time zone, the language and keyboard (English and US unless the server
    says otherwise), whether the installer may use Wi-Fi (off until a real
    laptop has passed the owner's test, owner decision 2026-10-15), and
    ``debug``: the QEMU kit's serial markers (never on a household's stick)."""

    timezone: str = "Etc/UTC"
    locale: str = "en_US.UTF-8"
    keymap: str = "us"
    country: str = "US"
    wifi: bool = False
    debug: bool = False

    def check(self) -> None:
        for value, rx, what in (
            (self.timezone, _TZ_RE, "time zone"), (self.locale, _LOCALE_RE, "locale"),
            (self.keymap, _KEYMAP_RE, "keyboard"), (self.country, _COUNTRY_RE, "country"),
        ):
            if not isinstance(value, str) or not rx.match(value):
                raise StickError("bad_option", f"the {what} {value!r} can't go into the installer's answers")


# ─── the room ─────────────────────────────────────────────────────────────

def parse_room(payload: str):
    """The room code, parsed by the satellite's own parser
    (satellite/enrolment.py). Raises :class:`StickError`."""
    from satellite import enrolment

    try:
        return enrolment.parse(payload)
    except enrolment.EnrolmentError as e:
        raise StickError(e.reason, str(e)) from None


def menu_room(name: str) -> str:
    """The room name as a boot menu can show it: letters, digits, spaces
    and ``-.,()`` only (GRUB and ISOLINUX read quotes, ``$`` and ``\\``
    themselves), at most 40 characters; "new room" when nothing is left."""
    keep = "".join(c for c in unicodedata.normalize("NFKD", name)
                   if (c.isascii() and c.isalnum()) or c in " -.,()")
    return " ".join(keep.split())[:40].strip() or "new room"


def hostname_for(room_id: str) -> str:
    """The installed room's host name: ``domovoi-<room id>`` as a DNS label."""
    return ("domovoi-" + room_id.replace("_", "-"))[:63].rstrip("-")


# ─── our files ────────────────────────────────────────────────────────────

def _text(rel: str) -> bytes:
    """One of the stick's own files, with LF line ends whatever the
    checkout did (a CR breaks every shell script in the installer)."""
    return (STICK_DIR / rel).read_bytes().replace(b"\r\n", b"\n")


def _mode(rel: str) -> int:
    return 0o755 if rel in _EXECUTABLE else 0o644


def _fill(template: bytes, values: dict[str, str]) -> bytes:
    text = template.decode("utf-8")
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise StickError("bad_option", f"{key} has a line break")
        text = text.replace(f"@{key}@", value)
    leftover = re.search(r"@[A-Z_]+@", text)
    if leftover:
        raise StickError("template", f"{leftover.group(0)} was not filled in")
    return text.encode("utf-8")


def preseed(options: Options, hostname: str) -> bytes:
    """The installer's answers (``/preseed.cfg`` in our initrd). A test
    build's serial copier is started by early.sh when it is on the stick."""
    return _fill(_text("preseed.cfg.in"), {
        "LOCALE": options.locale, "COUNTRY": options.country, "KEYMAP": options.keymap,
        "HOSTNAME": hostname, "TIMEZONE": options.timezone,
    })


def menus(room: str) -> tuple[bytes, bytes]:
    """Our two boot menus: GRUB's (UEFI) and ISOLINUX's (BIOS)."""
    values = {"ROOM": menu_room(room), "KERNEL_EXTRA": ""}
    return _fill(_text("grub.cfg.in"), values), _fill(_text("isolinux.cfg.in"), values)


def _cpio_newc(entries: list[tuple[str, int, bytes | None]], epoch: int) -> bytes:
    """A newc cpio archive (what the kernel unpacks as an initramfs) of
    ``(name, permission bits, data or None for a directory)``, owned by
    root, every time ``epoch``, inode numbers in order: the same entries
    give the same bytes."""
    out = io.BytesIO()

    def pad() -> None:
        out.write(b"\0" * (-out.tell() % 4))

    def header(ino: int, mode: int, nlink: int, size: int, name: bytes) -> None:
        fields = (ino, mode, 0, 0, nlink, epoch if ino else 0, size, 0, 0, 0, 0, len(name) + 1, 0)
        out.write(b"070701" + "".join(f"{v:08x}" for v in fields).encode("ascii"))
        out.write(name + b"\0")
        pad()

    for ino, (name, perm, data) in enumerate(entries, start=1):
        if data is None:
            header(ino, 0o040000 | perm, 2, 0, name.encode("utf-8"))
        else:
            header(ino, 0o100000 | perm, 1, len(data), name.encode("utf-8"))
            out.write(data)
            pad()
    header(0, 0, 1, 0, b"TRAILER!!!")
    return out.getvalue()


def initrd(*, room: str, core: str, hostname: str, options: Options, epoch: int) -> bytes:
    """The extra initrd (``/domovoi/preseed.gz``) the boot menus load after
    Debian's own: the answers file, our installer hooks and what they need
    to know (the room's name, the server's address, Wi-Fi on or off), and
    with Wi-Fi off the line that keeps every Wi-Fi driver from loading in
    the installer. gzip'd with no name and no time."""
    files: dict[str, tuple[int, bytes | None]] = {
        "preseed.cfg": (0o644, preseed(options, hostname)),
        "domovoi-stick": (0o755, None),
        "domovoi-stick/room-name": (0o644, room.encode("utf-8") + b"\n"),
        "domovoi-stick/core-url": (0o644, core.encode("utf-8") + b"\n"),
        "domovoi-stick/wifi": (0o644, b"on\n" if options.wifi else b"off\n"),
    }
    for name in INSTALLER_FILES:
        files[f"domovoi-stick/{name}"] = (_mode(f"installer/{name}"), _text(f"installer/{name}"))
    if options.debug:
        files["domovoi-stick/debug-serial.sh"] = (0o755, _text("test-only/debug-serial.sh"))
    if not options.wifi:
        files["etc"] = (0o755, None)
        files["etc/modprobe.d"] = (0o755, None)
        files["etc/modprobe.d/domovoi-stick-no-wifi.conf"] = (0o644, _text("installer/no-wifi.conf"))
    entries = [(name, perm, data) for name, (perm, data) in sorted(files.items())]
    return gzip.compress(_cpio_newc(entries, epoch), compresslevel=9, mtime=0)


def stick_json(*, pin: dict[str, Any], enrol, payload: str, bundle_sha256: str,
               options: Options, epoch: int) -> bytes:
    """``/domovoi/stick.json``: what the installed room reads at first boot
    (the room, the room code, the bundle's SHA-256, when it was made)."""
    doc = {
        "version": 1,
        "room": enrol.room,
        "room_id": enrol.room_id,
        "core": enrol.core,
        "fp": enrol.fp,
        "payload": payload,
        "bundle_sha256": bundle_sha256,
        "debian": pin["version"],
        "wifi": bool(options.wifi),
        "made": int(epoch),
    }
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _tar(entries: list[tuple[str, int, bytes | None]], epoch: int) -> bytes:
    """A plain tar (ustar) of ``(name, permission bits, data or None for a
    directory)``, root's, every time ``epoch``: same entries, same bytes."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, perm, data in entries:
            info = tarfile.TarInfo(name)
            info.mtime = epoch
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mode = perm
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def stick_files(*, pin: dict[str, Any], payload: str, bundle: bytes, epoch: int,
                options: Options | None = None, clips: dict[str, bytes] | None = None
                ) -> tuple[bytes, dict[str, Any]]:
    """Everything a room stick adds to Debian's image, as a tar that
    ``repack.py`` maps into it (:data:`repack.MAPS`), and what it says
    about the room. Raises :class:`StickError`."""
    options = options or Options()
    options.check()
    if not isinstance(epoch, int) or epoch <= 0:
        raise StickError("bad_option", "no build time")
    if not bundle or len(bundle) > MAX_BUNDLE_BYTES:
        raise StickError("bundle", "the satellite bundle is empty or too large")
    enrol = parse_room(payload)
    bundle_sha = hashlib.sha256(bundle).hexdigest()
    hostname = hostname_for(enrol.room_id)
    grub, isolinux = menus(enrol.room)
    entries: list[tuple[str, int, bytes | None]] = [
        ("boot", 0o755, None), ("boot/grub", 0o755, None), ("boot/grub/grub.cfg", 0o644, grub),
        ("domovoi", 0o755, None),
        ("domovoi/preseed.gz", 0o644, initrd(room=enrol.room, core=enrol.core, hostname=hostname,
                                             options=options, epoch=epoch)),
        ("domovoi/room", 0o755, None),
        ("domovoi/room/units", 0o755, None),
        ("domovoi/satellite.tar.gz", 0o644, bundle),
        ("domovoi/stick.json", 0o644, stick_json(pin=pin, enrol=enrol, payload=payload,
                                                 bundle_sha256=bundle_sha, options=options, epoch=epoch)),
        ("isolinux", 0o755, None), ("isolinux/isolinux.cfg", 0o644, isolinux),
    ]
    for name in ROOM_FILES:
        entries.append((f"domovoi/room/{name}", _mode(f"room/{name}"), _text(f"room/{name}")))
    for name in ROOM_UNITS:
        entries.append((f"domovoi/room/units/{name}", 0o644, _text(f"room/units/{name}")))
    kept = {}
    for name, data in sorted((clips or {}).items()):
        if name in CLIP_NAMES and isinstance(data, bytes) and 44 < len(data) <= MAX_CLIP_BYTES \
                and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            kept[name] = data
    # The code is said only when every clip is there (a missing digit would
    # say a different code).
    if set(kept) == set(CLIP_NAMES):
        entries.append(("domovoi/sounds", 0o755, None))
        entries += [(f"domovoi/sounds/{n}", 0o644, d) for n, d in kept.items()]
    if options.debug:
        entries.append(("domovoi/test-only", 0o755, None))
        entries.append(("domovoi/test-only/domovoi-stick-debug.service", 0o644,
                        _text("test-only/domovoi-stick-debug.service")))
        entries.append(("domovoi/test-only/debug-room.sh", 0o755, _text("test-only/debug-room.sh")))
    entries.sort(key=lambda e: e[0])
    info = {
        "room": enrol.room, "room_id": enrol.room_id, "core": enrol.core, "fp": enrol.fp,
        "hostname": hostname, "bundle_sha256": bundle_sha, "bundle_size": len(bundle),
        "debian": pin["version"], "wifi": bool(options.wifi), "spoken_code": "domovoi/sounds" in
        {e[0] for e in entries}, "made": epoch,
    }
    return _tar(entries, epoch), info
