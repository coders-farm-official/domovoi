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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from domovoi.satellite_media import cache

log = logging.getLogger(__name__)

# The satellite's base apt set (PROVISIONING §3) + the adoption-mode tools.
BASE_APT_PACKAGES = (
    "mpg123",
    "libportaudio2",
    # portaudio links against JACK and Pi OS does not ship it. `apt-get
    # download` fetches only what it is told, so this has to be named or
    # libportaudio2 arrives unusable: sounddevice cannot dlopen it and the
    # client dies on `import sounddevice` with OSError: libjack.so.0, having
    # looked perfectly healthy right up to that point.
    "libjack-jackd2-0",
    "libasound2",
    "alsa-utils",
    "mtools",
    "dosfstools",
    # xvf_host links against libusb to talk to the XVF3800 over USB.
    "libusb-1.0-0",
    # The setup portal's wildcard DNS. NetworkManager usually pulls this in
    # on Pi OS, but a shipped unit must not depend on that being true — a
    # satellite that cannot raise its portal because it has no internet is
    # unrecoverable in a customer's hands.
    "dnsmasq-base",
)


# Published as sdist only — there is no wheel for ANY platform, so pip's
# --only-binary=:all: (mandatory for a cross-platform download) can never
# satisfy them. Left in the main list, one of these fails the whole batch and
# takes every other wheel with it.
#
# spidev drives the 2-Mics HAT's APA102 LEDs over SPI. It is irrelevant to a
# USB mic array, and even on a HAT the satellite runs fine without it — the
# LEDs stay dark and a warning lands in the log (PROVISIONING §4b). Skipping
# it is the right trade against having no offline payload at all.
SDIST_ONLY_PACKAGES = ("spidev",)

# Debian's 64-bit time_t transition renamed several libraries. The old names
# survive as VIRTUAL packages with no installation candidate, which
# `apt-get download` cannot fetch — it fails with "no candidate" rather than
# "not found". Alternates are tried in order; the first that resolves wins,
# so listing a name that doesn't exist on a given release costs nothing.
DEB_ALTERNATES = {
    "libasound2": ("libasound2t64", "libasound2"),
    "libportaudio2": ("libportaudio2t64", "libportaudio2"),
    "libjack-jackd2-0": ("libjack-jackd2-0", "libjack0"),
}


def _requirement_name(spec: str) -> str:
    """The distribution name from a requirement line."""
    for sep in ("[", "=", ">", "<", "!", "~", ";", " "):
        spec = spec.split(sep, 1)[0]
    return spec.strip().lower()


def split_unfetchable(reqs: list[str]) -> tuple[list[str], list[str]]:
    """(fetchable, skipped) — skipped are the sdist-only ones."""
    keep, skip = [], []
    for spec in reqs:
        (skip if _requirement_name(spec) in SDIST_ONLY_PACKAGES else keep).append(spec)
    return keep, skip


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
    reqs, skipped = split_unfetchable(satellite_requirements(repo_root))
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
    if skipped:
        return True, (
            f"wheels cached in {dest} (no wheels exist for "
            f"{', '.join(skipped)} — LEDs on a 2-Mics HAT need an on-device "
            "build; harmless for USB mic arrays)"
        )
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
    script = build_deb_script(pkgs)
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
    missing = parse_missing(r.stdout or "")
    cache.stamp("debs")
    if missing:
        # Not fatal: stage 2 apt-installs whatever is absent, online. Worth
        # saying out loud, because it silently un-does "fully offline".
        return True, (
            f"debs cached in {dest} — could not fetch {', '.join(missing)}; "
            "a satellite will apt-get those online during stage 2"
        )
    return True, f"debs cached in {dest}"


MISSING_MARKER = "DOMOVOI_MISSING:"


