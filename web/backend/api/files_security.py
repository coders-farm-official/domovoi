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
* ``validate_root`` refuses filesystem/drive roots and anything sensitive;
  ``root_rejection`` is the same predicate with a reason string, so a library
  that drops out of the registry says WHY in the log instead of vanishing.
* ``ensure_core_library_dirs`` creates the ``CORE_LIBRARIES`` dirs at boot
  (called from the web lifespan) so a headless install actually HAS a
  Documents and a Pictures library; ``creation_refusal`` is the stricter
  "may we create this?" predicate that keeps us off drive roots, config-dir
  secrets and unmounted mountpoints.
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
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from domovoi.config import settings as core_settings
from domovoi.host_kind import host_kind

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


# True when this host's filesystem gives a name meanings the name does not
# obviously carry: on NTFS a colon opens an ALTERNATE DATA STREAM
# ("tax.pdf:stash.md" is bytes hanging off tax.pdf that no listing shows),
# and a trailing dot or space is silently trimmed (".env." is stored as
# ".env"). On POSIX all three are ordinary filename characters and nothing
# here fires. Module-level so a test can exercise the Windows rules on a
# Linux runner and the POSIX rules on this Windows dev box.
WINDOWS_NAME_RULES = os.name == "nt"

# Characters no legitimate caller puts in a path and that the OS answers with
# an exception rather than an error: NUL, tab, the rest of C0, and DEL.
_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}


def effective_names(name: str) -> set[str]:
    """Every name this one could actually BE once the filesystem is done
    with it — including itself.

    A filter that inspects the string the caller typed decides about a file
    that may not be the file that gets created. ``.env.`` and ``.env `` are
    both stored as ``.env`` on Windows, and ``.env:x`` creates ``.env`` with
    a stream hanging off it. Every one of those IS the reserved name the
    filter exists to refuse, and every one of them used to pass it.

    The trailing dot/space trim is applied on BOTH platforms deliberately.
    On POSIX ``.env.`` really is a different, ordinary file — but nothing
    legitimate is called that, and a filter that over-matches by one absurd
    name is worth more than one whose answer depends on which host the
    server happens to be running on. The colon split is NOT applied on
    POSIX: a colon is an ordinary character in a filename there
    ("Meeting: notes.md"), and refusing it would break a real name to fix a
    problem that does not exist.
    """
    out = {name}
    for cand in (name, name.split(":", 1)[0] if WINDOWS_NAME_RULES else name):
        out.add(cand)
        out.add(cand.rstrip(" ."))
    out.discard("")
    return out


def is_sensitive_name(name: str) -> bool:
    """True for a secret-shaped entry name (denylisted at listing/serve).

    Asks about every name ``name`` could turn into on disk, not only the
    spelling the caller sent — see :func:`effective_names`.
    """
    for candidate in effective_names(name):
        lower = candidate.lower()
        if candidate in DENY_NAMES or lower in DENY_NAMES:
            return True
        if Path(candidate).suffix.lower() in DENY_SUFFIXES:
            return True
    return False


