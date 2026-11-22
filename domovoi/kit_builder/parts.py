"""What can go on a kit: every piece, its size, where it comes from.

A :class:`Part` is one component of kit.json before it exists: its kind,
name, version, licence and pin, which tick box brings it (``group``),
which targets need it, its size on the stick (exact when every file is
known, else an estimate the card says "about" for), how much has to be
downloaded first, and how to make its files ready (``acquire``, which
downloads or builds what is missing, by pin, through the outbound gate,
and returns the files as ``Source`` objects for the writer).

:func:`catalogue` lists every part this server could put on a kit;
plan.py picks the ones the card's boxes ask for. Nothing here writes the
kit; acquiring fills only the kit cache (``~/.domovoi/offline_kit/cache``)
and, for files the server itself uses (Piper voices, voice ID's model, the
phone app, the wake words), the server's own caches, as their own
downloads would.

Never here (design section 7): the database, ``.env``, the setup code,
the server's identity, names, media, chat, voice profiles, room codes.
Every source path below is a pinned upstream file, a model or package
cache, or a file of the release; the privacy test greps a built kit for
a seeded server's secrets.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from domovoi.offline_kit import Source
from domovoi.offline_kit import format as fmt
from domovoi.offline_kit.sshsig import RELEASE_KEY, parse_public_key, sshsig_problem

from . import apt_mirror, fetch, host, images, licences, ollama_store, wheels
from .pins import FilePin, Pins

log = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
TEXTS = HERE / "texts"
RELEASE_REPO = "coders-farm-official/domovoi"
RELEASE_DOWNLOAD = f"https://github.com/{RELEASE_REPO}/releases/download/"
RELEASE_API = f"https://api.github.com/repos/{RELEASE_REPO}/releases/tags/"

PC, MAC = "pc-x86_64", "mac-arm64"
GROUPS = ("base", "ai", "vision", "rooms", "household")


class PartError(RuntimeError):
    pass


@dataclass
class Ctx:
    """What acquiring needs: progress (bytes), the cancel check, a logger
    line for the card, and the targets ticked."""

    targets: tuple[str, ...]
    progress: Callable[[int], None] = lambda n: None
    say: Callable[[str], None] = lambda s: None
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run


@dataclass
class Part:
    id: str
    group: str
    kind: str
    name: str
    version: str
    licence: str
    size: int
    exact: bool
    download: int
    acquire: Callable[[Ctx], list[Source]]
    targets: tuple[str, ...] = ()            # () = every kit
    pinned_by: str | None = None
    household_added: bool = False
    files_estimate: int = 1
    label: str = ""                          # "the Windows parts", for refusals
    note: str = ""                           # shown beside a household item

    def wanted(self, targets: tuple[str, ...]) -> bool:
        return not self.targets or any(t in targets for t in self.targets)


@dataclass
class Release:
    version: str
    tag: str
    commit: str
    assets: dict[str, dict] = field(default_factory=dict)   # name -> {size, sha256}


# ─── helpers ─────────────────────────────────────────────────────────────

def _pinned_file(pin: FilePin, subdir: str, kit_path: str, *, local: Path | None = None) -> tuple[int, Callable[[Ctx], Source]]:
    """(bytes to download, a function giving the file's Source) for one
    pinned upstream file: a local copy when it matches, else the kit
    cache, else downloaded into the cache."""
    cached = host.kit_root() / "cache" / subdir / pin.name
    here = (local is not None and fetch.present(local, pin.size)) or fetch.present(cached, pin.size)
    missing = 0 if here else pin.size_or_zero

    def get(ctx: Ctx) -> Source:
        if local is not None and fetch.matches(local, pin.sha256, pin.size):
            return Source(kit_path, local)
        if not fetch.matches(cached, pin.sha256, pin.size):
            ctx.say(f"Downloading {pin.name}")
            fetch.fetch_file(pin.url, cached, sha256=pin.sha256, size=pin.size, progress=ctx.progress)
        return Source(kit_path, cached)

    return missing, get


def _const(sources: list[Source]) -> Callable[[Ctx], list[Source]]:
    return lambda ctx: sources


def _text(name: str) -> bytes:
    return (TEXTS / name).read_bytes()


# ─── the release ─────────────────────────────────────────────────────────

def release_assets(tag: str, refresh: bool = False) -> dict[str, dict]:
    """{name: {size, sha256}} of the GitHub release's files, cached. From
    GitHub's release API (each asset's size and digest); needs the
    internet the first time."""
    cache = host.cache_dir("release", tag) / "assets.json"
    if not refresh:
        try:
            doc = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc:
                return doc
        except (OSError, ValueError):
            pass
    raw = fetch.fetch_bytes(RELEASE_API + tag, max_bytes=2 * 1024 * 1024,
                            headers={"Accept": "application/vnd.github+json"})
    out: dict[str, dict] = {}
    for a in json.loads(raw).get("assets", []):
        name, digest = str(a.get("name", "")), str(a.get("digest") or "")
        if re.fullmatch(r"[A-Za-z0-9._-]+", name) and digest.startswith("sha256:"):
            out[name] = {"size": int(a["size"]), "sha256": digest[7:]}
    if not out:
        raise PartError(f"GitHub lists no files for the release {tag}")
    cache.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _release_file(rel: Release, name: str) -> FilePin | None:
    a = rel.assets.get(name)
    if not a:
        return None
    return FilePin(name=name, url=f"{RELEASE_DOWNLOAD}{rel.tag}/{name}", sha256=a["sha256"],
                   size=a["size"], pinned_by="the release's signed manifest" if name.endswith((".exe", ".pkg")) else None)


def _check_manifest(path: Path, sig: Path, target: str, rel: Release, installer: FilePin) -> None:
    """The release manifest is signed by the release key, names this
    release, and names this installer (the same check the kit's checker
    makes on the stick)."""
    spec = fmt.RELEASE_MANIFESTS[target]
    key = parse_public_key(RELEASE_KEY)
    raw = path.read_bytes()
    problem = sshsig_problem(sig.read_bytes(), raw, key, spec["namespace"]) if key else "no release key"
    if problem:
        raise PartError(f"{path.name} isn't signed by Domovoi's release key: {problem}")
    m = json.loads(raw)
    if (m.get("tag"), m.get("version"), m.get("commit")) != (rel.tag, rel.version, rel.commit):
        raise PartError(f"{path.name} is for {m.get('tag')}, not this server's {rel.tag}")
    if (m.get("size"), m.get("sha256")) != (installer.size, installer.sha256):
        raise PartError(f"{path.name} names another {spec['file']} than the release holds")


def release_parts(rel: Release) -> list[Part]:
    out: list[Part] = []
    for target in (PC, MAC):
        spec = fmt.RELEASE_MANIFESTS[target]
        mname = spec["manifest"].split("/")[-1]
        pins = [_release_file(rel, n) for n in (spec["file"], mname, f"{mname}.sig")]
        if any(p is None for p in pins):
            continue
        installer, man, sig = pins  # type: ignore[misc]
        files = [(p, _pinned_file(p, f"release/{rel.tag}", f"release/{p.name}")) for p in pins]  # type: ignore[union-attr]

        def acquire(ctx: Ctx, files=files, target=target, installer=installer) -> list[Source]:
            srcs = [get(ctx) for _, (_, get) in files]
            _check_manifest(Path(srcs[1].src), Path(srcs[2].src), target, rel, installer)
            return srcs

        out.append(Part(
            id=f"release:{target}", group="base", targets=(target,), kind="release",
            name=f"Domovoi {rel.version} installer ({fmt.TARGETS[target]})", version=rel.version,
            licence="MIT", pinned_by=f"{spec['manifest']} (signed by the release key)",
            size=sum(p.size_or_zero for p, _ in files), exact=True,
            download=sum(m for _, (m, _) in files), acquire=acquire, files_estimate=3,
            label="the PC installer" if target == PC else "the Mac parts"))
    sums = _release_file(rel, "SHA256SUMS")
    if sums:
        miss, get = _pinned_file(sums, f"release/{rel.tag}", "release/SHA256SUMS")
        out.append(Part(id="release:sums", group="base", kind="release", name="The release's SHA256SUMS",
                        version=rel.version, licence="MIT", pinned_by=None, size=sums.size_or_zero,
                        exact=True, download=miss, acquire=lambda ctx, get=get: [get(ctx)],
                        label="the release files"))
    return out


# ─── the parts ───────────────────────────────────────────────────────────

def source_parts(rel: Release, repo_dir: str, run=subprocess.run) -> list[Part]:
    """install.sh at the release commit, and the git bundle up to the tag
    (refs: the tag and HEAD, both at the release commit)."""
    from domovoi import git_run

    def install_sh(ctx: Ctx) -> list[Source]:
        r = git_run.run(["git", "show", f"{rel.commit}:scripts/linux/install.sh"], cwd=repo_dir,
                        timeout=30, binary=True)
        if r.returncode != 0:
            raise PartError("install.sh isn't in the release commit")
        return [Source("linux/install.sh", r.stdout)]

    bundle = host.cache_dir("source") / f"domovoi-{rel.tag}.bundle"

    def make_bundle(ctx: Ctx) -> list[Source]:
        if not bundle.is_file():
            ctx.say("Packing Domovoi's source")
            part = bundle.with_name(bundle.name + ".part")
            r = git_run.run(["git", "bundle", "create", str(part), rel.tag, "HEAD"], cwd=repo_dir,
                            timeout=1800)
            if r.returncode != 0:
                part.unlink(missing_ok=True)
                raise PartError(f"git bundle failed: {(r.stderr or '').strip()[-300:]}")
            part.replace(bundle)
        return [Source("source/domovoi.bundle", bundle)]

    size = bundle.stat().st_size if bundle.is_file() else _pack_size(repo_dir)
    return [
        Part(id="source:install-sh", group="base", kind="linux-installer", name="install.sh",
             version=rel.version, licence="MIT", pinned_by="the release commit", size=64 * 1024,
             exact=False, download=0, acquire=install_sh),
        Part(id="source:bundle", group="base", kind="source", name="Domovoi's source (git bundle)",
             version=rel.tag, licence="MIT", pinned_by="the release commit (signed tag)", size=size,
             exact=bundle.is_file(), download=0, acquire=make_bundle),
    ]


def _pack_size(repo_dir: str) -> int:
    from domovoi import git_run

    try:
        r = git_run.run(["git", "count-objects", "-v"], cwd=repo_dir, timeout=30)
        kib = int(re.search(r"size-pack: (\d+)", r.stdout).group(1))  # type: ignore[union-attr]
        return kib * 1024
    except Exception:  # noqa: BLE001 — an estimate only
        return 150 * 1000 * 1000


def apt_parts(p: Pins) -> list[Part]:
    out = []
    for arch, target in (("amd64", PC), ("arm64", MAC)):
        doc = apt_mirror.cached(p, arch)
        root = apt_mirror.mirror_dir(arch) / "mirror"
        if doc:
            size = sum((root / f).stat().st_size for f in doc["files"])
            count = len(doc["files"])
        else:
            size, count = (640_000_000 if arch == "amd64" else 600_000_000), 460

        def acquire(ctx: Ctx, arch=arch) -> list[Source]:
            d = apt_mirror.cached(p, arch)
            from domovoi import egress
            if d is None or (not apt_mirror.fresh(d) and not egress.internet_turned_off()):
                ctx.say(f"Fetching the Ubuntu and Docker packages ({arch})")
                d = apt_mirror.build(p, arch, run=ctx.run)
            r = apt_mirror.mirror_dir(arch) / "mirror"
            return [Source(f"apt/{arch}/{f}", r / f) for f in d["files"]]

        out.append(Part(id=f"apt:{arch}", group="base", targets=(target,), kind="apt",
                        name=f"Ubuntu 26.04 and Docker packages ({arch})", version=p.manifest["docker_ce"],
                        licence="LicenseRef-distro-archive",
                        pinned_by="Ubuntu's, Docker's and NVIDIA's signed indexes (InRelease)",
                        size=size, exact=bool(doc), download=0 if doc else size,
                        acquire=acquire, files_estimate=count,
                        label="the Windows parts" if target == PC else "the Mac parts"))
    return out


def wheel_parts(p: Pins) -> list[Part]:
    picks = wheels.cached_picks(p.lock_text, p.nodeps_lock_text)
    out = []
    for arch, target in (("x86_64", PC), ("aarch64", MAC)):
        if picks:
            ps = picks[arch]
            size, exact, count = sum(x.size for x in ps), True, len(ps)
            missing = sum(x.size for x in wheels.missing(ps))
        else:
            size, exact, count = (175_000_000 if arch == "x86_64" else 145_000_000), False, 75
            missing = size

        def acquire(ctx: Ctx, arch=arch) -> list[Source]:
            ps = wheels.resolve(p.lock_text, p.nodeps_lock_text)[arch]
            if wheels.missing(ps):
                ctx.say(f"Downloading Python packages ({arch})")
                wheels.download(ps, ctx.progress)
            return [Source(f"{wheels.folder(arch)}/{x.filename}", wheels.file_path(x)) for x in ps]

        out.append(Part(id=f"python:{arch}", group="base", targets=(target,), kind="python-wheels",
                        name=f"Python packages ({arch})", version=wheels.lock_key(p.lock_text, p.nodeps_lock_text),
                        licence="LicenseRef-mixed-see-sbom", pinned_by="requirements-linux-py314.lock (hashes)",
                        size=size, exact=exact, download=missing, acquire=acquire, files_estimate=count,
                        label="the Windows parts" if target == PC else "the Mac parts"))
    return out


def image_parts(p: Pins, mpd_tag: str | None) -> list[Part]:
    out = []
    for pin in p.images:
        def acquire(ctx: Ctx, pin=pin) -> list[Source]:
            arches = ("amd64", "arm64") if MAC in ctx.targets else ("amd64",)
            ctx.say(f"Saving the {pin.repo} image")
            path = images.save(pin, arches, run=ctx.run)
            return [Source(images.kit_path(pin.repo, pin.tag), path)]

        cached = images.archive_path(("amd64",), pin.repo, pin.tag)
        size = cached.stat().st_size if cached.is_file() else images.estimate(pin.repo)
        here = cached.is_file() or _have_image(pin)
        out.append(Part(id=f"image:{pin.repo}", group="base", kind="container-image", name=pin.ref,
                        version=pin.digest, licence=pin.licence, pinned_by="domovoi/docker-compose.yml",
                        size=size, exact=cached.is_file(), download=0 if here else size, acquire=acquire,
                        label="the database images"))
    if mpd_tag:
        def mpd(ctx: Ctx) -> list[Source]:
            if images.local_image_id(mpd_tag, ctx.run) is None:
                raise PartError("this server has no music player image yet (it is built when the "
                                "first room plays music)")
            repo, _, tag = mpd_tag.partition(":")
            return [Source(images.kit_path(repo, tag or "latest"), images.save_local(mpd_tag, ctx.run))]

        out.append(Part(id="image:mpd", group="base", kind="container-image", name=mpd_tag,
                        version="built by this server", licence="LicenseRef-distro-archive", pinned_by=None,
                        size=images.estimate("mpd"), exact=False, download=0, acquire=mpd,
                        targets=(PC,)))
    return out


def _have_image(pin) -> bool:
    try:
        return images.have(pin)
    except (OSError, subprocess.SubprocessError):
        return False


def ollama_runtime_parts(p: Pins) -> list[Part]:
    miss, get = _pinned_file(p.ollama_linux, "ollama", f"ollama/{p.ollama_linux.name}")
    out = [Part(id="ollama:linux", group="ai", targets=(PC,), kind="ollama-runtime",
                name="Ollama for Linux", version=p.ollama_linux.url.split("/")[-2], licence="MIT",
                pinned_by=p.ollama_linux.pinned_by, size=p.ollama_linux.size_or_zero, exact=True,
                download=miss, acquire=lambda ctx: [get(ctx)], label="Ollama")]
    if p.ollama_darwin:
        dm, dget = _pinned_file(p.ollama_darwin, "mac", f"mac/{p.ollama_darwin.name}")
        out.append(Part(id="ollama:darwin", group="ai", targets=(MAC,), kind="mac-part",
                        name="Ollama for macOS", version=p.ollama_darwin.url.split("/")[-2], licence="MIT",
                        pinned_by=p.ollama_darwin.pinned_by, size=p.ollama_darwin.size_or_zero, exact=True,
                        download=dm, acquire=lambda ctx: [dget(ctx)], label="the Mac parts"))
    return out


def _model_part(tag: str, digest: str | None, group: str, *, household: bool = False,
                local: ollama_store.Model | None = None, standard_only: bool = False) -> Part | None:
    licence = licences.model_licence(tag)
    if licence is None or licences.refusal(tag):
        return None
    dirs = host.ollama_models_dirs()
    if local is None and digest:
        local = ollama_store.local_model(tag, digest, dirs)
        if local is None:
            local = ollama_store.local_model(tag, digest, [ollama_store.cache_folder()])
    if local is None:
        return None
    size, missing = local.size, ollama_store.missing_bytes(local, dirs)

    def acquire(ctx: Ctx) -> list[Source]:
        m = local
        if m is None or ollama_store.missing_bytes(m, dirs):
            ctx.say(f"Downloading the model {tag}")
            m = ollama_store.fetch_model(tag, digest or "", ctx.progress, have=dirs)
        return _model_sources(m, dirs)

    return Part(id=f"model:{tag}", group=group, kind="ollama-model", name=tag,
                version=local.digest, licence=licence,
                pinned_by=None if household else "scripts/linux/provision-manifest.conf",
                household_added=household, size=size, exact=local is not None, download=missing,
                acquire=acquire, files_estimate=6, label="the AI models",
                note="not checked against a release" if household else "")


def _model_sources(m: ollama_store.Model, dirs: list[Path]) -> list[Source]:
    srcs = [Source(f"ollama/models/{m.manifest_path()}", m.manifest)]
    for b in m.blobs:
        src = ollama_store.blob_source(m, b, dirs)
        if src is None:
            raise PartError(f"{m.tag}: a blob went missing while the kit was made")
        srcs.append(Source(f"ollama/models/{m.blob_path(b)}", src))
    return srcs


def model_parts(p: Pins) -> list[Part]:
    """Every pinned tier model: the floor tier's always (fetched when
    missing), the standard tier's only when this server has them."""
    out = []
    floor = {m.tag for m in p.models if m.tier == "floor"}
    for m in p.models:
        group = "vision" if m.role == "vision" else "ai"
        part = _model_part(m.tag, m.digest, group, standard_only=m.tag not in floor)
        if part is None and m.tag in floor:
            # Not on this server and not cached: its size comes from the
            # registry's manifest (cached), or a rough figure.
            part = _model_part_remote(m.tag, m.digest, group)
        if part is not None:
            out.append(part)
    return out


