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
import os
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


def _manifest(version: str, apks: list[dict], commit: str) -> str:
    return json.dumps({"manifest_version": 1, "release": {
        "package": "com.domovoi.app", "version_name": version, "version_code": 26101602,
        "tag": f"v{version}", "commit": commit, "published": "2026-10-16", "cert_sha256": CERT, "apks": apks,
    }}, indent=2) + "\n"


def _pins(commit: str, provision_sha: str, bundled: bool = True) -> str:
    return (f'#define DomovoiRepoUrl "https://github.com/coders-farm-official/domovoi"\n'
            f'#define DomovoiCommit "{commit}"\n'
            f'#define ProvisionUrl "https://raw.githubusercontent.com/x/" + DomovoiCommit + "/p.sh"\n'
            f'#define ProvisionSha256 "{provision_sha}"\n'
            + ("#define ProvisionBundled\n" if bundled else "; #define ProvisionBundled\n"))


def _pyproject(version: str) -> str:
    return f'[project]\nname = "domovoi"\nversion = "{fr.pep440(version)}"\n'


class World:
    """A checkout after the release commit (a later commit on top), its bare origin, and a fake release."""

    def __init__(self, tmp_path: Path, monkeypatch, version: str = "1.0.0-rc2", stable: str | None = None,
                 hand_notes: str | None = None, github_prerelease: bool = False, annotated: bool = False):
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
        monkeypatch.setenv("DOMOVOI_RELEASE_ORIGIN_URL", str(self.origin))
        _write(w, ".gitignore", "build/\n")
        _write(w, "pyproject.toml", _pyproject("1.0.0-rc1"))
        _write(w, "android/app/build.gradle.kts", '        versionName = "1.0.0-rc1"\n')
        _write(w, "android/release.json", '{"manifest_version": 1, "release": null}\n')
        _write(w, "docs/RELEASE_NOTES.md", NOTES_RC1)
        _write(w, "scripts/linux/provision.sh", PROVISION)
        _write(w, "windows/installer/pins.iss", _pins("1" * 40, "0" * 64))
        _write(w, "windows/build.ps1", "param([string]$Pin)\n")
        _git(w, "add", "-A"); _git(w, "commit", "--quiet", "-m", "rc1")
        _git(w, "tag", "v1.0.0-rc1")
        self.rc1 = _git(w, "rev-parse", "HEAD")
        # The build commit: the new version and its notes.
        _write(w, "pyproject.toml", _pyproject(version))
        _write(w, "android/app/build.gradle.kts", f'        versionName = "{version}"\n')
        _write(w, "docs/RELEASE_NOTES.md", NOTES_RC2)
        if hand_notes is not None:
            _write(w, f"docs/release/{self.tag}.md", hand_notes)
        _git(w, "add", "-A"); _git(w, "commit", "--quiet", "-m", "release: Domovoi")
        self.build = _git(w, "rev-parse", "HEAD")
        # The release commit: android/release.json naming its own APKs, on the build commit, tagged.
        self.apk_bytes = {f"domovoi-{version}-{abi}.apk": f"apk {abi}".encode() for abi in ("arm64-v8a", "armeabi-v7a")}
        self.apks = [{"abi": abi, "file": f"domovoi-{version}-{abi}.apk",
                      "url": f"https://github.com/coders-farm-official/domovoi/releases/download/v{version}/domovoi-{version}-{abi}.apk",
                      "sha256": hashlib.sha256(f"apk {abi}".encode()).hexdigest(), "size": len(f"apk {abi}".encode())}
                     for abi in ("arm64-v8a", "armeabi-v7a")]
        _write(w, "android/release.json", _manifest(version, self.apks, self.build))
        _git(w, "commit", "--quiet", "-am", "android: release")
        if annotated:
            _git(w, "tag", "-a", "-m", "a signed release, one day", self.tag)
        else:
            _git(w, "tag", self.tag)
        self.tag_commit = _git(w, "rev-parse", "HEAD")
        # Main moves on: a later commit (an old-style pins commit, say).
        _write(w, "windows/installer/pins.iss", _pins(self.tag_commit, hashlib.sha256(PROVISION).hexdigest()))
        _git(w, "commit", "--quiet", "-am", "windows: Setup installs it")
        self.head = _git(w, "rev-parse", "HEAD")
        _git(w, "push", "--quiet", "origin", "main", "--tags")
        if stable:
            _git(w, "push", "--quiet", "origin", f"{stable}^{{commit}}:refs/heads/stable")
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
                                              "isPrerelease": github_prerelease, "url": "https://example.invalid/r",
                                              "assets": assets}}}
        self.save()
        fake = tmp_path / "fake_gh.py"
        fake.write_text(FAKE_GH, encoding="utf-8")
        self.gh = [sys.executable, str(fake)]
        monkeypatch.setenv("FAKE_GH_STATE", str(self.state_file))
        self.said: list[str] = []
        self.saw: list[str] = []

    def save(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def load(self) -> dict:
        self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
        return self.state

    def tools(self, cls=None):
        return (cls or fr.Tools)(self.work, self.gh, self.said.append)

    def run(self, *extra: str, cls=None) -> int:
        return fr.run(["--setup", str(self.setup), *extra], tools=self.tools(cls), repo=self.work)

    def pin_build(self, src: Path, tag: str, repo: Path) -> Path:
        """What windows/build.ps1 -Pin does, in the worktree it is given:
        the pins moved to its commit (provision.sh fetched), then a Setup."""
        self.saw = sorted(p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file() and p.name != ".git")
        sha = _git(src, "rev-parse", "HEAD")
        provision = (src / "scripts" / "linux" / "provision.sh").read_bytes()
        _write(src, "windows/installer/pins.iss", _pins(sha, hashlib.sha256(provision).hexdigest(), bundled=False))
        _write(src, "windows/build/DomovoiSetup.exe", b"MZ built from " + sha.encode())
        return src / "windows" / "build" / "DomovoiSetup.exe"

    def run_built(self, *extra: str, builder=None, cls=None) -> int:
        return fr.run(list(extra), tools=self.tools(cls), repo=self.work, build_setup=builder or self.pin_build)

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
    assert w.tag_commit in notes and CERT in notes and setup_sha in notes
    assert "handed to it (`--setup`)" in notes and w.head not in notes
    assert "is a candidate for 1.0.0" in notes and "Servers that follow `stable` don't get it." in notes
    assert "## What's new since v1.0.0-rc1" in notes
    base = f"https://github.com/coders-farm-official/domovoi/blob/{w.tag}/docs/RELEASE_NOTES.md"
    assert f"({base}#2026-10-16--android-app-reply-reports-go-to-infocodersfarmorg)" in notes
    assert f"({base}#2026-10-16--a-frozen-core-restarts-itself)" in notes
    assert "Android app only (a new APK); nothing changes on the server." in notes
    assert "starts with a list" not in notes
    assert "in rc1 already" not in notes
    assert len(notes) < fr.BODY_BUDGET


@requires_git
def test_a_release_candidate_is_a_full_release_but_never_moves_stable(tmp_path, monkeypatch):
    """Every release is a full one, release candidates included (owner
    decision 2026-10-15: the suffix says what it is); stable still moves
    only for a version without a suffix."""
    w = World(tmp_path, monkeypatch)
    assert w.run() == 0
    (upload,) = w.calls("upload")
    assert "--clobber" in upload
    assert [Path(a).name for a in upload[3:5]] == ["DomovoiSetup.exe", "SHA256SUMS"]
    (edit,) = w.calls("edit")
    assert "--latest" in edit and "--prerelease" not in edit
    assert w.load()["releases"][w.tag]["isPrerelease"] is False
    assert edit[edit.index("--title") + 1] == "Domovoi v1.0.0-rc2"
    rel = w.load()["releases"][w.tag]
    assert sorted(a["name"] for a in rel["assets"]) == sorted([*w.apk_bytes, "DomovoiSetup.exe", "SHA256SUMS"])
    sums = (w.stage / "SHA256SUMS").read_bytes()
    assert w.state["files"]["SHA256SUMS"] == hashlib.sha256(sums).hexdigest()
    assert rel["body"] == (w.stage / "NOTES.md").read_text(encoding="utf-8")
    assert w.stable() == ""
    assert any(fr.STABLE_STAYS in s for s in w.said)


@requires_git
def test_a_release_github_marks_a_pre_release_stays_one_and_leaves_stable(tmp_path, monkeypatch):
    """release.ps1 -Prerelease: finish-release keeps it so, and a plain
    version marked a pre-release doesn't move stable either."""
    w = World(tmp_path, monkeypatch, version="1.0.0", stable="v1.0.0-rc1", github_prerelease=True)
    assert w.run() == 0
    (edit,) = w.calls("edit")
    assert "--prerelease" in edit and "--latest" not in edit
    assert w.load()["releases"][w.tag]["isPrerelease"] is True
    assert "is a pre-release" in w.state["releases"][w.tag]["body"]
    assert w.stable() == w.rc1


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


# ─── the Setup, built from the tag's own files ───────────────────────────

@requires_git
@requires_git
def test_the_tray_the_build_makes_is_attached_and_summed(tmp_path, monkeypatch):
    """DomovoiTray.exe (windows/build/bin, built with the Setup) goes on the
    release too, for Windows installs from before the tray: in SHA256SUMS,
    uploaded between the Setup and the sums, named in the notes."""
    w = World(tmp_path, monkeypatch, version="1.0.0")

    def build_with_tray(src: Path, tag: str, repo: Path) -> Path:
        exe = w.pin_build(src, tag, repo)
        _write(src, "windows/build/bin/DomovoiTray.exe", b"MZ tray " + tag.encode())
        return exe

    assert w.run_built(builder=build_with_tray) == 0
    tray_sha = hashlib.sha256(b"MZ tray " + w.tag.encode()).hexdigest()
    sums = (w.stage / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert sums[-2].endswith("  DomovoiSetup.exe") and sums[-1] == f"{tray_sha}  DomovoiTray.exe"
    (upload,) = w.calls("upload")
    assert [Path(a).name for a in upload[3:6]] == ["DomovoiSetup.exe", "DomovoiTray.exe", "SHA256SUMS"]
    assert w.load()["files"]["DomovoiTray.exe"] == tray_sha
    notes = w.state["releases"][w.tag]["body"]
    assert f"| DomovoiTray.exe |" in notes and tray_sha in notes and "from before the tray icon" in notes


@requires_git
def test_a_setup_handed_in_brings_its_tray_from_beside_it_or_none(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    assert w.run("--no-publish") == 0
    assert not (w.stage / "DomovoiTray.exe").exists()
    assert any("predates the tray icon" in s for s in w.said)
    (w.setup.parent / "bin").mkdir()
    (w.setup.parent / "bin" / "DomovoiTray.exe").write_bytes(b"MZ tray")
    assert w.run("--no-publish") == 0
    assert (w.stage / "SHA256SUMS").read_text(encoding="ascii").splitlines()[-1] == hashlib.sha256(b"MZ tray").hexdigest() + "  DomovoiTray.exe"
    with pytest.raises(fr.Refused, match="no tray at"):
        w.run("--no-publish", "--tray", str(tmp_path / "missing.exe"))


@requires_git
def test_the_satellite_setup_the_build_makes_is_attached_beside_the_setup(tmp_path, monkeypatch):
    """DomovoiSatelliteSetup.exe (windows/build, beside DomovoiSetup.exe;
    2026-10-21): in SHA256SUMS, uploaded before the sums, named in the
    notes with what SmartScreen says. Servers fetch it by this name."""
    from domovoi import satellite_setup

    assert fr.SATELLITE_SETUP_ASSET == satellite_setup.SETUP_ASSET
    w = World(tmp_path, monkeypatch, version="1.0.0")

    def build_with_satellite(src: Path, tag: str, repo: Path) -> Path:
        exe = w.pin_build(src, tag, repo)
        _write(src, "windows/build/DomovoiSatelliteSetup.exe", b"MZ satellite " + tag.encode())
        return exe

    assert w.run_built(builder=build_with_satellite) == 0
    sat_sha = hashlib.sha256(b"MZ satellite " + w.tag.encode()).hexdigest()
    sums = (w.stage / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert sums[-1] == f"{sat_sha}  DomovoiSatelliteSetup.exe"
    (upload,) = w.calls("upload")
    names = [Path(a).name for a in upload[3:]]
    assert names.index("DomovoiSetup.exe") < names.index("DomovoiSatelliteSetup.exe") < names.index("SHA256SUMS")
    assert w.load()["files"]["DomovoiSatelliteSetup.exe"] == sat_sha
    notes = w.state["releases"][w.tag]["body"]
    assert "| DomovoiSatelliteSetup.exe |" in notes and sat_sha in notes
    assert "Windows protected your PC" in notes and "Run anyway" in notes
    # SHA256SUMS in the format the server reads it.
    assert satellite_setup.parse_sums(chr(10).join(sums)) == sat_sha


@requires_git
def test_a_setup_handed_in_brings_its_satellite_setup_from_beside_it_or_none(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    assert w.run("--no-publish") == 0
    assert not (w.stage / "DomovoiSatelliteSetup.exe").exists()
    assert any("predates the satellite Setup" in s for s in w.said)
    (w.setup.parent / "DomovoiSatelliteSetup.exe").write_bytes(b"MZ sat")
    assert w.run("--no-publish") == 0
    last = (w.stage / "SHA256SUMS").read_text(encoding="ascii").splitlines()[-1]
    assert last == hashlib.sha256(b"MZ sat").hexdigest() + "  DomovoiSatelliteSetup.exe"
    with pytest.raises(fr.Refused, match="no satellite Setup at"):
        w.run("--no-publish", "--satellite-setup", str(tmp_path / "missing.exe"))


def test_the_setup_is_built_from_the_tag_alone_and_pinned_to_it(tmp_path, monkeypatch):
    """Review R3: commits after the tag never reach the Setup. It is built in
    a clean worktree of the tag's commit, where build.ps1 -Pin moves the pins
    to it; the worktree is gone afterwards."""
    w = World(tmp_path, monkeypatch, version="1.0.0")
    _write(w.work, "windows/helper/Evil.cs", "// not in the tag\n")
    _git(w.work, "add", "-A"); _git(w.work, "commit", "--quiet", "-m", "an installer change after the tag")
    _git(w.work, "push", "--quiet", "origin", "main")
    assert w.run_built() == 0
    assert "windows/helper/Evil.cs" not in w.saw and "android/release.json" in w.saw
    built = w.load()["files"]["DomovoiSetup.exe"]
    assert built == hashlib.sha256(b"MZ built from " + w.tag_commit.encode()).hexdigest()
    values, flags = fr.read_pins((w.stage / "pins.iss").read_text(encoding="utf-8"))
    assert values["DomovoiCommit"] == w.tag_commit and "ProvisionBundled" not in flags
    notes = w.state["releases"][w.tag]["body"]
    assert f"built from the tag's own files by `windows/build.ps1 -Pin {w.tag}`" in notes
    assert w.head not in notes
    assert _git(w.work, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert w.stable() == w.tag_commit


def _unpinned(w, src, tag, repo):
    exe = w.pin_build(src, tag, repo)
    _write(src, "windows/installer/pins.iss", _pins(w.build, hashlib.sha256(PROVISION).hexdigest(), bundled=False))
    return exe


def _bundling(w, src, tag, repo):
    exe = w.pin_build(src, tag, repo)
    _write(src, "windows/installer/pins.iss", _pins(w.tag_commit, hashlib.sha256(PROVISION).hexdigest(), bundled=True))
    return exe


def _wrong_provision(w, src, tag, repo):
    exe = w.pin_build(src, tag, repo)
    _write(src, "windows/installer/pins.iss", _pins(w.tag_commit, "f" * 64, bundled=False))
    return exe


def _more_than_pins(w, src, tag, repo):
    exe = w.pin_build(src, tag, repo)
    _write(src, "scripts/linux/provision.sh", PROVISION + b"echo not the tag's\n")
    return exe


def _nothing_pinned(w, src, tag, repo):
    _write(src, "windows/build/DomovoiSetup.exe", b"MZ")
    return src / "windows" / "build" / "DomovoiSetup.exe"


@pytest.mark.parametrize("builder, why", [
    (_unpinned, "not the tag's commit"),
    (_bundling, "bundles a provision.sh"),
    (_wrong_provision, "ProvisionSha256"),
    (_more_than_pins, "changed beyond"),
    (_nothing_pinned, "nothing pinned"),
])
@requires_git
def test_a_setup_that_is_not_the_tag_pinned_to_itself_is_refused(tmp_path, monkeypatch, builder, why):
    w = World(tmp_path, monkeypatch)
    with pytest.raises(fr.Refused, match=why):
        w.run_built(builder=lambda src, tag, repo: builder(w, src, tag, repo))
    assert w.calls("upload") == [] and w.calls("edit") == []
    assert _git(w.work, "worktree", "list", "--porcelain").count("worktree ") == 1


@requires_git
def test_a_tag_without_build_ps1_pin_says_how_to_build_it(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    src = tmp_path / "src"
    (src / "windows").mkdir(parents=True)
    (src / "windows" / "build.ps1").write_text("param([switch]$SkipInstaller)\n", encoding="utf-8")
    with pytest.raises(fr.Refused, match=r"has no -Pin.*build\.ps1 -Pin v1\.0\.0-rc2.*--setup"):
        fr._build_setup(src, w.tag, w.work)


# ─── what it refuses ─────────────────────────────────────────────────────

@requires_git
def test_a_tag_made_before_its_manifest_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    with pytest.raises(fr.Refused, match="names 1.0.0-rc1|names no release"):
        w.run("--no-publish", "--tag", "v1.0.0-rc1")


@requires_git
def test_a_tagged_commit_that_is_not_the_manifest_on_its_build_commit_is_refused(tmp_path, monkeypatch):
    """Review R1, finish-release's half: the tag's parent is the commit the
    manifest names, and the tag's commit changes that file only."""
    w = World(tmp_path, monkeypatch)
    # A commit slipped in between the build and the manifest, then the tag moved onto a rebuilt chain.
    _git(w.work, "checkout", "--quiet", w.build)
    _write(w.work, "unreviewed.txt", "an agent's change\n")
    _git(w.work, "add", "-A"); _git(w.work, "commit", "--quiet", "-m", "unreviewed")
    _write(w.work, "android/release.json", _manifest(w.version, w.apks, w.build))
    _git(w.work, "commit", "--quiet", "-am", "android: release")
    _git(w.work, "tag", "-f", w.tag)
    _git(w.work, "push", "--quiet", "--force", "origin", f"refs/tags/{w.tag}")
    with pytest.raises(fr.Refused, match="not android/release.json on top of the commit its APKs were built from"):
        w.run("--no-publish")
    # The manifest committed together with another file.
    _git(w.work, "checkout", "--quiet", w.build)
    _write(w.work, "android/release.json", _manifest(w.version, w.apks, w.build))
    _write(w.work, "also.txt", "and this\n")
    _git(w.work, "add", "-A"); _git(w.work, "commit", "--quiet", "-m", "android: release, and more")
    _git(w.work, "tag", "-f", w.tag)
    _git(w.work, "push", "--quiet", "--force", "origin", f"refs/tags/{w.tag}")
    with pytest.raises(fr.Refused, match="changes more than android/release.json"):
        w.run("--no-publish")


@requires_git
def test_an_annotated_tag_is_peeled_to_its_commit(tmp_path, monkeypatch):
    """Review R4: a signed (annotated) tag is a release like any other."""
    w = World(tmp_path, monkeypatch, version="1.0.0", annotated=True)
    assert _git(w.work, "rev-parse", w.tag) != w.tag_commit
    assert w.run() == 0
    assert w.stable() == w.tag_commit


@requires_git
def test_an_origin_that_is_not_the_github_repository_is_refused_first(tmp_path, monkeypatch):
    """Review R5: every URL of origin, push URLs included."""
    w = World(tmp_path, monkeypatch, version="1.0.0")
    other = tmp_path / "other.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(other)], check=True)
    _git(w.work, "config", "remote.origin.pushurl", str(other))
    with pytest.raises(fr.Refused, match="origin is .*other.git"):
        w.run()
    assert w.load()["calls"] == []
    _git(w.work, "config", "--unset", "remote.origin.pushurl")
    monkeypatch.delenv("DOMOVOI_RELEASE_ORIGIN_URL")
    with pytest.raises(fr.Refused, match="not github.com/coders-farm-official/domovoi"):
        w.run("--no-publish")
    for url in ("https://github.com/coders-farm-official/domovoi", "https://github.com/coders-farm-official/domovoi.git",
                "git@github.com:coders-farm-official/domovoi.git", "ssh://git@github.com/coders-farm-official/domovoi"):
        assert fr.ORIGIN_RE.fullmatch(url), url
    for url in ("https://github.com/someone/domovoi", "https://github.com/coders-farm-official/domovoi-fork",
                "https://example.com/coders-farm-official/domovoi"):
        assert not fr.ORIGIN_RE.fullmatch(url), url


def test_tags_and_versions_are_read_strictly():
    """Review R7: ASCII digits only, the whole string, and the pyproject spelling."""
    for good, suffix in (("v1.0.0", None), ("v1.0.0-rc2", "-rc2"), ("v10.2.33-beta1", "-beta1")):
        m = fr.TAG_RE.fullmatch(good)
        assert m and m.group(2) == suffix, good
    for bad in ("v1.0.0\n", " v1.0.0", "v1.0", "V1.0.0", "v١.٠.٠", "v1.0.0+build", "v1.0.0rc2"):
        assert not fr.TAG_RE.fullmatch(bad), ascii(bad)
    assert [fr.pep440(v) for v in ("1.0.0", "1.0.0-rc2", "1.0.0-alpha1", "1.0.0-beta.3", "1.0.0-preview")] == \
        ["1.0.0", "1.0.0rc2", "1.0.0a1", "1.0.0b3", None]


@requires_git
def test_a_pyproject_that_disagrees_with_the_tag_is_refused(tmp_path, monkeypatch):
    """The tag's own pyproject.toml must say its version (a release has no suffix there either)."""
    w = World(tmp_path, monkeypatch, version="1.0.0")
    _git(w.work, "checkout", "--quiet", w.rc1)
    _write(w.work, "android/release.json", _manifest(w.version, w.apks, w.rc1))
    _git(w.work, "commit", "--quiet", "-am", "android: release over rc1's files")
    _git(w.work, "tag", "-f", w.tag)
    _git(w.work, "push", "--quiet", "--force", "origin", f"refs/tags/{w.tag}")
    with pytest.raises(fr.Refused, match="pyproject.toml says 1.0.0rc1, not 1.0.0"):
        w.run("--no-publish", "--tag", w.tag)


@requires_git
def test_stable_goes_to_the_commit_checked_even_if_the_tag_moves_here(tmp_path, monkeypatch):
    """Review R2: the local tag re-pointed while the Setup builds; stable
    still gets the SHA that was checked, never the name again."""
    w = World(tmp_path, monkeypatch, version="1.0.0", stable="v1.0.0-rc1")
    _write(w.work, "unreviewed.txt", "not part of the release\n")
    _git(w.work, "add", "-A"); _git(w.work, "commit", "--quiet", "-m", "unreviewed change on main")
    unreviewed = _git(w.work, "rev-parse", "HEAD")
    _git(w.work, "push", "--quiet", "origin", "main")

    class Racy(fr.Tools):
        def git(self, *args, check=True):
            if args[:1] == ("push",):
                _git(w.work, "tag", "-f", w.tag, unreviewed)
            return super().git(*args, check=check)

    assert w.run(cls=Racy) == 0
    assert w.stable() == w.tag_commit


@requires_git
def test_stable_stays_if_the_tag_on_github_moves_before_the_push(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0", stable="v1.0.0-rc1")

    class Moved(fr.Tools):
        def gh(self, *args, check=True):
            if args[:2] == ("release", "edit"):
                subprocess.run(["git", "--git-dir", str(w.origin), "update-ref", f"refs/tags/{w.tag}", w.rc1], check=True)
            return super().gh(*args, check=check)

    with pytest.raises(fr.Refused, match="no longer names"):
        w.run(cls=Moved)
    assert w.stable() == w.rc1


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
def test_git_and_gh_never_come_from_the_checkout(tmp_path, monkeypatch):
    """Review R8: a git.exe planted in the checkout is never the git run."""
    w = World(tmp_path, monkeypatch)
    planted = w.work / ("git.exe" if os.name == "nt" else "git")
    planted.write_bytes(b"MZ planted")
    planted.chmod(0o755)
    (w.work / ("gh.bat" if os.name == "nt" else "gh")).write_bytes(b"echo planted\n")
    monkeypatch.chdir(w.work)
    monkeypatch.setenv("PATH", str(w.work) + os.pathsep + os.environ["PATH"])
    with pytest.raises(fr.Refused, match="inside the checkout"):
        fr.find_exe("git", w.work)
    monkeypatch.setenv("PATH", os.environ["PATH"].split(os.pathsep, 1)[1])
    assert not Path(fr.find_exe("git", w.work)).is_relative_to(w.work)
    assert fr.find_exe("gh", w.work) is None or not Path(fr.find_exe("gh", w.work)).is_relative_to(w.work)


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
    rel = fr.Release(tag="v9.9.9", version="9.9.9", prerelease=False, tag_commit="a" * 40,
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
    rel = fr.Release(tag="v1.0.0", version="1.0.0", prerelease=False, tag_commit="a" * 40,
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
    """The manifest is committed on the build commit (plumbing, HEAD moved
    only from the build commit: review R1), the release commit is checked to
    be that file on that commit before anything is pushed, the branch and the
    tag go up in one atomic push before gh makes the release on that tag
    (--verify-tag: review R6), a release is a full one unless -Prerelease,
    and the tag is checked to be the release commit afterwards."""
    text = (REPO / script).read_text(encoding="utf-8")
    ps1 = script.endswith(".ps1")
    commit = text.index("'commit-tree'" if ps1 else "git commit-tree")
    cas = text.index("'update-ref', '-m'" if ps1 else "git update-ref -m")
    guard = text.index("is not android/release.json on top of")
    push = text.index("'push', '--atomic', '--quiet', 'origin'" if ps1 else "git push --atomic --quiet origin")
    create = text.index("release', 'create'" if ps1 else "gh release create")
    assert guard < push < create, "checked, then pushed with its tag, then the release is made"
    fn_end = text.index("# --- carry on" if ps1 else "# ─── carry on")
    assert create < fn_end < commit < cas, "publishing is one function, called after the commit"
    if ps1:
        assert "'HEAD', $releaseCommit, $commit))" in text, "HEAD moves only from the build commit"
        assert "'--target', $ReleaseCommit, '--verify-tag'" in text
        assert "$refspecs += \"$($ReleaseCommit):refs/tags/$tag\"" in text
        assert text.count("$isPrerelease = [bool]$Prerelease") == 2
        assert "Contains('-')" not in text
        assert "-ne $ReleaseCommit) { Die" in text
        assert "'git commit'" not in text and "@('commit', " not in text
    else:
        assert 'HEAD "$release_commit" "$commit"' in text, "HEAD moves only from the build commit"
        assert '--target "$release_commit" --verify-tag' in text
        assert 'refspecs+=("$release_commit:refs/tags/$tag")' in text
        assert text.count('is_prerelease="$prerelease"\n') == 2
        assert "*-*) is_prerelease=1" not in text
        assert '[ "$tagged" = "$release_commit" ]' in text
        assert "git commit --quiet" not in text
    assert "--latest" in text and "--prerelease" in text
    assert "refs/tags/$tag^{}" in text, "a remote tag is read peeled"
    assert "DOMOVOI_RELEASE_ORIGIN_URL" in text and "coders-farm-official/domovoi(" in text
    # A run that stopped after the commit carries on (nothing signed again).
    assert "carries on from" in text
