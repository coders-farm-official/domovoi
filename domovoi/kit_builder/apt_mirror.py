"""Partial apt mirrors for the kit (design section 4, ``apt/``; risk 1).

An offline Ubuntu 26.04 install needs every package provision.sh installs
(``APT_PACKAGES``, docker-ce at its pins, nftables, the NVIDIA container
toolkit) and everything they pull in, as apt would resolve it on a fresh
system. The builder lets apt itself work that out, in a clean
``ubuntu:26.04`` container pinned by digest (``pins.APT_MIRROR_IMAGE``; an
unprivileged ``docker run``: no ``--privileged``, no host mounts but the
output folder), the trick
``domovoi/satellite_media/fetchers.py`` uses for the rooms' packages:

1. The container records its own package status first (``status.clean``),
   then installs what it needs to add Docker's and NVIDIA's repositories
   (keys checked by fingerprint, as provision.sh checks them).
2. ``apt-get install --download-only`` against ``status.clean`` downloads
   the whole closure, recommends included (provision.sh installs with
   them), for a system as bare as the container. A real Ubuntu has more
   installed already, so the set errs on the side of more packages.
3. It copies apt's lists (the signed ``InRelease`` and the ``Packages``
   indexes) and the ``.deb`` files to the output folder.

Then :func:`assemble` (here, in Python) lays them out as real
repositories, one per archive, that apt checks with its own keys:

    apt/<arch>/ubuntu/dists/<suite>/InRelease
    apt/<arch>/ubuntu/dists/<suite>/<component>/binary-<arch>/Packages
    apt/<arch>/ubuntu/pool/...                 (each .deb at its Filename)
    apt/<arch>/docker/dists/<suite>/...        (Docker's repository)
    apt/<arch>/nvidia/InRelease, Packages, ... (NVIDIA's flat repository)

``<arch>`` is ``amd64`` (and ``arm64`` for a Mac's VM, from
ports.ubuntu.com, in an arm64 container). Every ``.deb`` is matched to its
index entry by SHA-256 before it is kept; a ``.deb`` no index names stops
the build. Only regular files are taken from the container's output: a
symlink, a device or a folder where a file should be stops the build.
The ``Packages`` files are kept uncompressed: Ubuntu's,
Docker's and NVIDIA's ``InRelease`` list the uncompressed index's hash,
so apt verifies them as they are.

A mirror is cached in ``~/.domovoi/offline_kit/cache/apt/<arch>/`` with
``mirror.json`` (the key of the package set and pins it was made for, and
when). The next kit reuses it while the key is the same and it is younger
than :data:`MAX_AGE_SEC`; an older one is reused only when the server has
no internet (a snapshot is always internally consistent).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import host
from .pins import APT_MIRROR_IMAGE, Pins

IMAGE = APT_MIRROR_IMAGE
#: Every container the kit builder runs is named with this prefix, so a
#: cancel, a timeout or the next start can find and remove it.
CONTAINER_PREFIX = "domovoi-kit-"
MAX_AGE_SEC = 14 * 24 * 3600
TIMEOUT_SEC = 3600
#: Installed by provision.sh beside APT_PACKAGES (do_vm_guard), and what a
#: server needs to make kits itself (design section 3).
EXTRA_PACKAGES = ("nftables", "dosfstools", "mtools")
MIRROR_JSON = "mirror.json"

Run = Callable[..., subprocess.CompletedProcess]


class AptError(RuntimeError):
    pass


def wanted(p: Pins, arch: str) -> tuple[list[str], list[str]]:
    """(with recommends, without): the package specs to download."""
    m = p.manifest
    pkgs = [*p.apt_packages, *EXTRA_PACKAGES]
    docker = [f"docker-ce={m['docker_ce']}", f"docker-ce-cli={m['docker_ce_cli']}",
              f"containerd.io={m['containerd_io']}",
              f"docker-buildx-plugin={m['docker_buildx_plugin']}",
              f"docker-compose-plugin={m['docker_compose_plugin']}"]
    nvidia = []
    if arch == "amd64":
        nvidia = [f"nvidia-container-toolkit={m['nvidia_container_toolkit']}",
                  f"nvidia-container-toolkit-base={m['nvidia_container_toolkit_base']}",
                  f"libnvidia-container-tools={m['libnvidia_container_tools']}",
                  f"libnvidia-container1={m['libnvidia_container1']}"]
    return pkgs + docker, nvidia


def key(p: Pins, arch: str) -> str:
    with_rec, without = wanted(p, arch)
    m = p.manifest
    doc = {"arch": arch, "image": IMAGE, "with": sorted(with_rec), "without": sorted(without),
           "docker": [m.get("docker_key_fpr"), m.get("docker_repo_url"), m.get("docker_suite")],
           "nvidia": [m.get("nvidia_key_fpr"), m.get("nvidia_repo_url")]}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


def _q(s: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9:/._=+~@%-]+", s):
        raise AptError(f"refusing an unsafe value in the apt script: {s!r}")
    return s


def _owner() -> str | None:
    if hasattr(os, "getuid"):
        return f"{os.getuid()}:{os.getgid()}"
    return None


def script(p: Pins, arch: str, owner: str | None = None) -> str:
    """The shell the container runs. Every value is a pin from the release
    commit, checked to hold no shell syntax."""
    m = p.manifest
    with_rec, without = wanted(p, arch)
    lines = [
        "set -eu",
        "export DEBIAN_FRONTEND=noninteractive",
        "cp /var/lib/dpkg/status /tmp/status.clean",
        # The image's own apt settings keep indexes compressed and delete
        # downloaded packages; the mirror needs both as apt fetched them.
        "rm -f /etc/apt/apt.conf.d/docker-gzip-indexes /etc/apt/apt.conf.d/docker-clean",
        "rm -rf /var/lib/apt/lists/*",
        "apt-get update -qq",
        "apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg >/dev/null",
        "install -d -m 0755 /etc/apt/keyrings",
        # Docker's key, by fingerprint (provision.sh's check).
        f"curl -fsSL {_q(m['docker_key_url'])} -o /tmp/docker.asc",
        "n=$(gpg --show-keys --with-colons /tmp/docker.asc | grep -c '^pub')",
        "fpr=$(gpg --show-keys --with-colons /tmp/docker.asc | awk -F: '/^fpr/{print $10; exit}')",
        f"[ \"$n\" = 1 ] && [ \"$fpr\" = {_q(m['docker_key_fpr'])} ] || {{ echo 'DOMOVOI_KEY: docker' >&2; exit 3; }}",
        "gpg --dearmor -o /etc/apt/keyrings/docker.gpg </tmp/docker.asc",
        f"echo 'deb [arch={arch} signed-by=/etc/apt/keyrings/docker.gpg] {_q(m['docker_repo_url'])} "
        f"{_q(m['docker_suite'])} stable' >/etc/apt/sources.list.d/docker.list",
    ]
    if without:
        lines += [
            f"curl -fsSL {_q(m['nvidia_key_url'])} -o /tmp/nvidia.asc",
            "n=$(gpg --show-keys --with-colons /tmp/nvidia.asc | grep -c '^pub')",
            "fpr=$(gpg --show-keys --with-colons /tmp/nvidia.asc | awk -F: '/^fpr/{print $10; exit}')",
            f"[ \"$n\" = 1 ] && [ \"$fpr\" = {_q(m['nvidia_key_fpr'])} ] || {{ echo 'DOMOVOI_KEY: nvidia' >&2; exit 3; }}",
            "gpg --dearmor -o /etc/apt/keyrings/nvidia.gpg </tmp/nvidia.asc",
            f"echo 'deb [signed-by=/etc/apt/keyrings/nvidia.gpg] {_q(m['nvidia_repo_url'])} /' "
            ">/etc/apt/sources.list.d/nvidia.list",
        ]
    dl = ("apt-get install -y -q --download-only -o Dir::State::status=/tmp/status.clean "
          "-o Dir::Cache::archives=/tmp/debs")
    lines += [
        "apt-get update -qq",
        "mkdir -p /tmp/debs/partial",
        f"{dl} {' '.join(_q(x) for x in with_rec)}",
    ]
    if without:
        lines.append(f"{dl} --no-install-recommends {' '.join(_q(x) for x in without)}")
    lines += [
        "mkdir -p /out/lists /out/debs",
        "cp /var/lib/apt/lists/*InRelease /var/lib/apt/lists/*_Packages /out/lists/",
        "cp /tmp/debs/*.deb /out/debs/",
        "chmod -R a+rX /out",
    ]
    if owner:
        # The files are the core's to move and delete, not root's.
        lines.append(f"chown -R {_q(owner)} /out")
    return "\n".join(lines) + "\n"


def _nice() -> list[str]:
    out = []
    if shutil.which("nice"):
        out += ["nice", "-n", "19"]
    if shutil.which("ionice"):
        out += ["ionice", "-c", "3"]
    return out


def container_name(arch: str) -> str:
    return f"{CONTAINER_PREFIX}apt-{arch}-{secrets.token_hex(4)}"


def docker_command(p: Pins, arch: str, raw: Path, name: str | None = None) -> list[str]:
    return [*_nice(), "docker", "run", "--rm", "--name", name or container_name(arch),
            "--platform", f"linux/{arch}",
            "--security-opt", "no-new-privileges", "--cpu-shares", "128",
            "-v", f"{raw}:/out", IMAGE, "sh", "-c", script(p, arch, _owner())]


# ─── laying the download out as repositories ─────────────────────────────

@dataclass(frozen=True)
class Index:
    repo: str        # ubuntu, docker, nvidia
    rel: str         # the path inside the repo (dists/resolute/InRelease)


_REPO_HOSTS = (
    ("archive.ubuntu.com_ubuntu_", "ubuntu"),
    ("security.ubuntu.com_ubuntu_", "ubuntu"),
    ("ports.ubuntu.com_ubuntu-ports_", "ubuntu"),
    ("download.docker.com_linux_ubuntu_", "docker"),
    ("nvidia.github.io_libnvidia-container_stable_deb_", "nvidia"),
)


def classify(list_name: str) -> Index | None:
    """Where apt's list file ``list_name`` goes in the mirror, or None for
    one the kit doesn't keep."""
    for prefix, repo in _REPO_HOSTS:
        # A mirror host (cc.archive.ubuntu.com) is the same archive.
        i = list_name.find(prefix)
        if i < 0 or (i and not list_name[:i].endswith(".")):
            continue
        rest = list_name[i + len(prefix):]
        if repo == "nvidia":
            # Flat repository: <arch>_InRelease, <arch>_._Packages.
            m = re.fullmatch(r"(amd64|arm64)_(?:\._)?(InRelease|Packages)", rest)
            return Index(repo, m.group(2)) if m else None
        m = re.fullmatch(r"dists_([a-z0-9-]+)_InRelease", rest)
        if m:
            return Index(repo, f"dists/{m.group(1)}/InRelease")
        m = re.fullmatch(r"dists_([a-z0-9-]+)_([a-z0-9-]+)_binary-([a-z0-9]+)_Packages", rest)
        if m:
            return Index(repo, f"dists/{m.group(1)}/{m.group(2)}/binary-{m.group(3)}/Packages")
        return None
    return None


