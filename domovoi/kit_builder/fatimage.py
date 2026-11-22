"""The kit's disk image: one FAT32 partition labelled ``DOMOVOIKIT``,
built without root (design section 3).

``mkfs.fat`` (dosfstools) formats a plain file and ``mcopy`` (mtools)
writes files into it: both are OS packages run as separate programs (GPL,
fine under the licence policy, like xorriso). No loop device, no mount,
no root. The image is a whole disk: an MBR with one FAT32 (LBA) partition
starting at 1 MiB, the layout every operating system mounts from a USB
stick. Raspberry Pi Imager writes it byte for byte.

Files are streamed in: :class:`FatImage` is a sink for
``domovoi.offline_kit.write_kit_to``; each file it opens is ``mcopy``'s
standard input, so the writer hashes the bytes as it hands them over and
nothing is staged on the server's disk.

:func:`image_size` says how big the image must be for a list of files,
before anything is made: every file rounded up to whole clusters, the
directories, both FATs, the reserved sectors and a margin. The card's fit
check uses it too.
"""

from __future__ import annotations

import math
import os
import secrets
import shutil
import struct
import subprocess
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable

SECTOR = 512
#: The partition starts at 1 MiB, as partitioning tools align it.
PART_OFFSET = 1024 * 1024
MIN_CLUSTERS = 65525 + 16          # FAT32's floor, and some room
MAX_CLUSTER = 32 * 1024
RESERVED_SECTORS = 32
MARGIN = 8 * 1024 * 1024
LABEL = "DOMOVOIKIT"


class FatError(RuntimeError):
    pass


def tools() -> dict[str, str] | None:
    """The programs this needs, or None when one is missing."""
    found = {n: shutil.which(n) for n in ("mkfs.fat", "mcopy", "mmd")}
    return found if all(found.values()) else None  # type: ignore[return-value]


def cluster_size(data_bytes: int) -> int:
    """The largest cluster (512 B … 32 KiB) that still leaves FAT32 enough
    clusters: big clusters for a big kit, small ones for a test image."""
    c = MAX_CLUSTER
    while c > SECTOR and data_bytes // c < MIN_CLUSTERS:
        c //= 2
    return c


def _dirs(paths: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        parent = PurePosixPath(p).parent
        while str(parent) not in (".", ""):
            out.add(str(parent))
            parent = parent.parent
    return out


def image_size(files: Iterable[tuple[str, int]]) -> int:
    """Bytes of an image that holds these (path, size) files."""
    files = list(files)
    raw = sum(s for _, s in files) + len(files) * MAX_CLUSTER
    c = cluster_size(max(raw, MIN_CLUSTERS * SECTOR))
    data = sum(max(1, math.ceil(s / c)) * c for _, s in files)
    # Directory entries: 32 bytes each plus long-name entries (13
    # characters per 32 bytes), per directory, rounded up to clusters.
    per_dir: dict[str, int] = {}
    for path, _ in files:
        parent = str(PurePosixPath(path).parent)
        name = PurePosixPath(path).name
        per_dir[parent] = per_dir.get(parent, 64) + 32 * (1 + math.ceil(len(name) / 13))
    for d in _dirs(p for p, _ in files):
        per_dir.setdefault(d, 64)
        parent = str(PurePosixPath(d).parent)
        per_dir[parent] = per_dir.get(parent, 64) + 32 * (1 + math.ceil(len(PurePosixPath(d).name) / 13))
    data += sum(math.ceil(b / c) * c for b in per_dir.values())
    clusters = max(MIN_CLUSTERS, data // c + 2)
    fat = 2 * math.ceil(clusters * 4 / SECTOR) * SECTOR
    total = PART_OFFSET + RESERVED_SECTORS * SECTOR + fat + clusters * c + MARGIN
    total += total // 200  # half a percent for what the sums above miss
    return math.ceil(total / (1024 * 1024)) * 1024 * 1024


def _mbr(total_bytes: int) -> bytes:
    """A DOS partition table with one FAT32 (LBA, 0x0C) partition from
    1 MiB to the end."""
    start = PART_OFFSET // SECTOR
    count = total_bytes // SECTOR - start
    entry = struct.pack("<B3sB3sII", 0x00, b"\xfe\xff\xff", 0x0C, b"\xfe\xff\xff", start, count)
    mbr = bytearray(SECTOR)
    mbr[440:444] = secrets.token_bytes(4)
    mbr[446:462] = entry
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr)


class _McopyWriter:
    """A file in the image being written: mcopy reads it from stdin."""

    def __init__(self, cmd: list[str], env: dict[str, str]) -> None:
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE, env=env)

    def write(self, data: bytes) -> int:
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(data)
        except BrokenPipeError:
            self._finish()
            raise FatError("mcopy stopped while a file was written into the image") from None
        return len(data)

    def _finish(self) -> None:
        if self._proc.stdin and not self._proc.stdin.closed:
            try:
                self._proc.stdin.close()
            except BrokenPipeError:
                pass
        err = self._proc.stderr.read().decode("utf-8", "replace") if self._proc.stderr else ""
        if self._proc.wait(timeout=600) != 0:
            raise FatError(f"mcopy failed: {err.strip()[-300:]}")

    def __enter__(self) -> "_McopyWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self._proc.kill()
            self._proc.wait()
            return
        self._finish()


