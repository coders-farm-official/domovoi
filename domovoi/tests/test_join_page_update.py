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


def _join(href, app, platform="Linux armv81", clicks=(), clipboard=False) -> dict:
    node = shutil.which("node")
    assert node, "node is required to run the join page's script"
    scenario = json.dumps({"href": href, "ua": ANDROID_UA, "platform": platform, "app": app,
                           "clicks": list(clicks), "clipboard": clipboard})
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


# ─── trouble downloading: try again, what to tap, copy link ───────────────

@pytest.mark.parametrize("url", [BASE, BASE + "?abi=arm64-v8a&update=1"], ids=["household", "update"])
def test_the_retry_block_waits_until_a_download_is_tapped(url):
    page = _join(url, _status())
    assert page["dl-trouble"]["hidden"]
    page = _join(url, _status(), clicks=["dl-main"])
    assert not page["dl-trouble"]["hidden"]
    html = (aa.REPO / "domovoi/join_page/join.html").read_text(encoding="utf-8")
    assert "Didn't start, or did your phone block it?" in html and "Try again" in html
    assert page["dl-link"]["value"].startswith("http://192.168.1.20:6370/v1/android-app/files/")


@pytest.mark.parametrize("url", [BASE, BASE + "?abi=armeabi-v7a&update=1"], ids=["household", "update"])
def test_try_again_uses_a_fresh_url_every_time_and_keeps_the_download_attribute(url):
    page = _join(url, _status(), clicks=["dl-main", "dl-retry", "dl-retry", "dl-retry"])
    main = page["dl-main"]
    assert main["href"].endswith(".apk?try=3"), "a new url each time, never stacking ?try="
    assert main["href"].count("?") == 1
    assert main["download"] == main["href"].split("/")[-1].split("?")[0]
    assert [h.rsplit("?try=", 1)[-1] for h in main["fetched"]] == ["1", "2", "3"]
    assert page["dl-link"]["value"].endswith("?try=3")
    assert not page["dl-trouble"]["hidden"], "usable any number of times"


def test_try_again_retries_the_other_download_if_that_is_the_one_tapped():
    page = _join(BASE + "?abi=arm64-v8a", _status(), clicks=["dl-other", "dl-retry"])
    assert page["dl-other"]["href"].endswith("armeabi-v7a.apk?try=1")
    assert page["dl-main"]["fetched"] == [] and page["dl-main"]["href"].endswith("arm64-v8a.apk")


def test_try_again_before_any_tap_retries_the_main_download():
    page = _join(BASE, _status(), clicks=["dl-retry"])
    assert page["dl-main"]["href"].endswith("?try=1") and len(page["dl-main"]["fetched"]) == 1


def test_the_help_covers_every_common_block_in_plain_words():
    html = (aa.REPO / "domovoi/join_page/join.html").read_text(encoding="utf-8")
    for needle in ("Download anyway", "<b>Keep</b>", "Downloads", "Download multiple files", "<b>Settings</b>",
                   "Allow from this source", "More details", "Install anyway", "Another browser works too"):
        assert needle in html, needle
    assert "<details" in html, "collapsible"


def test_copy_link_uses_the_clipboard_when_there_is_one_and_says_how_when_not():
    page = _join(BASE, _status(), clicks=["dl-main", "dl-copy"], clipboard=True)
    assert page["copied"] == [page["dl-link"]["value"]] and "Link copied" in page["dl-copy-note"]["text"]
    page = _join(BASE, _status(), clicks=["dl-main", "dl-copy"])
    assert page["copied"] == [] and "Press and hold" in page["dl-copy-note"]["text"]


def test_the_room_code_and_the_32_bit_pick_are_untouched_by_the_retry_block():
    page = _join(BASE + "?abi=armeabi-v7a#" + PAYLOAD, _status(), clicks=["dl-main", "dl-retry"])
    assert page["dl-main"]["href"].endswith("armeabi-v7a.apk?try=1")
    assert page["title"]["text"] == "Make this phone a Domovoi room" and not page["step-room"]["hidden"]
    assert "#" not in page["dl-link"]["value"], "the room code never rides in a download link"


def test_the_download_route_ignores_an_unknown_query():
    from domovoi import main
    import inspect

    sig = inspect.signature(main.android_app_file)
    assert list(sig.parameters) == ["name"], "no query parameter to reject ?try=<n>"


def test_the_script_stays_plain_es5():
    import re

    js = (aa.REPO / "domovoi/join_page/join.js").read_text(encoding="utf-8")
    code = "\n".join(l for l in js.splitlines() if not l.lstrip().startswith(("/*", "*", "//")))
    assert not re.search(r"=>|\blet\b|\bconst\b|`|\.includes\(|\.startsWith\(|\bPromise\b|\bfetch\(", code)
