"""The core's half of the dashboard update pipeline (docs/LINUX_HOST.md,
"Updates from the dashboard").

Three pieces live in the core; the rest is scripts/linux/apply-update.sh,
run as root by domovoi-update.service (its own harness is
test_apply_update_script.py):

* `git_version.pull()` records the SHA an update should roll back to;
* `self_restart` starts the update unit instead of bouncing core and web,
  but only when the unit is installed, and never downgrades silently;
* `GET /v1/admin/version` reports the unit's last result and the bad SHA
  the panel must stop offering.

No DB, no sudo, no systemd: git runs for real against throwaway repos, and
every privileged probe is faked.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi import git_version, self_restart
from domovoi.config import settings

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(cwd: Path, *args: str) -> str:
    # GIT_CONFIG_GLOBAL comes from the fixture (an empty file): no user
    # hooks, signing or autocrlf leak in.
    env = {**os.environ, **_GIT_ENV}
    out = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / f"{name}.txt").write_text(name)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream_and_server(tmp_path, monkeypatch):
    """An upstream repo with commit A, and the server's clone of it at A.
    Returns (upstream_work, server, sha_a); settings.repo_dir → server."""
    # An empty global config, for these helpers and for the core's own git
    # calls alike.
    (tmp_path / "gitconfig").touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    sha_a = _commit(work, "a")
    server = tmp_path / "server"
    _git(tmp_path, "clone", "-q", str(work), str(server))
    monkeypatch.setattr(settings, "repo_dir", str(server))
    monkeypatch.setattr(git_version, "_BOOT_SHA", None, raising=False)
    return work, server, sha_a


def _boot_at(sha: str | None) -> None:
    git_version._BOOT_SHA = sha[:7] if sha else None


def _prev_file() -> Path:
    return Path(settings.update_state_dir) / git_version.PREV_SHA_FILE


# ─── git_version: the pre-pull SHA ────────────────────────────────────────


@requires_git
def test_a_pull_records_the_sha_it_moved_away_from(upstream_and_server):
    work, _server, sha_a = upstream_and_server
    _boot_at(sha_a)
    _commit(work, "b")

    res = asyncio.run(git_version.pull())

    assert res["pulled"] is True
    assert res["prev_sha"] == sha_a
    assert _prev_file().read_bytes() == f"{sha_a}\n".encode()   # full SHA, LF only


@requires_git
def test_two_pulls_before_a_restart_keep_the_running_sha(upstream_and_server):
    """ORIG_HEAD would name B after the second pull, but the process, its
    venv and its database are still at A — A is what a rollback needs."""
    work, _server, sha_a = upstream_and_server
    _boot_at(sha_a)
    _commit(work, "b")
    asyncio.run(git_version.pull())
    _commit(work, "c")

    res = asyncio.run(git_version.pull())

    assert res["prev_sha"] == sha_a
    assert _prev_file().read_text().strip() == sha_a


@requires_git
def test_after_the_restart_the_next_pull_records_the_new_baseline(upstream_and_server):
    work, _server, _sha_a = upstream_and_server
    sha_b = _commit(work, "b")
    _boot_at(None)
    asyncio.run(git_version.pull())
    _boot_at(sha_b)                     # restarted onto B
    _commit(work, "c")

    res = asyncio.run(git_version.pull())

    assert res["prev_sha"] == sha_b


@requires_git
def test_an_unknown_running_sha_falls_back_to_the_pre_pull_head(upstream_and_server):
    work, _server, sha_a = upstream_and_server
    _boot_at(None)                      # stub/test context: boot never captured
    _commit(work, "b")

    assert asyncio.run(git_version.pull())["prev_sha"] == sha_a


@requires_git
def test_a_running_sha_that_is_not_an_ancestor_is_not_used(upstream_and_server, tmp_path):
    """Someone reset the tree by hand: the running commit is off the new
    line of history, so it can't be what the update moves forward from."""
    work, server, sha_a = upstream_and_server
    _git(server, "checkout", "-q", "-b", "side")
    side = _commit(server, "side")
    _git(server, "checkout", "-q", "main")
    _boot_at(side)
    _commit(work, "b")

    assert asyncio.run(git_version.pull())["prev_sha"] == sha_a


