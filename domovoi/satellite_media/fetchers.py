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
* **openWakeWord base models** — the release assets
  ``openwakeword.utils.download_models()`` fetches, downloaded here from
  pinned URLs and checked against pinned SHA-256 digests and sizes
  (:data:`OWW_MODEL_FILES`). Nothing that fails the check reaches the
  bucket, and :func:`verify_oww_models` drops anything in it that the pins
  don't vouch for before a payload is assembled.

Prebuilt mic-board .dtbo overlays are cache-passthrough only: stage 1
installs whatever ``cache/dtbo/`` holds; when empty, stage 2 falls back to
the on-device compile the manual checklist documents.

Under ``INTERNET_ACCESS=never`` every fetcher returns ``(False, <the
turned-off reason>; using what is already cached)`` without running a
subprocess or a download, so preparing a card from the cache keeps
working and nothing leaves the house.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from domovoi import egress, net_safety
from domovoi.satellite_media import cache

log = logging.getLogger(__name__)


def _turned_off(what: str) -> tuple[bool, str] | None:
    """The fetchers' refusal under ``INTERNET_ACCESS=never``, or None."""
    if not egress.internet_turned_off():
        return None
    log.info("satellite media: %s not refreshed — internet access is turned off", what)
    return False, f"{egress.TURNED_OFF_REASON}; using what is already cached"

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
    refused = _turned_off("wheels")
    if refused:
        return refused
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
    refused = _turned_off("debs")
    if refused:
        return refused
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
# The one upstream commit this pipeline ships, and the digests of the two
# files that matter at that commit: the binary that becomes a sudoers
# target on every satellite, and the map it dlopens beside itself. The
# fetch checks out exactly this commit and refuses the cache when either
# file differs. Moving the pin is a deliberate act: update both values from
# a clone you have inspected.
XVF_HOST_COMMIT = "a652fe79da3a292b25decc0e1e7f267d29bb0284"   # 2026-08-14
XVF_HOST_SHA256 = {
    "xvf_host": "63f89c6672c0d89bc82d8182cb36013ac3619288780f315a2e0373fb3ed771f2",
    "libcommand_map.so": "c1b424313e48cfe97c5cfce0530ac05fe47f818cc0fba15a9954198ef105282c",
}


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_xvf_host_tree(src: Path) -> str | None:
    """Why ``src`` is not the pinned tool, or None when it is."""
    for name, want in XVF_HOST_SHA256.items():
        p = src / name
        if not p.is_file():
            return f"{name} is missing from {XVF_HOST_SUBDIR}"
        got = _sha256_file(p)
        if got != want:
            return (
                f"{name} does not match the pinned digest "
                f"(got {got[:12]}…, pinned {want[:12]}…)"
            )
    return None


