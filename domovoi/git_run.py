"""Running git from the Domovoi services: it never asks, and it stops on time.

Every git the core runs goes through :func:`run` (the update check and pull
in :mod:`domovoi.git_version`, the container guard's compose read, the wake
trainer's source id, the satellite media cache's LED-tool fetch). The web
process runs no git.

Two things went wrong without it (F-051, rc2 smoke on a Windows-hosted
core, 2026-10-16):

* **git asked.** "Check for updates" ran ``git fetch`` on an SSH origin
  from a home with no ``known_hosts``; ssh asked "The authenticity of host
  ... can't be established" through Git for Windows' ``git-askpass.exe``,
  a dialog on whoever's desktop was logged in, once per press. A service
  has nobody to answer: now nothing asks. :data:`NO_PROMPT_ENV` turns off
  git's terminal prompt, every askpass program (an empty ``GIT_ASKPASS``
  wins over ``core.askPass`` and ``SSH_ASKPASS``; ``SSH_ASKPASS_REQUIRE``
  tells ssh itself never to run one) and Git Credential Manager's windows;
  a ``GIT_SSH_COMMAND`` already set gets ``-o BatchMode=yes``. git runs
  with no terminal either (its own session on Linux, no console on
  Windows), so ssh can't fall back to asking there. A credential helper
  that answers without asking (a stored token) still works: the helpers
  are not turned off, only their questions.
* **the timeout didn't hold.** ``subprocess.run(timeout=...)`` kills git
  when time is up, but on Windows it then waits for git's output pipes to
  close, and ssh and the askpass dialog (git's children) held them open:
  a 10 s timeout ran for minutes, and the web gave up first and blamed
  "domovoi unreachable". :func:`run` kills git's whole process tree when
  time is up and never waits on its output for more than a moment.

A git killed that way can't remove the lock it held (``.git/index.lock``
during a status or a merge), and every git after it would refuse: so the
kill is followed by :func:`domovoi.git_lock.cleanup_after_kill`, which
removes a lock made in ``cwd`` since that git started, once no git works
there (the lock behind a household's stuck Pull, 2026-10-20).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence

log = logging.getLogger(__name__)

#: What every git the core runs gets in its environment (module docstring).
NO_PROMPT_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "SSH_ASKPASS_REQUIRE": "never",
    "GCM_INTERACTIVE": "never",
}

# How long a killed git's output may take to drain before it is given up.
_DRAIN_SEC = 2.0

# Windows: no console window per git (a console-less parent otherwise gets
# a fresh conhost flashing up for every child). 0 elsewhere.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def no_prompt_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """This process's environment, plus ``extra``, plus
    :data:`NO_PROMPT_ENV` (which ``extra`` can't undo), with
    ``-o BatchMode=yes`` added to a ``GIT_SSH_COMMAND`` that lacks it."""
    env = dict(os.environ)
    if extra:
        env.update(extra)
    env.update(NO_PROMPT_ENV)
    ssh = env.get("GIT_SSH_COMMAND")
    if ssh and "batchmode" not in ssh.lower():
        env["GIT_SSH_COMMAND"] = f"{ssh} -o BatchMode=yes"
    return env


def _popen_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": _NO_WINDOW}
    # Its own session: no controlling terminal for ssh to ask on, and one
    # process group to kill.
    return {"start_new_session": True}


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill git and everything it started (ssh, helpers, an askpass)."""
    if sys.platform != "win32":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except (OSError, ValueError):
            pass
    else:
        try:
            import psutil

            children = psutil.Process(proc.pid).children(recursive=True)
        except Exception:  # noqa: BLE001 - gone already, or no psutil
            children = []
        for child in children:
            try:
                child.kill()
            except Exception:  # noqa: BLE001 - exited meanwhile
                pass
    try:
        proc.kill()
    except OSError:
        pass


def run(
    cmd: Sequence[str],
    *,
    timeout: float,
    cwd: str | os.PathLike | None = None,
    env: Mapping[str, str] | None = None,
    binary: bool = False,
    **_ignored: object,
) -> subprocess.CompletedProcess:
    """Run ``cmd`` (``cmd[0]`` is git) and capture its text output, like
    ``subprocess.run(cmd, capture_output=True, text=True, timeout=...,
    check=False)``, so it drops in where that was called (extra keywords
    such as ``capture_output`` are accepted and ignored).

    ``env`` is added to this process's environment; the no-prompt settings
    (:func:`no_prompt_env`) always win. ``binary`` gives the output as
    bytes (a blob's content, git_lock), not text. Raises ``FileNotFoundError`` with
    no git, and ``subprocess.TimeoutExpired`` once ``timeout`` has passed:
    by then git and everything it started have been killed, and the locks
    it left in ``cwd`` removed."""
    started_at = time.time()
    proc = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **({} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}),
        env=no_prompt_env(env),
        **_popen_kwargs(),
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out = err = None
        try:
            proc.wait(timeout=_DRAIN_SEC)
        except subprocess.TimeoutExpired:
            pass
        try:
            out, err = proc.communicate(timeout=_DRAIN_SEC)
        except subprocess.TimeoutExpired:
            # Something outside the tree still holds the pipes: leave the
            # output behind rather than wait on it.
            log.warning("git %s: its output was still open after it was killed", cmd[1:2])
        except (OSError, ValueError):
            pass
        if cwd is not None:
            from domovoi import git_lock

            git_lock.cleanup_after_kill(cwd, started_at)
        raise subprocess.TimeoutExpired(list(cmd), timeout, output=out, stderr=err) from None
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, err)
