"""scripts/release/finish_release.py: the last step of a release, and the
order android/scripts/release.ps1|.sh publish in.

finish-release attaches the Windows Setup, writes one SHA256SUMS, sets the
notes and, last and only for a release without a suffix, fast-forwards
``stable``. Here it runs against a throwaway repository with a local bare
"origin" and a stand-in gh that keeps a fake GitHub release in a JSON file:
nothing reaches GitHub, nothing builds the real Setup (``--setup`` hands it
a file). Needs git; no DB.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from domovoi.tests.test_apply_update_script import BASH, requires_bash

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release" / "finish_release.py"
GIT = shutil.which("git")
requires_git = pytest.mark.skipif(GIT is None, reason="no git")


def _load():
    spec = importlib.util.spec_from_file_location("finish_release", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["finish_release"] = mod
    spec.loader.exec_module(mod)
    return mod


fr = _load()

FAKE_GH = textwrap.dedent('''\
    """A stand-in for gh release view/upload/edit, kept in the JSON file $FAKE_GH_STATE."""
    import hashlib, json, os, subprocess, sys
    path = os.environ["FAKE_GH_STATE"]
    state = json.load(open(path, encoding="utf-8"))
    args = sys.argv[1:]
    stable = subprocess.run(["git", "--git-dir", state["origin"], "rev-parse", "--verify", "--quiet", "refs/heads/stable"],
                            capture_output=True, text=True).stdout.strip()
    state["calls"].append({"args": args, "stable": stable})
    def save():
        json.dump(state, open(path, "w", encoding="utf-8"), indent=1)
    def opt(name):
        return args[args.index(name) + 1] if name in args else None
    tag = args[2] if len(args) > 2 else None
    rel = state["releases"].get(tag)
    if args[:2] == ["release", "view"]:
        save()
        if rel is None:
            print("release not found", file=sys.stderr); sys.exit(1)
        print(json.dumps({k: rel[k] for k in opt("--json").split(",") if k in rel})); sys.exit(0)
    if rel is None:
        save(); print("release not found", file=sys.stderr); sys.exit(1)
    if args[:2] == ["release", "upload"]:
        files = []
        for a in args[3:]:
            if a.startswith("--"):
                break
            files.append(a)
        for f in files:
            data = open(f, "rb").read()
            name = os.path.basename(f)
            if any(a["name"] == name for a in rel["assets"]) and "--clobber" not in args:
                save(); print(f"asset {name} exists", file=sys.stderr); sys.exit(1)
            rel["assets"] = [a for a in rel["assets"] if a["name"] != name]
            asset = {"name": name, "size": len(data), "url": "https://example.invalid/" + name}
            if not state.get("no_digest"):
                asset["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
            rel["assets"].append(asset)
            state["files"][name] = hashlib.sha256(data).hexdigest()
        save(); sys.exit(0)
    if args[:2] == ["release", "edit"]:
        rel["name"] = opt("--title")
        rel["body"] = open(opt("--notes-file"), encoding="utf-8").read()
        if "--prerelease" in args:
            rel["isPrerelease"] = True
        if "--latest" in args:
            rel["isPrerelease"] = False
            rel["latest"] = True
        save(); sys.exit(0)
    save(); print("unknown command", file=sys.stderr); sys.exit(2)
''')

NOTES_RC1 = """# Release notes

Newest first.

## 2026-10-15 — Older entry: in rc1 already

Server update.

* a list item
"""

NOTES_RC2 = """# Release notes

Newest first.

## 2026-10-16 — Android app: reply reports go to info@codersfarm.org

Android app only (a new APK); nothing changes on the server.

* **Email draft** goes to a new address.

## 2026-10-16 — A frozen core restarts itself

* starts with a list, so only the title is quoted

## 2026-10-15 — Older entry: in rc1 already

Server update.

