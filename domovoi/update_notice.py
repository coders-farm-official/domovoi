"""Update notices: "Domovoi 1.5.0 is available", for signed releases only.

Design note UPDATE-NOTICES (1.5.0). The house looks for a new **signed
release** (once a day under the internet answer Yes, by
:mod:`domovoi.workers.update_notice_check`, and whenever someone presses
**Check for updates**), says so on Home, and shows that release's three
Highlights in Settings → System → About & updates.

Words:

* **R**, the latest release: the commit at ``refs/remotes/origin/stable``
  after a fetch, with a digits-only tag ``vX.Y.Z`` pointing at it.
  Release candidates never move stable, so they never count.
* **signed**: R's ``DomovoiSetup.manifest.json`` from its GitHub release
  verifies against Domovoi's release key (namespace
  ``domovoi-windows-setup-manifest``), has kind ``domovoi-windows-setup``,
  and names R's tag and R's full commit. Anything else: no notice, no Home
  row, no highlights.
* **H**: the checkout's HEAD; **Base(H)**: the newest digits-only ``v`` tag
  that is an ancestor of H.

The "behind" rule (one rule, both channels): R is signed, R's commit is
not an ancestor of H, version(R) > version(Base(H)) (no Base: true), and
R's commit is not the update unit's rolled-back ``bad_sha``. Commits on
``main`` never make a notice: only a signed release can.

The highlights come from git, not from the GitHub page: ``git cat-file
blob <R>:docs/release/<tag>.md``, the commit the signed manifest names
(:mod:`domovoi.release_highlights` parses them as untrusted text). A house
on ``main`` whose tag file has none falls back to the same path on
``refs/remotes/origin/main`` (how 1.4.1's bullets reach main houses: a tag
never moves). A stable house has no fallback.

State: ``household_settings`` key ``update_check`` (V045; no migration),
written only here: the notice, the releases already verified (newest 3),
and the last automatic check. In memory while the database is away.

Nothing here installs anything: the Update button is the About card's own.
The offline kit and ``kit_update`` never import this module.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import subprocess
import time
from typing import Any

from domovoi import egress, git_run, git_version, self_restart
from domovoi.config import settings
from domovoi.release_highlights import parse_highlights

log = logging.getLogger(__name__)

STATE_KEY = "update_check"
MANIFEST_NAME = "DomovoiSetup.manifest.json"
MANIFEST_NAMESPACE = "domovoi-windows-setup-manifest"
MANIFEST_KIND = "domovoi-windows-setup"
#: Where a release's files are (android_app.RELEASE_DOWNLOAD).
RELEASE_DOWNLOAD = "https://github.com/coders-farm-official/domovoi/releases/download/"
#: The hosts a release-file download really uses: github.com and the two
#: storage hosts GitHub redirects it to. Every hop is held to these.
ASSET_HOSTS = frozenset({"github.com", "objects.githubusercontent.com",
                         "release-assets.githubusercontent.com"})
MANIFEST_MAX_BYTES = 64 * 1024
DOWNLOAD_TIMEOUT_SEC = 20.0
MAX_REDIRECTS = 3
USER_AGENT = "Domovoi update check"
KEEP_RELEASES = 3
#: A version as the house shows it: at most 14 characters, so it can't
#: break a line.
TAG_RE = re.compile(r"^v([0-9]{1,4})\.([0-9]{1,4})\.([0-9]{1,4})\Z")
_SHA_RE = re.compile(r"^[0-9a-f]{40}\Z")
_ERROR_MAX = 300
#: How long a HEAD read is reused by the notice read (Home asks often).
_HEAD_TTL_SEC = 30.0

WHY_OFF = ("setting_off", "internet_sometimes", "internet_never", "internet_unanswered", "no_git")

#: The release key, as install.sh and the Windows tray pin it. A test swaps
#: it for the throwaway key of windows/tests/release-sig/.
release_key: str | None = None
#: httpx transport for tests (a MockTransport); None in production.
_TRANSPORT: Any = None


def version_of(tag: str) -> tuple[int, int, int] | None:
    m = TAG_RE.match(tag or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


# ─── the signed manifest ─────────────────────────────────────────────────


def manifest_problem(manifest: bytes, sig: bytes, tag: str, commit: str,
                     key: str | None = None) -> str | None:
    """None when ``manifest`` is the signed manifest of release ``tag`` at
    ``commit``; otherwise why not, in plain words. Pure."""
    from domovoi.offline_kit.sshsig import RELEASE_KEY, parse_public_key, sshsig_problem

    pub = parse_public_key(key or release_key or RELEASE_KEY)
    if pub is None:
        return "no release key"
    problem = sshsig_problem(sig, manifest, pub, MANIFEST_NAMESPACE)
    if problem:
        return problem
    try:
        doc = json.loads(manifest.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "the signed manifest isn't JSON"
    if not isinstance(doc, dict):
        return "the signed manifest isn't a manifest"
    if doc.get("kind") != MANIFEST_KIND:
        return "the signed manifest is for something else"
    if doc.get("tag") != tag or doc.get("commit") != commit:
        return "the signed manifest names another release"
    return None


class _Unreachable(Exception):
    pass


async def _only_asset_hosts(request: Any) -> None:
    host = (request.url.host or "").lower()
    if request.url.scheme != "https" or host not in ASSET_HOSTS:
        raise _Unreachable(f"refused a request to {host!r}")


async def _download(url: str) -> bytes:
    """One small release file, at most :data:`MANIFEST_MAX_BYTES`, through
    the egress gate, every hop held to :data:`ASSET_HOSTS`."""
    kwargs: dict[str, Any] = {
        "timeout": DOWNLOAD_TIMEOUT_SEC, "follow_redirects": True, "max_redirects": MAX_REDIRECTS,
        "event_hooks": {"request": [_only_asset_hosts]},
    }
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT
    try:
        async with egress.async_client(**kwargs) as client:
            async with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as resp:
                if resp.status_code != 200:
                    raise _Unreachable(f"HTTP {resp.status_code}")
                try:
                    declared = int(resp.headers.get("content-length") or 0)
                except ValueError:
                    declared = 0
                if declared > MANIFEST_MAX_BYTES:
                    raise _Unreachable("too large")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > MANIFEST_MAX_BYTES:
                        raise _Unreachable("too large")
                return bytes(buf)
    except _Unreachable:
        raise
    except Exception as e:  # noqa: BLE001 — offline, refused, a timeout: not signed now
        raise _Unreachable(type(e).__name__) from e


async def verify_release(tag: str, commit: str) -> bool | None:
    """Whether release ``tag`` at ``commit`` is signed: True, False (its
    manifest doesn't check out), or None (it couldn't be downloaded).
    Tests replace this function (no network)."""
    base = f"{RELEASE_DOWNLOAD}{tag}/{MANIFEST_NAME}"
    try:
        manifest = await _download(base)
        sig = await _download(base + ".sig")
    except _Unreachable as e:
        log.info("update check: %s's signed manifest not read (%s)", tag, e)
        return None
    problem = manifest_problem(manifest, sig, tag, commit)
    if problem:
        log.warning("update check: %s is not a signed release: %s", tag, problem)
        return False
    return True


# ─── git (blocking) ──────────────────────────────────────────────────────


def _git(*args: str, binary: bool = False) -> subprocess.CompletedProcess:
    return git_run.run(["git", "-c", "advice.graftFileDeprecated=false", *args],
                       cwd=settings.repo_dir, timeout=git_version._GIT_TIMEOUT_SEC,
                       env=git_version._GIT_ENV, binary=binary)


def _full(rev: str) -> str | None:
    try:
        return git_version._full_sha(rev)
    except Exception:  # noqa: BLE001 — no git
        return None


def _ancestor(older: str, newer: str) -> bool:
    try:
        return _git("merge-base", "--is-ancestor", older, newer).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def stable_release() -> tuple[str, str] | None:
    """Blocking. ``(tag, commit)`` of R as last fetched, or None (no
    stable branch, or no digits-only tag on its commit)."""
    commit = _full("refs/remotes/origin/stable")
    if not commit:
        return None
    try:
        proc = _git("tag", "--points-at", commit)
    except Exception:  # noqa: BLE001
        return None
    tags = [t for t in proc.stdout.split() if version_of(t)] if proc.returncode == 0 else []
    if not tags:
        return None
    return max(tags, key=lambda t: version_of(t) or (0, 0, 0)), commit


def base_version(head: str) -> tuple[int, int, int] | None:
    """Blocking. The newest digits-only ``v`` tag that is an ancestor of
    ``head``, or None."""
    try:
        proc = _git("tag", "--merged", head, "--list", "v[0-9]*")
    except Exception:  # noqa: BLE001
        return None
    found = [v for v in (version_of(t) for t in proc.stdout.split()) if v] if proc.returncode == 0 else []
    return max(found) if found else None


_RELATION_CACHE: dict[tuple, tuple[str, int | None]] = {}


def relation(tag: str, commit: str, head: str, bad_sha: str | None) -> tuple[str, int | None]:
    """Blocking. ``(relation, changes_since)`` of H to the SIGNED release
    ``tag`` at ``commit``: ``same``, ``ahead`` (with the first-parent count
    of R..H), ``behind`` (the rule in the module doc), else ``unknown``.
    Memoised per (H, R, bad_sha), like the label cache."""
    key = (head, tag, commit, bad_sha)
    hit = _RELATION_CACHE.get(key)
    if hit is not None:
        return hit
    if head == commit:
        out: tuple[str, int | None] = ("same", None)
    elif _ancestor(commit, head):
        try:
            proc = _git("rev-list", "--count", "--first-parent", f"{commit}..{head}")
            n = int(proc.stdout.strip()) if proc.returncode == 0 else None
        except Exception:  # noqa: BLE001
            n = None
        out = ("ahead", n)
    else:
        base = base_version(head)
        newer = base is None or (version_of(tag) or (0, 0, 0)) > base
        out = ("behind", None) if newer and commit != bad_sha else ("unknown", None)
    if len(_RELATION_CACHE) > 64:
        _RELATION_CACHE.clear()
    _RELATION_CACHE[key] = out
    return out


_LOGGED: set[tuple[str, str]] = set()


def read_highlights(rev: str, tag: str) -> list[str]:
    """Blocking. The highlights of ``docs/release/<tag>.md`` at ``rev``,
    or ``[]`` (logged once per tag and source at INFO)."""
    from domovoi.release_highlights import MAX_FILE_BYTES

    path = f"{rev}:docs/release/{tag}.md"
    why = None
    try:
        size = _git("cat-file", "-s", path)
        if size.returncode != 0:
            why = "no such file"
        elif int(size.stdout.strip() or 0) > MAX_FILE_BYTES:
            why = "the file is too large"
        else:
            blob = _git("cat-file", "blob", path, binary=True)
            items = parse_highlights(blob.stdout) if blob.returncode == 0 else []
            if items:
                return items
            why = "no valid Highlights section"
    except Exception as e:  # noqa: BLE001 — no git, a timeout: no bullets
        why = type(e).__name__
    if (tag, rev) not in _LOGGED:
        _LOGGED.add((tag, rev))
        log.info("update check: no highlights for %s at %s (%s)", tag, rev[:20], why)
    return []


_MAIN_MEMO: dict[tuple[str, str], list[str]] = {}


def _main_highlights(tag: str) -> list[str]:
    main = _full("refs/remotes/origin/main")
    if not main:
        return []
    key = (main, tag)
    if key not in _MAIN_MEMO:
        if len(_MAIN_MEMO) > 8:
            _MAIN_MEMO.clear()
        _MAIN_MEMO[key] = read_highlights(main, tag)
    return _MAIN_MEMO[key]


_HEAD: tuple[str | None, float] | None = None


def head_sha() -> str | None:
    """Blocking. HEAD's full SHA (reused for a few seconds), or None
    without git."""
    global _HEAD
    now = time.monotonic()
    if _HEAD is not None and now - _HEAD[1] < _HEAD_TTL_SEC:
        return _HEAD[0]
    sha = _full("HEAD")
    _HEAD = (sha, now)
    return sha


def forget_caches() -> None:
    global _HEAD
    _HEAD = None
    _RELATION_CACHE.clear()
    _MAIN_MEMO.clear()


# ─── state (household_settings 'update_check') ───────────────────────────

_STATE: dict[str, Any] | None = None


def _empty() -> dict[str, Any]:
    return {"notice": None, "releases": {}, "last_auto": None}


def _clean(value: Any) -> dict[str, Any]:
    """The stored row, held to its shape (written only here, but read as
    data)."""
    out = _empty()
    if not isinstance(value, dict):
        return out
    n = value.get("notice")
    if (isinstance(n, dict) and version_of(n.get("tag")) and _SHA_RE.match(str(n.get("commit") or ""))
            and n.get("found_by") in ("auto", "manual") and isinstance(n.get("found_at"), str)):
        out["notice"] = {"tag": n["tag"], "version": n["tag"][1:], "commit": n["commit"],
                         "found_by": n["found_by"], "found_at": n["found_at"][:40]}
    rels = value.get("releases")
    if isinstance(rels, dict):
        for tag, r in rels.items():
            if (version_of(tag) and isinstance(r, dict) and _SHA_RE.match(str(r.get("commit") or ""))
                    and r.get("signed") is True):
                hl = r.get("highlights")
                hl = [h for h in hl if isinstance(h, str) and 0 < len(h) <= 80][:3] if isinstance(hl, list) else []
                out["releases"][tag] = {"commit": r["commit"], "signed": True,
                                        "highlights": hl if len(hl) == 3 else []}
    last = value.get("last_auto")
    if isinstance(last, dict) and isinstance(last.get("at"), str):
        err = last.get("error")
        out["last_auto"] = {"at": last["at"][:40], "ok": bool(last.get("ok")),
                            "error": err[:_ERROR_MAX] if isinstance(err, str) else None,
                            "ok_at": last["ok_at"][:40] if isinstance(last.get("ok_at"), str) else None}
    return out


async def _db_load() -> Any:
    from sqlalchemy import text

    from domovoi.db.session import session_scope

    async with session_scope() as s:
        row = (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                               {"k": STATE_KEY})).first()
    value = row[0] if row is not None else None
    return json.loads(value) if isinstance(value, str) else value


async def _db_save(value: dict[str, Any]) -> None:
    from sqlalchemy import text

    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(text(
            "INSERT INTO household_settings (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
        ), {"k": STATE_KEY, "v": json.dumps(value)})


async def load_state() -> dict[str, Any]:
    """The state, read from the database once (then kept in memory: this
    process is its only writer). With the database away, the in-memory
    state, and the database is asked again next time."""
    global _STATE
    if _STATE is not None:
        return _STATE
    global _MEMORY
    try:
        loaded = _clean(await _db_load())
    except Exception as e:  # noqa: BLE001 — the database is down: memory until it's back
        log.info("update check: state not read (%s); kept in memory", type(e).__name__)
        return _memory()
    if _MEMORY is not None:
        # What was learned while the database was away is newer: it wins.
        held, _MEMORY = _MEMORY, None
        await save_state(held)
        if _STATE is None:
            _STATE = held
        return _STATE
    _STATE = loaded
    return _STATE


_MEMORY: dict[str, Any] | None = None


def _memory() -> dict[str, Any]:
    global _MEMORY
    if _MEMORY is None:
        _MEMORY = _empty()
    return _MEMORY


async def save_state(state: dict[str, Any]) -> None:
    global _STATE
    rels = state.get("releases") or {}
    if len(rels) > KEEP_RELEASES:
        newest = sorted(rels, key=lambda t: version_of(t) or (0, 0, 0), reverse=True)[:KEEP_RELEASES]
        state["releases"] = {t: rels[t] for t in newest}
    try:
        await _db_save(state)
        _STATE = state
    except Exception as e:  # noqa: BLE001
        log.info("update check: state not saved (%s); kept in memory", type(e).__name__)


def reset_for_tests() -> None:
    global _STATE, _MEMORY, _RUNS, _LAST, _LOCK
    _STATE = None
    _MEMORY = None
    _RUNS = 0
    _LAST = None
    _LOCK = None
    _LOGGED.clear()
    forget_caches()


# ─── the evaluation ──────────────────────────────────────────────────────


def why_off(git_ok: bool) -> str | None:
    """Why automatic checks aren't running, or None when they are."""
    if not git_ok:
        return "no_git"
    answer = egress.policy()
    if answer == "never":
        return "internet_never"
    if answer == "":
        return "internet_unanswered"
    if answer == "sometimes":
        return "internet_sometimes"
    if not settings.update_auto_check:
        return "setting_off"
    return None