def unstorable_reason(rel_path: str) -> str | None:
    """Why this path cannot be stored AS WRITTEN, or ``None`` when it can.

    Normalise before deciding, and refuse what cannot be normalised. Three
    shapes are refused here, all of them cases where the path a caller sent
    and the path the filesystem would use are not the same thing:

    * **control characters** (a percent-decoded NUL or tab, anything else
      below 0x20, DEL). Not a hole on their own — nothing is written — but
      ``stat()`` answers a NUL with ``ValueError`` and a tab with
      ``OSError``, and that escapes the handler as an unhandled **500**. A
      malformed path from a phone belongs in the 4xx range; a 500 in the log
      is noise that hides the real ones.
    * **a stream separator** on an NTFS host. ``PUT /text/tax.pdf:stash.md``
      passed the text editor's extension gate (``.md``), passed containment
      (it IS inside Documents) and wrote bytes into an alternate data stream
      on the PDF: invisible to the Documents list, to the Files page, to the
      directory zip, to ``media_walk`` and to ``os.listdir``, yet readable
      straight back through ``/raw``. That is persistent storage inside the
      operator's Documents that the operator cannot see, list, export or
      delete. ``::$INDEX_ALLOCATION`` is the same syntax and answers a
      ``stat()`` with ``NotADirectoryError``.
    * **a trailing dot or space** on an NTFS host, which Windows trims at
      the filesystem layer — so the caller names one file and a different
      one is written. That is how ``.env.`` got past the reserved-name
      filter and landed on disk as ``.env``.

    Length is deliberately NOT checked: the limit is not a constant this
    module can know, and an over-long name fails on the write with an
    ``OSError`` the save routes now turn into a 4xx.
    """
    if any(ch in _CONTROL_CHARS for ch in rel_path):
        return "control characters are not allowed in a path"
    if not WINDOWS_NAME_RULES:
        return None
    if ":" in rel_path:
        return (
            "':' opens an alternate data stream on this server's filesystem, "
            "so a file saved there would be invisible to every listing"
        )
    for segment in rel_path.replace("\\", "/").split("/"):
        # "." and ".." are dot SEGMENTS, not names with a trailing dot:
        # they are relative-path syntax, they never reach the filesystem as
        # a filename, and containment already deals with them.
        if not segment or not segment.strip("."):
            continue
        if segment != segment.rstrip(" ."):
            return (
                f"{segment!r} cannot be stored as written — this server's "
                "filesystem drops a trailing dot or space, so the file would "
                "land under a different name"
            )
    return None


def assert_storable(rel_path: str) -> None:
    """``400`` when :func:`unstorable_reason` has one."""
    reason = unstorable_reason(rel_path)
    if reason is not None:
        raise HTTPException(status_code=400, detail=f"{rel_path}: {reason}")


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
    # Normalise before deciding, and refuse what cannot be normalised: a NUL
    # or a tab escapes the handler as a 500, and on an NTFS host a colon or
    # a trailing dot addresses a file other than the one named.
    assert_storable(rel)
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


def root_rejection(
    root: Path, allowed_under_config: set[Path] | None = None
) -> str | None:
    """Why ``root`` is unusable as a browsable root, or ``None`` when it is
    fine. Exactly the predicate :func:`validate_root` applies, but it hands
    back a human-readable reason so a dropped library can say WHY in the log
    instead of vanishing silently — that silence is the whole bug: a headless
    server has no ``~/Documents``, so the Documents library (and with it the
    "+ New document / spreadsheet / drawing" menu, which renders only for
    ``core:documents``) simply was not there, with nothing in the log."""
    r = root.expanduser().resolve(strict=False)
    try:
        if not r.exists():
            return "does not exist"
        if not r.is_dir():
            return "exists but is not a directory"
    except OSError as e:
        return f"could not be inspected ({e})"
    if r == Path(r.anchor) or len(r.parts) <= 1:
        return "is a whole filesystem / drive root"
    if _is_sensitive(r, allowed_under_config or set()):
        return "is inside the private config dir (~/.domovoi)"
    return None


def validate_root(root: Path, allowed_under_config: set[Path] | None = None) -> bool:
    """A candidate browsable root is valid iff it exists, is a directory, is
    not a filesystem/drive root, and is not sensitive (config-dir guard)."""
    return root_rejection(root, allowed_under_config) is None


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


def removable_drives_visible() -> bool:
    """Whether this host can see removable drives at all. False inside WSL
    (the Windows installer's host): Windows keeps USB sticks and SD cards
    to itself and WSL automounts none of them, so :func:`detect_removable`
    comes back empty whatever is plugged in. The satellite surfaces that
    depend on a drive — USB adoption, writing a card from media prep — read
    this to explain the gap instead of offering a list that is always
    empty. Detection itself still runs everywhere: a drive someone attached
    to WSL and mounted by hand still shows on the Files page."""
    return host_kind() != "wsl"


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
    ("core:pictures", "Pictures", "image", "pictures_dir", True, True, "pictures", False),
)

# The Documents library's id, named once. ``/api/files`` and
# ``/api/documents`` are TWO DOORS into this one folder, and both have to
# ask the same questions about it (see ``core_library`` below and
# :func:`web.backend.api.files.assert_documents_write_allowed`), so the id
# is a constant rather than a string literal repeated in two routers.
DOCUMENTS_LIBRARY_ID = "core:documents"

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