@requires_git
def test_a_failed_pull_records_nothing(upstream_and_server):
    work, server, sha_a = upstream_and_server
    _boot_at(sha_a)
    _commit(work, "b")
    (server / "b.txt").write_text("local, untracked, in the way")

    res = asyncio.run(git_version.pull())

    assert res["pulled"] is False
    assert not _prev_file().exists()


def test_recording_never_breaks_the_pull(monkeypatch, tmp_path):
    """An unwritable state dir is logged; the pull still answers."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(settings, "update_state_dir", str(blocker / "update"))

    git_version._write_prev_sha("a" * 40)            # must not raise
    assert not (blocker.parent / "update").exists()


@requires_git
def test_check_reports_the_upstream_sha(upstream_and_server):
    """The panel compares it with bad_sha to stop offering a rolled-back
    commit."""
    work, _server, _sha_a = upstream_and_server
    sha_b = _commit(work, "b")

    res = asyncio.run(git_version.commits_behind())

    assert res["upstream"] is True
    assert res["behind"] == 1
    assert res["upstream_sha"] == sha_b


# ─── self_restart: which restart, and never a silent downgrade ────────────


BOTH_PRESENT = {"systemctl": "/usr/bin/systemctl", "sudo": "/usr/bin/sudo"}


@pytest.fixture
def unit_dirs(tmp_path, monkeypatch):
    """Two fake unit directories, /etc-like first. Detection is POSIX-only."""
    etc = tmp_path / "etc-systemd"
    lib = tmp_path / "lib-systemd"
    etc.mkdir()
    lib.mkdir()
    monkeypatch.setattr(self_restart, "_UNIT_DIRS", (str(etc), str(lib)))
    monkeypatch.setattr(self_restart, "_WINDOWS", False)
    monkeypatch.setattr(self_restart, "_cap_cache", None)
    return etc, lib


def _install_unit(directory: Path) -> Path:
    path = directory / self_restart.UPDATE_UNIT
    path.write_text("[Service]\nType=oneshot\n")
    return path


def _fake_sudo(monkeypatch, allowed: set[tuple[str, ...]] | None, record: list):
    """sudo that grants exactly the systemctl argument tuples in `allowed`
    (None = everything)."""
    monkeypatch.setattr(self_restart.shutil, "which", lambda n: BOTH_PRESENT.get(n))

    def run(cmd, **kw):
        record.append(cmd)
        args = tuple(cmd[cmd.index("/usr/bin/systemctl") + 1:])
        rc = 0 if allowed is None or args in allowed else 1
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="")

    monkeypatch.setattr(self_restart.subprocess, "run", run)


START_UPDATE = ("--no-block", "start", "domovoi-update.service")
LEGACY = ("--no-block", "restart", "domovoi-core.service", "domovoi-web.service")


def test_no_unit_means_todays_restart(unit_dirs):
    assert self_restart.update_unit_installed() is False
    assert self_restart.restart_mode() == "restart"


def test_unit_file_in_any_search_dir_is_detected(unit_dirs):
    _etc, lib = unit_dirs
    _install_unit(lib)
    assert self_restart.update_unit_installed() is True
    assert self_restart.restart_mode() == "update"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege on Windows")
def test_a_masked_unit_is_not_installed(unit_dirs):
    etc, lib = unit_dirs
    _install_unit(lib)
    (etc / self_restart.UPDATE_UNIT).symlink_to(os.devnull)   # systemctl mask
    assert self_restart.update_unit_installed() is False


def test_an_empty_unit_file_is_masked_too(unit_dirs):
    """systemd reads a zero-byte unit file as masked, like a /dev/null
    link, and so must the button."""
    etc, lib = unit_dirs
    _install_unit(lib)
    (etc / self_restart.UPDATE_UNIT).write_bytes(b"")
    assert self_restart.update_unit_installed() is False
    assert self_restart.restart_mode() == "restart"


def test_the_suite_never_sees_the_hosts_real_unit():
    """conftest points the unit search path at an empty dir, so a Linux
    box with domovoi-update.service installed can still run the restart
    tests that expect today's plain bounce."""
    assert all("no-systemd-units" in d for d in self_restart._UNIT_DIRS)
    assert self_restart.restart_mode() == "restart"


