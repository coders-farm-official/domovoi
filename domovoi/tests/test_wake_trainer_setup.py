"""Settings → Wake Words → "Turn on wake-word training"
(``domovoi/wake_trainer_setup.py``).

As shipped before this, training could not be switched on without a shell
on the Windows install (the two settings weren't in the dashboard, the
image was built by nobody). The job here builds the image, checks the GPU,
downloads the data and then writes the two settings; these tests hold its
decisions with docker faked out:

* refusals with a reason, never a dead button: internet off, too little
  memory or disk, no Docker;
* a CPU-only server needs a second yes, and a GPU Docker can't reach stops
  the job and asks for it instead of silently training on the CPU;
* the settings land in ``.env`` and on the live singleton, the worker
  starts, and the command is the repo's own wrapper;
* progress and a failure persist across a reload; a job the core died in
  reads "interrupted";
* the time limit follows what the job found.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from domovoi import config_env_writer, egress
from domovoi import wake_trainer_setup as wts
from domovoi.config import settings
from domovoi.workers import wake_word_trainer as wwt

GPU = [{"name": "NVIDIA GeForce RTX 4070 Ti SUPER", "mem_used_mb": 600, "mem_total_mb": 16376}]


@pytest.fixture
def box(monkeypatch, tmp_path):
    """A 64 GB, 32-CPU Docker Engine host with plenty of disk, no GPU,
    nothing built yet; every docker call recorded."""
    calls: list[list[str]] = []
    state = {"gpus": [], "runtimes": ["io.containerd.runc.v2", "runc"], "probe_ok": True,
             "build_rc": 0, "warm_rc": 0, "mem_mib": 64 * 1024, "free_gb": 500.0,
             "docker_ok": True, "image": (False, None), "wsl": False}

    def docker_info():
        if not state["docker_ok"]:
            return {"ok": False, "error": "Docker is not running"}
        return {"ok": True, "version": "29.8.1", "ncpu": 32, "mem_bytes": 64 * 2**30,
                "root": str(tmp_path), "os": "Ubuntu 26.04", "runtimes": state["runtimes"]}

    async def host_gpus():
        return state["gpus"]

    def stream(argv, timeout, on_line, on_cancel=None):
        calls.append(argv)
        if argv[:2] == ["docker", "build"]:
            for k in range(1, 17):
                on_line(f"#{k + 4} [{k:2d}/16] RUN something")
            return state["build_rc"], ["#20 ERROR: build failed"] if state["build_rc"] else []
        on_line("[entrypoint] warm: 9300000000 bytes in the cache")
        return state["warm_rc"], ["[entrypoint] FATAL: feature dataset download failed"] \
            if state["warm_rc"] else []

    class Probe:
        def __init__(self, ok):
            self.returncode = 0 if ok else 1
            self.stdout = "NVIDIA GeForce RTX 4070 Ti SUPER\n" if ok else ""
            self.stderr = "" if ok else ("docker: Error response from daemon: failed to "
                                         "discover GPU vendor from CDI\n")

    def run(argv, timeout):
        calls.append(argv)
        return Probe(state["probe_ok"])

    started: list[tuple[str, str]] = []
    from domovoi.plugins_runtime import workers as rt

    monkeypatch.setattr(rt.WORKERS, "start_worker", lambda o, n: started.append((o, n)) or True)
    monkeypatch.setattr(wts, "_docker_info", docker_info)
    monkeypatch.setattr(wts, "_host_gpus", host_gpus)
    monkeypatch.setattr(wts, "_image_label", lambda image: state["image"])
    monkeypatch.setattr(wts, "source_tree_id", lambda: "a" * 40)
    monkeypatch.setattr(wts, "_disk_free_gb", lambda info: (state["free_gb"], "/var/lib/docker"))
    monkeypatch.setattr(wts, "_mem_total_mib", lambda: state["mem_mib"])
    # Not the Windows install's distro unless a test says so (the Linux
    # harness itself may run in WSL).
    monkeypatch.setattr(wts, "on_wsl", lambda: state["wsl"])
    monkeypatch.setattr(wts, "_physical_cores", lambda: 8)
    monkeypatch.setattr(wts, "_stream", stream)
    monkeypatch.setattr(wts, "_run", run)
    monkeypatch.setattr(wts, "_remove_warm", lambda: None)
    env_file = tmp_path / ".env"
    env_file.write_text("# the household's\nWHISPER_MODEL=small.en\n", encoding="utf-8")
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", env_file)
    monkeypatch.setattr(settings, "wake_models_dir", str(tmp_path / "home" / "wake_models"))
    monkeypatch.setattr(settings, "wake_word_trainer_enabled", False)
    monkeypatch.setattr(settings, "wake_word_train_command", "")
    monkeypatch.setattr(settings, "wake_word_train_timeout_sec", 0)
    monkeypatch.setattr(settings, "wake_trainer_cpus", 0.0)
    wts.reset_for_tests()
    yield {"state": state, "calls": calls, "started": started, "env": env_file}
    wts.reset_for_tests()


async def _finish():
    task = wts._TASK
    assert task is not None
    await asyncio.wait_for(task, 10)


# ─── refusals ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_internet_turned_off_refuses_with_the_reason(box):
    with egress.override_policy("never"):
        req = await wts.requirements(force=True)
        assert any("Internet access is turned off" in r for r in req["refusals"])
        with pytest.raises(wts.Refused, match="Internet access is turned off"):
            await wts.start(cpu_ok=True)
    assert box["calls"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("key", "value", "words"), [
    ("mem_mib", 15 * 1024, "needs a 24 GB server"),
    ("free_gb", 20.0, "of disk is free"),
    ("docker_ok", False, "Docker is not running"),
])
async def test_each_refusal_names_what_is_missing(box, key, value, words):
    box["state"][key] = value
    with egress.override_policy("always"):
        req = await wts.requirements(force=True)
        assert any(words in r for r in req["refusals"]), req["refusals"]
        with pytest.raises(wts.Refused, match=words):
            await wts.start(cpu_ok=True)


@pytest.mark.asyncio
async def test_disk_needed_drops_once_image_and_data_are_there(box):
    with egress.override_policy("always"):
        first = await wts.requirements(force=True)
        assert first["disk"]["need_gb"] == 31       # 12.4 + 17.9 + 0.5
        box["state"]["image"] = (True, "a" * 40)
        wts._update(data_ready=True)
        later = await wts.requirements(force=True)
    assert later["disk"]["need_gb"] == 0 or later["disk"]["need_gb"] == 1
    assert later["image"]["current"] is True


@pytest.mark.asyncio
async def test_a_refused_job_never_starts_while_a_word_trains(box, monkeypatch):
    monkeypatch.setattr(wwt, "training_active", lambda: True)
    with egress.override_policy("always"), pytest.raises(wts.Refused, match="training right now"):
        await wts.start(cpu_ok=True)


# ─── the CPU-only server ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_cpu_only_server_needs_a_second_yes(box):
    with egress.override_policy("always"):
        req = await wts.requirements(force=True)
        assert req["gpu"] is False and req["estimate"]["device"] == "cpu"
        # 32 CPUs, 8 physical cores: the measured 75 min on 8 cores.
        assert req["estimate"]["minutes_per_word"] == 75
        with pytest.raises(wts.Refused, match="trains on the CPU"):
            await wts.start(cpu_ok=False)
        await wts.start(cpu_ok=True)
        await _finish()
    st = (await wts.status())
    assert st["enabled"] is True and st["job"]["state"] == "done"
    assert wts.auto_timeout_sec() == 9000          # twice the estimate, never under 2 h
    assert not any("--gpus" in c for c in box["calls"])   # no probe without a card


# ─── the GPU server ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_gpu_server_turns_on_and_writes_the_two_settings(box):
    box["state"].update(gpus=GPU, runtimes=["nvidia", "runc"])
    with egress.override_policy("always"):
        req = await wts.requirements(force=True)
        assert req["gpu"] is True and req["estimate"]["minutes_per_word"] == 20
        await wts.start(cpu_ok=False)
        await _finish()
    st = await wts.status()
    assert st["job"]["state"] == "done" and st["job"]["percent"] == 100
    assert st["gpu_name"] == "NVIDIA GeForce RTX 4070 Ti SUPER"
    assert wts.auto_timeout_sec() == 3600
    # The live settings and .env, beside the household's own line.
    assert settings.wake_word_trainer_enabled is True
    cmd = settings.wake_word_train_command
    argv = wwt.build_train_argv(cmd, clips_dir="/c", phrase="hey domovoi", slug="s", out="/o")
    assert Path(argv[1]) == wts.wrapper_path()
    assert argv[2:] == ["--clips", "/c", "--phrase", "hey domovoi", "--slug", "s", "--out", "/o"]
    text = box["env"].read_text(encoding="utf-8")
    assert "WHISPER_MODEL=small.en" in text
    assert "WAKE_WORD_TRAINER_ENABLED=true" in text
    assert "WAKE_WORD_TRAIN_COMMAND=" in text
    assert box["started"] == [("core", "wake_word_trainer")]
    # Built with the label apply-update.sh compares, then the probe, then warm.
    build = next(c for c in box["calls"] if c[:2] == ["docker", "build"])
    assert f"org.domovoi.wake-trainer.src={'a' * 40}" in build
    kinds = [c[1] for c in box["calls"]]
    assert kinds.index("build") < kinds.index("run")
    warm = [c for c in box["calls"] if c[-1] == "--warm"]
    assert warm and "domovoi_oww_cache:/data/cache" in warm[0]


@pytest.mark.asyncio
async def test_a_gpu_docker_cant_reach_stops_and_asks(box):
    box["state"].update(gpus=GPU, runtimes=["nvidia", "runc"], probe_ok=False)
    with egress.override_policy("always"):
        await wts.start(cpu_ok=False)
        await _finish()
        st = await wts.status()
        assert st["job"]["state"] == "failed" and st["job"]["needs_cpu_confirm"] is True
        assert "can't reach the GPU" in st["job"]["error"]
        assert settings.wake_word_trainer_enabled is False      # nothing switched on
        assert not any(c[-1] == "--warm" for c in box["calls"])
        # The household says yes to the CPU.
        await wts.start(cpu_ok=True)
        await _finish()
    st = await wts.status()
    assert st["job"]["state"] == "done" and settings.wake_word_trainer_enabled is True
    assert wts.recorded_gpu() is False


@pytest.mark.asyncio
async def test_a_gpu_without_the_toolkit_says_so_and_estimates_the_cpu(box):
    box["state"].update(gpus=GPU)          # runtimes: no nvidia
    with egress.override_policy("always"):
        req = await wts.requirements(force=True)
    assert req["gpu"] is False and req["estimate"]["device"] == "cpu"
    assert any("NVIDIA Container Toolkit" in n for n in req["notes"])


# ─── failures stay ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_failed_build_is_kept_with_its_output(box):
    box["state"].update(gpus=GPU, runtimes=["nvidia"], build_rc=1)
    with egress.override_policy("always"):
        await wts.start()
        await _finish()
    wts.reset_for_tests()                   # a reload, or another process
    st = await wts.status()
    assert st["job"]["state"] == "failed" and st["job"]["stage"] == "image"
    assert "Building the trainer image failed" in st["job"]["error"]
    assert st["job"]["log_tail"] == ["#20 ERROR: build failed"]
    assert settings.wake_word_trainer_enabled is False


@pytest.mark.asyncio
async def test_a_failed_download_is_kept(box):
    box["state"].update(warm_rc=4)
    with egress.override_policy("always"):
        await wts.start(cpu_ok=True)
        await _finish()
    st = await wts.status()
    assert st["job"]["stage"] == "data" and "Downloading the training data failed" in st["job"]["error"]


def test_a_job_the_core_died_in_reads_interrupted(box):
    path = wts.state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"state": "running", "stage": "data", "percent": 60}', encoding="utf-8")
    wts.reset_for_tests()
    st = wts._load()
    assert st["state"] == "failed" and "Interrupted" in st["error"]


@pytest.mark.asyncio
async def test_progress_follows_the_build_steps(box):
    seen: list[int] = []
    real = wts._progress

    def spy(stage, frac, message):
        real(stage, frac, message)
        seen.append(wts._load()["percent"])

    import domovoi.wake_trainer_setup as mod
    mod._progress = spy
    try:
        with egress.override_policy("always"):
            await wts.start(cpu_ok=True)
            await _finish()
    finally:
        mod._progress = real
    assert seen == sorted(seen) and seen[-1] >= 97


# ─── the command and its --stop ───────────────────────────────────────────

def test_the_stop_command_is_derived_from_the_repos_wrapper_only():
    cmd = wts.default_train_command()
    stop = wts.wrapper_stop_argv(cmd)
    assert stop is not None and stop[-1] == "--stop" and Path(stop[-2]) == wts.wrapper_path()
    assert wts.wrapper_stop_argv("train --clips {clips_dir} {out}") is None


def test_a_set_time_limit_wins(box, monkeypatch):
    monkeypatch.setattr(settings, "wake_word_train_timeout_sec", 4321)
    assert wts.train_timeout_sec() == 4321
    monkeypatch.setattr(settings, "wake_word_train_timeout_sec", 0)
    assert wts.train_timeout_sec() == 7200       # nothing recorded: the CPU floor


# ─── review fix round (2026-10-13) ────────────────────────────────────────

def _memory_cap_gb(total_ram_gb: float) -> int:
    """windows/common/WslConfig.cs MemoryCapGb, transcribed (what the
    Windows installer gives the distro)."""
    labelled = round(total_ram_gb)
    if labelled < 24:
        return 10
    return max(16, min(64, labelled - 8))


@pytest.mark.asyncio
@pytest.mark.parametrize("pc_gb", [16, 24, 28, 30])
async def test_on_windows_the_memory_refusal_is_in_the_pcs_terms(box, pc_gb):
    """The distro gets the PC's memory minus 8 GB: a 24 GB PC was told "this
    server has 16 GB; training needs a 24 GB server" (review)."""
    box["state"]["wsl"] = True
    box["state"]["mem_mib"] = _memory_cap_gb(pc_gb) * 1024 - 300   # MemTotal reads a bit under
    with egress.override_policy("always"):
        req = await wts.requirements(force=True)
    text = " ".join(req["refusals"])
    assert "Training needs a PC with about 32 GB of memory or more" in text
    assert ("this PC has less than 24 GB" if pc_gb < 24 else f"this PC has about {pc_gb} GB") in text
    assert "server" not in text
    assert req["memory"]["pc_need_gb"] == 32


