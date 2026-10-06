"""Writing ``.lrc`` files for LRCLIB's timed lyrics (contract §6, [W1]–[W18],
[O1]–[O4]).

The household asked for both (2026-10-03): LRCLIB's lyrics in the database
AND a ``.lrc`` next to the song, so other players can show them too — but
only where the song has none, and never touching a ``.lrc`` that is not
Domovoi's own.

**Whose file is it?** A ``.lrc`` is Domovoi's own only while the row says
``lrc_state = 'written'``, the file still has the name Domovoi gave it
(``lrc_name``) and its bytes still hash to what was written
(``lrc_sha256``). Anything else — an edited copy of Domovoi's file, one
from another install, one restored from a backup — is the household's:
read as their lyrics and never written. An edited file turns the row to
``edited`` and a deleted one to ``deleted``; neither is written again.

**Never clobbering.** A new file is written to a temporary name created
exclusively (``O_CREAT | O_EXCL``), flushed and synced, then hard-linked to
its real name — the link fails if anything appeared there meanwhile — and
the temporary name is always removed. The temporary name is
``.<stem>.lrc.domovoi-<hex>.tmp``, or ``.domovoi-<hex>.lrc.tmp`` when that
would be too long a name (a long song name whose ``.lrc`` still fits).
Where hard links are not supported, the real name itself is created
exclusively. Any existing path at the target (a file, a directory, a
symlink, a broken symlink) means "exists": nothing is written and no
symlink is ever followed. Refreshing Domovoi's own file re-checks its
bytes right before ``os.replace``. A song whose file was renamed since
Domovoi wrote its ``.lrc`` gets a new one under the new name (the old one
is left where it is).

:data:`SIDECAR_LOCK` is held by the writer from creating a file until its
hash is committed, and by the local scan for a whole batch, so the scan
never meets a file Domovoi wrote a moment ago with its hash not yet
recorded. Nobody waits for the lock while holding uncommitted writes.

Domovoi never deletes or renames a ``.lrc`` ([W11]). The functions here are
blocking (run them in a worker thread), never raise, and never log a
lyric; file names are logged at DEBUG at most.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import secrets
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from domovoi.lyrics import LoopLocalLock
from domovoi.lyrics.lrc import MAX_LRC_BYTES
from domovoi.lyrics.sources import name_key, read_sidecar_bytes, sidecar_candidates

log = logging.getLogger(__name__)

#: Held by the writer from "create the file" to "record its hash,
#: committed", and by the local scan for a whole batch ([O4]).
SIDECAR_LOCK = LoopLocalLock()

#: The ``lrc_error`` codes of a failed write ([W13]).
ERROR_CODES = ("permission", "outside_music_dir", "io", "name")

# errno values meaning "this file system can't make hard links" ([W6]).
_LINK_UNSUPPORTED_ERRNOS = frozenset(
    e for e in (
        errno.EPERM,
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "ENOTSUP", None),
        errno.EXDEV,
        getattr(errno, "ENOSYS", None),
    ) if e is not None
)
# Windows: ERROR_INVALID_FUNCTION (FAT, exFAT), ERROR_NOT_SUPPORTED (some shares).
_LINK_UNSUPPORTED_WINERRORS = frozenset({1, 50})
# Windows: ERROR_INVALID_NAME, ERROR_FILENAME_EXCED_RANGE.
_NAME_WINERRORS = frozenset({123, 206})

_O_BINARY = getattr(os, "O_BINARY", 0)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def target_name(audio_path: str | os.PathLike[str]) -> str:
    """[W2]: the audio file's own stem with a lower-case ``.lrc``."""
    return f"{Path(audio_path).stem}.lrc"


@dataclass(frozen=True)
class WriteOutcome:
    """What one write attempt concluded. ``state`` is what to record in
    ``lrc_state`` (written | exists | failed | edited | deleted), or None
    when there is nothing to record (no write was needed or allowed, or
    refreshing Domovoi's own file failed and it stays as it was).
    ``error``: a [W13] code. ``stop``: the disk is full — no more writes
    this tick."""

    state: str | None
    name: str | None = None
    sha256: str | None = None
    error: str | None = None
    stop: bool = False
    #: The song's file was renamed since Domovoi wrote its ``.lrc`` (a plugin
    #: re-filing a download keeps the row and changes its name): that file
    #: was left where it is, and this was a new write for the new name.
    renamed: bool = False

    @property
    def wrote(self) -> bool:
        return self.state == "written"