#: Rough sizes of the floor models, for the card before their manifests
#: are known (design section 1).
MODEL_ESTIMATES = {"qwen3:4b": 2_500_000_000, "llama3.2:3b": 2_020_000_000, "qwen3-vl:2b": 1_930_000_000}


def _model_part_remote(tag: str, digest: str, group: str) -> Part | None:
    licence = licences.model_licence(tag)
    if licence is None or licences.refusal(tag):
        return None
    size, exact = 0, False
    try:
        raw = ollama_store._manifest_file(ollama_store.cache_folder(), tag).read_bytes()
        import hashlib
        if hashlib.sha256(raw).hexdigest() == digest:
            size, exact = len(raw) + sum(b.size for b in ollama_store.parse_manifest(raw)), True
    except (OSError, ollama_store.ModelProblem):
        pass
    if not exact:
        from domovoi import egress
        if not egress.internet_turned_off():
            try:
                raw = ollama_store.registry_manifest(tag, digest)
                size, exact = len(raw) + sum(b.size for b in ollama_store.parse_manifest(raw)), True
            except Exception as e:  # noqa: BLE001 — the estimate stands
                log.info("offline kit: %s's manifest not read: %s", tag, e)
    if not exact:
        size = next((v for k, v in MODEL_ESTIMATES.items() if tag.startswith(k)), 2_500_000_000)
    dirs = host.ollama_models_dirs()

    def acquire(ctx: Ctx) -> list[Source]:
        ctx.say(f"Downloading the model {tag}")
        return _model_sources(ollama_store.fetch_model(tag, digest, ctx.progress, have=dirs), dirs)

    return Part(id=f"model:{tag}", group=group, kind="ollama-model", name=tag, version=digest,
                licence=licence, pinned_by="scripts/linux/provision-manifest.conf", size=size, exact=exact,
                download=size, acquire=acquire, files_estimate=6, label="the AI models")


