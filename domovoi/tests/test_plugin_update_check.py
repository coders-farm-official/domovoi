"""Plugin update checks (domovoi/plugins_runtime/update_check.py).

DB-free: GitHub is an ``httpx.MockTransport``, the registry a list of
plain records. No real network: the transport answers everything, and the
egress client refuses any non-local host under "never" anyway.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domovoi import egress
from domovoi.auth import require_admin
from domovoi.plugins_runtime import update_check as uc
from domovoi.plugins_runtime.update_check import (
    Checker, compare, parse_version, pick_latest, source_repo,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Rec:
    slug: str
    version: str
    install_source: str = "github"
    source_ref: str | None = "https://github.com/acme/domovoi-plugin-weather@v1.0.0"
    manifest: dict[str, Any] = field(default_factory=dict)
    bundled: bool = False
    status: str = "ok"


def _release(tag: str, *, body: str = "", prerelease: bool = False, draft: bool = False) -> dict:
    return {"tag_name": tag, "body": body, "prerelease": prerelease, "draft": draft,
            "published_at": "2026-11-20T10:00:00Z"}


class FakeGitHub:
    """Answers api.github.com; records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, httpx.Response] = {}
        self.etag_hits = 0

    def set(self, path: str, status: int = 200, json: Any = None,
            headers: dict[str, str] | None = None) -> None:
        self.routes[path] = (status, json, headers or {})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = request.url.path
        if key not in self.routes and key.count("/") == 3 and key.startswith("/repos/"):
            # The repo itself (M6's identity read): the same ids every time.
            return httpx.Response(200, json=REPO_INFO)
        if key not in self.routes:
            return httpx.Response(404, json={"message": "Not Found"})
        status, body, headers = self.routes[key]
        etag = headers.get("ETag")
        if etag and request.headers.get("if-none-match") == etag:
            self.etag_hits += 1
            return httpx.Response(304, headers=headers)
        return httpx.Response(status, json=body, headers=headers)


REPO_INFO = {"id": 4242, "full_name": "acme/domovoi-plugin-weather", "owner": {"id": 77}}


@pytest.fixture(autouse=True)
def _pins(monkeypatch, tmp_path):
    """Each test's repository pins (M6) in its own file."""
    monkeypatch.setenv("DOMOVOI_PLUGIN_REPO_PINS", str(tmp_path / "pins.json"))


def _releases(gh: "FakeGitHub") -> list[httpx.Request]:
    return [r for r in gh.requests if r.url.path.count("/") > 3]


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def checker():
    return Checker()


REL = "/repos/acme/domovoi-plugin-weather/releases"
TAGS = "/repos/acme/domovoi-plugin-weather/tags"


# ─── semver ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b,want", [
    ("1.0.0", "1.0.1", -1),
    ("1.10.0", "1.9.9", 1),
    ("2.0.0", "2.0.0", 0),
    ("v1.2.3", "1.2.3", 0),
    ("1.0.0-alpha", "1.0.0", -1),
    ("1.0.0-alpha", "1.0.0-alpha.1", -1),
    ("1.0.0-alpha.1", "1.0.0-alpha.beta", -1),
    ("1.0.0-beta.2", "1.0.0-beta.11", -1),
    ("1.0.0-rc.1", "1.0.0", -1),
])
def test_semver_compare(a, b, want):
    assert compare(parse_version(a), parse_version(b)) == want
    assert compare(parse_version(b), parse_version(a)) == -want


@pytest.mark.parametrize("bad", ["1.0", "v1", "01.0.0", "1.0.0+build", "latest", "", None, "1.0.0-"])
def test_semver_rejects(bad):
    assert parse_version(bad) is None


# ─── pre-releases ───────────────────────────────────────────────────────

def test_prereleases_ignored_for_a_release_install():
    rels = [_release("v1.1.0"), _release("v1.2.0-rc.1"), _release("v1.3.0", prerelease=True),
            _release("v9.0.0", draft=True), _release("nightly")]
    best = pick_latest(rels, parse_version("1.0.0"))
    assert best.tag == "v1.1.0"


