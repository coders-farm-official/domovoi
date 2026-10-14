"""The training container never outlives its run, and never runs twice.

``scripts/wake_word/train_wake_word.py`` used to hand the whole run to
``docker run``: when the core's worker gave up (its fixed 3,600 s limit)
or the core stopped, the docker client died and the container carried on,
and the next Train started a second one on the same run dir (seen in the
2026-10-13 spike with a 20 s limit). Now the container has a fixed name and
a label, the wrapper removes a leftover one before it starts, stops its own
at ``WAKE_TRAINER_DEADLINE_SEC`` and on a stop signal, and ``--stop``
removes it on request. The worker's runner (``run_train_command``) asks the
command to stop, kills it after a grace period and then runs that
``--stop``. Here with a fake ``docker`` (``WAKE_TRAINER_DOCKER``) and plain
Python children: no Docker needed. Also the wrapper's resource caps, which
keep live voice working while a word trains.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from domovoi.workers import wake_word_trainer as wwt

WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "wake_word" / "train_wake_word.py"


def _wrapper():
    spec = importlib.util.spec_from_file_location("_wake_wrapper_stop", WRAPPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tw = _wrapper()

# A docker stand-in: logs every call as a JSON line, answers info/inspect,
# lists the "containers" in its state file for `ps --filter label=...`,
# forgets them on `rm -f`, fails the GPU probe (a CPU host), answers the
# cache volume's inspect (FAKE_VOLUME=0: no volume), runs the cache scrub
# (`run --entrypoint sh`, exit FAKE_SCRUB_RC) and makes the training `run`
# add itself to the state file, sleep FAKE_RUN_SEC and exit FAKE_RUN_RC.
FAKE_DOCKER = r'''
import json, os, sys, time
state = os.environ["FAKE_STATE"]
args = sys.argv[1:]
with open(os.path.join(state, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
running = os.path.join(state, "containers")
def listed():
    try:
        return [l for l in open(running, encoding="utf-8").read().split() if l]
    except OSError:
        return []
cmd = args[0] if args else ""
if cmd == "info":
    if "--format" in args:
        print("16 34359738368")
    sys.exit(0)
if cmd == "image":
    sys.exit(0)
if cmd == "volume":
    sys.exit(1 if os.environ.get("FAKE_VOLUME") == "0" else 0)
if cmd == "ps":
    print("\n".join(listed()))
    sys.exit(0)
if cmd == "rm":
    gone = set(args[2:])
    open(running, "w", encoding="utf-8").write("\n".join(c for c in listed() if c not in gone))
    if "train1" in gone:     # like the real one: removing it ends `docker run`
        try:
            import signal
            os.kill(int(open(os.path.join(state, "run.pid")).read()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
    sys.exit(0)
if cmd == "run":
    if "--entrypoint" in args and args[args.index("--entrypoint") + 1] == "sh":
        sys.exit(int(os.environ.get("FAKE_SCRUB_RC", "0")))     # the cache scrub
    if "--entrypoint" in args:
        sys.exit(1)          # the GPU probe: no GPU here
    open(os.path.join(state, "run.pid"), "w").write(str(os.getpid()))
    open(running, "a", encoding="utf-8").write("\ntrain1")
    print("training...", flush=True)
    time.sleep(float(os.environ.get("FAKE_RUN_SEC", "0")))
    sys.exit(int(os.environ.get("FAKE_RUN_RC", "0")))
sys.exit(0)
'''


@pytest.fixture
def fake_docker(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    script = tmp_path / "fake_docker.py"
    script.write_text(FAKE_DOCKER, encoding="utf-8")
    clips = tmp_path / "clips"
    clips.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("WAKE_TRAINER_")}
    env.update(FAKE_STATE=str(state), WAKE_TRAINER_DOCKER=f"{sys.executable} {script}")

    def calls() -> list[list[str]]:
        p = state / "calls.jsonl"
        if not p.is_file():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]

    def run(*extra: str, wait: bool = True, **more_env: str):
        argv = [sys.executable, str(WRAPPER), *extra]
        if not extra or extra[0] != "--stop":
            argv += ["--clips", str(clips), "--phrase", "hey domovoi", "--slug", "hey_domovoi",
                     "--out", str(tmp_path / "models" / "hey_domovoi.onnx")]
        e = dict(env, **more_env)
        if not wait:
            return subprocess.Popen(argv, env=e, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
        return subprocess.run(argv, env=e, capture_output=True, text=True, timeout=120)

    return {"state": state, "calls": calls, "run": run}


def _the_run(calls):
    runs = [c for c in calls if c and c[0] == "run" and "--entrypoint" not in c]
    assert len(runs) == 1, calls
    return runs[0]


def _scrubs(calls):
    """The scripts the cache scrubs ran (``sh -c``), in order."""
    return [c[-1] for c in calls if c and c[0] == "run" and "--entrypoint" in c
            and c[c.index("--entrypoint") + 1] == "sh"]


# ─── the wrapper ──────────────────────────────────────────────────────────

def test_a_leftover_container_is_removed_before_the_run(fake_docker):
    (fake_docker["state"] / "containers").write_text("stale1", encoding="utf-8")
    r = fake_docker["run"](FAKE_RUN_RC="0")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = fake_docker["calls"]()
    rm_at = calls.index(["rm", "-f", "stale1"])
    run_at = calls.index(_the_run(calls))
    assert rm_at < run_at
    assert ["ps", "-aq", "--filter", "label=org.domovoi.wake-train"] in calls


def test_the_run_is_named_labelled_and_capped(fake_docker):
    r = fake_docker["run"](FAKE_RUN_RC="0")
    assert r.returncode == 0
    run = _the_run(fake_docker["calls"]())
    assert run[run.index("--name") + 1] == "domovoi-wake-train"
    assert run[run.index("--label") + 1] == "org.domovoi.wake-train=hey_domovoi"
    # 16 CPUs on the fake host: 4 left for the core, Whisper and Ollama.
    assert run[run.index("--cpus") + 1] == "12"
    assert run[run.index("--cpu-shares") + 1] == "128"
    assert run[run.index("--memory") + 1] == "12g"
    assert run[run.index("--memory-swap") + 1] == "12g"
    assert run[-1] == "domovoi-wake-trainer:latest"


def test_the_exit_code_passes_through(fake_docker):
    assert fake_docker["run"](FAKE_RUN_RC="7").returncode == 7


def test_at_its_deadline_the_wrapper_removes_the_container(fake_docker):
    started = time.monotonic()
    r = fake_docker["run"](FAKE_RUN_SEC="60", WAKE_TRAINER_DEADLINE_SEC="3")
    assert r.returncode == 124, r.stdout + r.stderr
    assert time.monotonic() - started < 50
    calls = fake_docker["calls"]()
    run_at = calls.index(_the_run(calls))
    assert any(c[:2] == ["rm", "-f"] and "train1" in c for c in calls[run_at:]), calls
    assert "time limit" in r.stderr


def test_stop_removes_every_training_container(fake_docker):
    (fake_docker["state"] / "containers").write_text("a1\nb2", encoding="utf-8")
    r = fake_docker["run"]("--stop")
    assert r.returncode == 0
    assert ["rm", "-f", "a1", "b2"] in fake_docker["calls"]()
    assert fake_docker["run"]("--stop").returncode == 0   # nothing left: still fine


def test_stop_gives_the_run_time_to_clean_up_then_scrubs_the_copies(fake_docker):
    # Review 2026-10-13: `docker rm -f` alone killed the run before its
    # finally removed the copies of the household's recordings, and they
    # stayed in the cache volume. Now: `docker stop` (SIGTERM, a grace
    # period), then `rm -f`, then a scrub of whatever a killed run left.
    (fake_docker["state"] / "containers").write_text("a1", encoding="utf-8")
    assert fake_docker["run"]("--stop").returncode == 0
    calls = fake_docker["calls"]()
    stop_at = calls.index(["stop", "-t", str(tw.STOP_GRACE_SEC), "a1"])
    rm_at = calls.index(["rm", "-f", "a1"])
    assert stop_at < rm_at
    scrubs = _scrubs(calls)
    assert len(scrubs) == 1 and "positive_train/real_*.wav' -delete" in scrubs[0]
    scrub = next(c for c in calls if c and c[0] == "run" and "sh" in c)
    assert scrub[scrub.index("-v") + 1] == f"{tw.CACHE_VOLUME}:/data/cache"
    assert "--rm" in scrub and calls.index(scrub) > rm_at
    # A killed core's leftovers are scrubbed even with no container left.
    (fake_docker["state"] / "calls.jsonl").unlink()
    assert fake_docker["run"]("--stop").returncode == 0
    assert len(_scrubs(fake_docker["calls"]())) == 1


def test_no_cache_volume_no_scrub(fake_docker):
    # Training never turned on: --stop must not create the volume.
    assert fake_docker["run"]("--stop", FAKE_VOLUME="0").returncode == 0
    assert _scrubs(fake_docker["calls"]()) == []


def test_every_run_starts_by_scrubbing_leftover_copies(fake_docker):
    r = fake_docker["run"](FAKE_RUN_RC="0")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = fake_docker["calls"]()
    scrub_at = next(i for i, c in enumerate(calls) if c and c[0] == "run" and "sh" in c)
    assert scrub_at < calls.index(_the_run(calls))


def test_the_run_has_an_init_so_sigterm_reaches_the_entrypoint(fake_docker):
    # PID 1 ignores SIGTERM unless it installs a handler; with --init the
    # entrypoint is a child of tini and its handler removes the copies.
    assert fake_docker["run"](FAKE_RUN_RC="0").returncode == 0
    assert "--init" in _the_run(fake_docker["calls"]())


def test_forget_removes_a_deleted_words_folder_only(fake_docker):
    r = fake_docker["run"]("--stop", "--forget", "hey_domo", "--forget", "../etc")
    assert r.returncode == 0, r.stdout + r.stderr
    scrubs = _scrubs(fake_docker["calls"]())
    assert any(x.endswith("rm -rf /data/cache/runs/hey_domo") for x in scrubs), scrubs
    assert not any("etc" in x for x in scrubs)
    assert fake_docker["run"]("--stop", "--forget", "hey_domo", FAKE_SCRUB_RC="1").returncode == 1


@pytest.mark.skipif(not shutil.which("sh"), reason="needs a POSIX sh")
def test_keep_only_removes_every_other_words_folder(tmp_path):
    # The script the sweep runs in the container, run here on a copy of
    # the layout (/data/cache/runs/<slug>/...).
    seen = []
    tw_scrub = tw.scrub_cache
    try:
        tw.scrub_cache = lambda script, what: seen.append(script) or True
        assert tw.keep_only_words(["hey_domovoi", "hello_domo", "bad slug"])
    finally:
        tw.scrub_cache = tw_scrub
    runs = tmp_path / "data" / "cache" / "runs"
    for slug in ("hey_domovoi", "hello_domo", "hey_gone", "hey_domovoi_old"):
        (runs / slug / slug / "positive_train").mkdir(parents=True)
        (runs / slug / slug / "positive_train" / "real_0001.wav").write_bytes(b"x")
    script = seen[0].replace("/data/cache", tmp_path.as_posix() + "/data/cache")
    r = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert sorted(x.name for x in runs.iterdir()) == ["hello_domo", "hey_domovoi"]


def test_a_trainer_set_up_for_the_gpu_refuses_the_cpu(fake_docker):
    # Review 2026-10-13: a GPU that went away trained silently on the CPU
    # for over an hour under the GPU's time limit. Now exit 75, no run.
    r = fake_docker["run"](FAKE_RUN_RC="0", WAKE_TRAINER_REQUIRE_GPU="1")
    assert r.returncode == tw.EXIT_NO_GPU == 75, r.stdout + r.stderr
    assert "set up for the GPU" in r.stderr
    assert not [c for c in fake_docker["calls"]() if c and c[0] == "run" and "--entrypoint" not in c]


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_a_stop_signal_removes_the_container(fake_docker):
    proc = fake_docker["run"](wait=False, FAKE_RUN_SEC="60")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if any(c and c[0] == "run" and "--entrypoint" not in c for c in fake_docker["calls"]()):
            break
        time.sleep(0.2)
    time.sleep(0.5)
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode == 128 + signal.SIGTERM, out
    calls = fake_docker["calls"]()
    run_at = calls.index(_the_run(calls))
    assert any(c[:2] == ["rm", "-f"] and "train1" in c for c in calls[run_at:]), calls


# ─── resource caps ────────────────────────────────────────────────────────

def test_default_caps_leave_cores_free():
    assert tw.resource_limits(16, 64 * 2**30)[:4] == ["--cpus", "12", "--cpu-shares", "128"]
    assert tw.resource_limits(8, 64 * 2**30)[:2] == ["--cpus", "6"]
    assert tw.resource_limits(4, 64 * 2**30)[:2] == ["--cpus", "2"]
    assert tw.resource_limits(2, 64 * 2**30)[:2] == ["--cpus", "1"]


def test_a_cpu_set_bounds_the_cpu_count():
    flags = tw.resource_limits(32, 64 * 2**30, cpuset="16-31")
    assert flags[flags.index("--cpus") + 1] == "16"
    assert flags[flags.index("--cpuset-cpus") + 1] == "16-31"


def test_the_memory_cap_leaves_the_host_its_share():
    flags = tw.resource_limits(16, 14 * 2**30, memory="12g")
    assert flags[flags.index("--memory") + 1] == f"{8 * 1024}m"
    assert flags[flags.index("--memory-swap") + 1] == f"{8 * 1024}m"


def test_malformed_knobs_never_reach_docker():
    flags = tw.resource_limits(16, 0, cpus="lots", cpu_shares="--privileged",
                               cpuset="0-3;rm", memory="12 g")
    joined = " ".join(flags)
    assert "--privileged" not in joined and "rm" not in joined
    assert "--memory" not in flags and "--cpuset-cpus" not in flags
    assert flags[:2] == ["--cpus", "12"]


def test_pronunciations_pass_only_when_they_are_phones(monkeypatch):
    monkeypatch.setattr(tw, "PRONUNCIATIONS", "domovoi=D OW1 M OW2 V OY2")
    argv = tw.build_run_argv("/c", "/m", "hey domovoi", "hey_domovoi", [])
    assert "WAKE_PRONUNCIATIONS=domovoi=D OW1 M OW2 V OY2" in argv
    monkeypatch.setattr(tw, "PRONUNCIATIONS", "x=$(reboot)")
    argv = tw.build_run_argv("/c", "/m", "hey domovoi", "hey_domovoi", [])
    assert not any(a.startswith("WAKE_PRONUNCIATIONS=") for a in argv)


# ─── the worker's runner ──────────────────────────────────────────────────

def _child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.fixture(autouse=True)
def _short_grace(monkeypatch):
    # A child that ignores the stop request is killed after this; 30 s in
    # the worker, a few here.
    monkeypatch.setattr(wwt, "_STOP_GRACE_SEC", 3.0)


def test_runner_returns_the_exit_code_and_output(tmp_path):
    log = tmp_path / "logs" / "w.log"
    r = wwt.run_train_command(_child("print('hello'); raise SystemExit(3)"),
                              env=dict(os.environ), timeout=60, cancel=threading.Event(),
                              log_path=log)
    assert r.returncode == 3 and not r.timed_out and not r.cancelled
    assert "hello" in r.output
    assert "hello" in log.read_text(encoding="utf-8")


def test_runner_stops_the_command_at_the_limit_and_runs_stop(tmp_path):
    marker = tmp_path / "stopped"
    started = time.monotonic()
    r = wwt.run_train_command(
        _child("import time; print('working', flush=True); time.sleep(120)"),
        env=dict(os.environ), timeout=2, cancel=threading.Event(),
        stop_argv=_child(f"open({str(marker)!r}, 'w').write('x')"),
    )
    assert r.timed_out and r.returncode is None
    assert "working" in r.output
    assert marker.is_file()      # the wrapper's --stop ran after it
    assert time.monotonic() - started < 60


def test_runner_stops_the_command_when_cancelled(tmp_path):
    marker = tmp_path / "stopped"
    cancel = threading.Event()
    threading.Timer(1.0, cancel.set).start()
    r = wwt.run_train_command(
        _child("import time; time.sleep(120)"), env=dict(os.environ), timeout=600,
        cancel=cancel, stop_argv=_child(f"open({str(marker)!r}, 'w').write('x')"),
    )
    assert r.cancelled and not r.timed_out and r.returncode is None
    assert marker.is_file()


def test_synthesis_ipa_passes_only_when_it_is_word_ipa_pairs(monkeypatch):
    monkeypatch.setattr(tw, "SYNTH_IPA", "domovoi=dˈoʊmoʊvˌɔɪ;domo=dˈoʊmoʊ")
    argv = tw.build_run_argv("/c", "/m", "hey domovoi", "hey_domovoi", [])
    assert "WAKE_SYNTH_IPA=domovoi=dˈoʊmoʊvˌɔɪ;domo=dˈoʊmoʊ" in argv
    assert argv[-1] == tw.IMAGE
    for bad in ("domovoi=a b", "x=$(id)", "domovoi"):
        monkeypatch.setattr(tw, "SYNTH_IPA", bad)
        argv = tw.build_run_argv("/c", "/m", "hey domovoi", "hey_domovoi", [])
        assert not any(a.startswith("WAKE_SYNTH_IPA=") for a in argv)