def household_model_parts(p: Pins) -> list[Part]:
    pinned = {m.tag for m in p.models}
    out = []
    for m in ollama_store.list_local():
        if m.tag in pinned:
            continue
        part = _model_part(m.tag, m.digest, "household", household=True, local=m)
        if part is not None:
            out.append(part)
    return out


def refused_models(p: Pins) -> list[dict]:
    """Models on this server that can never go on a kit, with the reason
    (the card lists them, greyed)."""
    pinned = {m.tag for m in p.models}
    return [{"name": m.tag, "reason": licences.refusal(m.tag)} for m in ollama_store.list_local()
            if m.tag not in pinned and licences.refusal(m.tag)]


def speech_parts(p: Pins) -> list[Part]:
    from domovoi import whisper_cache
    from domovoi.clients import speaker_encoder as se
    from domovoi.clients import tts
    from domovoi.config import settings

    out: list[Part] = []
    w = p.whisper
    snap = None
    repo = whisper_cache.repo_dir(w["whisper_repo"])
    if repo is not None:
        cand = repo / "snapshots" / w["whisper_revision"]
        if fetch.present(cand / "model.bin", w["whisper_model_bin_bytes"]):
            snap = cand

    def whisper(ctx: Ctx) -> list[Source]:
        folder = snap or _fetch_whisper(w, ctx)
        if not fetch.matches(folder / "model.bin", w["whisper_model_bin_sha256"], w["whisper_model_bin_bytes"]):
            raise PartError("Whisper's model.bin doesn't match its pin")
        return [Source(f"speech/whisper-{w['whisper_model']}/{f.name}", f.resolve())
                for f in sorted(folder.iterdir()) if f.is_file()]

    out.append(Part(id="speech:whisper", group="base", targets=(PC,), kind="whisper-model",
                    name=f"Whisper {w['whisper_model']}", version=w["whisper_revision"], licence="MIT",
                    pinned_by="scripts/linux/provision-manifest.conf", size=w["whisper_model_bin_bytes"] + 3_000_000,
                    exact=False, download=0 if snap else w["whisper_model_bin_bytes"], acquire=whisper,
                    files_estimate=5, label="the speech models"))

    voices = Path(settings.voice_models_dir)
    for v in tts.SHIPPED_VOICES:
        onnx, js = voices / f"{v.model_ref}.onnx", voices / f"{v.model_ref}.onnx.json"
        here = fetch.present(onnx, v.onnx_bytes) and fetch.present(js, v.json_bytes)

        def voice(ctx: Ctx, v=v, onnx=onnx, js=js) -> list[Source]:
            if not fetch.matches(onnx, v.onnx_sha256, v.onnx_bytes) or not js.is_file():
                ctx.say(f"Downloading the voice {v.name}")
                voices.mkdir(parents=True, exist_ok=True)
                tts._piper_download(v.model_ref, voices)
            return [Source(f"speech/piper/{onnx.name}", onnx), Source(f"speech/piper/{js.name}", js)]

        out.append(Part(id=f"voice:{v.model_ref}", group="base", kind="piper-voice", name=v.model_ref,
                        version=tts.PIPER_PINNED_REVISION, licence="LicenseRef-public-domain",
                        pinned_by="domovoi/clients/tts.py PIPER_PINS", size=v.onnx_bytes + v.json_bytes,
                        exact=True, download=0 if here else v.onnx_bytes, acquire=voice, files_estimate=2,
                        label="the speech models"))

    spk = voices / se.MODEL_FILE

    def speaker(ctx: Ctx) -> list[Source]:
        if not fetch.matches(spk, se.MODEL_SHA256, se.MODEL_BYTES):
            ctx.say("Downloading voice ID's model")
            se.download_model(voices)
        return [Source(f"speech/speaker/{se.MODEL_FILE}", spk)]

    out.append(Part(id="speech:speaker", group="base", kind="speaker-model", name=se.MODEL_TAG,
                    version=se.MODEL_SHA256[:12], licence="Apache-2.0",
                    pinned_by="domovoi/clients/speaker_encoder.py MODEL_SHA256", size=se.MODEL_BYTES,
                    exact=True, download=0 if fetch.present(spk, se.MODEL_BYTES) else se.MODEL_BYTES,
                    acquire=speaker, label="the speech models"))

    if p.ggml_stt:
        miss, get = _pinned_file(p.ggml_stt, "mac", f"mac/{p.ggml_stt.name}", local=host.mac_stt_file())
        out.append(Part(id="mac:stt", group="base", targets=(MAC,), kind="mac-part",
                        name="whisper.cpp small.en", version=p.ggml_stt.sha256[:12], licence="MIT",
                        pinned_by=p.ggml_stt.pinned_by, size=p.ggml_stt.size_or_zero, exact=True,
                        download=miss, acquire=lambda ctx: [get(ctx)], label="the Mac parts"))
    return out