_NOTHING = WriteOutcome(None)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _error_code(e: OSError) -> str:
    winerror = getattr(e, "winerror", None)
    if e.errno == errno.ENAMETOOLONG or winerror in _NAME_WINERRORS:
        return "name"
    if sys.platform == "win32" and e.errno == errno.EINVAL:
        return "name"
    if isinstance(e, PermissionError):
        return "permission"
    return "io"


def _failed(e: OSError, name: str | None) -> WriteOutcome:
    return WriteOutcome("failed", name=name, error=_error_code(e), stop=e.errno == errno.ENOSPC)


def _link_unsupported(e: OSError) -> bool:
    if getattr(e, "winerror", None) in _LINK_UNSUPPORTED_WINERRORS:
        return True
    return e.errno in _LINK_UNSUPPORTED_ERRNOS and not isinstance(e, FileExistsError)


def _open_new(path: Path) -> int:
    """Create ``path`` exclusively, mode 0o644 through the umask ([W9]).
    ``FileExistsError`` when anything at all is there (a symlink too)."""
    return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARY, 0o644)


def _fill(fd: int, content: bytes) -> None:
    """Write, flush and sync ``content``, then close ``fd`` (closed on
    failure too)."""
    try:
        view = memoryview(content)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.close(fd)


def _write_new_file(path: Path, content: bytes) -> None:
    """Create ``path`` exclusively and fill it; a file this call created is
    removed again when filling it fails."""
    fd = _open_new(path)
    try:
        _fill(fd, content)
    except BaseException:
        _unlink_quietly(path)
        raise


def _unlink_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:  # noqa: BLE001 — leftovers are logged, never raised
        log.debug("lyrics .lrc: could not remove a temporary file (%s)", type(e).__name__)


#: The longest file name the scan's own file systems take: 255 bytes (ext4
#: and most POSIX ones, in UTF-8) or 255 characters (NTFS).
MAX_NAME = 255


def _temp_path(directory: Path, stem: str, *, short: bool = False) -> tuple[Path, bool]:
    """A temporary name in the song's folder ([W6], amended 2026-10-06),
    and whether it is the short form: one that says whose it is,
    ``.<stem>.lrc.domovoi-<hex>.tmp`` — or, when that would be longer than
    a file name may be (it is 18 characters longer than the ``.lrc`` it
    becomes), or ``short``, ``.domovoi-<hex>.lrc.tmp``. Neither is a
    ``.lrc`` to the scan or audio to the library indexer."""
    token = secrets.token_hex(4)
    name = f".{stem}.lrc.domovoi-{token}.tmp"
    if short or len(name) > MAX_NAME or len(name.encode("utf-8", "surrogateescape")) > MAX_NAME:
        return directory / f".domovoi-{token}.lrc.tmp", True
    return directory / name, False


def _name_trouble(e: OSError) -> bool:
    """An error that may be the temporary name's length, not the folder:
    too long, an invalid name, or (Windows without long paths) a path past
    MAX_PATH, which Windows reports as "path not found"."""
    return _error_code(e) == "name" or (sys.platform == "win32" and isinstance(e, FileNotFoundError))


def _write_temp(directory: Path, stem: str, content: bytes) -> Path:
    """Write ``content`` to a fresh temporary file in ``directory``
    (exclusively created, synced) and return its path. A name taken by
    somebody else's file is tried once more with another random part; a
    name too long for the file system, with the short form. Raises the
    last ``OSError``."""
    tmp, short = _temp_path(directory, stem)
    try:
        _write_new_file(tmp, content)
        return tmp
    except FileExistsError:
        retry, _ = _temp_path(directory, stem, short=short)
    except OSError as e:
        if short or not _name_trouble(e):
            raise
        retry, _ = _temp_path(directory, stem, short=True)
    _write_new_file(retry, content)
    return retry


