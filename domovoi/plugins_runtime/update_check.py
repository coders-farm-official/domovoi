"""Plugin update checks: is there a newer release on the plugin's GitHub repo?

Owner decision 2026-11-22. For each installed plugin whose source repo is
known, the core asks GitHub's REST API for the repo's releases (its tags
when it has no releases) and compares the newest ``vX.Y.Z`` with the
installed version, by semver. The Plugins page shows the answer; updating
is the ordinary GitHub install path at that tag (``POST
/v1/plugins/{slug}/upgrade`` with ``{"github_url": ".../repo@vX.Y.Z"}``),
so the same trust screen, checks and confirmation run. Nothing updates by
itself.

Where the repo comes from:

* a plugin installed from GitHub: its registry row's ``source_ref``
  (``https://github.com/<owner>/<repo>[@ref]@<ref>``, written by
  :func:`domovoi.plugins_runtime.installer.download_github_zip`). Checked
  by itself, once a day (``origin = "github"``);
* a plugin installed from a zip: the manifest's ``[plugin] homepage``, only
  when it is a ``https://github.com/<owner>/<repo>`` address. Checked only
  when an admin asks (**check GitHub for updates**; ``origin =
  "homepage"``): the household didn't choose GitHub for it;
* bundled and dev-mode plugins, and anything else: never.

Updating: the page asks the upgrade route for the release's own
``<slug>-X.Y.Z.zip``, checked against the release's ``SHA256SUMS``
(:func:`download_release_asset`: ``api.github.com``, then ``github.com``
redirecting to ``objects.githubusercontent.com`` /
``release-assets.githubusercontent.com``, every hop held to
:data:`ASSET_HOSTS`); a release without both files falls back to the tag's
source archive on ``codeload.github.com`` (the existing path) and the page
says so. A checksum mismatch never falls back.

Egress of the check itself: only ``api.github.com``, through :func:`domovoi.egress.async_client`, no
token and no credentials, a request hook that refuses any other host
(redirect hops included). ``INTERNET_ACCESS=never`` makes no request at
all. ``ETag`` / ``If-None-Match`` is kept per URL (a 304 costs no rate
limit), and a rate-limited answer (403/429 with ``X-RateLimit-Remaining:
0`` or ``Retry-After``) stops every check until GitHub's reset.

A private repo answers 404 (or 403) to a caller without credentials, and a
network failure answers nothing: both read ``unreachable`` ("can't check
for updates"), never "up to date". So does an answer the check can't read:
it ends that plugin's check, not the run (L37, review 2026-11-23).

The repository's identity (M6, review 2026-11-23): ``owner/repo`` is only
a name, which a renamed, transferred or deleted-and-re-registered repo
hands to someone else. Each check first asks ``GET /repos/<owner>/<repo>``
WITHOUT following a redirect and keeps the answer's numeric repository id
and owner id per plugin (the ``plugin_repo_pins`` table, V081; the
``~/.domovoi/plugin_repo_pins.json`` file that held them before is
imported once, :func:`pins_path`). They are read when a plugin is staged
from GitHub and pinned when the install is confirmed
(:func:`stage_pin`, :func:`record_install_pin`); a plugin installed
before that is pinned by its first check's answer. A later answer that
redirects (GitHub's answer for a renamed or transferred repo) or names
another id reads ``repo_changed`` ("the repository moved or changed
owner: reinstall from the new address"), and no update is offered.
:func:`download_release_asset`, and the staging of any update from
GitHub, hold the update to the same pin. Installing the plugin from
another repo address (an admin's deliberate choice) pins that repo
afresh.

The answers live in this process's memory: a restart checks again on the
worker's first tick.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Iterable

from fastapi import APIRouter, Depends, HTTPException, Request

from domovoi import egress
from domovoi.auth import require_admin

log = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
# The hosts plugin updates may reach: the check asks the API; the update
# (the existing install path) downloads from codeload.
ALLOWED_HOSTS = frozenset({"api.github.com", "codeload.github.com"})
CHECK_HOSTS = frozenset({"api.github.com"})

CHECK_INTERVAL_SEC = 24 * 3600.0
REQUEST_TIMEOUT_SEC = 20.0
NOTES_MAX_CHARS = 8000
USER_AGENT = "domovoi-plugin-update-check (+github.com/coders-farm-official/domovoi)"

# Answer states (the page's words for each are in web/static/plugins.jsx).
UPDATE_AVAILABLE = "update_available"
UP_TO_DATE = "up_to_date"
UNREACHABLE = "unreachable"       # private, gone, or no answer
RATE_LIMITED = "rate_limited"
INTERNET_OFF = "internet_off"
NOT_CHECKED = "not_checked"
REPO_CHANGED = "repo_changed"     # moved, transferred or re-registered (M6)


# ─── semver ─────────────────────────────────────────────────────────────

_SEMVER = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?\Z"
)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: tuple[str, ...] = ()

    @property
    def is_prerelease(self) -> bool:
        return bool(self.pre)

    def __str__(self) -> str:
        core = f"{self.major}.{self.minor}.{self.patch}"
        return f"{core}-{'.'.join(self.pre)}" if self.pre else core


def parse_version(text: str | None) -> Version | None:
    """``X.Y.Z`` or ``X.Y.Z-pre``, with an optional leading ``v``. Build
    metadata (``+...``) is not accepted: a tag carrying it could not be
    named in a GitHub install URL."""
    if not isinstance(text, str):
        return None
    m = _SEMVER.match(text.strip())
    if not m:
        return None
    pre = tuple(m.group(4).split(".")) if m.group(4) else ()
    return Version(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre)


def _cmp_ident(a: str, b: str) -> int:
    a_num, b_num = a.isdigit(), b.isdigit()
    if a_num and b_num:
        return (int(a) > int(b)) - (int(a) < int(b))
    if a_num != b_num:
        return -1 if a_num else 1  # numeric identifiers sort first
    return (a > b) - (a < b)


def compare(a: Version, b: Version) -> int:
    """Semver 2.0 precedence: -1, 0 or 1."""
    ta, tb = (a.major, a.minor, a.patch), (b.major, b.minor, b.patch)
    if ta != tb:
        return (ta > tb) - (ta < tb)
    if not a.pre or not b.pre:
        # a release outranks any pre-release of the same X.Y.Z
        return (not a.pre) - (not b.pre)
    for x, y in zip(a.pre, b.pre):
        c = _cmp_ident(x, y)
        if c:
            return c
    return (len(a.pre) > len(b.pre)) - (len(a.pre) < len(b.pre))


# ─── where a plugin's repo is ───────────────────────────────────────────

_NAME = r"[A-Za-z0-9_.-]+"
_REPO_URL = re.compile(
    rf"^https://github\.com/({_NAME})/({_NAME}?)(?:\.git)?(?=\Z|[@/#?])"
)


@dataclass(frozen=True)
class SourceRepo:
    owner: str
    repo: str
    origin: str  # "github" (checked by itself) | "homepage" (on request)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


def _repo_from_url(url: Any) -> tuple[str, str] | None:
    if not isinstance(url, str):
        return None
    m = _REPO_URL.match(url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if not repo or owner in (".", "..") or repo in (".", ".."):
        return None
    return owner, repo


def source_repo(record: Any) -> SourceRepo | None:
    """The GitHub repo updates for this plugin are looked for in, or None
    (bundled, dev-mode, uninstalled, or a zip with no GitHub homepage)."""
    if getattr(record, "bundled", False):
        return None
    if getattr(record, "status", None) == "uninstalled":
        return None
    source = getattr(record, "install_source", None)
    if source == "github":
        found = _repo_from_url(getattr(record, "source_ref", None))
        if found:
            return SourceRepo(found[0], found[1], "github")
    if source in ("github", "zip"):
        manifest = getattr(record, "manifest", None) or {}
        plugin_tbl = manifest.get("plugin") if isinstance(manifest, dict) else None
        found = _repo_from_url((plugin_tbl or {}).get("homepage"))
        if found:
            return SourceRepo(found[0], found[1], "homepage")
    return None


# ─── release notes ──────────────────────────────────────────────────────

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")


def clean_notes(body: Any) -> str:
    """Release notes as PLAIN TEXT: control and bidi-override characters
    removed, newlines normalised, capped at :data:`NOTES_MAX_CHARS`. The page
    renders the string as a text node, never as HTML or markdown."""
    if not isinstance(body, str):
        return ""
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text).strip()
    if len(text) > NOTES_MAX_CHARS:
        text = text[: NOTES_MAX_CHARS - 1].rstrip() + "…"
    return text


# ─── choosing the newest release ────────────────────────────────────────


@dataclass(frozen=True)
class Candidate:
    version: Version
    tag: str
    notes: str
    published_at: str | None
    assets: tuple[str, ...] = ()


def pick_latest(
    releases: Iterable[dict[str, Any]], installed: Version
) -> Candidate | None:
    """The newest release tagged ``vX.Y.Z`` (or ``X.Y.Z``). Drafts never
    count; pre-releases (GitHub's flag or a ``-pre`` tag) count only while
    the installed version is a pre-release itself."""
    best: Candidate | None = None
    for r in releases:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        tag = r.get("tag_name") if "tag_name" in r else r.get("name")
        v = parse_version(tag)
        if v is None:
            continue
        if (v.is_prerelease or bool(r.get("prerelease"))) and not installed.is_prerelease:
            continue
        if best is None or compare(v, best.version) > 0:
            best = Candidate(
                v, str(tag), clean_notes(r.get("body")),
                r.get("published_at") if isinstance(r.get("published_at"), str) else None,
                tuple(
                    str(a.get("name")) for a in (r.get("assets") or [])
                    if isinstance(a, dict) and isinstance(a.get("name"), str)
                ),
            )
    return best


# ─── the repository's pinned identity (M6) ──────────────────────────────


def pins_path() -> Path:
    """Where the pins were kept before the ``plugin_repo_pins`` table (V081):
    read once, into the table, then renamed ``*.imported``."""
    override = os.environ.get("DOMOVOI_PLUGIN_REPO_PINS")
    return Path(override) if override else Path.home() / ".domovoi" / "plugin_repo_pins.json"


_json_imported = False


async def _import_json_pins(s: Any) -> None:
    """Once per process: the old JSON file's pins into the table (a pin the
    table already has wins), and the file renamed so it is read only once."""
    global _json_imported
    if _json_imported:
        return
    _json_imported = True
    path = pins_path()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        log.warning("plugin update check: the old repository pins file can't be read (%s)",
                    type(e).__name__)
        return
    from sqlalchemy import text

    n = 0
    for slug, pin in (doc.items() if isinstance(doc, dict) else []):
        if not isinstance(pin, dict):
            continue
        rid, oid, repo = pin.get("repo_id"), pin.get("owner_id"), pin.get("repo")
        if type(rid) is not int or type(oid) is not int or not isinstance(repo, str):
            continue
        await s.execute(text(
            "INSERT INTO plugin_repo_pins (slug, repo, repo_id, owner_id, pinned_by) "
            "VALUES (:s, :r, :i, :o, 'import') ON CONFLICT (slug) DO NOTHING"),
            {"s": str(slug), "r": repo, "i": rid, "o": oid})
        n += 1
    try:
        os.replace(path, path.with_name(path.name + ".imported"))
    except OSError as e:
        log.warning("plugin update check: the old repository pins file stays (%s)", e)
    log.info("plugin update check: %d repository pin(s) moved into the database", n)


async def load_pins() -> dict[str, dict[str, Any]]:
    """Every plugin's pin: ``{slug: {repo, repo_id, owner_id, pinned_by}}``."""
    from sqlalchemy import text

    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await _import_json_pins(s)
        rows = (await s.execute(text(
            "SELECT slug, repo, repo_id, owner_id, pinned_by FROM plugin_repo_pins"))).all()
    return {r[0]: {"repo": r[1], "repo_id": int(r[2]), "owner_id": int(r[3]),
                   "pinned_by": r[4]} for r in rows}


