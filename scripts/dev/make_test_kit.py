"""Make an offline kit for testing the Linux install from one (release 1.5.0,
docs/OFFLINE_KIT.md), until the server makes real ones.

    python scripts/dev/make_test_kit.py --out DIR [--work DIR] [--commit SHA]
        [--version 1.5.0] [--key FILE] [--ai] [--vision] [--image FILE]

It writes a kit for a Windows or Linux PC (pc-x86_64) with the real writer
(domovoi.offline_kit.write_kit) from the real pinned artifacts of the
checkout at --commit (default HEAD): Ubuntu 26.04's and Docker's packages
(a partial mirror with their own signed indexes), the Python lock's
wheels, the database images and the music player image, Ollama, Whisper,
the voices, voice ID's model, the Domovoi wake word, the phone app and,
with --ai, the floor tier's AI models (--vision: and its vision model).
Each one is checked against the same pin the install checks.

What a test kit can't have is the release's own signature: the "release"
(a stand-in DomovoiSetup.exe and its manifest naming --commit) is signed
with a throwaway key (--key, an OpenSSH ed25519 private key, made when
missing; its public key is printed). An install from it then runs
install.sh in its test harness mode with that key
(DOMOVOI_INSTALL_TEST_HARNESS=1, DOMOVOI_INSTALL_RELEASE_KEY; see
scripts/dev/linux-install-vm.sh --offline-kit). Never for a real house.

Downloads happen here, on the machine that makes the kit, never on the one
that installs from it. They are cached in --work and checked again on
every use. Needs git, ssh-keygen and Docker (the apt mirror, the wheels,
the images and the music player's build run in containers); with
--image FILE it also writes the stick's FAT32 image (in a container:
mkfs.fat and mtools), as a server will.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from domovoi.offline_kit import Component, Source, write_kit  # noqa: E402

# The harnesses' Ubuntu 26.04 (scripts/dev/linux-harness.Dockerfile's BASE).
UBUNTU = "ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7"
UA = {"User-Agent": "domovoi-make-test-kit"}


def log(msg: str) -> None:
    print(f"make-test-kit: {msg}", flush=True)


def run(*cmd: str, cwd: Path | None = None, capture: bool = False, **kw) -> str:
    p = subprocess.run(list(cmd), cwd=cwd, text=True, capture_output=capture, **kw)
    if p.returncode != 0:
        raise SystemExit(f"make-test-kit: {' '.join(cmd[:4])}... failed ({p.returncode})"
                         + (f": {p.stderr.strip()[-800:]}" if capture else ""))
    return p.stdout if capture else ""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch(url: str, dest: Path, *, sha: str | None = None, size: int | None = None,
          headers: dict | None = None) -> Path:
    """URL into DEST (cached), held to SHA/SIZE when given."""
    if dest.is_file() and (sha is None or sha256(dest) == sha) and (size is None or dest.stat().st_size == size):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    log(f"downloading {url}")
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as out:
        shutil.copyfileobj(r, out, 1 << 20)
    if size is not None and part.stat().st_size != size:
        part.unlink()
        raise SystemExit(f"make-test-kit: {url}: {part.stat().st_size} bytes, pinned {size}")
    if sha is not None and sha256(part) != sha:
        part.unlink()
        raise SystemExit(f"make-test-kit: {url} does not match its pin {sha}")
    os.replace(part, dest)
    return dest


def rmtree(path: Path) -> None:
    """shutil.rmtree, also through git's read-only pack files (Windows)."""
    def onerror(func, p, _exc):
        os.chmod(p, 0o700)
        func(p)
    if path.exists():
        shutil.rmtree(path, onerror=onerror)


def git_show(commit: str, path: str) -> bytes:
    return subprocess.run(["git", "show", f"{commit}:{path}"], cwd=REPO, capture_output=True, check=True).stdout


def docker_path(p: Path) -> str:
    return str(p.resolve())


def docker(*args: str, capture: bool = False) -> str:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return run("docker", *args, capture=capture, env=env)


# ─── the pins, read from the commit ──────────────────────────────────────


