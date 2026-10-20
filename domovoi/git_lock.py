"""A git that died in Domovoi's checkout, and what it left behind.

git takes ``.git/index.lock`` (and ``HEAD.lock``, a ref's ``.lock``...)
with ``O_CREAT|O_EXCL`` while it works and renames or removes it when it is
done; a git that is killed outright (SIGKILL: :mod:`domovoi.git_run`'s
timeout, ``wsl --terminate`` at a Windows shutdown, a power cut) never
does, and every later git that needs the lock refuses: "Unable to create
'.../index.lock': File exists. Another git process seems to be running in
this repository". On a household's server nobody has a shell to remove
it, so the dashboard's Pull failed for good (2026-10-20, a Windows install).

Two pieces here:

* :func:`clear_stale_locks` removes such a lock when it is **stale**: no git
  process works in the checkout (``/proc``, else psutil; a process whose
  working directory can't be read counts as working there) and the lock is
  older than :data:`STALE_AFTER_SEC`. A live lock is never touched; a
  fresh one is left for the git that may still own it. The lock is looked
  at again right before it goes, so a new lock some other git took in
  between (another inode) stays. :func:`cleanup_after_kill` is the same for
  the git :func:`domovoi.git_run.run` itself just killed: a lock made since
  that git started goes at once.
* :func:`repair_interrupted_checkout` puts back the files a killed
  fast-forward had already rewritten (a torn tree): each file that differs
  from the index but holds, byte for byte, a version of itself from the
  commits being pulled is checked out from the index again, and a new file
  of those commits that is there untracked with that content is removed.
  Nothing is lost (every such content is in git's objects, and the next
  fast-forward writes it again); any other change is the household's own
  and is left alone, for git's own refusal to name.

``scripts/linux/apply-update.sh`` and ``scripts/linux/provision.sh`` do the
same in bash (``clear_stale_git_locks``), with the same threshold.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

log = logging.getLogger(__name__)

#: A lock younger than this is left alone even when no git can be seen: the
#: git that owns it may have started a moment ago. Every git the core runs
#: is bounded far below this (git_version), and so is every one the update
#: unit runs.
STALE_AFTER_SEC = 60.0

#: What the dashboard says instead of git's own text when it cleaned up
#: after an interrupted git and went on.
INTERRUPTED_NOTICE = "an earlier update was interrupted; Domovoi cleaned up and is trying again"

#: What it says when a git that is still running holds the checkout.
BUSY_MESSAGE = (
    "another git command is working in Domovoi's checkout right now, so nothing was "
    "changed; try again in a minute"
)

# The locks git leaves in .git (relative to the git dir), besides any
# *.lock under refs/.
_LOCK_NAMES = ("index.lock", "HEAD.lock", "ORIG_HEAD.lock", "packed-refs.lock", "config.lock", "shallow.lock")

_PROC = Path("/proc")


def git_dir(repo: str | os.PathLike) -> Path | None:
    """``repo``'s git directory: ``repo/.git``, or where a ``.git`` file
    (a linked worktree) points. None when ``repo`` is no checkout."""
    dotgit = Path(repo) / ".git"
    if dotgit.is_dir():
        return dotgit
    try:
        text = dotgit.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if text.startswith("gitdir:"):
        target = Path(text[len("gitdir:"):].strip())
        if not target.is_absolute():
            target = Path(repo) / target
        return target if target.is_dir() else None
    return None


def lock_files(repo: str | os.PathLike) -> list[Path]:
    """Every git lock file in ``repo``'s git directory right now."""
    gd = git_dir(repo)
    if gd is None:
        return []
    found = [gd / n for n in _LOCK_NAMES if os.path.lexists(gd / n)]
    refs = gd / "refs"
    if refs.is_dir():
        for root, _dirs, files in os.walk(refs):
            found.extend(Path(root) / f for f in files if f.endswith(".lock"))
    return found


def _within(path: str, roots: Sequence[str]) -> bool:
    return any(path == r or path.startswith(r.rstrip("/\\") + os.sep) for r in roots)


