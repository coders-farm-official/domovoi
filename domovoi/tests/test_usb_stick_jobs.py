"""The core's half of the room stick (domovoi/usb_stick/jobs.py and the
/v1/usb-stick routes in domovoi/main.py).

Old computers (USB stick), 2026-10-16. "Prepare the stick" refuses up front
with a reason (a job running, no xorriso, internet "never" without Debian's
image, short of disk), downloads Debian's netinst from its pinned URLs only
when it is not cached, builds the image, keeps one stick per room and at
most three, and serves each by its random id with Raspberry Pi Imager's
list and manifest. No DB, no network: the cache is a tmp dir, the download
a stand-in for fetchers._fetch_pinned, xorriso a small script.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from domovoi import egress, main
from domovoi.satellite_media import cache as media_cache
from domovoi.satellite_media import fetchers
from domovoi.usb_stick import image, jobs, repack
from satellite import enrolment

FP = "SHA256:" + base64.b64encode(hashlib.sha256(b"this server").digest()).decode().rstrip("=")
OTHER_FP = "SHA256:" + base64.b64encode(hashlib.sha256(b"another server").digest()).decode().rstrip("=")
CORE = "http://192.168.0.117:6370"
ISO = b"DEBIAN-NETINST" * 4096
PIN = {
    "pin_version": 1, "name": "debian-13.7.0-amd64-netinst.iso", "version": "13.7.0",
    "urls": ["https://cdimage.debian.org/debian-cd/13.7.0/amd64/iso-cd/debian-13.7.0-amd64-netinst.iso",
             "https://cdimage.debian.org/cdimage/archive/13.7.0/amd64/iso-cd/debian-13.7.0-amd64-netinst.iso"],
    "size": len(ISO), "sha256": hashlib.sha256(ISO).hexdigest(), "released": "2026-09-12",
    "shim_signatures": ["Microsoft Corporation UEFI CA 2011", "Microsoft UEFI CA 2023"],
}
BUNDLE = b"\x1f\x8b" + b"bundle" * 200


def code(room: str, fp: str = FP) -> str:
    return enrolment.build(CORE, fp, room)


FAKE_XORRISO = r'''
import sys
args = sys.argv[1:]
src = args[args.index("-indev") + 1]; out = args[args.index("-outdev") + 1]
open(out, "wb").write(open(src, "rb").read() + b"OUR FILES")
'''


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The cache and the builds in a tmp dir, a fake xorriso, internet on,
    plenty of disk, no job running."""
    monkeypatch.setattr(media_cache, "CACHE_ROOT", tmp_path / "satellite_media" / "cache")
    script = tmp_path / "fake_xorriso.py"
    script.write_text(FAKE_XORRISO, encoding="utf-8")
    if sys.platform == "win32":
        launcher = tmp_path / "xorriso.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "xorriso"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    monkeypatch.setattr(repack, "find_xorriso", lambda: str(launcher))
    monkeypatch.delenv("DOMOVOI_USB_STICK_REPACK", raising=False)
    monkeypatch.setattr(egress, "internet_turned_off", lambda: False)
    monkeypatch.setattr(jobs, "free_bytes", lambda path: 10 ** 12)
    image._checked.clear()
    jobs._job.update(running=False, stage=None, ok=None, message=None, reason=None, build=None, work=None,
                     room=None, room_id=None, bytes_done=0, bytes_todo=0)
    yield tmp_path
    jobs._job.update(running=False)


def cached(home: Path) -> Path:
    p = jobs.iso_path(PIN)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(ISO)
    return p


# ─── refusals, before anything is downloaded or written ───────────────────

def test_a_server_without_xorriso_says_what_to_install(home, monkeypatch):
    monkeypatch.setattr(repack, "find_xorriso", lambda: None)
    why = jobs.refusal(PIN)
    assert why["reason"] == "no_xorriso" and "xorriso" in why["message"]
    monkeypatch.setenv("DOMOVOI_USB_STICK_REPACK", '["python", "repack_in_docker.py"]')
    assert jobs.refusal(PIN) is None, "the test seam stands in for xorriso"


