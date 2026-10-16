"""Write satellite/locks/linux-python.txt: the exact Python a Linux satellite
installs when it uses its own, with each file's size, SHA-256 and URL.

    python scripts/dev/linux_runtime_lock.py --release 20261009 \
        --python x86_64=3.14.8 --python aarch64=3.13.16 --cache <a scratch folder> [--check] [--licences]

A spare Linux computer whose own Python is too old (Ubuntu 22.04 and Mint 21
have 3.10, Debian 11 has 3.9), missing, or not a Debian-family system's
installs python-build-standalone's CPython (Astral, github.com/astral-sh/
python-build-standalone): a relocatable build for glibc 2.17 and newer, the
"install_only_stripped" archive. satellite/scripts/install-pc.sh and the
core's cache (domovoi/satellite_linux.py) both download exactly the file
this lock names and refuse any byte that doesn't match its size and SHA-256.

The lock is a plain text file so the installer reads it with bash alone (a
computer that needs this Python may have no other). This script is the only
thing that writes it:

1. for each ``--python MACHINE=VERSION`` it names the release's
   ``cpython-VERSION+RELEASE-MACHINE-unknown-linux-gnu-install_only_stripped.tar.gz``,
   which must have a hash-pinned package list in satellite/locks/
   (pc-cpXY-MACHINE.txt) — no list, no lock;
2. downloads it into ``--cache``, hashes it and checks the digest against
   the release's own SHA256SUMS;
3. checks the archive is the layout the installer expects (``python/bin/
   python3``, the version's ``lib-dynload``);
4. ``--licences``: reads each build's licence metadata (PYTHON.json, in the
   release's ``pgo+lto-full`` archive of the same build; needs Python 3.14's
   ``compression.zstd`` or the ``zstd`` tool) and stops if any module links
   a library whose licence is not on the permissive list, apart from the
   ``_dbm`` module (Berkeley DB, Sleepycat), which the installer deletes;
5. writes the lock (``--check``: compares with the one in the tree; exit 1
   if it would change).

Moving a pin is a deliberate act: run it with ``--licences``, read the diff,
commit the lock. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import posixpath
import re
import shutil
import subprocess
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "satellite" / "locks" / "linux-python.txt"
BASE = "https://github.com/astral-sh/python-build-standalone/releases/download/"
MACHINES = ("x86_64", "aarch64")
VARIANT = "install_only_stripped"
#: Licences a module of the pinned Python may link (SPDX ids as PYTHON.json
#: writes them). Anything else stops the lock.
PERMISSIVE = {
    "Python-2.0", "CNRI-Python", "MIT", "X11", "BSD-2-Clause", "BSD-3-Clause", "0BSD", "Zlib",
    "Apache-2.0", "OpenSSL", "bzip2-1.0.6", "TCL", "blessing", "Unlicense", "ISC",
}
#: Modules that link something else, and are deleted from the installed
#: Python by install-pc.sh (never loaded): module -> what and why.
REMOVED = {"_dbm": "Berkeley DB (Sleepycat licence, copyleft)"}

HEADER = """\
# The Python a Linux satellite installs when it uses its own
# (satellite/scripts/install-pc.sh: the computer's python3 is older than
# 3.11, missing, or not a Debian-family system's), and that the core fetches
# and caches for it (domovoi/satellite_linux.py). python-build-standalone's
# CPython (Astral, https://github.com/astral-sh/python-build-standalone),
# "{variant}", built for glibc 2.17 and newer, release {release}.
# Every file is checked against its size and SHA-256 before it is used.
# Written by scripts/dev/linux_runtime_lock.py; never edited by hand.
#
# Licences (each build's PYTHON.json, checked with --licences): CPython
# (PSF-2.0); OpenSSL (Apache-2.0), libffi (MIT), SQLite (public domain),
# zlib (Zlib), bzip2 (bzip2-1.0.6), xz (0BSD), zstd (BSD-3-Clause), expat
# (MIT), mpdecimal (BSD-2-Clause), libuuid (BSD-3-Clause), ncurses (X11),
# libedit in place of GNU readline (BSD-3-Clause), Tcl/Tk (TCL), libX11,
# libxcb and libXau (MIT/X11). No GNU readline and no GDBM are in it. The
# one copyleft part, the dbm module's Berkeley DB (Sleepycat), is deleted
# by the installer from both its copies (lib-dynload/_dbm.*.so) and never
# loaded. The archive itself is upstream's, unmodified (its SHA-256 is the
# pin): each household's own server fetches it from GitHub and hands it
# only to that household's computers; Domovoi never ships or hosts it.
#
# python <machine> <version> <file> <size> <sha256> <url>
"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    if dest.is_file():
        return
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=300) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    part.replace(dest)


def file_name(release: str, machine: str, version: str, variant: str = VARIANT, ext: str = "tar.gz") -> str:
    return f"cpython-{version}+{release}-{machine}-unknown-linux-gnu-{variant}.{ext}"


def url_for(release: str, name: str) -> str:
    return BASE + release + "/" + urllib.parse.quote(name)


def _inside(name: str) -> bool:
    return not name.startswith("/") and ".." not in name.split("/") and name.split("/")[0] == "python"