async def _get_pin(slug: str) -> dict[str, Any] | None:
    from sqlalchemy import text

    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await _import_json_pins(s)
        row = (await s.execute(text(
            "SELECT repo, repo_id, owner_id FROM plugin_repo_pins WHERE slug = :s"),
            {"s": slug})).first()
    if row is None:
        return None
    return {"repo": row[0], "repo_id": int(row[1]), "owner_id": int(row[2])}


async def save_pin(slug: str, full_name: str, ident: tuple[int, int], *, by: str) -> None:
    """Pin ``slug`` to ``full_name``'s ids (replacing any pin it had)."""
    from sqlalchemy import text

    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await _import_json_pins(s)
        await s.execute(text(
            "INSERT INTO plugin_repo_pins (slug, repo, repo_id, owner_id, pinned_by, pinned_at) "
            "VALUES (:s, :r, :i, :o, :b, NOW()) ON CONFLICT (slug) DO UPDATE SET "
            "repo = EXCLUDED.repo, repo_id = EXCLUDED.repo_id, owner_id = EXCLUDED.owner_id, "
            "pinned_by = EXCLUDED.pinned_by, pinned_at = EXCLUDED.pinned_at"),
            {"s": slug, "r": full_name, "i": ident[0], "o": ident[1], "b": by})


