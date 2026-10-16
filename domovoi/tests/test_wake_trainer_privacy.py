"""No copy of a household recording outlives its training run, and the
training data is what was pinned.

The trainer copies the recorded clips (somebody's voice) into the cache
volume for a run and removes the copies at the end. The review of
2026-10-13 (a kill test in the maintainers' functional test harness)
found that a run stopped from outside left them there: the wrapper's
``docker rm -f`` killed the container before the entrypoint's ``finally``
ran, and as PID 1 the entrypoint ignored the SIGTERM a ``docker stop``
would have sent. Now:

* the container runs under ``--init`` and the entrypoint turns SIGTERM,
  SIGINT and SIGHUP into an exit that runs its ``finally``;
* the wrapper stops with ``docker stop`` (a grace period) before
  ``rm -f``, then scrubs ``real_*.wav`` from the volume for what a killed
  run left (test_wake_trainer_stop.py holds the order);
* every run starts by removing copies an earlier run left.

The pins: the Hugging Face datasets at fixed commits, the feature files and
the background zips checked against their SHA-256.

The end-to-end kill test needs Docker and a trainer image
(``WAKE_TRAINER_TEST_IMAGE``, e.g. ``domovoi-wake-trainer:latest``); without
them it skips.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "wake_word"
DOCKER_DIR = SCRIPTS / "docker"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ep = _load("_wake_entrypoint_privacy", DOCKER_DIR / "entrypoint.py")


def _run_dir(runs: Path, slug: str) -> Path:
    pos = runs / slug / slug / "positive_train"
    pos.mkdir(parents=True)
    (pos / "synth_0001.wav").write_bytes(b"synthetic")
    for i in range(3):
        (pos / f"real_{i:04d}_000.wav").write_bytes(b"somebody's voice")
    return pos


# ─── the entrypoint ───────────────────────────────────────────────────────

def test_leftover_copies_of_every_word_are_scrubbed(tmp_path):
    runs = tmp_path / "runs"
    a, b = _run_dir(runs, "hey_domovoi"), _run_dir(runs, "hello_domo")
    assert ep.scrub_real_copies(runs) == 6
    for pos in (a, b):
        assert [f.name for f in pos.iterdir()] == ["synth_0001.wav"]
    assert ep.scrub_real_copies(runs) == 0
    assert ep.scrub_real_copies(tmp_path / "none") == 0


def test_a_stop_signal_unwinds_through_the_finally(tmp_path):
    """The handler raises; the run's ``finally`` (drop_real_copies) runs."""
    pos = _run_dir(tmp_path / "runs", "hey_domovoi")
    with pytest.raises(SystemExit) as e:
        try:
            ep._on_stop_signal(signal.SIGTERM, None)
        finally:
            ep.drop_real_copies(pos.parent)
    assert e.value.code == 128 + signal.SIGTERM
    assert not list(pos.glob("real_*.wav"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_sigterm_to_a_running_entrypoint_removes_its_copies(tmp_path):
    """A real SIGTERM to a process sitting in a long stage."""
    pos = _run_dir(tmp_path / "runs", "hey_domovoi")
    child = tmp_path / "child.py"
    child.write_text(textwrap.dedent(f"""
        import importlib.util, sys, time
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("ep", {str(DOCKER_DIR / "entrypoint.py")!r})
        ep = importlib.util.module_from_spec(spec); spec.loader.exec_module(ep)
        ep.install_stop_handlers()
        try:
            print("augmenting", flush=True)
            time.sleep(600)
        finally:
            ep.drop_real_copies(Path({str(pos.parent)!r}))
    """), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(child)], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "augmenting"
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(30) == 128 + signal.SIGTERM
    assert not list(pos.glob("real_*.wav")) and (pos / "synth_0001.wav").is_file()


# ─── the pins ─────────────────────────────────────────────────────────────

def test_the_datasets_are_pinned_to_commits_and_hashes():
    for rev in (ep.FEATURES_REVISION, ep.RIRS_REVISION):
        assert len(rev) == 40 and all(c in "0123456789abcdef" for c in rev)
    for table in (ep.FEATURES_SHA256, ep.BACKGROUND_ZIP_SHA256):
        assert table and all(len(v) == 64 for v in table.values())
    assert {u.rsplit("/", 1)[-1] for u in ep.BACKGROUND_ZIP_URLS} == set(ep.BACKGROUND_ZIP_SHA256)
    src = (DOCKER_DIR / "entrypoint.py").read_text(encoding="utf-8")
    assert "revision=FEATURES_REVISION" in src and "revision=RIRS_REVISION" in src


def _features(monkeypatch, tmp_path, bodies: dict[str, bytes]):
    import hashlib

    fdir = tmp_path / "openwakeword_features"
    fdir.mkdir()
    for name, body in bodies.items():
        (fdir / name).write_bytes(body)
    monkeypatch.setattr(ep, "FEATURES_DIR", fdir)
    monkeypatch.setattr(ep, "VERIFIED", tmp_path / ".verified")
    monkeypatch.setattr(ep, "FEATURES_SHA256", {
        "a.npy": hashlib.sha256(b"negatives").hexdigest(),
        "b.npy": hashlib.sha256(b"validation").hexdigest()})


def test_features_that_match_their_pins_pass_once(monkeypatch, tmp_path):
    _features(monkeypatch, tmp_path, {"a.npy": b"negatives", "b.npy": b"validation"})
    ep.verify_features()
    assert ep.VERIFIED.is_file()
    (ep.FEATURES_DIR / "a.npy").write_bytes(b"changed")      # checked once, not every run
    ep.verify_features()


@pytest.mark.parametrize("bodies", [
    {"a.npy": b"tampered", "b.npy": b"validation"},
    {"a.npy": b"negatives"},
])
def test_features_that_dont_match_are_refused(monkeypatch, tmp_path, bodies):
    _features(monkeypatch, tmp_path, bodies)
    with pytest.raises(SystemExit) as e:
        ep.verify_features()
    assert e.value.code == 4 and not ep.VERIFIED.exists()


def test_a_background_zip_that_doesnt_match_is_refused(monkeypatch, tmp_path, capsys):
    import urllib.request

    def fetch(url, path):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("noise.wav", b"RIFF....")

    monkeypatch.setattr(urllib.request, "urlretrieve", fetch)
    monkeypatch.setattr(ep, "BACKGROUND_DIR", tmp_path / "bg")
    monkeypatch.setattr(ep, "BACKGROUND_SENTINEL", tmp_path / ".bg")
    with pytest.raises(SystemExit) as e:
        ep.ensure_background()
    assert e.value.code == 4 and "refused" in capsys.readouterr().err
    assert not list((tmp_path / "bg").glob("*.wav"))         # nothing extracted


# ─── end to end, in Docker ────────────────────────────────────────────────

IMAGE = os.environ.get("WAKE_TRAINER_TEST_IMAGE", "")
_CPUSET = os.environ.get("WAKE_TRAINER_TEST_CPUSET", "16-31")
_NAME = "wake-gpu-privacy"
_VOL = "wake-gpu-privacy-cache"
_LABEL = "org.domovoi.wake-train-privacy-test"

# The entrypoint's own main() with the heavy stages stubbed: no generation,
# the recorded clips really injected, then a long "augment" (the review's
# kill_driver.py).
_DRIVER = r'''
import os, sys, time
from pathlib import Path
import numpy as np
import soundfile as sf
sys.path.insert(0, "/ep")
import entrypoint as ep
clips = Path("/tmp/clips"); clips.mkdir(parents=True, exist_ok=True)
for i in range(3):
    sf.write(str(clips / f"clip_{i + 1:03d}.wav"), np.zeros(32000, dtype="float32"), 16000)
ep.CLIPS_IN = clips
ep.ensure_dataset = ep.ensure_rirs = ep.ensure_background = lambda: None
ep.detect_device = lambda: "cpu"
ep.lower_priority = lambda: None
ep.render_config = lambda slug, phrase, device, work: work / "cfg.yaml"
def run_stage(cfg, flag, device):
    if flag == "--augment_clips":
        print("augmenting", flush=True)
        time.sleep(600)
ep.run_stage = run_stage
os.environ.update(WAKE_SLUG="hey_privacy", WAKE_PHRASE="hey privacy", WAKE_N_SAMPLES="20")
sys.exit(ep.main())
'''


def _docker(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _copies() -> int:
    r = _docker("run", "--rm", "--name", f"{_NAME}-look", "--cpuset-cpus", _CPUSET,
                "-v", f"{_VOL}:/data/cache:ro", "--entrypoint", "sh", IMAGE, "-c",
                "find /data/cache/runs -name 'real_*.wav' | wc -l")
    assert r.returncode == 0, r.stderr
    return int(r.stdout.strip())


def _start_run(tmp_path: Path) -> None:
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    r = _docker("run", "-d", "--init", "--name", _NAME, "--label", f"{_LABEL}=hey_privacy",
                "--cpuset-cpus", _CPUSET, "-v", f"{_VOL}:/data/cache",
                "-v", f"{DOCKER_DIR.as_posix()}:/ep:ro", "-v", f"{tmp_path.as_posix()}:/drv:ro",
                "--entrypoint", "python", IMAGE, "/drv/driver.py")
    assert r.returncode == 0, r.stderr
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if "augmenting" in _docker("logs", _NAME).stdout:
            return
        time.sleep(1)
    raise AssertionError("the run never reached augment: " + _docker("logs", _NAME).stderr[-800:])


@pytest.fixture
def docker_run(monkeypatch):
    if not IMAGE or not shutil.which("docker"):
        pytest.skip("set WAKE_TRAINER_TEST_IMAGE to a trainer image (needs Docker)")
    if _docker("image", "inspect", IMAGE, timeout=60).returncode != 0:
        pytest.skip(f"no image {IMAGE}")
    tw = _load("_wake_wrapper_privacy", SCRIPTS / "train_wake_word.py")
    monkeypatch.setattr(tw, "LABEL", _LABEL)
    monkeypatch.setattr(tw, "CACHE_VOLUME", _VOL)
    monkeypatch.setattr(tw, "IMAGE", IMAGE)
    real = tw._docker

    def named(args, timeout):            # the scrub container: named, on the test CPUs
        if args and args[0] == "run":
            args = ["run", "--name", f"{_NAME}-scrub", "--cpuset-cpus", _CPUSET, *args[1:]]
        return real(args, timeout)

    monkeypatch.setattr(tw, "_docker", named)
    _docker("rm", "-f", _NAME, f"{_NAME}-look", f"{_NAME}-scrub")
    _docker("volume", "rm", _VOL)
    assert _docker("volume", "create", _VOL).returncode == 0
    yield tw
    _docker("rm", "-f", _NAME, f"{_NAME}-look", f"{_NAME}-scrub")
    _docker("volume", "rm", _VOL)


def test_a_stopped_run_leaves_no_recordings(docker_run, tmp_path):
    """``docker stop``: the entrypoint (under --init) gets SIGTERM and its
    finally removes the copies itself."""
    _start_run(tmp_path)
    assert _docker("stop", "-t", "10", _NAME, timeout=60).returncode == 0
    assert _docker("inspect", "-f", "{{.State.ExitCode}}", _NAME).stdout.strip() == "143"
    assert _copies() == 0


def test_a_killed_run_is_scrubbed_by_the_wrappers_stop(docker_run, tmp_path):
    """SIGKILL (a killed core, OOM): nothing in the container runs; the
    wrapper's stop path (the worker's cancel, timeout and sweep) scrubs."""
    tw = docker_run
    _start_run(tmp_path)
    assert _docker("kill", _NAME).returncode == 0
    assert _copies() > 0                        # what the review saw stay
    tw.remove_training_containers("test")
    assert _copies() == 0
    assert _docker("inspect", _NAME).returncode != 0       # and the container is gone


def test_the_wrappers_stop_on_a_running_run(docker_run, tmp_path):
    tw = docker_run
    _start_run(tmp_path)
    assert tw.remove_training_containers("test") == 1
    assert _copies() == 0