@pytest.mark.asyncio
async def test_on_windows_a_32_gb_pc_is_enough(box):
    box["state"].update(wsl=True, mem_mib=_memory_cap_gb(32) * 1024 - 300)
    with egress.override_policy("always"):
        req = await wts.requirements(force=True)
    assert req["refusals"] == [] and req["memory"]["pc"] == "about 32 GB"


@pytest.mark.asyncio
async def test_on_windows_no_disk_number_and_the_person_confirms_c(box):
    """Docker's data root is inside the distro's 1 TB virtual disk ("1015 GB
    free" in the e2e distro) and the distro can't see C:: no number, no
    refusal from it, and "Turn on" needs the person's word for C:."""
    box["state"].update(wsl=True, free_gb=1015.0)
    with egress.override_policy("always"):
        req = await wts.requirements(force=True)
        assert req["disk"]["free_gb"] is None and req["disk"]["confirm_gb"] == 40
        assert req["refusals"] == []
        assert any("can't see how much space is free on the Windows drive (C:)" in n
                   for n in req["notes"])
        with pytest.raises(wts.Refused, match="Confirm that C: has at least 40 GB free"):
            await wts.start(cpu_ok=True)
        await wts.start(cpu_ok=True, disk_ok=True)
        await _finish()
    assert (await wts.status())["job"]["state"] == "done"