def _fetch_whisper(w: dict, ctx: Ctx) -> Path:
    """The pinned faster-whisper snapshot into the kit cache, from Hugging
    Face at the pinned revision: model.bin held to its SHA-256, the small
    files to the git blob ids the revision's tree lists."""
    import hashlib

    folder = host.cache_dir("whisper", w["whisper_revision"])
    api = f"https://huggingface.co/api/models/{w['whisper_repo']}/tree/{w['whisper_revision']}"
    tree = json.loads(fetch.fetch_bytes(api, max_bytes=1024 * 1024))
    ctx.say(f"Downloading Whisper {w['whisper_model']}")
    for e in tree:
        if e.get("type") != "file":
            continue
        name = str(e["path"])
        if "/" in name or name.startswith("."):
            continue
        url = f"https://huggingface.co/{w['whisper_repo']}/resolve/{w['whisper_revision']}/{name}"
        dest = folder / name
        if name == "model.bin":
            if not fetch.matches(dest, w["whisper_model_bin_sha256"], w["whisper_model_bin_bytes"]):
                fetch.fetch_file(url, dest, sha256=w["whisper_model_bin_sha256"],
                                 size=w["whisper_model_bin_bytes"], progress=ctx.progress)
            continue
        if e.get("lfs"):
            sha = str(e["lfs"]["oid"])
            if not fetch.matches(dest, sha, int(e["lfs"]["size"])):
                fetch.fetch_file(url, dest, sha256=sha, size=int(e["lfs"]["size"]), progress=ctx.progress)
            continue
        size = int(e.get("size", 0))
        data = fetch.fetch_bytes(url, max_bytes=max(size, 1))
        if hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() != e.get("oid"):
            raise PartError(f"Whisper's {name} doesn't match the pinned revision")
        dest.write_bytes(data)
    return folder