def test_internet_never_refuses_only_while_debian_s_image_is_not_here(home, monkeypatch):
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    why = jobs.refusal(PIN)
    assert why["reason"] == "internet_off" and "Debian 13.7.0" in why["message"]
    cached(home)
    assert jobs.refusal(PIN) is None, "a cached image needs no download"


def test_a_short_disk_is_said_with_what_it_needs(home, monkeypatch):
    monkeypatch.setattr(jobs, "free_bytes", lambda path: 100)
    why = jobs.refusal(PIN)
    assert why["reason"] == "disk" and "needs about" in why["message"] and "kept for the next stick" in why["message"]
    assert jobs.need_bytes(PIN, cached=False) == 2 * PIN["size"] + jobs.MARGIN_BYTES
    assert jobs.need_bytes(PIN, cached=True) == PIN["size"] + jobs.MARGIN_BYTES


def test_one_job_at_a_time(home):
    jobs._job["running"] = True
    assert jobs.refusal(PIN)["reason"] == "busy"
    started, why = jobs.start(PIN, code("Den"), lambda: BUNDLE, image.Options())
    assert started is False and why["reason"] == "busy"


def test_the_repack_seam_reads_a_path_or_a_json_list(monkeypatch):
    monkeypatch.setenv("DOMOVOI_USB_STICK_REPACK", r"C:\kit\repack.cmd")
    assert jobs.repack_command() == [r"C:\kit\repack.cmd"]
    monkeypatch.setenv("DOMOVOI_USB_STICK_REPACK", '["py", "-I", "kit.py"]')
    assert jobs.repack_command() == ["py", "-I", "kit.py"]
    monkeypatch.setenv("DOMOVOI_USB_STICK_REPACK", "[broken")
    assert jobs.repack_command() is None
    monkeypatch.delenv("DOMOVOI_USB_STICK_REPACK")
    assert jobs.repack_command() is None


# ─── the download ─────────────────────────────────────────────────────────

def test_the_download_tries_debian_s_urls_in_turn_and_keeps_only_the_pinned_bytes(home, monkeypatch):
    seen = []

    def fetch(url, dest, *, sha256, size, progress=None):
        seen.append(url)
        assert (sha256, size) == (PIN["sha256"], PIN["size"]), "held to the pin"
        if "archive" not in url:
            raise ValueError("HTTP 404")
        dest.write_bytes(ISO)
        progress(len(ISO))

    monkeypatch.setattr(fetchers, "_fetch_pinned", fetch)
    jobs._download(PIN)
    assert seen == PIN["urls"]
    assert image.iso_matches(jobs.iso_path(PIN), PIN)
    seen.clear()
    jobs._download(PIN)
    assert seen == [], "cached: nothing downloaded"


def test_a_failed_download_names_each_host_and_never_under_never(home, monkeypatch):
    def fetch(url, dest, **kw):
        raise ValueError("checksum mismatch")

    monkeypatch.setattr(fetchers, "_fetch_pinned", fetch)
    with pytest.raises(RuntimeError) as e:
        jobs._download(PIN)
    assert "cdimage.debian.org: checksum mismatch" in str(e.value)
    assert not jobs.iso_path(PIN).exists()
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    with pytest.raises(RuntimeError, match="turned off"):
        jobs._download(PIN)


def test_an_older_pin_s_image_is_dropped_when_a_new_one_downloads(home, monkeypatch):
    old = media_cache.CACHE_ROOT / "usb_stick" / "13.6.0" / "debian-13.6.0-amd64-netinst.iso"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    monkeypatch.setattr(fetchers, "_fetch_pinned", lambda url, dest, **kw: dest.write_bytes(ISO))
    jobs._download(PIN)
    assert not old.parent.exists()


# ─── a build, its files, the list Imager reads ────────────────────────────

