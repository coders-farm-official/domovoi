"""Every pin the offline kit needs, read from the release commit.

An offline install checks every file against the pins in the commit it
installs (design section 6), so the kit builder reads the same pins from
the same commit: ``git show <commit>:<path>`` in this server's checkout
(:func:`git_reader`), never a copy. Tests hand :func:`load` a dict.

Where each pin lives (the files provision.sh, the Windows and the Mac
installers read):

* ``scripts/linux/provision-manifest.conf``: docker-ce and NVIDIA
  container toolkit versions, Whisper's Hugging Face revision and
  model.bin, the tier models by digest.
* ``docs/LINUX_HOST.md``: the Ollama release and its SHA-256.
* ``domovoi/docker-compose.yml``: the compose images by digest.
* ``domovoi/Dockerfile.mpd``: the MPD image's pinned base.
* ``domovoi/clients/tts.py`` / ``speaker_encoder.py``: the Piper voices
  and voice ID's model (imported from this process: they are code).
* ``android/release.json``: the phone APKs.
* ``windows/installer/pins.iss``: the WSL MSI and Ubuntu's WSL image.
* ``mac/installer/pins.conf``: Ollama for macOS, Ubuntu's arm64 cloud
  image, whisper.cpp's model.
* ``requirements-linux-py314.lock`` and ``...-no-deps.lock``: the wheels.

Sizes the pins don't carry (the Ollama tarball, the WSL MSI, Ubuntu's
images) are in :data:`KNOWN_SIZES`, read from the upstream servers when
the pin was set. Every file pin must have a size: it caps the download
and counts in the free-space check, so a pin without one stops
:func:`load` (a pin bump adds its size here in the same commit).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

Reader = Callable[[str], str]

MANIFEST_CONF = "scripts/linux/provision-manifest.conf"
LINUX_HOST_DOC = "docs/LINUX_HOST.md"
COMPOSE = "domovoi/docker-compose.yml"
MPD_DOCKERFILE = "domovoi/Dockerfile.mpd"
ANDROID_RELEASE = "android/release.json"
WINDOWS_PINS = "windows/installer/pins.iss"
MAC_PINS = "mac/installer/pins.conf"
LOCK = "requirements-linux-py314.lock"
NODEPS_LOCK = "requirements-linux-py314-no-deps.lock"
INSTALL_SH = "scripts/linux/install.sh"

#: Images on the stick: every compose image but SearXNG (AGPL, and off
#: under "never"; design section 7).
NEVER_IMAGES = ("searxng/",)

#: Sizes of pinned upstream files whose pins carry no size (Content-Length
#: from the upstream server on 2026-11-22). Keyed by SHA-256.
KNOWN_SIZES = {
    # ollama-linux-amd64.tar.zst v0.34.4
    "c238986e61d40c0cc5f4a9b9e40b9eea104350b77efa34741fc134e105cb9533": 1_427_703_051,
    # ollama-darwin.tgz v0.34.4
    "e9c8fddaab5f48f47f2c4ae3d23d0732f5182417125353faeed2188e34a22799": 160_042_307,
    # ubuntu-26.04.1-wsl-amd64.wsl
    "48d56724b5c8e60f24893e83e73bbb58c60b3ca22fba3da977075420acd54104": 418_495_746,
    # wsl.2.7.14.0.x64.msi
    "db084e536279a59e90a26ec598d8aa8a4dff8309f41d078fd06242953ac1ebcd": 258_990_080,
    # ubuntu-26.04-server-cloudimg-arm64.img release-20261016
    "8eb876c2ccc4b6ff89e4a65d511815923b285e04503ba465809bf587f763da24": 940_373_504,
}

#: The image the package mirror is made in (apt_mirror.py), by its index
#: digest so every platform resolves to the same build: Docker Hub's
#: ``library/ubuntu:26.04`` as the registry listed it on 2026-11-22. A new
#: pin is a new commit; the mirror's cache key includes it.
APT_MIRROR_IMAGE = ("ubuntu:26.04@sha256:"
                    "d66b366dac61b575bee2aef07e9c26e8070096fbe84a3d4d3eb0da069b5d76db")

_SHA = re.compile(r"^[0-9a-f]{64}\Z")


class PinError(ValueError):
    """A pin file is missing or doesn't say what the kit needs."""


@dataclass(frozen=True)
class FilePin:
    """One upstream file: where it comes from and what it must be."""

    name: str            # the file name on the stick
    url: str
    sha256: str
    size: int | None     # None: not pinned and not in KNOWN_SIZES (load refuses it)
    pinned_by: str

    @property
    def size_or_zero(self) -> int:
        return self.size or 0


@dataclass(frozen=True)
class ModelPin:
    tag: str             # e.g. qwen3:8b-q4_K_M
    digest: str          # the manifest's SHA-256 (what /api/tags calls digest)
    role: str            # tool, qa, vision
    tier: str            # floor, standard
    pinned_by: str = MANIFEST_CONF