def _highlights(state: dict[str, Any], tag: str, commit: str, channel: str | None) -> list[str]:
    """Blocking. R's highlights: the tag's file (cached per tag), then, on
    a house following main only, main's copy of it."""
    rel = (state.get("releases") or {}).get(tag)
    items = list(rel["highlights"]) if rel and rel.get("commit") == commit else read_highlights(commit, tag)
    if not items and channel == "main":
        items = _main_highlights(tag)
    return items


def _updating() -> bool:
    try:
        last = git_version.read_last_update()
        return bool(self_restart.underway(last))
    except Exception:  # noqa: BLE001
        return False


def _payload(notice: dict[str, Any] | None, highlights: list[str], updating: bool) -> dict[str, Any]:
    if not notice:
        return {"available": False, "updating": False}
    return {"available": True, "version": notice["version"], "highlights": list(highlights),
            "found_by": notice["found_by"], "found_at": notice["found_at"], "updating": updating}


async def _live_notice(state: dict[str, Any], head: str | None, bad: str | None) -> dict[str, Any] | None:
    """The stored notice, re-evaluated now: dropped (and saved) when an
    automatic one meets the setting off, its commit is the rolled-back
    one, or the house is no longer behind it; hidden (kept) under the
    internet answer No or without git."""
    n = state.get("notice")
    if not n:
        return None
    if head is None:
        return None
    drop = False
    if n["found_by"] == "auto" and not settings.update_auto_check:
        drop = True
    elif n["commit"] == bad:
        drop = True
    else:
        rel, _ = await asyncio.to_thread(relation, n["tag"], n["commit"], head, bad)
        drop = rel != "behind"
    if drop:
        state["notice"] = None
        await save_state(state)
        return None
    if egress.internet_turned_off():
        return None
    return n