def test_a_build_keeps_the_image_its_size_and_digest(home):
    cached(home)
    doc = jobs.build(PIN, code("Kid's Room"), BUNDLE, image.Options(), epoch=1_760_000_000)
    assert jobs.valid_id(doc["id"]) and doc["room_id"] == "kids_room" and doc["core"] == CORE
    path = jobs.image_file(doc["id"])
    assert path.read_bytes().startswith(ISO) and path.read_bytes().endswith(b"OUR FILES")
    assert doc["size"] == path.stat().st_size and doc["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert sorted(p.name for p in path.parent.iterdir()) == ["build.json", image.IMAGE_NAME], "no files tar left"
    assert [b["id"] for b in jobs.list_builds()] == [doc["id"]]
    pub = jobs.public_build(doc)
    base = f"{CORE}/v1/usb-stick/{doc['id']}/"
    assert (pub["image_url"], pub["os_list_url"], pub["manifest_url"]) == (
        base + "domovoi-room-stick.img", base + "os-list.json", base + "domovoi-room-stick.rpi-imager-manifest")


def test_imager_s_list_has_one_device_one_image_and_no_customisation(home):
    cached(home)
    doc = jobs.build(PIN, code("Kitchen"), BUNDLE, image.Options(), epoch=1_760_000_000)
    lst = jobs.os_list(doc)
    (dev,) = lst["imager"]["devices"]
    (os_,) = lst["os_list"]
    for key in ("name", "description", "icon", "url", "extract_size", "extract_sha256",
                "image_download_size", "release_date", "devices"):
        assert key in os_, key
    assert os_["devices"] == dev["tags"] == [jobs.IMAGER_DEVICE_TAG]
    assert os_["extract_sha256"] == doc["sha256"] and os_["extract_size"] == doc["size"]
    assert os_["release_date"] == "2025-10-09"
    assert "init_format" not in os_, "no OS customisation offered"
    assert "Kitchen" not in json.dumps(lst), "the room's name stays off the list"
    assert os_["url"].startswith("http://192.168.0.117:6370/v1/usb-stick/") and os_["url"].endswith(".img")


def test_one_stick_per_room_and_at_most_three(home):
    cached(home)
    ids = {}
    for i, room in enumerate(["Den", "Den", "Kitchen", "Office", "Garage"]):
        ids.setdefault(room, []).append(jobs.build(PIN, code(room), BUNDLE, image.Options(), epoch=1_760_000_000 + i)["id"])
    kept = [b["room"] for b in jobs.list_builds()]
    assert kept == ["Garage", "Office", "Kitchen"], "newest first, the oldest went"
    assert jobs.build_info(ids["Den"][0]) is None and jobs.build_info(ids["Den"][1]) is None


def test_an_image_that_changed_size_is_not_served_and_a_removed_one_is_gone(home):
    cached(home)
    doc = jobs.build(PIN, code("Den"), BUNDLE, image.Options(), epoch=1_760_000_000)
    path = jobs.image_file(doc["id"])
    with open(path, "ab") as f:
        f.write(b"tampered")
    assert jobs.image_file(doc["id"]) is None
    assert jobs.delete_build(doc["id"]) is True
    assert jobs.build_info(doc["id"]) is None and not path.parent.exists()
    assert jobs.delete_build(doc["id"]) is False


@pytest.mark.parametrize("bad", ["", "../../etc", "x" * 31, "x" * 33, "a/b" + "x" * 29, None, 7])
def test_only_a_well_formed_id_names_a_build(home, bad):
    assert not jobs.valid_id(bad)
    assert jobs.build_info(bad) is None and jobs.image_file(bad) is None


def test_a_half_made_build_is_never_listed_and_is_cleaned_up(home):
    cached(home)
    half = jobs.builds_root() / (".A" * 16)
    half.mkdir(parents=True)
    (half / image.IMAGE_NAME).write_bytes(b"partial")
    assert jobs.list_builds() == []
    jobs.build(PIN, code("Den"), BUNDLE, image.Options(), epoch=1_760_000_000)
    assert not half.exists()


def test_a_failed_repack_leaves_no_build(home, monkeypatch):
    cached(home)

    def broken(*a, **k):
        raise repack.RepackError("xorriso failed (exit 5)")

    monkeypatch.setattr(jobs, "_repack", broken)
    with pytest.raises(repack.RepackError):
        jobs.build(PIN, code("Den"), BUNDLE, image.Options())
    assert jobs.list_builds() == [] and not list(jobs.builds_root().iterdir())


def test_the_repack_command_seam_gets_repack_s_own_arguments(home, monkeypatch, tmp_path):
    cached(home)
    seen = tmp_path / "seen.json"
    kit = tmp_path / "kit.py"
    kit.write_text(
        "import json, sys, shutil\n"
        "a = sys.argv[1:]\n"
        f"json.dump(a, open({str(seen)!r}, 'w'))\n"
        "shutil.copyfile(a[a.index('--iso') + 1], a[a.index('--out') + 1])\n"
        "open(a[a.index('--out') + 1], 'ab').write(b'kit')\n", encoding="utf-8")
    monkeypatch.setenv("DOMOVOI_USB_STICK_REPACK", json.dumps([sys.executable, str(kit)]))
    doc = jobs.build(PIN, code("Den"), BUNDLE, image.Options(), epoch=1_760_000_123)
    args = json.loads(seen.read_text())
    assert args[args.index("--epoch") + 1] == "1760000123"
    assert Path(args[args.index("--iso") + 1]) == jobs.iso_path(PIN)
    assert jobs.image_file(doc["id"]).read_bytes().endswith(b"kit")


# ─── the job ──────────────────────────────────────────────────────────────

def _wait(timeout: float = 60.0) -> dict:
    t0 = time.time()
    while jobs.job_state()["running"]:
        assert time.time() - t0 < timeout, "the job did not finish"
        time.sleep(0.05)
    return jobs.job_state()


def test_the_job_downloads_builds_and_says_it_is_ready(home, monkeypatch):
    monkeypatch.setattr(fetchers, "_fetch_pinned", lambda url, dest, **kw: dest.write_bytes(ISO))
    started, why = jobs.start(PIN, code("Den"), lambda: BUNDLE, image.Options())
    assert started and why is None
    state = _wait()
    assert state["ok"] is True and state["message"] == "The stick for Den is ready."
    assert state["room_id"] == "den" and jobs.build_info(state["build"]) is not None
    status = jobs.status(PIN)
    assert status["debian"]["cached"] is True and status["refusal"] is None
    assert [b["room"] for b in status["builds"]] == ["Den"]


def test_a_failure_stays_on_the_card_until_the_next_try(home, monkeypatch):
    def fetch(url, dest, **kw):
        raise ValueError("connection refused")

    monkeypatch.setattr(fetchers, "_fetch_pinned", fetch)
    assert jobs.start(PIN, code("Den"), lambda: BUNDLE, image.Options())[0]
    state = _wait()
    assert state["ok"] is False and state["reason"] == "failed" and "connection refused" in state["message"]
    assert jobs.status(PIN)["job"]["message"] == state["message"], "still there for the next poll"
    monkeypatch.setattr(fetchers, "_fetch_pinned", lambda url, dest, **kw: dest.write_bytes(ISO))
    assert jobs.start(PIN, code("Den"), lambda: BUNDLE, image.Options())[0]
    assert jobs.job_state()["message"] is None, "the next try clears it"
    assert _wait()["ok"] is True


# ─── the core's routes ────────────────────────────────────────────────────

@pytest.fixture
def routes(home, monkeypatch):
    async def open_channel(*a, **k):
        return None

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", open_channel)
    monkeypatch.setattr(main, "_usb_stick_pin", lambda: PIN)
    monkeypatch.setattr(main.server_identity, "load_or_create", lambda: SimpleNamespace(fingerprint=FP))

    async def no_clips():
        return {}

    monkeypatch.setattr(main, "_usb_stick_clips", no_clips)
    return home


async def test_the_build_route_takes_only_this_server_s_room_code(routes):
    with pytest.raises(HTTPException) as e:
        await main.admin_usb_stick_build(main._UsbStickBuildBody(payload=code("Den", fp=OTHER_FP)))
    assert e.value.status_code == 422 and e.value.detail["reason"] == "not_this_server"
    with pytest.raises(HTTPException) as e:
        await main.admin_usb_stick_build(main._UsbStickBuildBody(payload="domovoi-room:1?core=nope"))
    assert e.value.status_code == 422


async def test_the_build_route_refuses_with_the_reason_before_starting(routes, monkeypatch):
    monkeypatch.setattr(egress, "internet_turned_off", lambda: True)
    with pytest.raises(HTTPException) as e:
        await main.admin_usb_stick_build(main._UsbStickBuildBody(payload=code("Den")))
    assert e.value.status_code == 409 and e.value.detail["reason"] == "internet_off"
    assert jobs.job_state()["running"] is False


async def test_the_build_route_starts_the_job_with_this_server_s_bundle(routes, monkeypatch):
    cached(routes)
    got = {}

    def start(pin, payload, bundle_fn, options, clips):
        got.update(payload=payload, bundle=bundle_fn(), options=options, clips=clips)
        return True, None

    monkeypatch.setattr(jobs, "start", start)
    monkeypatch.setattr(main.settings, "usb_stick_wifi", False)
    out = await main.admin_usb_stick_build(main._UsbStickBuildBody(payload=code("Den")))
    assert out["started"] is True and "builds" in out
    from domovoi import satellite_bundle

    assert got["bundle"] == satellite_bundle.current(main.SATELLITE_CODE_DIR, main._is_allowed_satellite_code).data
    assert got["options"].wifi is False and got["options"].debug is False
    monkeypatch.setattr(main.settings, "usb_stick_wifi", True)
    await main.admin_usb_stick_build(main._UsbStickBuildBody(payload=code("Den")))
    assert got["options"].wifi is True, "USB_STICK_WIFI reaches the next stick"


async def test_the_image_its_list_and_manifest_are_served_by_their_id(routes):
    cached(routes)
    doc = jobs.build(PIN, code("Den"), BUNDLE, image.Options(), epoch=1_760_000_000)
    resp = await main.usb_stick_image(doc["id"])
    assert Path(resp.path) == jobs.image_file(doc["id"])
    assert 'filename="domovoi-room-stick.img"' in resp.headers["content-disposition"]
    lst = await main.usb_stick_os_list(doc["id"])
    assert lst["os_list"][0]["extract_sha256"] == doc["sha256"]
    man = await main.usb_stick_manifest(doc["id"])
    assert json.loads(man.body) == lst
    assert "rpi-imager-manifest" in man.headers["content-disposition"]
    for bad in ("x" * 32, "../" + doc["id"][3:], doc["id"] + "x"):
        for call in (main.usb_stick_image, main.usb_stick_os_list, main.usb_stick_manifest):
            with pytest.raises(HTTPException) as e:
                await call(bad)
            assert e.value.status_code == 404
    out = await main.admin_usb_stick_delete(doc["id"])
    assert out["builds"] == []
    with pytest.raises(HTTPException) as e:
        await main.admin_usb_stick_delete(doc["id"])
    assert e.value.status_code == 404


async def test_a_frozen_server_serves_and_builds_no_stick(routes, monkeypatch):
    async def frozen(*a, **k):
        raise HTTPException(status_code=423, detail="frozen")

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", frozen)
    for call in (lambda: main.usb_stick_image("x" * 32), lambda: main.usb_stick_os_list("x" * 32),
                 lambda: main.admin_usb_stick_build(main._UsbStickBuildBody(payload=code("Den")))):
        with pytest.raises(HTTPException) as e:
            await call()
        assert e.value.status_code == 423


def test_the_admin_routes_are_an_admin_s_and_the_stick_s_files_are_public_by_id():
    from domovoi.admin_auth import require_admin_mutation, require_admin_read

    r = {(p.path, tuple(sorted(p.methods))): p for p in main.app.routes if hasattr(p, "methods")}
    assert any(d.dependency is require_admin_read for d in r[("/v1/admin/usb-stick", ("GET",))].dependencies)
    for key in (("/v1/admin/usb-stick/build", ("POST",)), ("/v1/admin/usb-stick/builds/{build_id}", ("DELETE",))):
        assert any(d.dependency is require_admin_mutation for d in r[key].dependencies), key
    for path in ("/v1/usb-stick/{build_id}/domovoi-room-stick.img", "/v1/usb-stick/{build_id}/os-list.json",
                 "/v1/usb-stick/{build_id}/domovoi-room-stick.rpi-imager-manifest"):
        assert not r[(path, ("GET",))].dependencies, path


def test_the_debug_seam_and_the_wifi_setting_reach_the_options(monkeypatch):
    monkeypatch.setenv("DOMOVOI_USB_STICK_DEBUG", "1")
    monkeypatch.setattr(main.settings, "usb_stick_wifi", False)
    assert main._usb_stick_options().debug is True
    monkeypatch.delenv("DOMOVOI_USB_STICK_DEBUG")
    o = main._usb_stick_options()
    assert o.debug is False and o.wifi is False
    o.check()
