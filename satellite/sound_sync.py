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


#: Characters a manifest path never holds, on any board: a backslash (a
#: Windows separator), a colon (a drive letter, ``C:/…``, or an NTFS stream,
#: ``x.mp3:evil``) and the rest Windows refuses in a name. A list is checked
#: by the same rule on a Pi and on Windows, so a signed list that would only
#: be dangerous on one of them is refused on both.
_UNSAFE_CHARS = frozenset('\\:*?"<>|')
#: Windows device names: ``CON``, ``NUL.mp3``, ``COM1`` open a device, not a file.
_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                           *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))})
#: What each file channel may write (the core serves nothing else on them).
SOUND_TYPES = (".mp3", ".wav")
WAKE_MODEL_TYPES = (".onnx", ".onnx.json")


def _safe_rel(rel: str) -> bool:
    """Whether a manifest path is a plain relative path inside the cache,
    on every board: not empty, not absolute (``/x``, ``//server/share``,
    ``C:/x``), no ``..`` or ``.`` part, no backslash, colon, control
    character or other character Windows refuses, no part ending in a dot
    or a space (Windows drops them, so ``x.mp3.`` would be ``x.mp3``), and
    no Windows device name. Manifest paths are POSIX, so the string is
    checked directly rather than through OS-dependent Path semantics.
    Security review of wf/sat-windows (2026-10-13): the old check only knew
    POSIX, so a signed list naming ``C:/Users/<you>/…/Startup/x.cmd`` was
    written there on Windows. :func:`safe_dest` also resolves the result
    and keeps it inside the cache."""
    if not isinstance(rel, str) or not rel or len(rel) > 400 or rel.startswith("/"):
        return False
    if any(c in _UNSAFE_CHARS or ord(c) < 0x20 or ord(c) == 0x7F for c in rel):
        return False
    for part in rel.split("/"):
        if part in ("", ".", "..") or part.endswith((".", " ")) or part.startswith(" "):
            return False
        if part.split(".", 1)[0].upper() in _DEVICE_NAMES:
            return False
    return True


def safe_dest(cache_dir: Path, rel: str, types: tuple[str, ...] | None = None) -> Path | None:
    """Where a manifest entry may be written: ``cache_dir / rel`` when the
    path is safe (:func:`_safe_rel`), of one of ``types`` (lower-case
    suffixes; None: any), and still inside ``cache_dir`` once both are
    resolved (a link or junction inside the cache can't lead out of it).
    None otherwise."""
    if not _safe_rel(rel):
        return None
    if types is not None and not rel.lower().endswith(types):
        return None
    root = cache_dir.resolve()
    dest = (root / rel).resolve()
    if dest == root or not dest.is_relative_to(root):
        return None
    return dest


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
        dest = safe_dest(cache_dir, rel, SOUND_TYPES)
        if dest is None:
            log.warning("sound sync: skipping unsafe manifest path %r (not a sound file inside the cache)", rel)
            continue
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


#: What this satellite wrote into the cache, so the prune removes only that.
#: ``~/.domovoi/sounds`` is also where a Domovoi *server* keeps its voices
#: (``voices/<voice>/…``) — on a box that runs both, a prune of every MP3
#: not in this satellite's list deleted the server's (security review of
#: wf/sat-pc, R4). A spare computer also refuses to run on a server's
#: account at all; this keeps the prune honest everywhere.
RECORD_NAME = ".satellite-synced.json"


def _record_path(cache_dir: Path) -> Path:
    return cache_dir / RECORD_NAME


def _read_record(cache_dir: Path) -> set[str] | None:
    import json

    try:
        doc = json.loads(_record_path(cache_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {r for r in doc if isinstance(r, str) and _safe_rel(r)} if isinstance(doc, list) else None


def _legacy_shape(rel: str) -> bool:
    """Before the record existed: only the shapes the satellite's own
    manifest uses (a top-level clip, or greetings/<clip>), never a server's
    ``voices/…`` or ``setup/…`` tree."""
    parts = rel.split("/")
    return len(parts) == 1 or (len(parts) == 2 and parts[0] == "greetings")


def _prune(cache_dir: Path, keep: set[str]) -> None:
    """Delete cached MP3s not in the manifest (e.g. greetings removed in the
    web UI), so the Pi never plays a stale clip — only ones this satellite
    put there (the record of its last sync; without one, only its own
    manifest's shapes) — then record what it keeps."""
    import json

    previous = _read_record(cache_dir)
    root = cache_dir.resolve()
    for p in cache_dir.rglob("*.mp3"):
        if p.is_symlink():
            continue
        rel = p.relative_to(cache_dir).as_posix()
        # Only a file really inside the cache (a junction is not a symlink
        # to Path, and rglob walks into one).
        if safe_dest(cache_dir, rel, SOUND_TYPES) is None or not p.resolve().is_relative_to(root):
            continue
        if rel in keep:
            continue
        ours = rel in previous if previous is not None else _legacy_shape(rel)
        if ours:
            try:
                p.unlink()
            except OSError:
                pass
    try:
        tmp = _record_path(cache_dir).with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(keep)), encoding="utf-8")
        tmp.replace(_record_path(cache_dir))
    except OSError:
        pass