def _bad_sha() -> str | None:
    try:
        return git_version._bad_sha(git_version.read_last_update())
    except Exception:  # noqa: BLE001
        return None


async def notice() -> dict[str, Any]:
    """``GET /v1/updates/notice``: what Home's row shows, and nothing more
    (no SHA, no channel, no error text)."""
    state = await load_state()
    head = await asyncio.to_thread(head_sha)
    bad = await asyncio.to_thread(_bad_sha)
    n = await _live_notice(state, head, bad)
    if not n:
        return _payload(None, [], False)
    channel = await asyncio.to_thread(lambda: git_version.tracking()["channel"])
    hl = await asyncio.to_thread(_highlights, state, n["tag"], n["commit"], channel)
    return _payload(n, hl, await asyncio.to_thread(_updating))


async def updates_state() -> dict[str, Any]:
    """The ``updates`` key of the version read and of the check."""
    state = await load_state()
    head = await asyncio.to_thread(head_sha)
    bad = await asyncio.to_thread(_bad_sha)
    why = why_off(head is not None)
    last = state.get("last_auto") or {}
    out: dict[str, Any] = {
        "auto_check": {"enabled": bool(settings.update_auto_check), "active": why is None, "why_off": why,
                       "last_at": last.get("at"), "last_ok": last.get("ok") if last else None},
        "latest_release": None, "relation": "unknown", "changes_since": None,
        "notice": {"available": False, "updating": False},
    }
    if head is None:
        return out
    n = await _live_notice(state, head, bad)
    channel = await asyncio.to_thread(lambda: git_version.tracking()["channel"])
    found = await asyncio.to_thread(stable_release)
    if found:
        tag, commit = found
        rel = (state.get("releases") or {}).get(tag)
        signed = bool(rel and rel.get("commit") == commit and rel.get("signed"))
        hl = await asyncio.to_thread(_highlights, state, tag, commit, channel) if signed else []
        out["latest_release"] = {"version": tag[1:], "tag": tag, "commit": commit, "signed": signed,
                                 "highlights": hl}
        if signed:
            out["relation"], out["changes_since"] = await asyncio.to_thread(relation, tag, commit, head, bad)
            if out["relation"] != "ahead":
                out["changes_since"] = None
    if n:
        nh = out["latest_release"]["highlights"] if (out["latest_release"] and
                                                     out["latest_release"]["tag"] == n["tag"]) else \
            await asyncio.to_thread(_highlights, state, n["tag"], n["commit"], channel)
        out["notice"] = _payload(n, nh, await asyncio.to_thread(_updating))
    return out