class FatImage:
    """A new kit image at ``path`` of ``total_bytes``, formatted and ready
    for files (a ``write_kit_to`` sink)."""

    def __init__(self, path: Path, total_bytes: int, label: str = LABEL) -> None:
        found = tools()
        if found is None:
            raise FatError("this server has no mkfs.fat or mcopy (the dosfstools and mtools packages)")
        self.path = Path(path)
        self.total = total_bytes
        self._made: set[str] = set()
        self._env = {**os.environ, "MTOOLS_SKIP_CHECK": "1", "MTOOLS_NO_VFAT": "0"}
        self.target = f"{self.path}@@{PART_OFFSET}"
        with open(self.path, "xb") as f:
            f.truncate(total_bytes)
            f.seek(0)
            f.write(_mbr(total_bytes))
        part_bytes = total_bytes - PART_OFFSET
        spc = cluster_size(part_bytes) // SECTOR
        r = subprocess.run([found["mkfs.fat"], "-F", "32", "-n", label, "-s", str(spc), "-S", str(SECTOR),
                            "-R", str(RESERVED_SECTORS), "-h", str(PART_OFFSET // SECTOR),
                            "--offset", str(PART_OFFSET // SECTOR), str(self.path), str(part_bytes // 1024)],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise FatError(f"mkfs.fat failed: {(r.stderr or r.stdout).strip()[-300:]}")
        self._tools = found

    def _mkdirs(self, path: str) -> None:
        need = []
        parent = PurePosixPath(path).parent
        while str(parent) not in (".", "") and str(parent) not in self._made:
            need.append(str(parent))
            parent = parent.parent
        for d in reversed(need):
            r = subprocess.run([self._tools["mmd"], "-i", self.target, f"::/{d}"], capture_output=True,
                               text=True, env=self._env, timeout=120)
            if r.returncode != 0:
                raise FatError(f"mmd {d} failed: {(r.stderr or '').strip()[-300:]}")
            self._made.add(d)

    def open(self, path: str, size: int) -> BinaryIO:
        self._mkdirs(path)
        cmd = [self._tools["mcopy"], "-i", self.target, "-D", "o", "-", f"::/{path}"]
        return _McopyWriter(cmd, self._env)  # type: ignore[return-value]


def extract(image: Path, dest: Path) -> None:
    """Copy every file out of a kit image into ``dest`` (tests; a careful
    person checking an image without writing a stick)."""
    mcopy = shutil.which("mcopy")
    if not mcopy:
        raise FatError("no mcopy")
    dest.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([mcopy, "-s", "-n", "-i", f"{image}@@{PART_OFFSET}", "::/*", str(dest)],
                       capture_output=True, text=True, env={**os.environ, "MTOOLS_SKIP_CHECK": "1"},
                       timeout=3600)
    if r.returncode != 0:
        raise FatError(f"mcopy out failed: {(r.stderr or '').strip()[-300:]}")
