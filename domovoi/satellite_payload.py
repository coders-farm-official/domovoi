"""Plugin satellite-payload enumeration — shared by the
``/v1/satellite-plugins`` channel (main.py) and the media-prep builder.

An installed, ENABLED plugin may declare a ``[satellite]`` section
(``SatelliteDecl`` in plugins_runtime/manifest.py): files to mirror onto
every satellite, apt packages, pinned pip requirements, and a root
post-install script. This module resolves those declarations against the
plugins registry (the ``plugins`` table's ``manifest`` JSONB + install
dirs) into a served file set.

Guarding is root-confinement + no-symlinks + the per-plugin size cap — NOT
an extension allowlist: payloads legitimately carry binaries (.dtbo
overlays, ELF tools). Disabling a plugin removes its subtree from the
manifest on the next request; the device's conservative prune then removes
exactly those files.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi.db.session import session_scope

log = logging.getLogger(__name__)


def _decl_from_manifest(raw: Any) -> dict[str, Any] | None:
    """The [satellite] table out of a plugins-registry manifest JSONB.
    Defensive: install-time validation is authoritative; anything malformed
    here is skipped, never raised."""
    if not isinstance(raw, dict):
        return None
    sat = raw.get("satellite")
    if not isinstance(sat, dict):
        return None
    return {
        "apt_packages": [
            p for p in sat.get("apt_packages", []) if isinstance(p, str)
        ],
        "pip_requirements": [
            p for p in sat.get("pip_requirements", []) if isinstance(p, str)
        ],
        "pip_lockfile": sat.get("pip_lockfile") or None,
        "files_dir": sat.get("files_dir") or None,
        "post_install": sat.get("post_install") or None,
        "max_payload_mb": (
            sat["max_payload_mb"]
            if isinstance(sat.get("max_payload_mb"), int)
            else 64
        ),
    }


async def enabled_satellite_plugins() -> list[dict[str, Any]]:
    """Enabled plugins that declare a [satellite] section, with their
    resolved install roots: ``[{slug, version, root, decl}]``."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT slug, version, install_dir, manifest FROM plugins "
                    "WHERE enabled ORDER BY slug"
                )
            )
        ).all()
    out: list[dict[str, Any]] = []
    for slug, version, install_dir, manifest in rows:
        decl = _decl_from_manifest(manifest)
        if decl is None or not install_dir:
            continue
        root = Path(install_dir)
        if not root.is_dir():
            log.warning(
                "satellite payload: %s install dir %s missing — skipping",
                slug, install_dir,
            )
            continue
        out.append({"slug": slug, "version": version, "root": root, "decl": decl})
    return out


def payload_files(root: Path, decl: dict[str, Any]) -> dict[str, Path]:
    """``{relpath_within_slug: absolute_path}`` for one plugin's payload:
    the files_dir tree (mirrored at the slug root), plus the post_install
    script and pip lockfile under their manifest-declared names. Symlinks
    skipped; everything root-confined; the size cap enforced (an over-cap
    payload serves NOTHING rather than a truncated tree)."""
    resolved_root = root.resolve()
    files: dict[str, Path] = {}
    total = 0

    def _confined(p: Path) -> bool:
        try:
            p.resolve().relative_to(resolved_root)
            return True
        except (ValueError, OSError):
            return False

    if decl.get("files_dir"):
        fd = root / decl["files_dir"]
        if fd.is_dir() and _confined(fd):
            for f in sorted(fd.rglob("*")):
                if f.is_symlink() or not f.is_file() or not _confined(f):
                    continue
                rel = f.relative_to(fd).as_posix()
                files[rel] = f
                total += f.stat().st_size
    for key in ("post_install", "pip_lockfile"):
        relname = decl.get(key)
        if relname:
            p = root / relname
            if p.is_file() and not p.is_symlink() and _confined(p):
                files[Path(relname).as_posix()] = p
                total += p.stat().st_size

    cap = decl.get("max_payload_mb", 64) * 1024 * 1024
    if total > cap:
        log.warning(
            "satellite payload: %s exceeds its %d MiB cap — serving nothing",
            root, decl.get("max_payload_mb", 64),
        )
        return {}
    return files


async def build_channel_manifest() -> dict[str, Any]:
    """The ``/v1/satellite-plugins/manifest`` document:
    ``{"files": {"<slug>/<rel>": sha256}, "meta": {slug: {...}}}``."""
    files: dict[str, str] = {}
    meta: dict[str, Any] = {}
    for entry in await enabled_satellite_plugins():
        slug, decl = entry["slug"], entry["decl"]
        plugin_files = payload_files(entry["root"], decl)
        for rel, path in plugin_files.items():
            try:
                files[f"{slug}/{rel}"] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            except OSError as e:
                log.warning("satellite payload: unreadable %s: %s", path, e)
        meta[slug] = {
            "version": entry["version"],
            "apt_packages": decl["apt_packages"],
            "pip_requirements": decl["pip_requirements"],
            "pip_lockfile": decl["pip_lockfile"],
            "post_install": decl["post_install"],
        }
    return {"files": files, "meta": meta}


async def resolve_channel_file(path: str) -> Path | None:
    """Resolve a ``<slug>/<rel>`` channel path to a servable file, or None.
    Only enabled plugins' current payload sets resolve — the same
    enumeration the manifest uses, so there is no side door."""
    slug, _, rel = path.partition("/")
    if not slug or not rel:
        return None
    for entry in await enabled_satellite_plugins():
        if entry["slug"] != slug:
            continue
        return payload_files(entry["root"], entry["decl"]).get(rel)
    return None