async def _evaluate(found_by: str) -> bool:
    """After a fetch: verify R, read its highlights, and move the notice
    (the table in design note §6). False when the check failed (R's
    manifest couldn't be read): the notice is then unchanged."""
    state = await load_state()
    found = await asyncio.to_thread(stable_release)
    head = await asyncio.to_thread(head_sha)
    if not found or head is None:
        return True
    tag, commit = found
    rels = state.setdefault("releases", {})
    rel = rels.get(tag)
    if not (rel and rel.get("commit") == commit and rel.get("signed")):
        signed = await verify_release(tag, commit)
        if signed is None:
            return False
        if not signed:
            return True   # not a signed release: no notice, nothing else changes
        items = await asyncio.to_thread(read_highlights, commit, tag)
        rels[tag] = {"commit": commit, "signed": True, "highlights": items}
    bad = await asyncio.to_thread(_bad_sha)
    rel_now, _ = await asyncio.to_thread(relation, tag, commit, head, bad)
    n = state.get("notice")
    if rel_now == "behind":
        if found_by == "auto" and not settings.update_auto_check:
            pass
        elif n is None:
            state["notice"] = {"tag": tag, "version": tag[1:], "commit": commit,
                               "found_by": found_by, "found_at": _now_iso()}
        else:
            source = "manual" if found_by == "manual" else n["found_by"]
            if n["tag"] != tag or n["commit"] != commit:
                state["notice"] = {"tag": tag, "version": tag[1:], "commit": commit,
                                   "found_by": source, "found_at": _now_iso()}
            elif source != n["found_by"]:
                state["notice"] = {**n, "found_by": source}
    elif n and n["tag"] == tag:
        state["notice"] = None
    await save_state(state)
    return True