def check_layout(path: Path, version: str) -> None:
    """The archive is a plain tree under python/: regular files, folders and
    links that stay inside it, no setuid, setgid or sticky bit (root unpacks
    it), and the layout the installer expects (security review of
    wf/sat-pc-python)."""
    minor = ".".join(version.split(".")[:2])
    want = {"python/bin/python3", f"python/lib/python{minor}/lib-dynload"}
    with tarfile.open(path, "r:gz") as t:
        names = set()
        for m in t:
            if not _inside(m.name):
                raise SystemExit(f"{path.name}: unexpected member {m.name!r}")
            if not (m.isreg() or m.isdir() or m.issym() or m.islnk()):
                raise SystemExit(f"{path.name}: {m.name!r} is not a file, folder or link")
            if m.mode & 0o7000:
                raise SystemExit(f"{path.name}: {m.name!r} has a setuid, setgid or sticky bit")
            if m.issym():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(m.name), m.linkname))
                if m.linkname.startswith("/") or not _inside(target):
                    raise SystemExit(f"{path.name}: {m.name!r} links out of the tree ({m.linkname!r})")
            if m.islnk() and not _inside(posixpath.normpath(m.linkname)):
                raise SystemExit(f"{path.name}: {m.name!r} is a hard link out of the tree ({m.linkname!r})")
            # Every member and the folders above it (the archive need not list folders).
            parts = m.name.rstrip("/").split("/")
            names.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    missing = want - names
    if missing:
        raise SystemExit(f"{path.name}: not the expected layout (missing {sorted(missing)})")


def _zstd_bytes(path: Path) -> bytes:
    try:
        from compression import zstd  # Python 3.14+

        with zstd.open(path, "rb") as f:
            return f.read()
    except ImportError:
        tool = shutil.which("zstd")
        if not tool:
            raise SystemExit("--licences needs Python 3.14 (compression.zstd) or the zstd tool")
        return subprocess.run([tool, "-dc", str(path)], check=True, capture_output=True).stdout


def licences(release: str, machine: str, version: str, cache: Path, sums: dict[str, str]) -> list[str]:
    """Every built module's licences from the full build's PYTHON.json (the
    archive held to the release's SHA256SUMS first); raises SystemExit for
    one outside PERMISSIVE that is not REMOVED."""
    name = file_name(release, machine, version, "pgo+lto-full", "tar.zst")
    path = cache / name
    download(url_for(release, name), path)
    digest = sha256_file(path)
    if sums.get(name) != digest:
        raise SystemExit(f"{name}: the release's SHA256SUMS says {sums.get(name)}, the download is {digest}")
    with tarfile.open(fileobj=io.BytesIO(_zstd_bytes(path))) as t:
        doc = json.load(t.extractfile("python/PYTHON.json"))
    lines, bad = [], []
    for licence in doc.get("licenses") or []:
        if licence not in PERMISSIVE:
            bad.append(f"CPython itself: {licence}")
    for module, variants in sorted(doc["build_info"]["extensions"].items()):
        for v in variants:
            got = v.get("licenses") or []
            if got:     # the rest is CPython's own code
                lines.append(f"{module}: {', '.join(got)}" + (f"  (deleted: {REMOVED[module]})" if module in REMOVED else ""))
            outside = [x for x in got if x not in PERMISSIVE]
            if outside and module not in REMOVED:
                bad.append(f"{module}: {', '.join(outside)}")
    if bad:
        raise SystemExit(f"{machine} {version}: licences outside the permissive list: {bad}")
    return lines


def build(release: str, pythons: list[tuple[str, str]], cache: Path, *, check_licences: bool) -> str:
    if not re.fullmatch(r"[0-9]{8}", release):
        raise SystemExit("--release is a python-build-standalone release tag, e.g. 20261009")
    cache.mkdir(parents=True, exist_ok=True)
    sums_path = cache / f"SHA256SUMS-{release}"
    download(url_for(release, "SHA256SUMS"), sums_path)
    sums = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition(" ")
        sums[name.lstrip(" *")] = digest
    rows = []
    for machine, version in pythons:
        if machine not in MACHINES:
            raise SystemExit(f"{machine}: the installer knows {', '.join(MACHINES)}")
        tag = "cp" + "".join(version.split(".")[:2])
        if not (ROOT / "satellite" / "locks" / f"pc-{tag}-{machine}.txt").is_file():
            raise SystemExit(f"no hash-pinned package list satellite/locks/pc-{tag}-{machine}.txt for {version}")
        name = file_name(release, machine, version)
        path = cache / name
        download(url_for(release, name), path)
        digest = sha256_file(path)
        if sums.get(name) != digest:
            raise SystemExit(f"{name}: the release's SHA256SUMS says {sums.get(name)}, the download is {digest}")
        check_layout(path, version)
        if check_licences:
            for line in licences(release, machine, version, cache, sums):
                print(f"  {machine} {line}")
        rows.append(f"python {machine} {version} {name} {path.stat().st_size} {digest} {url_for(release, name)}")
    return HEADER.format(variant=VARIANT, release=release) + "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True, help="python-build-standalone release tag, e.g. 20261009")
    ap.add_argument("--python", action="append", required=True, metavar="MACHINE=VERSION",
                    help="e.g. x86_64=3.14.8 (repeat per machine)")
    ap.add_argument("--cache", type=Path, required=True, help="where the files are downloaded to")
    ap.add_argument("--licences", action="store_true", help="check every module's licence (downloads the full builds)")
    ap.add_argument("--check", action="store_true", help="compare with the lock in the tree")
    args = ap.parse_args(argv)
    pythons = []
    for item in args.python:
        machine, _, version = item.partition("=")
        if not re.fullmatch(r"3\.[0-9]+\.[0-9]+", version):
            raise SystemExit(f"--python {item}: MACHINE=VERSION, e.g. x86_64=3.14.8")
        pythons.append((machine, version))
    text = build(args.release, pythons, args.cache, check_licences=args.licences)
    if args.check:
        same = LOCK.is_file() and LOCK.read_text(encoding="utf-8") == text
        print("the lock is current" if same else "the lock would change")
        return 0 if same else 1
    LOCK.write_bytes(text.encode("utf-8"))
    print(f"wrote {LOCK} ({len(pythons)} builds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
