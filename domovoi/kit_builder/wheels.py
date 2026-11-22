"""Python packages for the kit, from the lock, for each target's machine.

provision.sh installs the venv with ``pip install --require-hashes`` from
``requirements-linux-py314.lock`` (and ``--no-deps`` from the no-deps
lock). Offline it runs the same with ``--no-index --find-links
<kit>/python/cp314-<arch>/``, so that folder must hold, for every locked
package, one file whose SHA-256 is among the lock's hashes and that pip
on that machine would take.

The builder doesn't run pip's resolver for a foreign machine. It reads the
lock (name, version, hashes), asks PyPI's JSON API which files that
release has (name, size, SHA-256), and picks, per machine, the best file
whose SHA-256 the lock lists: a CPython 3.14 wheel for the machine, then
an ``abi3`` one, then a pure-Python one, then the sdist (provision.sh
builds the few sdist-only packages with the lock's own build tools). The
picks, with sizes, are cached per lock (``cache/python/<lock sha>.json``),
so the card's sizes are exact once the server has looked once; the files
are cached by name in ``cache/python/files/``.

Machines: ``x86_64`` (a PC; Ubuntu 26.04's glibc) and ``aarch64`` (a
Mac's VM). Every request goes through the outbound gate (fetch.py).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from . import fetch, host

PYPI_JSON = "https://pypi.org/pypi"
#: Ubuntu 26.04's glibc: manylinux wheels up to this one install.
GLIBC_MAX = (2, 43)
PY = (3, 14)
ARCHES = ("x86_64", "aarch64")
MAX_JSON_BYTES = 16 * 1024 * 1024


class WheelError(RuntimeError):
    pass


@dataclass(frozen=True)
class Locked:
    name: str
    version: str
    hashes: frozenset[str]


@dataclass(frozen=True)
class Pick:
    name: str
    version: str
    filename: str
    url: str
    sha256: str
    size: int


def parse_lock(text: str) -> list[Locked]:
    """Every ``name==version`` stanza and its ``--hash=sha256:`` values."""
    out = []
    for m in re.finditer(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)((?:\s*\\?\s*--hash=sha256:[0-9a-f]{64})+)",
                         text, re.M):
        hashes = frozenset(re.findall(r"--hash=sha256:([0-9a-f]{64})", m.group(3)))
        out.append(Locked(name=m.group(1), version=m.group(2), hashes=hashes))
    return out


def _manylinux_ok(plat: str, arch: str) -> int:
    """A rank (higher is better) when ``plat`` installs on ``arch``, else 0."""
    if plat == "any":
        return 1
    legacy = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}
    m = re.fullmatch(r"(manylinux1|manylinux2010|manylinux2014)_(\w+)", plat)
    if m:
        glibc, a = legacy[m.group(1)], m.group(2)
    else:
        m = re.fullmatch(r"manylinux_(\d+)_(\d+)_(\w+)", plat)
        if not m:
            return 0
        glibc, a = (int(m.group(1)), int(m.group(2))), m.group(3)
    if a != arch or glibc > GLIBC_MAX:
        return 0
    return 10 + glibc[0] * 100 + glibc[1]


def rank(filename: str, arch: str) -> int:
    """How good ``filename`` is for CPython 3.14 on ``arch``: 0 = unusable,
    1 = the sdist, higher = better wheels."""
    if filename.endswith((".tar.gz", ".zip")):
        return 1
    if not filename.endswith(".whl"):
        return 0
    parts = filename[:-4].split("-")
    if len(parts) < 5:
        return 0
    pys, abis, plats = parts[-3].split("."), parts[-2].split("."), parts[-1].split(".")
    best = 0
    for py in pys:
        for abi in abis:
            if abi == f"cp{PY[0]}{PY[1]}" and py == f"cp{PY[0]}{PY[1]}":
                kind = 4
            elif abi == "abi3" and (m := re.fullmatch(r"cp3(\d+)", py)) and int(m.group(1)) <= PY[1]:
                kind = 3
            elif abi == "none" and py in ("py3", f"py{PY[0]}{PY[1]}", "py2.py3", f"cp{PY[0]}{PY[1]}"):
                kind = 2
            else:
                continue
            for plat in plats:
                pr = _manylinux_ok(plat, arch)
                if pr:
                    best = max(best, kind * 10000 + pr)
    return best


def choose(locked: Locked, files: list[dict], arch: str) -> Pick:
    """The best file of this release for ``arch`` whose SHA-256 the lock
    lists. Raises WheelError when there is none."""
    cands = []
    for f in files:
        sha = (f.get("digests") or {}).get("sha256")
        if sha not in locked.hashes:
            continue
        r = rank(str(f.get("filename", "")), arch)
        if r:
            cands.append((r, f))
    if not cands:
        raise WheelError(f"{locked.name}=={locked.version}: no file of it the lock allows installs "
                         f"on {arch}")
    cands.sort(key=lambda c: c[0], reverse=True)
    f = cands[0][1]
    name = str(f["filename"])
    if "/" in name or "\\" in name or name.startswith("."):
        raise WheelError(f"{locked.name}: PyPI names a file {name!r}")
    return Pick(name=locked.name, version=locked.version, filename=name, url=str(f["url"]),
                sha256=f["digests"]["sha256"], size=int(f["size"]))


def _json(locked: Locked) -> list[dict]:
    raw = fetch.fetch_bytes(f"{PYPI_JSON}/{quote(locked.name)}/{quote(locked.version)}/json",
                            max_bytes=MAX_JSON_BYTES)
    try:
        doc = json.loads(raw)
        files = doc["urls"]
        assert isinstance(files, list)
        return files
    except (ValueError, KeyError, AssertionError):
        raise WheelError(f"PyPI's answer for {locked.name} is not its JSON API") from None


def _cache() -> Path:
    return host.cache_dir("python")


def lock_key(lock_text: str, nodeps_text: str) -> str:
    return hashlib.sha256((lock_text + "\0" + nodeps_text).encode()).hexdigest()[:16]


def cached_picks(lock_text: str, nodeps_text: str) -> dict[str, list[Pick]] | None:
    """The picks made for this lock, when the server has looked before."""
    try:
        doc = json.loads((_cache() / f"{lock_key(lock_text, nodeps_text)}.json").read_text(encoding="utf-8"))
        return {a: [Pick(**p) for p in doc[a]] for a in ARCHES}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def resolve(lock_text: str, nodeps_text: str, files_of=None) -> dict[str, list[Pick]]:
    """{arch: picks} for both machines (one PyPI request per package; the
    answer is cached). ``files_of`` replaces the PyPI request in tests."""
    hit = cached_picks(lock_text, nodeps_text)
    if hit is not None:
        return hit
    files_of = files_of or _json
    locked = parse_lock(lock_text) + parse_lock(nodeps_text)
    if not locked:
        raise WheelError("the lock lists no packages")
    out: dict[str, list[Pick]] = {a: [] for a in ARCHES}
    for lk in locked:
        files = files_of(lk)
        for a in ARCHES:
            out[a].append(choose(lk, files, a))
    doc = {a: [p.__dict__ for p in out[a]] for a in ARCHES}
    (_cache() / f"{lock_key(lock_text, nodeps_text)}.json").write_text(json.dumps(doc), encoding="utf-8")
    return out


def file_path(p: Pick) -> Path:
    return _cache() / "files" / p.filename


def missing(picks: list[Pick]) -> list[Pick]:
    return [p for p in picks if not fetch.present(file_path(p), p.size)]


def download(picks: list[Pick], progress: fetch.Tick | None = None) -> None:
    for p in missing(picks):
        fetch.fetch_file(p.url, file_path(p), sha256=p.sha256, size=p.size, progress=progress)


def folder(arch: str) -> str:
    return f"python/cp{PY[0]}{PY[1]}-{arch}"