class Pins:
    def __init__(self, commit: str):
        self.commit = commit
        self.manifest: dict[str, str] = {}
        for line in git_show(commit, "scripts/linux/provision-manifest.conf").decode().splitlines():
            m = re.match(r"^([a-z][a-z0-9_]*)=(\S+)$", line)
            if m:
                self.manifest[m.group(1)] = m.group(2)
        doc = git_show(commit, "docs/LINUX_HOST.md").decode()
        self.ollama_version = re.search(r"^OLLAMA_VERSION=([0-9.]+)$", doc, re.M).group(1)
        self.ollama_sha = re.search(r"^OLLAMA_SHA256=([0-9a-f]{64})$", doc, re.M).group(1)
        self.compose_images = re.findall(r"^\s*image:\s*(\S+@sha256:[0-9a-f]{64})\s*$",
                                         git_show(commit, "domovoi/docker-compose.yml").decode(), re.M)
        self.android = json.loads(git_show(commit, "android/release.json"))
        # The Python modules' own pins, from the commit's source.
        self.py = self._py_pins()

    def _py_pins(self) -> dict:
        with tempfile.TemporaryDirectory() as d:
            for rel in ("domovoi/clients/tts.py", "domovoi/clients/speaker_encoder.py", "domovoi/wake_default.py"):
                (Path(d) / Path(rel).name).write_bytes(git_show(self.commit, rel))
            tts = (Path(d) / "tts.py").read_text(encoding="utf-8")
            se = (Path(d) / "speaker_encoder.py").read_text(encoding="utf-8")
            wd = (Path(d) / "wake_default.py").read_text(encoding="utf-8")
        voices = re.findall(r'ShippedVoice\("([^"]+)",[^)]*?"([0-9a-f]{64})", (\d+),\s*"([0-9a-f]{40})", (\d+)\)', tts, re.S)
        return {
            "piper_repo": re.search(r'^PIPER_REPO_URL = "([^"]+)"', tts, re.M).group(1),
            "piper_rev": re.search(r'^PIPER_PINNED_REVISION = "([0-9a-f]{40})"', tts, re.M).group(1),
            "voices": [(v, s, int(n), b, int(jn)) for v, s, n, b, jn in voices],
            "speaker_file": re.search(r'^MODEL_FILE = "([^"]+)"', se, re.M).group(1),
            "speaker_url": "https://github.com/coders-farm-official/domovoi-models/releases/download/eres2net-vox-v1/",
            "speaker_sha": re.search(r'^MODEL_SHA256 = "([0-9a-f]{64})"', se, re.M).group(1),
            "speaker_size": int(re.search(r"^MODEL_BYTES = ([0-9_]+)", se, re.M).group(1).replace("_", "")),
            "wake_sha": re.search(r'slug="domovoi_v1".*?sha256="([0-9a-f]{64})"', wd, re.S).group(1),
            "wake_size": int(re.search(r'slug="domovoi_v1".*?size=(\d+)', wd, re.S).group(1)),
            "wake_repo": re.search(r'^MODELS_REPO = "([^"]+)"', wd, re.M).group(1),
            "wake_tag": re.search(r'slug="domovoi_v1".*?tag="([^"]+)"', wd, re.S).group(1),
        }


# ─── the parts ───────────────────────────────────────────────────────────


def make_bundle(work: Path, commit: str, tag: str) -> Path:
    """A git bundle whose stable and main are COMMIT and whose TAG names it
    (the full history up to it, as a release kit carries)."""
    out = work / f"domovoi-{commit[:12]}.bundle"
    if out.is_file():
        return out
    bare = work / "bundle.git"
    rmtree(bare)
    run("git", "clone", "-q", "--bare", "--no-local", str(REPO), str(bare))
    for ref in ("refs/heads/stable", "refs/heads/main"):
        run("git", "-C", str(bare), "update-ref", ref, commit)
    run("git", "-C", str(bare), "tag", "-f", tag, commit)
    run("git", "-C", str(bare), "bundle", "create", "-q", str(out), "refs/heads/stable", "refs/heads/main",
        f"refs/tags/{tag}")
    rmtree(bare)
    return out