def parse_packages(text: str) -> dict[str, str]:
    """{sha256: Filename} from a Packages index."""
    out: dict[str, str] = {}
    for stanza in text.split("\n\n"):
        fn = re.search(r"^Filename: (.+)$", stanza, re.M)
        sha = re.search(r"^SHA256: ([0-9a-f]{64})$", stanza, re.M)
        if fn and sha:
            out[sha.group(1)] = fn.group(1).strip()
    return out


def _regular(path: Path) -> Path:
    """``path`` itself (not what a link points at) is a regular file."""
    try:
        st = os.lstat(path)
    except OSError as e:
        raise AptError(f"{path.name} can't be read ({e.strerror or e})") from None
    if not stat.S_ISREG(st.st_mode):
        raise AptError(f"the package download left {path.name}, which is not a regular file; "
                       "nothing was kept")
    return path


def _regular_dir(path: Path) -> Path:
    try:
        st = os.lstat(path)
    except OSError as e:
        raise AptError(f"the package download left no {path.name} folder ({e.strerror or e})") from None
    if not stat.S_ISDIR(st.st_mode):
        raise AptError(f"the package download's {path.name} is not a folder; nothing was kept")
    return path


def _safe_rel(rel: str) -> str:
    rel = rel.removeprefix("./")
    parts = rel.split("/")
    if not rel or rel.startswith("/") or any(x in ("", ".", "..") for x in parts):
        raise AptError(f"an index names a file outside its repository: {rel!r}")
    return rel