def test_prereleases_count_when_installed_is_one():
    rels = [_release("v1.1.0"), _release("v1.2.0-rc.1")]
    assert pick_latest(rels, parse_version("1.1.0-beta.1")).tag == "v1.2.0-rc.1"


# ─── where the repo comes from ──────────────────────────────────────────

def test_source_repo_from_github_source_ref():
    r = source_repo(Rec("w", "1.0.0", source_ref="https://github.com/acme/x@v1.0.0@v1.0.0"))
    assert (r.owner, r.repo, r.origin) == ("acme", "x", "github")
    r = source_repo(Rec("w", "1.0.0", source_ref="https://github.com/acme/x.git@main"))
    assert r.full_name == "acme/x"


def test_source_repo_zip_needs_a_github_homepage():
    assert source_repo(Rec("w", "1.0.0", install_source="zip", source_ref="w-1.0.0.zip")) is None
    assert source_repo(Rec("w", "1.0.0", install_source="zip", source_ref=None,
                           manifest={"plugin": {"homepage": "https://example.com/w"}})) is None
    r = source_repo(Rec("w", "1.0.0", install_source="zip", source_ref=None,
                        manifest={"plugin": {"homepage": "https://github.com/acme/w"}}))
    assert (r.full_name, r.origin) == ("acme/w", "homepage")


def test_bundled_and_dev_are_never_checked():
    assert source_repo(Rec("radio", "1.0.0", bundled=True)) is None
    assert source_repo(Rec("d", "1.0.0", install_source="dev")) is None
    assert source_repo(Rec("u", "1.0.0", status="uninstalled")) is None


# ─── a check ────────────────────────────────────────────────────────────

async def test_update_available_with_text_notes(gh, checker):
    gh.set(REL, json=[_release("v1.2.0", body="<script>alert(1)</script>\r\n**fixed**\x00 ‮")])
    out = await checker.run([Rec("weather", "1.0.0")])
    u = out["weather"]
    assert u["state"] == uc.UPDATE_AVAILABLE
    assert u["latest"] == "1.2.0" and u["tag"] == "v1.2.0"
    # plain text: kept as characters for a text node, control/bidi removed
    assert u["notes_format"] == "text"
    assert u["notes"] == "<script>alert(1)</script>\n**fixed**"
    assert u["github_url"] == "https://github.com/acme/domovoi-plugin-weather@v1.2.0"


async def test_up_to_date(gh, checker):
    gh.set(REL, json=[_release("v1.0.0"), _release("v0.9.0")])
    out = await checker.run([Rec("weather", "1.0.0")])
    assert out["weather"]["state"] == uc.UP_TO_DATE


async def test_tags_when_no_releases(gh, checker):
    gh.set(REL, json=[])
    gh.set(TAGS, json=[{"name": "v1.0.0"}, {"name": "v1.4.0"}, {"name": "junk"}])
    out = await checker.run([Rec("weather", "1.0.0")])
    assert out["weather"]["state"] == uc.UPDATE_AVAILABLE
    assert out["weather"]["latest"] == "1.4.0"


@pytest.mark.parametrize("status", [404, 403, 500])
async def test_private_or_missing_repo_is_cant_check(gh, checker, status):
    gh.set(REL, status=status, json={"message": "Not Found"})
    out = await checker.run([Rec("weather", "1.0.0")])
    assert out["weather"]["state"] == uc.UNREACHABLE
    assert out["weather"]["state"] != uc.UP_TO_DATE


async def test_network_failure_is_cant_check(monkeypatch, checker):
    def boom(request):
        raise httpx.ConnectError("down", request=request)
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(boom))
    out = await checker.run([Rec("weather", "1.0.0")])
    assert out["weather"]["state"] == uc.UNREACHABLE


