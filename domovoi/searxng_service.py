"""Start or stop the local search helper (SearXNG) to match the internet answer.

Web answers ("what's the weather tomorrow", "check that online", news
topic discovery) go through the SearXNG container in
``domovoi/docker-compose.yml`` (service ``searxng``, container
``domovoi-searxng``, ``127.0.0.1:6888``). Nothing used to start it, so a
box whose owner wanted web answers got "I checked online but couldn't
find a clear answer" until someone ran ``docker compose up -d searxng``
by hand (fix B8).

The container follows ``INTERNET_ACCESS`` (docs/INTERNET.md):

* ``always`` / ``sometimes`` → ``docker compose up -d --no-deps searxng``.
  The first start pulls the pinned image (a few hundred MB), so it gets a
  generous timeout; ``restart: unless-stopped`` keeps it up across reboots.
* ``never`` → ``docker stop domovoi-searxng``, only when it is running.
  SearXNG is local, but every search it runs goes to public search
  engines.
* unanswered → nothing: today's behaviour, whatever the household did by
  hand.

Who calls :func:`reconcile`:

* the core, when an admin saves the answer (the ``internet_access``
  reapply hook, :func:`schedule_reconcile`, registered in
  ``main._register_core_reapply_hooks``). It is NEVER called at core boot:
  no docker call may slow or break a start;
* ``domovoi/scripts/dev.sh`` / ``dev.ps1`` and the Linux update unit
  (``scripts/linux/apply-update.sh``) do the same thing in shell, with the
  answer read through ``python -m domovoi.egress --print-policy``.

``DOMOVOI_MANAGE_SEARXNG=0`` (or ``false`` / ``no`` / ``off``) in the
process environment turns all of this off: test harnesses use it so they
never stop a container they did not start, and an operator who runs
SearXNG some other way can set it too.

Everything here is best effort: the docker CLI runs in a worker thread,
every outcome is logged, and nothing raises into the caller.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from domovoi import egress
from domovoi.config import settings

log = logging.getLogger(__name__)

MANAGE_ENV = "DOMOVOI_MANAGE_SEARXNG"
CONTAINER = "domovoi-searxng"
SERVICE = "searxng"

# The first start pulls the image; later ones take a second or two.
START_TIMEOUT_SEC = 600.0
STOP_TIMEOUT_SEC = 60.0
INSPECT_TIMEOUT_SEC = 20.0

_OPT_OUT_VALUES = frozenset({"0", "false", "no", "off"})

# On Windows a console-less parent would flash a console window for the
# docker child (same reason as git_version._NO_WINDOW). 0 elsewhere.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Fire-and-forget tasks are kept referenced until they finish, so the
# event loop's weak reference can't drop one mid-run.
_PENDING: set[asyncio.Task] = set()


@dataclass
class SearxngAction:
    action: Literal["start", "stop", "none", "skipped"]
    ok: bool
    detail: str


def compose_file() -> Path:
    return Path(settings.repo_dir) / "domovoi" / "docker-compose.yml"


def managed() -> bool:
    """False when the environment opts out (``DOMOVOI_MANAGE_SEARXNG=0``)."""
    value = (os.environ.get(MANAGE_ENV) or "").strip().lower()
    return value not in _OPT_OUT_VALUES


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Blocking docker call; :func:`reconcile` runs it in a thread."""
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=_NO_WINDOW,
    )


async def _docker(argv: list[str], timeout: float) -> tuple[bool, str]:
    """(ok, detail) for one docker call. Never raises."""
    try:
        proc = await asyncio.to_thread(_run, argv, timeout)
    except FileNotFoundError:
        return False, "docker is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"`{' '.join(argv[:3])} ...` timed out after {int(timeout)} s"
    except Exception as e:  # noqa: BLE001 — best effort, never raises
        return False, f"{type(e).__name__}: {e}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, (tail[-1] if tail else f"exit {proc.returncode}")[:400]
    return True, (proc.stdout or "").strip()[:400]


async def _running() -> bool | None:
    """Whether the container is running: True/False, or None when docker
    can't say (no such container counts as False)."""
    ok, out = await _docker(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
        INSPECT_TIMEOUT_SEC,
    )
    if not ok:
        if "no such" in out.lower():
            return False
        return None
    return out.strip().lower() == "true"


async def reconcile(answer: str | None = None) -> SearxngAction:
    """Start or stop the container to match ``answer`` (default: the
    current :func:`egress.policy`). Never raises."""
    try:
        if not managed():
            result = SearxngAction("skipped", True, f"{MANAGE_ENV} opts out")
        else:
            policy = egress.policy() if answer is None else egress.normalize_policy(answer)
            if policy in ("always", "sometimes"):
                result = await _start()
            elif policy == "never":
                result = await _stop()
            else:
                result = SearxngAction(
                    "none", True, "internet access is not answered; left as it is"
                )
    except Exception as e:  # noqa: BLE001 — best effort, never raises
        result = SearxngAction("none", False, f"{type(e).__name__}: {e}")
    level = logging.INFO if result.ok else logging.WARNING
    log.log(level, "search helper (SearXNG): %s %s — %s",
            result.action, "ok" if result.ok else "failed", result.detail)
    return result


async def _start() -> SearxngAction:
    path = compose_file()
    if not path.is_file():
        return SearxngAction("start", False, f"{path} not found")
    ok, detail = await _docker(
        [
            "docker", "compose", "-f", str(path),
            "--project-directory", str(path.parent),
            "up", "-d", "--no-deps", SERVICE,
        ],
        START_TIMEOUT_SEC,
    )
    if ok:
        return SearxngAction("start", True, f"{CONTAINER} is up")
    return SearxngAction("start", False, detail)


async def _stop() -> SearxngAction:
    running = await _running()
    if running is False:
        return SearxngAction("none", True, f"{CONTAINER} is not running")
    if running is None:
        return SearxngAction("stop", False, f"could not ask docker about {CONTAINER}")
    ok, detail = await _docker(["docker", "stop", CONTAINER], STOP_TIMEOUT_SEC)
    if ok:
        return SearxngAction("stop", True, f"{CONTAINER} stopped")
    return SearxngAction("stop", False, detail)


def schedule_reconcile() -> None:
    """The ``internet_access`` reapply hook: run :func:`reconcile` as a
    background task on the running loop. A no-op without a running loop
    (a synchronous caller has nothing to schedule on)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.debug("search helper (SearXNG): no running loop; reconcile skipped")
        return
    task = loop.create_task(reconcile(), name="searxng-reconcile")
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
