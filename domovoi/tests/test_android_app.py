"""The Android app on the core (domovoi/android_app.py, the /v1/android-app
routes and the join page): a manifest that pins every byte, a fetch from a
fake GitHub release that keeps only APKs signed by the pinned key, an
admin upload held to the same checks, nothing fetched when internet
access is off, and the routes' tiers. No DB, no network: the release is an
httpx MockTransport serving the signed fixtures
(domovoi/tests/fixtures/android_app/, throwaway keys), the cache a tmp dir.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from domovoi import android_app as aa
from domovoi import egress, main, net_safety

FIX = Path(__file__).resolve().parent / "fixtures" / "android_app"
META = json.loads((FIX / "fixtures.json").read_text(encoding="utf-8"))["files"]
RELEASE_CERT = META["release-arm64-v8a.apk"]["apksigner_cert_sha256"]
OTHER_CERT = META["other-arm64-v8a.apk"]["apksigner_cert_sha256"]
VERSION = "9.9.9"
TAG = aa.tag_for(VERSION)
REL_PATH = "/coders-farm-official/domovoi/releases/download/" + TAG + "/"


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


def manifest_doc(files: dict[str, bytes], cert: str = RELEASE_CERT, **over) -> dict:
    """A release.json naming ``files`` ({abi: bytes}) at their GitHub URLs."""
    apks = []
    for abi, body in files.items():
        name = aa.file_name(VERSION, abi)
        apks.append({"abi": abi, "file": name, "url": aa.RELEASE_DOWNLOAD + TAG + "/" + name,
                     "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)})
    rel = {"package": aa.PACKAGE, "version_name": VERSION, "version_code": 99, "tag": TAG,
           "commit": "a" * 40, "published": "2026-10-15", "cert_sha256": cert, "apks": apks}
    rel.update(over)
    return {"manifest_version": 1, "release": rel}


def load(tmp_path: Path, doc: dict) -> dict:
    path = tmp_path / "release.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return aa.load_manifest(path)


GOOD = {"arm64-v8a": fixture("release-arm64-v8a.apk"), "armeabi-v7a": fixture("release-armeabi-v7a.apk")}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    from domovoi.satellite_media import cache as media_cache

    monkeypatch.setattr(media_cache, "CACHE_ROOT", tmp_path / "cache")
    aa._checked.clear()
    return tmp_path / "cache" / "android_app" / "99"


@pytest.fixture
def github(monkeypatch):
    """A fake GitHub: release downloads redirect to an asset host, as the
    real one does; ``files`` maps an asset name to what it serves."""
    class Release:
        files: dict[str, bytes] = {}
        seen: list[str] = []

    Release.files = {aa.file_name(VERSION, abi): body for abi, body in GOOD.items()}
    Release.seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host")
        Release.seen.append(f"{host}{request.url.path}")
        if host == "github.com" and request.url.path.startswith(REL_PATH):
            name = request.url.path[len(REL_PATH):]
            return httpx.Response(302, headers={"location": f"https://release-assets.githubusercontent.com/x/{name}"})
        if host == "release-assets.githubusercontent.com":
            name = request.url.path.rsplit("/", 1)[-1]
            if name in Release.files:
                return httpx.Response(200, content=Release.files[name])
        return httpx.Response(404)

    from domovoi.satellite_media import fetchers

    monkeypatch.setattr(net_safety, "resolve_host", lambda host: [ipaddress.ip_address("140.82.112.3")])
    monkeypatch.setattr(fetchers, "_oww_http_client",
                        lambda: egress.sync_client(transport=httpx.MockTransport(handler)))
    return Release


# ─── the manifest ─────────────────────────────────────────────────────────

def test_the_tree_s_manifest_loads_and_says_whether_there_is_a_release():
    m = aa.load_manifest()
    rel = m["release"]
    if rel is None:
        assert aa.public_status(m) == {"release": None, "apks": [], "ready": False}
    else:
        assert {e["abi"] for e in rel["apks"]} == set(aa.ABIS)
        assert all(e["url"].startswith(aa.RELEASE_DOWNLOAD) for e in rel["apks"])


def test_a_manifest_names_its_files_exactly(tmp_path):
    m = load(tmp_path, manifest_doc(GOOD))
    assert [e["file"] for e in m["release"]["apks"]] == ["domovoi-9.9.9-arm64-v8a.apk",
                                                         "domovoi-9.9.9-armeabi-v7a.apk"]


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(manifest_version=2),
    lambda d: d["release"].update(package="com.evil.app"),
    lambda d: d["release"].update(version_name="1.0; rm -rf"),
    lambda d: d["release"].update(version_code=0),
    lambda d: d["release"].update(version_code=True),
    lambda d: d["release"].update(tag="v9.9.9"),
    lambda d: d["release"].update(cert_sha256="AB" * 32),
    lambda d: d["release"].update(apks=[]),
    lambda d: d["release"]["apks"][0].update(abi="x86_64"),
    lambda d: d["release"]["apks"][1].update(abi="arm64-v8a"),
    lambda d: d["release"]["apks"][0].update(file="../release.json"),
    lambda d: d["release"]["apks"][0].update(url="https://evil.example/domovoi-9.9.9-arm64-v8a.apk"),
    lambda d: d["release"]["apks"][0].update(
        url="https://github.com/someone-else/domovoi/releases/download/android-v9.9.9/domovoi-9.9.9-arm64-v8a.apk"),
    lambda d: d["release"]["apks"][0].update(sha256="zz"),
    lambda d: d["release"]["apks"][0].update(size=0),
    lambda d: d["release"]["apks"][0].update(size=aa.MAX_APK_BYTES + 1),
])
def test_a_malformed_manifest_is_refused(tmp_path, mutate):
    doc = manifest_doc(GOOD)
    mutate(doc)
    with pytest.raises(aa.ManifestError):
        load(tmp_path, doc)


# ─── fetching from the release ────────────────────────────────────────────

def test_the_fetch_keeps_the_signed_pinned_apks_and_serves_them(tmp_path, cache, github):
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        ok, message = aa.fetch(m)
    assert ok, message
    assert sorted(p.name for p in cache.iterdir()) == ["domovoi-9.9.9-arm64-v8a.apk", "domovoi-9.9.9-armeabi-v7a.apk"]
    assert aa.cached_file(m, "domovoi-9.9.9-arm64-v8a.apk").read_bytes() == GOOD["arm64-v8a"]
    st = aa.public_status(m)
    assert st["ready"] is True and [a["ready"] for a in st["apks"]] == [True, True]
    assert st["apks"][0]["path"] == "/v1/android-app/files/domovoi-9.9.9-arm64-v8a.apk"
    hosts = {s.split("/")[0] for s in github.seen}
    assert hosts == {"github.com", "release-assets.githubusercontent.com"}
    # Already verified files are not downloaded again.
    github.seen.clear()
    with egress.override_policy("always"):
        assert aa.fetch(m)[0]
    assert github.seen == []


def test_an_apk_signed_by_another_key_is_never_kept(tmp_path, cache, github):
    other = fixture("other-arm64-v8a.apk")
    github.files[aa.file_name(VERSION, "arm64-v8a")] = other
    m = load(tmp_path, manifest_doc({"arm64-v8a": other, "armeabi-v7a": GOOD["armeabi-v7a"]}))
    with egress.override_policy("always"):
        ok, message = aa.fetch(m)
    assert not ok and "not the release key" in message and "1 of 2" in message
    assert not (cache / "domovoi-9.9.9-arm64-v8a.apk").exists()
    assert not any(p.name.startswith(".") for p in cache.iterdir()), "no partial file is left"
    assert aa.cached_file(m, "domovoi-9.9.9-armeabi-v7a.apk") is not None


def test_bytes_that_are_not_the_pinned_ones_are_refused(tmp_path, cache, github):
    m = load(tmp_path, manifest_doc(GOOD))
    good = GOOD["arm64-v8a"]
    github.files[aa.file_name(VERSION, "arm64-v8a")] = good[:100] + bytes([good[100] ^ 1]) + good[101:]  # same size
    github.files[aa.file_name(VERSION, "armeabi-v7a")] = GOOD["armeabi-v7a"] + b"more"    # bigger
    with egress.override_policy("always"):
        ok, message = aa.fetch(m)
    assert not ok and "checksum mismatch" in message and "larger than" in message
    assert sorted(p.name for p in cache.iterdir()) == []


def test_a_pinned_hash_does_not_stand_in_for_the_signature(tmp_path, cache, github):
    """The manifest may pin a file's hash correctly and still name a file
    whose contents were changed after signing: the signature check refuses."""
    good = GOOD["arm64-v8a"]
    at = good.index(b"\x7fELF") + 10
    tampered = good[:at] + bytes([good[at] ^ 1]) + good[at + 1:]
    github.files[aa.file_name(VERSION, "arm64-v8a")] = tampered
    m = load(tmp_path, manifest_doc({"arm64-v8a": tampered}))
    with egress.override_policy("always"):
        ok, message = aa.fetch(m)
    assert not ok and "changed after signing" in message
    assert aa.cached_file(m, "domovoi-9.9.9-arm64-v8a.apk") is None


@pytest.mark.parametrize("abi,body", [
    ("armeabi-v7a", fixture("release-arm64-v8a.apk")),     # the 64-bit APK in the 32-bit slot
    ("arm64-v8a", fixture("release-two-abis.apk")),        # libraries for two ABIs
], ids=["64_bit_in_the_32_bit_slot", "two_abis"])
def test_the_wrong_abi_is_refused(tmp_path, cache, github, abi, body):
    github.files[aa.file_name(VERSION, abi)] = body
    m = load(tmp_path, manifest_doc({abi: body}))
    with egress.override_policy("always"):
        ok, message = aa.fetch(m)
    assert not ok and f"not {abi} only" in message
    assert aa.cached_file(m, aa.file_name(VERSION, abi)) is None


def test_no_fetch_when_internet_access_is_off(tmp_path, cache, github):
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("never"):
        ok, message = aa.fetch(m)
        assert aa.status(m)["internet_off"] is True
    assert not ok and "turned off" in message
    assert github.seen == []


def test_no_release_means_nothing_to_fetch_or_serve(tmp_path, cache):
    m = load(tmp_path, {"manifest_version": 1, "release": None})
    assert aa.fetch(m) == (False, "there is no app release yet")
    assert aa.cached_file(m, "domovoi-9.9.9-arm64-v8a.apk") is None
    assert aa.status(m)["release"] is None


def test_a_cached_file_that_changes_is_no_longer_served(tmp_path, cache, github):
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        assert aa.fetch(m)[0]
    path = cache / "domovoi-9.9.9-arm64-v8a.apk"
    path.write_bytes(fixture("other-arm64-v8a.apk")[: len(GOOD["arm64-v8a"])].ljust(len(GOOD["arm64-v8a"]), b"\0"))
    assert aa.cached_file(m, path.name) is None
    for name in ("../release.json", "domovoi-9.9.9-x86.apk", "", "domovoi-9.9.9-arm64-v8a.apk/../x"):
        assert aa.cached_file(m, name) is None


def test_an_older_release_s_files_are_dropped(tmp_path, cache, github):
    old = cache.parent / "98"
    old.mkdir(parents=True)
    (old / "domovoi-9.9.8-arm64-v8a.apk").write_bytes(b"old")
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        assert aa.fetch(m)[0]
    assert not old.exists()


# ─── an admin's upload ────────────────────────────────────────────────────

def _upload(m, body: bytes) -> Path:
    target = aa.upload_target(m)
    target.write_bytes(body)
    return target


def test_an_upload_of_a_release_file_is_kept(tmp_path, cache):
    m = load(tmp_path, manifest_doc(GOOD))
    entry = aa.accept_upload(m, _upload(m, GOOD["armeabi-v7a"]))
    assert entry["abi"] == "armeabi-v7a"
    assert aa.cached_file(m, "domovoi-9.9.9-armeabi-v7a.apk") is not None
    assert [p.name for p in cache.iterdir()] == ["domovoi-9.9.9-armeabi-v7a.apk"]


@pytest.mark.parametrize("body,reason", [
    (fixture("other-arm64-v8a.apk"), "unknown"),            # not a file the release names
    (b"not an apk at all", "unknown"),
], ids=["another_apk", "not_an_apk"])
def test_an_upload_that_is_not_a_release_file_is_refused(tmp_path, cache, body, reason):
    m = load(tmp_path, manifest_doc(GOOD))
    with pytest.raises(aa.ApkRefused) as e:
        aa.accept_upload(m, _upload(m, body))
    assert e.value.reason == reason and "9.9.9" in str(e.value)
    assert list(cache.iterdir()) == []


def test_an_upload_matching_a_bad_pin_is_still_held_to_the_key(tmp_path, cache):
    other = fixture("other-arm64-v8a.apk")
    m = load(tmp_path, manifest_doc({"arm64-v8a": other}))     # pins the other key's file, release cert
    with pytest.raises(aa.ApkRefused) as e:
        aa.accept_upload(m, _upload(m, other))
    assert e.value.reason == "cert"
    assert list(cache.iterdir()) == []


async def test_the_upload_route_checks_and_keeps_the_file(tmp_path, cache, monkeypatch):
    from starlette.datastructures import UploadFile

    async def open_channel(*a, **k):
        return None

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", open_channel)
    m = load(tmp_path, manifest_doc(GOOD))
    monkeypatch.setattr(main, "_android_manifest", lambda: m)
    got = await main.admin_android_app_upload(UploadFile(io.BytesIO(GOOD["arm64-v8a"]), filename="x.apk"))
    assert got["accepted"] == "domovoi-9.9.9-arm64-v8a.apk" and got["apks"][0]["ready"] is True
    with pytest.raises(HTTPException) as e:
        await main.admin_android_app_upload(UploadFile(io.BytesIO(fixture("other-arm64-v8a.apk")), filename="y.apk"))
    assert e.value.status_code == 422 and e.value.detail["reason"] == "unknown"
    with pytest.raises(HTTPException) as e:
        await main.admin_android_app_upload(UploadFile(io.BytesIO(GOOD["arm64-v8a"] * 2), filename="z.apk"))
    assert e.value.status_code == 413
    assert sorted(p.name for p in cache.iterdir()) == ["domovoi-9.9.9-arm64-v8a.apk"]


# ─── the routes ───────────────────────────────────────────────────────────

async def test_the_file_route_serves_only_checked_files(tmp_path, cache, monkeypatch):
    async def open_channel(*a, **k):
        return None

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", open_channel)
    m = load(tmp_path, manifest_doc(GOOD))
    monkeypatch.setattr(main, "_android_manifest", lambda: m)
    aa.accept_upload(m, _upload(m, GOOD["arm64-v8a"]))
    resp = await main.android_app_file("domovoi-9.9.9-arm64-v8a.apk")
    assert Path(resp.path) == cache / "domovoi-9.9.9-arm64-v8a.apk"
    assert resp.media_type == "application/vnd.android.package-archive"
    assert 'filename="domovoi-9.9.9-arm64-v8a.apk"' in resp.headers["content-disposition"]
    for name in ("domovoi-9.9.9-armeabi-v7a.apk", "..\\release.json", "join.html"):
        with pytest.raises(HTTPException) as e:
            await main.android_app_file(name)
        assert e.value.status_code == 404
    public = await main.android_app_public()
    assert public["release"]["version_name"] == "9.9.9" and public["ready"] is False
    assert "fetch" not in public and "internet_off" not in public


async def test_a_frozen_server_hands_out_no_app(monkeypatch):
    async def frozen(*a, **k):
        raise HTTPException(status_code=423, detail="frozen")

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", frozen)
    for call in (lambda: main.android_app_file("domovoi-9.9.9-arm64-v8a.apk"), main.admin_android_app_fetch):
        with pytest.raises(HTTPException) as e:
            await call()
        assert e.value.status_code == 423


def test_status_fetch_and_upload_are_an_admin_s_and_the_app_is_public():
    from domovoi.admin_auth import require_admin_mutation, require_admin_read

    routes = {r.path: r for r in main.app.routes if hasattr(r, "dependant")}
    assert any(d.dependency is require_admin_read for d in routes["/v1/admin/android-app"].dependencies)
    for path in ("/v1/admin/android-app/fetch", "/v1/admin/android-app/upload"):
        assert any(d.dependency is require_admin_mutation for d in routes[path].dependencies), path
        assert routes[path].methods == {"POST"}
    for path in ("/v1/android-app", "/v1/android-app/files/{name}", "/join", "/join/{asset}"):
        assert not routes[path].dependencies, path


async def test_the_join_page_is_static_with_a_strict_policy():
    page = await main.join_page()
    assert Path(page.path).name == "join.html" and page.media_type.startswith("text/html")
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe" not in csp
    assert page.headers["referrer-policy"] == "no-referrer"
    js = await main.join_page_asset("join.js")
    assert js.media_type.startswith("text/javascript")
    for bad in ("join.html", "../main.py", "x.js"):
        with pytest.raises(HTTPException):
            await main.join_page_asset(bad)
    html = (main.JOIN_PAGE_DIR / "join.html").read_text(encoding="utf-8")
    assert "<script>" not in html and " style=" not in html, "nothing inline: the policy forbids it"


def test_the_upload_routes_take_an_apk_sized_body():
    from domovoi import transport_guard
    from web.backend import middleware

    for path in ("/v1/admin/android-app/upload", "/api/satellites/android-app/upload"):
        assert transport_guard.body_limit_for(path) >= aa.MAX_APK_BYTES
    assert middleware.body_limit_for("/api/satellites/android-app/upload") >= aa.MAX_APK_BYTES


# ─── the release script's helpers ─────────────────────────────────────────

def test_the_committed_manifest_carries_the_writer_s_words():
    doc = json.loads(aa.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert doc["about"] == aa.MANIFEST_ABOUT


def test_write_then_check_is_what_the_release_script_runs(tmp_path):
    signed = tmp_path / "signed"
    signed.mkdir()
    for abi, body in GOOD.items():
        (signed / aa.file_name(VERSION, abi)).write_bytes(body)
    out = tmp_path / "release.json"
    args = ["write", str(out), str(signed), "--version-name", VERSION, "--version-code", "99",
            "--cert", RELEASE_CERT.upper(), "--commit", "b" * 40, "--published", "2026-10-15"]
    assert aa._main(args) == 0
    m = aa.load_manifest(out)
    assert m["release"]["cert_sha256"] == RELEASE_CERT and m["release"]["commit"] == "b" * 40
    assert [e["url"] for e in m["release"]["apks"]] == [
        f"https://github.com/coders-farm-official/domovoi/releases/download/android-v9.9.9/domovoi-9.9.9-{abi}.apk"
        for abi in aa.ABIS]
    sums = (signed / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert sums == [f"{e['sha256']}  {e['file']}" for e in m["release"]["apks"]]
    assert aa._main(["check", str(out), str(signed)]) == 0
    # Signed by another key: the check the script runs before publishing refuses it.
    other = fixture("other-arm64-v8a.apk")
    (signed / aa.file_name(VERSION, "arm64-v8a")).write_bytes(other)
    assert aa._main(["write", str(out), str(signed), "--version-name", VERSION, "--version-code", "99",
                     "--cert", RELEASE_CERT]) == 0
    assert aa._main(["check", str(out), str(signed)]) == 1
    # A version the manifest can't carry is never written.
    assert aa._main(["write", str(tmp_path / "bad.json"), str(signed), "--version-name", "1.0; x",
                     "--version-code", "99", "--cert", RELEASE_CERT]) == 2
    assert not (tmp_path / "bad.json").exists()
