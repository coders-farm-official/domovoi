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
* Every git call shells out via :func:`domovoi.git_run.run` inside
  ``asyncio.to_thread`` (git is blocking) with ``cwd=settings.repo_dir`` and a
  short timeout, and **never raises into the caller** — a missing git binary,
  a timeout, or any other failure collapses into a structured result so an
  admin endpoint can report "unknown / offline" instead of 500ing. git never
  asks anything (no password, no host key, no askpass window on the
  desktop), and the timeout holds: its whole process tree is killed when
  time is up (F-051). A failed fetch says why in plain words
  (:func:`_fetch_failure`).

On a Linux host with the update unit installed (docs/LINUX_HOST.md,
"Updates from the dashboard") this module is also the core's half of that
pipeline: :func:`pull` records the SHA to roll back to, and
:func:`version_state` reports the unit's last result.

Releases and channels (docs/LINUX_HOST.md, "Release channels"): a release
is a ``v<version>`` tag, and :func:`version_label` names the code by the
nearest one (``1.0.0`` on a release, ``1.0.0 + 14 (abc1234)`` past it). The
checkout follows one of two branches: ``stable``, which only ever moves to a
release's commit, or ``main``, which carries every change.
:func:`switch_channel` changes which one the check and the pull follow,
and never moves HEAD.

Signed updates (docs/LINUX_HOST.md, "Signed updates"): :func:`pull`
verifies the fetched tip's SSH signature against the root-owned
allowed-signers file BEFORE fast-forwarding, and refuses the pull while that
file exists and the tip does not verify. Without the file it warns, loudly,
and goes on, so an install that has not set signing up keeps updating.
:func:`version_state` reports the checkout's state as ``signature``. The
update unit re-verifies HEAD as root: its root-owned verifier
(scripts/linux/update-verifier.sh) fetches HEAD into a mirror only root can
write, verifies it there and runs the update from that commit's own copy of
scripts/linux, never from this checkout. This process runs as the service
user, and root does not take its word for it, nor run its files.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from domovoi import egress, git_lock, git_run, self_restart
from domovoi.config import settings

log = logging.getLogger(__name__)

# git is fast on a local clone; if it hasn't answered in 10s something is
# wedged (lock contention, network for fetch) and we'd rather report an
# error than pin the calling endpoint open.
_GIT_TIMEOUT_SEC = 10.0

# The fast-forward itself writes the whole update into the tree: hundreds
# of files from one release to the next (363 from 1.0.0-rc2 to batch 17),
# on a box that may be busy loading models. Killed at 10 s it left a torn
# tree and a stale index.lock (git_lock). The web waits 90 s for the pull.
_MERGE_TIMEOUT_SEC = 45.0

# How long a pull waits for the checkout lock the update unit holds
# (:func:`_checkout_lock`) before it says an update is under way.
_CHECKOUT_LOCK_WAIT_SEC = 10.0

# On Windows a console-less parent (pythonw, a service, uvicorn launched from
# a detached shell) gets a fresh conhost window for EVERY console child it
# spawns. The dashboard polls the snapshot every 1.5 s, which used to mean a
# flicker of git.exe windows all day: git_run.run starts git without one.