def build_deb_script(pkgs: list[str]) -> str:
    """Shell for the arm64 download container.

    ``apt-get download`` fetches EXACTLY the packages named — never their
    dependencies. That is a deliberate limit, not an oversight: this cache
    exists to get a master image to a working state, and a master is baked
    with a network, where stage 2's apt step resolves dependencies properly
    using the real package manager on the real device. Units flashed from
    that master inherit a complete system and install nothing.

    So a transitive dependency that Pi OS lacks has to be NAMED here
    (libjack, below, is one — libportaudio2 pulls it and nothing else
    would). The cost of getting that wrong is a card prepared fresh and
    booted with no network, which is the bench case rather than a
    customer's. Resolving the graph instead would mean an emulated arm64
    container and shipping half the base system in the payload, which buys
    nothing the master bake does not already give us.

    Deliberately NOT ``set -e``: one package with no candidate must not abort
    the other twelve. Anything unfetchable is reported on stdout instead.
    """
    lines = [
        "dpkg --add-architecture arm64",
        "apt-get update -qq",
        "cd /out",
        "miss=''",
    ]
    for pkg in pkgs:
        names = DEB_ALTERNATES.get(pkg, (pkg,))
        attempts = " || ".join(
            f"apt-get download {n}:arm64 2>/dev/null || apt-get download {n} 2>/dev/null"
            for n in names
        )
        lines.append(f"if {attempts}; then :; else miss=\"$miss {pkg}\"; fi")
    lines.append(f'[ -z "$miss" ] || echo "{MISSING_MARKER}$miss"')
    return "; ".join(lines)


def parse_missing(stdout: str) -> list[str]:
    """Package names the container could not download."""
    for line in stdout.splitlines():
        if line.startswith(MISSING_MARKER):
            return sorted(line[len(MISSING_MARKER):].split())
    return []


# Seeed's control tool for the XVF3800. The 12-LED ring is the satellite's
# entire visual language — listening, thinking, speaking, and the green dot
# that tracks your voice — and this binary is the ONLY way to drive it.
# Without it a shipped unit sits in the XMOS chip's own default display and
# never reacts to Domovoi at all, which reads as a dead device rather than a
# missing tool. It was a documented manual step (PROVISIONING §E) that no
# prepared card had ever run.
XVF_HOST_REPO = (
    "https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY.git"
)
XVF_HOST_SUBDIR = "host_control/rpi_64bit"


def fetch_xvf_host(run=subprocess.run) -> tuple[bool, str]:
    """The xvf_host LED/control tool into the cache. (ok, message).

    NOT a single file: xvf_host loads ``libcommand_map.so`` from its OWN
    directory, so the whole folder has to travel together — a lone binary
    fails at runtime with "cannot open shared object file".
    """
    dest = cache.bucket("xvf_host")
    git = shutil.which("git")
    if git is None:
        return False, (
            "git is not available, so the XVF3800 LED tool can't be cached — "
            "satellites built from this payload will have a dark ring"
        )
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "src"
        try:
            r = run(
                [git, "clone", "--depth", "1", XVF_HOST_REPO, str(clone)],
                capture_output=True, text=True, timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"xvf_host fetch unavailable: {e}"
        if r.returncode != 0:
            return False, f"xvf_host clone failed: {(r.stderr or '')[-300:]}"
        src = clone / XVF_HOST_SUBDIR
        if not src.is_dir():
            return False, (
                f"{XVF_HOST_SUBDIR} is not in the upstream repo any more — "
                "the layout changed; see PROVISIONING.md §E"
            )
        # Replace rather than merge: a stale companion .so beside a newer
        # binary is exactly the failure this folder-not-file rule exists for.
        if dest.is_dir():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
    cache.stamp("xvf_host")
    return True, f"xvf_host cached in {dest} ({len(list(dest.iterdir()))} files)"


def fetch_oww_models() -> tuple[bool, str]:
    """openWakeWord base models into the cache, when the package is
    importable server-side. (ok, message)."""
    dest = cache.bucket("oww_models")
    try:
        from openwakeword import utils as oww_utils  # type: ignore

        oww_utils.download_models(target_directory=str(dest))
    except ImportError:
        return False, (
            "openwakeword not installed on this server, so the wake-word "
            "models can't be cached and the payload is not fully offline "
            "(stage 2 fetches them over the internet instead). Fix with: "
            "pip install --no-deps openwakeword, then refresh again"
        )
    except Exception as e:  # noqa: BLE001 — network/hub errors degrade
        return False, f"model download failed: {e}"
    cache.stamp("oww_models")
    return True, f"models cached in {dest}"