def wake_parts() -> list[Part]:
    from domovoi import wake_default
    from domovoi.config import settings
    from domovoi.satellite_media import cache, fetchers

    out = []
    word = wake_default.PUBLISHED["domovoi_v1"]
    local = Path(settings.wake_models_dir) / word.file
    pin = FilePin(name=word.file, url=word.url, sha256=word.sha256 or "", size=word.size,
                  pinned_by="domovoi/wake_default.py PUBLISHED")
    miss, get = _pinned_file(pin, "wake", f"wake/{word.file}", local=local)
    notice = (b"Domovoi wake word (domovoi_v1) - Coders Farm, github.com/" + wake_default.MODELS_REPO.encode()
              + b"\nLicensed CC BY-NC-SA 4.0 (licences/CC-BY-NC-SA-4.0.txt): give credit, share under the "
              b"same licence, never sell.\n")
    out.append(Part(id="wake:domovoi_v1", group="base", kind="wake-word", name="domovoi_v1", version=word.tag,
                    licence=licences.WAKE_NC_SA, pinned_by=pin.pinned_by, size=(word.size or 0) + len(notice),
                    exact=True, download=miss,
                    acquire=lambda ctx: [get(ctx), Source("wake/domovoi_v1.LICENSE-NOTICE.txt", notice)],
                    files_estimate=2, label="the wake words"))
    oww = cache.CACHE_ROOT / "oww_models"
    for name in ("embedding_model.onnx", "melspectrogram.onnx", "silero_vad.onnx", "hey_jarvis_v0.1.onnx"):
        sha, size = fetchers.OWW_MODEL_FILES[name]
        fp = FilePin(name=name, url=fetchers.OWW_RELEASE_URL + name, sha256=sha, size=size,
                     pinned_by="domovoi/satellite_media/fetchers.py OWW_MODEL_FILES")
        m, g = _pinned_file(fp, "wake", f"wake/{name}", local=oww / name)
        lic = licences.oww_licence(name)
        kind = "wake-word" if name.startswith("hey_") else "wake-support"
        out.append(Part(id=f"wake:{name}", group="base", kind=kind,
                        name=name, version="openWakeWord v0.5.1" if "silero" not in name else "Silero VAD v4",
                        licence=lic, pinned_by=fp.pinned_by, size=size, exact=True, download=m,
                        acquire=lambda ctx, g=g: [g(ctx)], label="the wake words"))
    return out