class RepoChanged(Exception):
    """The repository at the plugin's recorded address is not the one it
    was installed from (moved, transferred, or a new repo under the old
    name)."""


def _identity(data: Any) -> tuple[int, int] | None:
    if not isinstance(data, dict):
        return None
    rid = data.get("id")
    owner = data.get("owner")
    oid = owner.get("id") if isinstance(owner, dict) else None
    if type(rid) is not int or type(oid) is not int:
        return None
    return rid, oid


_REDIRECTS = (301, 302, 303, 307, 308)


def answer_identity(status: int, data: Any) -> tuple[int, int]:
    """The ids in a ``GET /repos/<owner>/<repo>`` answer (not redirected).
    Raises :class:`RepoChanged` for a redirect, ``_Unreachable`` for an
    answer that names no repository."""
    if status in _REDIRECTS:
        raise RepoChanged("GitHub sends this address to another repository")
    ident = _identity(data) if status == 200 else None
    if ident is None:
        raise _Unreachable(f"GitHub answered {status}")
    return ident


async def check_pin(slug: str, full_name: str, status: int, data: Any) -> None:
    """Hold ``slug``'s repo answer (``GET /repos/<owner>/<repo>``, not
    redirected) to its pin, pinning it when there is none for this
    address (a plugin installed before pins were taken at install).
    Raises :class:`RepoChanged`, or ``_Unreachable`` for an answer that
    names no repository."""
    ident = answer_identity(status, data)
    pin = await _get_pin(slug)
    if pin and str(pin.get("repo", "")).lower() == full_name.lower():
        if (pin.get("repo_id"), pin.get("owner_id")) != ident:
            raise RepoChanged("a different repository now has this address")
        return
    await save_pin(slug, full_name, ident, by="check")


