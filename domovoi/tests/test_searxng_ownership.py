"""The search helper's folder in the checkout, and the pull it blocked.

2026-10-22: a household's Windows install stuck at 1feb00c, 291 commits
behind. Settings > About & updates > Pull said "an earlier update was
interrupted; Domovoi cleaned up and is trying again. pull failed: error:
unable to unlink old 'domovoi/searxng/settings.yml': Permission denied".

The cause: docker-compose.yml bind-mounted the checkout's domovoi/searxng/
at /etc/searxng, read-write, and the pinned SearXNG image's entrypoint (as
root, FORCE_OWNERSHIP on by default) chowns that to its own user, uid 977.
The service user's fast-forward could then not replace settings.yml; it
stopped there, after rewriting the rest of the tree (385 files changed, 373
added, eight migrations among them), with HEAD and the index left behind.

These hold the fix:

* the compose entry is read-only and FORCE_OWNERSHIP is off, so nothing in
  the container can take the folder (the settings revision moves, so every
  box recreates the container with it);
* a fast-forward that fails or is stopped partway puts back what it wrote,
  so the tree stays HEAD's, and one that stops on that folder says so in
  plain words;
* the bash side (restore_checkout_owner: the same block in apply-update.sh
  and provision.sh) gives a taken folder back, as root, by descriptor. Its
  harnesses run it (scripts/linux/tests: provision owner_*, apply-update
  owner_*).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from domovoi import git_lock, git_version
from domovoi.config import settings

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "domovoi" / "docker-compose.yml"
APPLY = REPO_ROOT / "scripts" / "linux" / "apply-update.sh"
PROVISION = REPO_ROOT / "scripts" / "linux" / "provision.sh"

UNLINK = "error: unable to unlink old 'domovoi/searxng/settings.yml': Permission denied\nUpdating 1feb00cc..35c7d89a\n"

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


# ─── the compose entry ───────────────────────────────────────────────────


def _searxng() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["searxng"]


def test_the_checkouts_folder_is_mounted_read_only():
    assert _searxng()["volumes"] == ["./searxng:/etc/searxng:ro"]


def test_the_entrypoint_is_told_not_to_chown():
    # The image's entrypoint: `[ "${FORCE_OWNERSHIP:-true}" = true ] && [ "$(id -u)" -eq 0 ]`
    # then `chown -R searxng:searxng "$target"`; anything but "true" skips it.
    assert _searxng()["environment"]["FORCE_OWNERSHIP"] == "false"


def test_the_settings_revision_moved_with_the_mount():
    """A running container keeps its old mount until it is recreated: the
    revision is what makes the next update recreate it (2 was the
    Wikipedia ranking; 3 is the read-only mount)."""
    assert _searxng()["environment"]["DOMOVOI_SEARXNG_SETTINGS_REV"] == "3"


def test_provisions_read_only_entry_matches_the_compose_file():
    """provision.sh starts the helper with the same entry on top of an older
    checkout's compose file (until the dashboard's update brings it), and
    recognises the new one."""
    text = PROVISION.read_text(encoding="utf-8")
    start = text.index("SEARXNG_READONLY_OVERRIDE='") + len("SEARXNG_READONLY_OVERRIDE='")
    override = yaml.safe_load(text[start:text.index("'", start)])["services"]["searxng"]
    assert override["volumes"] == _searxng()["volumes"]
    assert override["environment"]["FORCE_OWNERSHIP"] == _searxng()["environment"]["FORCE_OWNERSHIP"]
    # The revision stays the checkout's own: the update that brings the new
    # entry then recreates the container with it.
    assert "DOMOVOI_SEARXNG_SETTINGS_REV" not in override["environment"]
    assert "-f docker-compose.yml -f - up -d --no-deps searxng" in text


# ─── the pull ────────────────────────────────────────────────────────────


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


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Upstream at A, the server's clone at A, and B upstream: B changes
    domovoi/searxng/settings.yml and pyproject.toml, adds a migration and
    deletes gone.txt. Returns (server, sha_a, sha_b)."""
    (tmp_path / "gitconfig").touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("DOMOVOI_ALLOWED_SIGNERS", str(tmp_path / "no-such-signers"))
    monkeypatch.setattr(settings, "update_result_file", str(tmp_path / "update" / "last-result.json"))
    monkeypatch.setattr(git_lock, "git_processes", lambda repo: [])
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _write(work, "domovoi/searxng/settings.yml", "use_default_settings: true\n")
    _write(work, "pyproject.toml", "[project]\nname = 'domovoi'\n")
    _write(work, "domovoi/db/migrations/V001__a.sql", "select 1;\n")
    _write(work, "gone.txt", "gone\n")
    sha_a = _commit(work, "A")
    srv = tmp_path / "server"
    _git(tmp_path, "clone", "-q", str(work), str(srv))
    _write(work, "domovoi/searxng/settings.yml", "use_default_settings: true\nsearch: {formats: [json]}\n")
    _write(work, "pyproject.toml", "[project]\nname = 'domovoi'\nversion = '2'\n")
    _write(work, "domovoi/db/migrations/V002__b.sql", "select 2;\n")
    (work / "gone.txt").unlink()
    sha_b = _commit(work, "B")
    monkeypatch.setattr(settings, "repo_dir", str(srv))
    return srv, sha_a, sha_b


