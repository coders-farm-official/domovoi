"""The openWakeWord base models, served to spare-device satellites.

A Pi satellite runs openWakeWord from its Python package, and a prepared
card carries the base models the core cached for it (the ``oww_models``
bucket of the satellite media cache, every file pinned by SHA-256 and size
in :data:`domovoi.satellite_media.fetchers.OWW_MODEL_FILES`). An Android
phone in room mode has no Python package and no card: it runs the same
pipeline on ONNX Runtime (android ``roommode/``) and needs the same files —
the melspectrogram and embedding models, the default wake word's model and
the Silero voice detector — from the server it belongs to. The app does not
bundle them: the pretrained wake-word models are not under a licence the
app may ship.

This is that channel, beside ``/v1/wake-models`` (trained models), with the
same shape as the other file channels:

* ``GET /v1/wake-base/manifest.sig`` — ``{name: sha256}`` inside the
  server-signed envelope (:data:`WAKE_BASE_CHANNEL`), issued and serialled
  like every signed list. Signed only: no client predates it.
* ``GET /v1/wake-base/{name}`` — one model body.

Only what the cache holds AND what matches its pin is listed or served: a
file the dashboard's cache refresh did not verify, an unpinned name, a
symlink or anything that is not an ``.onnx`` never leaves the box. The phone
checks every body against the signed list and against its own copy of the
pins before ONNX Runtime parses it. An empty list means the cache was never
filled: the dashboard's Settings → Satellites → prepare satellite media →
**Refresh caches** fills it (one download, needs the internet).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from domovoi.satellite_media import cache
from domovoi.satellite_media.fetchers import OWW_MODEL_FILES

#: The signed-manifest channel name (the envelope's ``channel``).
WAKE_BASE_CHANNEL = "satellite-wake-base"

#: Digest cache: (path, size, mtime_ns) -> sha256, so a phone's connect
#: does not re-hash ~10 MB of models each time.
_DIGESTS: dict[tuple[str, int, int], str] = {}


def _bucket() -> Path:
    # Not cache.bucket(): that creates the folder, and a read must not.
    return cache.CACHE_ROOT / "oww_models"


def _digest(p: Path) -> str:
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns)
    hit = _DIGESTS.get(key)
    if hit is not None:
        return hit
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    digest = h.hexdigest()
    _DIGESTS[key] = digest
    return digest


def served_models() -> dict[str, str]:
    """``{name: sha256}`` for every pinned ``.onnx`` model the cache holds
    with exactly its pinned size and digest. Sorted by name."""
    root = _bucket()
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for name in sorted(OWW_MODEL_FILES):
        if not name.endswith(".onnx"):
            continue
        sha256, size = OWW_MODEL_FILES[name]
        p = root / name
        try:
            if p.is_symlink() or not p.is_file() or p.stat().st_size != size:
                continue
            if _digest(p) != sha256:
                continue
        except OSError:
            continue
        out[name] = sha256
    return out


def model_path(name: str) -> Path | None:
    """The file for a served ``name``, or None (unknown, unverified, or not
    in the cache). Only a name the manifest lists resolves, so nothing
    outside the bucket can be named."""
    if name not in served_models():
        return None
    return _bucket() / name