* a list item
"""

PROVISION = b"#!/usr/bin/env bash\necho provision\n"
CERT = "ab" * 32


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _write(root: Path, rel: str, data: str | bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _manifest(version: str, apks: list[dict]) -> str:
    return json.dumps({"manifest_version": 1, "release": {
        "package": "com.domovoi.app", "version_name": version, "version_code": 26101602,
        "tag": f"v{version}", "commit": "0" * 40, "published": "2026-10-16", "cert_sha256": CERT, "apks": apks,
    }}, indent=2) + "\n"


def _pins(commit: str, provision_sha: str) -> str:
    return (f'#define DomovoiRepoUrl "https://github.com/coders-farm-official/domovoi"\n'
            f'#define DomovoiCommit "{commit}"\n'
            f'#define ProvisionUrl "https://raw.githubusercontent.com/x/" + DomovoiCommit + "/p.sh"\n'
            f'#define ProvisionSha256 "{provision_sha}"\n')


class World:
    """A checkout at the pins commit after the release commit, its bare origin, and a fake release."""

    def __init__(self, tmp_path: Path, monkeypatch, version: str = "1.0.0-rc2", stable: str | None = None,
                 hand_notes: str | None = None):
        self.tmp = tmp_path
        self.version = version
        self.tag = f"v{version}"
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        subprocess.run(["git", "init", "--quiet", "--bare", str(self.origin)], check=True)
        self.work.mkdir()
        w = self.work
        _git(w, "init", "--quiet", "-b", "main")
        for k, v in (("user.name", "Alex"), ("user.email", "alex@example.com"), ("core.autocrlf", "false"),
                     ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            _git(w, "config", k, v)
        _git(w, "remote", "add", "origin", str(self.origin))
        _write(w, ".gitignore", "build/\n")
        _write(w, "android/app/build.gradle.kts", '        versionName = "1.0.0-rc1"\n')
        _write(w, "android/release.json", '{"manifest_version": 1, "release": null}\n')
        _write(w, "docs/RELEASE_NOTES.md", NOTES_RC1)
        _write(w, "scripts/linux/provision.sh", PROVISION)
        _write(w, "windows/installer/pins.iss", _pins("1" * 40, "0" * 64))
        _git(w, "add", "-A"); _git(w, "commit", "--quiet", "-m", "rc1")
        _git(w, "tag", "v1.0.0-rc1")
        self.rc1 = _git(w, "rev-parse", "HEAD")
        # The build commit: the new version and its notes.
        _write(w, "android/app/build.gradle.kts", f'        versionName = "{version}"\n')
        _write(w, "docs/RELEASE_NOTES.md", NOTES_RC2)
        _git(w, "commit", "--quiet", "-am", "release: Domovoi")
        self.build = _git(w, "rev-parse", "HEAD")
        # The release commit: android/release.json naming its own APKs, tagged.
        self.apk_bytes = {f"domovoi-{version}-{abi}.apk": f"apk {abi}".encode() for abi in ("arm64-v8a", "armeabi-v7a")}
        self.apks = [{"abi": abi, "file": f"domovoi-{version}-{abi}.apk",
                      "url": f"https://github.com/coders-farm-official/domovoi/releases/download/v{version}/domovoi-{version}-{abi}.apk",
                      "sha256": hashlib.sha256(f"apk {abi}".encode()).hexdigest(), "size": len(f"apk {abi}".encode())}
                     for abi in ("arm64-v8a", "armeabi-v7a")]
        _write(w, "android/release.json", _manifest(version, self.apks))
        if hand_notes is not None:
            _write(w, f"docs/release/{self.tag}.md", hand_notes)
        _git(w, "add", "-A"); _git(w, "commit", "--quiet", "-m", "android: release")
        _git(w, "tag", self.tag)
        self.tag_commit = _git(w, "rev-parse", "HEAD")
        # The pins commit.
        _write(w, "windows/installer/pins.iss", _pins(self.tag_commit, hashlib.sha256(PROVISION).hexdigest()))
        _git(w, "commit", "--quiet", "-am", "windows: Setup installs it")
        self.head = _git(w, "rev-parse", "HEAD")
        _git(w, "push", "--quiet", "origin", "main", "--tags")
        if stable:
            _git(w, "push", "--quiet", "origin", f"{stable}:refs/heads/stable")
        _git(w, "fetch", "--quiet", "origin")
        _git(w, "branch", "--quiet", "--set-upstream-to=origin/main")

        self.setup = tmp_path / "DomovoiSetup.exe"
        self.setup.write_bytes(b"MZ not really a Setup")
        assets = [{"name": e["file"], "size": e["size"], "url": "https://example.invalid/" + e["file"],
                   "digest": "sha256:" + e["sha256"]} for e in self.apks]
        sums = "".join(f"{e['sha256']}  {e['file']}\n" for e in self.apks).encode()
        assets.append({"name": "SHA256SUMS", "size": len(sums), "url": "https://example.invalid/SHA256SUMS",
                       "digest": "sha256:" + hashlib.sha256(sums).hexdigest()})
        self.state_file = tmp_path / "gh-state.json"
        self.state = {"origin": str(self.origin), "calls": [], "files": {},
                      "releases": {self.tag: {"tagName": self.tag, "name": f"Domovoi {self.tag}", "isDraft": False,
                                              "isPrerelease": "-" in version, "url": "https://example.invalid/r",
                                              "assets": assets}}}
        self.save()
        fake = tmp_path / "fake_gh.py"
        fake.write_text(FAKE_GH, encoding="utf-8")
        self.gh = [sys.executable, str(fake)]
        monkeypatch.setenv("FAKE_GH_STATE", str(self.state_file))
        self.said: list[str] = []

    def save(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def load(self) -> dict:
        self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
        return self.state

    def tools(self, cls=None):
        return (cls or fr.Tools)(self.work, self.gh, self.said.append)

    def run(self, *extra: str, cls=None) -> int:
        return fr.run(["--setup", str(self.setup), *extra], tools=self.tools(cls), repo=self.work)

    def stable(self) -> str:
        return subprocess.run(["git", "--git-dir", str(self.origin), "rev-parse", "--verify", "--quiet", "refs/heads/stable"],
                              capture_output=True, text=True).stdout.strip()

    def calls(self, verb: str) -> list[list[str]]:
        return [c["args"] for c in self.load()["calls"] if c["args"][:2] == ["release", verb]]

    @property
    def stage(self) -> Path:
        return self.work / "windows" / "build" / "release" / self.tag


# ─── the dry run and a pre-release ───────────────────────────────────────

@requires_git
def test_a_dry_run_stages_everything_and_changes_nothing_on_github(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    assert w.run("--no-publish") == 0
    assert w.calls("upload") == [] and w.calls("edit") == []
    assert w.stable() == ""
    sums = (w.stage / "SHA256SUMS").read_bytes()
    assert b"\r" not in sums
    setup_sha = hashlib.sha256(w.setup.read_bytes()).hexdigest()
    assert sums.decode("ascii").splitlines() == [f"{e['sha256']}  {e['file']}" for e in w.apks] + [f"{setup_sha}  DomovoiSetup.exe"]
    assert (w.stage / "DomovoiSetup.exe").read_bytes() == w.setup.read_bytes()
    notes = (w.stage / "NOTES.md").read_text(encoding="utf-8")
    assert w.tag_commit in notes and w.head in notes and CERT in notes and setup_sha in notes
    assert "pre-release" in notes
    assert "## What's new since v1.0.0-rc1" in notes
    base = f"https://github.com/coders-farm-official/domovoi/blob/{w.tag}/docs/RELEASE_NOTES.md"
    assert f"({base}#2026-10-16--android-app-reply-reports-go-to-infocodersfarmorg)" in notes
    assert f"({base}#2026-10-16--a-frozen-core-restarts-itself)" in notes
    assert "Android app only (a new APK); nothing changes on the server." in notes
    assert "starts with a list" not in notes
    assert "in rc1 already" not in notes
    assert len(notes) < fr.BODY_BUDGET


@requires_git
def test_a_prerelease_gets_its_setup_sums_and_notes_but_never_moves_stable(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    assert w.run() == 0
    (upload,) = w.calls("upload")
    assert "--clobber" in upload
    assert [Path(a).name for a in upload[3:5]] == ["DomovoiSetup.exe", "SHA256SUMS"]
    (edit,) = w.calls("edit")
    assert "--prerelease" in edit and "--latest" not in edit
    assert edit[edit.index("--title") + 1] == "Domovoi v1.0.0-rc2"
    rel = w.load()["releases"][w.tag]
    assert sorted(a["name"] for a in rel["assets"]) == sorted([*w.apk_bytes, "DomovoiSetup.exe", "SHA256SUMS"])
    sums = (w.stage / "SHA256SUMS").read_bytes()
    assert w.state["files"]["SHA256SUMS"] == hashlib.sha256(sums).hexdigest()
    assert rel["body"] == (w.stage / "NOTES.md").read_text(encoding="utf-8")
    assert w.stable() == ""
    assert any("pre-release never moves it" in s for s in w.said)


# ─── stable: last, forward only ──────────────────────────────────────────

@requires_git
def test_a_release_moves_stable_last_and_only_forward(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0", stable="v1.0.0-rc1")
    assert w.stable() == w.rc1
    assert w.run() == 0
    assert w.stable() == w.tag_commit
    # Every gh call, the edit and the upload included, saw stable where it was.
    assert {c["stable"] for c in w.load()["calls"]} == {w.rc1}
    (edit,) = w.calls("edit")
    assert "--latest" in edit and "--prerelease" not in edit
    assert "Servers that follow `stable` don't get it" not in w.state["releases"][w.tag]["body"]


@requires_git
def test_the_first_release_makes_stable(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    assert w.run() == 0
    assert w.stable() == w.tag_commit


@requires_git
def test_a_stable_the_tag_does_not_come_after_is_refused_before_anything_is_uploaded(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    side = w.tmp / "side"
    _git(w.tmp, "clone", "--quiet", str(w.origin), str(side))
    for k, v in (("user.name", "Alex"), ("user.email", "alex@example.com"), ("commit.gpgsign", "false")):
        _git(side, "config", k, v)
    _git(side, "checkout", "--quiet", "-b", "other", w.rc1)
    _write(side, "elsewhere.txt", "a commit the release doesn't have\n")
    _git(side, "add", "-A"); _git(side, "commit", "--quiet", "-m", "elsewhere")
    _git(side, "push", "--quiet", "origin", "HEAD:refs/heads/stable")
    diverged = w.stable()
    with pytest.raises(fr.Refused, match="never rewound or forced"):
        w.run()
    assert w.calls("upload") == [] and w.calls("edit") == []
    assert w.stable() == diverged


@requires_git
def test_a_second_run_replaces_the_files_and_leaves_stable(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    assert w.run() == 0
    w.setup.write_bytes(b"MZ rebuilt")
    assert w.run() == 0
    assert len(w.calls("upload")) == 2
    rel = w.load()["releases"][w.tag]
    assert [a["name"] for a in rel["assets"]].count("DomovoiSetup.exe") == 1
    assert w.state["files"]["DomovoiSetup.exe"] == hashlib.sha256(b"MZ rebuilt").hexdigest()
    assert w.stable() == w.tag_commit
    assert any("already at" in s for s in w.said)


# ─── what it refuses ─────────────────────────────────────────────────────

def _commit_pins(w: World, text: str) -> None:
    _write(w.work, "windows/installer/pins.iss", text)
    _git(w.work, "commit", "--quiet", "-am", "pins again")
    _git(w.work, "push", "--quiet", "origin", "main")


@requires_git
def test_pins_that_install_another_commit_are_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    _commit_pins(w, _pins(w.build, hashlib.sha256(PROVISION).hexdigest()))
    with pytest.raises(fr.Refused, match="move the Setup's pins"):
        w.run("--no-publish")


@requires_git
def test_a_provision_pin_that_is_not_the_script_setup_runs_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    _commit_pins(w, _pins(w.tag_commit, "f" * 64))
    with pytest.raises(fr.Refused, match="ProvisionSha256"):
        w.run("--no-publish")


@requires_git
def test_a_bundled_provision_script_is_the_one_pinned(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    newer = PROVISION + b"echo a fix\n"
    _write(w.work, "scripts/linux/provision.sh", newer)
    pins = _pins(w.tag_commit, hashlib.sha256(newer).hexdigest())
    pins += '#define ProvisionBundled\n#define ProvisionSource "..\\..\\scripts\\linux\\provision.sh"\n'
    _commit_pins(w, pins)
    assert w.run("--no-publish") == 0
    assert any("differs from the tag's" in s for s in w.said)


@requires_git
def test_a_tag_made_before_its_manifest_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    with pytest.raises(fr.Refused, match="names 1.0.0-rc1|names no release"):
        w.run("--no-publish", "--tag", "v1.0.0-rc1")


@pytest.mark.parametrize("change, why", [
    (lambda rel: rel["assets"].append({"name": "extra.zip", "size": 1, "url": "u", "digest": "sha256:" + "0" * 64}), "doesn't know"),
    (lambda rel: rel["assets"][0].update(digest="sha256:" + "0" * 64), "is not the file"),
    (lambda rel: rel["assets"].pop(0), "has no"),
    (lambda rel: rel.update(isDraft=True), "draft"),
])
@requires_git
def test_a_release_that_is_not_what_release_ps1_published_is_refused(tmp_path, monkeypatch, change, why):
    w = World(tmp_path, monkeypatch)
    change(w.state["releases"][w.tag])
    w.save()
    with pytest.raises(fr.Refused, match=why):
        w.run()
    assert w.calls("upload") == []


@requires_git
def test_no_release_on_github_is_refused_unless_a_dry_run(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    w.state["releases"] = {}
    w.save()
    with pytest.raises(fr.Refused, match="no GitHub release"):
        w.run()
    assert w.run("--no-publish") == 0


@requires_git
def test_a_dirty_checkout_or_a_head_before_the_tag_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    _write(w.work, "docs/RELEASE_NOTES.md", "changed\n")
    with pytest.raises(fr.Refused, match="uncommitted"):
        w.run("--no-publish")
    _git(w.work, "checkout", "--quiet", "--", "docs/RELEASE_NOTES.md")
    _git(w.work, "checkout", "--quiet", w.build)
    with pytest.raises(fr.Refused, match="does not come after the tag"):
        w.run("--no-publish", "--tag", w.tag)


@requires_git
def test_a_head_github_lacks_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    _write(w.work, "windows/README.txt", "local only\n")
    _git(w.work, "add", "-A"); _git(w.work, "commit", "--quiet", "-m", "not pushed")
    with pytest.raises(fr.Refused, match="not on GitHub yet"):
        w.run()


@requires_git
def test_without_github_digests_each_file_is_downloaded_and_checked(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    for a in w.state["releases"][w.tag]["assets"]:
        a.pop("digest")
    w.state["no_digest"] = True
    w.save()
    fetched = []

    class Downloads(fr.Tools):
        def download_sha256(self, url):
            name = url.rsplit("/", 1)[1]
            fetched.append(name)
            files = json.loads(w.state_file.read_text(encoding="utf-8"))["files"]
            known = {e["file"]: e["sha256"] for e in w.apks}
            return files.get(name) or known[name]

    assert w.run(cls=Downloads) == 0
    assert sorted(fetched) == sorted([*w.apk_bytes, "DomovoiSetup.exe", "SHA256SUMS"])

    class Tampered(Downloads):
        def download_sha256(self, url):
            return "0" * 64 if url.endswith("DomovoiSetup.exe") else super().download_sha256(url)

    with pytest.raises(fr.Refused, match="DomovoiSetup.exe on GitHub is not the file"):
        w.run(cls=Tampered)


@requires_git
def test_docs_release_at_the_tag_is_the_summary(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, hand_notes="## Hand-written\n\nThe summary for this release.\n")
    assert w.run("--no-publish") == 0
    notes = (w.stage / "NOTES.md").read_text(encoding="utf-8")
    assert "The summary for this release." in notes and "What's new since" not in notes


# ─── the notes, piece by piece ───────────────────────────────────────────

def test_anchors_are_github_s():
    # What GitHub's own renderer gave these headings (gh api markdown, 2026-10-16).
    seen: dict[str, int] = {}
    got = [fr.github_slug(h, seen) for h in (
        "Release notes",
        "2026-10-16 — Android app: reply reports go to info@codersfarm.org",
        "2026-10-12 (later) — The router learns a satellite's new name after one reboot",
        "2026-10-12 — Satellites: ready for spare devices (nothing changes for a Pi)",
        "2026-10-12 — Satellites: ready for spare devices (nothing changes for a Pi)",
        "`hey_jarvis` and **bold** → arrows Ünïcode 1.0.0-rc1",
    )]
    assert got == [
        "release-notes",
        "2026-10-16--android-app-reply-reports-go-to-infocodersfarmorg",
        "2026-10-12-later--the-router-learns-a-satellites-new-name-after-one-reboot",
        "2026-10-12--satellites-ready-for-spare-devices-nothing-changes-for-a-pi",
        "2026-10-12--satellites-ready-for-spare-devices-nothing-changes-for-a-pi-1",
        "hey_jarvis-and-bold--arrows-ünïcode-100-rc1",
    ]


def test_entries_skip_headings_in_code_and_count_repeats():
    text = "# T\n\n## A\n\none\n\n```\n## not a heading\n```\n\n### A\n\n## A\n\ntwo\n"
    got = fr.entries(text)
    assert [(e.heading, e.anchor) for e in got] == [("A", "a"), ("A", "a-2")]
    assert "not a heading" in got[0].body


def test_the_summary_shrinks_to_its_budget():
    new = [fr.Entry(heading=f"2026-10-{i:02d} — entry {i}", body="Words. " * 40, anchor=f"e{i}") for i in range(1, 60)]
    full = fr.summary(new, tag="v1.0.0", since="v0.9.0", budget=100_000)
    assert "Words." in full and "more, in the full notes" not in full
    titles = fr.summary(new, tag="v1.0.0", since="v0.9.0", budget=len(full) - 1)
    assert "Words." not in titles and "entry 59" in titles
    short = fr.summary(new, tag="v1.0.0", since="v0.9.0", budget=1500)
    assert len(short) <= 1500 and "more, in the full notes" in short
    assert "/blob/v1.0.0/docs/RELEASE_NOTES.md" in short


def test_the_whole_body_of_the_real_notes_fits():
    """This repo's own notes, summarised from nothing (the worst case)."""
    text = (REPO / "docs" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    new = fr.new_entries(text, None)
    assert len(new) > 20
    rel = fr.Release(tag="v9.9.9", version="9.9.9", prerelease=False, tag_commit="a" * 40, setup_commit="b" * 40,
                     cert=CERT, apks=[{"abi": "arm64-v8a", "file": "x.apk", "sha256": "c" * 64}], setup_sha256="d" * 64)
    frame = len(fr.body(rel))
    rel.notes = fr.summary(new, tag=rel.tag, since=None, budget=fr.BODY_BUDGET - frame)
    assert len(fr.body(rel)) <= fr.BODY_BUDGET < fr.BODY_LIMIT


def test_first_paragraph_only_when_it_is_prose_and_short():
    assert fr.first_paragraph("Server update.\nTwo lines.\n\n* list") == "Server update. Two lines."
    assert fr.first_paragraph("**Bold** start is prose.") == "**Bold** start is prose."
    assert fr.first_paragraph("* a list first") == ""
    assert fr.first_paragraph("| a | table |") == ""
    assert fr.first_paragraph("x" * (fr.PARAGRAPH_MAX + 1)) == ""


def test_sums_are_lf_two_spaces_and_every_asset():
    rel = fr.Release(tag="v1.0.0", version="1.0.0", prerelease=False, tag_commit="a" * 40, setup_commit="b" * 40,
                     cert=CERT, apks=[{"abi": "arm64-v8a", "file": "a.apk", "sha256": "1" * 64},
                                      {"abi": "armeabi-v7a", "file": "b.apk", "sha256": "2" * 64}], setup_sha256="3" * 64)
    assert fr.sums_text(rel) == f"{'1' * 64}  a.apk\n{'2' * 64}  b.apk\n{'3' * 64}  DomovoiSetup.exe\n"


def test_the_pins_are_read_the_way_inno_setup_writes_them():
    text = (REPO / "windows" / "installer" / "pins.iss").read_text(encoding="utf-8")
    values, flags = fr.read_pins(text)
    assert re.fullmatch(r"[0-9a-f]{40}", values["DomovoiCommit"])
    assert re.fullmatch(r"[0-9a-f]{64}", values["ProvisionSha256"])
    assert "ProvisionUrl" not in values  # an expression, not a value


# ─── the scripts around it ───────────────────────────────────────────────

def test_the_wrappers_run_the_python_and_are_lf_ascii():
    folder = REPO / "scripts" / "release"
    for name in ("finish-release.ps1", "finish-release.sh", "finish_release.py"):
        data = (folder / name).read_bytes()
        assert b"\r" not in data, name
    for name in ("finish-release.ps1", "finish-release.sh"):
        text = (folder / name).read_text(encoding="ascii")
        assert "finish_release.py" in text, name
    ps1 = (folder / "finish-release.ps1").read_text(encoding="ascii")
    for flag in ("--tag", "--no-publish", "--setup", "--since"):
        assert f"'{flag}'" in ps1, flag


@requires_bash
def test_the_bash_scripts_parse():
    for script in ("scripts/release/finish-release.sh", "android/scripts/release.sh"):
        proc = subprocess.run([BASH, "-n", str(REPO / script)], capture_output=True, text=True)
        assert proc.returncode == 0, f"{script}: {proc.stderr}"


@pytest.mark.parametrize("script", ["android/scripts/release.ps1", "android/scripts/release.sh"])
def test_release_commits_the_manifest_then_tags_that_commit(script):
    """The manifest is committed and pushed before gh makes the tag, the tag
    goes on the release commit (not the build commit), a version with a
    suffix is a pre-release, and the tag is checked to be that commit."""
    text = (REPO / script).read_text(encoding="utf-8")
    commit = text.index("'commit', '--quiet', '-m'" if script.endswith(".ps1") else 'git commit --quiet -m "android: release')
    create = text.index("release', 'create'" if script.endswith(".ps1") else "gh release create")
    push = text.index("'push', '--quiet', 'origin'" if script.endswith(".ps1") else "git push --quiet origin")
    assert push < create, "the release commit is pushed before the release is made"
    fn_end = text.index("# --- carry on" if script.endswith(".ps1") else "# ─── carry on")
    assert create < fn_end < commit, "publishing is one function, called after the commit"
    if script.endswith(".ps1"):
        assert "'--target', $ReleaseCommit" in text
        assert "$isPrerelease = $Prerelease -or $versionName.Contains('-')" in text
        assert "-ne $ReleaseCommit) { Die" in text
    else:
        assert '--target "$release_commit"' in text
        assert 'case "$version_name" in *-*) is_prerelease=1' in text
        assert '[ "$tagged" = "$release_commit" ]' in text
    assert "--latest" in text and "--prerelease" in text
    # A run that stopped after the commit carries on (nothing signed again).
    assert "carries on from" in text
