"""Settings → Satellites → add a spare device → Old computer (USB stick),
the dashboard's side (web/backend/api/satellites.py, web/static/
satellites.jsx), 2026-10-16.

The web routes hand the core the same enrolment payload the card shows and
pass its answers through (a refusal's reason included); the card states
the CPU floor and the cable up front, offers "Prepare the stick" (button
only), shows the download's progress and a failure until the next try,
then the Imager link, the Imager file, the image, the picture steps, the
boot keys, the Secure Boot note and the running cost; a room's health card
warns when its firmware lacks Microsoft's 2023 certificate. No DB, no core:
the hops are replaced, the JSX is driven by jsx_interact_harness.js.
"""

from __future__ import annotations

import json
import urllib.parse

import pytest
from fastapi import HTTPException

from domovoi.tests.test_web_spare_device_enrol import (
    FILES, FP, LINUX_RUNTIME, SHA, WIN_RUNTIME, Req, _SETUP, _drive,
)
from satellite import enrolment
from web.backend.api import satellites as web_sats
from web.backend.schemas import SpareDeviceEnrolRequest

CORE = "http://192.168.1.20:6370"
BUILD_ID = "AbCdEfGhIjKlMnOpQrStUvWxYz012345"
IDLE = {"running": False, "stage": None, "room": None, "room_id": None, "ok": None, "message": None,
        "reason": None, "bytes_done": 0, "bytes_todo": 0, "build": None}
DEBIAN = {"version": "13.7.0", "name": "debian-13.7.0-amd64-netinst.iso", "size": 792723456, "cached": False,
          "shim_signatures": ["Microsoft Corporation UEFI CA 2011", "Microsoft UEFI CA 2023"]}
STATUS = {"debian": DEBIAN, "refusal": None, "internet_off": False, "disk_free": 10 ** 11,
          "need_bytes": 1_850_000_000, "job": IDLE, "builds": []}


def build(room="Kid's Room", room_id="kids_room", **over):
    base = f"{CORE}/v1/usb-stick/{BUILD_ID}/"
    b = {"id": BUILD_ID, "room": room, "room_id": room_id, "made": 1_760_000_000, "size": 793051136,
         "sha256": "cd" * 32, "debian": "13.7.0", "wifi": False, "spoken_code": True,
         "image_url": base + "domovoi-room-stick.img", "os_list_url": base + "os-list.json",
         "manifest_url": base + "domovoi-room-stick.rpi-imager-manifest"}
    b.update(over)
    return b


@pytest.fixture
def core(monkeypatch):
    answers = {
        "/v1/health": (200, {"identity": {"fingerprint": FP}}),
        "/v1/satellite-code/bundle.sha256": (200, {"sha256": SHA, "size": 812345}),
        "/v1/admin/satellite-windows": (200, WIN_RUNTIME),
        "/v1/admin/android-app": (200, {"release": None, "apks": []}),
        # The enrolment answer asks for the Linux satellite's own Python too
        # (batch 11's older-Linux card, beside this one).
        "/v1/admin/satellite-linux": (200, LINUX_RUNTIME),
        "/v1/admin/usb-stick": (200, STATUS),
    }
    posted: list = []
    deleted: list = []
    answers["build"] = (200, dict(STATUS, started=True, job=dict(IDLE, running=True, stage="download")))

    async def get_admin(path, headers=None, **kw):
        return answers[path]

    async def post_admin(path, body=None, headers=None, **kw):
        posted.append((path, body))
        return answers["build"]

    async def delete_admin(path, headers=None, **kw):
        deleted.append(path)
        return 200, STATUS

    async def rooms():
        return [{"room_id": "office"}]

    async def pairings():
        return {}

    monkeypatch.setattr(web_sats, "get_admin", get_admin)
    monkeypatch.setattr(web_sats, "post_admin", post_admin)
    monkeypatch.setattr(web_sats, "delete_admin", delete_admin)
    monkeypatch.setattr(web_sats, "_list_rooms", rooms)
    monkeypatch.setattr(web_sats, "_list_pairings", pairings)
    monkeypatch.setattr(web_sats.satellite_adoption, "advertised_domovoi_url", lambda: "ws://192.168.1.20:6370")
    answers["posted"], answers["deleted"] = posted, deleted
    return answers