# How long the snapshot may reuse a SHA before asking git again. The tree only
# moves on a pull (which invalidates the cache) or a manual edit, and the
# version panel uses the uncached current_sha() anyway.
_SHA_CACHE_TTL_SEC = 60.0
_SHA_CACHE: tuple[str, float] | None = None


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in the repo dir, capturing text output.

    Blocking — callers wrap this in ``asyncio.to_thread``. Raises on the usual
    subprocess failure modes (FileNotFoundError, TimeoutExpired,
    CalledProcessError is NOT raised here since check=False); the async
    wrappers catch and translate. No prompts, and the timeout holds
    (:mod:`domovoi.git_run`)."""
    # The fast-forward writes the whole update into the tree: its own bound.
    timeout = _MERGE_TIMEOUT_SEC if args[:1] == ("merge",) else _GIT_TIMEOUT_SEC
    return _git(args, timeout)


def _git(args: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
    # advice.graftFileDeprecated: GIT_GRAFT_FILE (_GIT_ENV) names a graft
    # file git can open, so every git printed "hint: Support for
    # <GIT_DIR>/info/grafts is deprecated" into its stderr, and so into the
    # errors the Version card shows (the root verifier silenced it for its
    # own git only).
    return git_run.run(
        ["git", "-c", "advice.graftFileDeprecated=false", *args],
        cwd=settings.repo_dir,
        timeout=timeout,
        env=_GIT_ENV,
    )


# Every git call reads the objects as they are: no refs/replace and no
# grafts, so a commit can't be swapped for another one under its SHA when
# its signature is checked (rc2 review C3; the update unit's root git does
# the same). And no optional locks: the version card's `git status` (every
# minute or so) used to take .git/index.lock for its whole run, so a pull
# at that moment failed with "index.lock: File exists", and a status killed
# then (a timeout, a Windows shutdown) left the lock behind for good.
_GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull, "GIT_OPTIONAL_LOCKS": "0"}


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


async def cached_current_sha(ttl_sec: float = _SHA_CACHE_TTL_SEC) -> str:
    """`current_sha()` memoised for `ttl_sec`. For hot paths such as the admin
    snapshot the dashboard polls every 1.5 s: two git launches per poll is
    wasted work (and, on Windows, a console window each). `pull()` drops the
    cache so a fresh checkout is reported immediately."""
    global _SHA_CACHE
    now = time.monotonic()
    if _SHA_CACHE is not None and now - _SHA_CACHE[1] < ttl_sec:
        return _SHA_CACHE[0]
    sha = await current_sha()
    _SHA_CACHE = (sha, now)
    return sha


def invalidate_sha_cache() -> None:
    global _SHA_CACHE
    _SHA_CACHE = None


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
_BOOT_LABEL: str | None = None
_BOOT_MONOTONIC: float | None = None
_BOOT_WALL: float | None = None


async def capture_boot_state() -> None:
    """Record the SHA, its release label and the wall-clock time at process
    start. Called once from the core's startup hook, before serving
    traffic. Idempotent."""
    global _BOOT_SHA, _BOOT_LABEL, _BOOT_MONOTONIC, _BOOT_WALL
    if _BOOT_SHA is not None:
        return
    _BOOT_SHA = await current_sha()
    _BOOT_LABEL = await current_label()
    _BOOT_MONOTONIC = time.monotonic()
    _BOOT_WALL = time.time()


def boot_sha() -> str:
    """The SHA this process was launched from, or "unknown" if startup never
    captured it (stub/test contexts that skip the boot hook)."""
    return _BOOT_SHA or "unknown"


def boot_label() -> str | None:
    """The release label of the code this process was launched from
    (:func:`version_label` at boot), or None if startup never captured it."""
    return _BOOT_LABEL


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
    # Whether the dashboard can offer a working "restart to apply" button, or
    # has to fall back to printing the command.
    can_restart, restart_hint = await self_restart.capable_async()
    mode = self_restart.restart_mode()
    last, problem = await asyncio.to_thread(load_last_update)
    if problem == "missing" and mode != "update":
        # A host without the unit has no result, and that is no problem.
        problem = None
    in_progress = await asyncio.to_thread(self_restart.underway, last, mode)
    signature = await asyncio.to_thread(signature_state, checkout)
    if checkout == "unknown":
        # No git, or not a checkout: nothing more to ask git.
        checkout_label = await asyncio.to_thread(_pyproject_version) or "unknown"
        track = {"branch": None, "remote": None, "merge": None, "channel": None, "intended": None}
        newest = None
    else:
        checkout_label = await asyncio.to_thread(_checkout_label, checkout)
        track = await asyncio.to_thread(tracking)
        newest = await asyncio.to_thread(latest_release)
    return {
        # `sha` stays for backwards compatibility with existing callers —
        # and now means the RUNNING code, which is what they meant to ask.
        "sha": running,
        "running_sha": running,
        "checkout_sha": checkout,
        # The release label (version_label) of the running code, captured
        # at boot: "1.0.0", or "1.0.0 + 14 (abc1234)" past that release.
        # None when boot never captured it. `checkout_version` is the
        # checkout's, read now.
        "version": boot_label(),
        "checkout_version": checkout_label,
        # The branch of origin the check and the pull follow ("stable",
        # "main", another, or None), read from the checkout's config with
        # no fetch; `channels` the ones Settings → About & updates offers, and
        # `channel_pin` the branches root's update unit allows
        # (DOMOVOI_UPSTREAM_BRANCH), None when it allows any.
        "channel": track["channel"],
        # The channel the box was set up to follow when that wasn't possible
        # yet (stable before the first release), or the last one switched
        # to; the card points at the switch once it differs from `channel`
        # and the release is out. `latest_release` is origin/stable's label
        # as last fetched (no fetch here), None before the first release.
        "channel_intended": track["intended"],
        "latest_release": newest,
        "channels": list(CHANNELS),
        "channel_pin": channel_pin(),
        # "unreadable" when the update unit's settings file is there but this
        # process can't read it (then the pin above can't be known), else None.
        "channel_pin_problem": update_defaults_problem(),
        "restart_required": bool(
            known and running.removesuffix("-dirty") != checkout.removesuffix("-dirty")
        ),
        "started_at": started_at(),
        "uptime_sec": uptime_sec(),
        "restart_capable": can_restart,
        "restart_hint": restart_hint,
        # What to run by hand when restart_capable is false: the panel shows
        # it in place of the button. None on a host without systemd, where
        # there is no one command to give.
        "restart_command": self_restart.manual_command(mode),
        # "update" when the restart starts domovoi-update.service (sync,
        # migrate, health-check, roll back); "restart" when it only bounces
        # core and web, as every host did before that unit existed.
        "restart_mode": mode,
        # A restart or update is under way right now (self_restart.underway):
        # the restart this process just accepted, or the update unit running,
        # however it was started. The panel offers nothing that would start
        # another, and follows this one to its end, after a reload too.
        "restart_in_progress": in_progress,
        # The update unit's last run, or None on a host without one.
        "last_update": last,
        # Why last_update is None when it shouldn't be: "missing" (the unit
        # is installed but no result is there), "unreadable", "too_large",
        # "invalid" (not JSON) or "not_a_result". None when there is a result,
        # or no unit. A word only; the detail goes to this process's log.
        "last_update_problem": problem,
        # A commit that failed to apply and was rolled back. The panel stops
        # offering a pull while the upstream still points at it.
        "bad_sha": _bad_sha(last),
        # The checkout's signature state (see "Signed updates" below):
        # {status, signer, key, enforced, allowed_signers, detail}.
        "signature": signature,
    }


def _bad_sha(last: dict | None) -> str | None:
    bad = (last or {}).get("bad_sha")
    return bad if isinstance(bad, str) and _FULL_SHA_RE.match(bad) else None


# ─── Releases and channels ───────────────────────────────────────────────
#
# One version for the whole project, and one tag per release: v1.0.0,
# v1.0.1, v1.1.0-rc1 (design-notes V1-RELEASE-PLAN 1.1). The tags `gh
# release create` makes are lightweight, hence --tags.
#
# Two channels, both branches of origin. `stable` only ever fast-forwards
# to a release's commit, once the release is complete; `main` carries
# every change. Releases are cut from main, so stable is always on main's
# history: main → stable never moves a box backwards (it waits for the
# next release that holds its code), stable → main is a fast-forward.
# The channel IS the branch the checkout tracks, so the check, the pull,
# bad_sha and the update unit's upstream pin all keep working through
# @{u}, unchanged. New installs follow stable (provision.sh); a box
# installed before the channels follows main until someone switches it.

CHANNELS = ("stable", "main")
_TAG_MATCH = "v[0-9]*"
# `git describe` past a tag: v<release>-<commits since>-g<sha>. The
# release itself may hold hyphens (1.0.0-rc1), hence the lazy match.
_DESCRIBE_RE = re.compile(r"^v(?P<release>.+?)-(?P<n>[0-9]+)-g(?P<sha>[0-9a-f]{4,64})$")
_LABEL_MAX_CHARS = 80
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def _pyproject_version() -> str | None:
    """Blocking. ``[project] version`` of the checkout's pyproject.toml, or
    None. The package metadata is not asked: a development venv's editable
    install can hold an older version than the tree."""
    try:
        import tomllib

        with open(Path(settings.repo_dir) / "pyproject.toml", "rb") as f:
            version = tomllib.load(f).get("project", {}).get("version")
    except Exception:  # noqa: BLE001 — no file, not TOML: no version
        return None
    return version[:_LABEL_MAX_CHARS] if isinstance(version, str) and version else None


def format_label(described: str) -> str | None:
    """A ``git describe --tags`` answer as people read it: ``v1.0.0`` →
    ``1.0.0``, ``v1.0.0-14-gabc1234f`` → ``1.0.0 + 14 (abc1234)``. A
    ``-dirty`` suffix is dropped (the SHA carries that). None for anything
    that isn't a ``v`` tag."""
    text = (described or "").strip().removesuffix("-dirty")
    m = _DESCRIBE_RE.match(text)
    if m:
        label = f"{m['release']} + {int(m['n'])} ({m['sha'][:7]})"
    elif text.startswith("v") and len(text) > 1 and "\n" not in text:
        label = text[1:]
    else:
        return None
    return label[:_LABEL_MAX_CHARS]


def version_label(rev: str = "HEAD") -> str:
    """Blocking. The release label of ``rev``: the nearest ``v`` tag it is
    at or past (:func:`format_label`). With no such tag in its history,
    the pyproject version and the short SHA ("1.0.0rc2 (abc1234)"); with
    no git at all, the pyproject version alone, else "unknown". Never
    raises."""
    try:
        proc = _run("describe", "--tags", "--match", _TAG_MATCH, rev)
        if proc.returncode == 0:
            label = format_label(proc.stdout)
            if label:
                return label
        short = _run("rev-parse", "--short", f"{rev}^{{commit}}")
        sha = short.stdout.strip() if short.returncode == 0 else ""
    except Exception:  # noqa: BLE001 — no git / timeout
        sha = ""
    pyver = _pyproject_version()
    if sha and _SHORT_SHA_RE.match(sha):
        return f"{pyver} ({sha[:7]})" if pyver else sha[:7]
    return pyver or "unknown"


async def current_label() -> str:
    """:func:`version_label` of the checkout's HEAD. Never raises."""
    return await asyncio.to_thread(version_label, "HEAD")


# The checkout's label, keyed by the SHA current_sha() just read: it moves
# only with HEAD or with the tags, and a fetch (which brings tags) drops it.
_LABEL_CACHE: tuple[str, str] | None = None


def _checkout_label(checkout_sha: str) -> str:
    """Blocking. :func:`version_label` of HEAD, memoised per checkout SHA."""
    global _LABEL_CACHE
    if _LABEL_CACHE is not None and _LABEL_CACHE[0] == checkout_sha and checkout_sha != "unknown":
        return _LABEL_CACHE[1]
    label = version_label("HEAD")
    _LABEL_CACHE = (checkout_sha, label)
    return label


def invalidate_label_cache() -> None:
    global _LABEL_CACHE
    _LABEL_CACHE = None


