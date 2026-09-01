"""Git version helpers for the Domovoi server's working tree.

The Domovoi server runs straight out of a git clone (``settings.repo_dir``),
so the short HEAD SHA doubles as a human-readable version label for the web
dashboard's "what's running / is there an update?" surface.

Two things to keep straight:

* The SHA returned here is a **label only** — never an integrity check. The
  working tree is routinely dirty (rendered config, local tweaks), so the
  file bytes a satellite downloads over the code channel are verified against
  the per-file MANIFEST sha256, NOT this commit SHA. When the tree is dirty,
  `current_sha()` appends a ``-dirty`` suffix so the label can't masquerade
  as a clean checkout.
* Every git call shells out via ``subprocess.run`` inside
  ``asyncio.to_thread`` (git is blocking) with ``cwd=settings.repo_dir`` and a
  short timeout, and **never raises into the caller** — a missing git binary,
  a timeout, or any other failure collapses into a structured result so an
  admin endpoint can report "unknown / offline" instead of 500ing.
"""

from __future__ import annotations

import asyncio
import subprocess
import time

from domovoi.config import settings

# git is fast on a local clone; if it hasn't answered in 10s something is
# wedged (lock contention, network for fetch) and we'd rather report an
# error than pin the calling endpoint open.
_GIT_TIMEOUT_SEC = 10.0


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in the repo dir, capturing text output.

    Blocking — callers wrap this in ``asyncio.to_thread``. Raises on the usual
    subprocess failure modes (FileNotFoundError, TimeoutExpired,
    CalledProcessError is NOT raised here since check=False); the async
    wrappers catch and translate."""
    return subprocess.run(
        ["git", *args],
        cwd=settings.repo_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SEC,
        check=False,
    )


async def current_sha() -> str:
    """Short HEAD SHA as a version label, with a ``-dirty`` suffix when the
    working tree has uncommitted changes. Returns ``"unknown"`` on any
    failure (no git, not a repo, timeout) — never raises."""
    try:
        head = await asyncio.to_thread(_run, "rev-parse", "--short", "HEAD")
        if head.returncode != 0:
            return "unknown"
        sha = head.stdout.strip()
        if not sha:
            return "unknown"
        status = await asyncio.to_thread(_run, "status", "--porcelain")
        if status.returncode == 0 and status.stdout.strip():
            sha = f"{sha}-dirty"
        return sha
    except Exception:  # noqa: BLE001 — no git / not a repo / timeout → "unknown"
        return "unknown"


# ─── What's RUNNING vs what's CHECKED OUT ────────────────────────────────
#
# `current_sha()` shells out to git on every call, so it always reports the
# WORKING TREE. After a `git pull` without a restart, the tree has moved but
# the process is still executing the modules it imported at boot — and the
# dashboard's "what's running / is there an update?" panel would confidently
# show the new SHA while the old code served every request. That is precisely
# the moment someone goes looking at the version panel, and precisely when it
# used to mislead (observed 2026-09-01: a calculator fix appeared "deployed"
# while the fixed code had never been loaded).
#
# So capture the SHA ONCE at startup. `boot_sha()` is what the process is
# actually running; `current_sha()` is what's on disk. When they differ, a
# restart is pending.
_BOOT_SHA: str | None = None
_BOOT_MONOTONIC: float | None = None
_BOOT_WALL: float | None = None


async def capture_boot_state() -> None:
    """Record the SHA and wall-clock time at process start. Called once from
    the core's startup hook, before serving traffic. Idempotent."""
    global _BOOT_SHA, _BOOT_MONOTONIC, _BOOT_WALL
    if _BOOT_SHA is not None:
        return
    _BOOT_SHA = await current_sha()
    _BOOT_MONOTONIC = time.monotonic()
    _BOOT_WALL = time.time()


def boot_sha() -> str:
    """The SHA this process was launched from, or "unknown" if startup never
    captured it (stub/test contexts that skip the boot hook)."""
    return _BOOT_SHA or "unknown"


def uptime_sec() -> float | None:
    """Seconds since the boot state was captured, or None if never captured.
    Monotonic, so a clock adjustment can't make it go backwards."""
    if _BOOT_MONOTONIC is None:
        return None
    return time.monotonic() - _BOOT_MONOTONIC