# ─── the routes ───────────────────────────────────────────────────────────

async def test_prepare_hands_the_core_the_card_s_own_room_code(core):
    got = await web_sats.usb_stick_prepare(SpareDeviceEnrolRequest(room="Kid's Room"), Req())
    ((path, body),) = core["posted"]
    assert path == "/v1/admin/usb-stick/build"
    parsed = enrolment.parse(body["payload"])
    assert (parsed.core, parsed.fp, parsed.room) == (CORE, FP, "Kid's Room")
    assert got["started"] is True and got["room_id"] == "kids_room" and got["conflict"] is None
    enrol = await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Kid's Room"), Req())
    assert enrol["payload"] == body["payload"], "the same code the card shows"
    assert enrol["usb_stick"] == STATUS


async def test_prepare_says_a_taken_room_and_another_address(core):
    got = await web_sats.usb_stick_prepare(SpareDeviceEnrolRequest(room="Office", core="http://10.0.0.5:6370"), Req())
    assert "already exists" in got["conflict"]
    assert enrolment.parse(core["posted"][0][1]["payload"]).core == "http://10.0.0.5:6370"


async def test_a_refusal_from_the_core_comes_back_with_its_reason(core):
    core["build"] = (409, {"detail": {"reason": "internet_off", "message": "Internet access is off"}})
    resp = await web_sats.usb_stick_prepare(SpareDeviceEnrolRequest(room="Den"), Req())
    assert resp.status_code == 409
    assert json.loads(resp.body)["detail"]["reason"] == "internet_off"


async def test_a_room_the_code_can_not_carry_never_reaches_the_core(core):
    with pytest.raises(HTTPException) as e:
        await web_sats.usb_stick_prepare(SpareDeviceEnrolRequest(room="!!!"), Req())
    assert e.value.status_code == 422 and core["posted"] == []


async def test_status_and_remove_go_to_the_core_and_a_bad_id_does_not(core):
    assert await web_sats.usb_stick_status(Req()) == STATUS
    assert await web_sats.usb_stick_delete(BUILD_ID, Req()) == STATUS
    assert core["deleted"] == [f"/v1/admin/usb-stick/builds/{BUILD_ID}"]
    for bad in ("../../etc", "x" * 31, "x" * 32 + "/y"):
        with pytest.raises(HTTPException) as e:
            await web_sats.usb_stick_delete(bad, Req())
        assert e.value.status_code == 404
    assert len(core["deleted"]) == 1


def test_the_routes_are_an_admin_s():
    from domovoi.admin_auth import require_admin_mutation, require_admin_read

    routes = {(r.path, tuple(sorted(r.methods))): r for r in web_sats.router.routes}
    assert any(d.dependency is require_admin_read
               for d in routes[("/api/satellites/usb-stick", ("GET",))].dependencies)
    for key in (("/api/satellites/usb-stick", ("POST",)), ("/api/satellites/usb-stick/{build_id}", ("DELETE",))):
        assert any(d.dependency is require_admin_mutation for d in routes[key].dependencies), key