def _config(key: str) -> str | None:
    proc = _run("config", "--get", key)
    value = proc.stdout.strip() if proc.returncode == 0 else ""
    return value or None


def tracking() -> dict:
    """Blocking. What the checkout follows, from its config alone (no
    fetch, and the remote-tracking ref need not exist yet): ``branch`` (the
    local branch, None when HEAD is detached), ``remote`` and ``merge`` (its
    upstream), ``channel``, the upstream's branch name when the remote is
    origin, and ``intended``, the channel the box was set up for when that
    was not possible yet (``git config domovoi.intendedChannel``:
    provision.sh writes "stable" when origin had no stable branch, the
    switch writes the channel it moves to). Never raises."""
    out: dict = {"branch": None, "remote": None, "merge": None, "channel": None, "intended": None}
    try:
        intended = _config("domovoi.intendedChannel")
        out["intended"] = intended if intended in CHANNELS else None
        # The full name, not --short: a tag of the same name (a tag
        # "stable") would make git shorten it to "heads/stable".
        head = _run("symbolic-ref", "-q", "HEAD")
        full = head.stdout.strip() if head.returncode == 0 else ""
        branch = full.removeprefix("refs/heads/") if full.startswith("refs/heads/") else ""
        if not branch or not _BRANCH_RE.match(branch):
            return out
        out["branch"] = branch
        remote = _config(f"branch.{branch}.remote")
        merge = _config(f"branch.{branch}.merge")
        out["remote"], out["merge"] = remote, merge
        if remote == "origin" and merge and merge.startswith("refs/heads/"):
            name = merge.removeprefix("refs/heads/")
            out["channel"] = name if _BRANCH_RE.match(name) else None
    except Exception:  # noqa: BLE001 — no git / timeout: nothing known
        pass
    return out


def channel_pin() -> list[str] | None:
    """The branches root's update unit lets the checkout follow
    (``DOMOVOI_UPSTREAM_BRANCH`` in /etc/default/domovoi-update: one name,
    or several separated by spaces or commas; provision.sh writes the two
    channels), or None when it doesn't pin one. The dashboard can't edit
    that file, by design: a channel outside it is refused here, and by the
    unit itself."""
    raw = os.environ.get("DOMOVOI_UPSTREAM_BRANCH") or _defaults_get("DOMOVOI_UPSTREAM_BRANCH")
    names = [n for n in re.split(r"[\s,]+", raw or "") if n]
    return names or None


def update_defaults_problem() -> str | None:
    """"unreadable" when the update unit's env file exists but this process
    can't read it, else None. Then channel_pin() and allowed_signers_path()
    can't see what root pins (REV-20): the channel switch refuses rather
    than offer a branch root would then refuse at every update."""
    path = os.environ.get("DOMOVOI_UPDATE_DEFAULTS_FILE") or UPDATE_DEFAULTS_FILE
    try:
        with open(path, "rb"):
            return None
    except FileNotFoundError:
        return None
    except OSError:
        return "unreadable"


def _upstream_target() -> tuple[str | None, str | None]:
    """Blocking. ``(rev, refusal)``: what the check counts against and the
    pull merges. Without root's branch pin, the checkout's upstream
    (``@{u}``). With it, ``refs/remotes/origin/<branch>`` named outright,
    for the branch the checkout follows, which must be one the pin allows:
    ``@{u}`` is the service user's config (delta security review
    2026-10-16), and root checks the same again before it runs anything."""
    pin = channel_pin()
    if pin is None:
        return "@{u}", None
    track = tracking()
    branch = track["channel"]
    if branch is None or branch not in pin:
        if track["remote"] and track["merge"]:
            followed = f"{track['remote']}/{track['merge'].removeprefix('refs/heads/')}"
        else:
            followed = "no branch"
        return None, (
            f"this checkout follows {followed}, but the update unit only follows "
            f"{', '.join('origin/' + b for b in pin)} (DOMOVOI_UPSTREAM_BRANCH in "
            f"{UPDATE_DEFAULTS_FILE}); switch the channel in Settings > About & updates"
        )
    return f"refs/remotes/origin/{branch}", None


def _contained_in_pin(head: str, pin: list[str]) -> bool:
    """Blocking. Whether ``head`` is on one of the pinned branches as last
    fetched: the rule root applies before every run (apply-update.sh)."""
    for b in pin:
        ref = f"refs/remotes/origin/{b}"
        tip = _full_sha(ref)
        if tip is not None and (tip == head or _ancestor(head, ref)):
            return True
    return False


def _ancestor(older: str, newer: str) -> bool:
    return _run("merge-base", "--is-ancestor", older, newer).returncode == 0


def latest_release() -> str | None:
    """Blocking. The label of origin's ``stable`` as last fetched (the
    latest release), or None when there is no such branch yet."""
    if _full_sha("refs/remotes/origin/stable") is None:
        return None
    return version_label("refs/remotes/origin/stable")


def _channel_answer(ok: bool, error: str | None, channel: str | None = None,
                    changed: bool = False) -> dict:
    return {"ok": ok, "changed": changed, "channel": channel, "error": error}


def _switch_channel(channel: str, pin: list[str] | None = None) -> dict:
    """Blocking half of :func:`switch_channel`."""
    fetched, _tag_warning_ignored = _fetch_branches_and_tags("origin")
    if fetched.returncode != 0:
        return _channel_answer(False, _fetch_failure(fetched.stderr))
    target = _full_sha(f"refs/remotes/origin/{channel}")
    if target is None:
        why = " (no release has been published on it yet)" if channel == "stable" else ""
        return _channel_answer(False, f"the repository has no {channel} branch{why}, so there is nothing to follow there")
    head = _full_sha("HEAD")
    if head is None:
        return _channel_answer(False, "git could not read this server's HEAD")
    track = tracking()
    if track["branch"] == channel and track["channel"] == channel:
        _run("config", "domovoi.intendedChannel", channel)
        return _channel_answer(True, None, channel)
    # A pull only ever fast-forwards: the channel has to hold this code, or
    # be behind it on the same line (then updates wait for its next release).
    if not (_ancestor(head, target) or _ancestor(target, head)):
        return _channel_answer(
            False,
            f"this server runs {head[:12]}, which is not on {channel}'s history, so a pull "
            f"could never fast-forward to {channel}; nothing was changed",
        )
    # Root refuses to run code that is on none of the pinned branches: a
    # switch that would leave the box there is refused here instead.
    if pin is not None and not _contained_in_pin(head, pin):
        return _channel_answer(
            False,
            f"this server runs {head[:12]}, which is newer than {channel} and on none of the "
            f"branches the update unit allows ({', '.join(pin)}), so it would refuse every "
            "update and restart after the switch; nothing was changed",
        )
    local = _full_sha(f"refs/heads/{channel}")
    if local is not None and track["branch"] != channel and not (
        local == head or _ancestor(local, head) or _ancestor(local, target)
    ):
        return _channel_answer(
            False,
            f"the local branch {channel} holds commits of its own ({local[:12]}), which a "
            "switch would drop; nothing was changed",
        )
    try:
        with _checkout_lock():
            _tidy_checkout()
            if track["branch"] != channel:
                # -B at HEAD: the branch is (re)made where the checkout
                # already is, so no file changes and local edits stay where
                # they are.
                moved = _run("checkout", "-q", "-B", channel)
                if moved.returncode != 0:
                    return _channel_answer(False, _git_failure(moved, "git checkout failed"))
            tracked = _run("branch", "-q", "--set-upstream-to", f"origin/{channel}", channel)
            if tracked.returncode != 0:
                return _channel_answer(False, _git_failure(tracked, "git branch failed"))
    except _CheckoutBusy:
        return _channel_answer(False, "a restart or update is under way; switch again once it has finished")
    if _full_sha("HEAD") != head:  # never: -B at HEAD keeps the commit
        return _channel_answer(False, "HEAD moved during the switch; check the checkout by hand")
    # What the household chose now, so the card's "switch to stable" note
    # (an install from before the first release) doesn't come back.
    _run("config", "domovoi.intendedChannel", channel)
    log.info("update channel: now following origin/%s at %s (HEAD %s unchanged)", channel, target[:12], head[:12])
    return _channel_answer(True, None, channel, changed=True)