_LOCK: asyncio.Lock | None = None
_RUNS = 0
_LAST: dict[str, Any] | None = None


def _lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


def busy() -> bool:
    return _lock().locked()


async def _promote_manual() -> None:
    state = await load_state()
    n = state.get("notice")
    if n and n["found_by"] == "auto":
        state["notice"] = {**n, "found_by": "manual"}
        await save_state(state)


async def run_check(found_by: str) -> dict[str, Any]:
    """The one check, automatic or pressed: ``git_version.commits_behind``
    (the fetch), then the signed-release step. Answers commits_behind's
    dict plus ``updates``. One at a time: a press during another check
    waits for it and answers its result."""
    global _RUNS, _LAST
    seen = _RUNS
    async with _lock():
        if found_by == "manual" and _RUNS != seen and _LAST is not None:
            await _promote_manual()
            return {**_LAST, "updates": await updates_state()}
        checked = await git_version.commits_behind()
        forget_caches()   # the fetch may bring R, tags and a new main
        ok = False
        error = checked.get("error")
        if not egress.internet_turned_off():
            fetched = git_version.last_fetch()
            if fetched and fetched.get("ok"):
                ok = await _evaluate(found_by)
                if not ok:
                    error = "the release's signed manifest couldn't be downloaded"
            else:
                error = (fetched or {}).get("error") or error or "the update server didn't answer"
        if found_by == "auto":
            state = await load_state()
            prev = state.get("last_auto") or {}
            at = _now_iso()
            state["last_auto"] = {"at": at, "ok": ok, "error": None if ok else str(error or "")[:_ERROR_MAX],
                                  "ok_at": at if ok else prev.get("ok_at")}
            await save_state(state)
        _RUNS += 1
        _LAST = checked
        return {**checked, "updates": await updates_state()}


def last_auto_times() -> tuple[float | None, float | None, bool | None]:
    """(last attempt, last success, last attempt ok) as Unix times, from the
    state in memory (call :func:`load_state` first)."""
    state = _STATE if _STATE is not None else _memory()
    last = state.get("last_auto") or {}
    return _parse_iso(last.get("at")), _parse_iso(last.get("ok_at")), last.get("ok") if last else None