async def test_internet_off_makes_no_request(gh, checker):
    gh.set(REL, json=[_release("v2.0.0")])
    with egress.override_policy("never"):
        out = await checker.run([Rec("weather", "1.0.0")], force=True)
    assert gh.requests == []
    assert out["weather"]["state"] == uc.INTERNET_OFF


async def test_only_allowed_hosts_are_called(gh, checker):
    gh.set(REL, json=[_release("v2.0.0")])
    await checker.run([Rec("weather", "1.0.0"),
                       Rec("other", "0.1.0", source_ref="https://github.com/acme/other@main")])
    assert gh.requests
    assert {r.url.host for r in gh.requests} <= uc.CHECK_HOSTS <= uc.ALLOWED_HOSTS
    assert uc.ALLOWED_HOSTS == {"api.github.com", "codeload.github.com"}
    for r in gh.requests:
        assert "authorization" not in {k.lower() for k in r.headers}


async def test_a_redirect_off_github_is_refused(monkeypatch, checker):
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "api.github.com" and request.url.path.count("/") == 3:
            return httpx.Response(200, json=REPO_INFO)
        if request.url.host == "api.github.com":
            return httpx.Response(301, headers={"Location": "https://evil.example.net/x"})
        return httpx.Response(200, json=[])
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(handler))
    out = await checker.run([Rec("weather", "1.0.0")])
    assert seen == ["api.github.com", "api.github.com"]
    assert out["weather"]["state"] == uc.UNREACHABLE


async def test_etag_is_reused(gh, checker):
    gh.set(REL, json=[_release("v1.3.0")], headers={"ETag": '"abc"'})
    rec = Rec("weather", "1.0.0")
    first = await checker.run([rec], force=True)
    second = await checker.run([rec], force=True)
    assert _releases(gh)[1].headers["if-none-match"] == '"abc"'
    assert gh.etag_hits == 1
    assert second["weather"]["latest"] == first["weather"]["latest"] == "1.3.0"


async def test_daily_interval_without_force(gh, checker):
    gh.set(REL, json=[_release("v1.0.0")])
    rec = Rec("weather", "1.0.0")
    await checker.run([rec])
    await checker.run([rec])
    assert len(_releases(gh)) == 1
    checker.results["weather"]["checked_at"] = time.time() - uc.CHECK_INTERVAL_SEC - 1
    await checker.run([rec])
    assert len(_releases(gh)) == 2


async def test_rate_limit_headers_stop_checks(gh, checker):
    reset = int(time.time()) + 600
    gh.set(REL, status=403, json={"message": "API rate limit exceeded"},
           headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})
    out = await checker.run([Rec("weather", "1.0.0"),
                             Rec("other", "0.1.0", source_ref="https://github.com/acme/other@main")])
    assert out["weather"]["state"] == uc.RATE_LIMITED
    assert out["other"]["state"] == uc.RATE_LIMITED
    assert len(_releases(gh)) == 1  # the second plugin waited for the reset
    assert checker.blocked_until == reset


async def test_homepage_repo_only_on_request(gh, checker):
    gh.set("/repos/acme/zipped/releases", json=[_release("v3.0.0")])
    rec = Rec("zipped", "1.0.0", install_source="zip", source_ref="zipped-1.0.0.zip",
              manifest={"plugin": {"homepage": "https://github.com/acme/zipped"}})
    await checker.run([rec])
    assert gh.requests == []
    assert checker.view([rec])["plugins"]["zipped"]["state"] == uc.NOT_CHECKED
    out = await checker.run([rec], force=True, only_slug="zipped")
    assert out["zipped"]["state"] == uc.UPDATE_AVAILABLE


# ─── the routes ─────────────────────────────────────────────────────────

def _client(monkeypatch, records, *, admin=True) -> TestClient:
    app = FastAPI()
    app.include_router(uc.update_check_router)
    if admin:
        app.dependency_overrides[require_admin] = lambda: None

    async def fake_records():
        return records
    monkeypatch.setattr(uc, "_records", fake_records)
    monkeypatch.setattr(uc, "CHECKER", Checker())
    return TestClient(app)


