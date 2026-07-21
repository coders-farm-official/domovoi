"""Containment + media-library registry core for the Files tab (design §1–§4).

The Files surface exposes multiple browsable roots — core media dirs,
enabled-plugin media libraries, and present removable drives — through one
generic browse/download/upload/delete/import API (``files.py``). This module
is the **security heart**: every path the API touches passes through
:func:`safe_join`, and every candidate root through :func:`validate_root`, so
the client can only ever name a ``library_id`` plus a **relative** path — the
absolute ``root_path`` is held server-side and never serialized.

Containment guarantees (adversarially tested in ``test_files_security.py``):

* ``safe_join`` rejects ``..``, drive-absolute (``C:/…``), and UNC (``//host``)
  paths, and — via ``resolve()`` + ``relative_to`` against an already-resolved
  root — a symlink *inside* a library that points outside it.
* ``validate_root`` refuses filesystem/drive roots and anything sensitive.
* ``_is_sensitive`` keeps ``~/.domovoi`` secrets (``setup-code.txt``, ``tls/``,
  ``pairing_token``, plugin ``.env`` files) off the surface — only the
  explicitly-allowed media subdirs under the config dir register.
* The listing/serve/copy/delete paths additionally drop any entry whose
  realpath escapes the root, and filter secret-shaped names
  (:data:`DENY_NAMES` / :data:`DENY_SUFFIXES`).

The web process resolves every root without importing plugin code: core dirs
from ``core_settings``, plugin roots from the ``plugins.manifest`` JSONB
(``fetch_plugin_rows``), removable mounts from ``psutil`` (+ ctypes / procfs
fallbacks). ``resolve_plugin_root`` maps a fixed base-token vocabulary
(``install_dir | data_dir | music_dir | absolute | config``) to absolute paths;
the ``config`` base reads a single declared key from
``~/.domovoi/plugins/<slug>.env``.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from domovoi.config import settings as core_settings

log = logging.getLogger(__name__)

# ─── Config-dir secret guard ─────────────────────────────────────────────────
# Domovoi's secrets live under CONFIG_DIR (~/.domovoi): setup-code.txt, tls/,
# pairing_token, plugin .env files, plugins/data internals. Module-level so
# tests can point it at a tmp dir (mirrors admin_auth.CONFIG_DIR).
CONFIG_DIR = Path.home() / ".domovoi"

# Entry-level denylist — belt-and-suspenders on top of per-root containment.
# Even inside an allowed root, a listing/serve never surfaces these.
DENY_NAMES: frozenset[str] = frozenset(
    {"setup-code.txt", "pairing_token", ".env", "tls", ".domovoi"}
)
DENY_SUFFIXES: frozenset[str] = frozenset(
    {".env", ".key", ".pem", ".crt", ".p12", ".pfx"}
)


def is_sensitive_name(name: str) -> bool:
    """True for a secret-shaped entry name (denylisted at listing/serve)."""
    lower = name.lower()
    if name in DENY_NAMES or lower in DENY_NAMES:
        return True
    return Path(name).suffix.lower() in DENY_SUFFIXES


# ─── Containment ─────────────────────────────────────────────────────────────
def safe_join(root: Path, rel: str | None) -> Path:
    """Resolve ``rel`` strictly inside ``root``, or raise ``400``.

    ``root`` MUST already be an absolute, ``resolve()``-d registered root (that
    is what :func:`validate_root` / the registry builder guarantee). Airtight
    against ``..``, drive-absolute / UNC paths, and symlink escapes:
    ``resolve(strict=False)`` canonicalizes symlinks in the existing prefix, so
    a symlink inside ``root`` that points outside resolves outside and fails the
    ``relative_to`` check. An empty ``rel`` is the library root itself.
    """
    if rel is None or not rel.strip():
        return root
    norm = rel.replace("\\", "/").strip()
    # Reject Windows drive-absolute ("C:/…") and UNC ("//host/share") BEFORE
    # stripping leading slashes — joining an absolute onto root would otherwise
    # silently discard root. A single leading slash is a harmless "rooted-rel"
    # form (stripped below, stays contained), matching documents.py.
    if re.match(r"^[A-Za-z]:", norm) or norm.startswith("//"):
        raise HTTPException(status_code=400, detail="absolute path rejected")
    cleaned = norm.lstrip("/")
    # Defense in depth — the realpath check below is the true guard.
    if any(seg == ".." for seg in cleaned.split("/")):
        raise HTTPException(status_code=400, detail="'..' rejected")
    target = (root / cleaned).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes library root")
    return target


def _is_sensitive(p: Path, allowed_under_config: set[Path]) -> bool:
    """True when ``p`` is (or sits inside) the config dir but is NOT one of the
    explicitly-allowed media subdirs. Keeps ``~/.domovoi`` secrets off the
    Files surface even though ``audiobooks_dir`` / ``podcasts_dir`` and plugin
    ``data_dir`` roots legitimately live under it."""
    pr = p.expanduser().resolve(strict=False)
    cd = CONFIG_DIR.expanduser().resolve(strict=False)
    try:
        pr.relative_to(cd)
    except ValueError:
        return False  # not under ~/.domovoi → fine
    for allowed in allowed_under_config:
        ar = allowed.expanduser().resolve(strict=False)
        if pr == ar:
            return False
        try:
            pr.relative_to(ar)
            return False
        except ValueError:
            continue
    return True


def validate_root(root: Path, allowed_under_config: set[Path] | None = None) -> bool:
    """A candidate browsable root is valid iff it exists, is a directory, is
    not a filesystem/drive root, and is not sensitive (config-dir guard)."""
    r = root.expanduser().resolve(strict=False)
    try:
        if not r.exists() or not r.is_dir():
            return False
    except OSError:
        return False
    if r == Path(r.anchor) or len(r.parts) <= 1:
        return False  # never a whole filesystem / drive root
    if _is_sensitive(r, allowed_under_config or set()):
        return False
    return True


# ─── Removable drives ────────────────────────────────────────────────────────
def drive_token(mount: str) -> str:
    """Stable per-session id for a removable mount. ``E:\\`` → ``E`` (Windows);
    ``/media/user/sdb1`` → ``sdb1`` (Linux). Re-inserting the same drive reuses
    the id."""
    m = (mount or "").replace("\\", "/").rstrip("/")
    win = re.match(r"^([A-Za-z]):", m)
    if win:
        return win.group(1).upper()
    seg = m.rsplit("/", 1)[-1]
    return seg or "root"


def _removable_via_psutil() -> list[dict[str, Any]]:
    """psutil primary (Windows-first, works cross-platform where opts are
    populated). A partition is removable when its opts advertise ``removable``
    or ``cdrom``."""
    import psutil

    out: list[dict[str, Any]] = []
    for part in psutil.disk_partitions(all=False):
        opts = (part.opts or "").lower()
        if "removable" in opts or "cdrom" in opts:
            out.append(
                {
                    "mount": part.mountpoint,
                    "device": part.device,
                    "read_only": ("cdrom" in opts or "ro" in opts),
                }
            )
    return out


def _removable_windows_ctypes() -> list[dict[str, Any]]:
    """Windows fallback when psutil opts are unreliable. Enumerate logical
    drives via GetLogicalDrives(), keep DRIVE_REMOVABLE (2) / DRIVE_CDROM (5)."""
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    bitmask = kernel32.GetLogicalDrives()
    out: list[dict[str, Any]] = []
    for i in range(26):
        if not (bitmask >> i) & 1:
            continue
        letter = chr(ord("A") + i)
        root = f"{letter}:\\"
        dtype = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        if dtype in (2, 5):  # DRIVE_REMOVABLE / DRIVE_CDROM
            out.append(
                {"mount": root, "device": root, "read_only": dtype == 5}
            )
    return out


def _removable_linux() -> list[dict[str, Any]]:
    """Linux: parse ``/proc/mounts``; keep a device ``/dev/sdX#`` when its base
    block device is flagged removable in ``/sys/block/<base>/removable``."""
    out: list[dict[str, Any]] = []
    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        device, mount, _fstype, opts = parts[0], parts[1], parts[2], parts[3]
        if not device.startswith("/dev/"):
            continue
        dev_name = device.rsplit("/", 1)[-1]  # e.g. sdb1
        base = re.sub(r"\d+$", "", dev_name)  # sdb1 → sdb
        removable_flag = Path(f"/sys/block/{base}/removable")
        try:
            if removable_flag.read_text(encoding="utf-8").strip() != "1":
                continue
        except OSError:
            continue
        # /proc/mounts uses octal-escaped spaces (\040); decode the common ones.
        mount = mount.replace("\\040", " ")
        out.append(
            {
                "mount": mount,
                "device": device,
                "read_only": "ro" in opts.split(","),
            }
        )
    return out


def detect_removable() -> list[dict[str, Any]]:
    """Enumerate present removable mounts, on demand. Each probe is guarded so
    a failure degrades to "no removable libraries", never a 500. Tests
    monkeypatch this to inject a stub drive."""
    try:
        if sys.platform.startswith("win"):
            try:
                found = _removable_via_psutil()
            except Exception as e:  # noqa: BLE001
                log.debug("psutil removable probe failed: %s", e)
                found = []
            if not found:
                try:
                    found = _removable_windows_ctypes()
                except Exception as e:  # noqa: BLE001
                    log.debug("ctypes removable probe failed: %s", e)
                    found = []
            return found
        if sys.platform.startswith("linux"):
            return _removable_linux()
        return _removable_via_psutil()
    except Exception as e:  # noqa: BLE001 — never let detection 500 the registry
        log.warning("removable detection failed: %s", e)
        return []


# ─── Plugin config (.env) single-key reader ──────────────────────────────────
def _plugin_env_path(slug: str) -> Path:
    return CONFIG_DIR / "plugins" / f"{slug}.env"


def _read_plugin_env(slug: str) -> dict[str, str]:
    """Parse ``~/.domovoi/plugins/<slug>.env`` into an UPPER-cased key→value
    dict. Only ever consulted to resolve a plugin's declared media-path key —
    the file itself is never exposed as a browsable root."""
    path = _plugin_env_path(slug)
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            out[key.upper()] = val
    return out


def _config_values(slug: str, key: str, env_prefix: str, separator: str | None) -> list[str]:
    """The value(s) of a plugin config key, split on ``separator`` when given.
    Tries the bare declared key and the ``<ENV_PREFIX>KEY`` form so a decl of
    ``path = "library_dir"`` matches an env of ``ROMM_LIBRARY_DIR``."""
    env = _read_plugin_env(slug)
    candidates = [key.upper(), f"{env_prefix}{key}".upper()]
    raw = ""
    for cand in candidates:
        if env.get(cand):
            raw = env[cand]
            break
    if not raw:
        return []
    if separator:
        return [p.strip() for p in raw.split(separator) if p.strip()]
    return [raw.strip()] if raw.strip() else []


# base vocabulary the resolver understands (mirrors manifest MEDIA_LIBRARY_BASES).
_KNOWN_BASES = frozenset(
    {"install_dir", "data_dir", "music_dir", "absolute", "config"}
)


def resolve_plugin_root(
    decl: dict[str, Any],
    *,
    install_dir: str | None,
    slug: str,
    env_prefix: str | None = None,
    allowed_under_config: set[Path] | None = None,
) -> list[Path]:
    """Map a ``[[media_libraries]]`` decl to zero-or-more validated absolute
    roots without importing plugin code. Every candidate passes
    :func:`validate_root`; unresolvable / denylisted candidates are dropped
    (silently skipped by the builder). A ``config`` base with a ``separator``
    can yield multiple sibling roots (each host folder a separate library)."""
    base = str(decl.get("base") or "absolute")
    path = str(decl.get("path") or "")
    if base not in _KNOWN_BASES:
        return []
    if env_prefix is None:
        env_prefix = f"{slug.upper()}_"
    allowed = allowed_under_config or set()

    raw_candidates: list[Path] = []
    if base == "install_dir":
        if not install_dir:
            return []
        raw_candidates = [Path(install_dir) / path]
    elif base == "data_dir":
        raw_candidates = [CONFIG_DIR / "plugins" / "data" / slug / path]
    elif base == "music_dir":
        raw_candidates = [Path(core_settings.music_dir).expanduser() / path]
    elif base == "absolute":
        if not path:
            return []
        raw_candidates = [Path(path).expanduser()]
    elif base == "config":
        sep = decl.get("separator")
        sep = sep if isinstance(sep, str) and sep else None
        raw_candidates = [
            Path(v).expanduser()
            for v in _config_values(slug, path, env_prefix, sep)
        ]

    resolved: list[Path] = []
    seen: set[Path] = set()
    for cand in raw_candidates:
        r = cand.resolve(strict=False)
        if r in seen:
            continue
        if validate_root(r, allowed):
            resolved.append(r)
            seen.add(r)
    return resolved


# ─── The unified media-library record ────────────────────────────────────────
@dataclass(frozen=True)
class MediaLibrary:
    """One browsable root. ``root_path`` is server-side ONLY — :meth:`public`
    strips it before the record ever reaches a client."""

    id: str
    label: str
    kind: str  # "core" | "plugin" | "removable"
    icon: str
    kind_icon: str
    owner: str | None
    root_path: Path
    editable: bool
    importable: bool
    doc_editing: bool
    reindex_kind: str | None
    present: bool = True
    read_only_note: bool = False  # reserved; plugin RO libs set editable=False

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "icon": self.icon,
            "kind_icon": self.kind_icon,
            "owner": self.owner,
            "importable": self.importable,
            "editable": self.editable,
            "doc_editing": self.doc_editing,
            "reindex_kind": self.reindex_kind,
            "present": self.present,
        }


# Core libraries, resolved from settings. (id, label, icon, settings-attr,
# editable, importable, reindex_kind, doc_editing). cover_art_dir is
# deliberately absent — it is an internal cache, not a Files library.
CORE_LIBRARIES: tuple[tuple[str, str, str, str, bool, bool, str, bool], ...] = (
    ("core:music", "Music", "music", "music_dir", True, True, "music", False),
    ("core:audiobooks", "Audiobooks", "book-open", "audiobooks_dir", True, True, "audiobooks", False),
    ("core:podcasts", "Podcasts", "podcast", "podcasts_dir", True, True, "podcasts", False),
    ("core:documents", "Documents", "file-text", "documents_dir", True, True, "documents", True),
)

# reindex_kind values that trigger a post-write reindex.
INDEXED_KINDS: frozenset[str] = frozenset({"music", "audiobooks", "podcasts"})


def _allowed_under_config() -> set[Path]:
    """Media subdirs that legitimately live under CONFIG_DIR and so must NOT be
    treated as sensitive: audiobooks_dir, podcasts_dir, and the plugin data
    sandbox (``~/.domovoi/plugins/data``)."""
    allowed: set[Path] = set()
    for attr in ("audiobooks_dir", "podcasts_dir"):
        try:
            allowed.add(Path(getattr(core_settings, attr)).expanduser().resolve(strict=False))
        except Exception:  # noqa: BLE001
            continue
    allowed.add((CONFIG_DIR / "plugins" / "data").expanduser().resolve(strict=False))
    return allowed


def build_core_libraries(allowed_under_config: set[Path]) -> list[MediaLibrary]:
    libs: list[MediaLibrary] = []
    for (lib_id, label, icon, attr, editable, importable, reindex, doc_editing) in CORE_LIBRARIES:
        try:
            root = Path(getattr(core_settings, attr)).expanduser().resolve(strict=False)
        except Exception:  # noqa: BLE001
            continue
        if not validate_root(root, allowed_under_config):
            continue
        libs.append(
            MediaLibrary(
                id=lib_id,
                label=label,
                kind="core",
                icon=icon,
                kind_icon="folder",
                owner=None,
                root_path=root,
                editable=editable,
                importable=importable,
                doc_editing=doc_editing,
                reindex_kind=reindex,
            )
        )
    return libs


def build_plugin_libraries(
    rows: list[dict[str, Any]], allowed_under_config: set[Path]
) -> list[MediaLibrary]:
    libs: list[MediaLibrary] = []
    for row in rows:
        if not row.get("enabled") or row.get("status") not in ("ok", "degraded"):
            continue
        manifest = row.get("manifest") or {}
        env_prefix = (manifest.get("config") or {}).get("env_prefix") or f"{row['slug'].upper()}_"
        for decl in manifest.get("media_libraries") or []:
            if not isinstance(decl, dict) or not decl.get("id"):
                continue
            roots = resolve_plugin_root(
                decl,
                install_dir=row.get("install_dir"),
                slug=row["slug"],
                env_prefix=env_prefix,
                allowed_under_config=allowed_under_config,
            )
            read_only = bool(decl.get("read_only", True))
            for i, root in enumerate(roots):
                suffix = f":{i}" if len(roots) > 1 else ""
                libs.append(
                    MediaLibrary(
                        id=f"plugin:{row['slug']}:{decl['id']}{suffix}",
                        label=f"{row.get('name', row['slug'])} · {decl.get('label', decl['id'])}",
                        kind="plugin",
                        icon=str(decl.get("icon") or "puzzle"),
                        kind_icon="puzzle",
                        owner=row["slug"],
                        root_path=root,
                        editable=not read_only,
                        importable=not read_only,
                        doc_editing=False,
                        reindex_kind=None,
                    )
                )
    return libs


def build_removable_libraries() -> list[MediaLibrary]:
    libs: list[MediaLibrary] = []
    for rm in detect_removable():
        mount = rm.get("mount")
        if not mount:
            continue
        root = Path(mount).expanduser().resolve(strict=False)
        try:
            if not root.exists() or not root.is_dir():
                continue
        except OSError:
            continue
        cdrom = bool(rm.get("read_only"))
        libs.append(
            MediaLibrary(
                id=f"removable:{drive_token(mount)}",
                label=f"USB ({mount})",
                kind="removable",
                icon="disc" if cdrom else "hard-drive",
                kind_icon="hard-drive",
                owner=rm.get("device"),
                root_path=root,
                editable=False,
                importable=False,
                doc_editing=False,
                reindex_kind=None,
                present=True,
            )
        )
    return libs


async def build_libraries() -> list[MediaLibrary]:
    """Rebuild the full registry fresh: core libs (from config) + enabled-plugin
    media libraries (from the plugins.manifest JSONB) + present removable
    mounts. Ordered core, plugin, removable. ``root_path`` stays server-side."""
    from web.backend.plugin_host import fetch_plugin_rows

    allowed = _allowed_under_config()
    libs = build_core_libraries(allowed)
    try:
        rows = await fetch_plugin_rows()
    except Exception as e:  # noqa: BLE001 — a registry read failure ≠ no Files
        log.warning("plugin rows read failed while building libraries: %s", e)
        rows = []
    libs.extend(build_plugin_libraries(rows, allowed))
    libs.extend(build_removable_libraries())
    return libs