def make_release(work: Path, key: Path, version: str, commit: str) -> list[Source]:
    """A stand-in DomovoiSetup.exe and its manifest, signed with KEY."""
    d = work / "release"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    exe = f"Not a Windows installer: the stand-in a test kit carries for Domovoi {version} ({commit}).\n".encode()
    (d / "DomovoiSetup.exe").write_bytes(exe)
    manifest = {"commit": commit, "file": "DomovoiSetup.exe", "kind": "domovoi-windows-setup", "schema": 1,
                "sha256": hashlib.sha256(exe).hexdigest(), "size": len(exe), "tag": f"v{version}",
                "version": version}
    (d / "DomovoiSetup.manifest.json").write_bytes((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    run("ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", "domovoi-windows-setup-manifest",
        "DomovoiSetup.manifest.json", cwd=d)
    return [Source(f"release/{n}", d / n) for n in
            ("DomovoiSetup.exe", "DomovoiSetup.manifest.json", "DomovoiSetup.manifest.json.sig")]


APT_SCRIPT = r'''
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
printf 'Acquire::GzipIndexes "false";
Acquire::CompressionTypes::Order:: "xz";
' >/etc/apt/apt.conf.d/zz-kit-plain-lists
rm -f /etc/apt/apt.conf.d/docker-clean
apt-get -qq update && apt-get -qq install -y --no-install-recommends ca-certificates curl gnupg >/dev/null
install -d -m 0755 /etc/apt/keyrings
curl -fsSL "$DOCKER_KEY_URL" -o /out/docker-gpg
gpg --batch --quiet --dearmor </out/docker-gpg >/etc/apt/keyrings/docker.gpg
printf 'Types: deb\nURIs: %s\nSuites: %s\nComponents: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.gpg\n' \
  "$DOCKER_REPO_URL" "$DOCKER_SUITE" >/etc/apt/sources.list.d/docker.sources
apt-get -qq update
# Everything the install needs, as if nothing were installed (an empty
# dpkg status): the closure then holds every dependency at the snapshot's
# version, whatever the box installing from it has already. With
# Recommends, as provision.sh's own apt-get install takes them.
mkdir -p /tmp/debs/partial /tmp/st
: >/tmp/st/status
apt-get -qq -o Dir::State::status=/tmp/st/status -o Dir::Cache::archives=/tmp/debs \
  install -y --download-only $PACKAGES >/dev/null
# And everything a fresh Ubuntu 26.04 has, at the snapshot's version, for
# the upgrades the packages above may pull in.
apt-get -qq -o Dir::Cache::archives=/tmp/debs dist-upgrade -y --download-only >/dev/null || true
cd /var/lib/apt/lists
for f in *_InRelease; do
  host=${f%%_*}
  case $f in
    download.docker.com_*) base=/out/apt/docker ;;
    *) base=/out/apt/ubuntu ;;
  esac
  suite=$(printf '%s' "$f" | sed 's/.*_dists_\(.*\)_InRelease$/\1/')
  case $suite in *-backports) continue ;; esac
  mkdir -p "$base/dists/$suite"
  cp "$f" "$base/dists/$suite/InRelease"
  for p in "${f%_InRelease}"_*_binary-amd64_Packages; do
    [ -f "$p" ] || continue
    comp=$(printf '%s' "$p" | sed "s/^${f%_InRelease}_\(.*\)_binary-amd64_Packages$/\1/")
    mkdir -p "$base/dists/$suite/$comp/binary-amd64"
    cp "$p" "$base/dists/$suite/$comp/binary-amd64/Packages"
  done
done
cp /out/docker-gpg /out/apt/docker/gpg && rm -f /out/docker-gpg
# Each .deb where its index says it lives.
n=0
for deb in /tmp/debs/*.deb; do
  pkg=$(dpkg-deb -f "$deb" Package) ver=$(dpkg-deb -f "$deb" Version)
  sum=$(sha256sum "$deb" | cut -d' ' -f1)
  fn=$(apt-cache show "$pkg=$ver" 2>/dev/null | awk -v s="$sum" '/^Filename:/ {f=$2} /^SHA256:/ && $2 == s {print f; exit}')
  [ -n "$fn" ] || { echo "no index names $deb" >&2; exit 1; }
  case $fn in dists/*) base=/out/apt/docker ;; *) base=/out/apt/ubuntu ;; esac
  mkdir -p "$base/$(dirname "$fn")"
  cp "$deb" "$base/$fn"
  n=$((n + 1))
done
echo "apt mirror: $n packages"
'''


def make_apt(work: Path, pins: Pins, commit: str) -> Path:
    out = work / "apt-out"
    if (out / "apt" / "ubuntu").is_dir():
        return out / "apt"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    prov = git_show(commit, "scripts/linux/provision.sh").decode()
    m = re.search(r"^APT_PACKAGES=\(([^)]*)\)", prov, re.M | re.S)
    pkgs = m.group(1).split()
    m = pins.manifest
    pkgs += ["openssh-client", f"docker-ce={m['docker_ce']}", f"docker-ce-cli={m['docker_ce_cli']}",
             f"containerd.io={m['containerd_io']}", f"docker-buildx-plugin={m['docker_buildx_plugin']}",
             f"docker-compose-plugin={m['docker_compose_plugin']}"]
    log(f"apt mirror for {len(pkgs)} packages and what they need (a container, with network)")
    docker("run", "--rm", "--cpu-shares", "256", "-v", f"{docker_path(out)}:/out",
           "-e", f"DOCKER_KEY_URL={m['docker_key_url']}", "-e", f"DOCKER_REPO_URL={m['docker_repo_url']}",
           "-e", f"DOCKER_SUITE={m['docker_suite']}", "-e", f"PACKAGES={' '.join(pkgs)}",
           UBUNTU, "bash", "-c", APT_SCRIPT)
    for sub in ("ubuntu", "docker"):
        if not list((out / "apt" / sub / "dists").glob("*/*/binary-amd64/Packages")):
            shutil.rmtree(out)
            raise SystemExit(f"make-test-kit: the {sub} mirror has no Packages index (apt kept its lists compressed?)")
    return out / "apt"


WHEELS_SCRIPT = r'''
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get -qq update && apt-get -qq install -y --no-install-recommends python3-venv python3-pip ca-certificates \
  build-essential python3-dev pkg-config libasound2-dev >/dev/null
python3 -m venv /v
/v/bin/pip -q download --no-deps --require-hashes -d /out/wheels -r /src/requirements-linux-py314.lock
/v/bin/pip -q download --no-deps --require-hashes -d /out/wheels -r /src/requirements-linux-py314-no-deps.lock
ls /out/wheels | wc -l
'''


def make_wheels(work: Path, commit: str) -> Path:
    locks = b"".join(git_show(commit, n) for n in ("requirements-linux-py314.lock",
                                                     "requirements-linux-py314-no-deps.lock"))
    out = work / f"wheels-{hashlib.sha256(locks).hexdigest()[:12]}"
    if (out / "wheels").is_dir():
        return out / "wheels"
    shutil.rmtree(out, ignore_errors=True)
    src = out / "src"
    src.mkdir(parents=True)
    for n in ("requirements-linux-py314.lock", "requirements-linux-py314-no-deps.lock"):
        (src / n).write_bytes(git_show(commit, n))
    log("Python wheels for cp314 x86_64 (a container, with network)")
    docker("run", "--rm", "--cpu-shares", "256", "-v", f"{docker_path(out)}:/out", "-v",
           f"{docker_path(src)}:/src:ro", UBUNTU, "bash", "-c", WHEELS_SCRIPT)
    return out / "wheels"


def make_images(work: Path, pins: Pins, commit: str) -> list[tuple[str, Path]]:
    """(component name, docker save file): the database images by their
    pinned digest, and the music player built from the commit."""
    out = work / "images"
    out.mkdir(parents=True, exist_ok=True)
    res = []
    for ref in pins.compose_images:
        name = ref.split("@")[0]
        if not any(s in name for s in ("postgres", "flyway")):
            continue
        f = out / (re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".tar")
        if not f.is_file():
            log(f"docker pull + save {ref}")
            docker("pull", "-q", "--platform", "linux/amd64", ref)
            digest = ref.split("@")[1]
            repo = name.rsplit(":", 1)[0]
            docker("tag", ref, name)
            docker("save", "--platform", "linux/amd64", "-o", str(f), name, f"{repo}@{digest}")
        res.append((ref, f))
    f = out / f"domovoi-mpd-{hashlib.sha256(git_show(commit, 'domovoi/Dockerfile.mpd')).hexdigest()[:12]}.tar"
    if not f.is_file():
        log("docker build of the music player (domovoi/Dockerfile.mpd at the commit) + save")
        # Dockerfile.mpd copies nothing in: its own text is the whole context.
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "Dockerfile.mpd").write_bytes(git_show(commit, "domovoi/Dockerfile.mpd"))
            docker("build", "-q", "--platform", "linux/amd64", "-t", "domovoi-mpd:latest", "-f",
                   str(Path(d) / "Dockerfile.mpd"), d)
        docker("save", "--platform", "linux/amd64", "-o", str(f), "domovoi-mpd:latest")
    res.append(("domovoi-mpd", f))
    return res


def model_files(work: Path, tag: str, digest: str) -> list[Source]:
    """An Ollama model's manifest (held to DIGEST) and blobs (each held to
    its name), from this machine's Ollama when it has them, else the
    registry."""
    name, ver = tag.split(":", 1)
    ns = name if "/" in name else f"library/{name}"
    mdir = work / "ollama-models"
    man = mdir / "manifests" / "registry.ollama.ai" / ns / ver
    local = Path.home() / ".ollama" / "models"
    if not (man.is_file() and sha256(man) == digest):
        man.parent.mkdir(parents=True, exist_ok=True)
        found = None
        for cand in (local / "manifests" / "registry.ollama.ai" / ns).glob("*"):
            if cand.is_file() and sha256(cand) == digest:
                found = cand
        if found:
            shutil.copyfile(found, man)
        else:
            fetch(f"https://registry.ollama.ai/v2/{ns}/manifests/{ver}", man, sha=digest,
                  headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
    doc = json.loads(man.read_bytes())
    files = [Source(f"ollama/models/manifests/registry.ollama.ai/{ns}/{ver}", man)]
    for layer in [doc["config"], *doc["layers"]]:
        hexd = layer["digest"].split(":")[1]
        blob = mdir / "blobs" / f"sha256-{hexd}"
        if not (blob.is_file() and blob.stat().st_size == layer["size"]):
            lb = local / "blobs" / f"sha256-{hexd}"
            if lb.is_file() and lb.stat().st_size == layer["size"] and sha256(lb) == hexd:
                blob.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(lb, blob)
            else:
                fetch(f"https://registry.ollama.ai/v2/{ns}/blobs/{layer['digest']}", blob, sha=hexd,
                      size=layer["size"])
        files.append(Source(f"ollama/models/blobs/sha256-{hexd}", blob))
    return files


def make_kit(args: argparse.Namespace) -> None:
    commit = run("git", "rev-parse", args.commit, cwd=REPO, capture=True).strip()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    key = Path(args.key).resolve() if args.key else work / "test-release-key"
    if not key.is_file():
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "domovoi-test-kit", "-f", str(key))
    pins = Pins(commit)
    version, tag = args.version, f"v{args.version}"
    log(f"a kit of Domovoi {version} at {commit[:12]} into {args.out}")
    comps: list[Component] = []
    comps.append(Component("guide", "START-HERE", version, "MIT", [
        Source("START-HERE.txt", (
            f"A TEST offline kit of Domovoi {version} (commit {commit}), made by "
            "scripts/dev/make_test_kit.py. Its release signature is a throwaway key's, not "
            "Domovoi's: it is for testing the install, never for a real house.\n").encode())]))
    comps.append(Component("release", "DomovoiSetup", version, "MIT", make_release(work, key, version, commit),
                           pinned_by="release signature"))
    comps.append(Component("linux-installer", "install.sh", version, "MIT",
                           [Source("linux/install.sh", git_show(commit, "scripts/linux/install.sh"))],
                           pinned_by="release signature"))
    comps.append(Component("source", "domovoi", version, "MIT",
                           [Source("source/domovoi.bundle", make_bundle(work, commit, tag))],
                           pinned_by="release signature"))
    apt = make_apt(work, pins, commit)
    for sub, name in (("ubuntu", "ubuntu-26.04"), ("docker", "docker-ce")):
        root = apt / sub
        files = [Source(f"apt/{sub}/{p.relative_to(root).as_posix()}", p)
                 for p in sorted(root.rglob("*")) if p.is_file()]
        comps.append(Component("apt", name, "snapshot", "LicenseRef-distro-archive", files,
                               pinned_by="the archive's signed InRelease"))
    wheels = make_wheels(work, commit)
    comps.append(Component("python-wheels", "cp314-x86_64", "lock", "LicenseRef-mixed-see-sbom",
                           [Source(f"python/cp314-x86_64/{p.name}", p) for p in sorted(wheels.iterdir())],
                           pinned_by="requirements-linux-py314.lock"))
    for name, f in make_images(work, pins, commit):
        lic = "PostgreSQL" if "postgres" in name else ("Apache-2.0" if "flyway" in name else "LicenseRef-distro-archive")
        comps.append(Component("container-image", name, name.split("@")[-1], lic,
                               [Source(f"images/{f.name}", f)],
                               pinned_by=None if name == "domovoi-mpd" else "domovoi/docker-compose.yml"))
    asset = "ollama-linux-amd64.tar.zst"
    olla = fetch(f"https://github.com/ollama/ollama/releases/download/v{pins.ollama_version}/{asset}",
                 work / f"ollama-{pins.ollama_version}-{asset}", sha=pins.ollama_sha)
    comps.append(Component("ollama-runtime", "ollama", pins.ollama_version, "MIT",
                           [Source(f"ollama/{asset}", olla)], pinned_by="docs/LINUX_HOST.md"))
    includes = []
    if args.ai:
        includes.append("ai")
        m = pins.manifest
        for t in ("tool", "qa") + (("vision",) if args.vision else ()):
            tagname, dg = m[f"tier_floor_{t}_model"], m[f"tier_floor_{t}_digest"]
            lic = "LicenseRef-Llama-3.2-Community" if tagname.startswith("llama") else "Apache-2.0"
            comps.append(Component("ollama-model", tagname, dg, lic, model_files(work, tagname, dg),
                                   pinned_by="scripts/linux/provision-manifest.conf"))
        if args.vision:
            includes.append("vision")
    # Whisper at its pinned revision: the files faster-whisper's download takes.
    m = pins.manifest
    repo, rev = m["whisper_repo"], m["whisper_revision"]
    tree = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"https://huggingface.co/api/models/{repo}/tree/{rev}", headers=UA), timeout=60).read())
    wfiles = []
    for e in tree:
        n = e.get("path", "")
        if n in ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json") or n.startswith("vocabulary."):
            f = fetch(f"https://huggingface.co/{repo}/resolve/{rev}/{n}", work / "whisper" / rev / n,
                      sha=m["whisper_model_bin_sha256"] if n == "model.bin" else None)
            wfiles.append(Source(f"speech/whisper-{m['whisper_model']}/{n}", f))
    comps.append(Component("whisper-model", m["whisper_model"], rev, "MIT", wfiles,
                           pinned_by="scripts/linux/provision-manifest.conf"))
    py = pins.py
    for v, osha, osize, jblob, jsize in py["voices"]:
        lang_full, speaker, quality = v.split("-")
        rel = f"{lang_full.split('_')[0]}/{lang_full}/{speaker}/{quality}/{v}"
        o = fetch(f"{py['piper_repo']}/resolve/{py['piper_rev']}/{rel}.onnx", work / "piper" / f"{v}.onnx",
                  sha=osha, size=osize)
        j = fetch(f"{py['piper_repo']}/resolve/{py['piper_rev']}/{rel}.onnx.json", work / "piper" / f"{v}.onnx.json",
                  size=jsize)
        data = j.read_bytes()
        if hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() != jblob:
            raise SystemExit(f"make-test-kit: {v}.onnx.json does not match its pin")
        comps.append(Component("piper-voice", v, py["piper_rev"], "LicenseRef-public-domain",
                               [Source(f"speech/piper/{v}.onnx", o), Source(f"speech/piper/{v}.onnx.json", j)],
                               pinned_by="domovoi/clients/tts.py PIPER_PINS"))
    sp = fetch(py["speaker_url"] + py["speaker_file"], work / "speaker" / py["speaker_file"],
               sha=py["speaker_sha"], size=py["speaker_size"])
    comps.append(Component("speaker-model", "3dspeaker-eres2net", "eres2net-vox-v1", "Apache-2.0",
                           [Source(f"speech/speaker/{py['speaker_file']}", sp)],
                           pinned_by="domovoi/clients/speaker_encoder.py MODEL_SHA256"))
    wk = fetch(f"https://github.com/{py['wake_repo']}/releases/download/{py['wake_tag']}/domovoi_v1.onnx",
               work / "wake" / "domovoi_v1.onnx", sha=py["wake_sha"], size=py["wake_size"])
    comps.append(Component("wake-word", "domovoi_v1", py["wake_tag"], "CC-BY-NC-SA-4.0",
                           [Source("wake/domovoi_v1.onnx", wk)], pinned_by="domovoi/wake_default.py PUBLISHED"))
    rel = pins.android.get("release")
    avc = None
    if rel:
        avc = rel["version_code"]
        files = [Source("phone/release.json", git_show(commit, "android/release.json"))]
        for a in rel["apks"]:
            files.append(Source(f"phone/{a['file']}", fetch(a["url"], work / "phone" / a["file"],
                                                             sha=a["sha256"], size=a["size"])))
        comps.append(Component("phone-app", "domovoi-android", rel["version_name"], "MIT", files,
                               pinned_by="android/release.json"))
    log("writing the kit")
    doc = write_kit(args.out, version=version, commit=commit, components=comps, includes=includes,
                    android_version_code=avc)
    total = sum(e["size"] for c in doc["components"] for e in c["files"])
    log(f"done: {args.out}, {len(doc['components'])} components, {total / 1e9:.2f} GB")
    log(f"its release key (DOMOVOI_INSTALL_RELEASE_KEY): {' '.join(key.with_suffix('.pub').read_text().split()[:2])}"
        if key.with_suffix(".pub").is_file() else f"its release key: {key}.pub")
    if args.image:
        make_image(Path(args.out).resolve(), Path(args.image).resolve(), total)