def _stop_on_settings(monkeypatch, srv: Path, sha_b: str, *, stderr: str = UNLINK) -> None:
    """The merge as git does it when it can't unlink settings.yml: every
    other file of B written (the new migration untracked, gone.txt
    deleted), settings.yml left, HEAD and the index left at A, exit 1."""
    real = git_version._run

    def run(*args: str):
        if args[:2] == ("merge", "--ff-only"):
            _write(srv, "pyproject.toml", "[project]\nname = 'domovoi'\nversion = '2'\n")
            _write(srv, "domovoi/db/migrations/V002__b.sql", "select 2;\n")
            (srv / "gone.txt").unlink()
            return subprocess.CompletedProcess(["git", *args], 1, "", stderr)
        return real(*args)

    monkeypatch.setattr(git_version, "_run", run)


def _pull() -> dict:
    return asyncio.run(git_version.pull())


@requires_git
def test_a_pull_stopped_by_the_folder_says_so_in_plain_words(server, monkeypatch):
    srv, _a, sha_b = server
    _stop_on_settings(monkeypatch, srv, sha_b)

    res = _pull()

    assert res["pulled"] is False
    assert res["error"] == git_version.SEARXNG_FOLDER_MESSAGE
    assert res["error"] == (
        "the search helper's settings folder isn't writable; "
        "Restart Domovoi (or run Setup again on Windows) fixes it"
    )
    assert "Permission denied" not in res["error"]


@requires_git
def test_what_the_failed_fast_forward_wrote_is_put_back_at_once(server, monkeypatch):
    """Not left for the next pull: a Restart or a reboot before it would
    run B's code on A's dependencies, and Flyway would apply V002 from the
    tree without the update's backup."""
    srv, sha_a, sha_b = server
    _stop_on_settings(monkeypatch, srv, sha_b)

    _pull()

    assert _git(srv, "rev-parse", "HEAD") == sha_a
    assert _git(srv, "status", "--porcelain") == ""
    assert not (srv / "domovoi" / "db" / "migrations" / "V002__b.sql").exists()
    assert (srv / "gone.txt").read_bytes() == b"gone\n"


@requires_git
def test_another_failure_keeps_gits_own_words_and_is_put_back_too(server, monkeypatch):
    srv, sha_a, sha_b = server
    _stop_on_settings(monkeypatch, srv, sha_b,
                      stderr="error: unable to unlink old 'pyproject.toml': Read-only file system\n")

    res = _pull()

    assert res["pulled"] is False
    assert res["error"] == "error: unable to unlink old 'pyproject.toml': Read-only file system"
    assert _git(srv, "status", "--porcelain") == ""


@requires_git
def test_a_fast_forward_stopped_at_its_bound_is_put_back_too(server, monkeypatch):
    srv, sha_a, _b = server
    real = git_version._run

    def run(*args: str):
        if args[:2] == ("merge", "--ff-only"):
            _write(srv, "pyproject.toml", "[project]\nname = 'domovoi'\nversion = '2'\n")
            _write(srv, "domovoi/db/migrations/V002__b.sql", "select 2;\n")
            raise subprocess.TimeoutExpired(["git", *args], git_version._MERGE_TIMEOUT_SEC)
        return real(*args)

    monkeypatch.setattr(git_version, "_run", run)

    res = _pull()

    assert res["pulled"] is False
    assert "cleaned up after it" in res["error"]
    assert _git(srv, "status", "--porcelain") == ""


@requires_git
def test_the_households_own_edit_stays_when_the_pull_fails(server, monkeypatch):
    """The put-back takes only files holding a version from the update."""
    srv, _a, _b = server
    _write(srv, "gone.txt", "my own edit\n")
    real = git_version._run

    def run(*args: str):
        if args[:2] == ("merge", "--ff-only"):
            _write(srv, "pyproject.toml", "[project]\nname = 'domovoi'\nversion = '2'\n")
            return subprocess.CompletedProcess(["git", *args], 1, "", UNLINK)
        return real(*args)

    monkeypatch.setattr(git_version, "_run", run)

    _pull()

    assert (srv / "gone.txt").read_bytes() == b"my own edit\n"
    assert (srv / "pyproject.toml").read_bytes() == b"[project]\nname = 'domovoi'\n"


