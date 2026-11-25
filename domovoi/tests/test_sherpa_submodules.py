"""android/native/sherpa-onnx's sources as git submodules (1.5.0, for
F-Droid): src/ is sherpa-onnx and deps/<name>/ each library its CMake would
fetch, pinned to exactly the commits build-native.sh builds (SHERPA_COMMIT
and DEPS), from the same repositories.

The pins are read from the tree (`git ls-tree`: a submodule is a gitlink
there, whether or not it is checked out), so this runs on a clone without
--recursive too. Then build-native.sh's use of them, run with stand-in
tools up to the point where it has chosen its sources: a checked-out
submodule at another commit, or one that is no git checkout, stops the
build; SHERPA_SUBMODULES=no and the environment's own sources win; an empty
(not initialised) submodule folder is not used.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FOLDER = "android/native/sherpa-onnx"
SCRIPT = REPO / FOLDER / "build-native.sh"

needs_git = pytest.mark.skipif(shutil.which("git") is None or not (REPO / ".git").exists(), reason="needs git and a checkout")
needs_bash = pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="needs bash (POSIX)")


def _pins() -> tuple[str, str, list[tuple[str, str, str, str]]]:
    """(SHERPA_REPO, SHERPA_COMMIT, DEPS as (name, repo, ref, commit)), as
    build-native.sh --pins reads them."""
    out = subprocess.run(
        ["bash", "-c", 'source "$1" --pins; echo "$SHERPA_REPO"; echo "$SHERPA_COMMIT"; printf "%s\\n" "${DEPS[@]}"', "_", str(SCRIPT)],
        check=True, capture_output=True, text=True).stdout.splitlines()
    deps = [tuple(line.split("|")) for line in out[2:] if line]
    assert all(len(d) == 4 for d in deps)
    return out[0], out[1], deps  # type: ignore[return-value]


def _expected() -> dict[str, tuple[str, str]]:
    """Submodule path -> (url, commit)."""
    repo, commit, deps = _pins()
    want = {f"{FOLDER}/src": (repo, commit)}
    for name, url, _ref, c in deps:
        want[f"{FOLDER}/deps/{name}"] = (url, c)
    return want


@needs_bash
@needs_git
def test_each_submodule_is_at_build_native_shs_pin() -> None:
    want = _expected()
    assert len(want) == 9
    tree = subprocess.run(["git", "-C", str(REPO), "ls-tree", "-r", "HEAD", "--", FOLDER],
                          check=True, capture_output=True, text=True).stdout
    links = {}
    for line in tree.splitlines():
        meta, path = line.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "commit":
            assert mode == "160000"
            links[path] = sha
    assert links == {p: c for p, (_u, c) in want.items()}, "every submodule's commit is the one build-native.sh pins"


@needs_bash
def test_gitmodules_names_the_same_repositories() -> None:
    cp = configparser.ConfigParser()
    cp.read(REPO / ".gitmodules", encoding="utf-8")
    got = {cp[s]["path"]: cp[s]["url"] for s in cp.sections()}
    want = {p: u for p, (u, _c) in _expected().items()}
    assert {p: u for p, u in got.items() if p.startswith(FOLDER + "/")} == want
    for s in cp.sections():
        # The pins are the gitlinks; nothing in .gitmodules may move them.
        assert "branch" not in cp[s] and "update" not in cp[s], s


def test_the_app_build_reads_none_of_the_sources() -> None:
    """Gradle reads only jniLibs/, kotlin/ and the licences beside them."""
    gradle = (REPO / "android/app/build.gradle.kts").read_text(encoding="utf-8")
    for line in gradle.splitlines():
        if "native/sherpa-onnx" in line and "srcDir" in line:
            assert line.strip().endswith(('sherpa-onnx/jniLibs")', 'sherpa-onnx/kotlin")')), line
    assert "sherpa-onnx/src" not in gradle and "sherpa-onnx/deps" not in gradle


# ── build-native.sh with stand-in tools ─────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "alex", "GIT_AUTHOR_EMAIL": "alex@example.org",
           "GIT_COMMITTER_NAME": "alex", "GIT_COMMITTER_EMAIL": "alex@example.org"}
    return subprocess.run(["git", "-c", "init.defaultBranch=main", *args], cwd=cwd, check=True,
                          capture_output=True, text=True, env=env).stdout.strip()


def _stage(tmp: Path) -> tuple[Path, dict[str, str]]:
    """A copy of the folder's script, an NDK that says it is r29, cmake and
    ninja that do nothing, and an ONNX Runtime AAR that fails its pin, so a
    run stops right after it has chosen and checked its sources."""
    here = tmp / "repo" / FOLDER
    here.mkdir(parents=True)
    shutil.copy(SCRIPT, here / "build-native.sh")
    ndk = tmp / "ndk"
    (ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin").mkdir(parents=True)
    (ndk / "source.properties").write_text("Pkg.Revision = 29.0.14206865\n")
    clang = ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin/clang"
    clang.write_text("#!/bin/sh\n")
    clang.chmod(0o755)
    bin_ = tmp / "bin"
    bin_.mkdir()
    for tool in ("cmake", "ninja"):
        (bin_ / tool).write_text("#!/bin/sh\nexit 0\n")
        (bin_ / tool).chmod(0o755)
    if shutil.which("sha256sum") is None:  # a Mac's shasum stands in
        (bin_ / "sha256sum").write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n')
        (bin_ / "sha256sum").chmod(0o755)
    (tmp / "work").mkdir()
    (tmp / "ort.aar").write_text("not the pinned AAR")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SHERPA_", "OUT", "WORK"))}
    env.update(PATH=f"{bin_}{os.pathsep}{env.get('PATH', '')}", ANDROID_NDK=str(ndk), ABIS="arm64-v8a",
               SHERPA_WORK=str(tmp / "work"), SHERPA_OUT=str(tmp / "out"), ORT_AAR=str(tmp / "ort.aar"),
               GIT_CONFIG_GLOBAL=os.devnull)
    return here, env


def _run(here: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(here / "build-native.sh")], env=env, capture_output=True, text=True, timeout=120)


def _plain_sources(tmp: Path, env: dict[str, str]) -> None:
    """Every source given in the environment, as folders (no git)."""
    _repo, _commit, deps = _pins()
    (tmp / "given/sherpa").mkdir(parents=True)
    env["SHERPA_SRC"] = str(tmp / "given/sherpa")
    for name, *_ in deps:
        d = tmp / "given" / name
        d.mkdir()
        (d / "x.h").write_text("given\n")
        env["SHERPA_DEP_" + name.upper().replace("-", "_")] = str(d)


@needs_bash
@needs_git
def test_a_submodule_at_another_commit_stops_the_build(tmp_path: Path) -> None:
    here, env = _stage(tmp_path)
    src = here / "src"
    src.mkdir()
    _git(src, "init", "-q")
    (src / "CMakeLists.txt").write_text("project(x)\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "not the pin")
    r = _run(here, env)
    assert r.returncode != 0
    head = _git(src, "rev-parse", "HEAD")
    assert f"sherpa-onnx: the submodule at {src.resolve()} is at {head}, not the pinned {_pins()[1]}" in r.stderr, r.stderr
    assert "git submodule update --init" in r.stderr


@needs_bash
@needs_git
def test_a_populated_folder_that_is_no_checkout_stops_the_build(tmp_path: Path) -> None:
    here, env = _stage(tmp_path)
    _plain_sources(tmp_path, env)
    del env["SHERPA_DEP_KISSFFT"]
    (here / "deps/kissfft").mkdir(parents=True)
    (here / "deps/kissfft/kiss_fft.c").write_text("/* copied by hand */\n")
    r = _run(here, env)
    assert r.returncode != 0
    assert "kissfft: " in r.stderr and "has files but is no git checkout, so its commit can't be checked" in r.stderr, r.stderr


@needs_bash
@needs_git
def test_the_environment_and_sherpa_submodules_no_win(tmp_path: Path) -> None:
    here, env = _stage(tmp_path)
    _plain_sources(tmp_path, env)
    # A submodule at the wrong commit beside the script is never looked at:
    # the environment names every source.
    src = here / "src"
    src.mkdir()
    _git(src, "init", "-q")
    (src / "a").write_text("a\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "x")
    r = _run(here, env)
    assert "submodule" not in r.stdout + r.stderr.replace("git submodule", ""), r.stdout + r.stderr
    assert f"sherpa-onnx: {tmp_path / 'given/sherpa'}" in r.stdout or "(not a git checkout; commit not checked)" in r.stdout, r.stdout
    assert "is not onnxruntime-android" in r.stderr, "it went on to the AAR, past the sources"
    # SHERPA_SUBMODULES=no: not even with a source left to fetch (it then
    # clones; stopped here by a git that refuses the network).
    del env["SHERPA_DEP_JSON"]
    env["SHERPA_SUBMODULES"] = "no"
    (here / "deps/json").mkdir(parents=True)
    (here / "deps/json/stray").write_text("x\n")
    env["GIT_ALLOW_PROTOCOL"] = "none"
    r = _run(here, env)
    assert "json: the submodule" not in r.stderr and "no git checkout" not in r.stderr, r.stderr
    assert r.returncode != 0 and "transport 'https' not allowed" in r.stderr, r.stderr


@needs_bash
@needs_git
def test_an_empty_submodule_folder_is_not_used(tmp_path: Path) -> None:
    """A clone without --recursive: git leaves each submodule an empty
    folder, and the build fetches by pin as before."""
    here, env = _stage(tmp_path)
    _plain_sources(tmp_path, env)
    del env["SHERPA_DEP_EIGEN"]
    (here / "deps/eigen").mkdir(parents=True)
    env["GIT_ALLOW_PROTOCOL"] = "none"  # the clone it tries is refused here
    r = _run(here, env)
    assert r.returncode != 0
    assert "eigen: the submodule" not in r.stderr and "no git checkout" not in r.stderr, r.stderr
    assert "eigen: submodule" not in r.stdout
    assert "transport 'https' not allowed" in r.stderr, "it went to clone eigen by its pin: " + r.stderr


@needs_bash
@needs_git
def test_checked_out_submodules_here_are_used(tmp_path: Path) -> None:
    """When this checkout has them (git submodule update --init), the real
    script picks every one of them, at its pin."""
    want = _expected()
    if any(not (REPO / p).is_dir() or not any((REPO / p).iterdir()) for p in want):
        pytest.skip("the submodules aren't checked out here")
    here = REPO / FOLDER
    _, env = _stage(tmp_path)
    r = _run(here, env)
    assert r.returncode != 0 and "is not onnxruntime-android" in r.stderr, r.stdout + r.stderr
    for p, (_u, c) in want.items():
        name = "sherpa-onnx" if p.endswith("/src") else p.rsplit("/", 1)[1]
        assert f"   {name}: submodule {(REPO / p).resolve()} at {c}" in r.stdout, r.stdout