# ─── the card ─────────────────────────────────────────────────────────────

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const isBtn = (label) => (e) => e.type === 'button' && w.__deep(e).includes(label);
await h.click(isBtn('add a spare device')); await w.__settle(h);
h.type({ placeholder: "room name, e.g. Kid's Room" }, "Kid's Room");
await w.__settle(h);
await h.click(isBtn('make the code')); await w.__settle(h, 10);
const sec = () => h.find((e) => String((e.props && e.props.className) || '').includes('spare-usb-stick'));
const before = sec() ? w.__deep(sec()) : '';
const links = h.findAll((e) => e.type === 'a' && sec() && h.ancestors(e).includes(sec())).map((a) => a.props.href);
let after = '';
if (h.find(isBtn('Prepare the stick'))) { await h.click(isBtn('Prepare the stick')); await w.__settle(h, 10); after = w.__deep(sec()); }
if (h.find(isBtn('remove it from this server'))) { await h.click(isBtn('remove it from this server')); await w.__settle(h, 6); }
return { text: before, after, links, calls: h.calls };
"""


def _answer(stick):
    payload = enrolment.build(CORE, FP, "Kid's Room")
    return {
        "payload": payload, "room": "Kid's Room", "room_id": "kids_room", "core": CORE, "fingerprint": FP,
        "bundle_sha256": SHA, "bundle_size": 812345,
        "command": web_sats.install_command(CORE, SHA, payload),
        "command_windows": web_sats.install_command_windows(CORE, SHA, payload),
        "windows_runtime": WIN_RUNTIME, "usb_stick": stick, "conflict": None,
    }


def _scenario(stick, prepared=None):
    api = {
        "GET /api/satellites": [], "GET /api/satellites/approvals": [], "GET /api/satellites/pending": [],
        "GET /api/capabilities": {"host_kind": "linux"},
        "POST /api/satellites/enrolment": _answer(stick),
        "POST /api/satellites/usb-stick": prepared or dict(STATUS, started=True,
                                                           job=dict(IDLE, running=True, stage="download",
                                                                    room="Kid's Room", room_id="kids_room",
                                                                    bytes_done=396361728, bytes_todo=792723456)),
        "DELETE /api/satellites/usb-stick/" + BUILD_ID: STATUS,
    }
    return {"files": FILES, "component": "SatellitesPanel", "api": api, "script": _SCRIPT, "setup": _SETUP}


@pytest.fixture(scope="module")
def cards(tmp_path_factory) -> dict:
    return _drive({
        "fresh": _scenario(STATUS),
        "never": _scenario(dict(STATUS, refusal={"reason": "internet_off",
                                                  "message": "Internet access is off for this server (Settings → Internet), so it can't download Debian 13.7.0."})),
        "ready": _scenario(dict(STATUS, debian=dict(DEBIAN, cached=True),
                                builds=[build(), build(room="Office", room_id="office", id="Z" * 32)])),
        "failed": _scenario(dict(STATUS, job=dict(IDLE, ok=False, room_id="kids_room", room="Kid's Room",
                                                  message="Debian 13.7.0 could not be downloaded: HTTP 404"))),
    }, tmp_path_factory.mktemp("usb-stick-card"))


def test_the_card_says_the_floor_and_the_cable_before_anything(cards):
    text = cards["fresh"]["text"]
    assert "old computer (usb stick)" in text
    assert "erases that computer’s disk" in text and "naming the disk" in text
    assert "about 2009 or newer" in text and "refused before anything is erased" in text
    assert "network cable" in text and "Wi-Fi on the stick comes later" in text
    assert "2 GB or more" in text and "8 GB or more" in text
    assert "the first time, this server downloads Debian 13.7.0 (793 MB) from Debian" in text
    assert "30 to 80 W" in text and "laptop" in text, "the running cost of a tower"
    assert "boot-menu keys by maker" in text and "F12" in text and "Option" in text
    assert "Allow Microsoft 3rd Party UEFI CA" in text and "frozen" in text and "BIOS/UEFI update" in text


def test_prepare_is_a_button_that_asks_the_server_then_shows_the_download(cards):
    r = cards["fresh"]
    posts = [c for c in r["calls"] if c["method"] == "POST" and c["path"] == "/api/satellites/usb-stick"]
    assert [c["body"] for c in posts] == [{"room": "Kid's Room"}]
    assert "Downloading Debian 13.7.0 from Debian: 396 MB of 793 MB" in r["after"]


def test_internet_never_shows_the_reason_and_no_button(cards):
    r = cards["never"]
    assert "Internet access is off for this server" in r["text"]
    assert "Prepare the stick" not in r["text"]
    assert not any(c["path"] == "/api/satellites/usb-stick" and c["method"] == "POST" for c in r["calls"])


def test_a_failure_stays_until_the_next_try(cards):
    assert "Preparing the stick failed: Debian 13.7.0 could not be downloaded: HTTP 404" in cards["failed"]["text"]
    assert "Prepare the stick" in cards["failed"]["text"], "and the button is there to try again"


def test_a_ready_stick_offers_imager_first_then_the_file_and_the_image(cards):
    r = cards["ready"]
    b = build()
    assert r["links"][:3] == ["rpi-imager://?repo=" + urllib.parse.quote(b["os_list_url"], safe="-_.!~*'()"),
                              b["manifest_url"], b["image_url"]]
    assert "https://www.raspberrypi.com/software/" in r["links"] and "https://bztsrc.gitlab.io/usbimager/" in r["links"]
    text = r["text"]
    assert "The stick for Kid's Room is ready (793 MB" in text and "cdcdcdcdcdcdcdcd…" in text
    assert "nothing is sent to Raspberry Pi" in text
    for step in ("Write it to a USB stick", "boot-menu key", "type ERASE", "six-digit code"):
        assert step in text, step
    assert "Other sticks on this server" in text and "Office" in text
    assert "prepare it again" in text
    assert any(c["method"] == "DELETE" and c["path"] == f"/api/satellites/usb-stick/{BUILD_ID}" for c in r["calls"])


# ─── a room's health card: the firmware that lacks the 2023 certificate ────

_HEALTH = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
return { text: w.__deep(h.tree()) };
"""


