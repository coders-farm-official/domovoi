"""Updating an installed house from a newer offline kit (domovoi/kit_update.py,
release 1.5.0 P5; docs/OFFLINE_KIT.md "Updating from a kit").

No DB, no network, no Docker: the kit is written with the format's own
writer (tiny parts, the throwaway release key of windows/tests/release-sig),
the server's facts (its release, machine, Docker, dpkg, venv) are seams,
the checkout move runs against a throwaway git repo, and the update unit's
side (scripts/linux/apply-update.sh) is checked a function at a time in
bash with stubs. The full harness cases are in
scripts/linux/tests/test-apply-update.sh (offline_kit_*).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from domovoi import egress, kit_update
from domovoi.offline_kit import Component, Source, write_kit
from domovoi.offline_kit import format as fmt

REPO = Path(__file__).resolve().parents[2]
SIG = REPO / "windows" / "tests" / "release-sig"
TEST_KEY = (SIG / "test-key.pub").read_text(encoding="ascii")
OTHER_KEY = (SIG / "other-key.pub").read_text(encoding="ascii")
VERSION = "1.0.1"  # the signed fixture manifest's
COMMIT = "c0ffee0123456789abcdef0123456789abcdef01"
PART = 4000
WHEEL = b"PK wheel " * 600  # 5400 bytes: split in two parts
APK = b"apk!" * 100
IMAGE = b"image tar" * 50
DIGEST = "a" * 64
PC = ("pc-x86_64", "x86_64", "amd64")
SCRIPT = REPO / "scripts" / "linux" / "apply-update.sh"


@pytest.fixture(autouse=True)
def _server(monkeypatch, tmp_path):
    """A PC server on 1.0.0 that can update itself, with an empty venv,
    no Docker images and every Ubuntu package it needs."""
    monkeypatch.setattr(fmt, "MIN_PART_SIZE", 1)
    monkeypatch.setattr(kit_update.host, "kit_root", lambda: tmp_path / "home" / ".domovoi" / "offline_kit")
    monkeypatch.setattr(kit_update, "release_key", TEST_KEY)
    monkeypatch.setattr(kit_update, "running_version", lambda: "1.0.0")
    monkeypatch.setattr(kit_update, "host_target", lambda: PC)
    monkeypatch.setattr(kit_update, "update_unit_ready", lambda: None)
    monkeypatch.setattr(kit_update, "_channel_problem", lambda: None)
    monkeypatch.setattr(kit_update, "_installed", lambda: {("requests", "2.0.0")})
    monkeypatch.setattr(kit_update, "_image_present", lambda ref, digest: False)
    monkeypatch.setattr(kit_update, "missing_packages", lambda: [])
    monkeypatch.setattr(kit_update, "_phone_app_wanted", lambda doc: True)
    monkeypatch.setattr(kit_update, "_venv_bytes", lambda: 0)
    monkeypatch.setattr(kit_update, "_free_bytes", lambda p: 10**12)
    monkeypatch.setattr("domovoi.git_version.read_last_update", lambda: None)


def _make_kit(tmp_path: Path, **kw) -> Path:
    root = tmp_path / "stick" / "DOMOVOIKIT"
    comps = [
        Component("release", "Domovoi Setup", VERSION, "MIT", [
            Source("release/DomovoiSetup.exe", SIG / "setup.bin"),
            Source("release/DomovoiSetup.manifest.json", SIG / "manifest-1.0.1.json"),
            Source("release/DomovoiSetup.manifest.json.sig", SIG / "manifest-1.0.1.json.sig"),
        ], pinned_by="the release's signed manifest"),
        Component("source", "Domovoi source", VERSION, "MIT",
                  [Source("source/domovoi.bundle", b"# v2 git bundle\n" + b"x" * 100)]),
        Component("python-wheels", "Python packages for x86_64", VERSION, "LicenseRef-mixed-see-sbom", [
            Source("python/cp314-x86_64/httpx-0.28.1-py3-none-any.whl", WHEEL),
            Source("python/cp314-x86_64/requests-2.0.0-py3-none-any.whl", b"already installed"),
            Source("python/cp314-aarch64/httpx-0.28.1-py3-none-any.whl", b"not this machine"),
        ], pinned_by="requirements-linux-py314.lock (hashes)"),
        Component("container-image", f"postgres:16@sha256:{DIGEST}", DIGEST, "PostgreSQL",
                  [Source("images/postgres_16.tar", IMAGE)], pinned_by="domovoi/docker-compose.yml"),
        Component("phone-app", "Domovoi for Android", "1.0", "MIT",
                  [Source("phone/domovoi-1.0-arm64-v8a.apk", APK)], pinned_by="android/release.json"),
        Component("apt", "Ubuntu packages", "resolute", "LicenseRef-distro-archive",
                  [Source("apt/amd64/ubuntu/dists/resolute/InRelease", b"signed index")]),
    ]
    write_kit(root, version=kw.pop("version", VERSION), commit=COMMIT, components=comps,
              targets=kw.pop("targets", ["pc-x86_64"]), android_version_code=261120010,
              created_at="2026-11-22T10:15:00Z", part_size=PART)
    return root


def _send_all(root: Path, view: dict, chunk: int = 1000) -> None:
    s = view["session"]
    for f in s["files"]:
        data = (root / f["path"]).read_bytes()
        at = f["received"]
        while at < f["size"]:
            out = kit_update.receive(s["id"], f["path"], at, data[at:at + chunk])
            at = out["received"]


def _begin(root: Path) -> dict:
    return kit_update.begin((root / "kit.json").read_bytes())


# ─── What the kit must send ──────────────────────────────────────────────

def test_the_server_asks_only_for_what_it_lacks(tmp_path):
    root = _make_kit(tmp_path)
    s = _begin(root)["session"]
    paths = [f["path"] for f in s["files"]]
    # The release's signed manifest first, then the source, then the rest.
    assert paths[:3] == ["release/DomovoiSetup.manifest.json", "release/DomovoiSetup.manifest.json.sig",
                         "source/domovoi.bundle"]
    assert "python/cp314-x86_64/httpx-0.28.1-py3-none-any.whl.part01" in paths  # split: its parts
    assert not any("requests-2.0.0" in p for p in paths), "an installed package isn't sent"
    assert not any("aarch64" in p for p in paths), "another machine's packages aren't sent"
    assert "release/DomovoiSetup.exe" not in paths, "the installer isn't needed for an update"
    assert "images/postgres_16.tar" in paths and "phone/domovoi-1.0-arm64-v8a.apk" in paths
    assert not any(p.startswith("apt/") for p in paths), "Ubuntu's packages only when one is missing"
    assert s["state"] == "uploading" and s["bytes_received"] == 0


def test_present_images_and_missing_packages(tmp_path, monkeypatch):
    monkeypatch.setattr(kit_update, "_image_present", lambda ref, digest: digest == DIGEST)
    monkeypatch.setattr(kit_update, "missing_packages", lambda: ["mtools"])
    paths = [f["path"] for f in _begin(_make_kit(tmp_path))["session"]["files"]]
    assert "images/postgres_16.tar" not in paths
    assert "apt/amd64/ubuntu/dists/resolute/InRelease" in paths


def test_added_packages_match_apply_update():
    text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^ADDED_PACKAGES=\(([^)]*)\)", text, re.M)
    assert m and tuple(m.group(1).split()) == kit_update.ADDED_PACKAGES


# ─── The upload: chunks, resume, pins ────────────────────────────────────

def test_chunked_upload_resumes_and_applies_pins(tmp_path):
    root = _make_kit(tmp_path)
    view = _begin(root)
    sid = view["session"]["id"]
    bundle = (root / "source/domovoi.bundle").read_bytes()
    out = kit_update.receive(sid, "source/domovoi.bundle", 0, bundle[:50])
    assert out == {"path": "source/domovoi.bundle", "received": 50, "complete": False}
    # A piece at the wrong offset says what the server holds.
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.receive(sid, "source/domovoi.bundle", 0, bundle[:50])
    assert e.value.reason == "offset" and e.value.extra["received"] == 50 and e.value.status == 409
    # A reload: kit.json again picks the session up where it stopped.
    again = _begin(root)["session"]
    assert again["id"] == sid
    assert next(f for f in again["files"] if f["path"] == "source/domovoi.bundle")["received"] == 50
    _send_all(root, {"session": again})
    s = kit_update.status()["session"]
    assert s["files_left"] == 0 and s["bytes_received"] == s["bytes_total"]
    assert s["state"] == "uploading"


def test_a_file_that_isnt_the_kits_is_thrown_away(tmp_path):
    root = _make_kit(tmp_path)
    sid = _begin(root)["session"]["id"]
    size = (root / "source/domovoi.bundle").stat().st_size
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.receive(sid, "source/domovoi.bundle", 0, b"y" * size)
    assert e.value.reason == "pin" and e.value.status == 422
    assert "SHA-256" in e.value.message
    assert not (kit_update.files_dir() / "source/domovoi.bundle").exists()
    assert not (kit_update.files_dir() / "source/domovoi.bundle.partial").exists()
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.receive(sid, "source/domovoi.bundle", 0, b"y" * (size + 1))
    assert e.value.reason == "pin"
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.receive(sid, "release/DomovoiSetup.exe", 0, b"x")
    assert e.value.reason == "not_needed"
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.receive("0" * 32, "source/domovoi.bundle", 0, b"x")
    assert e.value.status == 404


def test_a_kit_signed_by_another_key_stops_at_the_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(kit_update, "release_key", OTHER_KEY)
    root = _make_kit(tmp_path)
    view = _begin(root)
    sid = view["session"]["id"]
    m = "release/DomovoiSetup.manifest.json"
    kit_update.receive(sid, m, 0, (root / m).read_bytes())
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.receive(sid, f"{m}.sig", 0, (root / f"{m}.sig").read_bytes())
    assert e.value.reason == "signature" and "not a Domovoi release" in e.value.message
    assert not kit_update.files_dir().exists(), "what was sent is removed"
    assert kit_update.status()["session"]["state"] == "refused"


# ─── Refusals ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("running,words", [
    ("1.0.1", "This kit is 1.0.1; this server already runs 1.0.1."),
    ("1.2.0", "which is newer. Kits never downgrade."),
])
def test_an_older_or_the_same_release_is_refused(tmp_path, monkeypatch, running, words):
    monkeypatch.setattr(kit_update, "running_version", lambda: running)
    with pytest.raises(kit_update.Refusal) as e:
        _begin(_make_kit(tmp_path))
    assert e.value.reason == "not_newer" and words in e.value.message
    assert not kit_update.files_dir().exists()


def test_a_release_candidate_is_older_than_its_release():
    assert kit_update._version_key("1.5.0-rc1") < kit_update._version_key("1.5.0")
    assert kit_update._version_key("1.4.9") < kit_update._version_key("1.5.0-rc1")
    assert kit_update._version_key("1.10.0") > kit_update._version_key("1.9.9")


def test_the_wrong_machine_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(kit_update, "host_target", lambda: ("mac-arm64", "aarch64", "arm64"))
    with pytest.raises(kit_update.Refusal) as e:
        _begin(_make_kit(tmp_path))
    assert e.value.reason == "wrong_arch"
    assert "This kit is for a Windows or Linux PC; this server is a Mac with an Apple chip" in e.value.message
    assert "“A Mac with an Apple chip” ticked" in e.value.message


def test_not_enough_disk_is_refused_before_the_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(kit_update, "_free_bytes", lambda p: 10_000)
    with pytest.raises(kit_update.Refusal) as e:
        _begin(_make_kit(tmp_path))
    assert e.value.reason == "disk" and "free on the server. It has" in e.value.message
    assert e.value.extra["needed"] > e.value.extra["free"]


def test_the_venv_copy_counts_when_packages_change(tmp_path, monkeypatch):
    monkeypatch.setattr(kit_update, "_venv_bytes", lambda: 5 * 10**9)
    s = _begin(_make_kit(tmp_path))["session"]
    need = [dict(n, why=w) for n, w in zip(s["files"], ["python"] * len(s["files"]))]
    assert kit_update._disk_needed(need) >= 5 * 10**9


def test_a_damaged_kit_json_and_other_refusals(tmp_path, monkeypatch):
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.begin(b"{not json")
    assert e.value.reason == "kit_damaged"
    doc = json.loads((_make_kit(tmp_path) / "kit.json").read_text(encoding="utf-8"))
    doc["kit_format"] = 99
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.begin(json.dumps(doc).encode())
    assert e.value.reason == "kit_newer_format"
    monkeypatch.setattr(kit_update, "update_unit_ready", lambda: "no update service")
    with pytest.raises(kit_update.Refusal) as e:
        _begin(tmp_path / "stick" / "DOMOVOIKIT")
    assert e.value.reason == "cannot_update" and e.value.message == "no update service"


# ─── Apply, the phone app, clean-up ──────────────────────────────────────

def _ready(tmp_path) -> tuple[Path, str]:
    root = _make_kit(tmp_path)
    view = _begin(root)
    _send_all(root, view)
    return root, view["session"]["id"]


def test_apply_joins_parts_moves_the_checkout_and_leaves_the_request(tmp_path, monkeypatch):
    _root, sid = _ready(tmp_path)
    moved = {}

    def fake_move(commit, tag, bundle):
        moved.update(commit=commit, tag=tag, bundle=bundle.read_bytes()[:6])
        return {"prev_sha": "b" * 40}

    monkeypatch.setattr(kit_update, "move_checkout", fake_move)
    view = kit_update.prepare_apply(sid)
    assert moved == {"commit": COMMIT, "tag": "v1.0.1", "bundle": b"# v2 g"}
    assert view["session"]["state"] == "applying"
    wheel = kit_update.files_dir() / "python/cp314-x86_64/httpx-0.28.1-py3-none-any.whl"
    assert wheel.read_bytes() == WHEEL, "split files are joined for the update unit"
    assert not list(wheel.parent.glob("*.part0*"))
    s = kit_update.status()["session"]
    assert s["files_left"] == 0, "a joined file's parts count as received"
    assert (kit_update.root() / "request").read_bytes() == f"commit={COMMIT}\n".encode()
    # Under way: no second apply, no cancel, no new kit.
    for call in (lambda: kit_update.prepare_apply(sid), kit_update.cancel,
                 lambda: _begin(tmp_path / "stick" / "DOMOVOIKIT")):
        with pytest.raises(kit_update.Refusal) as e:
            call()
        assert e.value.reason == "busy"


def test_apply_waits_for_every_file(tmp_path):
    root = _make_kit(tmp_path)
    sid = _begin(root)["session"]["id"]
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.prepare_apply(sid)
    assert e.value.reason == "not_complete"


def test_after_a_good_update_the_apps_go_in_and_the_copy_goes(tmp_path, monkeypatch):
    _root, sid = _ready(tmp_path)
    monkeypatch.setattr(kit_update, "move_checkout", lambda *a: {"prev_sha": None})
    kit_update.prepare_apply(sid)
    from domovoi import android_app

    accepted = []
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    monkeypatch.setattr(android_app, "load_manifest", lambda: {"release": {"version_code": 2}})
    monkeypatch.setattr(android_app, "upload_target", lambda m: bucket / ".upload.part")

    def accept(m, src):
        accepted.append(src.read_bytes())
        src.unlink()
        return {"file": "x.apk"}

    monkeypatch.setattr(android_app, "accept_upload", accept)
    # Still running: nothing yet.
    monkeypatch.setattr("domovoi.git_version.read_last_update", lambda: {"status": "running", "to_sha": COMMIT})
    assert kit_update.status()["session"]["state"] == "applying"
    monkeypatch.setattr("domovoi.git_version.read_last_update", lambda: {"status": "ok", "to_sha": COMMIT})
    s = kit_update.status()["session"]
    assert s["state"] == "done" and s["apps"] == {"kept": 1, "refused": []}
    assert accepted == [APK], "the APK went through accept_upload"
    assert not kit_update.files_dir().exists() and not (kit_update.root() / "request").exists()


def test_a_failed_update_keeps_the_files_and_says_why(tmp_path, monkeypatch):
    _root, sid = _ready(tmp_path)
    monkeypatch.setattr(kit_update, "move_checkout", lambda *a: {"prev_sha": None})
    kit_update.prepare_apply(sid)
    monkeypatch.setattr("domovoi.git_version.read_last_update",
                        lambda: {"status": "rolled_back", "to_sha": COMMIT, "error": "health failed"})
    s = kit_update.status()["session"]
    assert s["state"] == "failed" and "health failed" in s["error"]
    assert (kit_update.files_dir() / "source/domovoi.bundle").is_file()
    assert not (kit_update.root() / "request").exists()
    # Another try with the same stick: nothing is sent again.
    again = _begin(tmp_path / "stick" / "DOMOVOIKIT")["session"]
    assert again["id"] == sid and again["files_left"] == 0
    kit_update.cancel()
    assert not kit_update.root().exists(), "cancel removes everything"


def test_cancel_while_uploading_removes_the_copy(tmp_path):
    root = _make_kit(tmp_path)
    sid = _begin(root)["session"]["id"]
    kit_update.receive(sid, "source/domovoi.bundle", 0, b"x" * 10)
    assert kit_update.cancel()["session"] is None
    assert not kit_update.root().exists()


def test_the_whole_update_runs_under_never(tmp_path, monkeypatch):
    """Nothing in the update from a kit asks for the internet."""
    def no(*a, **k):
        raise AssertionError("the update from a kit reached for the internet")

    monkeypatch.setattr(egress, "require_internet", no)
    monkeypatch.setattr(egress, "async_client", no)
    monkeypatch.setattr(egress, "sync_client", no)
    monkeypatch.setattr(kit_update, "move_checkout", lambda *a: {"prev_sha": None})
    with egress.override_policy("never"):
        _root, sid = _ready(tmp_path)
        assert kit_update.prepare_apply(sid)["session"]["state"] == "applying"


# ─── The checkout move, against a real repository ────────────────────────

def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "alex", "GIT_AUTHOR_EMAIL": "alex@example.com",
           "GIT_COMMITTER_NAME": "alex", "GIT_COMMITTER_EMAIL": "alex@example.com"}
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args], cwd=cwd,
                          env=env, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A checkout on stable at A; upstream's B (tag v1.0.1) and C (not on
    A) in a bundle."""
    up = tmp_path / "upstream"
    up.mkdir()
    _git(up, "init", "-q", "-b", "stable")
    (up / "f").write_text("a")
    _git(up, "add", "f")
    _git(up, "commit", "-q", "-m", "A")
    a = _git(up, "rev-parse", "HEAD")
    co = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(up), str(co))
    (up / "f").write_text("b")
    _git(up, "commit", "-q", "-am", "B")
    b = _git(up, "rev-parse", "HEAD")
    _git(up, "tag", "v1.0.1")
    _git(up, "checkout", "-q", "--orphan", "other")
    (up / "f").write_text("c")
    _git(up, "add", "f")
    _git(up, "commit", "-q", "-m", "C")
    _git(up, "tag", "v9.0.0")
    c = _git(up, "rev-parse", "HEAD")
    bundle = tmp_path / "domovoi.bundle"
    _git(up, "bundle", "create", str(bundle), "--all")
    from domovoi.config import settings

    monkeypatch.setattr(settings, "repo_dir", str(co))
    monkeypatch.setattr(settings, "update_state_dir", str(tmp_path / "state"))
    monkeypatch.setattr(settings, "update_result_file", str(tmp_path / "state" / "last-result.json"))
    return {"co": co, "a": a, "b": b, "c": c, "bundle": bundle}


