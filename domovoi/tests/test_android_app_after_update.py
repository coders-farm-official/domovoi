"""The Android app on the server after an update (2026-10-23): the
readiness the dashboard gates its QR code on, the cache surviving an update
that brings a new release.json (the old release serves until the new one is
here), the automatic refresh's decision under each internet answer, and the
admin tier on what starts a download.

Kamron's Moto scanned the dashboard's room code and was told "the app isn't
on this server yet": the release in the cache was keyed by the OLD
versionCode, so the new release.json made it count as missing. No DB, no
network: the signed fixtures and the fake GitHub of test_android_app.py.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from domovoi import android_app as aa
from domovoi import egress, main
from domovoi.tests.test_android_app import (  # noqa: F401 — fixtures
    GOOD, OTHER_CERT, VERSION, cache, fixture, github, load, manifest_doc,
)

OLD_VERSION = "9.9.8"
OLD_CODE = 98


def put_old(cache: Path, files: dict[str, bytes] | None = None, version: str = OLD_VERSION,
            code: int = OLD_CODE) -> Path:
    """The cache as an earlier release left it: ``<code>/domovoi-<version>-<abi>.apk``."""
    folder = cache.parent / str(code)
    folder.mkdir(parents=True, exist_ok=True)
    for abi, body in (GOOD if files is None else files).items():
        (folder / aa.file_name(version, abi)).write_bytes(body)
    return folder


# ─── readiness: what the dashboard gates the QR code on ───────────────────

def test_nothing_cached_is_not_ready(tmp_path, cache):
    m = load(tmp_path, manifest_doc(GOOD))
    pub = aa.public_status(m)
    assert pub["ready"] is False and "newer" not in pub
    st = aa.status(m)
    assert st["ready"] is False and st["current_ready"] is False
    assert st["missing"] == ["domovoi-9.9.9-arm64-v8a.apk", "domovoi-9.9.9-armeabi-v7a.apk"]


def test_one_abi_of_two_is_not_ready(tmp_path, cache, github):
    m = load(tmp_path, manifest_doc(GOOD))
    cache.mkdir(parents=True)
    (cache / "domovoi-9.9.9-arm64-v8a.apk").write_bytes(GOOD["arm64-v8a"])
    assert aa.public_status(m)["ready"] is False
    assert aa.status(m)["missing"] == ["domovoi-9.9.9-armeabi-v7a.apk"]


def test_the_release_cached_is_ready(tmp_path, cache, github):
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        assert aa.fetch(m)[0]
    st = aa.status(m)
    assert st["ready"] is True and st["current_ready"] is True and st["missing"] == []
    assert "newer" not in st and st["release"]["version_name"] == VERSION


# ─── surviving an update ──────────────────────────────────────────────────

def test_after_an_update_the_old_release_serves_until_the_new_one_is_here(tmp_path, cache, github):
    """The update brought release 99; the admin had put 98 here. Phones
    still get 98, described as 98 (a phone's update check must not take it
    for 99), with 99 named as the newer one."""
    put_old(cache)
    m = load(tmp_path, manifest_doc(GOOD))
    pub = aa.public_status(m)
    assert pub["ready"] is True
    assert pub["release"]["version_name"] == OLD_VERSION and pub["release"]["version_code"] == OLD_CODE
    assert pub["newer"] == {"version_name": VERSION, "version_code": 99}
    assert [a["path"] for a in pub["apks"]] == [
        "/v1/android-app/files/domovoi-9.9.8-arm64-v8a.apk", "/v1/android-app/files/domovoi-9.9.8-armeabi-v7a.apk"]
    assert all("_path" not in a for a in pub["apks"]), "no server path leaks"
    assert aa.cached_file(m, "domovoi-9.9.8-arm64-v8a.apk").read_bytes() == GOOD["arm64-v8a"]
    st = aa.status(m)
    assert st["current_ready"] is False and st["current_version_name"] == VERSION
    assert st["bytes_total"] == sum(len(b) for b in GOOD.values()), "the download button sizes the new release"


def test_a_failed_download_keeps_the_old_release(tmp_path, cache, github):
    old = put_old(cache)
    github.files.clear()
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        ok, _ = aa.fetch(m)
    assert not ok
    assert old.is_dir() and aa.public_status(m)["release"]["version_name"] == OLD_VERSION


def test_the_new_release_complete_drops_the_old_one(tmp_path, cache, github):
    old = put_old(cache)
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        assert aa.fetch(m)[0]
    assert not old.exists()
    pub = aa.public_status(m)
    assert pub["release"]["version_name"] == VERSION and "newer" not in pub
    assert aa.cached_file(m, "domovoi-9.9.8-arm64-v8a.apk") is None


def test_the_newest_old_release_is_the_one_served(tmp_path, cache):
    put_old(cache, version="9.9.6", code=96)
    put_old(cache)
    m = load(tmp_path, manifest_doc(GOOD))
    assert aa.public_status(m)["release"]["version_code"] == OLD_CODE


@pytest.mark.parametrize("case", ["other_key", "one_abi", "wrong_abi", "two_versions", "newer_folder", "below_floor"])
def test_an_old_release_the_key_or_the_manifest_doesn_t_vouch_for_is_not_served(tmp_path, cache, case):
    doc = manifest_doc(GOOD)
    if case == "other_key":
        put_old(cache, {"arm64-v8a": fixture("other-arm64-v8a.apk"), "armeabi-v7a": GOOD["armeabi-v7a"]})
    elif case == "one_abi":
        put_old(cache, {"arm64-v8a": GOOD["arm64-v8a"]})
    elif case == "wrong_abi":
        put_old(cache, {"arm64-v8a": GOOD["arm64-v8a"], "armeabi-v7a": GOOD["arm64-v8a"]})
    elif case == "two_versions":
        folder = put_old(cache, {"arm64-v8a": GOOD["arm64-v8a"]})
        (folder / aa.file_name("9.9.7", "armeabi-v7a")).write_bytes(GOOD["armeabi-v7a"])
    elif case == "newer_folder":
        put_old(cache, code=100)
    elif case == "below_floor":
        put_old(cache)
        doc["release"]["min_served_version_code"] = 99
    m = load(tmp_path, doc)
    assert aa.public_status(m)["ready"] is False
    assert aa.cached_file(m, "domovoi-9.9.8-arm64-v8a.apk") is None


def test_min_served_version_code_is_checked(tmp_path):
    for bad in (0, 100, True, "98"):
        doc = manifest_doc(GOOD)
        doc["release"]["min_served_version_code"] = bad
        with pytest.raises(aa.ManifestError):
            load(tmp_path, doc)
    doc = manifest_doc(GOOD)
    doc["release"]["min_served_version_code"] = 98
    assert load(tmp_path, doc)["release"]["min_served_version_code"] == 98


def test_the_cache_lives_outside_the_checkout_and_no_update_or_setup_step_clears_it():
    """An update replaces the checkout and provision.sh re-runs on a Setup
    repair: the cache is in the service user's ~/.domovoi, which neither
    touches, and the Windows host never imports over an existing disk."""
    from domovoi.satellite_media import cache as media_cache

    root = media_cache.CACHE_ROOT.resolve()
    assert aa.REPO.resolve() not in root.parents
    assert root.parts[-3:] == (".domovoi", "satellite_media", "cache")
    for script in ("scripts/linux/apply-update.sh", "scripts/linux/provision.sh"):
        text = (aa.REPO / script).read_text(encoding="utf-8")
        for line in text.splitlines():
            if re.search(r"\brm\b|rmtree|Remove-Item", line):
                assert "satellite_media" not in line and "android_app" not in line, (script, line)
                assert not re.search(r"\.domovoi/?[\"' ]*$", line), (script, line)
    host = (aa.REPO / "windows" / "host" / "Host.cs").read_text(encoding="utf-8")
    assert "won't touch it" in host, "Setup never imports a new distro over the household's disk"


# ─── the file route through an update ─────────────────────────────────────

async def test_the_file_route_serves_the_old_release_until_the_new_one_is_here(tmp_path, cache, monkeypatch):
    async def open_channel(*a, **k):
        return None

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", open_channel)
    put_old(cache)
    m = load(tmp_path, manifest_doc(GOOD))
    monkeypatch.setattr(main, "_android_manifest", lambda: m)
    resp = await main.android_app_file("domovoi-9.9.8-armeabi-v7a.apk")
    assert Path(resp.path).read_bytes() == GOOD["armeabi-v7a"]
    public = await main.android_app_public()
    assert public["ready"] is True and public["newer"]["version_name"] == VERSION
    assert "auto" not in public and "fetch" not in public, "the job's state is the admin's"


# ─── the automatic refresh ────────────────────────────────────────────────

@pytest.mark.parametrize("policy,old_cached,go", [
    ("always", True, True),
    ("always", False, False),      # the first download is an admin's (owner decision 2026-10-15)
    ("sometimes", True, False),    # big automatic downloads stay off (docs/INTERNET.md)
    ("sometimes", False, False),
    ("never", True, False),
    ("never", False, False),
    ("", True, False),             # not answered: every default stays as it was
    ("", False, False),
])
def test_the_automatic_refresh_decision(tmp_path, cache, policy, old_cached, go):
    if old_cached:
        put_old(cache)
    m = load(tmp_path, manifest_doc(GOOD))
    ok, why = aa.auto_fetch_decision(m, policy)
    assert ok is go, why
    if go:
        assert OLD_VERSION in why and VERSION in why
    elif policy == "never":
        assert "turned off" in why and "upload" in why


def test_no_refresh_when_the_release_is_here_or_there_is_none(tmp_path, cache, github):
    none = load(tmp_path, {"manifest_version": 1, "release": None})
    assert aa.auto_fetch_decision(none, "always") == (False, "there is no app release yet")
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        assert aa.fetch(m)[0]
    ok, why = aa.auto_fetch_decision(m, "always")
    assert not ok and "already on this server" in why


def _run(coro):
    return asyncio.run(coro)


async def _no_wait(_):
    return None


@pytest.fixture
def fresh_job():
    aa._job.update(running=False, ok=None, message=None)
    aa._auto.update(state="idle", reason=None, tries=0, next_at=None)
    yield


def test_the_refresh_fetches_the_new_release_where_an_admin_put_the_old_one(tmp_path, cache, github, fresh_job):
    old = put_old(cache)
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        why = _run(aa.auto_refresh(lambda: m, delays=(0.0, 0.0), sleep=_no_wait, poll=0.01))
    assert "is on this server" in why and aa._auto["state"] == "done" and aa._auto["tries"] == 1
    assert aa.status(m)["current_ready"] is True and not old.exists()


@pytest.mark.parametrize("policy", ["never", "sometimes", ""])
def test_the_refresh_downloads_nothing_unless_internet_is_always(tmp_path, cache, github, fresh_job, policy):
    put_old(cache)
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy(policy):
        _run(aa.auto_refresh(lambda: m, delays=(0.0,), sleep=_no_wait))
    assert github.seen == [] and aa._auto["state"] == "skipped"


def test_the_refresh_never_makes_the_first_download(tmp_path, cache, github, fresh_job):
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        why = _run(aa.auto_refresh(lambda: m, delays=(0.0,), sleep=_no_wait))
    assert github.seen == [] and "first download is an admin's" in why


def test_the_refresh_retries_with_backoff_then_stops(tmp_path, cache, github, fresh_job):
    put_old(cache)
    github.files.clear()
    m = load(tmp_path, manifest_doc(GOOD))
    waits: list[float] = []

    async def sleep(d):
        waits.append(d)

    with egress.override_policy("always"):
        _run(aa.auto_refresh(lambda: m, delays=(1.0, 5.0, 25.0), sleep=sleep, poll=0.01))
    assert [w for w in waits if w >= 1.0] == [1.0, 5.0, 25.0]
    assert aa._auto["state"] == "failed" and aa._auto["tries"] == 3
    assert aa.public_status(m)["release"]["version_name"] == OLD_VERSION, "the old release still serves"


def test_the_refresh_waits_out_a_freeze(tmp_path, cache, github, fresh_job):
    put_old(cache)
    m = load(tmp_path, manifest_doc(GOOD))

    async def frozen():
        return True

    with egress.override_policy("always"):
        why = _run(aa.auto_refresh(lambda: m, delays=(0.0,), sleep=_no_wait, frozen=frozen))
    assert "freeze" in why and github.seen == []


def test_the_core_starts_and_stops_the_refresh():
    src = (aa.REPO / "domovoi" / "main.py").read_text(encoding="utf-8")
    assert "schedule_auto_refresh(frozen=container_freeze.is_frozen)" in src
    assert '("Android app refresh", _android_app.cancel_auto_refresh' in src
    assert aa.AUTO_DELAYS[0] >= 60, "never in the middle of the core's own start"


# ─── the admin tier ───────────────────────────────────────────────────────

def test_only_an_admin_starts_a_download_or_reads_its_state():
    from domovoi.admin_auth import require_admin_mutation, require_admin_read

    routes = {r.path: r for r in main.app.routes if hasattr(r, "dependant")}
    assert any(d.dependency is require_admin_mutation for d in routes["/v1/admin/android-app/fetch"].dependencies)
    assert any(d.dependency is require_admin_read for d in routes["/v1/admin/android-app"].dependencies)
    from web.backend.api import satellites as web_sat

    web = {r.path: r for r in web_sat.router.routes if hasattr(r, "dependant")}
    fetch = next(r for p, r in web.items() if p.endswith("/android-app/fetch"))
    assert any(d.dependency.__name__ == "require_admin_mutation" for d in fetch.dependencies)
    status = next(r for p, r in web.items() if p.endswith("/android-app") and "GET" in r.methods)
    assert any(d.dependency.__name__ == "require_admin_read" for d in status.dependencies)


# ─── the join page, the last resort ───────────────────────────────────────

def test_the_join_page_without_the_app_tells_the_admin_where_and_offers_reload():
    from domovoi.tests.test_android_app import PAYLOAD, _app_status, _join

    page = _join("http://192.168.1.20:6370/join#" + PAYLOAD, _app_status(ready=(False, False)))
    words = page["install-state"]["text"]
    assert "Settings → Rooms & devices → Phones & browsers → Download the app to this server" in words
    assert "reload this page" in words and "Not the admin?" in words
    assert not page["reload-line"]["hidden"]
    ready = _join("http://192.168.1.20:6370/join#" + PAYLOAD, _app_status())
    assert ready["reload-line"]["hidden"], "no Reload while the app is here"


def test_the_join_page_s_reload_button_reloads():
    import json
    import shutil
    import subprocess

    from domovoi.tests.test_android_app import JOIN_HARNESS, PAYLOAD, _app_status

    scenario = json.dumps({"href": "http://192.168.1.20:6370/join#" + PAYLOAD, "ua": "Android", "platform": "",
                           "app": _app_status(ready=(False, False)), "clicks": ["reload"]})
    proc = subprocess.run([shutil.which("node"), str(JOIN_HARNESS), str(aa.REPO), scenario],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["reloads"] == 1


def test_the_join_page_offers_an_older_release_as_what_it_is(tmp_path, cache):
    """After an update the server's status names the release it serves (the
    old one): the page offers those files and says that version."""
    from domovoi.tests.test_android_app import PAYLOAD, _join

    put_old(cache)
    m = load(tmp_path, manifest_doc(GOOD))
    page = _join("http://192.168.1.20:6370/join#" + PAYLOAD, aa.public_status(m))
    assert page["dl-main"]["href"] == "/v1/android-app/files/domovoi-9.9.8-arm64-v8a.apk"
    assert page["version"]["text"] == OLD_VERSION and page["install-state"]["hidden"]