def test_route_tiers():
    """GET is the plugin list's open read; check-now wears require_admin
    (the route-auth matrix walks the core app for it too)."""
    from domovoi.main import app as core_app
    from domovoi.tests.route_walk import iter_route_contexts
    from domovoi.tests.test_route_auth_matrix import _dependency_calls

    found = {}
    for rc in iter_route_contexts(core_app.routes):
        path = getattr(rc, "path", None)
        if path in ("/v1/plugins/updates", "/v1/plugins/updates/check"):
            for m in getattr(rc, "methods", None) or ():
                deps = set(_dependency_calls(getattr(rc, "dependant", None)))
                found[(m, path)] = require_admin in deps
    assert found == {("GET", "/v1/plugins/updates"): False,
                     ("POST", "/v1/plugins/updates/check"): True}


def test_web_check_now_is_an_allowlisted_proxy():
    from domovoi.tests.test_route_auth_matrix import ALLOWLIST

    assert "require_admin" in ALLOWLIST[("web", "POST", "/api/plugins/updates/check")]


def test_check_now_route(monkeypatch, gh):
    gh.set(REL, json=[_release("v1.1.0", body="notes")])
    client = _client(monkeypatch, [Rec("weather", "1.0.0"), Rec("radio", "1.0.0", bundled=True)])
    assert client.get("/v1/plugins/updates").json()["plugins"]["weather"]["state"] == uc.NOT_CHECKED
    body = client.post("/v1/plugins/updates/check", json={}).json()
    assert body["plugins"]["weather"]["state"] == uc.UPDATE_AVAILABLE
    assert "radio" not in body["plugins"]
    assert client.post("/v1/plugins/updates/check", json={"slug": "radio"}).status_code == 404


def test_check_now_with_internet_off(monkeypatch, gh):
    client = _client(monkeypatch, [Rec("weather", "1.0.0")])
    with egress.override_policy("never"):
        r = client.post("/v1/plugins/updates/check", json={})
    assert r.status_code == 409
    assert gh.requests == []


# ─── the update goes through the normal install path ────────────────────

async def test_update_url_stages_through_the_github_upgrade(monkeypatch, gh, checker):
    """The page's "Update to X.Y.Z" posts the result's github_url to the
    existing upgrade route: the same download + stage (preview, trust
    screen) as a hand-typed GitHub upgrade, and nothing is confirmed."""
    from domovoi.plugins_runtime import installer

    gh.set(REL, json=[_release("v1.2.0")])
    out = await checker.run([Rec("weather", "1.0.0")])
    url = out["weather"]["github_url"]
    assert installer._GITHUB_URL_RE.match(url).group(3) == "v1.2.0"

    calls: dict[str, Any] = {}

    async def fake_get_plugin(slug):
        return Rec(slug, "1.0.0")

    async def fake_download(github_url):
        calls["download"] = github_url
        return b"zip", f"{github_url}@v1.2.0"

    async def no_freeze(_op):
        return None

    def must_not_confirm(*a, **k):
        raise AssertionError("an update must wait for the trust-screen confirm")

    monkeypatch.setattr(installer.reg, "get_plugin", fake_get_plugin)
    monkeypatch.setattr(installer, "download_github_zip", fake_download)
    monkeypatch.setattr(installer, "_require_unfrozen", no_freeze)
    monkeypatch.setattr(installer, "confirm_upgrade", must_not_confirm)
    monkeypatch.setattr(installer, "confirm_install", must_not_confirm)

    class Req:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"github_url": url}

    staged_obj = installer.StagedInstall.__new__(installer.StagedInstall)
    staged_obj.staged_id = "s1"
    staged_obj.preview = {"trust_statement": installer.TRUST_STATEMENT}

    async def fake_stage(data, **kw):
        calls["stage"] = kw
        return staged_obj
    monkeypatch.setattr(installer, "stage_zip", fake_stage)

    res = await installer.api_upgrade(Req(), "weather")
    assert calls["download"] == url
    assert calls["stage"]["upgrade_of"] == "weather"
    assert calls["stage"]["install_source"] == "github"
    assert res == {"staged_id": "s1", "preview": staged_obj.preview}


