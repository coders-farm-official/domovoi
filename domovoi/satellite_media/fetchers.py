"""Cache-refresh fetchers for satellite media builds.

Three sources, each degrading independently (a build NEVER hard-fails on a
missing cache — it flips to `offline: false` with a loud warning and lets
the device's stage-2 bootstrap pull that piece online):

* **Wheels** — native ``pip download --platform manylinux_*_aarch64``; no
  Docker involved. This is the bulk of the offline payload.
* **Debs** — the small apt set (mpg123, libportaudio2, mtools, dosfstools
  + plugin apt packages) fetched arm64 via an UNPRIVILEGED
  ``debian:<release>-slim`` container. Docker Desktop absent/wedged →
  skipped.
* **openWakeWord base models** — via the openwakeword package when it's
  importable server-side; else skipped (a voice satellite's stage 2
  downloads them online after Wi-Fi).

Prebuilt mic-board .dtbo overlays are cache-passthrough only: stage 1
installs whatever ``cache/dtbo/`` holds; when empty, stage 2 falls back to
the on-device compile the manual checklist documents.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from domovoi.satellite_media import cache

log = logging.getLogger(__name__)

# The satellite's base apt set (PROVISIONING §3) + the adoption-mode tools.
BASE_APT_PACKAGES = (
    "mpg123",
    "libportaudio2",
    "libasound2",
    "alsa-utils",
    "mtools",
    "dosfstools",
)


def satellite_requirements(repo_root: Path) -> list[str]:
    reqs = repo_root / "satellite" / "requirements.txt"
    out: list[str] = []
    for line in reqs.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def fetch_wheels(
    repo_root: Path,
    python_version: str,
    platforms: tuple[str, ...],
    run=subprocess.run,
) -> tuple[bool, str]:
    """Populate the wheel cache for the target arch. (ok, message)."""
    dest = cache.bucket("wheels", python_version)
    base_cmd = [
        sys.executable, "-m", "pip", "download",
        "--only-binary=:all:",
        "--python-version", python_version,
        "--implementation", "cp",
        "-d", str(dest),
    ]
    for p in platforms:
        base_cmd += ["--platform", p]
    reqs = satellite_requirements(repo_root)
    # openwakeword installs --no-deps on the Pi (PROVISIONING §6); pip +
    # setuptools + wheel ride along so stage-1 can bootstrap the venv with
    # --no-index (no ensurepip needed).
    try:
        r = run(base_cmd + reqs, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            return False, f"wheel fetch failed: {(r.stderr or '')[-400:]}"
        r2 = run(
            base_cmd + ["--no-deps", "openwakeword"],
            capture_output=True, text=True, timeout=600,
        )
        r3 = run(
            [
                sys.executable, "-m", "pip", "download",
                "--only-binary=:all:", "-d", str(dest),
                "pip", "setuptools", "wheel",
            ],
            capture_output=True, text=True, timeout=600,
        )
        if r2.returncode != 0 or r3.returncode != 0:
            return False, "wheel fetch partially failed (openwakeword/pip tools)"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"wheel fetch unavailable: {e}"
    cache.stamp("wheels")
    return True, f"wheels cached in {dest}"


def docker_available(run=subprocess.run) -> bool:
    try:
        r = run(["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def fetch_debs(
    os_release: str,
    extra_packages: tuple[str, ...] = (),
    run=subprocess.run,
) -> tuple[bool, str]:
    """arm64 .debs via an unprivileged container. (ok, message)."""
    if not docker_available(run=run):
        return False, "docker unavailable — deb cache skipped (stage 2 uses apt online)"
    dest = cache.bucket("debs", os_release)
    pkgs = sorted({*BASE_APT_PACKAGES, *extra_packages})
    script = (
        "set -e; dpkg --add-architecture arm64; "
        "sed -i 's/^Components:/Components:/' /etc/apt/sources.list.d/*.sources 2>/dev/null || true; "
        "apt-get update -qq; cd /out; "
        + " ; ".join(f"apt-get download {p}:arm64 || apt-get download {p}" for p in pkgs)
    )
    try:
        r = run(
            [
                "docker", "run", "--rm",
                "-v", f"{dest}:/out",
                f"debian:{os_release}-slim",
                "sh", "-c", script,
            ],
            capture_output=True, text=True, timeout=900,
        )
        if r.returncode != 0:
            return False, f"deb fetch failed: {(r.stderr or '')[-400:]}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"deb fetch unavailable: {e}"
    cache.stamp("debs")
    return True, f"debs cached in {dest}"


def fetch_oww_models() -> tuple[bool, str]:
    """openWakeWord base models into the cache, when the package is
    importable server-side. (ok, message)."""
    dest = cache.bucket("oww_models")
    try:
        from openwakeword import utils as oww_utils  # type: ignore

        oww_utils.download_models(target_directory=str(dest))
    except ImportError:
        return False, (
            "openwakeword not installed server-side — models skipped "
            "(a voice satellite's stage 2 downloads them online)"
        )
    except Exception as e:  # noqa: BLE001 — network/hub errors degrade
        return False, f"model download failed: {e}"
    cache.stamp("oww_models")
    return True, f"models cached in {dest}"
