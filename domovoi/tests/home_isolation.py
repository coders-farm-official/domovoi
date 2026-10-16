"""No test run writes the developer's real ``~/.domovoi``.

Domovoi keeps its state in the home directory of the account it runs as:
``server-identity.json``, ``device-token.txt``, voices, plugin logs, the
satellite's spool and config. Most of those paths are computed ONCE, at
import (``Path.home() / ".domovoi"`` in a module constant or a settings
default), so a fixture that moves ``HOME`` later is too late for them, and
on a developer's PC every test that reached one wrote the real file:
``run/core-ready.json`` and ``logs/core-faulthandler.log`` from a test core
starting up, the satellite's ``log-spool.txt``, ``logs/plugin_<slug>.log``
for every fixture plugin, ``piper_voices/``.

So, before anything imports ``domovoi`` (the repo-root ``conftest.py``
calls :func:`isolate` first thing; the package conftests call it again,
harmlessly, for a run that starts below the root):

* ``HOME`` and ``USERPROFILE`` point at a fresh temp folder of this
  process's own (``domovoi-test-home-<worker>-…``), ``DOMOVOI_HOME`` at
  its ``.domovoi``, and ``HOMEDRIVE``/``HOMEPATH`` (Windows' fallback when
  ``USERPROFILE`` is missing) are dropped. ``Path.home()``, ``~`` and every
  default built from them land there. Under ``pytest -n`` the workers
  inherit the controller's environment and each makes its own.
* An environment variable holding an absolute path inside the real
  ``~/.domovoi`` (``VOICE_MODELS_DIR=C:\\Users\\me\\.domovoi\\…``
  exported in the shell) is dropped, so the default applies instead.
* The real home is remembered in ``DOMOVOI_TEST_REAL_HOME`` (inherited by
  workers and by any pytest a test starts) — :func:`real_home`.

And the guard, in the controller (or the one process of a serial run):
:func:`start_guard` records every path under the real ``~/.domovoi`` with
its mtime and size before collection; at the end of the session
:func:`check_guard` compares, and anything created, changed or removed
fails the run with the paths named. A machine without the folder (CI) has
nothing to compare, but the folder appearing during the run is a failure
too. ``DOMOVOI_TEST_HOME_GUARD=off`` skips the guard (never the isolation)
for a box where something else legitimately writes there during a run, a
satellite running as the same account for instance.

A test that needs a home of its own still uses ``tmp_path`` (and
``monkeypatch.setenv("HOME", ...)``): the session home is shared by every
test in the process. Stdlib only, and imports nothing of ``domovoi``.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

#: The home the run started from (the developer's), inherited by workers.
REAL_HOME_ENV = "DOMOVOI_TEST_REAL_HOME"
#: The pid that made the current temp home: a worker (another pid) makes its own.
OWNER_ENV = "DOMOVOI_TEST_HOME_PID"
#: ``off`` skips the guard.
GUARD_ENV = "DOMOVOI_TEST_HOME_GUARD"
HOME_VARS = ("HOME", "USERPROFILE")
#: ntpath.expanduser builds a home from these when USERPROFILE is unset.
FALLBACK_VARS = ("HOMEDRIVE", "HOMEPATH")

_state: dict = {"home": None, "before": None, "leaks": None}


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


def _inside(path: str, root: str) -> bool:
    try:
        p = _norm(path)
    except (TypeError, ValueError):
        return False
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def real_home() -> Path:
    """The developer's home, as it was before :func:`isolate` moved it."""
    return Path(os.environ.get(REAL_HOME_ENV) or Path.home())


def real_config_dir() -> Path:
    return real_home() / ".domovoi"


def session_home() -> Path | None:
    """This process's temp home, or None before :func:`isolate`."""
    return _state["home"]


def _worker() -> str:
    return os.environ.get("PYTEST_XDIST_WORKER") or "main"


def isolate() -> Path:
    """Point the home at a temp folder of this process's own. Idempotent."""
    if _state["home"] is not None:
        return _state["home"]
    if os.environ.get(OWNER_ENV) == str(os.getpid()) and os.environ.get("HOME"):
        # A second copy of this module in the same process: the home is made.
        _state["home"] = Path(os.environ["HOME"])
        return _state["home"]
    real = os.environ.get(REAL_HOME_ENV) or str(Path.home())
    os.environ[REAL_HOME_ENV] = real
    real_cfg = _norm(Path(real) / ".domovoi")
    for key, value in list(os.environ.items()):
        # One absolute path, not a list (PATH may well start in the home).
        if os.pathsep not in value and os.path.isabs(value) and _inside(value, real_cfg):
            os.environ.pop(key, None)
    home = Path(tempfile.mkdtemp(prefix=f"domovoi-test-home-{_worker()}-"))
    (home / ".domovoi").mkdir()
    for var in HOME_VARS:
        os.environ[var] = str(home)
    for var in FALLBACK_VARS:
        os.environ.pop(var, None)
    os.environ["DOMOVOI_HOME"] = str(home / ".domovoi")
    os.environ[OWNER_ENV] = str(os.getpid())
    _state["home"] = home
    return home


