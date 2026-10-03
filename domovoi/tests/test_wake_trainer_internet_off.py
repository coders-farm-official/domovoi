"""B12: under INTERNET_ACCESS=never the wake-word trainer does not start
the train command (its recipe downloads several GB of training data the
first time). The queued word is marked failed with a retryable "needs the
internet" message; a user-started job may fail visibly (CONTRACT 5.12).
Unset keeps today's behaviour. DB-free: the queue and session are fakes.
"""

from __future__ import annotations

import subprocess
from contextlib import asynccontextmanager

import pytest

from domovoi import egress
from domovoi.config import settings
from domovoi.db import repositories
from domovoi.workers import wake_word_trainer as wwt


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
    FakeQueue.rows = [{"id": 7, "slug": "hey_hearth", "phrase": "hey hearth"}]
    FakeQueue.failed, FakeQueue.ready = [], []

    @asynccontextmanager
    async def scope():
        yield FakeSession()

    monkeypatch.setattr(wwt, "session_scope", scope)
    monkeypatch.setattr(repositories, "WakeWordsRepository", FakeQueue)
    monkeypatch.setattr(settings, "wake_word_train_command", "train {clips_dir} {phrase} {out}")
    monkeypatch.setattr(settings, "wake_clips_dir", str(tmp_path / "clips"))
    monkeypatch.setattr(settings, "wake_models_dir", str(tmp_path / "models"))
    runs: list[list[str]] = []

    def fake_run(argv, **kw):
        runs.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr(wwt.subprocess, "run", fake_run)
    return runs


@pytest.mark.asyncio
async def test_never_marks_the_word_failed_without_running_the_command(queue) -> None:
    with egress.override_policy("never"):
        n = await wwt.WakeWordTrainer().tick()
    assert n == 1
    assert queue == []  # the train command never started
    assert FakeQueue.failed == [(7, wwt._INTERNET_OFF_MSG)]
    assert "training needs the internet the first time it runs" in wwt._INTERNET_OFF_MSG
    assert egress.TURNED_OFF_REASON in wwt._INTERNET_OFF_MSG


@pytest.mark.parametrize("answer", ["", "always", "sometimes"])
@pytest.mark.asyncio
async def test_otherwise_the_command_runs_as_today(queue, answer) -> None:
    with egress.override_policy(answer):
        n = await wwt.WakeWordTrainer().tick()
    assert n == 1
    assert len(queue) == 1 and queue[0][0] == "train"
    assert FakeQueue.failed and FakeQueue.failed[0][1].startswith("train command exited 1")
