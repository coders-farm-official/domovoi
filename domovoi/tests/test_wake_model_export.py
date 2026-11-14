"""Download a trained wake word to share (Settings → Voice → Wake words →
Download): ``GET /api/wake-words/{id}/export`` on the web, built by the
core's ``GET /v1/admin/wake/export/{id}`` (domovoi/wake_model_card.py).

* both hops are admin reads (the cookie or the Bearer);
* the zip holds ``<slug>.onnx`` (+ ``.onnx.json``), ``SHA256SUMS``,
  ``MODEL_CARD.md`` and ``LICENSE-NOTICE.md`` and nothing else;
* the card says what the server knows (from the row, the model file, the
  training record, the trainer's own config) and "not recorded" for the
  rest; "Before sharing" names the household's recordings when they went in;
* nothing private: no recordings, clip names, room, the word's display
  name, paths or addresses;
* a word that isn't ready is a 409, a missing one a 404.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from domovoi import admin_auth
from domovoi import wake_model_card as card
from domovoi.config import settings
from domovoi.tests.conftest import requires_db
from domovoi.tests.route_walk import iter_route_contexts

PRIVATE_NAME = "Alex's bedroom word"
PRIVATE_ROOM = "alex-bedroom"


def _gates(app, method: str, path: str) -> list[Any]:
    def calls(dep):
        for d in getattr(dep, "dependencies", ()):
            if d.call is not None:
                yield d.call
            yield from calls(d)

    for rc in iter_route_contexts(app.routes):
        if getattr(rc, "path", None) == path and method in (getattr(rc, "methods", None) or set()):
            return list(calls(getattr(rc, "dependant", None)))
    raise AssertionError(f"{method} {path} is not a route")


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    models = tmp_path / "home" / ".domovoi" / "wake_models"
    clips = tmp_path / "home" / ".domovoi" / "wake_clips"
    models.mkdir(parents=True)
    clips.mkdir(parents=True)
    monkeypatch.setattr(settings, "wake_models_dir", str(models))
    monkeypatch.setattr(settings, "wake_clips_dir", str(clips))
    return models, clips


def _plant(models: Path, clips: Path, slug: str, *, recordings: bool) -> bytes:
    """A model, its companion, and (for a recorded word) clips with their
    room in the sidecar and the clips folder's training note."""
    body = b"ONNX-not-really-" + slug.encode() + bytes(range(256))
    (models / f"{slug}.onnx").write_bytes(body)
    (models / f"{slug}.onnx.json").write_text('{"model": "' + slug + '"}', "utf-8")
    if recordings:
        sd = clips / slug
        (sd / ".analysis").mkdir(parents=True)
        for i in (1, 2, 3):
            (sd / f"clip_{i:04d}.wav").write_bytes(b"RIFF" + bytes(40))
            (sd / ".analysis" / f"clip_{i:04d}.json").write_text(
                json.dumps({"room": PRIVATE_ROOM, "recorded_at": "2026-11-01T10:00:00Z"}), "utf-8")
        (sd / ".trained.json").write_text(
            json.dumps({"last_clip": 3, "clips": 3, "trained_at": "2026-11-02T08:00:00Z"}), "utf-8")
    return body


def _row(slug: str, *, synthetic: bool, trained_clips: int | None) -> dict[str, Any]:
    return {"id": 7, "name": PRIVATE_NAME, "slug": slug, "phrase": "hey domovoi",
            "threshold": 0.8, "model_ref": slug, "is_default": False, "status": "ready",
            "source_room_id": PRIVATE_ROOM, "clip_count": 3, "error": None,
            "synthetic_only": synthetic, "trained_clips": trained_clips}


def _unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


# ── tiers ──────────────────────────────────────────────────────────────────

def test_both_hops_are_admin_reads():
    from domovoi.main import app as core_app
    from web.backend.main import app as web_app

    core = _gates(core_app, "GET", "/v1/admin/wake/export/{wake_word_id}")
    web = _gates(web_app, "GET", "/api/wake-words/{wake_word_id}/export")
    for gates in (core, web):
        assert admin_auth.require_admin_read in gates
        assert admin_auth.require_device_read not in gates
        assert admin_auth.require_device not in gates


# ── the zip ────────────────────────────────────────────────────────────────

