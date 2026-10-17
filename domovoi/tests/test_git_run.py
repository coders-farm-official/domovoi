"""Every git the Domovoi services run never asks anything and stops on time
(F-051, domovoi/git_run.py).

rc2 smoke (2026-10-16), a Windows-hosted core: "Check for updates" ran
``git fetch`` on an SSH origin from a home with no ``known_hosts``. ssh
asked about the host key through Git for Windows' askpass, a dialog on the
desktop, once per press; the call ran past its 10 s timeout, the web gave
up first, and the card said "domovoi unreachable".

The git runs here are real (a local HTTP server that wants a password, a
stand-in ssh that hangs), so what is checked is what git does with the
environment it is given, on this platform. Nothing reaches the network.
"""

from __future__ import annotations

import ast
import asyncio
import http.server
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from domovoi import egress, git_run, git_version

REPO_ROOT = Path(__file__).resolve().parents[2]
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="no git on this host")


# ─── the environment ─────────────────────────────────────────────────────


def test_the_environment_turns_every_prompt_off(monkeypatch) -> None:
    monkeypatch.setenv("GIT_ASKPASS", "/usr/libexec/git-core/git-gui--askpass")
    monkeypatch.setenv("SSH_ASKPASS", "C:/Program Files/Git/mingw64/bin/git-askpass.exe")
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    env = git_run.no_prompt_env({"GIT_TERMINAL_PROMPT": "1", "GIT_NO_REPLACE_OBJECTS": "1"})
    assert env["GIT_TERMINAL_PROMPT"] == "0"           # `extra` can't turn one back on
    assert env["GIT_ASKPASS"] == "" and env["SSH_ASKPASS"] == ""
    assert env["SSH_ASKPASS_REQUIRE"] == "never"
    assert env["GCM_INTERACTIVE"] == "never"
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
    # No ssh command of our own when none is set: core.sshCommand (a deploy
    # key, say) still applies, and nothing above lets ssh ask.
    assert "GIT_SSH_COMMAND" not in env


