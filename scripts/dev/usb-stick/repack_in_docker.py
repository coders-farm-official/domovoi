"""Room stick kit: domovoi/usb_stick/repack.py in a Linux container.

For a Domovoi core on this Windows box (ft.py's throwaway core), which has
no xorriso: the core runs this through DOMOVOI_USB_STICK_REPACK with the
repack step's own arguments (--iso --files --out --epoch). Everything goes
in on the container's stdin as one tar (Debian's image, the stick's files,
repack.py itself) and the image comes back on stdout: Docker Desktop's bind
mounts served just-written files empty or stale in the spike, and the core
writes the image it just downloaded.

The container (USB_STICK_KIT_IMAGE, built from the Dockerfile beside this:
Debian 13, xorriso, python3) runs pinned to the E-cores with no network,
named domovoi-usb-stick-repack-<pid>, removed after.

  python repack_in_docker.py --iso X.iso --files Y.tar --out Z.img --epoch N
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import threading
from pathlib import Path

IMAGE = os.environ.get("USB_STICK_KIT_IMAGE", "domovoi-usb-stick-kit:1")
REPACK = Path(__file__).resolve().parents[3] / "domovoi" / "usb_stick" / "repack.py"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True)
    ap.add_argument("--files", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epoch", required=True, type=int)
    a = ap.parse_args(argv)
    docker = shutil.which("docker") or r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    inner = (
        "set -e; mkdir -p /w; tar -xf - -C /w; "
        "python3 -I /w/repack.py --iso /w/debian.iso --files /w/files.tar --out /w/out.img "
        f"--epoch {int(a.epoch)} >&2; cat /w/out.img"
    )
    cmd = [docker, "run", "--rm", "-i", "--cpuset-cpus", "16-31", "--network", "none",
           "--name", f"domovoi-usb-stick-repack-{os.getpid()}", IMAGE, "bash", "-c", inner]
    out = Path(a.out)
    part = out.with_name(out.name + ".kit")
    errors: list[str] = []
    with open(part, "wb") as sink:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=sink, stderr=subprocess.PIPE)

        def feed() -> None:
            try:
                with tarfile.open(fileobj=p.stdin, mode="w|") as tar:
                    tar.add(a.iso, arcname="debian.iso")
                    tar.add(a.files, arcname="files.tar")
                    tar.add(str(REPACK), arcname="repack.py")
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
            finally:
                try:
                    p.stdin.close()
                except OSError:
                    pass

        t = threading.Thread(target=feed, daemon=True)
        t.start()
        err = p.stderr.read().decode("utf-8", "replace")
        rc = p.wait()
        t.join(timeout=60)
    if rc != 0 or errors:
        part.unlink(missing_ok=True)
        print(f"repack in docker failed (exit {rc}): {err[-800:]} {errors}", file=sys.stderr)
        return 1
    os.replace(part, out)
    if err.strip():
        print(err.strip()[-800:], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