def test_move_checkout_fast_forwards_to_the_kits_release(checkout):
    out = kit_update.move_checkout(checkout["b"], "v1.0.1", checkout["bundle"])
    co = checkout["co"]
    assert _git(co, "rev-parse", "HEAD") == checkout["b"]
    assert _git(co, "rev-parse", "refs/remotes/origin/stable") == checkout["b"], \
        "origin/stable moves so the update unit's branch rule holds"
    assert out["prev_sha"] == checkout["a"]
    assert (Path(checkout["co"]).parent / "state" / "prev_sha").read_text().strip() == checkout["a"]
    assert subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/domovoi/offline-kit"], cwd=co,
                          capture_output=True).returncode != 0, "the private ref goes"


def test_move_checkout_refusals(checkout):
    co = checkout["co"]
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.move_checkout(checkout["a"], "v1.0.1", checkout["bundle"])
    assert e.value.reason == "bundle", "the tag must be the signed manifest's commit"
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.move_checkout(checkout["c"], "v9.0.0", checkout["bundle"])
    assert e.value.reason == "not_newer", "a release not built on what runs is refused"
    with pytest.raises(kit_update.Refusal) as e:
        kit_update.move_checkout(checkout["b"], "v2.0.0", checkout["bundle"])
    assert e.value.reason == "bundle"
    assert _git(co, "rev-parse", "HEAD") == checkout["a"], "nothing moved"