def household_wake_parts() -> list[Part]:
    from domovoi import wake_default
    from domovoi.config import settings

    d = Path(settings.wake_models_dir)
    out = []
    try:
        files = sorted(d.glob("*.onnx"))
    except OSError:
        files = []
    for f in files:
        slug = f.stem
        if slug in wake_default.PUBLISHED or slug in wake_default.STOCK_SLUGS or not re.fullmatch(r"[a-z0-9_]{1,64}", slug):
            continue
        size = f.stat().st_size
        out.append(Part(id=f"wake-household:{slug}", group="household", kind="wake-word", name=slug,
                        version=f"{size}", licence=licences.WAKE_NC_SA, pinned_by=None, household_added=True,
                        size=size, exact=True, download=0,
                        acquire=lambda ctx, f=f: [Source(f"wake/household/{f.name}", f)],
                        note="not checked against a release"))
    return out


def phone_parts(p: Pins) -> list[Part]:
    from domovoi.satellite_media import cache

    rel = p.android
    if not rel:
        return []
    bucket = cache.CACHE_ROOT / "android_app" / str(rel["version_code"])
    getters, size, miss = [], 0, 0
    for a in rel["apks"]:
        fp = FilePin(name=a["file"], url=a["url"], sha256=a["sha256"], size=int(a["size"]),
                     pinned_by="android/release.json")
        m, g = _pinned_file(fp, "phone", f"phone/{a['file']}", local=bucket / a["file"])
        getters.append(g)
        size += fp.size_or_zero
        miss += m
    release_json = json.dumps({"manifest_version": 1, "release": rel}, indent=2).encode()

    def acquire(ctx: Ctx) -> list[Source]:
        return [g(ctx) for g in getters] + [Source("phone/release.json", release_json)]

    return [Part(id="phone:android", group="base", kind="phone-app", name="Domovoi for Android",
                 version=str(rel["version_name"]), licence="MIT", pinned_by="android/release.json",
                 size=size + len(release_json), exact=True, download=miss, acquire=acquire,
                 files_estimate=len(getters) + 1, label="the phone app")]