@dataclass(frozen=True)
class ImagePin:
    ref: str             # repo:tag@sha256:<digest>, as compose writes it
    repo: str            # e.g. postgres, flyway/flyway, letta/letta
    tag: str
    digest: str
    licence: str


@dataclass
class Pins:
    commit: str
    manifest: dict[str, str]
    ollama_linux: FilePin
    ollama_darwin: FilePin | None
    models: list[ModelPin]
    whisper: dict[str, Any]
    ggml_stt: FilePin | None
    images: list[ImagePin]
    mpd_base: str | None
    android: dict[str, Any] | None
    windows: list[FilePin]
    mac: list[FilePin]
    lock_text: str
    nodeps_lock_text: str
    apt_packages: list[str]


def parse_conf(text: str) -> dict[str, str]:
    """``key=value`` lines (provision-manifest.conf, pins.conf)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _need(d: dict[str, str], key: str, where: str) -> str:
    v = d.get(key)
    if not v:
        raise PinError(f"{where} has no {key}")
    return v


def _sha(v: str, what: str) -> str:
    if not _SHA.match(v):
        raise PinError(f"{what} is not a SHA-256")
    return v


def parse_iss(text: str) -> dict[str, str]:
    """``#define Name "value"`` (and ``"a" + Name + "b"``) from pins.iss."""
    raw: dict[str, str] = {}
    for m in re.finditer(r'^#define\s+(\w+)\s+(.+)$', text, re.M):
        raw[m.group(1)] = m.group(2).strip()
    out: dict[str, str] = {}

    def value(name: str, depth: int = 0) -> str:
        if name in out:
            return out[name]
        expr = raw.get(name, "")
        parts = []
        for tok in re.findall(r'"[^"]*"|\w+', expr):
            if tok.startswith('"'):
                parts.append(tok[1:-1])
            elif depth < 8 and tok in raw:
                parts.append(value(tok, depth + 1))
        out[name] = "".join(parts)
        return out[name]

    for name in raw:
        value(name)
    return out


def ollama_release(doc: str) -> tuple[str, str]:
    """(version, SHA-256 of ollama-linux-amd64.tar.zst) from LINUX_HOST.md,
    read as provision.sh reads it (exactly one line of each)."""
    v = re.findall(r"^OLLAMA_VERSION=([0-9][0-9.]*)$", doc, re.M)
    s = re.findall(r"^OLLAMA_SHA256=([0-9a-f]{64})$", doc, re.M)
    if len(v) != 1 or len(s) != 1:
        raise PinError(f"{LINUX_HOST_DOC} should pin exactly one Ollama release")
    return v[0], s[0]


def compose_images(text: str) -> list[ImagePin]:
    """Every ``image: repo:tag@sha256:…`` in the compose file, once, but
    those that never go on a stick (:data:`NEVER_IMAGES`)."""
    seen: dict[str, ImagePin] = {}
    for m in re.finditer(r"^\s*image:\s*([^\s#]+)", text, re.M):
        ref = m.group(1).strip("'\"")
        mm = re.fullmatch(r"([a-z0-9][a-z0-9._/-]*):([A-Za-z0-9._-]+)@sha256:([0-9a-f]{64})", ref)
        if not mm:
            raise PinError(f"{COMPOSE}: {ref} is not pinned by digest")
        repo, tag, digest = mm.groups()
        if any(repo.startswith(n) for n in NEVER_IMAGES):
            continue
        licence = "PostgreSQL" if repo == "postgres" else "Apache-2.0"
        seen.setdefault(ref, ImagePin(ref=ref, repo=repo, tag=tag, digest=digest, licence=licence))
    return list(seen.values())


def model_pins(m: dict[str, str]) -> list[ModelPin]:
    out: list[ModelPin] = []
    seen: set[str] = set()
    for tier in ("floor", "standard"):
        for role in ("tool", "qa", "vision"):
            tag = m.get(f"tier_{tier}_{role}_model")
            digest = m.get(f"tier_{tier}_{role}_digest")
            if not tag or not digest:
                continue
            _sha(digest, f"tier_{tier}_{role}_digest")
            if tag in seen:
                continue
            seen.add(tag)
            out.append(ModelPin(tag=tag, digest=digest, role=role, tier=tier))
    if not out:
        raise PinError(f"{MANIFEST_CONF} pins no models")
    return out


def lock_python(text: str) -> str:
    """The Python the lock was compiled for ("3.14")."""
    m = re.search(r"autogenerated by pip-compile with Python ([0-9]+\.[0-9]+)", text)
    return m.group(1) if m else "3.14"


def _apt_packages(provision: str) -> list[str]:
    m = re.search(r"^APT_PACKAGES=\(([^)]*)\)", provision, re.M | re.S)
    if not m:
        raise PinError("provision.sh has no APT_PACKAGES list")
    return m.group(1).split()


