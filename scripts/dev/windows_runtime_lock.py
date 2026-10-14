"""Write satellite/windows-runtime.json: the exact Python and wheels a Windows
satellite installs, each with its URL, SHA-256, size and licence.

    python scripts/dev/windows_runtime_lock.py --python 3.14.8 --pip 26.2.1 \
        --cache <a scratch folder> [--check]

The Windows satellite (docs/SPARE_DEVICE_SATELLITES.md) installs an embeddable
CPython and a fixed set of win_amd64 wheels. Nothing is resolved on the
laptop: the installer and the core's cache (domovoi/satellite_windows.py)
both download exactly the files this lock names and refuse any byte that
doesn't match its SHA-256 and size. This script is the only thing that
resolves, here, on a maintainer's machine:

1. pip resolves satellite/requirements.txt + requirements-windows.txt for
   win_amd64 and the lock's CPython (binary wheels only; the requirement
   markers are evaluated for that target, so spidev and the Pi's
   onnxruntime pin drop out), plus openwakeword without its dependencies
   (its tflite pin is Linux-only and unused) and pip itself (the installer
   runs pip from its wheel);
2. every file is downloaded into ``--cache`` and checked against the hash
   PyPI reported, and the embeddable zip from python.org is hashed;
3. the lock is written (``--check``: compared with the one in the tree, and
   exit 1 if it would change).

Moving a pin is a deliberate act: run it, read the diff (the licence column
included), commit the lock. Stdlib only, plus pip.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "satellite" / "windows-runtime.json"
PLATFORM = "win_amd64"
PYTHON_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"
#: What a wheel bundles beyond its own licence, said once here so the lock
#: and the guide say it too (checked by hand from each wheel's licence files).
BUNDLED_NOTES = {
    "numpy": "bundles OpenBLAS (BSD-3-Clause), LAPACK (BSD-3-Clause) and the GCC runtime "
             "(GPL-3.0-or-later WITH GCC-exception-3.1: the exception permits this use)",
    "scipy": "bundles OpenBLAS (BSD-3-Clause), LAPACK (BSD-3-Clause) and the GCC runtime "
             "(GPL-3.0-or-later WITH GCC-exception-3.1: the exception permits this use)",
    "sounddevice": "bundles PortAudio (MIT); its ASIO-enabled builds (Steinberg ASIO SDK) "
                   "are deleted after install and never loaded",
    "onnxruntime": "third-party notices in the wheel (ThirdPartyNotices.txt), all permissive",
}
#: Removed from site-packages after install: never used, and not ours to ship.
REMOVE_AFTER_INSTALL = [
    "_sounddevice_data/portaudio-binaries/libportaudio32bit-asio.dll",
    "_sounddevice_data/portaudio-binaries/libportaudio64bit-asio.dll",
    "_sounddevice_data/portaudio-binaries/libportaudioarm64-asio.dll",
]
PYTHON_LICENSE = ("PSF-2.0; bundles OpenSSL (Apache-2.0), libffi (MIT), SQLite (public domain), "
                  "zlib-ng (Zlib), bzip2 (bzip2-1.0.6), xz (0BSD), zstd (BSD-3-Clause), expat (MIT), "
                  "mpdecimal (BSD-2-Clause), HACL* (MIT), libtommath (Unlicense): LICENSE.txt in the zip")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, sha256: str | None = None) -> tuple[str, int]:
    if not dest.is_file() or (sha256 and sha256_file(dest) != sha256):
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    got = sha256_file(dest)
    if sha256 and got != sha256:
        raise SystemExit(f"{dest.name}: PyPI said {sha256}, the download is {got}")
    return got, dest.stat().st_size


def licence(meta: dict) -> str:
    expr = meta.get("license_expression")
    if expr:
        return expr
    for c in meta.get("classifier") or []:
        if c.startswith("License :: OSI Approved :: "):
            return c.rsplit(" :: ", 1)[-1]
    text = (meta.get("license") or "").strip().splitlines()
    return text[0][:60] if text else "see the wheel"


def resolve(python: str, reqs: list[str], *, no_deps: bool = False) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        cmd = [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--quiet",
               "--only-binary=:all:", "--platform", PLATFORM, "--implementation", "cp",
               "--python-version", ".".join(python.split(".")[:2]),
               "--target", str(Path(tmp) / "t"), "--report", str(report)]
        if no_deps:
            cmd.append("--no-deps")
        subprocess.run(cmd + reqs, check=True)
        doc = json.loads(report.read_text(encoding="utf-8"))
    out = []
    for item in doc["install"]:
        info = item["download_info"]
        out.append({
            "name": item["metadata"]["name"].lower().replace("_", "-"),
            "version": item["metadata"]["version"],
            "url": info["url"],
            "sha256": info["archive_info"]["hashes"]["sha256"],
            "license": licence(item["metadata"]),
        })
    return out


def build(python: str, pip_version: str, cache: Path) -> dict:
    cache.mkdir(parents=True, exist_ok=True)
    sat = ROOT / "satellite"
    wheels = resolve(python, ["-r", str(sat / "requirements.txt"), "-r", str(sat / "requirements-windows.txt")])
    wheels += resolve(python, ["openwakeword==0.6.0"], no_deps=True)
    pip = resolve(python, [f"pip=={pip_version}"], no_deps=True)[0]
    entries = []
    for w in sorted(wheels, key=lambda x: x["name"]):
        name = w["url"].rsplit("/", 1)[-1]
        sha, size = download(w["url"], cache / name, w["sha256"])
        note = BUNDLED_NOTES.get(w["name"])
        entries.append({"name": w["name"], "version": w["version"], "file": name, "url": w["url"],
                        "sha256": sha, "size": size,
                        "license": w["license"] + (f"; {note}" if note else "")})
    pip_name = pip["url"].rsplit("/", 1)[-1]
    pip_sha, pip_size = download(pip["url"], cache / pip_name, pip["sha256"])
    py_url = PYTHON_URL.format(v=python)
    py_name = py_url.rsplit("/", 1)[-1]
    py_sha, py_size = download(py_url, cache / py_name)
    return {
        "about": [
            "The exact Python and wheels a Windows satellite installs (satellite/scripts/install-windows.ps1,",
            "satellite/win_install.py), and that the core fetches and caches for it (domovoi/satellite_windows.py).",
            "Every file is checked against its sha256 and size before it is used. Written by",
            "scripts/dev/windows_runtime_lock.py; never edited by hand.",
        ],
        "lock_version": 1,
        "platform": PLATFORM,
        "python": {"version": python, "file": py_name, "url": py_url, "sha256": py_sha, "size": py_size,
                   "license": PYTHON_LICENSE},
        "pip": {"name": "pip", "version": pip["version"], "file": pip_name, "url": pip["url"],
                "sha256": pip_sha, "size": pip_size, "license": pip["license"]},
        "wheels": entries,
        "remove_after_install": REMOVE_AFTER_INSTALL,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True, help="CPython version, e.g. 3.14.8")
    ap.add_argument("--pip", required=True, help="pip version for the installer, e.g. 26.2.1")
    ap.add_argument("--cache", type=Path, required=True, help="where the files are downloaded to")
    ap.add_argument("--check", action="store_true", help="compare with the lock in the tree")
    args = ap.parse_args(argv)
    doc = build(args.python, args.pip, args.cache)
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        same = LOCK.is_file() and LOCK.read_text(encoding="utf-8") == text
        print("the lock is current" if same else "the lock would change")
        return 0 if same else 1
    LOCK.write_bytes(text.encode("utf-8"))
    print(f"wrote {LOCK} ({len(doc['wheels'])} wheels, {sum(w['size'] for w in doc['wheels']) + doc['python']['size']} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
