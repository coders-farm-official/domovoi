"""Per-ABI versionCodes in android/release.json (domovoi/android_app.py),
2026-11-14, for reproducible builds that F-Droid can publish: each APK has
its own code, base*10 + its ABI's digit (armeabi-v7a 1, arm64-v8a 2), named
per entry, and the release's own ``version_code`` is base*10, so a phone on
an older app (which compares only that number) still sees the update. A
manifest from before, with no per-entry codes, loads unchanged.

No DB, no network: the signed fixtures of test_android_app.py.
"""

from __future__ import annotations

import json

import pytest

from domovoi import android_app as aa
from domovoi.tests.test_android_app import GOOD, RELEASE_CERT, TAG, VERSION, cache, load, manifest_doc  # noqa: F401

BASE = 26120101
TOP = BASE * 10


def with_codes(doc: dict, codes: dict[str, int] | None = None) -> dict:
    doc["release"]["version_code"] = TOP
    for e in doc["release"]["apks"]:
        e["version_code"] = (codes or {}).get(e["abi"], TOP + aa.ABI_DIGIT[e["abi"]])
    return doc


def test_the_digits_are_the_app_build_s():
    assert aa.ABI_DIGIT == {"armeabi-v7a": 1, "arm64-v8a": 2}
    # arm64 above armeabi: a 64-bit phone that can take either prefers it.
    assert aa.ABI_DIGIT["arm64-v8a"] > aa.ABI_DIGIT["armeabi-v7a"]


def test_a_manifest_with_per_abi_codes_loads_and_keeps_them(tmp_path):
    m = load(tmp_path, with_codes(manifest_doc(GOOD)))
    assert m["release"]["version_code"] == TOP
    assert {e["abi"]: e["version_code"] for e in m["release"]["apks"]} == {
        "arm64-v8a": TOP + 2, "armeabi-v7a": TOP + 1}


def test_a_manifest_from_before_loads_as_it_did(tmp_path):
    m = load(tmp_path, manifest_doc(GOOD))
    assert all("version_code" not in e for e in m["release"]["apks"])


@pytest.mark.parametrize("codes", [
    {"arm64-v8a": TOP + 1},          # armeabi's digit on arm64
    {"arm64-v8a": TOP},              # the bundle's
    {"armeabi-v7a": BASE * 10 + 11},  # another release's
    {"arm64-v8a": str(TOP + 2)},
    {"arm64-v8a": True},
    {"arm64-v8a": None},
])
def test_a_wrong_per_abi_code_is_refused(tmp_path, codes):
    with pytest.raises(aa.ManifestError):
        load(tmp_path, with_codes(manifest_doc(GOOD), codes))


def test_codes_are_all_or_nothing(tmp_path):
    doc = with_codes(manifest_doc(GOOD))
    del doc["release"]["apks"][1]["version_code"]
    with pytest.raises(aa.ManifestError, match="every APK"):
        load(tmp_path, doc)


def test_the_join_page_and_phones_see_each_apk_s_code(tmp_path, cache):  # noqa: F811
    m = load(tmp_path, with_codes(manifest_doc(GOOD)))
    pub = aa.public_status(m)
    # The top-level code an older app compares: base*10, above every older
    # release's 8-digit code and below each of this release's APKs.
    assert pub["release"]["version_code"] == TOP
    assert {a["abi"]: a["version_code"] for a in pub["apks"]} == {"arm64-v8a": TOP + 2, "armeabi-v7a": TOP + 1}
    # And nothing new for a manifest from before.
    old = aa.public_status(load(tmp_path, manifest_doc(GOOD)))
    assert all("version_code" not in a for a in old["apks"])


def test_an_older_app_still_sees_the_first_per_abi_release_as_newer():
    """1.2.0 (26111301, the last 8-digit release) against the first release
    with per-ABI codes: its top-level code is above, so the notice shows."""
    assert TOP > 26111301
    assert all(TOP < TOP + d <= TOP + 9 for d in aa.ABI_DIGIT.values())
    # Android's ceiling, up to the year 2099's last daily build.
    assert 99123199 * 10 + 9 < 2_100_000_000


def test_write_takes_the_per_abi_codes(tmp_path):
    signed = tmp_path / "signed"
    signed.mkdir()
    for abi, body in GOOD.items():
        (signed / aa.file_name(VERSION, abi)).write_bytes(body)
    out = tmp_path / "release.json"
    base = ["write", str(out), str(signed), "--version-name", VERSION, "--version-code", str(TOP),
            "--cert", RELEASE_CERT, "--tag", TAG]
    codes = ["--apk-version-code", f"arm64-v8a={TOP + 2}", "--apk-version-code", f"armeabi-v7a={TOP + 1}"]
    assert aa._main(base + codes) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [(e["abi"], e["version_code"]) for e in doc["release"]["apks"]] == [
        ("arm64-v8a", TOP + 2), ("armeabi-v7a", TOP + 1)]
    assert aa._main(["check", str(out), str(signed)]) == 0
    # One missing, one twice, an unknown ABI, a code the manifest refuses.
    out.unlink()
    for bad in (codes[:2], codes + codes[:2], ["--apk-version-code", "x86=1"] + codes,
                ["--apk-version-code", f"arm64-v8a={TOP + 1}", "--apk-version-code", f"armeabi-v7a={TOP + 1}"]):
        assert aa._main(base + bad) == 2, bad
        assert not out.exists()
