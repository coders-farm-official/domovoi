"""Ollama models: read from this server's Ollama folder, or fetched from
Ollama's registry by pin.

An Ollama model is a manifest (``manifests/registry.ollama.ai/library/
<name>/<tag>``, JSON) and the blobs it names (``blobs/sha256-<hex>``).
The pin in provision-manifest.conf is the manifest's own SHA-256 (what
``/api/tags`` reports as ``digest``); every blob is named by its SHA-256.
So a model is checked end to end: the manifest's bytes against the pin,
each blob against the digest the manifest names. The kit keeps Ollama's
layout under ``ollama/models/``, so an installer copies the folder into
Ollama's own and it lists the model.

Two sources, the same check:

* :func:`local_model`: a folder this process can read
  (:func:`domovoi.kit_builder.host.ollama_models_dirs`).
* :func:`fetch_model`: ``registry.ollama.ai``'s v2 API, through the
  outbound gate, into the kit cache (``~/.domovoi/offline_kit/cache/
  ollama``), when the server lacks the model or can't read Ollama's
  folder (provision.sh's ``/usr/share/ollama`` is the ``ollama`` account's
  home; whether the core's account can read it depends on its mode).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import fetch, host
from .licences import split_tag

REGISTRY = "registry.ollama.ai"
REGISTRY_URL = f"https://{REGISTRY}/v2/library"
MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"
MAX_MANIFEST_BYTES = 256 * 1024
LICENSE_MEDIA = "application/vnd.ollama.image.license"


@dataclass
class Blob:
    digest: str          # hex
    size: int
    media_type: str


@dataclass
class Model:
    tag: str                       # name:tag
    digest: str                    # the manifest's SHA-256
    manifest: bytes
    blobs: list[Blob]
    folder: Path | None = None     # the models folder holding it, when local
    licence_texts: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.manifest) + sum(b.size for b in self.blobs)

    def manifest_path(self) -> str:
        name, tag = split_tag(self.tag)
        return f"manifests/{REGISTRY}/library/{name}/{tag}"

    def blob_path(self, b: Blob) -> str:
        return f"blobs/sha256-{b.digest}"


class ModelProblem(ValueError):
    pass


def parse_manifest(raw: bytes) -> list[Blob]:
    try:
        doc = json.loads(raw.decode("utf-8"))
        items = [doc["config"], *doc["layers"]]
        blobs = []
        for it in items:
            d = str(it["digest"])
            if not d.startswith("sha256:") or len(d) != 71:
                raise ModelProblem(f"a blob digest {d!r} is not sha256")
            blobs.append(Blob(digest=d[7:], size=int(it["size"]), media_type=str(it.get("mediaType", ""))))
        return blobs
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as e:
        raise ModelProblem(f"not an Ollama manifest: {e}") from None


def _manifest_file(folder: Path, model: str) -> Path:
    name, tag = split_tag(model)
    return folder / "manifests" / REGISTRY / "library" / name / tag


def local_model(model: str, digest: str | None = None,
                folders: list[Path] | None = None) -> Model | None:
    """``model`` from a readable Ollama folder whose manifest is ``digest``
    (when given) and whose blobs are all there at their sizes. Cheap: the
    blobs' SHA-256 is checked as they are copied onto the kit."""
    for folder in folders if folders is not None else host.ollama_models_dirs():
        mf = _manifest_file(folder, model)
        try:
            raw = mf.read_bytes()
        except OSError:
            continue
        got = hashlib.sha256(raw).hexdigest()
        if digest is not None and got != digest:
            continue
        try:
            blobs = parse_manifest(raw)
        except ModelProblem:
            continue
        if all(fetch.present(folder / "blobs" / f"sha256-{b.digest}", b.size) for b in blobs):
            return Model(tag=model, digest=got, manifest=raw, blobs=blobs, folder=folder)
    return None


def list_local(folders: list[Path] | None = None) -> list[Model]:
    """Every complete model in the readable Ollama folders (library models
    only: a model pulled from another registry is not offered)."""
    out: dict[str, Model] = {}
    for folder in folders if folders is not None else host.ollama_models_dirs():
        lib = folder / "manifests" / REGISTRY / "library"
        if not lib.is_dir():
            continue
        for name_dir in sorted(lib.iterdir()):
            if not name_dir.is_dir():
                continue
            for tag_file in sorted(name_dir.iterdir()):
                tag = f"{name_dir.name}:{tag_file.name}"
                if tag in out or not tag_file.is_file():
                    continue
                m = local_model(tag, None, [folder])
                if m is not None:
                    out[tag] = m
    return list(out.values())


def cache_folder() -> Path:
    return host.cache_dir("ollama")


def registry_manifest(model: str, digest: str) -> bytes:
    """The pinned manifest from Ollama's registry (or the kit cache)."""
    mf = _manifest_file(cache_folder(), model)
    try:
        raw = mf.read_bytes()
        if hashlib.sha256(raw).hexdigest() == digest:
            return raw
    except OSError:
        pass
    name, tag = split_tag(model)
    raw = fetch.fetch_bytes(f"{REGISTRY_URL}/{name}/manifests/{tag}", max_bytes=MAX_MANIFEST_BYTES,
                            headers={"Accept": MANIFEST_ACCEPT})
    got = hashlib.sha256(raw).hexdigest()
    if got != digest:
        raise ModelProblem(f"Ollama's registry now serves another build of {model} "
                           f"(digest {got[:12]}, pinned {digest[:12]})")
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_bytes(raw)
    return raw


def fetch_model(model: str, digest: str, progress: fetch.Tick | None = None,
                have: list[Path] | None = None) -> Model:
    """``model`` at ``digest`` into the kit cache from Ollama's registry.
    Blobs another readable folder already holds (``have``) are not
    downloaded again: they are read from there."""
    raw = registry_manifest(model, digest)
    blobs = parse_manifest(raw)
    folder = cache_folder()
    name, _ = split_tag(model)
    for b in blobs:
        dest = folder / "blobs" / f"sha256-{b.digest}"
        if fetch.present(dest, b.size):
            continue
        if any(fetch.present(h / "blobs" / f"sha256-{b.digest}", b.size) for h in have or []):
            continue
        fetch.fetch_file(f"{REGISTRY_URL}/{name}/blobs/sha256:{b.digest}", dest, sha256=b.digest,
                         size=b.size, progress=progress)
    return Model(tag=model, digest=digest, manifest=raw, blobs=blobs, folder=folder)


def blob_source(m: Model, b: Blob, extra: list[Path] | None = None) -> Path | None:
    """Where ``b`` is on this server: the model's folder, then the cache,
    then any other readable folder."""
    for folder in [p for p in [m.folder, cache_folder(), *(extra or [])] if p is not None]:
        p = folder / "blobs" / f"sha256-{b.digest}"
        if fetch.present(p, b.size):
            return p
    return None


def missing_bytes(m: Model, extra: list[Path] | None = None) -> int:
    return sum(b.size for b in m.blobs if blob_source(m, b, extra) is None)


def licence_texts(m: Model, extra: list[Path] | None = None) -> list[str]:
    """The licence layers' texts (the Llama licence and its acceptable use
    policy, Qwen's Apache-2.0), for licences/."""
    out = []
    for b in m.blobs:
        if b.media_type != LICENSE_MEDIA or b.size > 512 * 1024:
            continue
        p = blob_source(m, b, extra)
        if p is not None:
            try:
                out.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return out