async def switch_channel(channel: str) -> dict:
    """Follow origin's ``channel`` branch ("stable" or "main") from now on:
    what the update check and the pull compare with and move to. HEAD never
    moves: the local branch ``channel`` is made at HEAD and set to track
    ``origin/<channel>``, and the next check says what is new there.
    Returns ``{ok, changed, channel, error}``. Never raises.

    Refused, with nothing changed, for a channel not in :data:`CHANNELS`;
    under ``INTERNET_ACCESS=never`` (it fetches); while a restart or update
    is under way; when root's update unit pins other branches
    (:func:`channel_pin`); when origin has no such branch; when this code is
    not on that branch's line (a pull could never fast-forward there); and
    when a local branch of that name holds commits nothing else has."""
    if channel not in CHANNELS:
        return _channel_answer(False, f"unknown channel {channel!r}: one of {', '.join(CHANNELS)}")
    if egress.internet_turned_off():
        return _channel_answer(False, egress.TURNED_OFF_REASON)
    last = await asyncio.to_thread(read_last_update)
    if await asyncio.to_thread(self_restart.underway, last):
        return _channel_answer(
            False, "a restart or update is under way; switch once it has finished"
        )
    if update_defaults_problem():
        return _channel_answer(
            False,
            f"this process can't read {UPDATE_DEFAULTS_FILE}, so it can't tell which "
            "branches the update unit allows; make that file readable (chmod 0644: it "
            "holds no secrets) and try again",
        )
    pin = channel_pin()
    if pin is not None and channel not in pin:
        return _channel_answer(
            False,
            f"this server's update unit only follows {', '.join(pin)} "
            f"(DOMOVOI_UPSTREAM_BRANCH in {UPDATE_DEFAULTS_FILE}, which only an "
            "administrator on the box can change)",
        )
    try:
        return await asyncio.to_thread(_switch_channel, channel, pin)
    except FileNotFoundError:
        return _channel_answer(False, "git not installed")
    except subprocess.TimeoutExpired:
        return _channel_answer(
            False, f"git timed out after {_GIT_TIMEOUT_SEC:g} s; the channel was not changed"
        )
    except Exception as e:  # noqa: BLE001 — never raise into the endpoint
        return _channel_answer(False, str(e)[:_ERROR_MAX_CHARS])


# ─── Signed updates ──────────────────────────────────────────────────────
#
# A compromise of upstream `main` (a stolen token, a merged pull request)
# used to yield root on every Linux install at its next update: the update
# unit runs the pulled checkout's own script as root. The bound is a
# signature. Every commit the owner makes is SSH-signed, the allowed keys
# live in a root-owned file on the box, and nothing from an unverified
# commit runs as root while that file exists. Root runs only the verified
# copy of a commit (scripts/linux/update-verifier.sh), so an edit this
# process, or anything else running as the service user, makes to the
# working tree never reaches root either.
#
# Enforcement is the file's presence: no file, no enforcement, and a loud
# warning instead, so the live install keeps working until the owner sets
# signing up. The file's path is the one the update unit reads
# (DOMOVOI_ALLOWED_SIGNERS in /etc/default/domovoi-update), so the two
# halves of the pipeline agree without a second setting.
#
# `git verify-commit` is the verdict, with the signers file and the
# programs git may run pinned on the command line: the checkout's own
# config must not get a say in what verifies it. Only SSH signatures are
# expected; an OpenPGP one has no keyring here and reads as unverified.

ALLOWED_SIGNERS_DEFAULT = "/etc/domovoi/allowed_signers"
UPDATE_DEFAULTS_FILE = "/etc/default/domovoi-update"
_SIGNATURE_STATUSES = ("verified", "unverified", "unsigned", "unknown")
_SIGNER_MAX_CHARS = 200

# The last verdict, keyed by (sha, signers file, its mtime): the panel
# polls, and ssh-keygen per poll is wasted work.
_SIG_CACHE: tuple[tuple, dict] | None = None


def _defaults_get(key: str) -> str:
    """The last ``KEY=value`` for KEY in the update unit's root-owned env
    file (quotes stripped), or "". The same reading apply-update.sh's
    ``dotenv_get`` gives domovoi/.env. Never raises."""
    path = os.environ.get("DOMOVOI_UPDATE_DEFAULTS_FILE") or UPDATE_DEFAULTS_FILE
    value = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    v = v.strip()
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                        v = v[1:-1]
                    value = v
    except OSError:
        return ""
    return value


def allowed_signers_path() -> str:
    """Where the allowed-signers file is expected: ``DOMOVOI_ALLOWED_SIGNERS``
    from this process's environment, else from the update unit's env file,
    else /etc/domovoi/allowed_signers."""
    return (
        os.environ.get("DOMOVOI_ALLOWED_SIGNERS")
        or _defaults_get("DOMOVOI_ALLOWED_SIGNERS")
        or ALLOWED_SIGNERS_DEFAULT
    )


def signing_enforced() -> bool:
    """Whether the allowed-signers file exists, which is what turns
    enforcement on (for this process and for the update unit alike)."""
    return os.path.isfile(allowed_signers_path())


def _verify_config(signers: str) -> list[str]:
    """The ``-c`` pins every verifying git call carries. The signers file
    overrides whatever the checkout's config names; the program pins keep
    a ``gpg.ssh.program`` in that config from choosing what runs."""
    return [
        "-c", f"gpg.ssh.allowedSignersFile={signers}",
        "-c", "gpg.ssh.program=ssh-keygen",
        "-c", "gpg.program=gpg",
        "-c", "gpg.openpgp.program=gpg",
        "-c", "gpg.x509.program=gpgsm",
    ]


