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

On a Linux host with the update unit installed (docs/LINUX_HOST.md,
"Updates from the dashboard") this module is also the core's half of that
pipeline: :func:`pull` records the SHA to roll back to, and
:func:`version_state` reports the unit's last result.

Signed updates (docs/LINUX_HOST.md, "Signed updates"): :func:`pull`
verifies the fetched tip's SSH signature against the root-owned
allowed-signers file BEFORE fast-forwarding, and refuses the pull while that
file exists and the tip does not verify. Without the file it warns, loudly,
and goes on, so an install that has not set signing up keeps updating.
:func:`version_state` reports the checkout's state as ``signature``. The
update unit re-verifies HEAD as root (apply-update.sh): this process runs as
the service user, and root does not take its word for it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from domovoi import egress, self_restart
from domovoi.config import settings

log = logging.getLogger(__name__)

# git is fast on a local clone; if it hasn't answered in 10s something is
# wedged (lock contention, network for fetch) and we'd rather report an
# error than pin the calling endpoint open.
_GIT_TIMEOUT_SEC = 10.0

# On Windows a console-less parent (pythonw, a service, uvicorn launched from
# a detached shell) gets a fresh conhost window for EVERY console child it
# spawns. The dashboard polls the snapshot every 1.5 s, which used to mean a
# flicker of git.exe windows all day. CREATE_NO_WINDOW only exists on Windows;
# 0 is a no-op elsewhere.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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
    wrappers catch and translate."""
    return subprocess.run(
        ["git", *args],
        cwd=settings.repo_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SEC,
        check=False,
        creationflags=_NO_WINDOW,
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


# ─── Signed updates ──────────────────────────────────────────────────────
#
# A compromise of upstream `main` (a stolen token, a merged pull request)
# used to yield root on every Linux install at its next update: the update
# unit runs the pulled checkout's own script as root. The bound is a
# signature. Every commit the owner makes is SSH-signed, the allowed keys
# live in a root-owned file on the box, and nothing from an unverified
# checkout runs as root while that file exists.
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
# <why>; ...", what env-secrets added), never a tool's raw output, and they
# are what an owner must see. Every other detail (failed steps' output
# tails, the search helper's docker output) stays on the box.
_WARN_DETAIL_STEPS = frozenset({"sync-deps", "rollback-deps", "env-secrets"})
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


async def fetch() -> dict:
    """`git fetch` so subsequent behind/ahead counts compare against the live
    upstream. Returns ``{"ok": bool, "error": str|None}`` — offline / no
    remote surfaces as ``ok=False`` with the git stderr, never an exception.

    Under ``INTERNET_ACCESS=never`` it reports ``ok=False`` with the
    turned-off reason and runs no git at all."""
    if egress.internet_turned_off():
        return {"ok": False, "error": egress.TURNED_OFF_REASON}
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

    Returns ``{"behind": int, "ahead": int, "upstream": bool,
    "upstream_sha": str|None, "error": str|None}``. ``upstream_sha`` is the
    full SHA a pull would move to, which the version panel compares with a
    rolled-back ``bad_sha``. No tracking branch or an offline fetch →
    ``upstream=False`` plus an error string and zeroed counts. Never
    raises.

    Under ``INTERNET_ACCESS=never``: ``upstream=False`` with the
    turned-off reason, and no git runs (the counts against a stale
    upstream would only mislead)."""
    if egress.internet_turned_off():
        return {"behind": 0, "ahead": 0, "upstream": False,
                "upstream_sha": None, "error": egress.TURNED_OFF_REASON}
    fetched = await fetch()
    try:
        behind = await asyncio.to_thread(
            _run, "rev-list", "--count", "HEAD..@{u}"
        )
        ahead = await asyncio.to_thread(
            _run, "rev-list", "--count", "@{u}..HEAD"
        )
        upstream_sha = await asyncio.to_thread(_full_sha, "@{u}")
    except Exception as e:  # noqa: BLE001 — never raise into the endpoint
        return {"behind": 0, "ahead": 0, "upstream": False,
                "upstream_sha": None, "error": str(e)}

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
        }
    try:
        return {
            "behind": int(behind.stdout.strip() or 0),
            "ahead": int(ahead.stdout.strip() or 0),
            "upstream": True,
            "upstream_sha": upstream_sha,
            "error": fetched["error"],
        }
    except ValueError as e:
        return {"behind": 0, "ahead": 0, "upstream": False,
                "upstream_sha": None, "error": str(e)}


def _not_pulled(error: str, signature: dict | None = None) -> dict:
    return {"pulled": False, "new_sha": None, "error": error,
            "prev_sha": None, "signature": signature}


async def pull() -> dict:
    """A fast-forward pull, in three steps: ``git fetch``, verify the
    fetched tip's signature, ``git merge --ff-only <that tip>``. A
    deliberate, separate admin action (never run by a check). Returns
    ``{"pulled": bool, "new_sha": str|None, "error": str|None, "prev_sha":
    str|None, "signature": dict|None}``. A dirty or diverged tree fails the
    fast-forward and comes back ``pulled=False`` with the git stderr as
    ``error`` — we never force a merge or reset. Never raises.

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
    try:
        fetched = await asyncio.to_thread(_run, "fetch")
        if fetched.returncode != 0:
            return _not_pulled((fetched.stderr or "git fetch failed").strip())
        target = await asyncio.to_thread(_full_sha, "@{u}")
        if target is None:
            return _not_pulled(
                "no upstream branch is configured for the current branch, "
                "so there is nothing to pull (git branch --set-upstream-to)"
            )
        signature = await asyncio.to_thread(verify_commit, target)
    except FileNotFoundError:
        return _not_pulled("git not installed")
    except subprocess.TimeoutExpired:
        return _not_pulled("git pull timed out")
    except Exception as e:  # noqa: BLE001
        return _not_pulled(str(e))

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
        proc = await asyncio.to_thread(_run, "merge", "--ff-only", target)
    except FileNotFoundError:
        return _not_pulled("git not installed", signature)
    except subprocess.TimeoutExpired:
        return _not_pulled("git pull timed out", signature)
    except Exception as e:  # noqa: BLE001
        return _not_pulled(str(e), signature)

    if proc.returncode != 0:
        return _not_pulled((proc.stderr or "git pull failed").strip(), signature)
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
    }
