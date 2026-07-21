"""Sync rendered sound clips from the Domovoi server into a local cache.

The server renders greeting / canned audio under its ``satellite/
sounds/`` tree and exposes a manifest + the files over HTTP
(``/v1/sounds/manifest`` and ``/v1/sounds/{path}``). The satellite mirrors
them into ``~/.domovoi/sounds/`` by hash, so editing greetings (web UI) +
a server re-render reaches the Pi with no manual rsync.

Best-effort by design: the client calls ``sync`` inside a try/except and
falls back to whatever's already in the cache (or the clips bundled in the
``satellite/`` tree) on any failure — a satellite always greets, even
offline. ``requests`` is already a satellite dependency.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import requests

log = logging.getLogger("satellite.sound_sync")


def http_base_from_ws(ws_url: str) -> str:
    """``ws://host:port[/path]`` → ``http://host:port`` — swap the scheme,
    drop any path so we hit the server's HTTP root."""
    base = ws_url.strip()
    scheme = "http"
    if base.startswith("wss://"):
        scheme, base = "https", base[len("wss://"):]
    elif base.startswith("ws://"):
        scheme, base = "http", base[len("ws://"):]
    elif "://" in base:
        scheme, base = base.split("://", 1)
    host = base.split("/", 1)[0]
    return f"{scheme}://{host}"


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _safe_rel(rel: str) -> bool:
    """Reject manifest paths that would escape the cache dir — absolute,
    parent-refs, or empty. Manifest paths are posix, so check the string
    directly rather than via os-dependent Path semantics."""
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    return all(part and part != ".." for part in rel.split("/"))


def sync(
    http_base: str,
    cache_dir: Path,
    voice: str | None = None,
    timeout: float = 5.0,
) -> int:
    """Fetch the manifest, download missing/changed clips into ``cache_dir``,
    and prune cached MP3s no longer in the manifest. Returns the number of
    files downloaded. Raises on a network/HTTP error (the caller catches).

    ``voice`` scopes the sync to that voice's clip subtree on the
    server (``?voice=``); the manifest keys stay voice-relative, so
    the cache always holds *this* satellite's voice at canonical paths
    (greetings/…, network_issues.mp3). None → the server's default
    voice."""
    base = http_base.rstrip("/")
    params = {"voice": voice} if voice else None
    r = requests.get(f"{base}/v1/sounds/manifest", params=params, timeout=timeout)
    r.raise_for_status()
    manifest: dict[str, str] = r.json()

    cache_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for rel, sha in manifest.items():
        if not _safe_rel(rel):
            log.warning("sound sync: skipping unsafe manifest path %r", rel)
            continue
        dest = cache_dir / rel
        if dest.is_file() and _sha256(dest) == sha:
            continue
        data = requests.get(f"{base}/v1/sounds/{rel}", params=params, timeout=timeout)
        data.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data.content)
        downloaded += 1

    _prune(cache_dir, set(manifest.keys()))
    return downloaded


def _prune(cache_dir: Path, keep: set[str]) -> None:
    """Delete cached MP3s not in the manifest (e.g. greetings removed in the
    web UI), so the Pi never plays a stale clip."""
    for p in cache_dir.rglob("*.mp3"):
        rel = p.relative_to(cache_dir).as_posix()
        if rel not in keep:
            try:
                p.unlink()
            except OSError:
                pass
