"""What the wake-word worker hands the training command, and when it runs it.

* The trainer's knobs reach the wrapper: the settings (Settings →
  Configuration → Wake words) and any other ``WAKE_TRAINER_*`` line in
  ``domovoi/.env`` become the command's environment. Before this a
  ``.env`` line never got there (the core reads ``.env`` into its
  settings, not into ``os.environ``), so a knob needed a systemd drop-in.
* The time limit is a setting, and the wrapper's own deadline sits a
  minute under it.
* The phrase's pronunciations come from Domovoi's own phonemizer (the
  "say it like" list first: "Domovoi = DOH-moe-voy").
* One at a time: nothing trains while training is switched off or being
  set up, ``training_active()`` holds during a run, and a core shutdown
  stops the run (the row stays queued) instead of leaving it running.

DB-free: the queue and session are fakes, the runner is patched.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from domovoi import config_env_writer, egress
from domovoi import wake_trainer_setup as wts
from domovoi.config import settings
from domovoi.db import repositories
from domovoi.workers import wake_word_trainer as wwt

WRAPPER = (Path(__file__).resolve().parents[2] / "scripts" / "wake_word"
           / "train_wake_word.py")


class FakeSession:
    slugs: list[str] = ["hey_domovoi", "hey_domo"]

    async def execute(self, stmt, *a, **k):
        if "SELECT slug FROM wake_words" in str(stmt):
            class Rows:
                def all(self_inner):
                    return [(x,) for x in FakeSession.slugs]
            return Rows()
        return None


class FakeQueue:
    rows: list[dict] = []
    failed: list[tuple[int, str]] = []
    ready: list[tuple[int, str]] = []
    stopped: list[int] = []

    def __init__(self, _s) -> None:
        pass

    async def next_training(self):
        return FakeQueue.rows.pop(0) if FakeQueue.rows else None

    async def mark_failed(self, wid, error):
        FakeQueue.failed.append((wid, error))

    async def mark_ready(self, wid, ref):
        FakeQueue.ready.append((wid, ref))

    async def mark_stopped(self, wid):
        FakeQueue.stopped.append(wid)
        return True


@pytest.fixture
def queue(monkeypatch, tmp_path):
    FakeQueue.rows = [{"id": 7, "slug": "hey_domovoi", "phrase": "hey domovoi"}]
    FakeQueue.failed, FakeQueue.ready, FakeQueue.stopped = [], [], []

    @asynccontextmanager
    async def scope():
        yield FakeSession()

    monkeypatch.setattr(wwt, "session_scope", scope)
    monkeypatch.setattr(repositories, "WakeWordsRepository", FakeQueue)
    monkeypatch.setattr(settings, "wake_word_trainer_enabled", True)
    monkeypatch.setattr(settings, "wake_word_train_command",
                        f'python "{WRAPPER}" --clips "{{clips_dir}}" --phrase "{{phrase}}" '
                        f'--slug "{{slug}}" --out "{{out}}"')
    monkeypatch.setattr(settings, "wake_clips_dir", str(tmp_path / "clips"))
    monkeypatch.setattr(settings, "wake_models_dir", str(tmp_path / "models"))
    env_file = tmp_path / ".env"
    env_file.write_text("WAKE_TRAINER_SHM_SIZE=4g\nWAKE_TRAINER_CPUSET=16-31\n", encoding="utf-8")
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", env_file)
    for k in ("WAKE_TRAINER_SHM_SIZE", "WAKE_TRAINER_CPUSET", "WAKE_TRAINER_N_SAMPLES",
              "WAKE_PRONUNCIATIONS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(wwt, "_SWEPT", True)      # the sweep has its own test
    wts.reset_for_tests()
    yield
    wts.reset_for_tests()


def _capture(monkeypatch, result=None, during=None):
    seen: dict = {}

    def fake(argv, **kw):
        seen["argv"] = argv
        seen.update(kw)
        if during:
            during(seen)
        return result or wwt.TrainRun(1, "boom")

    monkeypatch.setattr(wwt, "run_train_command", fake)
    return seen


@pytest.mark.asyncio
async def test_knobs_from_settings_and_env_reach_the_wrapper(queue, monkeypatch):
    monkeypatch.setattr(settings, "wake_trainer_n_samples", 20000)
    monkeypatch.setattr(settings, "wake_trainer_steps", 0)          # 0 = the image's own
    monkeypatch.setattr(settings, "wake_trainer_cpus", 6.5)
    monkeypatch.setattr(settings, "wake_trainer_memory_gb", 16)
    monkeypatch.setattr(settings, "wake_trainer_image", "domovoi-wake-trainer:latest")
    monkeypatch.setattr(settings, "wake_word_train_timeout_sec", 5400)
    monkeypatch.setenv("WAKE_TRAINER_STEPS", "999")                 # a setting of 0 clears it
    seen = _capture(monkeypatch)
    with egress.override_policy("always"):
        assert await wwt.WakeWordTrainer().tick() == 1
    env = seen["env"]
    assert env["WAKE_TRAINER_N_SAMPLES"] == "20000"
    assert "WAKE_TRAINER_STEPS" not in env
    assert env["WAKE_TRAINER_CPUS"] == "6.5"
    assert env["WAKE_TRAINER_MEMORY"] == "16g"
    assert env["WAKE_TRAINER_IMAGE"] == "domovoi-wake-trainer:latest"
    # .env lines that aren't settings get there too.
    assert env["WAKE_TRAINER_SHM_SIZE"] == "4g"
    assert env["WAKE_TRAINER_CPUSET"] == "16-31"
    assert seen["timeout"] == 5400
    assert env["WAKE_TRAINER_DEADLINE_SEC"] == "5340"
    # The repo's wrapper: stopped through its own --stop.
    assert seen["stop_argv"][-2:] == [str(WRAPPER), "--stop"]
    assert seen["log_path"].name == "hey_domovoi.log"


@pytest.mark.asyncio
async def test_the_process_environment_wins_over_env_file(queue, monkeypatch):
    monkeypatch.setenv("WAKE_TRAINER_SHM_SIZE", "8g")
    seen = _capture(monkeypatch)
    with egress.override_policy("always"):
        await wwt.WakeWordTrainer().tick()
    assert seen["env"]["WAKE_TRAINER_SHM_SIZE"] == "8g"


@pytest.mark.asyncio
async def test_the_house_pronunciation_goes_with_the_phrase(queue, monkeypatch):
    seen = _capture(monkeypatch)
    with egress.override_policy("always"):
        await wwt.WakeWordTrainer().tick()
    # "Domovoi = DOH-moe-voy" from the shipped "say it like" list; "hey" is
    # a dictionary word and stays the trainer's.
    assert seen["env"]["WAKE_PRONUNCIATIONS"] == "domovoi=D OW1 M OW2 V OY2"


def test_an_unlisted_word_gets_the_phonemizers_guess_and_dictionary_words_none():
    got = dict(item.split("=") for item in wwt.phrase_pronunciations(
        "hello marisol tavi").split(";") if item)
    assert "hello" not in got
    assert set(got) == {"marisol", "tavi"}
    assert all(p.split() and all(ph.isupper() or ph[-1].isdigit() for ph in p.split())
               for p in got.values())


@pytest.mark.asyncio
async def test_switched_off_trains_nothing(queue, monkeypatch):
    monkeypatch.setattr(settings, "wake_word_trainer_enabled", False)
    seen = _capture(monkeypatch)
    assert await wwt.WakeWordTrainer().tick() == 0
    assert not seen and FakeQueue.rows      # still queued


@pytest.mark.asyncio
async def test_nothing_trains_while_training_is_being_set_up(queue, monkeypatch):
    monkeypatch.setattr(wts, "job_running", lambda: True)
    seen = _capture(monkeypatch)
    assert await wwt.WakeWordTrainer().tick() == 0
    assert not seen and FakeQueue.rows


@pytest.mark.asyncio
async def test_training_active_holds_during_the_run_only(queue, monkeypatch):
    seen = _capture(monkeypatch, during=lambda s: s.update(active=wwt.training_active()))
    with egress.override_policy("always"):
        await wwt.WakeWordTrainer().tick()
    assert seen["active"] is True
    assert wwt.training_active() is False


@pytest.mark.asyncio
async def test_a_shutdown_stops_the_run_and_leaves_the_word_queued(queue, monkeypatch):
    started = threading.Event()
    stopped = threading.Event()

    def blocking(argv, **kw):
        started.set()
        assert kw["cancel"].wait(30), "the cancel never came"
        stopped.set()
        return wwt.TrainRun(None, "", cancelled=True)

    monkeypatch.setattr(wwt, "run_train_command", blocking)
    with egress.override_policy("always"):
        task = asyncio.create_task(wwt.WakeWordTrainer().tick())
        while not started.is_set():
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stopped.is_set()
    assert FakeQueue.failed == [] and FakeQueue.ready == []
    assert wwt.training_active() is False


@pytest.mark.asyncio
async def test_the_first_tick_removes_a_container_a_crashed_core_left(queue, monkeypatch):
    monkeypatch.setattr(wwt, "_SWEPT", False)
    swept: list[list[str]] = []

    def fake_run(argv, **kw):
        swept.append(list(argv))

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(wwt.subprocess, "run", fake_run)
    _capture(monkeypatch)
    with egress.override_policy("always"):
        await wwt.WakeWordTrainer().tick()
        FakeQueue.rows = [{"id": 8, "slug": "hey_domo", "phrase": "hey domo"}]
        await wwt.WakeWordTrainer().tick()
    # Once, and with the words that still exist: a deleted word's training
    # files (and copies of recordings a killed run left) go too.
    assert len(swept) == 1 and swept[0][-3:] == ["--stop", "--keep-only", "hey_domovoi,hey_domo"]


@pytest.mark.asyncio
async def test_a_timeout_names_the_setting(queue, monkeypatch):
    monkeypatch.setattr(settings, "wake_word_train_timeout_sec", 0)
    monkeypatch.setattr(wts, "auto_timeout_sec", lambda: 3600)
    _capture(monkeypatch, result=wwt.TrainRun(None, "", timed_out=True))
    with egress.override_policy("always"):
        await wwt.WakeWordTrainer().tick()
    (wid, error), = FakeQueue.failed
    assert wid == 7
    assert "timed out after 3600s" in error and "Training time limit" in error


# ─── found in the end-to-end run (2026-10-13, domovoi-t8) ─────────────────

@pytest.mark.asyncio
async def test_a_core_restart_mid_training_leaves_the_word_queued(queue, monkeypatch):
    """systemctl restart domovoi-core: systemd's SIGTERM reaches the wrapper
    too, which removes its container and exits 143 a moment before the
    worker is cancelled. That is a shutdown, not a failure."""
    _capture(monkeypatch, result=wwt.TrainRun(143, "[wake-trainer] told to stop (signal 15)"))
    calls = {"n": 0}

    def stopping():
        calls["n"] += 1
        return calls["n"] > 1      # false at the tick's start, true after the run

    monkeypatch.setattr(wwt.lifecycle, "shutting_down", stopping)
    with egress.override_policy("always"):
        assert await wwt.WakeWordTrainer().tick() == 0
    assert FakeQueue.failed == [] and FakeQueue.ready == []


@pytest.mark.asyncio
async def test_a_stop_that_isnt_a_shutdown_is_a_failure_with_the_wrappers_words(queue, monkeypatch):
    monkeypatch.setattr(wwt.lifecycle, "shutting_down", lambda: False)
    monkeypatch.setattr(wwt.asyncio, "sleep", _no_sleep)
    out = "\n".join(["DEBUG:generate_samples:Batch 580/714 complete"] * 50
                    + ["[wake-trainer] told to stop (signal 15); removing the training container",
                       "[wake-trainer] removed 1 training container(s) (stopped)"])
    _capture(monkeypatch, result=wwt.TrainRun(143, out))
    with egress.override_policy("always"):
        await wwt.WakeWordTrainer().tick()
    (_, error), = FakeQueue.failed
    assert error.startswith("train command exited 143: [wake-trainer] told to stop")
    assert "DEBUG" not in error


async def _no_sleep(_s):
    return None


def test_the_error_tail_prefers_the_trainers_own_lines():
    out = "\n".join(["Training:  40%|████  | 400/1000 [00:03<00:04, 140.00it/s]",
                     "DEBUG:x:y",
                     "[entrypoint] FATAL: feature dataset download failed: 403",
                     "plain"])
    assert wwt.error_tail(out) == "[entrypoint] FATAL: feature dataset download failed: 403"
    assert wwt.error_tail("a\nb\nc") == "a\nb\nc"


@pytest.mark.asyncio
async def test_the_house_pronunciation_is_what_the_synthetic_clips_say(queue, monkeypatch):
    """espeak-ng (inside the trainer image) says DOM-oo-voy; the household's
    "say it like" list says DOH-moe-voy, and the clips are spoken from it."""
    seen = _capture(monkeypatch)
    with egress.override_policy("always"):
        await wwt.WakeWordTrainer().tick()
    assert seen["env"]["WAKE_SYNTH_IPA"] == "domovoi=dˈoʊmoʊvˌɔɪ"


def test_only_words_with_a_household_or_lexicon_pronunciation_are_sent():
    assert wwt.synthesis_ipa("hello domo") == "domo=dˈoʊmoʊ"
    assert wwt.synthesis_ipa("hey jarvis") == ""          # espeak-ng's own reading stays
    assert wwt.synthesis_ipa("hey ollama") == "ollama=oʊlˈɑːmə"   # a lexicon fix


# ─── review fix round (2026-10-13) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_puts_the_word_back_to_recording_not_queued(queue, monkeypatch):
    """The dashboard's Stop on the word that is training: the run is
    cancelled (its container and the copies of the recordings go) and the
    word goes back to "recording" with its clips; not failed, not queued."""
    started = threading.Event()

    def blocking(argv, **kw):
        started.set()
        assert kw["cancel"].wait(30), "the stop never came"
        return wwt.TrainRun(None, "", cancelled=True)

    monkeypatch.setattr(wwt, "run_train_command", blocking)
    with egress.override_policy("always"):
        task = asyncio.create_task(wwt.WakeWordTrainer().tick())
        while not started.is_set():
            await asyncio.sleep(0.05)
        assert wwt.current_training() == 7
        assert wwt.request_stop(8) is False          # not the one training
        assert wwt.request_stop(7) is True
        assert await asyncio.wait_for(task, 30) == 1
    assert FakeQueue.stopped == [7]
    assert FakeQueue.failed == [] and FakeQueue.ready == []
    assert wwt.current_training() is None and wwt.request_stop(7) is False


@pytest.mark.asyncio
async def test_set_up_for_the_gpu_the_run_must_have_it(queue, monkeypatch):
    """Review 2026-10-13: a GPU that went away (the toolkit lost, the card
    gone) trained silently on the CPU under the GPU's one-hour limit. Set
    up for the GPU, the wrapper is told to refuse the CPU (exit 75), and
    the word fails saying why."""
    monkeypatch.setattr(wts, "recorded_gpu", lambda: True)
    seen = _capture(monkeypatch, result=wwt.TrainRun(75, "[wake-trainer] ERROR: set up for the GPU"))
    with egress.override_policy("always"):
        await wwt.WakeWordTrainer().tick()
    assert seen["env"]["WAKE_TRAINER_REQUIRE_GPU"] == "1"
    (wid, error), = FakeQueue.failed
    assert wid == 7 and "set up for the GPU, and Docker can't reach it" in error
    assert "Check the GPU and trainer again" in error


@pytest.mark.asyncio
async def test_set_up_for_the_cpu_the_gpu_isnt_required(queue, monkeypatch):
    monkeypatch.setattr(wts, "recorded_gpu", lambda: False)
    monkeypatch.setenv("WAKE_TRAINER_REQUIRE_GPU", "1")      # a stale one never leaks in
    seen = _capture(monkeypatch)
    with egress.override_policy("always"):
        await wwt.WakeWordTrainer().tick()
    assert "WAKE_TRAINER_REQUIRE_GPU" not in seen["env"]