# ─── the HTTP side ──────────────────────────────────────────────────────

# Tests hand a fake transport in here (httpx.MockTransport): no real network.
_TRANSPORT: Any = None


class HostNotAllowed(Exception):
    pass


async def _only_check_hosts(request: Any) -> None:
    host = (request.url.host or "").lower()
    if request.url.scheme != "https" or host not in CHECK_HOSTS:
        raise HostNotAllowed(f"plugin update check refused a request to {host!r}")


@dataclass
class _Cached:
    etag: str
    data: Any


class _RateLimited(Exception):
    pass


class _Unreachable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Checker:
    """Process-wide state: the answers, the ETag cache, the rate-limit
    block. One check runs at a time."""

    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}
        self.etags: dict[str, _Cached] = {}
        self.blocked_until: float = 0.0
        self.last_run_at: float | None = None
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        self.__init__()

    # -- rate limits --------------------------------------------------

    def _note_limits(self, resp: Any) -> None:
        h = resp.headers
        retry_after = h.get("retry-after")
        remaining = h.get("x-ratelimit-remaining")
        reset = h.get("x-ratelimit-reset")
        until = 0.0
        if retry_after and retry_after.strip().isdigit():
            until = time.time() + int(retry_after.strip())
        elif remaining is not None and remaining.strip() == "0" and reset and reset.strip().isdigit():
            until = float(int(reset.strip()))
        if until > self.blocked_until:
            self.blocked_until = until

    def rate_limited(self) -> bool:
        return self.blocked_until > time.time()

    # -- one GET with ETag reuse ---------------------------------------

    async def _get_json(self, client: Any, url: str, *, identity: bool = False) -> Any:
        """The answer's JSON. ``identity``: the repo itself, NOT followed
        through a redirect (M6); returns ``(status, data)``."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        cached = self.etags.get(url)
        if cached is not None:
            headers["If-None-Match"] = cached.etag
        try:
            resp = await client.get(url, headers=headers, follow_redirects=not identity)
        except HostNotAllowed:
            raise _Unreachable("redirected off api.github.com")
        except Exception as e:  # noqa: BLE001 - network, TLS, egress refusal
            if isinstance(e, egress.InternetTurnedOffConnectError):
                raise
            raise _Unreachable(f"no answer ({type(e).__name__})")
        self._note_limits(resp)
        if resp.status_code == 304 and cached is not None:
            return (200, cached.data) if identity else cached.data
        if resp.status_code in (403, 429) and self.rate_limited():
            raise _RateLimited()
        if identity and resp.status_code in _REDIRECTS:
            return resp.status_code, None
        if resp.status_code != 200:
            raise _Unreachable(f"GitHub answered {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise _Unreachable("GitHub's answer wasn't JSON")
        etag = resp.headers.get("etag")
        if etag:
            self.etags[url] = _Cached(etag, data)
        return (200, data) if identity else data

    async def _check_one(self, client: Any, record: Any, repo: SourceRepo) -> dict[str, Any]:
        base = {
            "slug": record.slug,
            "repo": repo.full_name,
            "origin": repo.origin,
            "installed": record.version,
            "checked_at": time.time(),
        }
        installed = parse_version(record.version)
        if installed is None:
            return {**base, "state": UNREACHABLE, "reason": "installed version isn't semver"}
        api = f"{API_BASE}/repos/{repo.owner}/{repo.repo}"
        try:
            status, info = await self._get_json(client, api, identity=True)
            await check_pin(record.slug, repo.full_name, status, info)
            releases = await self._get_json(client, f"{api}/releases?per_page=30")
            if not isinstance(releases, list):
                raise _Unreachable("unexpected answer")
            best = pick_latest(releases, installed)
            if best is None and not any(
                isinstance(r, dict) and parse_version(r.get("tag_name")) for r in releases
            ):
                # No semver releases at all: the repo's tags are the versions.
                tags = await self._get_json(client, f"{api}/tags?per_page=100")
                if not isinstance(tags, list):
                    raise _Unreachable("unexpected answer")
                best = pick_latest(tags, installed)
        except _RateLimited:
            prior = self.results.get(record.slug)
            if prior and prior.get("state") in (UPDATE_AVAILABLE, UP_TO_DATE):
                return prior
            return {**base, "state": RATE_LIMITED, "retry_at": self.blocked_until}
        except _Unreachable as e:
            return {**base, "state": UNREACHABLE, "reason": e.reason}
        except RepoChanged as e:
            return {**base, "state": REPO_CHANGED, "reason": str(e)}
        if best is None or compare(best.version, installed) <= 0:
            return {**base, "state": UP_TO_DATE}
        zip_name = asset_name(record.slug, best.version)
        has_asset = zip_name in best.assets and SUMS_NAME in best.assets
        return {
            **base,
            "state": UPDATE_AVAILABLE,
            "latest": str(best.version),
            "tag": best.tag,
            "notes": best.notes,
            "notes_format": "text",
            "published_at": best.published_at,
            "release_url": f"https://github.com/{repo.full_name}/releases/tag/{best.tag}",
            # The ordinary GitHub install path, at that tag.
            "github_url": f"https://github.com/{repo.full_name}@{best.tag}",
            # How the update will download: the release's zip checked
            # against its SHA256SUMS when both are attached, else the
            # tag's source archive (codeload).
            "install_from": "release_asset" if has_asset else "source",
            "asset": zip_name if has_asset else None,
        }

    # -- a run ----------------------------------------------------------

    async def run(
        self,
        records: Iterable[Any],
        *,
        force: bool = False,
        only_slug: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Check every plugin that is due. ``force`` (an admin's **check
        now**) ignores the daily interval; ``only_slug`` checks that one
        plugin, a zip install with a GitHub homepage included."""
        async with self._lock:
            todo: list[tuple[Any, SourceRepo]] = []
            now = time.time()
            for rec in records:
                repo = source_repo(rec)
                if repo is None:
                    self.results.pop(rec.slug, None)
                    continue
                if only_slug is not None:
                    if rec.slug != only_slug:
                        continue
                elif repo.origin != "github":
                    continue  # a homepage repo is checked on request only
                prior = self.results.get(rec.slug)
                if prior and not _same_install(prior, rec, repo):
                    prior = None  # updated or reinstalled since: the old answer is stale
                    self.results.pop(rec.slug, None)
                if (
                    not force and prior and prior.get("state") != INTERNET_OFF
                    and now - float(prior.get("checked_at") or 0) < CHECK_INTERVAL_SEC
                ):
                    continue
                todo.append((rec, repo))
            if not todo:
                return self.results
            if egress.internet_turned_off():
                for rec, repo in todo:
                    self.results[rec.slug] = {
                        "slug": rec.slug, "repo": repo.full_name, "origin": repo.origin,
                        "installed": rec.version, "state": INTERNET_OFF,
                        "checked_at": now,
                    }
                return self.results
            self.last_run_at = now
            kwargs: dict[str, Any] = {
                "timeout": REQUEST_TIMEOUT_SEC,
                "follow_redirects": True,
                "event_hooks": {"request": [_only_check_hosts]},
            }
            if _TRANSPORT is not None:
                kwargs["transport"] = _TRANSPORT
            async with egress.async_client(**kwargs) as client:
                for rec, repo in todo:
                    if self.rate_limited():
                        prior = self.results.get(rec.slug)
                        if not (prior and prior.get("state") in (UPDATE_AVAILABLE, UP_TO_DATE)):
                            self.results[rec.slug] = {
                                "slug": rec.slug, "repo": repo.full_name,
                                "origin": repo.origin, "installed": rec.version,
                                "state": RATE_LIMITED, "retry_at": self.blocked_until,
                                "checked_at": now,
                            }
                        continue
                    try:
                        self.results[rec.slug] = await self._check_one(client, rec, repo)
                    except egress.InternetTurnedOffConnectError:
                        self.results[rec.slug] = {
                            "slug": rec.slug, "repo": repo.full_name, "origin": repo.origin,
                            "installed": rec.version, "state": INTERNET_OFF, "checked_at": now,
                        }
                    except Exception as e:  # noqa: BLE001 - L37: one odd answer ends this plugin's check, not the run
                        log.warning("plugin update check: %s: %s", rec.slug, type(e).__name__)
                        self.results[rec.slug] = {
                            "slug": rec.slug, "repo": repo.full_name, "origin": repo.origin,
                            "installed": rec.version, "state": UNREACHABLE,
                            "reason": "GitHub's answer couldn't be read", "checked_at": now,
                        }
            return self.results

    def view(self, records: Iterable[Any]) -> dict[str, Any]:
        """What the Plugins page reads: one entry per plugin with a known
        repo (``not_checked`` until a check answers)."""
        out: dict[str, Any] = {}
        for rec in records:
            repo = source_repo(rec)
            if repo is None:
                continue
            prior = self.results.get(rec.slug)
            if prior and _same_install(prior, rec, repo):
                out[rec.slug] = dict(prior)
            else:
                out[rec.slug] = {
                    "slug": rec.slug, "repo": repo.full_name, "origin": repo.origin,
                    "installed": rec.version, "state": NOT_CHECKED,
                }
            out[rec.slug]["origin"] = repo.origin
        return {
            "plugins": out,
            "interval_sec": CHECK_INTERVAL_SEC,
            "last_run_at": self.last_run_at,
            "rate_limited_until": self.blocked_until if self.rate_limited() else None,
            "internet_off": egress.internet_turned_off(),
        }