def test_page_routes_the_update_through_the_trust_screen():
    src = (REPO_ROOT / "web" / "static" / "plugins.jsx").read_text(encoding="utf-8")
    # The update button hands the tag URL to the same staging flow the
    # GitHub upgrade box uses (stageGithub -> TrustConfirmModal).
    assert "<PluginUpdatePanel p={p} u={update} onUpdate={onUpdateRelease}" in src
    assert "onUpdateRelease={(slug, url) => flow.stageGithub(url, slug, { release_asset: true })}" in src
    assert "setStaged({" in src and "<TrustConfirmModal" in src
    # Release notes are a text node, never HTML.
    assert "dangerouslySetInnerHTML" not in src
    assert re.search(r"\{String\(u\.notes\)\}", src)
    # can't-check is never worded as up to date
    assert "can't check for updates (private or unreachable)" in src


# ─── the release's own zip, checked against SHA256SUMS ──────────────────

ZIP = b"PK\x03\x04 fake plugin zip"


def _sums(data: bytes, name: str = "weather-1.2.0.zip") -> bytes:
    import hashlib
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n".encode()


class FakeReleaseHost:
    """api.github.com + github.com (302) + objects.githubusercontent.com."""

    def __init__(self, *, assets=True, zip_bytes=ZIP, sums=None,
                 redirect_to="objects.githubusercontent.com"):
        self.hosts: list[str] = []
        self.assets = assets
        self.zip_bytes = zip_bytes
        self.sums = _sums(ZIP) if sums is None else sums
        self.redirect_to = redirect_to

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        path = request.url.path
        dl = "https://github.com/acme/domovoi-plugin-weather/releases/download/v1.2.0/"
        if request.url.host == "api.github.com" and path == "/repos/acme/domovoi-plugin-weather":
            return httpx.Response(200, json=REPO_INFO)
        if request.url.host == "api.github.com" and path.endswith("/releases/tags/v1.2.0"):
            assets = [{"name": "weather-1.2.0.zip", "browser_download_url": dl + "weather-1.2.0.zip"},
                      {"name": "SHA256SUMS", "browser_download_url": dl + "SHA256SUMS"}]
            return httpx.Response(
                200, json={"tag_name": "v1.2.0", "assets": assets if self.assets else []})
        if request.url.host == "github.com":
            name = path.rsplit("/", 1)[-1]
            return httpx.Response(
                302, headers={"Location": f"https://{self.redirect_to}/store/{name}"})
        if request.url.host == "objects.githubusercontent.com":
            name = path.rsplit("/", 1)[-1]
            return httpx.Response(
                200, content=self.sums if name == "SHA256SUMS" else self.zip_bytes)
        return httpx.Response(404)


URL = "https://github.com/acme/domovoi-plugin-weather@v1.2.0"


async def test_check_says_how_the_update_installs(gh, checker):
    gh.set(REL, json=[{**_release("v1.2.0"),
                       "assets": [{"name": "weather-1.2.0.zip"}, {"name": "SHA256SUMS"}]}])
    u = (await checker.run([Rec("weather", "1.0.0")]))["weather"]
    assert (u["install_from"], u["asset"]) == ("release_asset", "weather-1.2.0.zip")
    gh.set(REL, json=[{**_release("v1.2.0"), "assets": [{"name": "weather-1.2.0.zip"}]}])
    u = (await checker.run([Rec("weather", "1.0.0")], force=True))["weather"]
    assert (u["install_from"], u["asset"]) == ("source", None)


