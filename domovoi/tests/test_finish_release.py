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
SSH_KEYGEN = shutil.which("ssh-keygen")
requires_ssh_keygen = pytest.mark.skipif(SSH_KEYGEN is None, reason="no ssh-keygen")


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
    if args[:2] == ["release", "create"]:
        if rel is not None:
            save(); print("release exists", file=sys.stderr); sys.exit(1)
        on_origin = subprocess.run(["git", "--git-dir", state["origin"], "rev-parse", "--verify", "--quiet",
                                    f"refs/tags/{tag}"], capture_output=True, text=True).returncode == 0
        if "--verify-tag" not in args or not on_origin:
            save(); print("tag not on the remote", file=sys.stderr); sys.exit(1)
        rel = state["releases"][tag] = {"tagName": tag, "name": opt("--title"), "isDraft": False,
                                        "isPrerelease": "--prerelease" in args, "url": "https://example.invalid/n",
                                        "assets": [], "body": open(opt("--notes-file"), encoding="utf-8").read()}
        args = ["release", "upload", tag] + args[3:args.index("--repo")] + ["--clobber"]
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


def _manifest(version: str, apks: list[dict], commit: str, version_code: int = 26101602) -> str:
    return json.dumps({"manifest_version": 1, "release": {
        "package": "com.domovoi.app", "version_name": version, "version_code": version_code,
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
                 hand_notes: str | None = None, github_prerelease: bool = False, annotated: bool = False,
                 release_key: bytes | None = None, per_abi_codes: bool = False):
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
        if release_key is not None:
            _write(w, "windows/installer/release-signing.pub", release_key)
        _git(w, "add", "-A"); _git(w, "commit", "--quiet", "-m", "release: Domovoi")
        self.build = _git(w, "rev-parse", "HEAD")
        # The release commit: android/release.json naming its own APKs, on the build commit, tagged.
        self.apk_bytes = {f"domovoi-{version}-{abi}.apk": f"apk {abi}".encode() for abi in ("arm64-v8a", "armeabi-v7a")}
        self.apks = [{"abi": abi, "file": f"domovoi-{version}-{abi}.apk",
                      "url": f"https://github.com/coders-farm-official/domovoi/releases/download/v{version}/domovoi-{version}-{abi}.apk",
                      "sha256": hashlib.sha256(f"apk {abi}".encode()).hexdigest(), "size": len(f"apk {abi}".encode())}
                     for abi in ("arm64-v8a", "armeabi-v7a")]
        # Per-ABI versionCodes (since 2026-11-14): each APK base*10 + its
        # ABI's digit, the release's code base*10; the base is 26101602.
        if per_abi_codes:
            for e in self.apks:
                e["version_code"] = 261016020 + {"armeabi-v7a": 1, "arm64-v8a": 2}[e["abi"]]
        _write(w, "android/release.json", _manifest(version, self.apks, self.build,
                                                     261016020 if per_abi_codes else 26101602))
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
    assert "| DomovoiTray.exe |" in notes and tray_sha in notes and "from before the tray icon" in notes


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
def test_docs_release_at_the_tag_is_the_full_notes_and_its_glance_the_page(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, hand_notes=FULL_NOTES)
    assert w.run("--no-publish") == 0
    notes = (w.stage / "NOTES.md").read_text(encoding="utf-8")
    assert "- **A highlight**, in a line." in notes and "## Security fixes\n" in notes
    assert "The long intro" not in notes and "The long story" not in notes, "the full notes stay in the file"
    assert "# Domovoi" not in notes.replace("## ", "")
    blob = f"https://github.com/coders-farm-official/domovoi/blob/{w.tag}"
    assert (f"## What's new since 1.0.0-rc1\n\n**More detail:** [the full 1.0.0-rc2 release notes]"
            f"({blob}/docs/release/{w.tag}.md) and [every change in 1.0.0-rc2]({blob}/docs/RELEASE_NOTES.md).") in notes
    # The house order: the lead (a candidate's), Download, What's new, the glance, the files last.
    order = [notes.index(s) for s in ("is a candidate for", "## Download", "## What's new", "**More detail:**",
                                      "A highlight", "## Security fixes", "## Files and checksums",
                                      "| DomovoiSetup.exe |")]
    assert order == sorted(order)


FULL_NOTES = """# Domovoi 1.0.0-rc2

The long intro of the full notes.

## At a glance

- **A highlight**, in a line.
- Another.

### Security fixes

- **package 1.2.3:** one line.

```
### not a heading: code
```

## What is new since 1.0.0-rc1

The long story.
"""


@requires_git
@pytest.mark.parametrize("hand, why", [
    ("# Domovoi 1.0.0\n\nOnly the long form.\n", "needs exactly one \"## At a glance\" section (it has 0)"),
    ("## At a glance\n\n- x\n\n## At a glance\n\n- y\n", "(it has 2)"),
    ("## At a glance\n\nProse first.\n\n- x\n", "must start with the highlights"),
    ("## At a glance\n\n- x\n\n### Download\n\n- y\n", "has a \"Download\" heading"),
    ("## At a glance\n\n- x\n\n### Files\n\n- y\n", "has a \"Files\" heading"),
    ("## At a glance\n\n- x\n\n### What's new in rooms\n\n- y\n", "has a \"What's new in rooms\" heading"),
])
def test_a_docs_release_not_in_the_house_style_is_refused(tmp_path, monkeypatch, hand, why):
    w = World(tmp_path, monkeypatch, hand_notes=hand)
    with pytest.raises(fr.Refused, match=re.escape(why)):
        w.run("--no-publish")
    assert w.calls("edit") == [] and w.calls("upload") == []


def test_the_house_style_check_takes_full_notes_with_a_glance():
    assert fr.release_doc_problems(FULL_NOTES) == []
    assert fr.release_doc_problems(FULL_NOTES.replace("\n", "\r\n")) == []
    page = fr.glance_notes(FULL_NOTES, since="v1.0.0-rc1")
    assert page.startswith("## What's new since 1.0.0-rc1\n\n- **A highlight**")
    assert "\n## Security fixes\n" in page and "\n### not a heading: code\n" in page, "code stays as it is"
    assert "long" not in page


def test_more_detail_links_the_full_notes_and_the_release_s_dated_section():
    """The real 1.3.0 notes: the anchor GitHub gives ``## 2026-11-16 — 1.3.0``
    (the line the live 1.3.0 page carries)."""
    text = (REPO / "docs" / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert fr.notes_anchor(text, "1.3.0") == "2026-11-16--130"
    assert fr.notes_anchor(text, "9.9.9") is None
    rel = _release(tag="v1.3.0", version="1.3.0", full_notes=True, notes_anchor="2026-11-16--130",
                   notes="## What's new since 1.2.1\n\n- A highlight.\n")
    line = ("**More detail:** [the full 1.3.0 release notes](https://github.com/coders-farm-official/domovoi/blob/"
            "v1.3.0/docs/release/v1.3.0.md) and [every change in 1.3.0](https://github.com/coders-farm-official/"
            "domovoi/blob/v1.3.0/docs/RELEASE_NOTES.md#2026-11-16--130).")
    assert fr.more_detail(rel) == line
    assert f"## What's new since 1.2.1\n\n{line}\n\n- A highlight.\n" in fr.body(rel)
    # Without docs/release/<tag>.md (a summary of RELEASE_NOTES.md), only the dated section.
    rel.full_notes = False
    assert fr.more_detail(rel) == ("**More detail:** [every change in 1.3.0](https://github.com/coders-farm-official/"
                                   "domovoi/blob/v1.3.0/docs/RELEASE_NOTES.md#2026-11-16--130).")


@requires_git
def test_a_summary_release_gets_the_more_detail_line_under_what_s_new(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    assert w.run("--no-publish") == 0
    notes = (w.stage / "NOTES.md").read_text(encoding="utf-8")
    assert "## What's new since v1.0.0-rc1\n\n**More detail:** [every change in 1.0.0-rc2](" in notes
    assert "docs/release/" not in notes


def _release(**kw) -> "fr.Release":
    base = dict(tag="v2.0.0", version="2.0.0", prerelease=False, tag_commit="a" * 40, cert=CERT,
                apks=[{"abi": "arm64-v8a", "file": "domovoi-2.0.0-arm64-v8a.apk", "sha256": "1" * 64},
                      {"abi": "armeabi-v7a", "file": "domovoi-2.0.0-armeabi-v7a.apk", "sha256": "2" * 64}],
                setup_sha256="3" * 64, satellite_setup_sha256="4" * 64, tray_sha256="5" * 64,
                linux_install="curl -fsSL https://example.invalid/scripts/linux/install.sh | sudo bash",
                notes="## What's new\n\n- Something.\n")
    base.update(kw)
    return fr.Release(**base)


def test_download_comes_first_with_a_direct_link_to_each_file():
    text = fr.body(_release())
    assert text.startswith("## Download\n"), "right under the title: no lead for a plain release"
    dl = text[:text.index("## What's new")]
    get = "https://github.com/coders-farm-official/domovoi/releases/download/v2.0.0/"
    for name in ("DomovoiSetup.exe", "domovoi-2.0.0-arm64-v8a.apk", "domovoi-2.0.0-armeabi-v7a.apk",
                 "DomovoiSatelliteSetup.exe"):
        assert f"[{name}]({get}{name})" in dl
    assert "Windows 11 PC — the server" in dl and "Which one?" in dl and fr.PLAY_URL in dl
    assert "  curl -fsSL https://example.invalid/scripts/linux/install.sh | sudo bash\n" in dl
    assert fr.UPDATE_LINE in dl and "Raspberry Pi room" in dl
    assert "Windows protected your PC" in dl
    # Everything else comes after the summary: the checks, then the files.
    assert text.index("## What's new") < text.index("## Files and checksums") < text.index("| DomovoiSetup.exe |")
    assert "<summary>Every file, with its SHA-256</summary>\n\n|" in text, "a blank line, or GitHub shows raw Markdown"


def test_download_without_a_room_setup_or_a_linux_line_still_says_where():
    text = fr.body(_release(satellite_setup_sha256=None, linux_install=None))
    dl = text[:text.index("## What's new")]
    assert "DomovoiSatelliteSetup.exe" not in dl and "Raspberry Pi room" in dl
    assert "curl" not in dl and "/blob/v2.0.0/docs/LINUX_HOST.md#one-command" in dl
    assert "The Windows Setup is not code-signed" in dl


def test_a_server_only_release_links_the_apks_on_the_release_that_holds_them():
    app_from = {"version_name": "1.9.0", "version_code": 26101602, "tag": "v1.9.0",
                "apks": [{"abi": "arm64-v8a", "file": "domovoi-1.9.0-arm64-v8a.apk"},
                         {"abi": "armeabi-v7a", "file": "domovoi-1.9.0-armeabi-v7a.apk"}]}
    text = "\n".join(fr.download(_release(apks=[], app_from=app_from)))
    assert "/releases/download/v1.9.0/domovoi-1.9.0-arm64-v8a.apk" in text
    assert "/releases/download/v1.9.0/domovoi-1.9.0-armeabi-v7a.apk" in text
    assert "/releases/download/v2.0.0/domovoi-" not in text


def test_the_linux_line_is_copied_from_linux_host_md_not_retyped():
    doc = (REPO / "docs" / "LINUX_HOST.md").read_text(encoding="utf-8")
    line = fr.linux_install_line(doc)
    assert line and line in doc.splitlines() and line.endswith("install.sh | sudo bash")
    assert fr.linux_install_line("no command here") is None


@pytest.mark.parametrize("doc", [fr.LINUX_DOC, fr.PI_ROOM_DOC])
def test_the_docs_the_download_links_have_their_headings(doc):
    path, heading = doc
    text = (REPO / path).read_text(encoding="utf-8")
    assert heading in [fr._heading_text(h) for _, _, h in fr._headings(text)], f"{path} has no heading {heading!r}"


def test_release_docs_after_1_3_0_are_in_the_house_style():
    """docs/release/v<version>.md after 1.3.0 (1.3.0 and before are the full
    notes their tags hold, from before the glance; their pages are set)."""
    for p in sorted((REPO / "docs" / "release").glob("v*.md")):
        v = re.fullmatch(r"v([0-9]+)\.([0-9]+)\.([0-9]+)(-.*)?", p.stem)
        if v and tuple(int(x) for x in v.groups()[:3]) > (1, 3, 0):
            assert fr.release_doc_problems(p.read_text(encoding="utf-8")) == [], p.name


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
    for flag in ("--tag", "--no-publish", "--prerelease", "--setup", "--since"):
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


# ─── the Windows update's signatures ─────────────────────────────────────


def _keypair(tmp_path: Path, name: str) -> tuple[Path, bytes]:
    """A throwaway ed25519 key (no passphrase), outside the checkout."""
    d = tmp_path / "keys"
    d.mkdir(exist_ok=True)
    key = d / name
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "throwaway " + name, "-f", str(key)], check=True)
    return key, (d / (name + ".pub")).read_bytes()


def _verify(sig: Path, data: Path, public: bytes, ns: str, tmp_path: Path) -> bool:
    allowed = tmp_path / "allowed"
    parts = public.decode().split()
    allowed.write_bytes(f"x {parts[0]} {parts[1]}\n".encode())
    with open(data, "rb") as f:
        return subprocess.run([SSH_KEYGEN, "-Y", "verify", "-f", str(allowed), "-I", "x", "-n", ns, "-s", str(sig)],
                              stdin=f, capture_output=True).returncode == 0


@requires_git
@requires_ssh_keygen
def test_a_tag_that_pins_the_release_key_gets_its_setup_signed(tmp_path, monkeypatch):
    """The Setup, its manifest, and both Ed25519 signatures (SSHSIG, each
    under its own namespace), made with the key's private half by
    ssh-keygen, checked against the tag's pinned public key, in SHA256SUMS
    and uploaded after the Setup."""
    key, public = _keypair(tmp_path, "release")
    w = World(tmp_path, monkeypatch, version="1.0.0", release_key=public)
    assert w.run("--signing-key", str(key)) == 0
    stage = w.stage
    manifest = json.loads((stage / "DomovoiSetup.manifest.json").read_bytes())
    setup = stage / "DomovoiSetup.exe"
    assert manifest == {"kind": "domovoi-windows-setup", "schema": 1, "tag": w.tag, "version": "1.0.0",
                        "commit": w.tag_commit, "file": "DomovoiSetup.exe", "size": setup.stat().st_size,
                        "sha256": hashlib.sha256(setup.read_bytes()).hexdigest()}
    assert b"\r" not in (stage / "DomovoiSetup.manifest.json").read_bytes()
    assert _verify(stage / "DomovoiSetup.exe.sig", setup, public, "domovoi-windows-setup", tmp_path)
    assert _verify(stage / "DomovoiSetup.manifest.json.sig", stage / "DomovoiSetup.manifest.json", public,
                   "domovoi-windows-setup-manifest", tmp_path)
    assert not _verify(stage / "DomovoiSetup.exe.sig", setup, public, "domovoi-windows-setup-manifest", tmp_path), \
        "the namespaces keep the two signatures apart"
    names = [line.split("  ", 1)[1] for line in (stage / "SHA256SUMS").read_text(encoding="ascii").splitlines()]
    assert names[-4:] == ["DomovoiSetup.exe", "DomovoiSetup.exe.sig", "DomovoiSetup.manifest.json",
                          "DomovoiSetup.manifest.json.sig"]
    (upload,) = w.calls("upload")
    assert [Path(a).name for a in upload[3:8]] == ["DomovoiSetup.exe", "DomovoiSetup.exe.sig", "DomovoiSetup.manifest.json",
                                                   "DomovoiSetup.manifest.json.sig", "SHA256SUMS"]
    notes = w.state["releases"][w.tag]["body"]
    assert "| DomovoiSetup.exe.sig |" in notes and "ssh-keygen -Y verify" in notes
    assert not (stage / ".allowed_signers").exists()
    assert key.read_bytes(), "the key stays where it was"


@requires_git
@requires_ssh_keygen
def test_a_tag_that_pins_the_key_is_refused_without_it_or_with_another(tmp_path, monkeypatch):
    key, public = _keypair(tmp_path, "release")
    other, _ = _keypair(tmp_path, "other")
    w = World(tmp_path, monkeypatch, version="1.0.0", release_key=public)
    monkeypatch.delenv("DOMOVOI_RELEASE_SIGNING_KEY", raising=False)
    with pytest.raises(fr.Refused, match="must be signed: pass --signing-key"):
        w.run()
    assert w.calls("upload") == [] and w.calls("edit") == [] and w.stable() == "", "refused before GitHub"
    # A dry run without the key goes on, says so, and signs nothing.
    assert w.run("--no-publish") == 0
    assert not (w.stage / "DomovoiSetup.exe.sig").exists()
    assert any("no signing key" in m for m in w.said)
    with pytest.raises(fr.Refused, match="doesn't verify against the tag's windows/installer/release-signing.pub"):
        w.run("--no-publish", "--signing-key", str(other))
    with pytest.raises(fr.Refused, match="no release key at"):
        w.run("--no-publish", "--signing-key", str(tmp_path / "missing"))
    inside = w.work / "build" / "release-key"   # ignored by git, still inside
    inside.parent.mkdir(exist_ok=True)
    shutil.copyfile(key, inside)
    with pytest.raises(fr.Refused, match="inside the checkout"):
        w.run("--no-publish", "--signing-key", str(inside))
    assert w.calls("upload") == []
    monkeypatch.setenv("DOMOVOI_RELEASE_SIGNING_KEY", str(key))
    assert w.run("--no-publish") == 0, "the environment names it too"
    assert (w.stage / "DomovoiSetup.exe.sig").is_file()


@requires_git
def test_a_tag_without_the_key_is_released_without_signatures(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    monkeypatch.delenv("DOMOVOI_RELEASE_SIGNING_KEY", raising=False)
    assert w.run("--no-publish") == 0
    assert not (w.stage / "DomovoiSetup.exe.sig").exists() and not (w.stage / "DomovoiSetup.manifest.json").exists()
    assert any("no Windows update signatures" in m for m in w.said)
    with pytest.raises(fr.Refused, match="the tag pins no release key"):
        w.run("--no-publish", "--signing-key", str(tmp_path / "whatever"))


def test_the_signature_files_are_known_to_the_release_check():
    rel = {"assets": [{"name": n} for n in ("DomovoiSetup.exe.sig", "DomovoiSetup.manifest.json",
                                             "DomovoiSetup.manifest.json.sig")]}
    with pytest.raises(fr.Refused, match="has no a.apk"):
        fr._check_published_apks(rel, [{"file": "a.apk", "size": 1, "sha256": "1" * 64}])


# ─── a release that doesn't change the app (path b) ──────────────────────

def _server_only(w: World, version: str = "1.0.1", *, gradle: str | None = None, manifest: str | None = None,
                 push: bool = True) -> str:
    """On top of World's app release (v1.0.0, its APKs on GitHub): the
    server-only release commit, pyproject at ``version``, the app and
    android/release.json as v1.0.0 left them. Pushed to main unless not."""
    _write(w.work, "pyproject.toml", _pyproject(version))
    _write(w.work, "android/app/build.gradle.kts",
           gradle if gradle is not None else '        versionCode = 26101602\n        versionName = "1.0.0"\n')
    if manifest is not None:
        _write(w.work, "android/release.json", manifest)
    _git(w.work, "commit", "--quiet", "-am", f"Release {version}")
    head = _git(w.work, "rev-parse", "HEAD")
    if push:
        _git(w.work, "push", "--quiet", "origin", "main")
        _git(w.work, "fetch", "--quiet", "origin")
    w.tag = f"v{version}"
    return head


def _local_tag(w: World, tag: str) -> str:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"], cwd=w.work,
                          capture_output=True, text=True).stdout.strip()


def _origin_has_tag(w: World, tag: str) -> bool:
    return subprocess.run(["git", "--git-dir", str(w.origin), "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"],
                          capture_output=True).returncode == 0


@requires_git
def test_a_server_only_dry_run_tags_nothing_and_ships_no_apks(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    head = _server_only(w)
    before = (w.work / "android" / "release.json").read_bytes()
    assert w.run("--no-publish") == 0, "the tag is v + pyproject's version"
    assert _local_tag(w, "v1.0.1") == "", "the tag made for the build is gone again"
    assert not _origin_has_tag(w, "v1.0.1")
    assert w.calls("create") == [] and w.calls("upload") == [] and w.calls("edit") == []
    sums = (w.stage / "SHA256SUMS").read_text(encoding="ascii")
    assert ".apk" not in sums and "DomovoiSetup.exe" in sums
    notes = (w.stage / "NOTES.md").read_text(encoding="utf-8")
    assert head in notes and "still 1.0.0 (versionCode 26101602)" in notes and "releases/tag/v1.0.0" in notes
    assert ".apk |" not in notes
    assert "(https://github.com/coders-farm-official/domovoi/releases/download/v1.0.0/domovoi-1.0.0-arm64-v8a.apk)" \
        in notes, "Download links the app's APKs on the release that holds them"
    assert (w.work / "android" / "release.json").read_bytes() == before
    assert any("the app is unchanged" in m for m in w.said)


@requires_git
def test_a_server_only_release_tags_head_creates_the_release_and_moves_stable(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    head = _server_only(w)
    assert w.run() == 0
    origin_tag = subprocess.run(["git", "--git-dir", str(w.origin), "rev-parse", "refs/tags/v1.0.1^{commit}"],
                                capture_output=True, text=True).stdout.strip()
    assert origin_tag == head and _local_tag(w, "v1.0.1") == head
    rel = w.load()["releases"]["v1.0.1"]
    assert sorted(a["name"] for a in rel["assets"]) == ["DomovoiSetup.exe", "SHA256SUMS"]
    assert rel["name"] == "Domovoi v1.0.1" and rel["isPrerelease"] is False
    assert w.stable() == head
    assert w.load()["releases"]["v1.0.0"]["assets"][0]["name"].endswith(".apk"), "the app's release untouched"
    # The manifest the server reads still fetches from v1.0.0.
    shown = json.loads(_git(w.work, "show", "v1.0.1:android/release.json"))
    assert {e["url"].split("/download/")[1].split("/")[0] for e in shown["release"]["apks"]} == {"v1.0.0"}
    # A second run finds the tag and the release, and replaces the files.
    assert w.run() == 0
    assert len(w.calls("create")) == 1 and w.calls("upload")


@requires_git
def test_a_server_only_pre_release_is_marked_and_leaves_stable(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0", stable="v1.0.0")
    _server_only(w)
    assert w.run("--prerelease") == 0
    assert w.load()["releases"]["v1.0.1"]["isPrerelease"] is True
    assert w.stable() == w.tag_commit


@pytest.mark.parametrize("change, why", [
    ({"gradle": '        versionCode = 26101701\n        versionName = "1.0.1"\n'}, "the app changed"),
    ({"manifest": '{"manifest_version": 1, "release": null}\n'}, "names no release"),
    ({"push": False}, "not on origin/main"),
])
@requires_git
def test_a_server_only_release_that_is_not_one_is_refused_before_github(tmp_path, monkeypatch, change, why):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    _server_only(w, **change)
    with pytest.raises(fr.Refused, match=why):
        w.run()
    assert _local_tag(w, "v1.0.1") == "", "no tag left behind"
    assert w.calls("create") == [] and w.calls("upload") == [] and w.stable() == ""
    assert not _origin_has_tag(w, "v1.0.1")


@requires_git
def test_a_manifest_that_differs_from_the_app_release_s_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    doc = json.loads((w.work / "android" / "release.json").read_text(encoding="utf-8"))
    doc["release"]["published"] = "2026-11-14"
    _server_only(w, manifest=json.dumps(doc, indent=2) + "\n")
    with pytest.raises(fr.Refused, match="is not the one v1.0.0 released"):
        w.run("--no-publish")


@requires_git
def test_a_server_only_release_whose_app_release_lacks_its_apks_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    _server_only(w)
    w.state["releases"]["v1.0.0"]["assets"] = [a for a in w.state["releases"]["v1.0.0"]["assets"]
                                              if "arm64" not in a["name"]]
    w.save()
    with pytest.raises(fr.Refused, match="has no domovoi-1.0.0-arm64-v8a.apk"):
        w.run()
    assert _local_tag(w, "v1.0.1") == ""


@requires_git
def test_a_tag_already_on_github_elsewhere_never_moves(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    _server_only(w)
    _git(w.work, "push", "--quiet", "origin", f"{w.tag_commit}:refs/tags/v1.0.1")
    with pytest.raises(fr.Refused):
        w.run()
    assert w.calls("create") == []


@requires_git
def test_path_a_still_needs_release_ps1_s_tag(tmp_path, monkeypatch):
    """A HEAD whose manifest names the tag being cut: release.ps1 makes it."""
    w = World(tmp_path, monkeypatch, version="1.0.0")
    _git(w.work, "tag", "-d", "v1.0.0")
    _git(w.work, "push", "--quiet", "origin", ":refs/tags/v1.0.0")
    with pytest.raises(fr.Refused, match="android/scripts/release.ps1 publishes the release and makes the tag"):
        w.run("--no-publish")


def test_the_default_tag_is_pyproject_s(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.1"\n', encoding="utf-8")
    assert fr._default_tag(tmp_path) == "v1.2.1"
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0rc2"\n', encoding="utf-8")
    assert fr._default_tag(tmp_path) == "v1.0.0-rc2"
    for v in ("1.0.0", "1.0.0-rc2", "2.0.0-alpha1", "2.0.0-beta3"):
        assert fr.semver(fr.pep440(v)) == v


# ─── the release workflow: --signing-agent and --plan ────────────────────

def _agent_tools() -> Path | None:
    """A folder holding ssh-agent, ssh-add and ssh-keygen of one OpenSSH
    whose ssh-agent runs on its own (``-s``): Git for Windows' or a POSIX
    one. Windows' own OpenSSH runs its agent as a service, so it is passed
    over."""
    exe = ".exe" if os.name == "nt" else ""
    dirs = []
    if shutil.which("ssh-agent"):
        dirs.append(Path(shutil.which("ssh-agent")).parent)
    if os.name == "nt" and GIT:
        dirs.append(Path(GIT).resolve().parents[1] / "usr" / "bin")
    for d in dirs:
        if "system32" in str(d).lower():
            continue
        if all((d / (n + exe)).is_file() for n in ("ssh-agent", "ssh-add", "ssh-keygen")):
            return d
    return None


AGENT_DIR = _agent_tools()
requires_agent = pytest.mark.skipif(AGENT_DIR is None, reason="no ssh-agent that runs on its own")

ASKPASS = b"#!/bin/sh\nprintf '%s\\n' \"$THROWAWAY_PASSPHRASE\"\n"


@pytest.fixture
def agent(tmp_path, monkeypatch):
    """A throwaway, passphrase-protected ed25519 key, loaded into a private
    ssh-agent the way the release workflow does it (the key text on
    ssh-add's stdin, the passphrase from SSH_ASKPASS with
    SSH_ASKPASS_REQUIRE=force, no terminal), then its file deleted: the
    agent is the only place the private half is. Yields its public line."""
    d = tmp_path / "agent-keys"
    d.mkdir()
    keygen = str(AGENT_DIR / "ssh-keygen")
    passphrase = "throwaway passphrase, test only"
    subprocess.run([keygen, "-q", "-t", "ed25519", "-N", passphrase, "-C", "throwaway", "-f", str(d / "k")], check=True)
    key_text = (d / "k").read_bytes()
    (d / "k").unlink()
    public = (d / "k.pub").read_bytes()
    out = subprocess.run([str(AGENT_DIR / "ssh-agent"), "-s"], capture_output=True, text=True, check=True).stdout
    env = dict(re.findall(r"(SSH_AUTH_SOCK|SSH_AGENT_PID)=([^;]+);", out))
    askpass = d / "askpass.sh"
    askpass.write_bytes(ASKPASS)
    askpass.chmod(0o700)
    add_env = {**os.environ, **env, "SSH_ASKPASS": str(askpass), "SSH_ASKPASS_REQUIRE": "force",
               "THROWAWAY_PASSPHRASE": passphrase}
    add_env.pop("DISPLAY", None)
    try:
        subprocess.run([str(AGENT_DIR / "ssh-add"), "-q", "-"], input=key_text, env=add_env, check=True,
                       capture_output=True)
        listed = subprocess.run([str(AGENT_DIR / "ssh-add"), "-L"], env={**os.environ, **env},
                                capture_output=True, text=True).stdout
        assert public.decode().split()[1] in listed
        monkeypatch.setenv("SSH_AUTH_SOCK", env["SSH_AUTH_SOCK"])
        monkeypatch.delenv("DOMOVOI_RELEASE_SIGNING_KEY", raising=False)
        # finish-release finds ssh-keygen on PATH: the agent's own first.
        monkeypatch.setenv("PATH", str(AGENT_DIR) + os.pathsep + os.environ.get("PATH", ""))
        yield public
    finally:
        subprocess.run([str(AGENT_DIR / "ssh-agent"), "-k"], env={**os.environ, **env}, capture_output=True)


@requires_git
@requires_agent
def test_signing_agent_signs_with_the_agent_s_key_without_a_prompt_or_a_key_file(tmp_path, monkeypatch, agent):
    public = agent
    w = World(tmp_path, monkeypatch, version="1.0.0", release_key=public)
    assert w.run("--signing-agent") == 0
    stage = w.stage
    assert _verify(stage / "DomovoiSetup.exe.sig", stage / "DomovoiSetup.exe", public, "domovoi-windows-setup", tmp_path)
    assert _verify(stage / "DomovoiSetup.manifest.json.sig", stage / "DomovoiSetup.manifest.json", public,
                   "domovoi-windows-setup-manifest", tmp_path)
    assert not (stage / ".release-signing.pub").exists() and not (stage / ".allowed_signers").exists()
    assert not (tmp_path / "agent-keys" / "k").exists(), "no private key file anywhere"
    (upload,) = w.calls("upload")
    assert "DomovoiSetup.manifest.json.sig" in [Path(a).name for a in upload]


@requires_git
@requires_agent
def test_signing_agent_refuses_an_agent_without_the_tag_s_key(tmp_path, monkeypatch, agent):
    _, other = _keypair(tmp_path, "other")
    w = World(tmp_path, monkeypatch, version="1.0.0", release_key=other)
    with pytest.raises(fr.Refused, match="does the agent hold the private half"):
        w.run("--signing-agent")
    assert w.calls("upload") == [] and w.stable() == ""


@requires_git
def test_signing_agent_needs_an_agent_and_a_pinned_key_and_not_a_key_file(tmp_path, monkeypatch):
    w = World(tmp_path / "p", monkeypatch, version="1.0.0",
              release_key=b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA x\n")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    with pytest.raises(fr.Refused, match="no SSH_AUTH_SOCK"):
        w.run("--signing-agent")
    with pytest.raises(fr.Refused, match="not both"):
        w.run("--signing-agent", "--signing-key", str(tmp_path / "k"))
    assert w.calls("upload") == []
    (tmp_path / "u").mkdir()
    unpinned = World(tmp_path / "u", monkeypatch, version="1.0.0")
    with pytest.raises(fr.Refused, match="--signing-agent given, but the tag pins no release key"):
        unpinned.run("--signing-agent")


def _plan(w: World, *extra: str) -> dict:
    out = w.tmp / "plan.json"
    out.unlink(missing_ok=True)
    assert fr.run(["--plan", str(out), *extra], tools=w.tools(), repo=w.work) == 0
    return json.loads(out.read_text(encoding="utf-8"))


def _app_release_commit(w: World, head: str, tag: str) -> str:
    """What release.ps1 makes: android/release.json naming ``tag``, on HEAD."""
    doc = json.loads((w.work / "android" / "release.json").read_text(encoding="utf-8"))
    doc["release"].update(tag=tag, commit=head, version_name="1.0.1", version_code=26101701)
    _write(w.work, "android/release.json", json.dumps(doc, indent=2) + "\n")
    _git(w.work, "commit", "--quiet", "-am", "android: release 1.0.1")
    return _git(w.work, "rev-parse", "HEAD")


APP_1_0_1 = '        versionCode = 26101701\n        versionName = "1.0.1"\n'


@requires_git
def test_plan_a_server_only_release_finishes_at_head(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    head = _server_only(w)
    doc = _plan(w)
    assert doc == {"tag": "v1.0.1", "version": "1.0.1", "head": head, "path": "server", "sign_app": False,
                   "finish_at": head}
    assert _local_tag(w, "v1.0.1") == "" and not _origin_has_tag(w, "v1.0.1"), "a plan changes nothing"
    assert w.load()["calls"] == [], "nor reads GitHub's releases"


@requires_git
def test_plan_refuses_a_release_doc_not_in_the_house_style(tmp_path, monkeypatch):
    """Before anyone approves the run: the page part of the full notes is checked."""
    w = World(tmp_path, monkeypatch, version="1.0.0")
    _write(w.work, "docs/release/v1.0.1.md", "# Domovoi 1.0.1\n\nIntro.\n")
    _git(w.work, "add", "docs/release/v1.0.1.md")
    _server_only(w)
    with pytest.raises(fr.Refused, match="docs/release/v1.0.1.md: it needs exactly one"):
        _plan(w)


@requires_git
def test_plan_an_app_release_signs_it_first(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    head = _server_only(w, gradle=APP_1_0_1)
    doc = _plan(w)
    assert doc["path"] == "app" and doc["sign_app"] is True and doc["finish_at"] == head
    assert doc["app"] == {"version_name": "1.0.1", "version_code": 26101701}


@pytest.mark.parametrize("gradle, path", [
    ('        versionCode = 26101602\n        versionName = "1.0.0"\n', "server"),
    (APP_1_0_1, "app"),
])
@requires_git
def test_plan_reads_a_per_abi_manifest_by_its_base(tmp_path, monkeypatch, gradle, path):
    """A manifest with per-ABI versionCodes names base*10 at the top: the app
    is unchanged when build.gradle.kts still says that base."""
    w = World(tmp_path, monkeypatch, version="1.0.0", per_abi_codes=True)
    _server_only(w, gradle=gradle)
    assert _plan(w)["path"] == path
    assert fr._released_base({"version_code": 261016020, "apks": w.apks}) == 26101602
    assert fr._released_base({"version_code": 26101602, "apks": [{"abi": "arm64-v8a"}]}) == 26101602


@requires_git
def test_a_server_only_release_after_a_per_abi_app_release_finishes(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0", per_abi_codes=True)
    _server_only(w)
    assert w.run() == 0


@pytest.mark.parametrize("change, why", [
    ({"push": False}, "not origin/main's tip"),
    ({"gradle": '        versionCode = 26101501\n        versionName = "1.0.1"\n'}, "not above the last release's"),
    ({"manifest": '{"manifest_version": 1, "release": null}\n'}, "only android/scripts/release.ps1 writes it"),
])
@requires_git
def test_plan_refuses_what_is_not_a_release_to_make(tmp_path, monkeypatch, change, why):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    _server_only(w, **change)
    with pytest.raises(fr.Refused, match=why):
        _plan(w)


@requires_git
def test_plan_needs_main_s_tip_to_be_the_release_commit(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    _server_only(w, push=False)
    _write(w.work, "README.md", "after the release commit\n")
    _git(w.work, "add", "-A"); _git(w.work, "commit", "--quiet", "-m", "later")
    _git(w.work, "push", "--quiet", "origin", "main")
    with pytest.raises(fr.Refused, match="not a release commit"):
        _plan(w)


@requires_git
def test_plan_resumes_a_run_that_stopped_after_tagging(tmp_path, monkeypatch):
    (tmp_path / "b").mkdir()
    w = World(tmp_path / "b", monkeypatch, version="1.0.0")
    head = _server_only(w)
    # Path b: finish-release pushed the tag, then stopped.
    _git(w.work, "push", "--quiet", "origin", f"{head}:refs/tags/v1.0.1")
    doc = _plan(w)
    assert doc["path"] == "server" and doc["sign_app"] is False and doc["finish_at"] == head
    # Path a, its release commit published by an earlier attempt of the run
    # (re-run on the commit it was dispatched on): finish at that commit.
    (tmp_path / "a").mkdir()
    w2 = World(tmp_path / "a", monkeypatch, version="1.0.0")
    head2 = _server_only(w2, gradle=APP_1_0_1)
    release_commit = _app_release_commit(w2, head2, "v1.0.1")
    _git(w2.work, "push", "--quiet", "origin", f"{release_commit}:refs/tags/v1.0.1", f"{release_commit}:refs/heads/main")
    _git(w2.work, "reset", "--quiet", "--hard", head2)
    doc = _plan(w2)
    assert doc["path"] == "app" and doc["sign_app"] is False and doc["finish_at"] == release_commit
    # Dispatched again on main, now the release commit: finish there.
    _git(w2.work, "reset", "--quiet", "--hard", release_commit)
    doc = _plan(w2)
    assert doc["path"] == "app" and doc["sign_app"] is False and doc["finish_at"] == release_commit


@requires_git
def test_plan_refuses_a_tag_on_github_elsewhere_or_a_finished_release(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    # main's tip after v1.0.0, no new version: v1.0.0 is on GitHub, before HEAD.
    with pytest.raises(fr.Refused, match="neither HEAD"):
        _plan(w)
    _server_only(w)
    _git(w.work, "push", "--quiet", "origin", f"{w.tag_commit}:refs/tags/v1.0.1")
    with pytest.raises(fr.Refused, match="neither HEAD"):
        _plan(w)


# ─── the SBOMs (--sbom-dir) and the provenance line (--provenance) ───────

def _sboms(d: Path, version: str, app: str | None = None, *, extra: dict[str, bytes] | None = None) -> Path:
    """What release.yml's sbom job hands finish-release: CycloneDX JSON
    naming the release (the app's: its versionName)."""
    d.mkdir(parents=True, exist_ok=True)
    named = {fr.SERVER_SBOM_ASSET: version, fr.WEB_SBOM_ASSET: version}
    if app is not None:
        named[fr.ANDROID_SBOM_ASSET] = app
    for name, v in named.items():
        doc = {"bomFormat": "CycloneDX", "specVersion": "1.6", "metadata": {"component": {"name": name, "version": v}}}
        (d / name).write_text(json.dumps(doc) + "\n", encoding="utf-8")
    for name, data in (extra or {}).items():
        (d / name).write_bytes(data)
    return d


@requires_git
def test_the_sboms_are_attached_summed_and_listed(tmp_path, monkeypatch):
    """Path a: the server's, the web's and the app's SBOM go up before
    SHA256SUMS, which lists them; the notes name them and, with
    --provenance, how to check where a file was built. A re-run finds them
    on the release and takes them as its own."""
    w = World(tmp_path, monkeypatch, version="1.0.0")
    d = _sboms(tmp_path / "sboms", "1.0.0", app="1.0.0")
    assert w.run("--sbom-dir", str(d), "--provenance") == 0
    sha = {n: hashlib.sha256((d / n).read_bytes()).hexdigest() for n in fr.SBOM_ASSETS}
    sums = (w.stage / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert sums[-3:] == [f"{sha[n]}  {n}" for n in fr.SBOM_ASSETS]
    (upload,) = w.calls("upload")
    assert [Path(a).name for a in upload[3:8]] == ["DomovoiSetup.exe", *fr.SBOM_ASSETS, "SHA256SUMS"]
    rel = w.load()["releases"][w.tag]
    assert set(fr.SBOM_ASSETS) <= {a["name"] for a in rel["assets"]}
    for n in fr.SBOM_ASSETS:
        assert f"| {n} |" in rel["body"] and sha[n] in rel["body"]
    assert "`gh attestation verify <file> -R coders-farm-official/domovoi`" in rel["body"]
    assert "SECURITY.md#verifying-a-release" in rel["body"]
    assert w.run("--sbom-dir", str(d), "--provenance") == 0


@requires_git
def test_without_provenance_the_notes_promise_none(tmp_path, monkeypatch):
    """A local run (the fallback) makes no attestation, so its notes don't
    say there is one, and it has no SBOMs to attach."""
    w = World(tmp_path, monkeypatch, version="1.0.0")
    assert w.run() == 0
    rel = w.load()["releases"][w.tag]
    assert "gh attestation" not in rel["body"] and ".cdx.json" not in rel["body"]
    assert not any(n.endswith(".cdx.json") for n in (w.stage / "SHA256SUMS").read_text(encoding="ascii").split())


@requires_git
def test_a_server_only_release_takes_the_server_and_web_sboms_only(tmp_path, monkeypatch):
    """Path b carries no APKs, so no app SBOM: its APKs' release has it."""
    w = World(tmp_path, monkeypatch, version="1.0.0")
    _server_only(w)
    with pytest.raises(fr.Refused, match="carries no APKs, so no app SBOM"):
        w.run("--sbom-dir", str(_sboms(tmp_path / "three", "1.0.1", app="1.0.0")))
    assert w.calls("create") == [] and w.calls("upload") == []
    assert w.run("--sbom-dir", str(_sboms(tmp_path / "two", "1.0.1"))) == 0
    rel = w.load()["releases"]["v1.0.1"]
    assert sorted(a["name"] for a in rel["assets"]) == sorted(
        ["DomovoiSetup.exe", "SHA256SUMS", fr.SERVER_SBOM_ASSET, fr.WEB_SBOM_ASSET])


@pytest.mark.parametrize("make, why", [
    (lambda d: _sboms(d, "1.0.0"), "takes exactly"),                                   # no app SBOM on path a
    (lambda d: _sboms(d, "1.0.0", app="1.0.0", extra={"x.json": b"{}"}), "takes exactly"),
    (lambda d: _sboms(d, "0.9.9", app="1.0.0"), "another release"),
    (lambda d: _sboms(d, "1.0.0", app="0.9.9"), "another release"),
    (lambda d: _sboms(d, "1.0.0", app="1.0.0", extra={fr.WEB_SBOM_ASSET: b"not json"}), "is not JSON"),
    (lambda d: _sboms(d, "1.0.0", app="1.0.0", extra={fr.WEB_SBOM_ASSET: b'{"spdxVersion": "SPDX-2.3"}'}),
     "not a CycloneDX SBOM"),
])
@requires_git
def test_sboms_that_are_not_this_release_s_are_refused_before_github(tmp_path, monkeypatch, make, why):
    w = World(tmp_path, monkeypatch, version="1.0.0")
    with pytest.raises(fr.Refused, match=why):
        w.run("--sbom-dir", str(make(tmp_path / "sboms")))
    assert w.calls("upload") == [] and w.calls("edit") == []
    with pytest.raises(fr.Refused, match="no folder"):
        w.run("--sbom-dir", str(tmp_path / "missing"))


def test_the_sboms_are_known_to_the_release_check_the_app_s_only_with_apks():
    apk = [{"file": "a.apk", "size": 1, "sha256": "1" * 64}]
    both = {"assets": [{"name": n} for n in (fr.SERVER_SBOM_ASSET, fr.WEB_SBOM_ASSET, fr.ANDROID_SBOM_ASSET)]}
    with pytest.raises(fr.Refused, match="has no a.apk"):
        fr._check_published_apks(both, apk)
    with pytest.raises(fr.Refused, match="doesn't know: domovoi-android.cdx.json"):
        fr._check_published_apks(both, [])


def test_the_sbom_names_are_the_generator_s():
    spec = importlib.util.spec_from_file_location("release_sbom", REPO / "scripts" / "release" / "sbom.py")
    sbom = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbom)
    assert (sbom.ASSET_NAMES["server"], sbom.ASSET_NAMES["web"], sbom.ASSET_NAMES["android"]) == fr.SBOM_ASSETS


@pytest.mark.parametrize("script", ["android/scripts/release.ps1", "android/scripts/release.sh"])
def test_a_failed_signing_tool_shows_its_whole_error_without_secrets(script):
    """A failed apksigner prints a headline and then its whole output (stdout
    and stderr, the last 60 lines), not one matching line: the 1.3.0 release
    hid "does not contain a key" and "Failed to load signer" that way. Any
    secret's value is masked before it is shown."""
    text = (REPO / script).read_text(encoding="utf-8")
    for name in ("DOMOVOI_RELEASE_KS_PASS", "DOMOVOI_RELEASE_KEY_PASS", "ANDROID_KEYSTORE_PASSWORD", "ANDROID_KEY_PASSWORD"):
        assert name in text.split("signing", 1)[0] or name in text, name
    assert "***" in text and "60" in text
    if script.endswith(".ps1"):
        assert "Select-Object -Last $max" in text and "function Hide-Secrets" in text
        assert "Select-Object -First 1\n    if ($why) { Write-Host \"  $why\" }\n}" not in text
    else:
        assert "show_tool_error()" in text
        assert text.count('show_tool_error "$') == 2, "signing and verifying both show the whole output"
        assert "2>&1 >/dev/null" not in text, "signing's stdout is kept too"


# ─── the macOS installer (--mac-pkg; mac-installer PLAN 4.12) ────────────


def _mac_pkg(w: "World", *, tag: str | None = None, commit: str | None = None, sha: str | None = None,
             where: Path | None = None, ref: str | None = None) -> Path:
    """A DomovoiSetup-mac.pkg as the Mac build leaves it: the package and
    its build record beside it."""
    d = where or (w.tmp / "mac-build")
    d.mkdir(parents=True, exist_ok=True)
    pkg = d / "DomovoiSetup-mac.pkg"
    pkg.write_bytes(b"xar! not really a package")
    rec = {"kind": "domovoi-mac-setup-build", "schema": 1, "tag": tag or w.tag, "commit": commit or w.tag_commit,
           "file": "DomovoiSetup-mac.pkg", "size": pkg.stat().st_size,
           "sha256": sha or hashlib.sha256(pkg.read_bytes()).hexdigest()}
    if ref is not None:
        rec["ref"] = ref
    (d / "DomovoiSetup-mac.build.json").write_text(json.dumps(rec), encoding="utf-8")
    return pkg


@requires_git
def test_a_release_without_the_mac_package_is_unchanged(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    assert w.run("--no-publish") == 0
    assert not (w.stage / "DomovoiSetup-mac.pkg").exists()
    sums = (w.stage / "SHA256SUMS").read_text(encoding="ascii")
    notes = (w.stage / "NOTES.md").read_text(encoding="utf-8")
    assert "mac" not in sums.lower() and "DomovoiSetup-mac" not in notes and "Mac (preview)" not in notes


@requires_git
def test_the_mac_package_is_attached_summed_and_noted(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    pkg = _mac_pkg(w)
    assert w.run("--mac-pkg", str(pkg)) == 0
    staged = w.stage / "DomovoiSetup-mac.pkg"
    assert staged.read_bytes() == pkg.read_bytes()
    sha = hashlib.sha256(pkg.read_bytes()).hexdigest()
    lines = (w.stage / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert f"{sha}  DomovoiSetup-mac.pkg" in lines
    (upload,) = w.calls("upload")
    names = [Path(a).name for a in upload[3:upload.index("--repo")]]
    assert "DomovoiSetup-mac.pkg" in names and names[-1] == "SHA256SUMS"
    notes = w.state["releases"][w.tag]["body"]
    assert "| DomovoiSetup-mac.pkg |" in notes and sha in notes and "**Mac (Apple silicon, macOS 26 or later) — the server:**" in notes
    assert "/releases/download/" + w.tag + "/DomovoiSetup-mac.pkg" in notes, "the Download section links it"
    rel = w.load()["releases"][w.tag]
    assert "DomovoiSetup-mac.pkg" in [a["name"] for a in rel["assets"]]
    # A second run (the package already on GitHub) is known to the release check.
    assert w.run("--mac-pkg", str(pkg)) == 0


@requires_git
@pytest.mark.parametrize("bad, why", [
    ({"tag": "v0.9.0"}, "built from v0.9.0"),
    ({"commit": "1" * 40}, "built from"),
    ({"sha": "0" * 64}, "describes another package"),
])
def test_a_mac_package_not_built_from_the_tag_is_refused(tmp_path, monkeypatch, bad, why):
    w = World(tmp_path, monkeypatch)
    pkg = _mac_pkg(w, **bad)
    with pytest.raises(fr.Refused, match=why):
        w.run("--no-publish", "--mac-pkg", str(pkg))
    assert w.calls("upload") == []


@requires_git
def test_the_release_page_says_how_to_open_the_ad_hoc_signed_mac_package(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    assert w.run("--mac-pkg", str(_mac_pkg(w))) == 0
    notes = w.load()["releases"][w.tag]["body"]
    dl = notes[:notes.index("## Files and checksums")]
    assert fr.MAC_GATEKEEPER_LINE in dl
    assert "Privacy & Security" in dl and "**Open Anyway**" in dl
    assert "Windows protected your PC" in dl, "beside the Windows line"
    assert "signed ad hoc" in notes


@requires_git
def test_an_app_releases_mac_package_may_come_from_the_commit_under_the_tag(tmp_path, monkeypatch):
    """The workflow builds it from the dispatched commit (w.build), which the
    app release's tag (w.tag_commit) adds android/release.json to; it must
    install the tag itself."""
    w = World(tmp_path, monkeypatch)
    assert _git(w.work, "rev-parse", f"{w.tag_commit}^") == w.build
    assert w.run("--no-publish", "--mac-pkg", str(_mac_pkg(w, commit=w.build, ref=w.tag))) == 0
    with pytest.raises(fr.Refused, match=f"build it with --ref {w.tag}"):
        w.run("--no-publish", "--mac-pkg", str(_mac_pkg(w, commit=w.build, ref=w.build)))
    with pytest.raises(fr.Refused, match="built from"):
        w.run("--no-publish", "--mac-pkg", str(_mac_pkg(w, commit=w.rc1, ref=w.tag)))


@requires_git
def test_a_wrong_mac_package_stops_the_run_before_the_windows_build(tmp_path, monkeypatch):
    """The Mac check runs first (a wrong package costs seconds, not the
    Setup build), and against the copy in the staging folder, the file that
    gets signed and attached."""
    w = World(tmp_path, monkeypatch)
    built = []

    def builder(src, tag, repo):
        built.append(tag)
        return w.pin_build(src, tag, repo)

    pkg = _mac_pkg(w, sha="0" * 64)
    with pytest.raises(fr.Refused, match="describes another package"):
        w.run_built("--no-publish", "--mac-pkg", str(pkg), builder=builder)
    assert built == [], "the Windows Setup was built before the Mac package was checked"
    assert (w.stage / "DomovoiSetup-mac.pkg").is_file(), "the check ran against the staged copy"


@requires_git
def test_a_mac_package_without_its_record_or_file_is_refused(tmp_path, monkeypatch):
    w = World(tmp_path, monkeypatch)
    pkg = _mac_pkg(w)
    (pkg.parent / "DomovoiSetup-mac.build.json").unlink()
    with pytest.raises(fr.Refused, match="no readable Mac build record"):
        w.run("--no-publish", "--mac-pkg", str(pkg))
    with pytest.raises(fr.Refused, match="no Mac package at"):
        w.run("--no-publish", "--mac-pkg", str(tmp_path / "missing.pkg"))
    with pytest.raises(fr.Refused, match="--mac-build-record goes with --mac-pkg"):
        w.run("--no-publish", "--mac-build-record", str(tmp_path / "x.json"))
    # A record elsewhere, named.
    other = _mac_pkg(w, where=tmp_path / "elsewhere")
    shutil.move(str(other.parent / "DomovoiSetup-mac.build.json"), str(tmp_path / "record.json"))
    assert w.run("--no-publish", "--mac-pkg", str(other), "--mac-build-record", str(tmp_path / "record.json")) == 0
    (tmp_path / "record.json").write_text('{"kind": "something-else", "schema": 1}', encoding="utf-8")
    with pytest.raises(fr.Refused, match="is not a Mac build record"):
        w.run("--no-publish", "--mac-pkg", str(other), "--mac-build-record", str(tmp_path / "record.json"))


@requires_git
@requires_ssh_keygen
def test_a_tag_that_pins_the_key_gets_the_mac_package_signed_under_its_own_namespaces(tmp_path, monkeypatch):
    key, public = _keypair(tmp_path, "release")
    w = World(tmp_path, monkeypatch, version="1.0.0", release_key=public)
    pkg = _mac_pkg(w)
    assert w.run("--signing-key", str(key), "--mac-pkg", str(pkg)) == 0
    stage = w.stage
    staged = stage / "DomovoiSetup-mac.pkg"
    manifest = json.loads((stage / "DomovoiSetup-mac.manifest.json").read_bytes())
    assert manifest == {"kind": "domovoi-mac-setup", "schema": 1, "tag": w.tag, "version": "1.0.0",
                        "commit": w.tag_commit, "file": "DomovoiSetup-mac.pkg", "size": staged.stat().st_size,
                        "sha256": hashlib.sha256(staged.read_bytes()).hexdigest()}
    assert _verify(stage / "DomovoiSetup-mac.pkg.sig", staged, public, "domovoi-mac-setup", tmp_path)
    assert _verify(stage / "DomovoiSetup-mac.manifest.json.sig", stage / "DomovoiSetup-mac.manifest.json", public,
                   "domovoi-mac-setup-manifest", tmp_path)
    assert not _verify(stage / "DomovoiSetup-mac.pkg.sig", staged, public, "domovoi-windows-setup", tmp_path), \
        "a Mac signature is no Windows Setup's"
    assert not _verify(stage / "DomovoiSetup.exe.sig", stage / "DomovoiSetup.exe", public, "domovoi-mac-setup", tmp_path)
    names = [line.split("  ", 1)[1] for line in (stage / "SHA256SUMS").read_text(encoding="ascii").splitlines()]
    assert names[-4:] == ["DomovoiSetup-mac.pkg", "DomovoiSetup-mac.pkg.sig", "DomovoiSetup-mac.manifest.json",
                          "DomovoiSetup-mac.manifest.json.sig"]
    (upload,) = w.calls("upload")
    uploaded = [Path(a).name for a in upload[3:upload.index("--repo")]]
    for n in ("DomovoiSetup-mac.pkg", "DomovoiSetup-mac.pkg.sig", "DomovoiSetup-mac.manifest.json",
              "DomovoiSetup-mac.manifest.json.sig"):
        assert n in uploaded, n
    # The Windows files are as they were.
    win = json.loads((stage / "DomovoiSetup.manifest.json").read_bytes())
    assert win["kind"] == "domovoi-windows-setup" and win["file"] == "DomovoiSetup.exe"
    assert not (stage / ".allowed_signers").exists()


def test_the_mac_files_are_known_to_the_release_check():
    rel = {"assets": [{"name": n} for n in ("DomovoiSetup-mac.pkg", "DomovoiSetup-mac.pkg.sig",
                                             "DomovoiSetup-mac.manifest.json", "DomovoiSetup-mac.manifest.json.sig")]}
    with pytest.raises(fr.Refused, match="has no a.apk"):
        fr._check_published_apks(rel, [{"file": "a.apk", "size": 1, "sha256": "1" * 64}])


def test_the_powershell_wrapper_passes_the_mac_package():
    ps1 = (REPO / "scripts" / "release" / "finish-release.ps1").read_text(encoding="ascii")
    assert "'--mac-pkg'" in ps1 and "'--mac-build-record'" in ps1 and "[string]$MacPkg" in ps1