CHECKER = Checker()


def _same_install(prior: dict[str, Any], rec: Any, repo: SourceRepo) -> bool:
    """An answer still stands for ``rec``: the same version from the same
    repo (L36: a reinstall of that version from another repo is not)."""
    return (prior.get("installed") == rec.version
            and str(prior.get("repo", "")).lower() == repo.full_name.lower())


# ─── installing an update from the release's own zip ─────────────────────

SUMS_NAME = "SHA256SUMS"
SUMS_MAX_BYTES = 64 * 1024
# The hosts a release-asset download really uses: the API (the release's
# asset list), github.com (the asset's download address) and the two
# storage hosts GitHub redirects that to. Every hop is held to this set.
ASSET_HOSTS = frozenset({
    "api.github.com", "github.com",
    "objects.githubusercontent.com", "release-assets.githubusercontent.com",
})
_SHA256_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S.*?)\s*\Z")


def asset_name(slug: str, version: Version) -> str:
    return f"{slug}-{version}.zip"


def parse_sums(text: str) -> dict[str, str]:
    """``SHA256SUMS`` (``sha256sum`` output): {file name: lowercase hex}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _SHA256_LINE.match(line.strip())
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


async def _only_asset_hosts(request: Any) -> None:
    host = (request.url.host or "").lower()
    if request.url.scheme != "https" or host not in ASSET_HOSTS:
        raise HostNotAllowed(f"release download refused a request to {host!r}")


# ─── pinned at install (M6) ─────────────────────────────────────────────

#: staged_id -> (owner/repo, (repo_id, owner_id)) read when a plugin was
#: staged from GitHub, written as its pin once the install is confirmed.
_STAGED_PINS: dict[str, tuple[str, tuple[int, int]]] = {}


async def _read_identity(owner: str, repo: str) -> tuple[int, int]:
    """``GET /repos/<owner>/<repo>`` on api.github.com, never following a
    redirect: the repository's ids. Raises :class:`RepoChanged` or
    ``_Unreachable``."""
    kwargs: dict[str, Any] = {
        "timeout": REQUEST_TIMEOUT_SEC, "follow_redirects": False,
        "event_hooks": {"request": [_only_check_hosts]},
    }
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT
    try:
        async with egress.async_client(**kwargs) as client:
            resp = await client.get(
                f"{API_BASE}/repos/{owner}/{repo}",
                headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    except (RepoChanged, _Unreachable):
        raise
    except Exception as e:  # noqa: BLE001 - no answer: no pin now
        raise _Unreachable(type(e).__name__) from e
    try:
        data = resp.json() if resp.status_code == 200 else None
    except ValueError:
        data = None
    return answer_identity(resp.status_code, data)


async def stage_pin(staged_id: str, slug: str, source_ref: str | None, *, upgrade: bool) -> None:
    """When a plugin is staged from GitHub (an install or an update), read
    its repository's ids now, to pin once the admin confirms
    (:func:`record_install_pin`). An update from the address the plugin is
    pinned to whose ids differ is refused here, whichever download it used
    (the release's zip or the tag's source archive). The address
    redirecting to another repository is refused too. No answer (offline,
    rate limited) pins nothing now: the first update check pins it."""
    from domovoi.plugins_runtime.installer import InstallError

    found = _repo_from_url(source_ref)
    if found is None or egress.internet_turned_off():
        return
    full_name = f"{found[0]}/{found[1]}"
    try:
        ident = await _read_identity(*found)
    except RepoChanged as e:
        raise InstallError(
            "repo_changed",
            f"{e}: install the plugin from the repository's new address if you trust it.")
    except _Unreachable as e:
        log.info("plugin %s: repository ids not read at install (%s); the first update "
                 "check pins them", slug, e.reason)
        return
    if upgrade:
        try:
            pin = await _get_pin(slug)
        except Exception as e:  # noqa: BLE001 - no pin table read: as before
            log.warning("plugin %s: repository pin not read (%s)", slug, type(e).__name__)
            pin = None
        if pin and str(pin.get("repo", "")).lower() == full_name.lower() and (
                pin.get("repo_id"), pin.get("owner_id")) != ident:
            raise InstallError(
                "repo_changed",
                "a different repository now has this address: the repository moved or "
                "changed owner, so the update was not installed. Reinstall the plugin "
                "from its new address if you trust it.")
    _STAGED_PINS[staged_id] = (full_name, ident)


async def record_install_pin(staged_id: str, slug: str) -> None:
    """The confirmed install's pin (see :func:`stage_pin`). Never raises."""
    staged = _STAGED_PINS.pop(staged_id, None)
    if staged is None:
        return
    try:
        await save_pin(slug, staged[0], staged[1], by="install")
    except Exception as e:  # noqa: BLE001 - the first update check pins it
        log.warning("plugin %s: repository pin not saved at install (%s)", slug, type(e).__name__)