def test_detection_never_runs_sudo_or_systemctl(unit_dirs, monkeypatch):
    _install_unit(unit_dirs[0])

    def boom(*a, **kw):
        raise AssertionError("detection must be a plain file check")

    monkeypatch.setattr(self_restart.subprocess, "run", boom)
    assert self_restart.restart_mode() == "update"


def test_windows_never_reports_the_unit(unit_dirs, monkeypatch):
    _install_unit(unit_dirs[0])
    monkeypatch.setattr(self_restart, "_WINDOWS", True)
    assert self_restart.restart_mode() == "restart"


def test_update_mode_probes_and_fires_the_start(unit_dirs, monkeypatch):
    _install_unit(unit_dirs[0])
    calls: list[list[str]] = []
    _fake_sudo(monkeypatch, {START_UPDATE}, calls)
    monkeypatch.setattr(self_restart, "_RESTART_DELAY_SEC", 0.01)

    async def go():
        res = await self_restart.restart()
        await asyncio.sleep(0.15)
        return res

    res = asyncio.run(go())
    assert res["ok"] is True
    assert res["mode"] == "update"
    assert res["units"] == ["domovoi-update.service"]
    assert calls[0] == ["/usr/bin/sudo", "-n", "-l", "/usr/bin/systemctl", *START_UPDATE]
    assert calls[-1] == ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", *START_UPDATE]


def test_unit_without_its_grant_is_incapable_not_downgraded(unit_dirs, monkeypatch):
    """Only the legacy grant exists. Falling back to a plain bounce would
    load new code without its dependencies or migrations — the incident
    class this unit exists to end. Say so instead."""
    _install_unit(unit_dirs[0])
    calls: list[list[str]] = []
    _fake_sudo(monkeypatch, {LEGACY}, calls)

    res = asyncio.run(self_restart.restart())

    assert res["ok"] is False
    assert res["mode"] == "update"
    assert "domovoi-update.service" in res["error"] and "sudoers" in res["error"]
    assert all("-l" in c for c in calls), "nothing may fire"
    assert not any("restart" in c for c in calls), "no legacy fallback"


def test_without_the_unit_the_command_is_exactly_todays(unit_dirs, monkeypatch):
    calls: list[list[str]] = []
    _fake_sudo(monkeypatch, {LEGACY}, calls)
    monkeypatch.setattr(self_restart, "_RESTART_DELAY_SEC", 0.01)

    async def go():
        res = await self_restart.restart()
        await asyncio.sleep(0.15)
        return res

    res = asyncio.run(go())
    assert res["ok"] is True and res["mode"] == "restart"
    assert res["units"] == ["domovoi-core.service", "domovoi-web.service"]
    assert calls[-1] == ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", *LEGACY]


def test_installing_the_unit_invalidates_the_cached_probe(unit_dirs, monkeypatch):
    calls: list[list[str]] = []
    _fake_sudo(monkeypatch, {LEGACY}, calls)
    assert asyncio.run(self_restart.capable_async()) == (True, None)

    _install_unit(unit_dirs[0])                      # admin installs the unit
    ok, why = asyncio.run(self_restart.capable_async())

    assert ok is False and "domovoi-update.service" in why
    assert len(calls) == 2, "the new mode must be probed, not served from cache"


# ─── GET /v1/admin/version: the last result ───────────────────────────────