async def test_release_asset_verified_through_the_allowed_redirects(monkeypatch):
    fake = FakeReleaseHost()
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(fake.handler))
    data, ref, info = await uc.download_release_asset(URL, "weather")
    assert data == ZIP and ref == URL
    assert info["from"] == "release_asset" and info["asset"] == "weather-1.2.0.zip"
    assert set(fake.hosts) == {"api.github.com", "github.com", "objects.githubusercontent.com"}
    assert set(fake.hosts) <= uc.ASSET_HOSTS


async def test_checksum_mismatch_stops_the_update(monkeypatch):
    from domovoi.plugins_runtime.installer import InstallError

    fake = FakeReleaseHost(zip_bytes=b"tampered")
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(fake.handler))
    with pytest.raises(InstallError) as e:
        await uc.download_release_asset(URL, "weather")
    assert e.value.code == "checksum_mismatch"


async def test_sums_without_the_zip_line_stops(monkeypatch):
    from domovoi.plugins_runtime.installer import InstallError

    fake = FakeReleaseHost(sums=_sums(ZIP, "other-1.0.0.zip"))
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(fake.handler))
    with pytest.raises(InstallError) as e:
        await uc.download_release_asset(URL, "weather")
    assert e.value.code == "checksum_missing"


async def test_redirect_off_the_asset_hosts_is_refused(monkeypatch):
    from domovoi.plugins_runtime.installer import InstallError

    fake = FakeReleaseHost(redirect_to="evil.example.net")
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(fake.handler))
    with pytest.raises(InstallError):
        await uc.download_release_asset(URL, "weather")
    assert "evil.example.net" not in fake.hosts


async def test_no_assets_means_fallback(monkeypatch):
    fake = FakeReleaseHost(assets=False)
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(fake.handler))
    with pytest.raises(uc.NoReleaseAsset):
        await uc.download_release_asset(URL, "weather")


async def test_release_asset_internet_off_no_request(monkeypatch):
    from domovoi.plugins_runtime.installer import InstallError

    fake = FakeReleaseHost()
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(fake.handler))
    with egress.override_policy("never"), pytest.raises(InstallError) as e:
        await uc.download_release_asset(URL, "weather")
    assert e.value.code == "internet_off" and fake.hosts == []


@pytest.mark.parametrize("assets", [True, False])
async def test_upgrade_route_prefers_the_asset_and_falls_back(monkeypatch, assets):
    from domovoi.plugins_runtime import installer

    fake = FakeReleaseHost(assets=assets)
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(fake.handler))
    calls: dict[str, Any] = {}

    async def fake_get_plugin(slug):
        return Rec(slug, "1.0.0")

    async def fake_codeload(github_url):
        calls["codeload"] = github_url
        return b"source-zip", f"{github_url}@v1.2.0"

    staged = installer.StagedInstall.__new__(installer.StagedInstall)
    staged.staged_id, staged.preview = "s1", {"trust_statement": installer.TRUST_STATEMENT}

    async def fake_stage(data, **kw):
        calls["stage"] = (data, kw)
        return staged

    async def no_freeze(_op):
        return None

    monkeypatch.setattr(installer.reg, "get_plugin", fake_get_plugin)
    monkeypatch.setattr(installer, "download_github_zip", fake_codeload)
    monkeypatch.setattr(installer, "stage_zip", fake_stage)
    monkeypatch.setattr(installer, "_require_unfrozen", no_freeze)

    class Req:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {"github_url": URL, "release_asset": True}

    res = await installer.api_upgrade(Req(), "weather")
    data, kw = calls["stage"]
    assert kw["upgrade_of"] == "weather" and kw["install_source"] == "github"
    assert res["staged_id"] == "s1"  # staged only: the trust screen confirms
    if assets:
        assert data == ZIP and "codeload" not in calls
        assert res["download"]["from"] == "release_asset"
        assert kw["source_ref"] == URL
    else:
        assert data == b"source-zip" and calls["codeload"] == URL
        assert res["download"]["from"] == "source"


# ─── M6: the repository's identity is pinned ────────────────────────────