class NoReleaseAsset(Exception):
    """The release has no ``<slug>-X.Y.Z.zip`` + ``SHA256SUMS`` pair: the
    caller falls back to the tag's source archive."""


async def download_release_asset(github_url: str, slug: str) -> tuple[bytes, str, dict[str, Any]]:
    """The update's zip from the release at ``github_url``'s tag, checked
    against the release's ``SHA256SUMS``. Returns (zip bytes, source_ref,
    what was checked). Raises :class:`NoReleaseAsset` when the release
    doesn't carry both files (or the tag has no release), and the
    installer's ``InstallError`` for anything that must stop the update:
    a checksum mismatch never falls back."""
    import hashlib

    from domovoi.plugins_runtime.installer import (
        _GITHUB_URL_RE, MAX_ZIP_BYTES, InstallError,
    )

    m = _GITHUB_URL_RE.match(github_url.strip())
    if not m or not m.group(3):
        raise InstallError("github_url_invalid", "expected https://github.com/<owner>/<repo>@<tag>")
    owner, repo, tag = m.group(1), m.group(2), m.group(3)
    version = parse_version(tag)
    if version is None:
        raise NoReleaseAsset("the tag isn't a vX.Y.Z version")
    if egress.internet_turned_off():
        raise InstallError("internet_off", egress.TURNED_OFF_REASON)
    zip_name = asset_name(slug, version)
    headers = {"User-Agent": USER_AGENT}
    kwargs: dict[str, Any] = {
        "timeout": 60, "follow_redirects": True,
        "event_hooks": {"request": [_only_asset_hosts]},
    }
    if _TRANSPORT is not None:
        kwargs["transport"] = _TRANSPORT

    async def fetch(client: Any, url: str, cap: int, what: str) -> bytes:
        buf = bytearray()
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code != 200:
                    raise InstallError(
                        "github_download_failed", f"{what}: GitHub answered {resp.status_code}")
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > cap:
                        raise InstallError("zip_too_large", f"{what} exceeded {cap} bytes — aborted")
        except HostNotAllowed as e:
            raise InstallError("github_download_failed", f"{what}: {e}")
        return bytes(buf)

    async with egress.async_client(**kwargs) as client:
        # M6: the repo answering at this address must be the one pinned for
        # this plugin (a redirect is not followed: a moved repo stops here).
        try:
            info = await client.get(
                f"{API_BASE}/repos/{owner}/{repo}", follow_redirects=False,
                headers={**headers, "Accept": "application/vnd.github+json"})
        except HostNotAllowed as e:
            raise InstallError("github_download_failed", str(e))
        try:
            info_data = info.json() if info.status_code == 200 else None
        except ValueError:
            info_data = None
        try:
            await check_pin(slug, f"{owner}/{repo}", info.status_code, info_data)
        except RepoChanged as e:
            raise InstallError(
                "repo_changed",
                f"{e}: the repository moved or changed owner, so the update was not "
                "installed. Reinstall the plugin from its new address if you trust it.")
        except _Unreachable as e:
            raise InstallError("github_repo_unavailable", e.reason)
        api = f"{API_BASE}/repos/{owner}/{repo}/releases/tags/{tag}"
        try:
            r = await client.get(
                api, headers={**headers, "Accept": "application/vnd.github+json"})
        except HostNotAllowed as e:
            raise InstallError("github_download_failed", str(e))
        if r.status_code == 404:
            raise NoReleaseAsset("the tag has no GitHub release")
        if r.status_code != 200:
            raise InstallError("github_repo_unavailable", f"GitHub answered {r.status_code}")
        assets = {
            a.get("name"): a.get("browser_download_url")
            for a in (r.json().get("assets") or []) if isinstance(a, dict)
        }
        prefix = f"https://github.com/{owner}/{repo}/releases/download/"
        zip_url, sums_url = assets.get(zip_name), assets.get(SUMS_NAME)
        if not (isinstance(zip_url, str) and isinstance(sums_url, str)):
            raise NoReleaseAsset(f"the release doesn't attach {zip_name} and {SUMS_NAME}")
        if not (zip_url.startswith(prefix) and sums_url.startswith(prefix)):
            raise InstallError(
                "github_download_failed", "the release's files point outside the repo")
        sums = parse_sums(
            (await fetch(client, sums_url, SUMS_MAX_BYTES, SUMS_NAME)).decode("utf-8", "replace"))
        want = sums.get(zip_name)
        if not want:
            raise InstallError(
                "checksum_missing", f"{SUMS_NAME} has no line for {zip_name}")
        data = await fetch(client, zip_url, MAX_ZIP_BYTES, zip_name)
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise InstallError(
            "checksum_mismatch",
            f"{zip_name} doesn't match the release's {SUMS_NAME} — not installed",
            {"expected": want, "actual": got},
        )
    source_ref = f"https://github.com/{owner}/{repo}@{tag}"
    return data, source_ref, {"from": "release_asset", "asset": zip_name, "sha256": got}