def fetch_xvf_host(run=subprocess.run) -> tuple[bool, str]:
    """The xvf_host LED/control tool into the cache. (ok, message).

    NOT a single file: xvf_host loads ``libcommand_map.so`` from its OWN
    directory, so the whole folder has to travel together — a lone binary
    fails at runtime with "cannot open shared object file".

    Fetched at XVF_HOST_COMMIT, exactly: an empty clone, one fetch of that
    commit, a checkout of it, and a digest check on the files that matter
    before anything reaches the cache. Upstream's branch tip is never
    what ships.
    """
    refused = _turned_off("xvf_host")
    if refused:
        return refused
    dest = cache.bucket("xvf_host")
    git = shutil.which("git")
    if git is None:
        return False, (
            "git is not available, so the XVF3800 LED tool can't be cached — "
            "satellites built from this payload will have a dark ring"
        )
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "src"
        clone.mkdir()
        steps = (
            ([git, "init", "-q", str(clone)], 60),
            ([git, "-C", str(clone), "remote", "add", "origin", XVF_HOST_REPO], 60),
            ([git, "-C", str(clone), "fetch", "-q", "--depth", "1", "origin",
              XVF_HOST_COMMIT], 600),
            ([git, "-C", str(clone), "checkout", "-q", XVF_HOST_COMMIT], 60),
        )
        for cmd, limit in steps:
            try:
                r = run(cmd, capture_output=True, text=True, timeout=limit)
            except (OSError, subprocess.TimeoutExpired) as e:
                return False, f"xvf_host fetch unavailable: {e}"
            if r.returncode != 0:
                return False, (
                    f"xvf_host fetch of {XVF_HOST_COMMIT[:12]} failed at "
                    f"`git {cmd[1] if cmd[1] != '-C' else cmd[3]}`: "
                    f"{(r.stderr or '')[-300:]}"
                )
        try:
            head = run(
                [git, "-C", str(clone), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"xvf_host fetch unavailable: {e}"
        if head.returncode != 0 or (head.stdout or "").strip() != XVF_HOST_COMMIT:
            return False, (
                f"xvf_host checkout is not {XVF_HOST_COMMIT[:12]} "
                f"(got {(head.stdout or '').strip()[:12] or '?'})"
            )
        src = clone / XVF_HOST_SUBDIR
        if not src.is_dir():
            return False, (
                f"{XVF_HOST_SUBDIR} is not at the pinned commit — "
                "the layout changed; see PROVISIONING.md §E"
            )
        problem = verify_xvf_host_tree(src)
        if problem:
            return False, f"xvf_host refused: {problem}"
        # Replace rather than merge: a stale companion .so beside a newer
        # binary is exactly the failure this folder-not-file rule exists for.
        if dest.is_dir():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
    cache.stamp("xvf_host")
    return True, (
        f"xvf_host {XVF_HOST_COMMIT[:12]} cached in {dest} "
        f"({len(list(dest.iterdir()))} files, digests verified)"
    )


# The openWakeWord base models every voice satellite runs: exactly the files
# openwakeword.utils.download_models() fetches (openwakeword 0.6.0 reads
# them from its v0.5.1 release), pinned here by SHA-256 and size so a
# replaced release asset can't become every satellite's wake-word detector
# (SAT-11). The digests were taken from copies whose sizes match the
# release's asset list (GitHub API, 2026-10-07); silero_vad.onnx is the
# widely published Silero VAD v4 digest. A new openWakeWord release means
# new rows here, not a looser check.
OWW_RELEASE_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
OWW_MODEL_FILES: dict[str, tuple[str, int]] = {
    "alexa_v0.1.onnx": ("6ff566a01d12670e8d9e3c59da32651db1575d17272a601b7f8a39283dfbae3e", 854246),
    "alexa_v0.1.tflite": ("7333a317a790070a7f3432b81d9439c779481cc4ebd67c73da7174ea3cf48397", 855312),
    "embedding_model.onnx": ("70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f", 1326578),
    "embedding_model.tflite": ("c0aea21eb84a4ce90a08c870da41b7a7173b45269e6a3207c71d67c40f3a59d8", 1330312),
    "hey_jarvis_v0.1.onnx": ("94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb", 1271370),
    "hey_jarvis_v0.1.tflite": ("14bff778604985e1b5c19f0f7bbe477a69cf281d8db34b232b3b972411f710e2", 1278912),
    "hey_mycroft_v0.1.onnx": ("c2a311e8fa1338de89c31b3b46dc4dffd4af2f9a8d6ddead48893c2d301b1f18", 857691),
    "hey_mycroft_v0.1.tflite": ("bf9e43136afd3ca323698820a6e32a47f885ef4c30a3b8b577ec71688a9d64d8", 860300),
    "hey_rhasspy_v0.1.onnx": ("5a9b3ed3be2910e35780e097905aa9f35a9c10038df47914cf2b3ec4d670f6ea", 204081),
    "hey_rhasspy_v0.1.tflite": ("01d2526b45068f565aa3849d6ec2b7abae099154fc1b496f9ef20de9ef241fe9", 416140),
    "melspectrogram.onnx": ("ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f", 1087958),
    "melspectrogram.tflite": ("96fa0adccb6e8cf95cb14465409a1a2898ee4a96a85bb9ed3c7eb0e68bf163e8", 1092516),
    "silero_vad.onnx": ("a35ebf52fd3ce5f1469b2a36158dba761bc47b973ea3382b3186ca15b1f5af28", 1807522),
    "timer_v0.1.onnx": ("371e44535470a29248b3b8f1bbbbaf2525c86417fd8f75c67fcf02ae0b9626df", 1742475),
    "timer_v0.1.tflite": ("21d5b0267e97df64870b7aca312e2043ebed248d365698926a115a3694ff9626", 1743316),
    "weather_v0.1.onnx": ("8441da8e746899e8d969528d5bad5651cdd563079c05962788f77753041f60e7", 1149158),
    "weather_v0.1.tflite": ("4178991c7aeb76670f5a56559eb4129a6f3ae6207886db8bd8094fea7d362c3f", 1150224),
}
_OWW_CHUNK = 1 << 16


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_OWW_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _oww_http_client():
    """The client the model downloads use: egress-hooked, so each request
    (redirects included) is refused under ``never``. Tests replace it."""
    import httpx

    return egress.sync_client(timeout=httpx.Timeout(30.0, read=120.0))


def _fetch_pinned(url: str, dest: Path, *, sha256: str, size: int) -> None:
    """Download ``url`` to ``dest`` through net_safety (every hop to a
    public address), reading at most ``size`` bytes, and move it into
    place only when it is exactly ``size`` bytes with digest ``sha256``.
    Raises ValueError otherwise; nothing partial is left behind."""
    part = dest.with_name(f".{dest.name}.part")
    h = hashlib.sha256()
    written = 0
    try:
        with _oww_http_client() as client:
            response = net_safety.open_stream_sync(client, url)
            try:
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}")
                with open(part, "wb") as f:
                    for chunk in response.iter_bytes(_OWW_CHUNK):
                        written += len(chunk)
                        if written > size:
                            raise ValueError(f"larger than the pinned {size} bytes")
                        h.update(chunk)
                        f.write(chunk)
            finally:
                response.close()
        if written != size:
            raise ValueError(f"{written} bytes, pinned {size}")
        if h.hexdigest() != sha256:
            raise ValueError(f"checksum mismatch: pinned {sha256}, got {h.hexdigest()}")
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


def verify_oww_models(dest: Path | None = None) -> tuple[int, list[str]]:
    """Hold the ``oww_models`` bucket to the pins: every file that isn't a
    pinned model with its pinned size and digest is removed (an older
    refresh stored whatever the release served). (verified, removed)."""
    dest = dest or cache.bucket("oww_models")
    verified, removed = 0, []
    for p in sorted(dest.iterdir()) if dest.is_dir() else []:
        pin = OWW_MODEL_FILES.get(p.name)
        ok = (
            pin is not None and p.is_file() and not p.is_symlink()
            and p.stat().st_size == pin[1] and _sha256_file(p) == pin[0]
        )
        if ok:
            verified += 1
            continue
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(p.name)
        except OSError as e:
            log.warning("satellite media: could not remove unverified %s: %s", p, e)
    if removed:
        log.warning(
            "satellite media: removed %d unverified file(s) from the wake-word model cache: %s",
            len(removed), ", ".join(removed),
        )
    return verified, removed


def fetch_oww_models() -> tuple[bool, str]:
    """openWakeWord base models into the cache, each checked against its
    pinned digest; what is already there and verified is kept. (ok,
    message)."""
    refused = _turned_off("oww_models")
    if refused:
        return refused
    dest = cache.bucket("oww_models")
    verify_oww_models(dest)
    fetched = 0
    for name, (sha256, size) in OWW_MODEL_FILES.items():
        target = dest / name
        if target.is_file():
            continue            # verified just above
        try:
            _fetch_pinned(OWW_RELEASE_URL + name, target, sha256=sha256, size=size)
        except Exception as e:  # noqa: BLE001 — network/hub errors degrade
            return False, f"model download failed: {name}: {e}"
        fetched += 1
    cache.stamp("oww_models")
    return True, (
        f"{len(OWW_MODEL_FILES)} wake-word models verified in {dest} "
        f"({fetched} downloaded)"
    )