def platform_parts(p: Pins) -> list[Part]:
    out = []
    for fp in p.windows:
        m, g = _pinned_file(fp, "windows", f"windows/{fp.name}")
        lic = "MIT" if fp.name.endswith(".msi") else "LicenseRef-distro-archive"
        out.append(Part(id=f"windows:{fp.name}", group="base", targets=(PC,), kind="windows-part",
                        name=fp.name, version=fp.sha256[:12], licence=lic, pinned_by=fp.pinned_by,
                        size=fp.size_or_zero, exact=fp.size is not None, download=m,
                        acquire=lambda ctx, g=g: [g(ctx)], label="the Windows parts"))
    for fp in p.mac:
        if fp is p.ollama_darwin or fp is p.ggml_stt:
            continue
        m, g = _pinned_file(fp, "mac", f"mac/{fp.name}")
        out.append(Part(id=f"mac:{fp.name}", group="base", targets=(MAC,), kind="mac-part", name=fp.name,
                        version=fp.sha256[:12], licence="LicenseRef-distro-archive", pinned_by=fp.pinned_by,
                        size=fp.size_or_zero, exact=fp.size is not None, download=m,
                        acquire=lambda ctx, g=g: [g(ctx)], label="the Mac parts"))
    return out


def rooms_parts(rel: Release) -> list[Part]:
    """What an offline server needs to add rooms: the Windows satellite
    Setup, Debian's netinst for the room stick, and the rooms' package
    cache as this server holds it (P6 teaches the installers to use it)."""
    from domovoi.satellite_media import cache
    from domovoi.usb_stick import image as stick_image

    out = []
    sat = _release_file(rel, "DomovoiSatelliteSetup.exe")
    if sat:
        m, g = _pinned_file(sat, f"release/{rel.tag}", f"rooms/{sat.name}")
        out.append(Part(id="rooms:windows-setup", group="rooms", kind="rooms", name=sat.name,
                        version=rel.version, licence="MIT", pinned_by="the release's SHA256SUMS",
                        size=sat.size_or_zero, exact=True, download=m, acquire=lambda ctx: [g(ctx)],
                        label="the rooms' parts"))
    try:
        pin = stick_image.load_pin()
        fp = FilePin(name=pin["name"], url=pin["urls"][0], sha256=pin["sha256"], size=pin["size"],
                     pinned_by="domovoi/usb_stick/debian-image.json")
        m, g = _pinned_file(fp, "rooms", f"rooms/{pin['name']}",
                            local=cache.CACHE_ROOT / "usb_stick" / pin["version"] / pin["name"])
        out.append(Part(id="rooms:debian", group="rooms", kind="rooms", name=pin["name"], version=pin["version"],
                        licence="LicenseRef-distro-archive", pinned_by=fp.pinned_by, size=pin["size"], exact=True,
                        download=m, acquire=lambda ctx: [g(ctx)], label="the rooms' parts"))
    except stick_image.PinError:
        pass
    files = []
    for bucket in ("wheels", "debs", "oww_models"):
        root = cache.CACHE_ROOT / bucket
        if root.is_dir():
            for f in sorted(root.rglob("*")):
                if f.is_file() and not f.is_symlink() and not f.name.startswith("."):
                    rel_path = f.relative_to(cache.CACHE_ROOT).as_posix()
                    if fmt.path_problem(f"rooms/satellite/{rel_path}") is None:
                        files.append((f"rooms/satellite/{rel_path}", f))
    if files:
        out.append(Part(id="rooms:satellite-cache", group="rooms", kind="rooms",
                        name="The rooms' package cache", version="this server's", licence="LicenseRef-mixed-see-sbom",
                        pinned_by=None, size=sum(f.stat().st_size for _, f in files), exact=True, download=0,
                        acquire=lambda ctx: [Source(k, f) for k, f in files], files_estimate=len(files)))
    return out