def relocate(value: object) -> str | None:
    """``value`` (a settings string) moved from the real ``~/.domovoi`` to
    the temp home's, or None when it doesn't point inside the real one."""
    if not isinstance(value, str) or not value or _state["home"] is None:
        return None
    if os.pathsep in value or not os.path.isabs(value):
        return None
    real_cfg = _norm(real_config_dir())
    if not _inside(value, real_cfg):
        return None
    # normcase only changes case (and slashes), so the lengths agree.
    rel = os.path.normpath(os.path.abspath(value))[len(real_cfg):].lstrip(os.sep)
    return str(_state["home"] / ".domovoi" / rel) if rel else str(_state["home"] / ".domovoi")


def cleanup() -> None:
    """Remove this process's temp home. Logging file handlers still open
    on it (``plugin_<slug>.log``, the core's own) are closed first: Windows
    won't delete an open file. Best effort past that (faulthandler's file)."""
    home = _state["home"]
    if home is None or _norm(home) == _norm(real_home()):
        return
    import logging

    root = _norm(home)
    loggers = [logging.getLogger()] + [
        lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger)
    ]
    for logger in loggers:
        for handler in list(logger.handlers):
            if _inside(getattr(handler, "baseFilename", "") or "", root):
                logger.removeHandler(handler)
                handler.close()
    shutil.rmtree(home, ignore_errors=True)


# ─── the guard ──────────────────────────────────────────────────────────────

def snapshot(root: Path) -> dict[str, tuple[int, int]] | None:
    """Every path under ``root`` (relative, ``/``-separated) → (mtime_ns,
    size; -1 for a directory), or None when ``root`` doesn't exist.
    One ``scandir`` per directory: on Windows the stat comes with the
    listing, ~0.3 s for 65k entries."""
    try:
        st = os.stat(root)
    except FileNotFoundError:
        return None
    except OSError:
        return {}
    out: dict[str, tuple[int, int]] = {".": (st.st_mtime_ns, -1)}
    base = len(os.fspath(root)) + 1
    stack = [os.fspath(root)]
    while stack:
        current = stack.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    s = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                rel = entry.path[base:].replace(os.sep, "/")
                out[rel] = (s.st_mtime_ns, -1 if is_dir else s.st_size)
                if is_dir:
                    stack.append(entry.path)
    return out


def changes(before: dict | None, after: dict | None) -> list[tuple[str, str, int | None]]:
    """``(kind, path, mtime_ns)`` for each path created, modified or
    removed between two :func:`snapshot` results. A directory whose only
    change is its own mtime (an entry came or went in it) is left out:
    that entry is reported itself."""
    before = before if before is not None else {}
    after = after if after is not None else {}
    found: list[tuple[str, str, int | None]] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if old is None:
            found.append(("created", path, new[0]))
        elif new is None:
            found.append(("removed", path, None))
        elif not (old[1] == new[1] == -1):
            found.append(("modified", path, new[0]))
    return found


def guard_enabled() -> bool:
    return os.environ.get(GUARD_ENV, "").strip().lower() not in ("off", "0", "false", "no")


def start_guard() -> None:
    """Record the real ``~/.domovoi`` (controller / serial process only)."""
    if os.environ.get("PYTEST_XDIST_WORKER") or not guard_enabled() or _state["before"] is not None:
        return
    # ("taken", None) when the folder doesn't exist: it must still not exist at the end.
    _state["before"] = ("taken", snapshot(real_config_dir()))


def check_guard(session) -> list[tuple[str, str, int | None]]:
    """At session end (after xdist's workers are down): fail the run if the
    real ``~/.domovoi`` changed. Returns what changed."""
    if hasattr(session.config, "workerinput") or _state["before"] is None:
        return []
    found = changes(_state["before"][1], snapshot(real_config_dir()))
    _state["leaks"] = found
    if found and session.exitstatus in (0, 5):  # OK, NO_TESTS_COLLECTED
        session.exitstatus = 1
    return found


def report_lines() -> list[str]:
    found = _state["leaks"]
    if not found:
        return []
    root = real_config_dir()
    lines = [
        f"This run wrote the developer's real {root} ({len(found)} path(s)); tests run with",
        f"HOME/USERPROFILE at a temp folder ({session_home()}), so something reached it anyway:",
    ]
    for kind, path, mtime in found[:40]:
        when = time.strftime("%H:%M:%S", time.localtime(mtime / 1e9)) if mtime else ""
        lines.append(f"  {kind:<9}{root / path}  {when}".rstrip())
    if len(found) > 40:
        lines.append(f"  ... and {len(found) - 40} more")
    lines += [
        "A path built from the real home before domovoi/tests/home_isolation.py moved it, or",
        "taken from somewhere else (an absolute path in a fixture, a subprocess given its own",
        "environment): find the writer by the path, point it at tmp_path, and rerun. If another",
        "process on this machine wrote it (a core or satellite running as this user, another",
        f"branch's test run), rerun when it is quiet; {GUARD_ENV}=off skips this check.",
    ]
    return lines


def write_report(terminalreporter) -> None:
    lines = report_lines()
    if not lines:
        return
    if terminalreporter is None:
        print("\n".join(lines), file=sys.stderr)
        return
    terminalreporter.section("real ~/.domovoi written during the run", sep="=", red=True, bold=True)
    for line in lines:
        terminalreporter.write_line(line, red=True)
    # And in the last line, beside "N passed": the exit status alone is easy to miss.
    terminalreporter.stats.setdefault("real-home writes", []).extend(_state["leaks"])