@pytest.mark.asyncio
async def test_elsewhere_the_disk_is_measured_and_no_confirm_is_asked(box):
    with egress.override_policy("always"):
        req = await wts.requirements(force=True)
        assert req["disk"]["free_gb"] == 500.0 and req["disk"]["confirm_gb"] is None
        await wts.start(cpu_ok=True)
        await _finish()


@pytest.mark.asyncio
async def test_two_overlapping_turn_ons_start_one_job(box, monkeypatch):
    """Review: both passed the job check while the first was still in
    ``requirements`` (seconds on a loaded box), so two jobs ran and the
    second one's cleanup removed the first one's download container. The
    review's own test (functional-testing/review-wake-gpu), now passing."""
    real_requirements = wts.requirements
    jobs: list[str] = []

    async def slow_requirements(force: bool = False):
        out = await real_requirements(force=force)
        await asyncio.sleep(0.3)
        return out

    async def fake_job(cpu_ok, gpus, ncpu, physical):
        jobs.append("job")
        await asyncio.sleep(0.5)

    monkeypatch.setattr(wts, "requirements", slow_requirements)
    monkeypatch.setattr(wts, "_job", fake_job)
    with egress.override_policy("always"):
        results = await asyncio.gather(
            wts.start(cpu_ok=True), wts.start(cpu_ok=True), return_exceptions=True)
        await asyncio.sleep(0.8)
    refused = [r for r in results if isinstance(r, wts.Refused)]
    assert len(jobs) == 1 and len(refused) == 1, results
    assert "already being set up" in str(refused[0])


