"""WakeWordTrainer worker (Feature 5).

The trainer drains the ``training`` queue head of ``wake_words`` and shells
out to an operator-configured external Linux command. It NEVER trains
in-process (openWakeWord automatic training is Linux-only; Domovoi is
Windows). These tests never actually train: ``subprocess.run`` is
monkeypatched, so we exercise the four decision branches of ``tick()``:

  * no training row → no-op (returns 0)
  * training row + EMPTY ``wake_word_train_command`` → mark_failed with
    the runbook message (the gating default)
  * training row + a command that "succeeds" (rc=0) AND a planted
    ``<slug>.onnx`` → mark_ready(slug)
  * training row + a command that "fails" (rc!=0) → mark_failed with the
    stderr tail

The worker's lifecycle (start/stop/_run) follows the core worker template and
not re-tested here; ``tick`` is the unit under test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from domovoi.db.repositories import WakeWordsRepository
from domovoi.tests.conftest import requires_db
from domovoi.workers import wake_word_trainer as wwt
from domovoi.workers.wake_word_trainer import WakeWordTrainer


async def _seed_training_row(slug: str = "hey_domovoi") -> int:
    """Insert a wake word and drive it into the 'training' status so the
    trainer's ``next_training`` picks it up."""
    from domovoi.db.session import engine, session_scope
    from sqlalchemy import text as sql_text

    async with engine.begin() as conn:
        await conn.execute(sql_text("DELETE FROM wake_words"))
    async with session_scope() as s:
        repo = WakeWordsRepository(s)
        wid = await repo.create(name="Hey Domovoi", slug=slug, phrase="hey domovoi")
        for _ in range(3):
            await repo.bump_clip_count(wid)
        await repo.mark_training(wid, 1)
    return wid


async def _get(wid: int) -> dict:
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return await WakeWordsRepository(s).get(wid)


@requires_db
@pytest.mark.asyncio
async def test_tick_no_training_row_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    from domovoi.db.session import engine
    from sqlalchemy import text as sql_text

    async with engine.begin() as conn:
        await conn.execute(sql_text("DELETE FROM wake_words"))

    def _boom(*a, **k):  # subprocess must not be reached with an empty queue
        raise AssertionError("subprocess.run must not run when the queue is empty")

    monkeypatch.setattr(wwt.subprocess, "run", _boom)
    assert await WakeWordTrainer().tick() == 0


@requires_db
@pytest.mark.asyncio
async def test_tick_empty_command_marks_failed_with_runbook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wid = await _seed_training_row()
    monkeypatch.setattr(wwt.settings, "wake_word_train_command", "")

    def _boom(*a, **k):
        raise AssertionError("subprocess.run must not run with no command configured")

    monkeypatch.setattr(wwt.subprocess, "run", _boom)

    assert await WakeWordTrainer().tick() == 1
    row = await _get(wid)
    assert row["status"] == "failed"
    # The exact runbook sentinel surfaced on the Wake Words tab.
    assert row["error"] == wwt._NO_COMMAND_MSG
    assert "scripts/wake_word/README.md" in row["error"]


@requires_db
@pytest.mark.asyncio
async def test_tick_success_marks_ready_when_model_produced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wid = await _seed_training_row(slug="hey_domovoi")

    models_dir = tmp_path / "wake_models"
    clips_dir = tmp_path / "wake_clips"
    monkeypatch.setattr(wwt.settings, "wake_models_dir", str(models_dir))
    monkeypatch.setattr(wwt.settings, "wake_clips_dir", str(clips_dir))
    monkeypatch.setattr(
        wwt.settings,
        "wake_word_train_command",
        "train --clips {clips_dir} --phrase {phrase} --slug {slug} --out {out}",
    )

    captured_argv: list[str] = []

    def _fake_run(argv, *a, **k):
        # The trainer substitutes the placeholders before splitting.
        captured_argv.extend(argv)
        # Simulate the external pipeline materializing the model.
        out = Path(models_dir) / "hey_domovoi.onnx"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"onnx-bytes")
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(wwt.subprocess, "run", _fake_run)

    assert await WakeWordTrainer().tick() == 1

    # Placeholders were substituted into the argv.
    joined = " ".join(captured_argv)
    assert "hey_domovoi" in joined
    assert str(models_dir) in joined
    assert "hey domovoi" in joined  # the phrase

    row = await _get(wid)
    assert row["status"] == "ready"
    # model_ref is the slug (the load-bearing invariant).
    assert row["model_ref"] == "hey_domovoi"
    assert row["error"] is None


@requires_db
@pytest.mark.asyncio
async def test_tick_success_rc_but_no_model_marks_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rc==0 but the expected <slug>.onnx never appeared → failed, not
    ready (the command lied about success)."""
    wid = await _seed_training_row()
    monkeypatch.setattr(wwt.settings, "wake_models_dir", str(tmp_path / "wake_models"))
    monkeypatch.setattr(wwt.settings, "wake_clips_dir", str(tmp_path / "wake_clips"))
    monkeypatch.setattr(wwt.settings, "wake_word_train_command", "noop {out}")

    def _fake_run(argv, *a, **k):
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(wwt.subprocess, "run", _fake_run)

    assert await WakeWordTrainer().tick() == 1
    row = await _get(wid)
    assert row["status"] == "failed"
    assert "not" in row["error"].lower()  # "... was not produced"


@requires_db
@pytest.mark.asyncio
async def test_tick_nonzero_rc_marks_failed_with_stderr_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wid = await _seed_training_row()
    monkeypatch.setattr(wwt.settings, "wake_models_dir", str(tmp_path / "wake_models"))
    monkeypatch.setattr(wwt.settings, "wake_clips_dir", str(tmp_path / "wake_clips"))
    monkeypatch.setattr(wwt.settings, "wake_word_train_command", "boom {out}")

    def _fake_run(argv, *a, **k):
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="CUDA: out of memory")

    monkeypatch.setattr(wwt.subprocess, "run", _fake_run)

    assert await WakeWordTrainer().tick() == 1
    row = await _get(wid)
    assert row["status"] == "failed"
    assert "exited 2" in row["error"]
    assert "out of memory" in row["error"]


@requires_db
@pytest.mark.asyncio
async def test_tick_timeout_marks_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wedged external command that times out is failed, not left
    hanging in 'training'."""
    wid = await _seed_training_row()
    monkeypatch.setattr(wwt.settings, "wake_models_dir", str(tmp_path / "wake_models"))
    monkeypatch.setattr(wwt.settings, "wake_clips_dir", str(tmp_path / "wake_clips"))
    monkeypatch.setattr(wwt.settings, "wake_word_train_command", "hang {out}")

    def _fake_run(argv, *a, **k):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=wwt._TRAIN_TIMEOUT_SEC)

    monkeypatch.setattr(wwt.subprocess, "run", _fake_run)

    assert await WakeWordTrainer().tick() == 1
    row = await _get(wid)
    assert row["status"] == "failed"
    assert "timed out" in row["error"]
