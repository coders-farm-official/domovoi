"""The release side of the Mac update (mac-installer PLAN 4.12, §6 item
10): what ``mac/build.sh --tag`` writes beside the package is what
``finish-release --mac-pkg`` checks, the package carries the release key
its updates trust, and the update train's signer (mac/tests/train/
sign_train_release.py) makes the four release files exactly as
finish-release does, with a throwaway key only.

No Mac here: build.sh's record-writing snippet runs by itself on a stand-in
package. DB-free; the signer's half needs ssh-keygen.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SH = REPO_ROOT / "mac" / "build.sh"
SIGNER = REPO_ROOT / "mac" / "tests" / "train" / "sign_train_release.py"
SSH_KEYGEN = shutil.which("ssh-keygen")
requires_ssh_keygen = pytest.mark.skipif(SSH_KEYGEN is None, reason="no ssh-keygen")
TAG = "v1.90.1"
COMMIT = "c0ffee0123456789abcdef0123456789abcdef01"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fr():
    return _load(REPO_ROOT / "scripts" / "release" / "finish_release.py", "finish_release")


def _record_snippet() -> str:
    """The Python build.sh runs to write DomovoiSetup-mac.build.json."""
    text = BUILD_SH.read_text(encoding="utf-8")
    m = re.search(r'"\$OUT/DomovoiSetup-mac\.build\.json" <<\'PY\'\n(.*?)\nPY\n', text, re.S)
    assert m, "build.sh writes the build record with a python heredoc"
    return m.group(1)


def _build_record(tmp_path: Path, pkg: Path, *, tag: str = TAG, commit: str = COMMIT, ref: str | None = None) -> Path:
    import hashlib

    out = tmp_path / "DomovoiSetup-mac.build.json"
    env = {**os.environ, "TAG": tag, "COMMIT": commit, "REF": ref or commit, "VERSION": tag[1:],
           "SHA": hashlib.sha256(pkg.read_bytes()).hexdigest(), "SIZE": str(pkg.stat().st_size)}
    subprocess.run([sys.executable, "-I", "-c", _record_snippet(), str(out)], env=env, check=True)
    return out


@pytest.fixture
def pkg(tmp_path) -> Path:
    p = tmp_path / "build" / "DomovoiSetup-mac.pkg"
    p.parent.mkdir()
    p.write_bytes(bytes(range(256)) * 40)
    return p


def test_build_shs_record_is_what_finish_release_checks(fr, tmp_path, pkg):
    rec = _build_record(pkg.parent, pkg)
    doc = json.loads(rec.read_text(encoding="utf-8"))
    assert doc["kind"] == fr.MAC_BUILD_KIND and doc["schema"] == 1 and doc["tag"] == TAG and doc["commit"] == COMMIT
    fr.check_mac_build_record(rec, pkg, tag=TAG, tag_commit=COMMIT)
    with pytest.raises(fr.Refused, match="built from"):
        fr.check_mac_build_record(rec, pkg, tag="v1.90.2", tag_commit=COMMIT)
    pkg.write_bytes(pkg.read_bytes() + b"x")
    with pytest.raises(fr.Refused, match="another package"):
        fr.check_mac_build_record(rec, pkg, tag=TAG, tag_commit=COMMIT)


def test_a_package_built_under_an_app_releases_tag_must_install_the_tag(fr, tmp_path, pkg):
    """The release workflow builds the package from the dispatched commit,
    while it waits for approval; an app release's tag is that commit plus
    android/release.json. finish-release takes such a package only when it
    installs the tag itself (build.sh --ref <tag>)."""
    parent, tag_commit = "1" * 40, "2" * 40
    rec = _build_record(pkg.parent, pkg, commit=parent, ref=TAG)
    assert json.loads(rec.read_text(encoding="utf-8"))["ref"] == TAG
    fr.check_mac_build_record(rec, pkg, tag=TAG, tag_commit=tag_commit, built_on=parent)
    with pytest.raises(fr.Refused, match="built from"):
        fr.check_mac_build_record(rec, pkg, tag=TAG, tag_commit=tag_commit)        # a server-only tag: no parent
    with pytest.raises(fr.Refused, match="built from"):
        fr.check_mac_build_record(rec, pkg, tag=TAG, tag_commit=tag_commit, built_on="3" * 40)
    rec = _build_record(pkg.parent, pkg, commit=parent)                            # installs the parent
    with pytest.raises(fr.Refused, match=f"build it with --ref {TAG}"):
        fr.check_mac_build_record(rec, pkg, tag=TAG, tag_commit=tag_commit, built_on=parent)


def test_build_sh_names_the_record_finish_release_looks_for(fr):
    text = BUILD_SH.read_text(encoding="utf-8")
    assert f'"$OUT/{fr.MAC_BUILD_RECORD}"' in text
    assert f'"kind": "{fr.MAC_BUILD_KIND}"' in text
    # A release build is the tag's commit, at the tag's version.
    assert 'refs/tags/$TAG^{commit}' in text and 'VERSION=${TAG#v}' in text


def test_the_package_carries_the_release_key_its_updates_trust(fr):
    text = BUILD_SH.read_text(encoding="utf-8")
    assert f"PROJECT_KEY=$REPO/{fr.RELEASE_KEY}" in text, "the key finish-release signs with, by default"
    assert 'cp "$RELEASE_KEY" "$S/installer/release-signing.pub"' in text
    assert "needs --release-repo (a throwaway fork)" in text


# ─── the train's signer ─────────────────────────────────────────────────


def _keypair(tmp_path: Path, name: str) -> Path:
    d = tmp_path / "keys"
    d.mkdir(exist_ok=True)
    key = d / name
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "throwaway " + name, "-f", str(key)], check=True)
    return key


def _verify(sig: Path, data: Path, public: bytes, ns: str, tmp_path: Path) -> bool:
    allowed = tmp_path / "allowed"
    parts = public.decode().split()
    allowed.write_bytes(f"x {parts[0]} {parts[1]}\n".encode())
    with open(data, "rb") as f:
        return subprocess.run([SSH_KEYGEN, "-Y", "verify", "-f", str(allowed), "-I", "x", "-n", ns, "-s", str(sig)],
                              stdin=f, capture_output=True).returncode == 0


@requires_ssh_keygen
def test_the_train_signer_makes_the_four_files_finish_release_would(fr, tmp_path, pkg):
    _build_record(pkg.parent, pkg)
    key = _keypair(tmp_path, "train")
    out = tmp_path / "release"
    signer = _load(SIGNER, "sign_train_release")
    rc = signer.main(["--pkg", str(pkg), "--tag", TAG, "--commit", COMMIT, "--key", str(key), "--out", str(out),
                      "--fork", "alex/domovoi-train"])
    assert rc == 0
    names = {fr.MAC_SETUP_ASSET, *fr.MAC_SIGNATURE_ASSETS, "SHA256SUMS"}
    assert {p.name for p in out.iterdir()} == names
    pub = (key.parent / "train.pub").read_bytes()
    assert _verify(out / fr.MAC_SETUP_SIG_ASSET, out / fr.MAC_SETUP_ASSET, pub, fr.MAC_SETUP_NAMESPACE, tmp_path)
    assert _verify(out / fr.MAC_MANIFEST_SIG_ASSET, out / fr.MAC_MANIFEST_ASSET, pub, fr.MAC_MANIFEST_NAMESPACE, tmp_path)
    m = json.loads((out / fr.MAC_MANIFEST_ASSET).read_text(encoding="ascii"))
    assert m == {"kind": "domovoi-mac-setup", "schema": 1, "tag": TAG, "version": "1.90.1", "commit": COMMIT,
                 "file": "DomovoiSetup-mac.pkg", "size": pkg.stat().st_size, "sha256": fr.sha256_file(pkg)}
    sums = (out / "SHA256SUMS").read_text(encoding="ascii")
    assert f"{fr.sha256_file(pkg)}  DomovoiSetup-mac.pkg" in sums and len(sums.splitlines()) == 4


@requires_ssh_keygen
def test_the_train_signer_refuses_the_project_key_and_repository(fr, tmp_path, pkg, capsys):
    _build_record(pkg.parent, pkg)
    signer = _load(SIGNER, "sign_train_release")
    # A "private key" whose public half is the project's release key.
    fake = tmp_path / "keys" / "project"
    fake.parent.mkdir()
    fake.write_text("not a key\n", encoding="ascii")
    shutil.copyfile(REPO_ROOT / fr.RELEASE_KEY, fake.with_name("project.pub"))
    assert signer.main(["--pkg", str(pkg), "--tag", TAG, "--commit", COMMIT, "--key", str(fake), "--out", str(tmp_path / "o1")]) == 2
    assert "never used for the train" in capsys.readouterr().err
    key = _keypair(tmp_path, "train")
    assert signer.main(["--pkg", str(pkg), "--tag", TAG, "--commit", COMMIT, "--key", str(key), "--out", str(tmp_path / "o2"),
                        "--fork", "coders-farm-official/domovoi"]) == 2
    assert "throwaway fork" in capsys.readouterr().err
    # A record for another tag.
    assert signer.main(["--pkg", str(pkg), "--tag", "v1.90.2", "--commit", COMMIT, "--key", str(key), "--out", str(tmp_path / "o3")]) == 2
    assert not (tmp_path / "o3" / fr.MAC_SETUP_SIG_ASSET).exists()


def test_the_signer_never_talks_to_github():
    """It prints the gh command for the owner; it runs nothing that reaches GitHub."""
    src = SIGNER.read_text(encoding="utf-8")
    for bad in ('["gh"', "'gh'", "urllib", "requests", "http.client", "socket"):
        assert bad not in src, bad
    runs = re.findall(r"subprocess\.run\(\[([^\]]*)", src)
    assert runs == ['"git", "-C", str(REPO), "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"'], runs