# ─── HTTP surface ───────────────────────────────────────────────────────

update_check_router = APIRouter(tags=["plugins-admin"])


async def _records() -> list[Any]:
    from domovoi.plugins_runtime import registry as reg

    return await reg.list_plugins()


@update_check_router.get("/v1/plugins/updates")
async def api_updates() -> dict[str, Any]:
    """Each GitHub-sourced plugin's update answer (open read, the plugin
    list's tier). Notes are plain text."""
    return CHECKER.view(await _records())


@update_check_router.post(
    "/v1/plugins/updates/check", dependencies=[Depends(require_admin)]
)
async def api_check_now(request: Request) -> dict[str, Any]:
    """**Check now** (admin): every GitHub-installed plugin, or one plugin
    (``{"slug": ...}``: also a zip install with a GitHub homepage). 409
    under ``INTERNET_ACCESS=never``, with no request made."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an empty body checks everything
        body = {}
    slug = body.get("slug") if isinstance(body, dict) else None
    if slug is not None and not isinstance(slug, str):
        raise HTTPException(status_code=422, detail="slug must be a string")
    if egress.internet_turned_off():
        raise egress.http_exception("checking for plugin updates")
    records = await _records()
    if slug is not None and not any(
        r.slug == slug and source_repo(r) is not None for r in records
    ):
        raise HTTPException(
            status_code=404, detail=f"plugin {slug!r} has no GitHub repo to check"
        )
    await CHECKER.run(records, force=True, only_slug=slug)
    return CHECKER.view(records)
