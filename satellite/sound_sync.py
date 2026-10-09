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


# Said once per process, not on every connect: an unpinned device syncs
# its clips on trust, as it always did, and the journal says so once.
_UNSIGNED_WARNED = False


def fetch_manifest(
    base: str,
    channel_path: str,
    channel: str,
    *,
    params: dict | None = None,
    timeout: float,
    expected_fingerprint: str | None,
    what: str,
    record: str | None = None,
) -> dict[str, str]:
    """A file channel's ``{rel: sha256}`` list — the signed envelope when
    this device knows which server to expect, the plain manifest when it
    does not. Shared by the sounds and wake-model channels, which fetch
    their lists the same way; the code and payload channels have their
    own, stricter wrappers.

    Pinned: ``manifest.sig`` is required, verified against the pin, and
    refused when older than the last list accepted on this channel (or,
    with ``record``, under that record — the sounds channel keeps one per
    voice); a server that serves none needs upgrading and is said so.
    Unpinned: the unsigned list, with one warning per process."""
    global _UNSIGNED_WARNED
    from satellite import server_identity

    if not expected_fingerprint:
        if not _UNSIGNED_WARNED:
            _UNSIGNED_WARNED = True
            log.warning(
                "%s: this device has no server fingerprint, so file lists "
                "are taken on trust (see satellite/PROVISIONING.md)", what,
            )
        r = requests.get(f"{base}{channel_path}/manifest", params=params, timeout=timeout)
        r.raise_for_status()
        manifest = r.json()
    else:
        r = requests.get(
            f"{base}{channel_path}/manifest.sig", params=params, timeout=timeout
        )
        if r.status_code == 404:
            raise RuntimeError(
                f"{what}: this device expects a signed manifest and the "
                "server serves none; upgrade the Domovoi server first"
            )
        r.raise_for_status()
        try:
            manifest = server_identity.accept_manifest_envelope(
                r.json(), channel=channel, expected_fingerprint=expected_fingerprint,
                record=record,
            )
        except server_identity.IdentityError as e:
            raise RuntimeError(f"{what}: {e}; nothing was written") from e
    if not isinstance(manifest, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in manifest.items()
    ):
        raise RuntimeError(f"{what}: the manifest is not a path -> sha256 map")
    return manifest


def sync(
    http_base: str,
    cache_dir: Path,
    voice: str | None = None,
    timeout: float = 5.0,
    expected_fingerprint: str | None = None,
) -> int:
    """Fetch the manifest, download missing/changed clips into ``cache_dir``,
    and prune cached MP3s no longer in the manifest. Returns the number of
    files downloaded. Raises on a network/HTTP error (the caller catches).

    ``voice`` scopes the sync to that voice's clip subtree on the
    server (``?voice=``); the manifest keys stay voice-relative, so
    the cache always holds *this* satellite's voice at canonical paths
    (greetings/…, network_issues.mp3). None → the server's default
    voice.

    ``expected_fingerprint`` is the server this device belongs to. With
    one, the list has to come signed by that server (``manifest.sig``),
    and every body has to hash to what the signed list says before it is
    written — these are the clips the room SAYS, and a host on the path
    does not get to choose them. Without one, the unsigned list, as
    before; the body check still runs against it."""
    base = http_base.rstrip("/")
    params = {"voice": voice} if voice else None
    from satellite import server_identity

    manifest = fetch_manifest(
        base, "/v1/sounds", "satellite-sounds", params=params, timeout=timeout,
        expected_fingerprint=expected_fingerprint, what="sound sync",
        # One record per voice, as the server keeps one serial per voice:
        # a device that switches voice and back must not judge one voice's
        # list against another's newer serial.
        record=server_identity.sounds_record(voice),
    )

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
        body = data.content
        if hashlib.sha256(body).hexdigest() != sha:
            log.warning(
                "sound sync: sha256 mismatch for %r; skipping (the body is "
                "not the one the manifest lists)", rel,
            )
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
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
