"""The satellite code as one file: what a spare computer's install command
downloads (spare-device satellites, S1, 2026-10-13).

The dashboard's "Add a spare device" shows a one-line command that fetches
``GET /v1/satellite-code/bundle.tar.gz`` from the core and runs
``satellite/scripts/install-pc.sh`` out of it — but only after
``sha256sum -c`` has matched the bundle against the SHA-256 the dashboard
put into the command. That digest is what makes plain HTTP on the LAN
safe enough here: a host that answers in the core's place, or changes the
download on the way, hands over different bytes and the command stops
before anything runs. The installer then proves the core's identity
against the fingerprint in the enrolment payload before it writes a thing
(satellite/enrolment.py ``verify``).

The bundle is exactly the satellite-code channel's file list (the same
allowlist, so no caches, backups or ``.env`` and none of the extension-less
root helpers a Pi installs), under ``satellite/``, and it is
**deterministic**: sorted entries, zeroed times and owners, fixed modes, a
gzip header without a time. The same tree gives the same bytes and so the
same digest, which is what lets the dashboard quote it ahead of the
download. Built on demand and kept while the tree's manifest is unchanged.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

#: The top directory inside the bundle; install-pc.sh lives at
#: ``satellite/scripts/install-pc.sh`` under the extraction directory.
ROOT_NAME = "satellite"
#: Files the installer marks executable.
_EXECUTABLE = (".sh",)
#: A bundle larger than this is refused (the tree is ~1.5 MB today).
MAX_BUNDLE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Bundle:
    data: bytes
    sha256: str
    files: int
    manifest_digest: str


def manifest(code_dir: Path, allowed: Callable[[Path], bool]) -> dict[str, str]:
    """``{relative path: sha256}`` of every file the channel would serve."""
    out: dict[str, str] = {}
    if not code_dir.is_dir():
        return out
    for p in code_dir.rglob("*"):
        if p.is_symlink() or not p.is_file() or not allowed(p):
            continue
        out[p.relative_to(code_dir).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def build(code_dir: Path, files: dict[str, str]) -> bytes:
    """The deterministic ``.tar.gz`` of ``files`` (a :func:`manifest`)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        dirs: set[str] = set()
        for rel in sorted(files):
            parts = rel.split("/")
            for i in range(1, len(parts)):
                d = "/".join(parts[:i])
                if d not in dirs:
                    dirs.add(d)
                    info = tarfile.TarInfo(f"{ROOT_NAME}/{d}")
                    info.type, info.mode = tarfile.DIRTYPE, 0o755
                    _zero(info)
                    tar.addfile(info)
            body = (code_dir / rel).read_bytes()
            if hashlib.sha256(body).hexdigest() != files[rel]:
                raise RuntimeError(f"{rel} changed while the bundle was being built")
            info = tarfile.TarInfo(f"{ROOT_NAME}/{rel}")
            info.size = len(body)
            info.mode = 0o755 if rel.endswith(_EXECUTABLE) else 0o644
            _zero(info)
            tar.addfile(info, io.BytesIO(body))
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0, compresslevel=9) as gz:
        gz.write(raw.getvalue())
    data = out.getvalue()
    if len(data) > MAX_BUNDLE_BYTES:
        raise RuntimeError(f"the satellite bundle is {len(data)} bytes, over {MAX_BUNDLE_BYTES}")
    return data


def _zero(info: tarfile.TarInfo) -> None:
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""


_lock = threading.Lock()
_cache: Bundle | None = None


def current(code_dir: Path, allowed: Callable[[Path], bool]) -> Bundle:
    """The bundle for the tree as it is now; rebuilt only when its file
    list or any file's digest changed. Blocking: call it off the loop."""
    global _cache
    files = manifest(code_dir, allowed)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()
    with _lock:
        if _cache is not None and _cache.manifest_digest == digest:
            return _cache
        data = build(code_dir, files)
        _cache = Bundle(data=data, sha256=hashlib.sha256(data).hexdigest(),
                        files=len(files), manifest_digest=digest)
        return _cache


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None