def test_the_zip_holds_the_model_its_sums_the_card_and_the_notice(dirs):
    models, clips = dirs
    body = _plant(models, clips, "hey_domovoi", recordings=True)
    files = _unzip(card.build_bundle(_row("hey_domovoi", synthetic=False, trained_clips=118)))
    assert sorted(files) == sorted(f"hey_domovoi/{n}" for n in (
        "hey_domovoi.onnx", "hey_domovoi.onnx.json", "SHA256SUMS", "MODEL_CARD.md",
        "LICENSE-NOTICE.md"))
    assert files["hey_domovoi/hey_domovoi.onnx"] == body
    sums = files["hey_domovoi/SHA256SUMS"].decode()
    for name, data in files.items():
        if name.endswith("SHA256SUMS"):
            continue
        assert f"{hashlib.sha256(data).hexdigest()}  {name.split('/', 1)[1]}\n" in sums
    assert card.bundle_name("hey_domovoi") == "hey_domovoi-wake-word.zip"


def test_the_card_of_a_word_trained_on_recordings(dirs):
    models, clips = dirs
    body = _plant(models, clips, "domovoi", recordings=True)
    md = _unzip(card.build_bundle(_row("domovoi", synthetic=False, trained_clips=118)))[
        "domovoi/MODEL_CARD.md"].decode()
    assert '"hey domovoi"' in md
    assert "| Recommended threshold | 0.8 " in md
    assert hashlib.sha256(body).hexdigest() in md
    assert f"| Size | {len(body)} bytes |" in md
    assert "| Synthetic voices only | no |" in md
    assert "| The household's recordings used | 118 |" in md
    # No training record yet: the date comes from the clips folder's note,
    # the knobs are "not recorded", the score likewise with how to get one.
    assert "2026-11-02T08:00:00Z (from the clips folder's training note)" in md
    assert "| Training steps | not recorded |" in md
    assert "Score clips, then Download again" in md
    assert "False wakes per hour: not recorded. To measure" in md
    # Before sharing names the recordings and what the .onnx holds.
    before = md.split("## Before sharing", 1)[1]
    assert "118 recordings of the household" in before
    assert "learned weights, not the recordings" in before
    assert "decision" in before
    assert "CC BY-NC-SA 4.0" in before


def test_the_card_of_a_synthetic_only_word(dirs):
    models, clips = dirs
    _plant(models, clips, "hey_domovoi_synthetic", recordings=False)
    md = _unzip(card.build_bundle(_row("hey_domovoi_synthetic", synthetic=True, trained_clips=0)))[
        "hey_domovoi_synthetic/MODEL_CARD.md"].decode()
    assert "| Synthetic voices only | yes |" in md
    assert "| The household's recordings used | 0 (trained on synthetic voices only) |" in md
    before = md.split("## Before sharing", 1)[1]
    assert "nobody's recordings went into it" in before
    assert "learned weights" not in before
    assert "recordings of the household" not in md
    # No marker and no record: the date is the file's, labelled as such.
    assert "| Trained | not recorded; the model file was written " in md


def test_the_card_reads_the_trainer_s_own_config(dirs):
    """The datasets, their pins and the trainer commits come from
    scripts/wake_word/docker, not a hard-coded list."""
    models, clips = dirs
    _plant(models, clips, "domovoi", recordings=False)
    cfg = card.trainer_config()
    assert cfg["FEATURES_REPO"] == "davidscripka/openwakeword_features"
    assert re.fullmatch(r"[0-9a-f]{40}", cfg["FEATURES_REVISION"])
    assert re.fullmatch(r"[0-9a-f]{40}", cfg["OWW_REF"])
    assert cfg["VOICE_FILE"].endswith(".pt") and re.fullmatch(r"[0-9a-f]{64}", cfg["VOICE_SHA256"])
    assert cfg["env_defaults"]["WAKE_N_SAMPLES"]
    files = _unzip(card.build_bundle(_row("domovoi", synthetic=True, trained_clips=0)))
    md = files["domovoi/MODEL_CARD.md"].decode()
    notice = files["domovoi/LICENSE-NOTICE.md"].decode()
    assert cfg["FEATURES_REVISION"] in md and cfg["OWW_REF"] in md and cfg["RIRS_REVISION"] in md
    for sha in cfg["FEATURES_SHA256"].values():
        assert sha in md
    for licence in ("CC BY-NC-SA 4.0 (non-commercial, share-alike)", "educational use only",
                    "no licence stated", "Apache-2.0"):
        assert licence in md, licence
    for who in ("ACAV100M", "MUSDB18", "FSD50K", "FMA", "LibriTTS-R", "openWakeWord",
                "McDermott"):
        assert who in notice, who
    assert "Creative Commons Attribution-NonCommercial-ShareAlike 4.0" in notice
    assert "Domovoi's code is MIT" in notice


