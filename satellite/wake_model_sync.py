"""Sync trained wake-word models from the Domovoi server into a local cache.

Feature #5 — wake-word training. Positive clips are recorded ON a satellite
(server-initiated, see `client._wake_recording_loop`), the server
trains an openWakeWord ``<slug>.onnx``, and the dashboard "pushes"
it to a room. The push is two frames: a ``set_wake_word`` control frame (the
slug the Pi should switch to) and a ``wake_models_changed`` frame that tells
the Pi to re-mirror the model cache. This module is the mirror step.

The server exposes a manifest + the model bodies over HTTP
(``/v1/wake-models/manifest`` and ``/v1/wake-models/{path}``), exactly like
the sounds channel. The satellite mirrors them into
``~/.domovoi/wake_models/`` by hash, so a model trained + pushed from the web
UI reaches the Pi with no manual scp.

A custom openWakeWord model is a single ``<slug>.onnx`` (with an optional
``<slug>.onnx.json`` companion). The openWakeWord prediction dict keys off
the model file's STEM, which the Pi's effective wake word (the sidecar slug,
see ``client.WAKE_SIDECAR``) must equal — so the served filename, the sidecar
value, and the loaded model stem all derive from the same ``voice_slug(name)``
on the server. See the slug invariant in the server-side handlers.

Best-effort by design: the client calls ``sync`` inside a try/except and
falls back to whatever's already in the cache on any failure. ``requests`` is
already a satellite dependency. Reuses ``_safe_rel`` / ``_sha256`` /
``http_base_from_ws`` from ``sound_sync`` so the path-traversal guard and the
scheme-swap stay defined once.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import requests

from satellite.sound_sync import _safe_rel, _sha256, http_base_from_ws  # noqa: F401

log = logging.getLogger("satellite.wake_model_sync")


def sync(http_base: str, cache_dir: Path, timeout: float = 10.0) -> int:
    """Fetch the wake-model manifest, download missing/changed ``.onnx``
    (and optional ``.onnx.json``) files into ``cache_dir``, verifying each
    body's sha256 against the manifest. Returns the number of files
    downloaded. Raises on a network/HTTP error (the caller catches).

    Prune is deliberately skipped here: ``sound_sync._prune`` is hard-wired
    to ``*.mp3`` and there is no previous-manifest tracking in this channel,
    so a stale ``<slug>.onnx`` left in the cache is harmless — it is only
    ever loaded when the sidecar names that exact slug, and a removed model
    flips the sidecar/effective wake word elsewhere. Keeping prune out avoids
    ever deleting a model the Pi is actively running mid-switch.
    """
    base = http_base.rstrip("/")
    r = requests.get(f"{base}/v1/wake-models/manifest", timeout=timeout)
    r.raise_for_status()
    manifest: dict[str, str] = r.json()

    cache_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for rel, sha in manifest.items():
        if not _safe_rel(rel):
            log.warning("wake-model sync: skipping unsafe manifest path %r", rel)
            continue
        dest = cache_dir / rel
        if dest.is_file() and _sha256(dest) == sha:
            continue
        data = requests.get(f"{base}/v1/wake-models/{rel}", timeout=timeout)
        data.raise_for_status()
        # Verify the downloaded body matches the manifest hash before it
        # lands in the cache — a corrupt/half model would load fine but
        # never fire, the silent-wake failure mode we most want to avoid.
        body = data.content
        if hashlib.sha256(body).hexdigest() != sha:
            log.warning(
                "wake-model sync: sha256 mismatch for %r; skipping (server "
                "manifest and body disagree)", rel,
            )
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        downloaded += 1
    return downloaded