@pytest.mark.asyncio
async def test_internet_never_stops_the_job_and_its_download(box, monkeypatch):
    """Review: answering "never" mid-setup let the 18 GB download run on.
    The internet_access hook (main.py) calls on_internet_answer: the job
    stops, the download container goes, and the job says why."""
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    removed: list[str] = []

    def stream(argv, timeout, on_line, on_cancel=None):
        box["calls"].append(argv)
        if argv[:2] == ["docker", "build"]:
            return 0, []
        loop.call_soon_threadsafe(started.set)
        assert wts._CANCEL.wait(10), "never told to stop"
        if on_cancel is not None:
            on_cancel()
        return None, [f"stopped: {wts._CANCEL_REASON}"]

    monkeypatch.setattr(wts, "_stream", stream)
    monkeypatch.setattr(wts, "_remove_warm", lambda: removed.append("warm"))
    rebuild: list[str] = []
    monkeypatch.setattr(wts, "stop_background_rebuild", lambda reason: rebuild.append(reason))
    with egress.override_policy("always"):
        await wts.start(cpu_ok=True)
        await asyncio.wait_for(started.wait(), 10)
    with egress.override_policy("never"):
        wts.on_internet_answer()
        await _finish()
        await asyncio.sleep(0.1)
    st = await wts.status()
    assert st["job"]["state"] == "failed"
    assert st["job"]["error"].startswith("Stopped: internet access was turned off")
    assert "warm" in removed and rebuild and settings.wake_word_trainer_enabled is False


