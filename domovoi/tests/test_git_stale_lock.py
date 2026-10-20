"""A git killed in Domovoi's checkout, and the pull after it
(domovoi/git_lock.py, git_version.pull, git_run's timeout).

2026-10-20: a household's Windows install could no longer update. Settings >
Version > Pull said "error: Unable to create '/opt/domovoi/.git/index.lock':
File exists ...", after git's grafts hint, and nobody there has a shell to
remove the file. These hold the fix: a stale lock goes (and only a stale
one), the files a killed fast-forward rewrote are put back, the timeout's
kill cleans up after itself, the card says it in plain words, a pull waits
for the update unit's checkout lock, and git's hint is gone.

git runs for real against throwaway repositories. The bash side
(scripts/linux: the same block in apply-update.sh and provision.sh) is held
to the same text here and run by its harnesses.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from domovoi import git_lock, git_run, git_version
from domovoi.config import settings

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="flock: the update unit is Linux's")

REPO_ROOT = Path(__file__).resolve().parents[2]
APPLY = REPO_ROOT / "scripts" / "linux" / "apply-update.sh"
PROVISION = REPO_ROOT / "scripts" / "linux" / "provision.sh"

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **_ENV},
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _write(repo: Path, name: str, text: str) -> None:
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode())


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _age(path: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t))


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Upstream ``work`` at A, the server's clone at A, and B upstream: B
    changes a.txt and pkg/b.txt, deletes gone.txt and adds pkg/new.txt.
    Returns (work, server, sha_a, sha_b)."""
    (tmp_path / "gitconfig").touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("DOMOVOI_ALLOWED_SIGNERS", str(tmp_path / "no-such-signers"))
    # No update unit here: its result file (and checkout.lock) don't exist.
    monkeypatch.setattr(settings, "update_result_file", str(tmp_path / "update" / "last-result.json"))
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _write(work, "a.txt", "a1\n")
    _write(work, "pkg/b.txt", "b1\n")
    _write(work, "gone.txt", "gone\n")
    _write(work, "keep.txt", "keep\n")
    sha_a = _commit(work, "A")
    srv = tmp_path / "server"
    _git(tmp_path, "clone", "-q", str(work), str(srv))
    _write(work, "a.txt", "a2\n")
    _write(work, "pkg/b.txt", "b2\n")
    (work / "gone.txt").unlink()
    _write(work, "pkg/new.txt", "new\n")
    sha_b = _commit(work, "B")
    monkeypatch.setattr(settings, "repo_dir", str(srv))
    monkeypatch.setattr(git_version, "_BOOT_SHA", sha_a[:7], raising=False)
    return work, srv, sha_a, sha_b


def _pull() -> dict:
    return asyncio.run(git_version.pull())


def _no_gits(monkeypatch) -> None:
    """No other git on this box counts: the tests own their repositories."""
    monkeypatch.setattr(git_lock, "git_processes", lambda repo: [])


# ─── stale, fresh, live ──────────────────────────────────────────────────


@requires_git
def test_a_stale_index_lock_is_cleaned_up_and_the_pull_goes_on(server, monkeypatch):
    _work, srv, _a, sha_b = server
    lock = srv / ".git" / "index.lock"
    lock.write_bytes(b"")
    _age(lock, 600)

    res = _pull()

    assert res["pulled"] is True, res
    assert _git(srv, "rev-parse", "HEAD") == sha_b
    assert res["notice"] == git_lock.INTERRUPTED_NOTICE
    assert "an earlier update was interrupted; Domovoi cleaned up and is trying again" == res["notice"]
    assert not lock.exists()


@requires_git
def test_a_pull_with_nothing_to_clean_up_has_no_notice(server):
    res = _pull()
    assert res["pulled"] is True and res["notice"] is None


@requires_git
def test_a_fresh_lock_stays_and_the_card_says_so_in_plain_words(server, monkeypatch):
    _no_gits(monkeypatch)
    _work, srv, sha_a, _b = server
    lock = srv / ".git" / "index.lock"
    lock.write_bytes(b"")   # a moment old: its git may have just started

    res = _pull()

    assert res["pulled"] is False
    assert res["error"] == git_lock.BUSY_MESSAGE
    assert "Unable to create" not in res["error"] and "hint:" not in res["error"]
    assert lock.exists()
    assert _git(srv, "rev-parse", "HEAD") == sha_a