# ─── Boot-time creation of the configured core library dirs ──────────────────
# Nothing in the product ever created these. ``documents_dir`` defaults to
# ``~/Documents`` and ``pictures_dir`` to ``~/Pictures`` — XDG desktop
# conventions a headless server account simply does not have — so
# :func:`validate_root` failed, :func:`build_core_libraries` skipped them, and
# the Files page silently lost both libraries on every real deployment.
def _env_name(attr: str) -> str:
    """The ``.env`` / environment spelling of a settings field (``DOCUMENTS_DIR``)."""
    return attr.upper()


def _home_dir() -> Path:
    return Path.home().expanduser().resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def creation_refusal(
    root: Path, allowed_under_config: set[Path] | None = None
) -> str | None:
    """Why ``root`` must NOT be auto-created, or ``None`` when creating it is
    safe. Deliberately stricter than the inverse of :func:`validate_root`:

    * a filesystem/drive root, or a path inside the ``~/.domovoi`` secret area,
      would be refused as a library anyway — creating it is pure damage;
    * a path that already exists as a FILE is a misconfiguration to report, not
      something to clobber;
    * **a path outside the home directory is never created.** That is the
      unmounted-drive guard. ``MUSIC_DIR=/mnt/media/music`` on an external disk
      has a parent (``/mnt/media``) that exists whether or not the disk is
      mounted, so a "create when the parent exists" rule would cheerfully make
      an empty directory on the root filesystem that the real mount then MASKS:
      the household sees an empty library, writes files into it, and those files
      vanish the next time the drive comes up (and quietly fill the system
      disk meanwhile). ``os.path.ismount`` cannot tell an empty mountpoint
      awaiting its drive from an ordinary empty directory, so we do not guess.
      Home is the one tree we know exists, is owned by this process, and is
      never a pending mount — if it were, ``~/.domovoi`` (setup code, device
      token, TLS) would already be broken. Everything else belongs to the
      operator, who now gets a WARNING naming the setting and the path instead
      of a library that is simply absent.
    """
    r = root.expanduser().resolve(strict=False)
    if r == Path(r.anchor) or len(r.parts) <= 1:
        return "is a whole filesystem / drive root"
    if _is_sensitive(r, allowed_under_config or set()):
        return "is inside the private config dir (~/.domovoi)"
    try:
        if r.exists() and not r.is_dir():
            return "already exists and is not a directory"
    except OSError as e:
        return f"could not be inspected ({e})"
    if not _is_within(r, _home_dir()):
        return (
            "is outside the home directory, so it may be the mountpoint of a "
            "drive that is not mounted yet (an empty directory there would mask "
            "the mount) — create it yourself"
        )
    return None


@dataclass(frozen=True)
class LibraryDirResult:
    """One row of :func:`ensure_core_library_dirs`'s report."""

    library_id: str
    setting: str
    path: Path
    status: str  # "created" | "exists" | "refused" | "failed"
    reason: str | None = None