def _last_line(text: str) -> str | None:
    """The last line of ``text`` that says something: ssh-keygen's reason
    ("No principal matched.") comes after its description of the key."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line:
            return line[:_SIGNER_MAX_CHARS]
    return None


def verify_commit(sha: str) -> dict:
    """Blocking. The signature state of commit ``sha``:

    ``status``: ``verified`` (signed by a key in the allowed-signers file),
    ``unsigned``, ``unverified`` (signed, but not by a listed key, or no
    file to check against) or ``unknown`` (git could not say).
    ``signer`` and ``key`` are the principal and key fingerprint of a
    verified signature. ``enforced`` says whether the file exists, and
    ``allowed_signers`` where it is expected. ``detail`` is one line for a
    human. Never raises."""
    signers = allowed_signers_path()
    enforced = os.path.isfile(signers)
    out: dict = {
        "status": "unknown", "signer": None, "key": None,
        "enforced": enforced, "allowed_signers": signers, "detail": None,
    }
    if not _SHORT_SHA_RE.match(sha or ""):
        out["detail"] = "no commit to verify"
        return out
    try:
        if enforced:
            cfg = _verify_config(signers)
            verdict = _run(*cfg, "verify-commit", sha)
            info = _run(*cfg, "log", "-1", "--format=%G?%n%GS%n%GK", sha)
            if info.returncode != 0:
                out["detail"] = _last_line(info.stderr) or "git could not read the commit"
                return out
            lines = info.stdout.split("\n") + ["", "", ""]
            mark, signer, key = lines[0].strip(), lines[1].strip(), lines[2].strip()
            if verdict.returncode == 0 and mark == "G":
                out.update(
                    status="verified",
                    signer=signer[:_SIGNER_MAX_CHARS] or None,
                    key=key[:_SIGNER_MAX_CHARS] or None,
                    detail=f"signed by {signer or 'an allowed key'}, listed in {signers}",
                )
            elif mark == "N":
                out.update(status="unsigned", detail="not signed")
            else:
                reason = _last_line(verdict.stderr) or "the signature did not verify"
                out.update(
                    status="unverified",
                    detail=f"signed, but not by a key in {signers} ({reason})",
                )
            return out
        # Nothing to check against: say whether it is signed at all.
        body = _run("cat-file", "commit", sha)
        if body.returncode != 0:
            out["detail"] = _last_line(body.stderr) or "git could not read the commit"
            return out
        signed = any(line.startswith("gpgsig") for line in body.stdout.splitlines())
        if signed:
            out.update(
                status="unverified",
                detail=f"signed, but there is no {signers} to check it against",
            )
        else:
            out.update(status="unsigned", detail=f"not signed, and there is no {signers}")
        return out
    except Exception as e:  # noqa: BLE001 — no git / timeout → unknown, never raise
        out["detail"] = f"{type(e).__name__}: {e}"[:_SIGNER_MAX_CHARS]
        return out


def signature_state(checkout_sha: str) -> dict:
    """Blocking. :func:`verify_commit` for the checkout's HEAD (``-dirty``
    stripped), memoised until HEAD or the signers file changes. ``unknown``
    without a git call when the SHA itself is unknown."""
    global _SIG_CACHE
    sha = (checkout_sha or "").removesuffix("-dirty")
    if sha == "unknown" or not _SHORT_SHA_RE.match(sha):
        return {
            "status": "unknown", "signer": None, "key": None,
            "enforced": signing_enforced(), "allowed_signers": allowed_signers_path(),
            "detail": "no commit to verify",
        }
    signers = allowed_signers_path()
    try:
        stamp: int | None = os.stat(signers).st_mtime_ns
    except OSError:
        stamp = None
    key = (sha, signers, stamp)
    if _SIG_CACHE is not None and _SIG_CACHE[0] == key:
        return dict(_SIG_CACHE[1])
    result = verify_commit(sha)
    _SIG_CACHE = (key, result)
    return dict(result)


def invalidate_signature_cache() -> None:
    global _SIG_CACHE
    _SIG_CACHE = None


def _clean_signature(value: object) -> dict | None:
    """The update unit's ``signature`` object, trimmed to what the panel
    uses, or None when it is not one."""
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if not isinstance(status, str) or status not in _SIGNATURE_STATUSES:
        return None
    signer = value.get("signer")
    return {
        "status": status,
        "signer": signer[:_SIGNER_MAX_CHARS] if isinstance(signer, str) else None,
        "enforced": value.get("enforced") is True,
    }


# ─── The update unit's side of the story ─────────────────────────────────
#
# apply-update.sh (run as root by domovoi-update.service) needs to know what
# to roll back to. It keeps its own record of the last SHA it applied, but
# before its first run the only witness is this process: record it at pull
# time, in a file the script reads (settings.update_state_dir/prev_sha).
#
# ORIG_HEAD is not good enough on its own: git rewrites it on every pull, so
# after two pulls without a restart it names the middle commit, while the
# process, its venv and its database are still at the first. The SHA to roll
# back to is the one this process is RUNNING, whenever the tree has moved
# ahead of it.

PREV_SHA_FILE = "prev_sha"
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SHORT_SHA_RE = re.compile(r"^[0-9a-f]{4,64}$")


def _full_sha(rev: str) -> str | None:
    """Blocking. ``rev`` as a full commit SHA, or None."""
    proc = _run("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    sha = proc.stdout.strip() if proc.returncode == 0 else ""
    return sha if _FULL_SHA_RE.match(sha) else None


def _rollback_baseline() -> str | None:
    """Blocking. The SHA an update applied after this pull should roll back
    to: the running code when the tree is already ahead of it (an earlier
    pull is still waiting for its restart), else the current HEAD. None when
    git can't say."""
    head = _full_sha("HEAD")
    if head is None:
        return None
    running = boot_sha().removesuffix("-dirty")
    if _SHORT_SHA_RE.match(running):
        full_running = _full_sha(running)
        if full_running and full_running != head:
            ancestor = _run("merge-base", "--is-ancestor", full_running, head)
            if ancestor.returncode == 0:
                return full_running
    return head