IMAGE_SCRIPT = r'''
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get -qq update && apt-get -qq install -y --no-install-recommends dosfstools mtools >/dev/null
img=/img/$IMG
rm -f "$img"
truncate -s "${SIZE_MB}M" "$img"
mkfs.fat -F 32 -n DOMOVOIKIT "$img" >/dev/null
export MTOOLS_SKIP_CHECK=1
cd /kit
mcopy -s -Q -i "$img" ./* ::/
echo "image: $(du -h "$img" | cut -f1)"
'''


def make_image(kit: Path, image: Path, total: int) -> None:
    """The stick's image: one FAT32 file system labelled DOMOVOIKIT holding
    the kit (a partition table is the server's job; a loop-mounted file
    system is what a test needs)."""
    size_mb = int(total * 1.08 / 1e6) + 256
    log(f"FAT32 image {image} ({size_mb} MB)")
    image.parent.mkdir(parents=True, exist_ok=True)
    docker("run", "--rm", "--cpu-shares", "256", "-v", f"{docker_path(kit)}:/kit:ro", "-v",
           f"{docker_path(image.parent)}:/img", "-e", f"IMG={image.name}", "-e", f"SIZE_MB={size_mb}",
           UBUNTU, "bash", "-c", IMAGE_SCRIPT)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="the kit's folder (missing or empty)")
    ap.add_argument("--work", default=str(REPO / ".test-results" / "test-kit"), help="download cache")
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--version", default="1.5.0", help="the version the test release names")
    ap.add_argument("--key", help="the throwaway signing key (made in --work when missing)")
    ap.add_argument("--ai", action="store_true", help="the floor tier's AI models")
    ap.add_argument("--vision", action="store_true", help="with --ai: its vision model too")
    ap.add_argument("--image", help="also write the stick's FAT32 image here")
    make_kit(ap.parse_args())


if __name__ == "__main__":
    main()