def started_at() -> float | None:
    """Unix timestamp of process start, or None if never captured. Wall-clock,
    for display; use :func:`uptime_sec` for durations."""
    return _BOOT_WALL


async def version_state() -> dict:
    """Everything the dashboard needs to tell "running" from "checked out".

    ``restart_required`` is the honest answer to "is what I just pulled
    actually live?" — True only when both SHAs are known and differ. A dirty
    tree is ignored for that comparison: the ``-dirty`` suffix moves with any
    uncommitted edit and would otherwise scream "restart" forever on a
    development box.
    """
    checkout = await current_sha()
    running = boot_sha()
    known = running != "unknown" and checkout != "unknown"
    return {
        # `sha` stays for backwards compatibility with existing callers —
        # and now means the RUNNING code, which is what they meant to ask.
        "sha": running,
        "running_sha": running,
        "checkout_sha": checkout,
        "restart_required": bool(
            known and running.removesuffix("-dirty") != checkout.removesuffix("-dirty")
        ),
        "started_at": started_at(),
        "uptime_sec": uptime_sec(),
    }


async def fetch() -> dict:
    """`git fetch` so subsequent behind/ahead counts compare against the live
    upstream. Returns ``{"ok": bool, "error": str|None}`` — offline / no
    remote surfaces as ``ok=False`` with the git stderr, never an exception."""
    try:
        proc = await asyncio.to_thread(_run, "fetch")
        if proc.returncode != 0:
            return {"ok": False, "error": (proc.stderr or "git fetch failed").strip()}
        return {"ok": True, "error": None}
    except FileNotFoundError:
        return {"ok": False, "error": "git not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "git fetch timed out"}
    except Exception as e:  # noqa: BLE001 — never raise into the endpoint
        return {"ok": False, "error": str(e)}


async def commits_behind() -> dict:
    """Fetch, then count how far HEAD is behind/ahead of its upstream.

    Returns ``{"behind": int, "ahead": int, "upstream": bool, "error":
    str|None}``. No tracking branch or an offline fetch → ``upstream=False``
    plus an error string and zeroed counts. Never raises."""
    fetched = await fetch()
    try:
        behind = await asyncio.to_thread(
            _run, "rev-list", "--count", "HEAD..@{u}"
        )
        ahead = await asyncio.to_thread(
            _run, "rev-list", "--count", "@{u}..HEAD"
        )
    except Exception as e:  # noqa: BLE001 — never raise into the endpoint
        return {"behind": 0, "ahead": 0, "upstream": False, "error": str(e)}

    # A missing upstream makes rev-list exit non-zero ("no upstream
    # configured" / "unknown revision @{u}"). Report it as upstream=False
    # rather than guessing.
    if behind.returncode != 0 or ahead.returncode != 0:
        err = (behind.stderr or ahead.stderr or "no upstream").strip()
        # Prefer the fetch error when the fetch itself failed (offline), so
        # the dashboard can distinguish "no tracking branch" from "offline".
        return {
            "behind": 0,
            "ahead": 0,
            "upstream": False,
            "error": fetched["error"] or err,
        }
    try:
        return {
            "behind": int(behind.stdout.strip() or 0),
            "ahead": int(ahead.stdout.strip() or 0),
            "upstream": True,
            "error": fetched["error"],
        }
    except ValueError as e:
        return {"behind": 0, "ahead": 0, "upstream": False, "error": str(e)}


async def pull() -> dict:
    """`git pull --ff-only` — a deliberate, separate admin action (never run
    by a check). Returns ``{"pulled": bool, "new_sha": str|None, "error":
    str|None}``. A dirty or diverged tree fails the fast-forward and comes
    back ``pulled=False`` with the git stderr as ``error`` — we never force a
    merge or reset. Never raises."""
    try:
        proc = await asyncio.to_thread(_run, "pull", "--ff-only")
    except FileNotFoundError:
        return {"pulled": False, "new_sha": None, "error": "git not installed"}
    except subprocess.TimeoutExpired:
        return {"pulled": False, "new_sha": None, "error": "git pull timed out"}
    except Exception as e:  # noqa: BLE001
        return {"pulled": False, "new_sha": None, "error": str(e)}

    if proc.returncode != 0:
        return {
            "pulled": False,
            "new_sha": None,
            "error": (proc.stderr or "git pull failed").strip(),
        }
    return {"pulled": True, "new_sha": await current_sha(), "error": None}