def test_a_dataset_the_table_doesnt_know_says_so(dirs, tmp_path):
    d = tmp_path / "docker"
    d.mkdir()
    (d / "entrypoint.py").write_text(
        'RIRS_REPO = "someone/new_echoes"\nRIRS_REVISION = "abc"\n', "utf-8")
    rows = card.datasets_in_use(card.trainer_config(d))
    assert rows[0]["source"] == "huggingface.co/datasets/someone/new_echoes"
    assert "licence not recorded" in rows[0]["licence"]
    assert card.datasets_in_use(card.trainer_config(tmp_path / "missing")) == []


def test_the_training_record_and_score_fill_the_card(dirs):
    models, clips = dirs
    _plant(models, clips, "domovoi", recordings=True)
    card.record_training(
        "domovoi", synthetic_only=False, recordings=118,
        env={"WAKE_TRAINER_STEPS": "40000", "WAKE_TRAINER_N_SAMPLES": ""},
        gpu=True, trainer_source="a" * 40, domovoi_version="1.1.0 (abc1234)")
    card.record_score("domovoi", {"threshold": 0.8, "n_clips": 118, "raw_recall": 0.97,
                                  "raw_max": 0.999, "trimmed_recall": 0.95, "trimmed_max": 0.99,
                                  "silence_score": 0.001, "clips": ["clip_0001.wav"]})
    md = _unzip(card.build_bundle(_row("domovoi", synthetic=False, trained_clips=118)))[
        "domovoi/MODEL_CARD.md"].decode()
    assert "| Training steps | 40000 |" in md
    assert "| Synthetic clips | 5000 (the trainer's default) |" in md
    assert "| Domovoi version | 1.1.0 (abc1234) |" in md
    assert "git tree " + "a" * 40 in md
    assert "| Trained on | GPU |" in md
    assert "| Recordings that woke it (raw) | 0.97 |" in md
    assert "clip_0001" not in md
    # A retrained (different) model file: neither record describes it.
    (models / "domovoi.onnx").write_bytes(b"a newer model")
    md = _unzip(card.build_bundle(_row("domovoi", synthetic=False, trained_clips=118)))[
        "domovoi/MODEL_CARD.md"].decode()
    assert "| Training steps | not recorded |" in md
    assert "0.97" not in md
    # Deleting the word forgets its record.
    card.forget("domovoi")
    assert card.read_record("domovoi") == {}


def test_the_records_live_outside_the_served_models_folder(dirs):
    models, _ = dirs
    path = card.record_path("domovoi")
    assert path is not None and models.resolve() not in path.resolve().parents
    assert card.record_path("../evil") is None


def test_nothing_private_goes_in(dirs, tmp_path):
    models, clips = dirs
    _plant(models, clips, "hey_domovoi", recordings=True)
    card.record_score("hey_domovoi", {"threshold": 0.8, "n_clips": 3, "raw_recall": 1.0,
                                      "raw_max": 0.9, "trimmed_recall": None, "trimmed_max": None,
                                      "silence_score": None})
    for synthetic in (False, True):
        files = _unzip(card.build_bundle(_row("hey_domovoi", synthetic=synthetic,
                                                  trained_clips=0 if synthetic else 3)))
        assert not any(n.endswith(".wav") for n in files)
        for name, data in files.items():
            if name.endswith(".onnx"):
                continue
            t = data.decode("utf-8")
            for secret in (PRIVATE_NAME, "Alex", "alex", "bedroom", "clip_0", ".wav",
                           str(tmp_path), tmp_path.as_posix(), str(models), "wake_clips",
                           str(Path.home()), Path.home().as_posix()):
                assert secret not in t, (name, secret)
            assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", t), name


def test_a_missing_model_file_is_refused(dirs):
    with pytest.raises(FileNotFoundError):
        card.build_bundle(_row("gone", synthetic=True, trained_clips=0))
    with pytest.raises(FileNotFoundError):
        card.build_bundle(_row("../escape", synthetic=True, trained_clips=0))


# ── the core route, against the real table ────────────────────────────────

async def _seed(*, status: str, slug: str = "hey_domovoi") -> int:
    from domovoi.db.repositories import WakeWordsRepository
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        await s.execute(text("TRUNCATE wake_words RESTART IDENTITY CASCADE"))
        repo = WakeWordsRepository(s)
        wid = await repo.create(name=PRIVATE_NAME, slug=slug, phrase="hey domovoi",
                                threshold=0.8, source_room_id=PRIVATE_ROOM)
        if status == "ready":
            await repo.mark_ready(wid, slug, 3)
    return wid