def _write_prev_sha(sha: str) -> None:
    """Blocking. Atomically record ``sha`` for the update unit. Best-effort:
    a host without the unit never reads it, so a failure is logged, never
    raised."""
    state_dir = Path(settings.update_state_dir).expanduser()
    tmp = state_dir / f".{PREV_SHA_FILE}.{os.getpid()}.tmp"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        # Bytes, so Windows doesn't turn the newline into CRLF.
        tmp.write_bytes(f"{sha}\n".encode("ascii"))
        # Read by the update unit as the service user; nothing secret.
        os.chmod(tmp, 0o644)
        os.replace(tmp, state_dir / PREV_SHA_FILE)
    except OSError as e:
        log.warning("could not record the pre-pull SHA for the update unit: %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# The result file is small; anything bigger is not ours.
_LAST_UPDATE_MAX_BYTES = 256 * 1024
# What GET /v1/admin/version passes through. Every paired device can read
# that endpoint (device tier since CORE-21), so the script's full record
# (backup paths, raw step output) stays on the box.
_LAST_UPDATE_FIELDS = (
    "status", "mode", "from_sha", "to_sha", "prev_source", "bad_sha",
    "started_at", "finished_at", "duration_sec", "deps_changed",
    "mpd_changed", "migrations_before", "migrations_after", "db_restored",
    "error", "signature",
)
_STEP_FIELDS = ("name", "status", "duration_sec")
# A `warn` step of these keeps its detail, trimmed the way the signature
# object is: the script writes these details itself ("lock not applied:
# <why>; ...", what env-secrets added, os-jobs' "could not mask ..." lines,
# core-restart's "a core that ends itself when frozen stays down",
# journal's "the journal may still be lost at a power cycle"),
# never a tool's raw output, and they are what an owner must see; so are
# gpu-runtime's (scripts/linux/gpu-runtime.sh's own summary line) and
# wake-trainer's (a sentence of apply-update.sh's). Every other detail
# (failed steps' output tails, the search helper's docker output) stays on
# the box.
_WARN_DETAIL_STEPS = frozenset(
    {
        "sync-deps",
        "rollback-deps",
        "env-secrets",
        "os-jobs",
        "gpu-runtime",
        "wake-trainer",
        "core-restart",
        "journal",
        "update-unit",
        # "HEAD ... is on none of the pinned branches ...; it is the commit
        # this box last applied, so this restart goes on" (rc2 review C2).
        "upstream",
        # "not installed: xorriso (...)": old computers' room sticks.
        "added-packages",
    }
)
_WARN_DETAIL_MAX_CHARS = 300
_ERROR_MAX_CHARS = 500
# apply-update.sh before 2026-09-30 printed a negative duration as
# "-89.-412", which no JSON parser takes: a wall clock stepped back during
# the run, or the uutils date(1) of Ubuntu 26.04, whose +%s%3N gave the
# script nanoseconds of varying width to subtract. Such a file stays until
# the next update replaces it, and it can hold a bad_sha, so a result that
# fails to parse is read once more with those durations as null.
_BROKEN_DURATION_RE = re.compile(r'("duration_sec": )-?[0-9]+\.-[0-9]+')
# The same uutils date made every other duration garbage too, some of them
# plausible-looking (a 102 s run recorded as 101766807.450 s, one step as
# 62577.839 s). A duration longer than its run, by the run's own
# started_at and finished_at (whole seconds, hence the slack), is served as
# null; so is one over a day, the bound when the result gives none.
_DURATION_SLACK_SEC = 2
_DURATION_MAX_SEC = 86400
_RESULT_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# The last problem logged, so a dashboard polling every 1.5 s logs a broken
# result once, not on every poll.
_LOGGED_PROBLEM: tuple[str, str, str] | None = None


def _note_problem(path: Path, problem: str, detail: str) -> None:
    global _LOGGED_PROBLEM
    key = (str(path), problem, detail)
    if key != _LOGGED_PROBLEM:
        _LOGGED_PROBLEM = key
        log.warning(
            "the update unit's result %s is %s (%s), so GET /v1/admin/version "
            "reports last_update null", path, problem, detail,
        )


def _result_time(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, _RESULT_TIME_FORMAT).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _duration_bound(doc: dict) -> float:
    """The longest any duration in this result can honestly be."""
    started = _result_time(doc.get("started_at"))
    finished = _result_time(doc.get("finished_at"))
    if started is None or finished is None or finished < started:
        # Still running, or a clock stepped back during the run.
        return _DURATION_MAX_SEC
    return min(finished - started + _DURATION_SLACK_SEC, _DURATION_MAX_SEC)


def _duration(value: object, bound: float) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not (math.isfinite(value) and 0 <= value <= bound):
        return None
    return value


def read_last_update() -> dict | None:
    """Blocking. The update unit's last result (``settings.update_result_file``),
    trimmed to the fields the dashboard needs, or None when there is no
    readable result. Never raises."""
    return load_last_update()[0]


def load_last_update() -> tuple[dict | None, str | None]:
    """Blocking. :func:`read_last_update`, plus why it is None: "missing",
    "unreadable", "too_large", "invalid" or "not_a_result" (None along with
    a result). Every reason but "missing" is logged once. Never raises."""
    global _LOGGED_PROBLEM
    path = Path(settings.update_result_file).expanduser()
    try:
        with path.open("rb") as f:
            raw = f.read(_LAST_UPDATE_MAX_BYTES + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError as e:
        _note_problem(path, "unreadable", f"{type(e).__name__}: {e.strerror or e}")
        return None, "unreadable"
    if len(raw) > _LAST_UPDATE_MAX_BYTES:
        _note_problem(path, "too_large", f"over {_LAST_UPDATE_MAX_BYTES} bytes")
        return None, "too_large"
    # "replace", not strict: the error and step text are the tail of pip,
    # docker or git output, cut to length by the script. One stray byte
    # there must not hide the whole result, and with it the bad_sha that
    # stops the panel offering a rolled-back commit again.
    text = raw.decode("utf-8", errors="replace")
    try:
        doc = json.loads(text)
    except ValueError as e:
        try:
            doc = json.loads(_BROKEN_DURATION_RE.sub(r"\g<1>null", text))
        except ValueError:
            _note_problem(path, "invalid", f"not JSON: {e}")
            return None, "invalid"
    if not isinstance(doc, dict) or not isinstance(doc.get("status"), str):
        _note_problem(path, "not_a_result", "no status")
        return None, "not_a_result"
    _LOGGED_PROBLEM = None
    out = {k: doc[k] for k in _LAST_UPDATE_FIELDS if k in doc}
    for k in ("from_sha", "to_sha", "bad_sha"):
        if k in out and not (isinstance(out[k], str) and _FULL_SHA_RE.match(out[k])):
            out[k] = None
    if isinstance(out.get("error"), str):
        out["error"] = out["error"][:_ERROR_MAX_CHARS]
    if "signature" in out:
        out["signature"] = _clean_signature(out["signature"])
    bound = _duration_bound(doc)
    if "duration_sec" in out:
        out["duration_sec"] = _duration(out["duration_sec"], bound)
    steps = doc.get("steps")
    out["steps"] = []
    for s in steps if isinstance(steps, list) else []:
        if isinstance(s, dict):
            step = {k: s.get(k) for k in _STEP_FIELDS}
            step["duration_sec"] = _duration(step["duration_sec"], bound)
            detail = _warn_detail(s)
            if detail is not None:
                step["detail"] = detail
            out["steps"].append(step)
    return out, None


def _warn_detail(step: dict) -> str | None:
    """The detail a `warn` step of :data:`_WARN_DETAIL_STEPS` carries, one
    line, at most :data:`_WARN_DETAIL_MAX_CHARS`; None for any other step."""
    if step.get("status") != "warn" or step.get("name") not in _WARN_DETAIL_STEPS:
        return None
    detail = step.get("detail")
    if not isinstance(detail, str):
        return None
    line = detail.strip().splitlines()[0].strip() if detail.strip() else ""
    return line[:_WARN_DETAIL_MAX_CHARS] or None


# The branches first, on their own: they are what the check counts and the
# pull merges. Then the tags, explicitly and never forced: the release
# labels come from them. A published tag never moves; if one upstream ever
# did, git refuses to overwrite this server's copy ("would clobber existing
# tag"), and that is a warning about the labels on the card, never a failed
# fetch: one moved tag must not stop every box from updating (rc2 review C1).
_FETCH = ("fetch", "--no-tags")
_FETCH_TAGS = ("fetch", "--no-tags", "origin", "refs/tags/*:refs/tags/*")
_REJECTED_TAG_RE = re.compile(r"\[rejected\]\s+(\S+)\s+->\s+\S+\s+\(would clobber existing tag\)")


def _tag_warning(stderr: str) -> str:
    moved = sorted(set(_REJECTED_TAG_RE.findall(stderr or "")))
    if moved:
        names = ", ".join(moved[:5]) + (" and more" if len(moved) > 5 else "")
        return (
            f"a published release tag moved upstream ({names}); this server keeps the copy it "
            "had, so a version label may be off. Updates are not affected. Published tags "
            "never move: tell Domovoi's maintainers"
        )[:_ERROR_MAX_CHARS]
    return (
        f"the release tags could not be fetched ({_last_line(stderr) or 'git fetch failed'}); "
        "updates are not affected, but a version label may be out of date"
    )[:_ERROR_MAX_CHARS]


# What git's stderr says when the remote wanted something git may not ask
# for here (a password, a token, a host key to trust), when the name did not
# resolve, and when nothing answered. Lowercased substrings.
_ASKED_MARKERS = (
    "terminal prompts disabled", "could not read username", "could not read password",
    "host key verification failed", "permission denied", "authentication failed",
    "could not read from remote repository",
)
_DNS_MARKERS = (
    "could not resolve host", "could not resolve hostname", "name or service not known",
    "temporary failure in name resolution", "no such host is known",
)
_UNREACHABLE_MARKERS = (
    "failed to connect", "couldn't connect", "could not connect", "connection refused",
    "connection timed out", "operation timed out", "network is unreachable",
    "no route to host", "connection reset",
)


def _fetch_failure(stderr: str | None) -> str:
    """A failed ``git fetch`` in plain words, git's own last line after it
    (F-051): what the Version card shows after "update check failed:"."""
    text = (stderr or "").strip()
    low = text.lower()
    if any(m in low for m in _ASKED_MARKERS):
        why = ("the update server wants a password or a host key this server doesn't "
               "have, and git here never asks")
    elif any(m in low for m in _DNS_MARKERS):
        why = "can't look up the update server (no internet, or DNS is down)"
    elif any(m in low for m in _UNREACHABLE_MARKERS):
        why = "can't reach the update server"
    else:
        why = "git fetch failed"
    last = _last_line(text)
    return (f"{why} (git: {last})" if last else why)[:_ERROR_MAX_CHARS]


def _timed_out() -> str:
    return f"the update server didn't answer in {_GIT_TIMEOUT_SEC:g} s (git fetch timed out)"


def _fetch_branches_and_tags(*remote: str) -> tuple[subprocess.CompletedProcess, str | None]:
    """Blocking. ``(the branch fetch, tag_warning)``: the tags are fetched
    only after the branches, and their failure is ``tag_warning``, never the
    fetch's."""
    branches = _run(*_FETCH, *remote)
    warning = None
    if branches.returncode == 0:
        tags = _run(*_FETCH_TAGS)
        if tags.returncode != 0:
            warning = _tag_warning(tags.stderr)
            log.warning("update check: %s", warning)
    invalidate_label_cache()
    return branches, warning


async def fetch() -> dict:
    """`git fetch --no-tags`, then the tags on their own
    (:func:`_fetch_branches_and_tags`), so subsequent behind/ahead counts
    compare against the live upstream and the release labels know every
    tag. Returns ``{"ok": bool, "error": str|None, "tag_warning":
    str|None}``: offline / no remote surfaces as ``ok=False`` with the git
    stderr; tags that could not be fetched (one that moved upstream) only as
    ``tag_warning``. Never an exception.

    Under ``INTERNET_ACCESS=never`` it reports ``ok=False`` with the
    turned-off reason and runs no git at all."""
    if egress.internet_turned_off():
        return {"ok": False, "error": egress.TURNED_OFF_REASON}
    try:
        proc, warning = await asyncio.to_thread(_fetch_branches_and_tags)
        if proc.returncode != 0:
            return {"ok": False, "error": _fetch_failure(proc.stderr), "tag_warning": None}
        return {"ok": True, "error": None, "tag_warning": warning}
    except FileNotFoundError:
        return {"ok": False, "error": "git not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": _timed_out()}
    except Exception as e:  # noqa: BLE001 — never raise into the endpoint
        return {"ok": False, "error": str(e)}


async def commits_behind() -> dict:
    """Fetch, then count how far HEAD is behind/ahead of its upstream.

    Returns ``{"behind": int, "ahead": int, "upstream": bool,
    "upstream_sha": str|None, "error": str|None, "channel": str|None,
    "upstream_version": str|None, "latest_release": str|None,
    "tag_warning": str|None}`` (the last: :func:`fetch`'s).
    ``upstream_sha`` is the full SHA a pull would move to, which the version
    panel compares with a rolled-back ``bad_sha``. ``channel`` is the
    branch followed (:func:`tracking`), ``upstream_version`` the release
    label of what a pull would move to (on ``stable``, the release that is
    available), ``latest_release`` the label of origin's ``stable`` (None
    before the first release). No tracking branch or an offline fetch →
    ``upstream=False`` plus an error string and zeroed counts. Never
    raises.

    Under ``INTERNET_ACCESS=never``: ``upstream=False`` with the
    turned-off reason, and no git runs (the counts against a stale
    upstream would only mislead)."""
    if egress.internet_turned_off():
        return {"behind": 0, "ahead": 0, "upstream": False,
                "upstream_sha": None, "error": egress.TURNED_OFF_REASON}
    # A lock an interrupted git left would fail the next pull: gone now if
    # it is stale (git_lock), whoever looks first. Before the fetch, whose
    # own background maintenance (git maintenance run --auto) is a git at
    # work in the checkout for a while, so no lock would count as stale.
    await asyncio.to_thread(git_lock.clear_stale_locks, settings.repo_dir)
    fetched = await fetch()
    try:
        target, refusal = await asyncio.to_thread(_upstream_target)
        if target is None:
            release = await asyncio.to_thread(_release_info, None)
            return {"behind": 0, "ahead": 0, "upstream": False,
                    "upstream_sha": None, "error": refusal, **release}
        behind = await asyncio.to_thread(
            _run, "rev-list", "--count", f"HEAD..{target}"
        )
        ahead = await asyncio.to_thread(
            _run, "rev-list", "--count", f"{target}..HEAD"
        )
        upstream_sha = await asyncio.to_thread(_full_sha, target)
        release = await asyncio.to_thread(_release_info, upstream_sha)
    except Exception as e:  # noqa: BLE001 — never raise into the endpoint
        return {"behind": 0, "ahead": 0, "upstream": False,
                "upstream_sha": None, "error": str(e), **_NO_RELEASE_INFO}

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
            "upstream_sha": None,
            "error": fetched["error"] or err,
            **release,
        }
    try:
        return {
            "behind": int(behind.stdout.strip() or 0),
            "ahead": int(ahead.stdout.strip() or 0),
            "upstream": True,
            "upstream_sha": upstream_sha,
            "error": fetched["error"],
            "tag_warning": fetched.get("tag_warning"),
            **release,
        }
    except ValueError as e:
        return {"behind": 0, "ahead": 0, "upstream": False,
                "upstream_sha": None, "error": str(e), **release}


_NO_RELEASE_INFO = {"channel": None, "upstream_version": None, "latest_release": None}


def _release_info(upstream_sha: str | None) -> dict:
    """Blocking. The check's release fields (:func:`commits_behind`)."""
    return {
        "channel": tracking()["channel"],
        "upstream_version": version_label(upstream_sha) if upstream_sha else None,
        "latest_release": latest_release(),
    }


def _not_pulled(error: str, signature: dict | None = None) -> dict:
    return {"pulled": False, "new_sha": None, "error": error,
            "prev_sha": None, "signature": signature}


# ─── the checkout: one git at a time, and what an interrupted one left ───

#: Beside the update unit's last-result.json, root-owned (apply-update.sh
#: makes it): the update unit holds it for its whole run, and a pull or a
#: channel switch takes it before moving the checkout, so the two never run
#: git on the working tree at the same time. The core opens it read-only
#: (flock needs no more). Without the unit there is no file, and only this
#: process's own pulls are kept apart (:data:`_CHECKOUT_MUTEX`).
CHECKOUT_LOCK_NAME = "checkout.lock"
_CHECKOUT_MUTEX = threading.Lock()


class _CheckoutBusy(Exception):
    """The checkout lock stayed taken: an update or another pull runs."""


@contextlib.contextmanager
def _checkout_lock(wait: float = _CHECKOUT_LOCK_WAIT_SEC):
    """Blocking. Hold the checkout lock (:data:`CHECKOUT_LOCK_NAME`) for the
    block, waiting up to ``wait`` seconds; raises :class:`_CheckoutBusy`
    when it stays taken."""
    if not _CHECKOUT_MUTEX.acquire(timeout=wait):
        raise _CheckoutBusy()
    fd = None
    try:
        path = Path(settings.update_result_file).parent / CHECKOUT_LOCK_NAME
        if sys.platform != "win32" and path.is_file():
            import fcntl

            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
            except OSError as e:
                log.warning("can't open %s (%s): this pull is not kept apart from the update unit", path, e)
            if fd is not None:
                deadline = time.monotonic() + wait
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise _CheckoutBusy() from None
                        time.sleep(0.2)
        yield
    finally:
        if fd is not None:
            os.close(fd)  # releases the flock
        _CHECKOUT_MUTEX.release()


def _tidy_checkout(base: str | None = None, tip: str | None = None) -> bool:
    """Blocking, under :func:`_checkout_lock`. Clean up after a git that
    was killed in the checkout (:mod:`domovoi.git_lock`): its stale locks,
    and with ``base`` (HEAD) and ``tip`` (what is being pulled) the files a
    killed fast-forward had already rewritten. True when there was
    something to clean up."""
    repo = settings.repo_dir
    cleared = git_lock.clear_stale_locks(repo)
    repaired: dict = {}
    if base and tip and base != tip:
        repaired = git_lock.repair_interrupted_checkout(_run, repo, base, tip)
    return bool(cleared["removed"] or repaired.get("restored") or repaired.get("removed"))


def _git_failure(proc: subprocess.CompletedProcess, fallback: str) -> str:
    """A failed git that moves the checkout, as the Version card says it:
    a lock still held is said in plain words (git_lock.BUSY_MESSAGE), and
    git's own text never carries its hint: lines."""
    if git_lock.is_lock_error(proc.stderr):
        return git_lock.BUSY_MESSAGE
    return (git_lock.without_hints(proc.stderr) or fallback)[:_ERROR_MAX_CHARS]


def _fast_forward(target: str) -> tuple[subprocess.CompletedProcess, str | None]:
    """Blocking. ``git merge --ff-only target`` under the checkout lock,
    after cleaning up what an interrupted git left. Returns the merge and
    the notice for the Version card (git_lock.INTERRUPTED_NOTICE) when it
    cleaned up. Raises :class:`_CheckoutBusy` while an update holds the
    checkout, and ``subprocess.TimeoutExpired`` (its locks already removed
    by git_run) past :data:`_MERGE_TIMEOUT_SEC`."""
    with _checkout_lock():
        # Again, now that nothing else can start on the checkout: an update
        # that began while this pull fetched applies the HEAD it read.
        if self_restart.underway(read_last_update()):
            raise _CheckoutBusy()
        head = _full_sha("HEAD")
        notice = git_lock.INTERRUPTED_NOTICE if _tidy_checkout(head, target) else None
        proc = _run("merge", "--ff-only", target)
        if proc.returncode != 0 and git_lock.is_lock_error(proc.stderr):
            # A lock that turned stale since the look above (its git died
            # meanwhile): once more, if it goes now.
            if git_lock.clear_stale_locks(settings.repo_dir)["removed"]:
                notice = git_lock.INTERRUPTED_NOTICE
                proc = _run("merge", "--ff-only", target)
        if notice:
            log.warning("pull: %s", notice)
        return proc, notice


async def pull() -> dict:
    """A fast-forward pull, in three steps: ``git fetch``, verify the
    fetched tip's signature, ``git merge --ff-only <that tip>``. A
    deliberate, separate admin action (never run by a check). Returns
    ``{"pulled": bool, "new_sha": str|None, "error": str|None, "prev_sha":
    str|None, "signature": dict|None, "notice": str|None}``. A dirty or
    diverged tree fails the fast-forward and comes back ``pulled=False``
    with the git stderr as ``error`` — we never force a merge or reset.
    Never raises.

    An interrupted git (:mod:`domovoi.git_lock`): before the fast-forward,
    under the lock the update unit holds too (:func:`_checkout_lock`), a
    stale lock it left is removed and the files a killed fast-forward had
    rewritten are put back; ``notice`` then says so in plain words
    (git_lock.INTERRUPTED_NOTICE), and a lock a live git still holds is
    ``error`` = git_lock.BUSY_MESSAGE, never git's own text.

    Signed updates: while the allowed-signers file exists, a tip that does
    not verify against it is refused BEFORE the tree moves (``pulled=False``,
    ``signature`` says why). Without the file the pull goes ahead and a
    warning is logged. The tip is verified by its SHA and that same SHA is
    what is merged, so nothing fetched in between can slip through.

    A successful pull also records ``prev_sha``, the full SHA an update
    should roll back to, for the Linux update unit (see
    :func:`_rollback_baseline`).

    Under ``INTERNET_ACCESS=never`` it reports ``pulled=False`` with the
    turned-off reason and runs no git at all. So it does while a restart or
    update is under way (:func:`self_restart.underway`): the update unit
    applies the HEAD it read when it started, and moving the checkout under
    it would leave the services running code it never checked."""
    if egress.internet_turned_off():
        return _not_pulled(egress.TURNED_OFF_REASON)
    last = await asyncio.to_thread(read_last_update)
    if await asyncio.to_thread(self_restart.underway, last):
        return _not_pulled(
            "a restart or update is under way; pull again once it has finished"
        )
    try:
        baseline = await asyncio.to_thread(_rollback_baseline)
    except Exception:  # noqa: BLE001 — the pull matters more than the record
        baseline = None
    # Before the fetch, as at the check (commits_behind says why).
    cleared_first = bool((await asyncio.to_thread(git_lock.clear_stale_locks, settings.repo_dir))["removed"])
    try:
        fetched, tag_warning = await asyncio.to_thread(_fetch_branches_and_tags)
        if fetched.returncode != 0:
            return _not_pulled(_fetch_failure(fetched.stderr))
        upstream, refusal = await asyncio.to_thread(_upstream_target)
        if upstream is None:
            return _not_pulled(refusal)
        target = await asyncio.to_thread(_full_sha, upstream)
        if target is None:
            return _not_pulled(
                "no upstream branch is configured for the current branch, "
                "so there is nothing to pull (git branch --set-upstream-to)"
            )
        signature = await asyncio.to_thread(verify_commit, target)
    except FileNotFoundError:
        return _not_pulled("git not installed")
    except subprocess.TimeoutExpired:
        return _not_pulled(f"git timed out after {_GIT_TIMEOUT_SEC:g} s; nothing was pulled")
    except Exception as e:  # noqa: BLE001
        return _not_pulled(str(e))

    if update_defaults_problem():
        log.warning(
            "can't read %s: the branch pin and the signers path it may set are unknown to "
            "this process, so the pull follows the checkout's own upstream; the update unit "
            "still applies them as root (make the file readable: chmod 0644)",
            UPDATE_DEFAULTS_FILE,
        )
    if signature["status"] != "verified":
        if signature["enforced"]:
            error = (
                f"refused: the fetched commit {target[:12]} is {signature['detail']}; "
                f"signed updates are enforced by {signature['allowed_signers']}, so the "
                "checkout was not moved. Pull a commit signed by a listed key, or remove "
                "that file to turn enforcement off (docs/LINUX_HOST.md, Signed updates)."
            )
            log.error("pull refused: %s", error)
            return _not_pulled(error, signature)
        log.warning(
            "UNSIGNED UPDATE: pulling %s, which is %s. Signed updates are not enforced "
            "on this box (no %s), so whatever upstream holds will run as root at the "
            "next update. docs/LINUX_HOST.md, Signed updates, says how to turn them on.",
            target[:12], signature["detail"], signature["allowed_signers"],
        )
    try:
        proc, notice = await asyncio.to_thread(_fast_forward, target)
    except FileNotFoundError:
        return _not_pulled("git not installed", signature)
    except _CheckoutBusy:
        return _not_pulled(
            "a restart or update is under way; pull again once it has finished", signature
        )
    except subprocess.TimeoutExpired:
        return _not_pulled(
            f"the update took longer than {_MERGE_TIMEOUT_SEC:g} s and was stopped; Domovoi "
            "cleaned up after it, so press Pull again",
            signature,
        )
    except Exception as e:  # noqa: BLE001
        return _not_pulled(str(e), signature)

    if cleared_first:
        notice = git_lock.INTERRUPTED_NOTICE
    if proc.returncode != 0:
        out = _not_pulled(_git_failure(proc, "git pull failed"), signature)
        out["notice"] = notice
        return out
    invalidate_sha_cache()
    invalidate_signature_cache()
    if baseline is not None:
        await asyncio.to_thread(_write_prev_sha, baseline)
    return {
        "pulled": True,
        "new_sha": await current_sha(),
        "error": None,
        "prev_sha": baseline,
        "signature": signature,
        # git_lock.INTERRUPTED_NOTICE when an interrupted git's leftovers
        # were cleaned up first, else None.
        "notice": notice,
        # Tags that could not be fetched (one moved upstream): the labels
        # may be off; the pull itself went ahead.
        "tag_warning": tag_warning,
    }
