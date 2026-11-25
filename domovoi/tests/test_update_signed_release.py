"""The update notice's "signed release" (domovoi/update_notice.py; design
note UPDATE-NOTICES §2, §5).

The manifest check runs on the release-signature fixtures ssh-keygen made
for the Windows tray's tests (windows/tests/release-sig/, a throwaway
key); the download runs against an httpx MockTransport. No network, no DB.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from domovoi import egress, update_notice

REPO = Path(__file__).resolve().parents[2]
SIG = REPO / "windows" / "tests" / "release-sig"
TEST_KEY = (SIG / "test-key.pub").read_text(encoding="ascii")
OTHER_KEY = (SIG / "other-key.pub").read_text(encoding="ascii")
MANIFEST = (SIG / "manifest-1.0.1.json").read_bytes()
MANIFEST_SIG = (SIG / "manifest-1.0.1.json.sig").read_bytes()
TAG = "v1.0.1"
COMMIT = "c0ffee0123456789abcdef0123456789abcdef01"


def test_the_fixture_manifest_is_a_signed_release():
    assert update_notice.manifest_problem(MANIFEST, MANIFEST_SIG, TAG, COMMIT, TEST_KEY) is None


def test_the_pinned_release_key_does_not_take_the_test_key_s_signature():
    assert update_notice.manifest_problem(MANIFEST, MANIFEST_SIG, TAG, COMMIT) is not None


def test_another_key():
    assert "other than Domovoi's release key" in update_notice.manifest_problem(
        MANIFEST, MANIFEST_SIG, TAG, COMMIT, OTHER_KEY)


def test_the_wrong_namespace():
    wrong = (SIG / "manifest-1.0.1.json.wrong-namespace.sig").read_bytes()
    assert "signature is for something else" in update_notice.manifest_problem(
        MANIFEST, wrong, TAG, COMMIT, TEST_KEY)


def test_a_changed_manifest_fails_the_signature():
    changed = MANIFEST.replace(b"v1.0.1", b"v1.0.2")
    assert update_notice.manifest_problem(changed, MANIFEST_SIG, "v1.0.2", COMMIT, TEST_KEY) is not None


@pytest.mark.parametrize(("tag", "commit"), [
    ("v1.0.2", COMMIT),                     # another tag
    (TAG, "d" * 40),                        # another commit
])
def test_the_manifest_must_name_this_release(tag, commit):
    assert update_notice.manifest_problem(MANIFEST, MANIFEST_SIG, tag, commit, TEST_KEY) == (
        "the signed manifest names another release")


def test_the_wrong_kind(monkeypatch):
    monkeypatch.setattr(update_notice, "MANIFEST_KIND", "domovoi-mac-setup")
    assert update_notice.manifest_problem(MANIFEST, MANIFEST_SIG, TAG, COMMIT, TEST_KEY) == (
        "the signed manifest is for something else")
    assert json.loads(MANIFEST)["kind"] == "domovoi-windows-setup"


# ─── the download ────────────────────────────────────────────────────────


class _GitHub:
    """github.com answers 302 to release-assets.githubusercontent.com,
    which answers the files; everything asked is recorded."""

    def __init__(self, *, redirect_host="release-assets.githubusercontent.com", files=None, big=False):
        self.seen: list[str] = []
        self.redirect_host = redirect_host
        self.files = files if files is not None else {
            "DomovoiSetup.manifest.json": MANIFEST, "DomovoiSetup.manifest.json.sig": MANIFEST_SIG}
        self.big = big

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(f"{request.url.host}{request.url.path}")
        name = request.url.path.rsplit("/", 1)[-1]
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"location": f"https://{self.redirect_host}/x/{name}"})
        if self.big:
            return httpx.Response(200, content=b"x" * (64 * 1024 + 1))
        if name in self.files:
            return httpx.Response(200, content=self.files[name])
        return httpx.Response(404)


@pytest.fixture
def github(monkeypatch):
    def install(**kw):
        gh = _GitHub(**kw)
        monkeypatch.setattr(update_notice, "_TRANSPORT", httpx.MockTransport(gh))
        monkeypatch.setattr(update_notice, "release_key", TEST_KEY)
        return gh
    return install


def _verify():
    return asyncio.run(update_notice.verify_release(TAG, COMMIT))


def test_a_signed_release_through_github_s_redirect(github):
    gh = github()
    with egress.override_policy("always"):
        assert _verify() is True
    assert gh.seen == [
        "github.com/coders-farm-official/domovoi/releases/download/v1.0.1/DomovoiSetup.manifest.json",
        "release-assets.githubusercontent.com/x/DomovoiSetup.manifest.json",
        "github.com/coders-farm-official/domovoi/releases/download/v1.0.1/DomovoiSetup.manifest.json.sig",
        "release-assets.githubusercontent.com/x/DomovoiSetup.manifest.json.sig",
    ]


def test_a_redirect_off_github_is_refused(github):
    gh = github(redirect_host="evil.example")
    with egress.override_policy("always"):
        assert _verify() is None
    assert not any(s.startswith("evil.example") for s in gh.seen)


def test_a_manifest_over_64_kib_is_refused(github):
    github(big=True)
    with egress.override_policy("always"):
        assert _verify() is None


def test_a_missing_manifest_is_unknown_not_unsigned(github):
    github(files={})
    with egress.override_policy("always"):
        assert _verify() is None


def test_a_bad_signature_is_not_signed(github):
    github(files={"DomovoiSetup.manifest.json": MANIFEST,
                  "DomovoiSetup.manifest.json.sig": (SIG / "manifest-0.9.0.json.sig").read_bytes()})
    with egress.override_policy("always"):
        assert _verify() is False


def test_under_never_nothing_is_requested(github):
    gh = github()
    with egress.override_policy("never"):
        assert _verify() is None
    assert gh.seen == []


def test_a_verified_tag_is_not_downloaded_again(github, monkeypatch, tmp_path):
    """_evaluate asks for a tag's manifest once: the state keeps it,
    per tag and commit."""
    update_notice.reset_for_tests()
    saved = {}

    async def load():
        return saved.get("v")

    async def save(value):
        saved["v"] = json.loads(json.dumps(value))

    monkeypatch.setattr(update_notice, "_db_load", load)
    monkeypatch.setattr(update_notice, "_db_save", save)
    monkeypatch.setattr(update_notice, "stable_release", lambda: (TAG, COMMIT))
    monkeypatch.setattr(update_notice, "head_sha", lambda: "e" * 40)
    monkeypatch.setattr(update_notice, "read_highlights", lambda rev, tag: [])
    monkeypatch.setattr(update_notice, "relation", lambda *a: ("ahead", 1))
    monkeypatch.setattr(update_notice, "_bad_sha", lambda: None)
    gh = github()
    with egress.override_policy("always"):
        assert asyncio.run(update_notice._evaluate("manual")) is True
        asked = len(gh.seen)
        assert asked == 4
        assert asyncio.run(update_notice._evaluate("manual")) is True
    assert len(gh.seen) == asked
    assert saved["v"]["releases"][TAG] == {"commit": COMMIT, "signed": True, "highlights": []}
    update_notice.reset_for_tests()
