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