def _write_result(doc: dict | str) -> Path:
    path = Path(settings.update_result_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return path


ROLLED_BACK = {
    "status": "rolled_back",
    "mode": "update",
    "from_sha": "a" * 40,
    "to_sha": "b" * 40,
    "prev_source": "applied",
    "bad_sha": "b" * 40,
    "started_at": "2026-09-25T10:00:00Z",
    "finished_at": "2026-09-25T10:02:00Z",
    "duration_sec": 120.5,
    "deps_changed": True,
    "mpd_changed": False,
    "migrations_before": 13,
    "migrations_after": 14,
    "backup": "/var/lib/domovoi-update/backups/pre-bbbbbbbbbbbb-20260925T100000Z.dump",
    "db_restored": True,
    "error": "update to bbbbbbbbbbbb failed at health and was rolled back",
    "steps": [
        {"name": "backup", "status": "ok", "duration_sec": 1.2, "detail": None},
        {"name": "health", "status": "failed", "duration_sec": 120.0,
         "detail": "Traceback (most recent call last): /opt/domovoi/..."},
    ],
}


@pytest.fixture
def stub_version_probes(monkeypatch):
    async def sha():
        return "aaaaaaa"

    async def cap():
        return (False, "test")

    monkeypatch.setattr(git_version, "current_sha", sha)
    monkeypatch.setattr(self_restart, "capable_async", cap)
    monkeypatch.setattr(self_restart, "restart_mode", lambda: "update")


def test_version_reports_the_last_update_and_its_bad_sha(stub_version_probes):
    _write_result(ROLLED_BACK)

    state = asyncio.run(git_version.version_state())

    last = state["last_update"]
    assert last["status"] == "rolled_back"
    assert last["from_sha"] == "a" * 40 and last["to_sha"] == "b" * 40
    assert last["db_restored"] is True
    assert state["bad_sha"] == "b" * 40
    assert state["restart_mode"] == "update"


def test_version_keeps_the_scripts_private_details_on_the_box(stub_version_probes):
    """The endpoint is open: backup paths and raw step output stay local."""
    _write_result(ROLLED_BACK)

    last = asyncio.run(git_version.version_state())["last_update"]

    assert "backup" not in last
    assert last["steps"] == [
        {"name": "backup", "status": "ok", "duration_sec": 1.2},
        {"name": "health", "status": "failed", "duration_sec": 120.0},
    ]


def test_no_unit_no_result(stub_version_probes):
    state = asyncio.run(git_version.version_state())
    assert state["last_update"] is None
    assert state["bad_sha"] is None


@pytest.mark.parametrize("content", [
    "{not json",
    "[]",
    json.dumps({"no": "status"}),
    json.dumps({**ROLLED_BACK, "bad_sha": "not-a-sha"}),
])
def test_a_broken_result_file_never_breaks_the_panel(stub_version_probes, content):
    _write_result(content)
    state = asyncio.run(git_version.version_state())
    assert state["bad_sha"] is None
    last = state["last_update"]
    assert last is None or last["status"] == "rolled_back"


def test_a_stray_byte_in_the_error_keeps_the_result_and_its_bad_sha(stub_version_probes):
    """The script cuts pip/docker output to length with cut(1), which on
    Ubuntu counts bytes, so pip's "╰─>" can be split mid-character. That
    must cost one replacement character, not the whole result: losing
    bad_sha would put the rolled-back commit back on offer."""
    doc = json.dumps({**ROLLED_BACK, "error": "sync-deps failed (exit 1): XX"})
    # "─" is three bytes; keep two of them, as a byte-counting cut would.
    raw = doc.encode("utf-8").replace(b"XX", "× ╰─".encode("utf-8")[:-1])
    path = Path(settings.update_result_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)

    state = asyncio.run(git_version.version_state())

    assert state["bad_sha"] == "b" * 40
    assert state["last_update"]["status"] == "rolled_back"
    assert state["last_update"]["error"].startswith("sync-deps failed (exit 1): ×")


def test_an_oversized_result_is_ignored(stub_version_probes):
    _write_result({**ROLLED_BACK, "error": "x" * (git_version._LAST_UPDATE_MAX_BYTES + 1)})
    assert asyncio.run(git_version.version_state())["last_update"] is None


def test_a_long_error_is_truncated(stub_version_probes):
    _write_result({**ROLLED_BACK, "error": "e" * 5000})
    last = asyncio.run(git_version.version_state())["last_update"]
    assert len(last["error"]) == git_version._ERROR_MAX_CHARS


def test_the_endpoint_serves_it(stub_version_probes):
    from domovoi import main

    _write_result(ROLLED_BACK)
    body = asyncio.run(main.admin_version())

    assert body["last_update"]["status"] == "rolled_back"
    assert body["bad_sha"] == "b" * 40
    assert body["restart_mode"] == "update"