def _own_bytes(path: Path, music_root: Path) -> bytes | None:
    """The current bytes of the file at ``path`` (None when they can't be
    read by the sidecar rules)."""
    return read_sidecar_bytes(path, music_root).data


def write_sidecar(
    audio_path: str | os.PathLike[str],
    content: bytes,
    *,
    music_root: Path,
    lrc_state: str | None,
    lrc_name: str | None,
    lrc_sha256: str | None,
    link: Callable[[Path, Path], None] = os.link,
) -> WriteOutcome:
    """Write (or refresh) the ``.lrc`` for the audio file at ``audio_path``
    with ``content`` (the bytes of :func:`~domovoi.lyrics.lrc.format_lrc`),
    given the row's current ``lrc_*`` columns. Blocking; never raises.
    ``music_root`` is ``Path(settings.music_dir).expanduser().resolve()``.
    ``link`` is ``os.link`` (a seam for the tests of [W6]'s fallback)."""
    if lrc_state in ("edited", "deleted"):                     # [W5]
        return _NOTHING
    audio = Path(audio_path)
    name = target_name(audio)
    directory = audio.parent
    target = directory / name
    try:
        if not os.path.exists(audio):                          # [W16]
            return _NOTHING
        # [W3]: the audio file inside MUSIC_DIR (its folder too, through any
        # symlink).
        if not _inside(audio.resolve(strict=False), music_root):
            return WriteOutcome("failed", name=name, error="outside_music_dir")
        with os.scandir(directory) as it:
            names = [entry.name for entry in it]
    except (OSError, RuntimeError, ValueError) as e:
        if isinstance(e, OSError):
            return _failed(e, name)
        return WriteOutcome("failed", name=name, error="io")
    candidates = sidecar_candidates(audio.name, names)
    new_sha = sha256_hex(content)
    # A file bigger than the scan reads could never be recognised as
    # Domovoi's own again: such lyrics stay in the database only.
    too_big = not fits_on_disk(content)

    if lrc_state == "written" and lrc_name and song_renamed(lrc_name, audio):
        # The song's file has another name since Domovoi wrote its .lrc (a
        # plugin re-filing a download keeps the row, not the name): that
        # .lrc is left where it is ([W11]) — it is no longer this song's —
        # and this is a new write for the new name, under [W4] as any is.
        return replace(_new_file(directory, audio, name, target, candidates, content, new_sha,
                                 too_big=too_big, music_root=music_root, link=link),
                       renamed=True)

    if lrc_state == "written" and lrc_name:
        own = directory / lrc_name
        if not os.path.lexists(own):                           # [O3]
            return WriteOutcome("deleted", name=lrc_name, sha256=lrc_sha256)
        current = _own_bytes(own, music_root)
        if current is None:
            # Not readable just now: no verdict either way, nothing written.
            return WriteOutcome(None, name=lrc_name, error="io")
        if sha256_hex(current) != lrc_sha256:                  # [O2]
            return WriteOutcome("edited", name=lrc_name, sha256=lrc_sha256)
        if new_sha == lrc_sha256:                              # [W8]
            return _NOTHING
        others = [c for c in candidates if c != lrc_name]
        if others or (name != lrc_name and os.path.lexists(target)):
            # Another .lrc for this song turned up beside Domovoi's: leave
            # both as they are.
            return _NOTHING
        if too_big:
            return WriteOutcome(None, name=lrc_name, error="io")
        return _refresh(own, content, new_sha, music_root=music_root,
                        lrc_name=lrc_name, lrc_sha256=lrc_sha256)

    return _new_file(directory, audio, name, target, candidates, content, new_sha,
                     too_big=too_big, music_root=music_root, link=link)


def song_renamed(lrc_name: str, audio_path: str | os.PathLike[str]) -> bool:
    """Whether the ``.lrc`` Domovoi wrote for a song (``lrc_name``) is no
    longer named after the song's file — the file was renamed since. Names
    compare as the sidecar rule compares them (NFC, any case)."""
    return name_key(lrc_name) != name_key(target_name(audio_path))