def test_an_ssh_command_already_set_runs_in_batch_mode(monkeypatch) -> None:
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i ~/.ssh/deploy")
    assert git_run.no_prompt_env()["GIT_SSH_COMMAND"] == "ssh -i ~/.ssh/deploy -o BatchMode=yes"
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    assert git_run.no_prompt_env()["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"


# ─── real git: no askpass, and the timeout holds ─────────────────────────


class _WantsPassword(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's name
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="domovoi-test"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@needs_git
def test_git_never_runs_an_askpass_program(tmp_path, monkeypatch) -> None:
    """A remote that wants a password: git would ask the configured
    askpass program (``core.askPass``, the way Git for Windows' dialog is
    reached). A stand-in that only leaves a mark shows whether it ran."""
    for var in ("GIT_ASKPASS", "SSH_ASKPASS", "GIT_TERMINAL_PROMPT", "GCM_INTERACTIVE"):
        monkeypatch.delenv(var, raising=False)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _WantsPassword)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    marker = tmp_path / "askpass-ran"
    askpass = tmp_path / "askpass.sh"
    askpass.write_bytes(f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\necho nobody\n".encode())
    askpass.chmod(0o755)
    cmd = ["git", "-c", "credential.helper=", "-c", f"core.askPass={askpass.as_posix()}",
           "ls-remote", f"http://127.0.0.1:{server.server_address[1]}/repo.git"]
    try:
        # The control: the same git without the runner asks.
        plain = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                               stdin=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert plain.returncode != 0
        assert marker.exists(), "the control never reached the askpass program"
        marker.unlink()

        r = git_run.run(cmd, timeout=30)
    finally:
        server.shutdown()
    assert r.returncode != 0
    assert not marker.exists(), "git ran an askpass program"
    assert "terminal prompts disabled" in r.stderr


@needs_git
def test_a_git_that_hangs_stops_at_its_timeout_and_takes_its_children(tmp_path, monkeypatch) -> None:
    """git's ssh (a stand-in that never answers) is killed with it: the
    call returns at the timeout, and nothing git started outlives it."""
    psutil = pytest.importorskip("psutil")
    pids = tmp_path / "ssh.pids"
    pids.write_text("", encoding="utf-8")
    fake_ssh = tmp_path / "fake_ssh.py"
    fake_ssh.write_text(
        "import os, time\n"
        f"with open({str(pids)!r}, 'a') as f:\n"
        "    f.write(str(os.getpid()) + chr(10))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "GIT_SSH_COMMAND", f'"{Path(sys.executable).as_posix()}" "{fake_ssh.as_posix()}"'
    )
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        git_run.run(["git", "ls-remote", "ssh://git@127.0.0.1/repo.git"], timeout=3)
    took = time.monotonic() - t0
    assert took < 3 + 2 * git_run._DRAIN_SEC + 2, f"the 3 s timeout ran {took:.1f} s"

    started = [int(p) for p in pids.read_text(encoding="utf-8").split()]
    assert started, "git never started the stand-in ssh"

    def gone(pid: int) -> bool:
        try:
            return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not all(gone(p) for p in started):
        time.sleep(0.1)
    assert all(gone(p) for p in started), "git's ssh outlived the timeout"


@needs_git
def test_the_runner_answers_like_subprocess_run(tmp_path) -> None:
    r = git_run.run(["git", "--version"], timeout=30, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("git version")
    with pytest.raises(FileNotFoundError):
        git_run.run([str(tmp_path / "no-such-git")], timeout=5)


# ─── the update check says what went wrong ───────────────────────────────


def _failing_fetch(monkeypatch, stderr: str) -> None:
    def fake_run(*args):
        return subprocess.CompletedProcess(["git", *args], 128, "", stderr)

    monkeypatch.setattr(git_version, "_run", fake_run)


@pytest.mark.parametrize(
    ("stderr", "words"),
    [
        ("Host key verification failed.\r\nfatal: Could not read from remote repository.\n\n"
         "Please make sure you have the correct access rights\nand the repository exists.\n",
         "the update server wants a password or a host key this server doesn't have"),
        ("fatal: could not read Username for 'https://example.test': terminal prompts disabled\n",
         "the update server wants a password or a host key this server doesn't have"),
        ("fatal: unable to access 'https://example.test/domovoi.git/': "
         "Could not resolve host: example.test\n",
         "can't look up the update server"),
        ("fatal: unable to access 'https://example.test/domovoi.git/': "
         "Failed to connect to example.test port 443 after 21 ms: Couldn't connect to server\n",
         "can't reach the update server"),
        ("fatal: something else entirely\n", "git fetch failed"),
    ],
)
def test_a_failed_fetch_is_said_in_plain_words(monkeypatch, stderr, words) -> None:
    _failing_fetch(monkeypatch, stderr)
    with egress.override_policy("always"):
        res = asyncio.run(git_version.fetch())
    assert res["ok"] is False
    assert res["error"].startswith(words), res["error"]
    assert "(git: " in res["error"]                    # git's own last line, after


def test_a_fetch_past_its_timeout_says_so(monkeypatch) -> None:
    def slow(*args):
        raise subprocess.TimeoutExpired(["git", *args], git_version._GIT_TIMEOUT_SEC)

    monkeypatch.setattr(git_version, "_run", slow)
    with egress.override_policy("always"):
        res = asyncio.run(git_version.fetch())
    assert res == {"ok": False,
                   "error": "the update server didn't answer in 10 s (git fetch timed out)"}


def test_the_web_waits_longer_than_the_cores_git(monkeypatch) -> None:
    """The check, the pull and a channel switch: the web's wait outlasts
    the core's git timeouts, so the card shows the core's words, not
    "domovoi unreachable"."""
    from types import SimpleNamespace

    from web.backend.api import config as config_api

    seen: dict[str, float] = {}

    async def post_admin(path, body=None, timeout=30.0, headers=None):
        seen[path] = timeout
        return 200, {"ok": True}

    monkeypatch.setattr(config_api, "post_admin", post_admin)
    request = SimpleNamespace(headers={}, client=None, cookies={})

    async def press_all():
        await config_api.check_version(request)
        await config_api.pull_version(request)
        await config_api.switch_version_channel(
            config_api.VersionChannelRequest(channel="main"), request
        )

    asyncio.run(press_all())
    assert set(seen) == {"/v1/admin/version/check", "/v1/admin/version/pull",
                         "/v1/admin/version/channel"}
    # fetch + tags + the local reads, each at most _GIT_TIMEOUT_SEC
    assert all(t >= 6 * git_version._GIT_TIMEOUT_SEC for t in seen.values()), seen


# ─── no git outside the runner ───────────────────────────────────────────

_SPAWNERS = {"run", "Popen", "check_output", "check_call", "call", "create_subprocess_exec"}


def _git_spawns(path: Path) -> list[int]:
    """Lines where ``path`` hands a literal ``["git", ...]`` (or a list
    whose first item is a name bound to ``shutil.which("git")``) straight
    to a subprocess function."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in _SPAWNERS:
            continue
        owner = fn.value.id if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) else None
        if owner not in ("subprocess", "asyncio"):
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.List) and first.elts and isinstance(first.elts[0], ast.Constant) \
                and first.elts[0].value == "git":
            hits.append(node.lineno)
    return hits


def test_the_services_run_git_only_through_the_runner() -> None:
    offenders = []
    for root in ("domovoi", "web/backend"):
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "tests" in path.relative_to(REPO_ROOT).parts or path.name == "git_run.py":
                continue
            offenders += [f"{path.relative_to(REPO_ROOT)}:{line}" for line in _git_spawns(path)]
    assert offenders == [], f"git run without domovoi.git_run: {offenders}"


def test_the_led_tool_fetch_defaults_to_the_runner() -> None:
    import inspect

    from domovoi.satellite_media import fetchers

    assert inspect.signature(fetchers.fetch_xvf_host).parameters["run"].default is git_run.run