# ─── Routes: admin only ──────────────────────────────────────────────────

def _deps(app, path: str, method: str) -> set:
    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return {d.call for d in r.dependant.dependencies}
    raise AssertionError(f"no route {method} {path}")


def test_routes_are_admin_tier():
    from domovoi.admin_auth import require_admin_mutation, require_admin_read
    from domovoi.main import app as core
    from web.backend.main import app as web

    for app, base in ((core, "/v1/admin/offline-kit/update"), (web, "/api/offline-kit/update")):
        assert require_admin_read in _deps(app, base, "GET")
        assert require_admin_mutation in _deps(app, base, "POST")
        assert require_admin_mutation in _deps(app, base, "DELETE")
        assert require_admin_mutation in _deps(app, base + "/{sid}/file", "PUT")
        assert require_admin_mutation in _deps(app, base + "/{sid}/apply", "POST")


def test_the_web_update_routes_come_before_a_kits_id():
    from web.backend.main import app as web

    paths = [(r.path, sorted(getattr(r, "methods", []))) for r in web.routes
             if getattr(r, "path", "").startswith("/api/offline-kit")]
    i_update = paths.index(("/api/offline-kit/update", ["DELETE"]))
    i_build = paths.index(("/api/offline-kit/{build_id}", ["DELETE"]))
    assert i_update < i_build