def _new_file(
    directory: Path, audio: Path, name: str, target: Path, candidates: list[str],
    content: bytes, new_sha: str, *, too_big: bool, music_root: Path,
    link: Callable[[Path, Path], None],
) -> WriteOutcome:
    """[W4]: a new ``.lrc``, only where the song has none at all."""
    if candidates or os.path.lexists(target):                  # [W4], [W10]
        return WriteOutcome("exists", name=candidates[0] if candidates else name)
    if too_big:
        return WriteOutcome("failed", name=name, error="io")
    try:
        if not _inside(target.resolve(strict=False), music_root):   # [W3]
            return WriteOutcome("failed", name=name, error="outside_music_dir")
    except (OSError, RuntimeError, ValueError):
        return WriteOutcome("failed", name=name, error="io")
    return _create(directory, audio.stem, target, content, new_sha, link=link)


def _create(
    directory: Path, stem: str, target: Path, content: bytes, sha: str,
    *, link: Callable[[Path, Path], None],
) -> WriteOutcome:
    """[W6]: temporary file, exclusively created and synced, hard-linked to
    the target; the exclusive-create fallback where links don't work."""
    name = target.name
    # _write_new_file removes what it created when it fails, and nothing
    # else: a path that was already there is never touched.
    try:
        tmp = _write_temp(directory, stem, content)
    except OSError as e:
        return _failed(e, name)
    try:
        try:
            link(tmp, target)
        except FileExistsError:
            return WriteOutcome("exists", name=name)
        except OSError as e:
            if not _link_unsupported(e):
                return _failed(e, name)
            return _create_directly(target, content, sha)
    finally:
        _unlink_quietly(tmp)
    log.debug("lyrics .lrc: wrote %s", name)
    return WriteOutcome("written", name=name, sha256=sha)


def _create_directly(target: Path, content: bytes, sha: str) -> WriteOutcome:
    """[W6]'s fallback: create the target itself exclusively. Never
    clobbers; a reader may see it half-written (the scan re-reads files
    that change). A file this call created and could not fill is removed
    again — only that one: a path that already existed is never touched."""
    name = target.name
    try:
        _write_new_file(target, content)
    except FileExistsError:
        return WriteOutcome("exists", name=name)
    except OSError as e:
        return _failed(e, name)
    log.debug("lyrics .lrc: wrote %s (no hard links here)", name)
    return WriteOutcome("written", name=name, sha256=sha)


def _refresh(
    own: Path, content: bytes, sha: str, *, music_root: Path,
    lrc_name: str, lrc_sha256: str | None,
) -> WriteOutcome:
    """[W7]: replace Domovoi's own file, re-checking it is still Domovoi's
    immediately before. A failure leaves the old file and the row as they
    were (the row stays ``written``: the file is still Domovoi's)."""
    try:
        tmp = _write_temp(own.parent, Path(lrc_name).stem, content)
    except OSError as e:
        return WriteOutcome(None, name=lrc_name, error=_error_code(e), stop=e.errno == errno.ENOSPC)
    try:
        if not os.path.lexists(own):
            return WriteOutcome("deleted", name=lrc_name, sha256=lrc_sha256)
        current = _own_bytes(own, music_root)
        if current is None:
            return WriteOutcome(None, name=lrc_name, error="io")
        if sha256_hex(current) != lrc_sha256:
            return WriteOutcome("edited", name=lrc_name, sha256=lrc_sha256)
        os.replace(tmp, own)
    except OSError as e:
        return WriteOutcome(None, name=lrc_name, error=_error_code(e), stop=e.errno == errno.ENOSPC)
    finally:
        _unlink_quietly(tmp)
    log.debug("lyrics .lrc: refreshed %s", lrc_name)
    return WriteOutcome("written", name=lrc_name, sha256=sha)


def fits_on_disk(content: bytes) -> bool:
    """A file Domovoi writes stays readable by its own scan."""
    return len(content) <= MAX_LRC_BYTES


__all__ = [
    "ERROR_CODES",
    "MAX_NAME",
    "SIDECAR_LOCK",
    "WriteOutcome",
    "fits_on_disk",
    "sha256_hex",
    "song_renamed",
    "target_name",
    "write_sidecar",
]