def assemble(raw: Path, out: Path) -> list[str]:
    """Lay ``raw`` (``lists/`` and ``debs/`` from the container) out as
    repositories under ``out``. Returns the mirror's files (relative to
    ``out``). Raises AptError for a .deb no index names."""
    if out.exists():
        shutil.rmtree(out)
    files: list[str] = []
    by_sha: dict[str, tuple[str, str]] = {}
    lists, debs = _regular_dir(raw / "lists"), _regular_dir(raw / "debs")
    for lf in sorted(lists.iterdir()):
        idx = classify(lf.name)
        if idx is None:
            continue
        _regular(lf)
        dest = out / idx.repo / idx.rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(lf, dest)
        files.append(f"{idx.repo}/{idx.rel}")
        if idx.rel.endswith("Packages"):
            for sha, fn in parse_packages(lf.read_text(encoding="utf-8", errors="replace")).items():
                by_sha.setdefault(sha, (idx.repo, fn))
    unknown = []
    for deb in sorted(debs.glob("*.deb")):
        _regular(deb)
        h = hashlib.sha256()
        with open(deb, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        hit = by_sha.get(h.hexdigest())
        if hit is None:
            unknown.append(deb.name)
            continue
        repo, fn = hit
        rel = f"{repo}/{_safe_rel(fn)}"
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(deb, dest)
        files.append(rel)
    if unknown:
        raise AptError("apt downloaded packages no index names: " + ", ".join(unknown[:10]))
    if not any(f.endswith(".deb") for f in files):
        raise AptError("apt downloaded no packages")
    return sorted(set(files))


# ─── the cache ───────────────────────────────────────────────────────────

def mirror_dir(arch: str) -> Path:
    return host.cache_dir("apt", arch)


def cached(p: Pins, arch: str) -> dict | None:
    """The cached mirror's record when it was made for these pins."""
    try:
        doc = json.loads((mirror_dir(arch) / MIRROR_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("key") != key(p, arch) or not isinstance(doc.get("files"), list):
        return None
    root = mirror_dir(arch) / "mirror"
    if not all((root / f).is_file() for f in doc["files"]):
        return None
    return doc


def deb_hashes(root: Path, files: list[str]) -> dict[str, str]:
    """{mirror path: SHA-256} of every .deb in ``files``, from the
    mirror's own ``Packages`` indexes (the hashes apt checks)."""
    out: dict[str, str] = {}
    for f in files:
        if not f.endswith("Packages"):
            continue
        repo = f.split("/", 1)[0]
        try:
            text = (root / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for sha, fn in parse_packages(text).items():
            try:
                out.setdefault(f"{repo}/{_safe_rel(fn)}", sha)
            except AptError:
                continue
    return out


def fresh(doc: dict | None, now: float | None = None) -> bool:
    return bool(doc) and (now or time.time()) - float(doc.get("made", 0)) < MAX_AGE_SEC


def build(p: Pins, arch: str, run: Run = subprocess.run) -> dict:
    """Make the mirror for ``arch`` (minutes; needs Docker and the
    internet). Returns mirror.json."""
    from domovoi import egress

    egress.require_internet("the Ubuntu and Docker packages for the offline kit")
    base = mirror_dir(arch)
    raw = base / "raw"
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)
    try:
        try:
            r = run(docker_command(p, arch, raw), capture_output=True, text=True, timeout=TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            raise AptError("the package download took longer than an hour; nothing was kept") from None
        if r.returncode != 0:
            err = (r.stderr or "")[-600:]
            if "DOMOVOI_KEY" in err:
                raise AptError("a repository's signing key is not the pinned one; nothing was kept")
            if "exec format error" in err or "no matching manifest" in err:
                raise AptError(f"this server's Docker can't run {arch} containers, which the "
                               f"{arch} packages need")
            raise AptError(f"the package download failed: {err.strip()[-400:]}")
        files = assemble(raw, base / "mirror")
    finally:
        shutil.rmtree(raw, ignore_errors=True)
    doc = {"key": key(p, arch), "arch": arch, "made": time.time(), "image": IMAGE, "files": files}
    (base / MIRROR_JSON).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return doc


# ─── the repositories' signing keys ──────────────────────────────────────
# An offline install adds Docker's (and NVIDIA's) repository with the key
# provision.sh would fetch from docker_key_url / nvidia_key_url, so the kit
# carries it: apt/<arch>/docker/gpg and apt/amd64/nvidia/gpg, the file the
# vendor serves, byte for byte. It is fetched through the outbound gate
# and kept only when it holds exactly one primary key with the pinned
# fingerprint (provision.sh's own check, made here without gpg).

class KeyFileError(AptError):
    pass


def _dearmor(raw: bytes) -> bytes:
    import base64

    text = raw.decode("ascii", "replace")
    if "-----BEGIN PGP PUBLIC KEY BLOCK-----" not in text:
        return raw  # already binary
    body = text.split("-----BEGIN PGP PUBLIC KEY BLOCK-----", 1)[1].split("-----END PGP", 1)[0]
    lines = [ln.strip() for ln in body.strip().splitlines()]
    # Armor headers end at the first blank line; the checksum line starts with "=".
    if "" in lines:
        lines = lines[lines.index("") + 1:]
    data = "".join(ln for ln in lines if ln and not ln.startswith("="))
    try:
        return base64.b64decode(data, validate=True)
    except ValueError as e:
        raise KeyFileError(f"the key file isn't a valid OpenPGP armor ({e})") from None


def primary_fingerprints(raw: bytes) -> list[str]:
    """The fingerprints of every primary public key (packet tag 6) in an
    OpenPGP key file, armored or binary. v4 keys: SHA-1 over 0x99, the
    two-byte length and the packet body (RFC 4880 12.2)."""
    data = _dearmor(raw)
    out, i = [], 0
    while i < len(data):
        b = data[i]
        if not b & 0x80:
            raise KeyFileError("the key file isn't OpenPGP data")
        if b & 0x40:  # new format
            tag, i = b & 0x3F, i + 1
            first = data[i]
            if first < 192:
                length, i = first, i + 1
            elif first < 224:
                length, i = ((first - 192) << 8) + data[i + 1] + 192, i + 2
            elif first == 255:
                length, i = int.from_bytes(data[i + 1:i + 5], "big"), i + 5
            else:
                raise KeyFileError("the key file uses partial lengths")
        else:  # old format
            tag, lt, i = (b >> 2) & 0x0F, b & 0x03, i + 1
            if lt == 3:
                raise KeyFileError("the key file uses an indeterminate length")
            n = (1, 2, 4)[lt]
            length, i = int.from_bytes(data[i:i + n], "big"), i + n
        body = data[i:i + length]
        if len(body) != length:
            raise KeyFileError("the key file is cut short")
        if tag == 6:
            if not body or body[0] != 4:
                raise KeyFileError(f"a version {body[0] if body else '?'} key; only version 4 is understood")
            out.append(hashlib.sha1(b"\x99" + length.to_bytes(2, "big") + body).hexdigest().upper())
        i += length
    return out


def key_problem(raw: bytes, fpr: str) -> str | None:
    """Why ``raw`` isn't the pinned key, or None."""
    try:
        fprs = primary_fingerprints(raw)
    except KeyFileError as e:
        return str(e)
    if fprs != [fpr.upper()]:
        return f"it holds {len(fprs)} primary key(s) {', '.join(f[-16:] for f in fprs)}, not only {fpr}"
    return None


def keys(p: Pins, arch: str) -> list[tuple[str, str, str]]:
    """(kit path, url, pinned fingerprint) of the keys a kit for ``arch``
    carries."""
    m = p.manifest
    out = [(f"apt/{arch}/docker/gpg", m["docker_key_url"], m["docker_key_fpr"])]
    if arch == "amd64":
        out.append((f"apt/{arch}/nvidia/gpg", m["nvidia_key_url"], m["nvidia_key_fpr"]))
    return out


def key_file(path: str, url: str, fpr: str) -> bytes:
    """The key for kit path ``path``: the cached copy when it still holds
    the pinned key, else fetched (through the outbound gate) and checked."""
    from . import fetch

    cache = host.cache_dir("apt", "keys") / hashlib.sha256(url.encode()).hexdigest()[:16]
    try:
        raw = cache.read_bytes()
        if key_problem(raw, fpr) is None:
            return raw
    except OSError:
        pass
    raw = fetch.fetch_bytes(url, max_bytes=256 * 1024)
    why = key_problem(raw, fpr)
    if why:
        raise AptError(f"the key at {url} is not the pinned one: {why}")
    cache.write_bytes(raw)
    return raw
