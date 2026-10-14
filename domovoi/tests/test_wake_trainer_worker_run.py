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
    async def execute(self, *a, **k):
        return None


class FakeQueue:
    rows: list[dict] = []
    failed: list[tuple[int, str]] = []
    ready: list[tuple[int, str]] = []

    def __init__(self, _s) -> None:
        pass

    async def next_training(self):
        return FakeQueue.rows.pop(0) if FakeQueue.rows else None

    async def mark_failed(self, wid, error):
        FakeQueue.failed.append((wid, error))

    async def mark_ready(self, wid, ref):
        FakeQueue.ready.append((wid, ref))


@pytest.fixture
def queue(monkeypatch, tmp_path):
    FakeQueue.rows = [{"id": 7, "slug": "hey_domovoi", "phrase": "hey domovoi"}]
    FakeQueue.failed, FakeQueue.ready = [], []

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
    assert len(swept) == 1 and swept[0][-1] == "--stop"


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
