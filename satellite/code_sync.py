"""Sync the satellite's own code tree from the Domovoi server + tarball backup.

The companion to ``sound_sync``: where that mirrors rendered audio clips,
this mirrors the ``satellite/`` source tree itself so a self-upgrade can be
pushed from the dashboard with no manual scp. The server exposes a
manifest + the file bodies over HTTP (``/v1/satellite-code/manifest`` and
``/v1/satellite-code/{path}``); the satellite verifies each downloaded body
against the manifest sha256 before writing it.

Integrity is the *manifest* sha256, never a git SHA — the server's
working tree may be dirty, so the git short SHA is only a version *label*, never
used to verify file bytes. A mismatch aborts the whole sync (the half-written
tree is recoverable from the pre-write tarball backup, see ``backup_tree`` /
``restore_tree``).

Prune is deliberately conservative: only files that were in the PREVIOUS
synced manifest but are absent from the new one are removed, so a stray local
file (or anything under ``~/.domovoi/``) is never touched. The tree is never
globbed for prune candidates.

UNTESTED on the dev host — exercised only on a real Pi during an upgrade.
``requests`` is already a satellite dependency.
"""

from __future__ import annotations

import hashlib
import logging
import tarfile
from pathlib import Path

import requests

from satellite.sound_sync import _safe_rel, _sha256, http_base_from_ws

log = logging.getLogger("satellite.code_sync")

# Re-export so callers can import the shared helpers from here too.
__all__ = [
    "_safe_rel",
    "_sha256",
    "http_base_from_ws",
    "fetch_manifest",
    "sync_code",
    "backup_tree",
    "restore_tree",
]


def fetch_manifest(
    base: str,
    timeout: float = 10.0,
    expected_fingerprint: str | None = None,
) -> dict[str, str]:
    """The code channel's file list, authenticated when this device knows
    who to expect.

    With a fingerprint pinned we ask for ``manifest.sig`` — an envelope
    carrying the list and the server's signature over it — and refuse
    anything that does not verify, BEFORE a single body is downloaded.
    Checking the list rather than each file is what makes this worth
    doing: the per-file sha256 below only ever proved that the bodies
    matched the list the same host handed us.

    With nothing pinned (an image prepared before server identities) we
    fetch the plain manifest exactly as before and say so once, at
    warning level, so an unverified upgrade is at least visible in the
    journal.
    """
    from satellite import server_identity

    if not expected_fingerprint:
        log.warning(
            "code sync: this device has no server fingerprint, so the code "
            "manifest is taken on trust (see satellite/PROVISIONING.md)"
        )
        r = requests.get(f"{base}/v1/satellite-code/manifest", timeout=timeout)
        r.raise_for_status()
        manifest = r.json()
    else:
        r = requests.get(f"{base}/v1/satellite-code/manifest.sig", timeout=timeout)
        if r.status_code == 404:
            raise RuntimeError(
                "code sync: this device expects a signed manifest and the "
                "server serves none; upgrade the Domovoi server first"
            )
        r.raise_for_status()
        try:
            manifest = server_identity.verify_manifest_envelope(
                r.json(),
                channel=server_identity.CODE_CHANNEL,
                expected_fingerprint=expected_fingerprint,
            )
        except server_identity.IdentityError as e:
            raise RuntimeError(f"code sync: {e}; nothing was written") from e
    if not isinstance(manifest, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in manifest.items()
    ):
        raise RuntimeError("code sync: the manifest is not a path -> sha256 map")
    return manifest


def sync_code(
    http_base: str,
    satellite_root: Path,
    ext_allow: frozenset[str],
    prev_manifest: dict[str, str],
    timeout: float = 10.0,
    expected_fingerprint: str | None = None,
) -> dict:
    """Fetch the code manifest, download missing/changed files into
    ``satellite_root`` (verifying each body's sha256 against the manifest),
    and prune files that were in ``prev_manifest`` but are gone from the new
    one. Returns ``{"manifest", "downloaded", "pruned"}``.

    Raises on a network/HTTP error, on a manifest this device's server did
    not sign (``expected_fingerprint``), OR on a sha256 mismatch — any of
    them aborts the whole sync so a corrupt/tampered body never lands on
    disk (the caller restores from the pre-write tarball backup).

    ``ext_allow`` is the allowlist of file extensions the code channel
    carries; a manifest entry with any other suffix is skipped with a
    warning (defence-in-depth — the server already filters)."""
    base = http_base.rstrip("/")
    manifest: dict[str, str] = fetch_manifest(base, timeout, expected_fingerprint)

    satellite_root.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for rel, sha in manifest.items():
        if not _safe_rel(rel):
            log.warning("code sync: skipping unsafe manifest path %r", rel)
            continue
        if Path(rel).suffix not in ext_allow:
            log.warning("code sync: skipping non-allowlisted path %r", rel)
            continue
        dest = satellite_root / rel
        if dest.is_file() and _sha256(dest) == sha:
            continue
        data = requests.get(f"{base}/v1/satellite-code/{rel}", timeout=timeout)
        data.raise_for_status()
        body = data.content
        actual = hashlib.sha256(body).hexdigest()
        if actual != sha:
            raise RuntimeError(
                f"code sync: sha256 mismatch for {rel!r} "
                f"(expected {sha}, got {actual}); aborting upgrade"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        downloaded += 1

    pruned = _prune_within_previous(satellite_root, manifest, prev_manifest, ext_allow)
    return {"manifest": manifest, "downloaded": downloaded, "pruned": pruned}


def _prune_within_previous(
    satellite_root: Path,
    manifest: dict[str, str],
    prev_manifest: dict[str, str],
    ext_allow: frozenset[str],
) -> list[str]:
    """Delete files present in the PREVIOUS synced manifest but absent from
    the new one — and only those. We never glob the tree; the candidate set
    is exactly ``set(prev) - set(new)``, so a local file the server
    has never owned (or anything outside the synced set) survives untouched.
    Each candidate is re-checked through the same ``_safe_rel`` + allowlist
    guards before unlink. Returns the list of pruned posix rel paths."""
    pruned: list[str] = []
    for rel in set(prev_manifest) - set(manifest):
        if not _safe_rel(rel) or Path(rel).suffix not in ext_allow:
            continue
        try:
            (satellite_root / rel).unlink(missing_ok=True)
            pruned.append(rel)
        except OSError:
            pass
    return pruned


def backup_tree(satellite_root: Path, backup_path: Path) -> None:
    """Tar the whole ``satellite_root`` tree to ``backup_path`` before an
    upgrade writes over it, so a botched sync can be rolled back by simply
    extracting this tarball. ``__pycache__`` is excluded (regenerated on
    import; only bloats the archive). The arcname is the dir's own name so
    ``restore_tree`` can extract over the parent and land it back in place."""
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if "__pycache__" in Path(info.name).parts:
            return None
        return info

    with tarfile.open(backup_path, "w") as tar:
        tar.add(satellite_root, arcname=satellite_root.name, filter=_filter)


def restore_tree(backup_path: Path, satellite_root: Path) -> None:
    """Extract the backup tarball back over the parent of ``satellite_root``,
    landing the saved tree in place — the rollback half of ``backup_tree``.

    ``filter="fully_trusted"`` preserves the exact archive contents (perms /
    links) — the tarball is one we created locally from our own tree, so the
    restrictive 'data' filter isn't needed; passing an explicit filter pins
    behavior across Python 3.12→3.14 (where the default flips) and silences the
    extraction-filter deprecation warning."""
    with tarfile.open(backup_path, "r") as tar:
        tar.extractall(satellite_root.parent, filter="fully_trusted")
