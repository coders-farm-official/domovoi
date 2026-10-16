"""The join page opened by the Android app's "update available" notice.

The app (android/app/src/main/java/com/domovoi/app/update/AppUpdate.kt)
opens ``/join?abi=<abi>&update=1``: it knows the phone's own processor
(Build.SUPPORTED_ABIS), so domovoi/join_page/join.js puts that APK first
when this server holds it, and words the page for a phone that already
has the app. Run through the same stand-in DOM as test_android_app.py
(join_page_harness.js).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi import android_app as aa

HARNESS = Path(__file__).with_name("join_page_harness.js")
ANDROID_UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0 Mobile Safari/537.36")
BASE = "http://192.168.1.20:6370/join"
PAYLOAD = ("domovoi-room:1?core=http%3A%2F%2F192.168.1.20%3A6370&fp=SHA256%3A4gq%2BpNW%2FlnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"
           "&room=Den")


def _status(ready=(True, True)):
    apks = [{"abi": abi, "file": aa.file_name("1.0.1", abi), "size": size, "ready": r,
             "path": aa.FILES_PATH + aa.file_name("1.0.1", abi)}
            for (abi, size), r in zip((("arm64-v8a", 80_000_000), ("armeabi-v7a", 48_000_000)), ready)]
    return {"release": {"version_name": "1.0.1", "version_code": 26110101, "cert_sha256": "ab" * 32},
            "apks": apks, "ready": all(ready)}


def _join(href, app, platform="Linux armv81") -> dict:
    node = shutil.which("node")
    assert node, "node is required to run the join page's script"
    scenario = json.dumps({"href": href, "ua": ANDROID_UA, "platform": platform, "app": app})
    proc = subprocess.run([node, str(HARNESS), str(aa.REPO), scenario],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_apps_32_bit_pick_goes_first_whatever_the_browser_says():
    page = _join(BASE + "?abi=armeabi-v7a&update=1", _status())
    assert page["dl-main"]["href"].endswith("armeabi-v7a.apk")
    assert page["dl-other"]["href"].endswith("arm64-v8a.apk"), "the other one stays offered"
    assert "the one the app chose for this phone" in page["dl-main-sub"]["text"]


def test_the_apps_64_bit_pick_beats_a_browser_that_looks_32_bit():
    page = _join(BASE + "?abi=arm64-v8a&update=1", _status(), platform="Linux armv7l")
    assert page["dl-main"]["href"].endswith("arm64-v8a.apk")
    assert page["dl-other"]["href"].endswith("armeabi-v7a.apk")


def test_a_pick_the_server_doesnt_hold_falls_back_to_what_it_has():
    page = _join(BASE + "?abi=armeabi-v7a&update=1", _status(ready=(True, False)))
    assert page["dl-main"]["href"].endswith("arm64-v8a.apk") and page["dl-other-line"]["hidden"]
    assert "the app chose" not in page["dl-main-sub"]["text"]


@pytest.mark.parametrize("abi", ["x86_64", "armeabi-v7a%3Cb%3E", ""])
def test_an_abi_a_release_doesnt_carry_is_ignored(abi):
    page = _join(BASE + "?abi=" + abi, _status(), platform="Linux armv7l")
    assert page["dl-main"]["href"].endswith("armeabi-v7a.apk"), "the browser's own hint decides"
    assert "the app chose" not in page["dl-main-sub"]["text"]


def test_an_update_is_worded_for_a_phone_that_has_the_app():
    page = _join(BASE + "?abi=arm64-v8a&update=1", _status())
    assert page["title"]["text"] == "Update the Domovoi app"
    assert page["install-title"]["text"] == "Install the update"
    assert "installs over the one you have" in page["lede"]["text"]
    assert page["step-connect"]["hidden"], "no 'pick this home's Domovoi' step for an update"
    assert not page["downloads"]["hidden"]


def test_without_update_the_household_page_is_unchanged():
    page = _join(BASE + "?abi=arm64-v8a", _status())
    # The stand-in DOM starts every element empty: join.html's own heading
    # ("Get the Domovoi app") is what shows when the script leaves it alone.
    assert page["title"]["text"] == "" and page["lede"]["text"] == ""
    assert not page["step-connect"]["hidden"]


def test_a_room_code_keeps_the_room_page_even_with_update():
    page = _join(BASE + "?update=1#" + PAYLOAD, _status())
    assert page["title"]["text"] == "Make this phone a Domovoi room"
    assert not page["step-room"]["hidden"]


def test_an_update_the_server_doesnt_hold_says_so_and_offers_nothing():
    page = _join(BASE + "?abi=arm64-v8a&update=1", _status(ready=(False, False)))
    assert page["title"]["text"] == "Update the Domovoi app"
    assert "Download the app to this server" in page["install-state"]["text"]
    assert page["downloads"]["hidden"]