def ensure_core_library_dirs(
    allowed_under_config: set[Path] | None = None,
) -> list[LibraryDirResult]:
    """Create every directory named by :data:`CORE_LIBRARIES`, at boot.

    ``mkdir -p`` semantics with **ordinary permissions** — these hold household
    media, not secrets, so ``admin_auth.write_private_file``'s 0600 writer is
    deliberately not used. Driven off the table, so a library added to
    :data:`CORE_LIBRARIES` later is covered without touching this function.

    Idempotent and boot-safe: an existing directory is left exactly as it is
    (``mkdir`` is not called at all, so no permission or mtime churn), a path
    that must not be created is refused by :func:`creation_refusal`, and every
    failure is a WARNING rather than an exception. Boot must never fail because
    a media directory could not be created — this runs on every start,
    including on a box whose disk is full or whose media drive is unmounted."""
    allowed = (
        _allowed_under_config() if allowed_under_config is None else allowed_under_config
    )
    results: list[LibraryDirResult] = []
    # Unpack only the first four columns so extra columns can be added to the
    # table without touching this loop.
    for lib_id, _label, _icon, attr, *_rest in CORE_LIBRARIES:
        try:
            raw = getattr(core_settings, attr)
        except Exception as e:  # noqa: BLE001 — a bad setting must not stop boot
            log.warning(
                "media library %s: setting %s is unreadable (%s); the library "
                "will not appear on the Files page",
                lib_id, _env_name(attr), e,
            )
            results.append(
                LibraryDirResult(lib_id, attr, Path("."), "failed", f"unreadable: {e}")
            )
            continue
        try:
            root = Path(raw).expanduser().resolve(strict=False)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "media library %s: %s=%r is not a usable path (%s); the library "
                "will not appear on the Files page",
                lib_id, _env_name(attr), raw, e,
            )
            results.append(
                LibraryDirResult(lib_id, attr, Path("."), "failed", f"bad path: {e}")
            )
            continue

        refusal = creation_refusal(root, allowed)
        if refusal:
            log.warning(
                "media library %s: NOT creating %s (%s=%r) because it %s. The "
                "Files page will not show this library until that directory "
                "exists.",
                lib_id, root, _env_name(attr), raw, refusal,
            )
            results.append(LibraryDirResult(lib_id, attr, root, "refused", refusal))
            continue

        try:
            if root.is_dir():
                # Already there — touch nothing (no mkdir, no chmod, no utime).
                results.append(LibraryDirResult(lib_id, attr, root, "exists"))
                continue
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning(
                "media library %s: could not create %s (%s=%r): %s. The Files "
                "page will not show this library until that directory exists.",
                lib_id, root, _env_name(attr), raw, e,
            )
            results.append(LibraryDirResult(lib_id, attr, root, "failed", str(e)))
            continue

        log.info(
            "media library %s: created %s (%s)", lib_id, root, _env_name(attr)
        )
        results.append(LibraryDirResult(lib_id, attr, root, "created"))
    return results


# Last skip reason logged per library, so the registry rebuild (which runs on
# every Files page load) warns ONCE per distinct problem rather than on every
# request — and warns again when the problem changes, or comes back after the
# library was healthy.
_SKIP_WARNED: dict[str, str] = {}


def _warn_core_library_skipped(
    lib_id: str, attr: str, raw: object, path: Path, reason: str
) -> None:
    key = f"{path}|{reason}"
    if _SKIP_WARNED.get(lib_id) == key:
        log.debug("media library %s still unavailable: %s %s", lib_id, path, reason)
        return
    _SKIP_WARNED[lib_id] = key
    log.warning(
        "Files: media library %s is NOT available — %s=%r resolves to %s, which "
        "%s. Create that directory (or point the setting elsewhere) and reload "
        "the Files page.",
        lib_id, _env_name(attr), raw, path, reason,
    )


def build_core_libraries(allowed_under_config: set[Path]) -> list[MediaLibrary]:
    libs: list[MediaLibrary] = []
    for (lib_id, label, icon, attr, editable, importable, reindex, doc_editing) in CORE_LIBRARIES:
        raw = None
        try:
            raw = getattr(core_settings, attr)
            root = Path(raw).expanduser().resolve(strict=False)
        except Exception as e:  # noqa: BLE001
            _warn_core_library_skipped(
                lib_id, attr, raw, Path("."), f"is not a usable path ({e})"
            )
            continue
        reason = root_rejection(root, allowed_under_config)
        if reason:
            _warn_core_library_skipped(lib_id, attr, raw, root, reason)
            continue
        _SKIP_WARNED.pop(lib_id, None)
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


def core_library(library_id: str) -> MediaLibrary | None:
    """The record for ONE core library, resolved from config alone.

    :func:`build_libraries` is the full registry: it also reads the
    plugins table and scans removable mounts. A core library needs
    neither — its root comes straight from ``core_settings`` — and
    :mod:`web.backend.api.documents` has to ask for ``core:documents`` on
    every save, so it gets a database-free way to do it.

    ``None`` means the library is not on the surface at all right now
    (:func:`root_rejection` turned its configured root down — most often
    "does not exist" on a headless install that has no ``~/Documents``
    yet). Callers must treat that as "no library-level rule to apply",
    NOT as "denied": the documents router creates that folder on first
    save, and refusing the first save would make the library unreachable
    forever.
    """
    for lib in build_core_libraries(_allowed_under_config()):
        if lib.id == library_id:
            return lib
    return None


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