async def test_a_moved_repo_is_flagged_not_offered(gh, checker):
    """M6 (review 2026-11-23): the first check pins the repo's ids; the
    same name answering with a redirect (renamed or transferred) or with
    other ids (deleted and re-registered) offers no update."""
    gh.set(REL, json=[_release("v1.2.0")])
    rec = Rec("weather", "1.0.0")
    assert (await checker.run([rec]))["weather"]["state"] == uc.UPDATE_AVAILABLE
    assert uc.load_pins()["weather"]["repo_id"] == 4242

    gh.set("/repos/acme/domovoi-plugin-weather", json={"id": 999, "owner": {"id": 1}})
    gh.set(REL, json=[_release("v999.0.0")])
    u = (await checker.run([rec], force=True))["weather"]
    assert u["state"] == uc.REPO_CHANGED and "github_url" not in u

    gh.set("/repos/acme/domovoi-plugin-weather", status=301, json={"url": "x"},
           headers={"Location": "https://api.github.com/repositories/4242"})
    assert (await checker.run([rec], force=True))["weather"]["state"] == uc.REPO_CHANGED
    # The identity read is never followed through the redirect.
    assert not any(r.url.path.startswith("/repositories/") for r in gh.requests)


async def test_a_reinstall_from_another_repo_pins_afresh(gh, checker):
    gh.set(REL, json=[_release("v1.0.0")])
    await checker.run([Rec("weather", "1.0.0")])
    other = Rec("weather", "1.0.0", source_ref="https://github.com/acme/other-weather@v1.0.0")
    gh.set("/repos/acme/other-weather", json={"id": 5, "owner": {"id": 6}})
    gh.set("/repos/acme/other-weather/releases", json=[_release("v1.0.0")])
    assert (await checker.run([other], force=True))["weather"]["state"] == uc.UP_TO_DATE
    assert uc.load_pins()["weather"]["repo"] == "acme/other-weather"


async def test_the_update_download_is_held_to_the_pin(monkeypatch):
    from domovoi.plugins_runtime.installer import InstallError

    uc.check_pin("weather", "acme/domovoi-plugin-weather", 200, {"id": 1, "owner": {"id": 2}})
    fake = FakeReleaseHost()
    monkeypatch.setattr(uc, "_TRANSPORT", httpx.MockTransport(fake.handler))
    with pytest.raises(InstallError) as e:
        await uc.download_release_asset(URL, "weather")
    assert e.value.code == "repo_changed"
    assert "github.com" not in fake.hosts and "objects.githubusercontent.com" not in fake.hosts


async def test_an_answer_for_another_repo_is_dropped(gh, checker):
    """L36: an answer is for one version FROM ONE REPO."""
    gh.set(REL, json=[_release("v1.2.0")])
    await checker.run([Rec("weather", "1.0.0")])
    other = Rec("weather", "1.0.0", source_ref="https://github.com/acme/other-weather@v1.0.0")
    view = checker.view([other])["plugins"]["weather"]
    assert view["state"] == uc.NOT_CHECKED and view["repo"] == "acme/other-weather"


async def test_one_odd_answer_does_not_end_the_run(gh, checker, monkeypatch):
    """L37: an unexpected value fails that plugin's check, not the rest."""
    gh.set(REL, json=[_release("v1.2.0")])
    gh.set("/repos/acme/other/releases", json=[_release("v0.2.0")])
    real = uc.pick_latest

    def picky(releases, installed):
        if installed == parse_version("9.9.9"):
            raise TypeError("odd")
        return real(releases, installed)

    monkeypatch.setattr(uc, "pick_latest", picky)
    out = await checker.run([
        Rec("weather", "9.9.9"),
        Rec("other", "0.1.0", source_ref="https://github.com/acme/other@main"),
    ])
    assert out["weather"]["state"] == uc.UNREACHABLE
    assert out["other"]["state"] == uc.UPDATE_AVAILABLE