def _roots(repo: str | os.PathLike) -> list[str]:
    roots = [os.path.realpath(repo)]
    gd = git_dir(repo)
    if gd is not None:
        real = os.path.realpath(gd)
        if not _within(real, roots):
            roots.append(real)
    return roots


def _git_like(name: str) -> bool:
    name = os.path.basename(name).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name == "git" or name.startswith("git-")


def _cmdline_mentions(args: Iterable[str], roots: Sequence[str]) -> bool:
    return any(r in a for a in list(args)[1:] if a for r in roots)


def _proc_gits(roots: Sequence[str]) -> list[int] | None:
    """Linux: the pids of the git processes working in ``roots``, read from
    /proc. A git whose working directory can't be read (another user's)
    counts as working there."""
    me = os.getpid()
    pids: list[int] = []
    try:
        entries = list(_PROC.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue  # gone meanwhile
        if not _git_like(comm):
            continue
        try:
            args = (entry / "cmdline").read_bytes().decode("utf-8", "replace").split("\0")
        except OSError:
            args = []
        try:
            cwd = os.readlink(entry / "cwd")
        except FileNotFoundError:
            continue  # exited meanwhile
        except OSError:
            pids.append(pid)  # can't tell where it works: as if here
            continue
        if _within(cwd, roots) or _cmdline_mentions(args, roots):
            pids.append(pid)
    return pids


def _psutil_gits(roots: Sequence[str]) -> list[int] | None:
    try:
        import psutil
    except ImportError:
        return None
    me = os.getpid()
    pids: list[int] = []
    for p in psutil.process_iter(["name"]):
        if p.pid == me or not _git_like(p.info.get("name") or ""):
            continue
        try:
            cwd = os.path.realpath(p.cwd())
            args = p.cmdline()
        except psutil.NoSuchProcess:
            continue
        except Exception:  # noqa: BLE001 - AccessDenied and the like: as if here
            pids.append(p.pid)
            continue
        if _within(cwd, roots) or _cmdline_mentions(args, roots):
            pids.append(p.pid)
    return pids


def git_processes(repo: str | os.PathLike) -> list[int] | None:
    """The pids of the git processes working in ``repo`` (by working
    directory or by a path on their command line), or None when this box
    can't say (no /proc and no psutil): then no lock counts as stale."""
    roots = _roots(repo)
    if sys.platform.startswith("linux") and _PROC.is_dir():
        found = _proc_gits(roots)
        if found is not None:
            return found
    return _psutil_gits(roots)


def _same(a: os.stat_result, b: os.stat_result) -> bool:
    return a.st_ino == b.st_ino and a.st_mtime_ns == b.st_mtime_ns and a.st_dev == b.st_dev


def _age(st: os.stat_result, now: float) -> float:
    age = now - st.st_mtime
    # A lock dated well in the future is from before the clock moved back
    # (a VM that slept, a fixed RTC): its age can't be read, so it is old.
    return float("inf") if age < -300 else age


def _remove(path: Path, seen: os.stat_result) -> bool:
    try:
        now_st = os.lstat(path)
    except FileNotFoundError:
        return False
    if not _same(seen, now_st):
        return False  # another lock took its place: not the one looked at
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False


def clear_stale_locks(repo: str | os.PathLike, *, stale_after: float = STALE_AFTER_SEC,
                      now: float | None = None) -> dict:
    """Remove every stale git lock in ``repo`` (module docstring). Returns
    ``{"removed": [names], "live": [names], "fresh": [names],
    "unknown": [names]}``: what went, what a running git holds, what is too
    young to tell, and what stays because this box can't see processes.
    Never raises."""
    out: dict = {"removed": [], "live": [], "fresh": [], "unknown": []}
    try:
        locks = lock_files(repo)
        if not locks:
            return out
        gd = git_dir(repo)
        procs = git_processes(repo)
        t = time.time() if now is None else now
        for lock in locks:
            name = os.path.relpath(lock, gd) if gd is not None else lock.name
            try:
                st = os.lstat(lock)
            except FileNotFoundError:
                continue
            if procs is None:
                out["unknown"].append(name)
            elif procs:
                out["live"].append(name)
            elif _age(st, t) < stale_after:
                out["fresh"].append(name)
            elif _remove(lock, st):
                out["removed"].append(name)
                log.warning(
                    "removed a stale git lock %s in %s: %.0f s old and no git process works "
                    "there (a git was killed before it could clean up)",
                    name, repo, _age(st, t),
                )
    except OSError as e:
        log.warning("could not look at git's locks in %s: %s", repo, e)
    return out


def cleanup_after_kill(repo: str | os.PathLike | None, started_at: float) -> list[str]:
    """The git :func:`domovoi.git_run.run` started at ``started_at`` (epoch
    seconds) in ``repo`` has just been killed with everything it started:
    the locks it made are removed (made since it started, and no git works
    there). Returns their names. Never raises."""
    if repo is None:
        return []
    removed: list[str] = []
    try:
        locks = lock_files(repo)
        if not locks:
            return []
        procs = git_processes(repo)
        if procs is None or procs:
            return []
        gd = git_dir(repo)
        for lock in locks:
            try:
                st = os.lstat(lock)
            except FileNotFoundError:
                continue
            if st.st_mtime >= started_at - 2 and _remove(lock, st):
                name = os.path.relpath(lock, gd) if gd is not None else lock.name
                removed.append(name)
                log.warning("removed %s in %s, left by the git that was just stopped for taking too long", name, repo)
    except OSError as e:
        log.warning("could not clean up after the stopped git in %s: %s", repo, e)
    return removed


def is_lock_error(stderr: str | None) -> bool:
    """Whether git refused because a lock file was there."""
    low = (stderr or "").lower()
    return ".lock" in low and ("file exists" in low or "unable to create" in low or "cannot lock ref" in low)


def without_hints(stderr: str | None) -> str:
    """git's stderr without its ``hint:`` lines (advice, never the error)."""
    return "\n".join(l for l in (stderr or "").splitlines() if not l.startswith("hint:")).strip()


# ─── a torn tree ─────────────────────────────────────────────────────────

GitRun = Callable[..., subprocess.CompletedProcess]


def _nul(text: str | None) -> list[str]:
    return [p for p in (text or "").split("\0") if p]


def _chunks(paths: Sequence[str], limit: int = 6000) -> Iterable[list[str]]:
    """``paths`` in runs that fit a command line (Windows': 32 767
    characters), since a killed fast-forward can leave thousands."""
    run: list[str] = []
    size = 0
    for p in paths:
        if run and size + len(p) + 3 > limit:
            yield run
            run, size = [], 0
        run.append(p)
        size += len(p) + 3
    if run:
        yield run


def _versions(run: GitRun, base: str, tip: str, paths: Sequence[str]) -> dict[str, set[str]]:
    """For each of ``paths``: every blob the commits ``base..tip`` gave it,
    and "" where one of them deleted it."""
    out: dict[str, set[str]] = {}
    for part in _chunks(paths):
        proc = run("log", "--format=", "--raw", "--no-abbrev", "--no-renames", "-z", f"{base}..{tip}", "--", *part)
        if proc.returncode != 0:
            continue
        # ":<old mode> <new mode> <old blob> <new blob> <status>" NUL <path> NUL, per change.
        parts = (proc.stdout or "").split("\0")
        i = 0
        while i + 1 < len(parts):
            meta = parts[i].strip()
            if not meta.startswith(":"):
                i += 1
                continue
            path = parts[i + 1]
            i += 2
            fields = meta[1:].split()
            if len(fields) < 5:
                continue
            new, status = fields[3], fields[4]
            out.setdefault(path, set()).add("" if status.startswith("D") else new)
    return out


def _hashes(run: GitRun, paths: Sequence[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for part in _chunks(paths):
        proc = run("hash-object", "--", *part)
        lines = (proc.stdout or "").split()
        if proc.returncode == 0 and len(lines) == len(part):
            found.update(zip(part, lines))
    return found


def _blob(repo: str | os.PathLike, sha: str) -> bytes | None:
    """The bytes of blob ``sha`` (git's own, unfiltered), or None."""
    from domovoi import git_run

    try:
        proc = git_run.run(
            ["git", "-c", "advice.graftFileDeprecated=false", "cat-file", "blob", sha],
            cwd=repo, timeout=10, env={"GIT_NO_REPLACE_OBJECTS": "1"}, binary=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


# A file the killed git was writing is cut short: a byte-for-byte start of
# one of its versions holds nothing that version doesn't, so it is put back
# too. Looked for in this many files at most, each this big at most.
_PREFIX_FILES = 20
_PREFIX_MAX_BYTES = 8 * 1024 * 1024


def _cut_short(repo: str | os.PathLike, path: str, known: set[str], budget: list[int]) -> bool:
    """Whether ``path`` holds the start of one of the ``known`` blobs (and
    is shorter): a file a killed checkout was writing."""
    if budget[0] <= 0:
        return False
    try:
        data = (Path(repo) / path).read_bytes()
    except OSError:
        return False
    if len(data) > _PREFIX_MAX_BYTES:
        return False
    budget[0] -= 1
    for sha in known:
        if not sha:
            continue
        blob = _blob(repo, sha)
        if blob is not None and len(data) < len(blob) and blob.startswith(data):
            return True
    return False


def repair_interrupted_checkout(run: GitRun, repo: str | os.PathLike, base: str, tip: str) -> dict:
    """Put back what a fast-forward from ``base`` (HEAD) towards ``tip``
    had rewritten when it was killed (module docstring). ``run(*args)`` runs
    git in ``repo``. Returns ``{"restored": [paths], "removed": [paths]}``.
    Never raises; a step that fails leaves the tree as it was."""
    out: dict = {"restored": [], "removed": []}
    try:
        changed = run("diff", "--name-only", "--no-renames", "-z")
        if changed.returncode != 0:
            return out
        dirty = _nul(changed.stdout)
        added_proc = run("diff", "--name-only", "--no-renames", "--diff-filter=A", "-z", base, tip)
        added = _nul(added_proc.stdout) if added_proc.returncode == 0 else []
        untracked: list[str] = []
        for part in _chunks(added):
            others = run("ls-files", "--others", "-z", "--", *part)
            if others.returncode == 0:
                untracked.extend(_nul(others.stdout))
        if not dirty and not untracked:
            return out
        versions = _versions(run, base, tip, sorted(set(dirty) | set(untracked)))
        present = [p for p in dirty + untracked if os.path.isfile(Path(repo) / p) and not os.path.islink(Path(repo) / p)]
        hashes = _hashes(run, present)
        budget = [_PREFIX_FILES]

        def from_the_update(p: str) -> bool:
            known = versions.get(p)
            if not known:
                return False
            h = hashes.get(p)
            if h is not None:
                return h in known or _cut_short(repo, p, known, budget)
            return not os.path.lexists(Path(repo) / p) and "" in known

        torn = [p for p in dirty if from_the_update(p)]
        new = [p for p in untracked if hashes.get(p) is not None and from_the_update(p)]
        for part in _chunks(torn):
            back = run("checkout", "-q", "--", *part)
            if back.returncode != 0:
                log.warning("could not put back the files an interrupted update rewrote: %s", without_hints(back.stderr))
                return out
            out["restored"].extend(part)
        for p in new:
            try:
                os.unlink(Path(repo) / p)
                out["removed"].append(p)
            except OSError:
                pass
        if torn or out["removed"]:
            log.warning(
                "an interrupted update had rewritten %d file(s) and added %d: put back as they were "
                "at %s, so the update can run again (%s)",
                len(torn), len(out["removed"]), base[:12], ", ".join((torn + out["removed"])[:5]),
            )
    except Exception as e:  # noqa: BLE001 - a repair must never break the pull
        log.warning("could not look for files an interrupted update left: %s", e)
    return out