@requires_git
def test_a_lock_a_running_git_holds_is_never_removed(server):
    """git commit -a holds index.lock while its editor is open: a live
    git, in the checkout, with a lock made to look old."""
    _work, srv, sha_a, _b = server
    env = {**os.environ, **_ENV, "GIT_EDITOR": f'"{sys.executable}" -c "import time; time.sleep(60)"'}
    holder = subprocess.Popen(["git", "commit", "-a", "--allow-empty"], cwd=srv, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lock = srv / ".git" / "index.lock"
    try:
        deadline = time.monotonic() + 20
        while not lock.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert lock.exists(), "git commit never took the lock"
        _age(lock, 3600)

        seen = git_lock.clear_stale_locks(srv)
        assert seen["live"] == ["index.lock"] and seen["removed"] == []
        res = _pull()
        assert res["pulled"] is False and res["error"] == git_lock.BUSY_MESSAGE
        assert lock.exists()
        assert _git(srv, "rev-parse", "HEAD") == sha_a
    finally:
        git_run._kill_tree(holder)
        holder.wait(timeout=10)


@requires_git
def test_a_box_that_cant_see_its_processes_removes_nothing(server, monkeypatch):
    monkeypatch.setattr(git_lock, "git_processes", lambda repo: None)
    _work, srv, _a, _b = server
    lock = srv / ".git" / "index.lock"
    lock.write_bytes(b"")
    _age(lock, 3600)
    assert git_lock.clear_stale_locks(srv)["unknown"] == ["index.lock"]
    assert lock.exists()


@requires_git
def test_a_stale_ref_lock_goes_too(server, monkeypatch):
    _no_gits(monkeypatch)
    _work, srv, _a, _b = server
    ref_lock = srv / ".git" / "refs" / "heads" / "main.lock"
    ref_lock.write_bytes(b"")
    _age(ref_lock, 600)
    head_lock = srv / ".git" / "HEAD.lock"
    head_lock.write_bytes(b"")
    _age(head_lock, 600)

    out = git_lock.clear_stale_locks(srv)

    assert sorted(out["removed"]) == sorted(["HEAD.lock", os.path.join("refs", "heads", "main.lock")])
    assert not ref_lock.exists() and not head_lock.exists()


@requires_git
def test_a_lock_dated_in_the_future_counts_as_old(server, monkeypatch):
    """The clock moved back since (a VM that slept): its age can't be read."""
    _no_gits(monkeypatch)
    _work, srv, _a, _b = server
    lock = srv / ".git" / "index.lock"
    lock.write_bytes(b"")
    _age(lock, -3600)
    assert git_lock.clear_stale_locks(srv)["removed"] == ["index.lock"]


def test_a_lock_replaced_in_between_stays(tmp_path):
    """Looked at, then another git's lock took its place: not removed."""
    lock = tmp_path / "index.lock"
    lock.write_bytes(b"old")
    seen = os.lstat(lock)
    lock.unlink()
    lock.write_bytes(b"new, another git's")
    os.utime(lock, ns=(seen.st_mtime_ns + 5_000_000_000, seen.st_mtime_ns + 5_000_000_000))
    assert git_lock._remove(lock, seen) is False
    assert lock.exists()


@requires_git
def test_the_update_check_cleans_up_a_stale_lock_too(server, monkeypatch):
    _no_gits(monkeypatch)
    _work, srv, _a, _b = server
    lock = srv / ".git" / "index.lock"
    lock.write_bytes(b"")
    _age(lock, 600)
    res = asyncio.run(git_version.commits_behind())
    assert res["behind"] == 1
    assert not lock.exists()


# ─── a killed fast-forward: a torn tree ──────────────────────────────────


def _tear(srv: Path, *, with_lock: bool = True) -> None:
    """What a fast-forward to B killed partway leaves: a.txt and pkg/b.txt
    already B's, gone.txt already deleted, pkg/new.txt written (untracked:
    the new index never landed), and its index.lock."""
    _write(srv, "a.txt", "a2\n")
    _write(srv, "pkg/b.txt", "b2\n")
    (srv / "gone.txt").unlink()
    _write(srv, "pkg/new.txt", "new\n")
    if with_lock:
        lock = srv / ".git" / "index.lock"
        lock.write_bytes(b"")
        _age(lock, 600)


@requires_git
def test_without_the_repair_git_refuses_the_torn_tree(server):
    """The case being fixed: git's own fast-forward won't go over it."""
    _work, srv, _a, sha_b = server
    _tear(srv, with_lock=False)
    _git(srv, "fetch", "-q")
    proc = subprocess.run(["git", "merge", "--ff-only", sha_b], cwd=srv, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "would be overwritten" in proc.stderr


@requires_git
def test_a_killed_fast_forward_is_put_back_and_pulled_again(server, monkeypatch):
    _no_gits(monkeypatch)
    _work, srv, _a, sha_b = server
    _tear(srv)

    res = _pull()

    assert res["pulled"] is True, res
    assert res["notice"] == git_lock.INTERRUPTED_NOTICE
    assert _git(srv, "rev-parse", "HEAD") == sha_b
    assert _git(srv, "status", "--porcelain") == ""
    assert (srv / "a.txt").read_bytes() == b"a2\n"
    assert (srv / "pkg" / "new.txt").read_bytes() == b"new\n"
    assert not (srv / "gone.txt").exists()


@requires_git
def test_the_households_own_changes_stay(server, monkeypatch):
    """keep.txt edited by hand (no commit has that content), a.txt torn:
    a.txt is put back, keep.txt stays, and the pull goes on around it."""
    _no_gits(monkeypatch)
    _work, srv, _a, sha_b = server
    _tear(srv)
    _write(srv, "keep.txt", "my own edit\n")

    res = _pull()

    assert res["pulled"] is True, res
    assert _git(srv, "rev-parse", "HEAD") == sha_b
    assert (srv / "keep.txt").read_bytes() == b"my own edit\n"


@requires_git
def test_an_edit_that_is_no_version_of_the_file_is_left_for_git_to_name(server, monkeypatch):
    """a.txt holds content of its own (a half-written file, or an edit):
    nothing puts it back, and git's refusal names it, without its hints."""
    _no_gits(monkeypatch)
    _work, srv, sha_a, _b = server
    _tear(srv)
    _write(srv, "a.txt", "a half-written fi")

    res = _pull()

    assert res["pulled"] is False
    assert "a.txt" in res["error"] and "hint:" not in res["error"]
    assert (srv / "a.txt").read_bytes() == b"a half-written fi"
    assert _git(srv, "rev-parse", "HEAD") == sha_a


@requires_git
def test_the_file_the_killed_git_was_writing_is_put_back_too(server, monkeypatch):
    """Cut short: the first bytes of B's version (a Restart's SIGTERM, a
    kill, in the middle of writing it)."""
    _no_gits(monkeypatch)
    _work, srv, _a, sha_b = server
    _tear(srv)
    _write(srv, "a.txt", "a")   # the start of B's "a2\n"

    res = _pull()

    assert res["pulled"] is True, res
    assert _git(srv, "rev-parse", "HEAD") == sha_b
    assert _git(srv, "status", "--porcelain") == ""


@requires_git
def test_thousands_of_torn_files_fit_the_command_line(server, monkeypatch):
    """A killed fast-forward of a big update: the paths go in runs."""
    _no_gits(monkeypatch)
    work, srv, _a, _b = server
    for i in range(1500):
        _write(work, f"many/dir-with-a-long-name-{i:04d}/file-{i:04d}.txt", f"{i}\n")
    sha_c = _commit(work, "C")
    _git(srv, "fetch", "-q")
    for i in range(1500):
        _write(srv, f"many/dir-with-a-long-name-{i:04d}/file-{i:04d}.txt", f"{i}\n")
    out = git_lock.repair_interrupted_checkout(git_version._run, srv, _git(srv, "rev-parse", "HEAD"), sha_c)
    assert len(out["removed"]) == 1500
    assert _pull()["pulled"] is True


@requires_git
def test_an_untracked_file_of_its_own_is_never_removed(server, monkeypatch):
    _no_gits(monkeypatch)
    _work, srv, _a, sha_b = server
    _write(srv, "pkg/new.txt", "the household's own file\n")
    out = git_lock.repair_interrupted_checkout(git_version._run, srv, _git(srv, "rev-parse", "HEAD"), sha_b)
    assert out == {"restored": [], "removed": []}
    assert (srv / "pkg" / "new.txt").read_bytes() == b"the household's own file\n"


# ─── git_run's timeout cleans up after the git it killed ─────────────────


@requires_git
def test_the_timeout_kill_removes_the_lock_its_git_made(server):
    _work, srv, _a, _b = server
    env = {**_ENV, "GIT_EDITOR": f'"{sys.executable}" -c "import time; time.sleep(60)"'}
    with pytest.raises(subprocess.TimeoutExpired):
        git_run.run(["git", "commit", "-a", "--allow-empty"], cwd=srv, env=env, timeout=3)
    deadline = time.monotonic() + 10
    while (srv / ".git" / "index.lock").exists() and time.monotonic() < deadline:
        # On Windows the tree's last process may take a moment to go.
        git_lock.cleanup_after_kill(srv, time.time() - 30)
        time.sleep(0.2)
    assert not (srv / ".git" / "index.lock").exists()


@requires_git
def test_after_a_kill_an_older_lock_is_not_its_to_remove(server, monkeypatch):
    _no_gits(monkeypatch)
    _work, srv, _a, _b = server
    lock = srv / ".git" / "index.lock"
    lock.write_bytes(b"")
    _age(lock, 30)   # made before the killed git started
    assert git_lock.cleanup_after_kill(srv, time.time() - 5) == []
    assert lock.exists()


@requires_git
def test_a_merge_past_its_bound_says_so_in_plain_words(server, monkeypatch):
    real = git_version._run

    def slow(*args):
        if args[0] == "merge":
            raise subprocess.TimeoutExpired(["git", *args], git_version._MERGE_TIMEOUT_SEC)
        return real(*args)

    monkeypatch.setattr(git_version, "_run", slow)
    res = _pull()
    assert res["pulled"] is False
    assert res["error"].startswith("the update took longer than 45 s and was stopped; Domovoi cleaned up")


# ─── the update unit's checkout lock ─────────────────────────────────────


@requires_git
@posix_only
def test_a_pull_waits_for_the_update_units_checkout_lock(server, monkeypatch):
    import fcntl

    _work, srv, sha_a, _b = server
    lock = Path(settings.update_result_file).parent / git_version.CHECKOUT_LOCK_NAME
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"")
    os.chmod(lock, 0o644)
    monkeypatch.setattr(git_version, "_CHECKOUT_LOCK_WAIT_SEC", 0.5)
    fd = os.open(lock, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        res = asyncio.run(git_version.pull())
    finally:
        os.close(fd)
    assert res["pulled"] is False
    assert res["error"] == "a restart or update is under way; pull again once it has finished"
    assert _git(srv, "rev-parse", "HEAD") == sha_a

    assert _pull()["pulled"] is True   # released: the next one goes


@requires_git
@posix_only
def test_a_pull_holds_the_checkout_lock_while_it_moves_the_tree(server, monkeypatch):
    import fcntl

    _work, _srv, _a, _b = server
    lock = Path(settings.update_result_file).parent / git_version.CHECKOUT_LOCK_NAME
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"")
    held = []
    real = git_version._run

    def probe(*args):
        if args[0] != "merge":
            return real(*args)
        fd = os.open(lock, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held.append(False)
        except BlockingIOError:
            held.append(True)
        finally:
            os.close(fd)
        return real(*args)

    monkeypatch.setattr(git_version, "_run", probe)
    assert _pull()["pulled"] is True
    assert held == [True]


# ─── git's grafts hint, and its optional locks ───────────────────────────


def test_the_fast_forward_has_its_own_longer_bound(monkeypatch):
    seen = []
    monkeypatch.setattr(git_version.git_run, "run", lambda cmd, **kw: seen.append((cmd[3], kw["timeout"])))
    git_version._run("merge", "--ff-only", "x")
    git_version._run("status", "--porcelain")
    assert seen == [("merge", git_version._MERGE_TIMEOUT_SEC), ("status", git_version._GIT_TIMEOUT_SEC)]


@requires_git
def test_the_cores_git_prints_no_grafts_hint(server):
    _work, _srv, _a, _b = server
    proc = git_version._run("merge", "--ff-only", "no-such-revision")
    assert proc.returncode != 0
    assert "info/grafts" not in proc.stderr and "hint:" not in proc.stderr


def test_every_core_git_skips_optional_locks():
    """git status (the version card, every minute) never takes index.lock."""
    assert git_version._GIT_ENV["GIT_OPTIONAL_LOCKS"] == "0"


def test_hints_never_reach_the_card():
    stderr = ("hint: Support for <GIT_DIR>/info/grafts is deprecated\nhint: and will be removed\n"
              "error: Your local changes to the following files would be overwritten by merge:\n\ta.txt\n")
    assert git_lock.without_hints(stderr).startswith("error: Your local changes")


@pytest.mark.parametrize("stderr", [
    "fatal: Unable to create '/opt/domovoi/.git/index.lock': File exists.\n\nAnother git process seems to be running",
    "error: cannot lock ref 'refs/heads/main': Unable to create '/opt/domovoi/.git/refs/heads/main.lock': File exists.",
])
def test_a_lock_refusal_is_said_in_plain_words(stderr):
    proc = subprocess.CompletedProcess(["git"], 128, "", "hint: x\n" + stderr)
    assert git_version._git_failure(proc, "git pull failed") == git_lock.BUSY_MESSAGE


# ─── the bash side carries the same block ────────────────────────────────

BEGIN = "# >>> stale-git-lock block >>>\n"
END = "# <<< stale-git-lock block <<<\n"


def _block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and text.count(END) == 1, path.name
    return text[text.index(BEGIN):text.index(END) + len(END)]


def test_apply_update_and_provision_carry_the_same_block():
    assert _block(APPLY) == _block(PROVISION)


def test_the_bash_threshold_is_the_cores():
    assert f"GIT_LOCK_STALE_SEC={git_lock.STALE_AFTER_SEC:g}\n" in _block(APPLY)


def test_root_never_removes_a_lock_itself():
    """The checkout belongs to the service user: the removal runs as them."""
    block = _block(APPLY)
    assert '"$GL_AS" rm -f -- "$f"' in block
    assert block.count(" rm ") == 1
    assert "GL_AS=as_user\n" in APPLY.read_text(encoding="utf-8")
    assert "GL_AS=as_svc\n" in PROVISION.read_text(encoding="utf-8")