NOT_INSTALLED = {"state": "not_installed", "detail": "no domovoi-provisioning.service on this device; on a "
                 "satellite Pi, sudo ~/satellite-venv/bin/python -m satellite.net_settings applies it once"}


@pytest.fixture(scope="module")
def health(tmp_path_factory) -> dict:
    def scenario(boot, net_step=None):
        sample = {"seq": 1, "rss_kb": 120000, "boot": boot} if boot is not None else {"seq": 1, "rss_kb": 120000}
        if net_step:
            sample.update(net_step=net_step, hostname="domovoi-kitchen")
        api = {"GET /api/satellites/kitchen/health": {"latest": {"received_at": "2026-10-16T10:00:00Z",
                                                                 "sample": sample}, "history": None}}
        return {"files": FILES, "component": "SatHealthCard", "props": {"room": "kitchen", "online": True},
                "api": api, "script": _HEALTH, "setup": _SETUP}

    return _drive({
        "needs": scenario({"firmware": "uefi", "secure_boot": True, "ca_2023": False, "loader_held": True,
                           "stick": True}),
        "fine": scenario({"firmware": "uefi", "secure_boot": True, "ca_2023": True, "loader_held": True,
                          "stick": True}),
        "bios": scenario({"firmware": "bios", "stick": True}),
        "nosb": scenario({"firmware": "uefi", "stick": True}),
        "pi": scenario(None, NOT_INSTALLED),
        "stick-net": scenario({"firmware": "bios", "stick": True}, NOT_INSTALLED),
    }, tmp_path_factory.mktemp("usb-stick-health"))


def test_a_room_whose_firmware_lacks_the_2023_certificate_is_warned_about(health):
    text = health["needs"]["text"]
    assert "firmware needs the 2023 certificate" in text
    assert "UEFI · Secure Boot on · no 2023 certificate · boot loader frozen" in text
    assert "doesn't have Microsoft's 2023 Secure Boot certificate" in text and "BIOS/UEFI update" in text
    assert "Once Debian ships a boot loader signed with the 2023 certificate only" in text, "said as what will happen"


def test_no_warning_when_the_firmware_has_it_or_there_is_no_secure_boot(health):
    for name in ("fine", "bios", "pi"):
        assert "firmware needs the 2023 certificate" not in health[name]["text"], name
    assert "firmware has the 2023 certificate" in health["fine"]["text"]
    assert "BIOS" in health["bios"]["text"]
    assert "boot loader frozen" not in health["pi"]["text"], "a Pi shows nothing of this"


def test_firmware_without_secure_boot_is_explained_not_unknown(health):
    text = health["nosb"]["text"]
    assert "no Secure Boot setting" in text and "Secure Boot unknown" not in text
    assert "doesn't say whether Secure Boot is on" in text
    assert "firmware needs the 2023 certificate" not in text


def test_a_stick_room_shows_no_pi_network_step_but_a_pi_still_does(health):
    stick = health["stick-net"]["text"]
    assert "not installed" not in stick and "sudo" not in stick and "domovoi-kitchen" in stick
    assert "not installed" in health["pi"]["text"] and "sudo" in health["pi"]["text"]