@pytest.mark.asyncio
async def test_internet_still_allowed_stops_nothing(box, monkeypatch):
    rebuild: list[str] = []
    monkeypatch.setattr(wts, "stop_background_rebuild", lambda reason: rebuild.append(reason))
    with egress.override_policy("always"):
        wts.on_internet_answer()
        await asyncio.sleep(0.05)
    assert rebuild == [] and not wts._CANCEL.is_set()


def test_the_update_rebuild_is_stopped_through_its_unit(box, monkeypatch):
    """Only the unit's main process, and only when it is a docker build."""
    killed: list[int] = []
    procs = {"cmd": b"/usr/bin/docker\0build\0-t\0domovoi-wake-trainer:latest\0"}

    class R:
        returncode, stdout, stderr = 0, "4242\n", ""

    monkeypatch.setattr(wts, "_run", lambda argv, t: R())
    monkeypatch.setattr(wts.os, "kill", lambda pid, sig: killed.append(pid), raising=False)
    real_read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: procs["cmd"]
                        if self.as_posix() == "/proc/4242/cmdline" else real_read(self))
    assert wts.stop_background_rebuild("internet off") is True and killed == [4242]
    procs["cmd"] = b"/usr/bin/sleep\0600\0"
    assert wts.stop_background_rebuild("internet off") is False and killed == [4242]
    R.stdout = "0\n"                          # the unit isn't running
    assert wts.stop_background_rebuild("internet off") is False