@requires_git
@pytest.mark.skipif(sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs a POSIX folder the test's own user can't write")
def test_the_real_git_failure_end_to_end(server):
    """git itself, with the folder made unwritable (as uid 977's is to the
    service user): the error, the plain message, and a clean tree."""
    srv, sha_a, _b = server
    folder = srv / "domovoi" / "searxng"
    folder.chmod(0o555)
    try:
        res = _pull()
    finally:
        folder.chmod(0o755)
    assert res["pulled"] is False
    assert res["error"] == git_version.SEARXNG_FOLDER_MESSAGE
    assert _git(srv, "rev-parse", "HEAD") == sha_a
    assert _git(srv, "status", "--porcelain") == ""


@requires_git
def test_after_the_folder_is_given_back_the_pull_goes_through(server, monkeypatch):
    srv, _a, sha_b = server
    real = git_version._run
    _stop_on_settings(monkeypatch, srv, sha_b)
    assert _pull()["pulled"] is False
    monkeypatch.setattr(git_version, "_run", real)

    res = _pull()

    assert res["pulled"] is True, res
    assert _git(srv, "rev-parse", "HEAD") == sha_b


@pytest.mark.parametrize(("stderr", "blocked"), [
    (UNLINK, True),
    ("error: unable to create file domovoi/searxng/limiter.toml: Permission denied\n", True),
    ("fatal: cannot create directory at 'domovoi/searxng/x': Permission denied\n", True),
    ("error: unable to unlink old 'pyproject.toml': Permission denied\n", False),
    ("error: Your local changes to the following files would be overwritten by merge:\n\tdomovoi/searxng/settings.yml\n", False),
])
def test_only_the_folders_own_refusal_gets_the_message(stderr, blocked):
    proc = subprocess.CompletedProcess(["git"], 1, "", stderr)
    assert (git_version._git_failure(proc, "git pull failed") == git_version.SEARXNG_FOLDER_MESSAGE) is blocked


# ─── the bash side carries the same block ────────────────────────────────

BEGIN = "# >>> checkout-owner block >>>\n"
END = "# <<< checkout-owner block <<<\n"


def _block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and text.count(END) == 1, path.name
    return text[text.index(BEGIN):text.index(END) + len(END)]


def test_apply_update_and_provision_carry_the_same_block():
    assert _block(APPLY) == _block(PROVISION)


def _snippet() -> str:
    block = _block(APPLY)
    start = block.index("CO_PY='") + len("CO_PY='")
    return block[start:block.index("\n'\n", start)]


def test_the_snippet_is_python_with_no_quote_to_break_the_shell_string():
    snippet = _snippet()
    assert "'" not in snippet
    compile(snippet, "checkout-owner", "exec")


def test_root_changes_an_owner_only_by_descriptor():
    """No chown by name in the shell, none in the Python, every component
    opened without following a link; the list of paths comes from git as
    the service user."""
    block = _block(APPLY)
    snippet = _snippet()
    assert "os.fchown(fd, uid, gid)" in snippet
    assert "os.chown" not in snippet and "lchown" not in snippet
    assert "O_NOFOLLOW" in snippet and "dir_fd=fd" in snippet
    code = [line for line in block.replace(snippet, "").splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in code if "chown" in line]
    assert '"$GL_AS" git -C "$dir" ls-files -z' in block
    assert 'python3 -I -c "$CO_PY"' in block


def test_it_runs_before_every_git_of_the_update_unit_and_in_setups_tidy():
    apply = APPLY.read_text(encoding="utf-8")
    main = apply[apply.index("\nmain() {"):]
    assert main.index('restore_checkout_owner "$REPO_DIR" "$SERVICE_USER"') < main.index("clear_stale_git_locks") \
        < main.index("git_as rev-parse --verify HEAD")
    rollback = apply[apply.index("\nrollback() {"):apply.index("\nmain() {")]
    assert rollback.index("restore_checkout_owner") < rollback.index("reset --keep")
    prov = PROVISION.read_text(encoding="utf-8")
    tidy = prov[prov.index("\ntidy_checkout() {"):]
    assert tidy.index("restore_checkout_owner") < tidy.index("repair_torn_checkout")
    assert 'searxng) if ! dry; then restore_checkout_owner "$CHECKOUT" "$SVC_USER"; fi ;;' in prov
