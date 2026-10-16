"""Put a room stick's files into Debian's image with xorriso.

Old computers (USB stick), 2026-10-16. ``image.stick_files`` makes a tar of
what we add; this unpacks it into a fresh folder and runs

    xorriso -indev debian.iso -outdev room.img \\
      -map <dir>/domovoi /domovoi \\
      -map <dir>/boot/grub/grub.cfg /boot/grub/grub.cfg \\
      -map <dir>/isolinux/isolinux.cfg /isolinux/isolinux.cfg \\
      -boot_image any replay

which copies every one of Debian's files and boot records as they are
(``-boot_image any replay``: the isohybrid MBR for BIOS PCs, the EFI
partition for UEFI ones, Secure Boot's signed shim untouched), adds
``/domovoi`` and swaps in our two boot menus. ``SOURCE_DATE_EPOCH`` is set
for xorriso, and every file's time comes from the tar, so the same inputs
give the same image byte for byte.

xorriso (GPL-3.0-or-later, from libisoburn) is the server host's own
system package, run as a separate program: never bundled with Domovoi.

Stdlib only and runnable on its own (``python3 -I repack.py --iso …
--files … --out … --epoch …``), so the QEMU kit can run exactly this step
in a Linux container for a Domovoi server that has no xorriso of its own
(a developer's Windows box; domovoi/usb_stick/jobs.py,
DOMOVOI_USB_STICK_REPACK).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

#: What the tar may hold and where each piece goes in the image.
MAPS = (
    ("domovoi", "/domovoi"),
    ("boot/grub/grub.cfg", "/boot/grub/grub.cfg"),
    ("isolinux/isolinux.cfg", "/isolinux/isolinux.cfg"),
)
_TOP = frozenset({"domovoi", "boot", "isolinux"})
TIMEOUT_SEC = 900


class RepackError(RuntimeError):
    """xorriso is missing, refused, or made something that is not an image."""


def find_xorriso() -> str | None:
    """xorriso on this host, or None."""
    for candidate in ("/usr/bin/xorriso", "/bin/xorriso"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("xorriso")


def unpack(files_tar: Path, dest: Path) -> None:
    """Unpack the stick's tar into ``dest``: plain files and folders only,
    under the three names :data:`MAPS` takes, nothing outside ``dest``
    (tarfile's ``data`` filter), times and modes as the tar has them."""
    with tarfile.open(files_tar, mode="r:") as tar:
        members = tar.getmembers()
        for m in members:
            top = m.name.split("/", 1)[0]
            if top not in _TOP or not (m.isfile() or m.isdir()):
                raise RepackError(f"the stick's files have an unexpected entry: {m.name!r}")
        tar.extractall(dest, members=members, filter="data")


def xorriso_args(xorriso: str, iso: Path, work: Path, out: Path) -> list[str]:
    args = [xorriso, "-report_about", "WARNING", "-indev", str(iso), "-outdev", str(out)]
    for rel, target in MAPS:
        args += ["-map", str(work / rel), target]
    # Root's, like Debian's own files: the image does not depend on which
    # account the server runs as.
    targets = [target for _rel, target in MAPS]
    args += ["-chown_r", "0", *targets, "--", "-chgrp_r", "0", *targets, "--"]
    return args + ["-boot_image", "any", "replay"]


def repack(iso: Path, files_tar: Path, out: Path, epoch: int, *, xorriso: str | None = None,
           run=subprocess.run) -> None:
    """Debian's image at ``iso`` plus the stick's files from ``files_tar``,
    written to ``out`` (replaced; nothing is left there on a failure).
    Raises :class:`RepackError`."""
    xorriso = xorriso or find_xorriso()
    if not xorriso:
        raise RepackError("xorriso is not installed on this server")
    iso, files_tar, out = Path(iso), Path(files_tar), Path(out)
    with tempfile.TemporaryDirectory(prefix="domovoi-stick-") as tmp:
        work = Path(tmp) / "files"
        work.mkdir()
        unpack(files_tar, work)
        for rel, _target in MAPS:
            if not (work / rel).exists():
                raise RepackError(f"the stick's files have no {rel}")
        part = out.with_name(f".{out.name}.part")
        part.unlink(missing_ok=True)
        env = dict(os.environ, SOURCE_DATE_EPOCH=str(int(epoch)), LC_ALL="C")
        try:
            r = run(xorriso_args(xorriso, iso, work, part), capture_output=True, text=True,
                    timeout=TIMEOUT_SEC, env=env)
            if r.returncode != 0:
                raise RepackError(f"xorriso failed (exit {r.returncode}): {(r.stderr or r.stdout or '')[-600:]}")
            try:
                size = part.stat().st_size
            except OSError:
                raise RepackError("xorriso wrote nothing") from None
            if size <= iso.stat().st_size:
                raise RepackError(f"xorriso's image is {size} bytes, no larger than Debian's")
            os.replace(part, out)
        except subprocess.TimeoutExpired as e:
            raise RepackError(f"xorriso took longer than {TIMEOUT_SEC} s") from e
        except OSError as e:
            raise RepackError(f"xorriso could not run: {e}") from e
        finally:
            part.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="repack.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--iso", required=True)
    ap.add_argument("--files", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epoch", required=True, type=int)
    ap.add_argument("--xorriso")
    a = ap.parse_args(argv)
    try:
        repack(Path(a.iso), Path(a.files), Path(a.out), a.epoch, xorriso=a.xorriso)
    except RepackError as e:
        print(f"repack: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