def licence_part(rel: Release, repo_dir: str, model_parts_: list[Part]) -> Part:
    """licences/: Domovoi's licence, the licence list, the full texts the
    kit's files call for, the release's SBOMs, and a README tying them
    together (the Ubuntu/Debian source note and "Built with Llama")."""
    from domovoi import git_run

    sboms = [p for p in (_release_file(rel, n) for n in ("domovoi-server.cdx.json", "domovoi-android.cdx.json",
                                                          "domovoi-web.cdx.json")) if p]
    getters = [_pinned_file(s, f"release/{rel.tag}", f"licences/{s.name}") for s in sboms]

    def acquire(ctx: Ctx) -> list[Source]:
        srcs = []
        for path, dest in (("LICENSE", "licences/Domovoi-LICENSE.txt"), ("docs/LICENSES.md", "licences/LICENSES.md")):
            r = git_run.run(["git", "show", f"{rel.commit}:{path}"], cwd=repo_dir, timeout=30, binary=True)
            if r.returncode == 0:
                srcs.append(Source(dest, r.stdout))
        srcs.append(Source("licences/Apache-2.0.txt", _text("Apache-2.0.txt")))
        srcs.append(Source("licences/CC-BY-NC-SA-4.0.txt", _text("CC-BY-NC-SA-4.0.txt")))
        srcs.append(Source("licences/README.txt", readme(model_parts_).encode()))
        for _, g in getters:
            srcs.append(g(ctx))
        return srcs

    return Part(id="licences", group="base", kind="licence", name="Licences and notices", version=rel.version,
                licence="MIT", pinned_by=None, size=sum(s.size_or_zero for s in sboms) + 120_000, exact=False,
                download=sum(m for m, _ in getters), acquire=acquire, files_estimate=8 + len(sboms),
                label="the release files")


def readme(model_parts_: list[Part]) -> str:
    llama = any(p.licence == "LicenseRef-Llama-3.2-Community" for p in model_parts_)
    lines = [
        "Licences and notices for this Domovoi offline kit",
        "",
        "Don't sell this stick: some files on it may only be shared for free.",
        "",
        "Domovoi itself, its installers and the phone app: MIT (Domovoi-LICENSE.txt).",
        "LICENSES.md lists every licence Domovoi depends on. The *.cdx.json files are the",
        "release's software bills of materials (every Python package and app library).",
        "",
        "Wake words: the Domovoi wake word (domovoi_v1) and openWakeWord's hey_jarvis and",
        "melspectrogram models are licensed CC BY-NC-SA 4.0 (CC-BY-NC-SA-4.0.txt): give credit,",
        "share them under the same licence, never sell them. openWakeWord's embedding model is",
        "Apache-2.0; Silero VAD is MIT.",
        "",
        "Speech: Whisper small.en (MIT), the Piper voices norman, kristin and ljspeech (public",
        "domain), 3D-Speaker's ERes2Net voice ID model (Apache-2.0, Apache-2.0.txt).",
        "",
        "AI models (ollama/models): each model's licence is one of its own files (Ollama's",
        "license layer). Qwen3 and Qwen3-VL: Apache-2.0.",
    ]
    if llama:
        lines += ["Built with Llama. Llama 3.2 is licensed under the Llama 3.2 Community License,",
                  "Copyright (c) Meta Platforms, Inc. All Rights Reserved. The licence and the acceptable",
                  "use policy are the llama3.2 model's license layers in ollama/models/blobs/."]
    lines += [
        "",
        "Ubuntu, Debian and Docker packages (apt/, images/, windows/, mac/) are the",
        "distributions' own, unmodified, under their own licences, GPL parts included. Their",
        "source is in Ubuntu's (https://launchpad.net/ubuntu) and Debian's",
        "(https://sources.debian.org) public archives.",
        "",
        "Ollama (MIT) is unmodified; it bundles GPU runtimes under their own terms.",
        "",
    ]
    return "\n".join(lines)
