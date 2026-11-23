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
for updates"), never "up to date".

The answers live in this process's memory: a restart checks again on the
worker's first tick.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
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

    async def _get_json(self, client: Any, url: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        cached = self.etags.get(url)
        if cached is not None:
            headers["If-None-Match"] = cached.etag
        try:
            resp = await client.get(url, headers=headers)
        except HostNotAllowed:
            raise _Unreachable("redirected off api.github.com")
        except Exception as e:  # noqa: BLE001 - network, TLS, egress refusal
            if isinstance(e, egress.InternetTurnedOffConnectError):
                raise
            raise _Unreachable(f"no answer ({type(e).__name__})")
        self._note_limits(resp)
        if resp.status_code == 304 and cached is not None:
            return cached.data
        if resp.status_code in (403, 429) and self.rate_limited():
            raise _RateLimited()
        if resp.status_code != 200:
            raise _Unreachable(f"GitHub answered {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise _Unreachable("GitHub's answer wasn't JSON")
        etag = resp.headers.get("etag")
        if etag:
            self.etags[url] = _Cached(etag, data)
        return data

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
                if prior and prior.get("installed") != rec.version:
                    prior = None  # updated since: the old answer is stale
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
            if prior and prior.get("installed") == rec.version:
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