def load(read: Reader, commit: str) -> Pins:
    """Every pin, from the files ``read`` returns (paths as above).
    Raises PinError naming the first missing one."""
    m = parse_conf(read(MANIFEST_CONF))
    version, sha = ollama_release(read(LINUX_HOST_DOC))
    ollama_linux = FilePin(
        name="ollama-linux-amd64.tar.zst",
        url=f"https://github.com/ollama/ollama/releases/download/v{version}/ollama-linux-amd64.tar.zst",
        sha256=sha, size=KNOWN_SIZES.get(sha), pinned_by=f"{LINUX_HOST_DOC}:OLLAMA_SHA256")

    whisper = {k: _need(m, k, MANIFEST_CONF) for k in (
        "whisper_model", "whisper_repo", "whisper_revision", "whisper_model_bin_sha256",
        "whisper_model_bin_bytes")}
    whisper["whisper_model_bin_bytes"] = int(whisper["whisper_model_bin_bytes"])

    try:
        rel = json.loads(read(ANDROID_RELEASE)).get("release")
    except (ValueError, PinError, OSError):
        rel = None

    win = parse_iss(read(WINDOWS_PINS))
    windows = []
    for key in ("WslMsi", "Ubuntu"):
        name, url, s = win.get(f"{key}Name"), win.get(f"{key}Url"), win.get(f"{key}Sha256")
        if not (name and url and s):
            raise PinError(f"{WINDOWS_PINS} doesn't pin {key}")
        windows.append(FilePin(name=name, url=url, sha256=_sha(s, f"{key}Sha256"),
                               size=KNOWN_SIZES.get(s), pinned_by=f"{WINDOWS_PINS}:{key}Sha256"))

    mac: list[FilePin] = []
    ollama_darwin = ggml = None
    try:
        mp = parse_conf(read(MAC_PINS))
    except (PinError, OSError):
        mp = {}
    if mp.get("OLLAMA_URL") and mp.get("OLLAMA_SHA256"):
        s = _sha(mp["OLLAMA_SHA256"], "OLLAMA_SHA256")
        ollama_darwin = FilePin(name=mp["OLLAMA_URL"].rsplit("/", 1)[-1], url=mp["OLLAMA_URL"],
                                sha256=s, size=KNOWN_SIZES.get(s), pinned_by=f"{MAC_PINS}:OLLAMA_SHA256")
        mac.append(ollama_darwin)
    if mp.get("UBUNTU_IMAGE_URL") and mp.get("UBUNTU_IMAGE_SHA256"):
        s = _sha(mp["UBUNTU_IMAGE_SHA256"], "UBUNTU_IMAGE_SHA256")
        mac.append(FilePin(name=mp["UBUNTU_IMAGE_URL"].rsplit("/", 1)[-1], url=mp["UBUNTU_IMAGE_URL"],
                           sha256=s, size=KNOWN_SIZES.get(s), pinned_by=f"{MAC_PINS}:UBUNTU_IMAGE_SHA256"))
    if mp.get("STT_MODEL_URL") and mp.get("STT_MODEL_SHA256"):
        s = _sha(mp["STT_MODEL_SHA256"], "STT_MODEL_SHA256")
        ggml = FilePin(name=mp["STT_MODEL_URL"].rsplit("/", 1)[-1], url=mp["STT_MODEL_URL"], sha256=s,
                       size=int(mp["STT_MODEL_BYTES"]) if mp.get("STT_MODEL_BYTES", "").isdigit() else None,
                       pinned_by=f"{MAC_PINS}:STT_MODEL_SHA256")
        mac.append(ggml)

    for fp in [ollama_linux, *windows, *mac]:
        if not fp.size or fp.size < 0:
            raise PinError(f"{fp.pinned_by} pins {fp.name} with no size; add it to KNOWN_SIZES "
                           "(domovoi/kit_builder/pins.py)")

    mpd = re.search(r"^FROM\s+(\S+@sha256:[0-9a-f]{64})", read(MPD_DOCKERFILE), re.M)
    try:
        nodeps = read(NODEPS_LOCK)
    except (PinError, OSError):
        nodeps = ""
    return Pins(
        commit=commit, manifest=m, ollama_linux=ollama_linux, ollama_darwin=ollama_darwin,
        models=model_pins(m), whisper=whisper, ggml_stt=ggml, images=compose_images(read(COMPOSE)),
        mpd_base=mpd.group(1) if mpd else None, android=rel, windows=windows, mac=mac,
        lock_text=read(LOCK), nodeps_lock_text=nodeps,
        apt_packages=_apt_packages(read("scripts/linux/provision.sh")),
    )


def git_reader(repo_dir: str, commit: str) -> Reader:
    """Read files as they are in ``commit`` of the checkout at ``repo_dir``."""
    from domovoi import git_run

    def read(path: str) -> str:
        r = git_run.run(["git", "show", f"{commit}:{path}"], cwd=repo_dir, timeout=20)
        if r.returncode != 0:
            raise PinError(f"{path} is not in commit {commit[:12]}")
        return r.stdout

    return read