@requires_db
def test_the_core_route_serves_the_zip_for_a_ready_word(dirs):
    from domovoi import main

    models, clips = dirs
    _plant(models, clips, "hey_domovoi", recordings=True)
    wid = asyncio.run(_seed(status="ready"))
    resp = asyncio.run(main.admin_wake_export(wid))
    assert resp.media_type == "application/zip"
    assert 'filename="hey_domovoi-wake-word.zip"' in resp.headers["content-disposition"]
    files = _unzip(resp.body)
    assert "hey_domovoi/MODEL_CARD.md" in files
    assert PRIVATE_ROOM not in files["hey_domovoi/MODEL_CARD.md"].decode()


@requires_db
def test_the_core_route_refuses_a_word_not_ready_or_missing(dirs):
    from domovoi import main

    models, clips = dirs
    _plant(models, clips, "hey_domovoi", recordings=True)
    wid = asyncio.run(_seed(status="recording"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_wake_export(wid))
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_wake_export(wid + 999))
    assert e.value.status_code == 404
    # Ready, but its model file is gone: 409 with the way out.
    wid = asyncio.run(_seed(status="ready", slug="lost"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_wake_export(wid))
    assert e.value.status_code == 409 and "train it again" in e.value.detail


# ── the web hop ────────────────────────────────────────────────────────────

def test_the_web_hop_passes_the_zip_and_the_refusals_through(monkeypatch):
    from fastapi.testclient import TestClient

    import web.backend.api.wake_words as wake_api
    from web.backend.main import app as web_app

    answers: list[tuple[int, bytes, dict]] = [
        (200, b"PK-zip", {"content-disposition": 'attachment; filename="domovoi-wake-word.zip"'}),
        (409, b'{"detail": "no trained model to download"}', {}),
        (404, b'{"detail": "wake word 9 not found"}', {}),
        (0, b"", {}),
    ]
    seen: list[str] = []

    async def fake_get(path, timeout=30.0, headers=None):
        seen.append(path)
        return answers.pop(0)

    monkeypatch.setattr(wake_api, "get_admin_bytes", fake_get)
    with TestClient(web_app, headers={"X-Requested-With": "domovoi-tests"}) as client:
        r = client.get("/api/wake-words/9/export")
        assert r.status_code == 200 and r.content == b"PK-zip"
        assert r.headers["content-type"] == "application/zip"
        assert 'filename="domovoi-wake-word.zip"' in r.headers["content-disposition"]
        r = client.get("/api/wake-words/9/export")
        assert r.status_code == 409 and "no trained model" in r.json()["detail"]
        assert client.get("/api/wake-words/9/export").status_code == 404
        assert client.get("/api/wake-words/9/export").status_code == 502
    assert seen[0] == "/v1/admin/wake/export/9"


# ── the dashboard ──────────────────────────────────────────────────────────

def test_the_download_button_shows_only_on_a_ready_word():
    src = (Path(__file__).resolve().parents[2] / "web" / "static" / "settings.jsx").read_text("utf-8")
    i = src.index("{/* Download: the trained model packed to share")
    block = src[i:i + 900]
    # canPush is `w.status === 'ready'`.
    assert "{canPush && onDownload && (" in block
    assert "const canPush = w.status === 'ready';" in src
    assert ">{downloading ? 'Downloading…' : 'Download'}</Button>" in block
    assert "the model and a model card, ready to share" in block
    assert "apiFetchRaw(`/api/wake-words/${w.id}/export`)" in src


def test_deleting_a_word_forgets_its_record(dirs, monkeypatch):
    """The web's delete calls the core's forget, which drops the record."""
    from domovoi import main
    from domovoi.workers import wake_word_trainer as wwt

    models, clips = dirs
    _plant(models, clips, "domovoi", recordings=False)
    card.record_training("domovoi", synthetic_only=True, recordings=0, env={}, gpu=None,
                         trainer_source=None, domovoi_version=None)
    assert card.read_record("domovoi")
    monkeypatch.setattr(wwt, "forget_word", lambda slug, wait_sec=120.0: True)

    async def run():
        await main.admin_wake_forget(main._AdminWakeForgetBody(slug="domovoi"))
        await asyncio.sleep(0)

    asyncio.run(run())
    assert card.read_record("domovoi") == {}