def test_body_limits_carry_a_chunk_and_kit_json():
    from domovoi.transport_guard import body_limit_for
    from web.backend.middleware import body_limit_for as web_limit

    for p in ("/v1/admin/offline-kit/update/abc/file", "/api/offline-kit/update"):
        assert body_limit_for(p) >= fmt.MAX_KIT_JSON
    assert web_limit("/api/offline-kit/update/abc/file") >= kit_update.CHUNK_MAX


# ─── apply-update.sh's side, a function at a time ────────────────────────

def _bash() -> str | None:
    from domovoi.tests.test_apply_update_script import BASH

    return BASH


def _functions(*names: str) -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    out = []
    for n in names:
        m = (re.search(rf"^{n}\(\) \{{ .*\}}\n", text, re.M)
             or re.search(rf"^{n}\(\) \{{\n.*?^\}}\n", text, re.M | re.S))
        assert m, n
        out.append(m.group(0))
    return "\n".join(out)


def _run_bash(body: str, cwd: Path) -> subprocess.CompletedProcess:
    bash = _bash()
    if bash is None:
        pytest.skip("no usable bash (Git Bash on Windows)")
    return subprocess.run([bash, "-c", body], cwd=cwd, capture_output=True, text=True, timeout=60)


def test_apply_update_takes_the_cores_upload_only_for_head(tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    head = "d" * 40
    prelude = ("set -euo pipefail\nas_user() { \"$@\"; }\nSERVICE_USER=alex\nOFFLINE_AS_USER=0\n"
               f"OFFLINE_KIT_DIR={kit.as_posix()}\nHEAD_SHA={head}\n"
               + _functions("resolve_offline_source"))
    show = '\nOFFLINE_SOURCE=${DOMOVOI_OFFLINE_SOURCE:-}\nresolve_offline_source\necho "[$OFFLINE_SOURCE] $OFFLINE_AS_USER"\n'
    r = _run_bash(prelude + show, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[] 0", "no request: no offline source"
    (kit / "request").write_bytes(f"commit={'e' * 40}\n".encode())
    assert _run_bash(prelude + show, tmp_path).stdout.strip() == "[] 0", "another commit's request"
    (kit / "request").write_bytes(f"commit={head}\n".encode())
    assert _run_bash(prelude + show, tmp_path).stdout.strip() == f"[{kit.as_posix()}/files] 1"
    r = _run_bash("export DOMOVOI_OFFLINE_SOURCE=/media/alex/DOMOVOIKIT\n" + prelude + show, tmp_path)
    assert r.stdout.strip() == "[/media/alex/DOMOVOIKIT] 0", "an administrator's folder wins, read as root"


def test_kit_archive_name_matches_the_builder():
    from domovoi.kit_builder import images

    r = _run_bash(_functions("kit_archive_name") + "\nkit_archive_name domovoi-mpd:latest; echo\n"
                  "kit_archive_name registry.local/x/mpd:2.1; echo\nkit_archive_name mpd; echo\n", REPO)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == [images.archive_name("domovoi-mpd", "latest"),
                                images.archive_name("registry.local/x/mpd", "2.1"),
                                images.archive_name("mpd", "latest")]


def test_compose_pins_are_the_digests_without_the_search_helper(tmp_path):
    compose = (f"services:\n  postgres:\n    image: postgres:16.15-trixie@sha256:{'a' * 64}\n"
               f"  letta:\n    image: \"letta/letta:0.6@sha256:{'b' * 64}\"\n"
               f"  searxng:\n    image: searxng/searxng:2025@sha256:{'c' * 64}\n"
               "  plain:\n    image: busybox:latest\n")
    (tmp_path / "compose.yml").write_text(compose, encoding="utf-8")
    body = (f"git_as() {{ cat {(tmp_path / 'compose.yml').as_posix()}; }}\nHEAD_SHA=x\nCOMPOSE_PATH=y\n"
            + _functions("compose_pins") + "\ncompose_pins\n")
    r = _run_bash(body, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == [f"postgres@sha256:{'a' * 64}", f"letta/letta@sha256:{'b' * 64}"]


def test_offline_stage_copies_files_and_refuses_a_link(tmp_path):
    src = tmp_path / "files"
    (src / "python" / "cp314-x86_64").mkdir(parents=True)
    (src / "python" / "cp314-x86_64" / "a-1-py3-none-any.whl").write_bytes(b"w")
    (src / "images").mkdir()
    (src / "images" / "domovoi-mpd_latest.tar").write_bytes(b"i")
    upd = tmp_path / "upd"
    upd.mkdir()
    body = ("set -euo pipefail\nuname() { echo x86_64; }\ndpkg() { echo amd64; }\n"
            "as_user() { \"$@\"; }\n"
            f"OFFLINE_SOURCE={src.as_posix()}\nOFFLINE_AS_USER=1\nUPDATE_DIR={upd.as_posix()}\n"
            "MPD_TAG=domovoi-mpd:latest\nOFFLINE_WHEELS= OFFLINE_APT= OFFLINE_MPD_TAR=\n"
            + _functions("as_src", "offline_deb_arch", "kit_archive_name", "offline_stage")
            + '\noffline_stage\necho "W=$OFFLINE_WHEELS M=$OFFLINE_MPD_TAR A=$OFFLINE_APT"\n')
    r = _run_bash(body, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    stage = upd / "offline-kit"
    assert (stage / "python/cp314-x86_64/a-1-py3-none-any.whl").read_bytes() == b"w"
    assert f"W={stage.as_posix()}/python/cp314-x86_64" in r.stdout
    assert f"M={stage.as_posix()}/images/domovoi-mpd_latest.tar" in r.stdout and "A= " not in r.stdout + " "
    if os.name == "nt":
        return  # Git Bash makes copies, not links
    os.symlink("/etc/passwd", src / "images" / "link.tar")
    r = _run_bash(body, tmp_path)
    assert r.returncode != 0 and "something other than files and folders" in r.stdout


def test_apply_update_offline_wiring():
    """The parts of the run the kit changes, read from the script: the
    never refusal counts only what the kit doesn't carry, pip gets no index,
    the rollback puts the venv copy back, and the stage always goes."""
    text = SCRIPT.read_text(encoding="utf-8")
    full = re.search(r"^full_update\(\) \{\n.*?^\}\n", text, re.M | re.S).group(0)
    assert full.index("resolve_offline_source") < full.index('[ "$POLICY_AT_START" = never ]')
    assert 'if [ "$POLICY_AT_START" = never ] && { [ "$net_deps" = 1 ]' in full
    assert full.index("run_step offline-kit offline_prepare") < full.index("run_step backup backup_db")
    assert "run_step venv-copy snapshot_venv_dir" in full
    sync = re.search(r"^sync_deps\(\) \{\n.*?^\}\n", text, re.M | re.S).group(0)
    assert 'PIP_SRC=(--no-index --find-links "$OFFLINE_WHEELS")' in sync
    locked = re.search(r"^sync_deps_locked\(\) \{\n.*?^\}\n", text, re.M | re.S).group(0)
    assert locked.count('${PIP_SRC[@]+"${PIP_SRC[@]}"}') == 4
    rb = re.search(r"^rollback\(\) \{\n.*?^\}\n", text, re.M | re.S).group(0)
    assert "run_step rollback-deps restore_venv_dir" in rb and "IN_ROLLBACK=1" in rb
    assert "offline_cleanup" in re.search(r"^on_exit\(\) \{\n.*?^\}\n", text, re.M | re.S).group(0)
    assert "offline_mpd_image" in re.search(r"^rebuild_mpd\(\) \{\n.*?^\}\n", text, re.M | re.S).group(0)
    added = re.search(r"^reconcile_added_packages\(\) \{\n.*?^\}\n", text, re.M | re.S).group(0)
    assert added.index('if [ -n "$OFFLINE_APT" ]') < added.index('if [ "$policy" = never ]')