@pytest.mark.asyncio
async def test_a_card_docker_cant_use_asks_for_the_gpu_runtime_again(box):
    """Turn on with an NVIDIA card and no runtime leaves the flag
    apply-update.sh reads: the next update or Restart sets the runtime up
    again instead of waiting out its day after a failure."""
    box["state"].update(gpus=GPU)            # runtimes: no nvidia
    with egress.override_policy("always"):
        await wts.start(cpu_ok=True)
        await _finish()
    assert wts.gpu_runtime_retry_flag().is_file()


@pytest.mark.asyncio
async def test_no_flag_when_docker_has_the_runtime(box):
    box["state"].update(gpus=GPU, runtimes=["nvidia", "runc"])
    with egress.override_policy("always"):
        await wts.start(cpu_ok=True)
        await _finish()
    assert not wts.gpu_runtime_retry_flag().exists()


def _footprint_docker(monkeypatch, calls, image=True, volume=True):
    df = ('[{"Name":"domovoi_oww_cache","Size":"17.9GB"},'
          '{"Name":"other","Size":"1GB"}]')

    def run(argv, timeout):
        calls.append(argv)

        class R:
            returncode, stdout, stderr = 0, "", ""
        r = R()
        if argv[:3] == ["docker", "image", "inspect"]:
            r.returncode, r.stdout = (0, "12400000000\n") if image else (1, "")
        elif argv[:3] == ["docker", "volume", "inspect"]:
            r.returncode = 0 if volume else 1
        elif argv[:3] == ["docker", "system", "df"]:
            r.stdout = df
        return r

    monkeypatch.setattr(wts, "_run", run)


def test_the_footprint_is_dockers_own_sizes(box, monkeypatch):
    calls: list[list[str]] = []
    _footprint_docker(monkeypatch, calls)
    fp = wts.footprint()
    assert fp["image_bytes"] == 12_400_000_000 and fp["volume_bytes"] == 17_900_000_000
    assert fp["total_bytes"] == 30_300_000_000
    _footprint_docker(monkeypatch, calls, image=False, volume=False)
    fp = wts.footprint()
    assert fp["image_bytes"] is None and fp["volume_bytes"] is None and fp["total_bytes"] is None


@pytest.mark.asyncio
async def test_turn_off_frees_the_space_and_switches_off(box, monkeypatch):
    calls: list[list[str]] = []
    _footprint_docker(monkeypatch, calls)
    sql: list[str] = []

    class S:
        async def execute(self, stmt, *a, **k):
            sql.append(str(stmt))

    from contextlib import asynccontextmanager

    from domovoi.db import session as db_session

    @asynccontextmanager
    async def scope():
        yield S()

    monkeypatch.setattr(db_session, "session_scope", scope)
    with egress.override_policy("always"):
        await wts.start(cpu_ok=True)
        await _finish()
    assert settings.wake_word_trainer_enabled is True
    out = await wts.turn_off()
    assert out["errors"] == [] and out["freed"]["total_bytes"] == 30_300_000_000
    assert ["docker", "image", "rm", "-f", "domovoi-wake-trainer:latest"] in calls
    assert ["docker", "volume", "rm", "-f", "domovoi_oww_cache"] in calls
    assert ["docker", "builder", "prune", "-f"] in calls
    assert settings.wake_word_trainer_enabled is False and settings.wake_word_train_command == ""
    text = box["env"].read_text(encoding="utf-8")
    assert "WAKE_WORD_TRAINER_ENABLED=false" in text and "WHISPER_MODEL=small.en" in text
    assert any("SET status = 'recording'" in q and "WHERE status = 'training'" in q for q in sql)
    st = await wts.status()
    assert st["enabled"] is False and st["requirements"]["data_ready"] is False
    assert st["job"]["message"].startswith("Turned off")


@pytest.mark.asyncio
async def test_turn_off_waits_for_a_training_or_a_job(box, monkeypatch):
    monkeypatch.setattr(wwt, "training_active", lambda: True)
    with pytest.raises(wts.Refused, match="stop it first"):
        await wts.turn_off()
    monkeypatch.setattr(wwt, "training_active", lambda: False)
    monkeypatch.setattr(wts, "_STARTING", True)
    with pytest.raises(wts.Refused, match="being set up"):
        await wts.turn_off()
