"""Synthetic-voices-only wake words (V047).

A word can train on the trainer's synthetic voices alone: none of the
household's recordings reach the trainer and the minimum-clips gate does not
apply. Its kind is fixed when it is made (a recorded word's synthetic twin is
its own word, own slug, own model file), and every model records how many
recordings it was trained on, so the dashboard can say which models hold
somebody's voice.

Covered here: the wrapper never mounts a clips folder for such a word; the
entrypoint uses no clip even if one is mounted; the worker hands it an empty
folder (never the word's own) and records 0; a recorded word records its
count; the web routes (create, train without clips, the synthetic copy, the
provenance fields, respelling). The preview ("Hear the training voices") is
in test_wake_preview.py.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi.tests.conftest import requires_db

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "wake_word" / "train_wake_word.py"
ENTRYPOINT = ROOT / "scripts" / "wake_word" / "docker" / "entrypoint.py"


@pytest.fixture(autouse=True)
def _leave_no_wake_words(request):
    """The rows these tests make (ready ones among them) never outlive them:
    a later module that posts to id 1 would find a trained word."""
    yield
    if request.node.get_closest_marker("skipif") is None:   # not a requires_db test
        return
    from domovoi.db.session import engine

    async def _truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE wake_words RESTART IDENTITY CASCADE"))

    try:
        asyncio.run(_truncate())
    except Exception:  # noqa: BLE001 - no database in this run
        pass


def _load(path: Path, name: str, monkeypatch=None, env: dict | None = None):
    if monkeypatch is not None:
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the host wrapper ──────────────────────────────────────────────────────

def test_wrapper_mounts_no_clips_for_a_synthetic_word():
    w = _load(WRAPPER, "_ww_synth")
    argv = w.build_run_argv("/home/alex/clips", "/home/alex/models", "hey alex", "hey_alex",
                            [], synthetic_only=True)
    assert not any(a.endswith(":/data/clips:ro") for a in argv)
    assert "WAKE_SYNTHETIC_ONLY=1" in argv
    # The models and cache mounts stay.
    assert "/home/alex/models:/data/models" in argv


def test_wrapper_mounts_the_clips_for_a_recorded_word():
    w = _load(WRAPPER, "_ww_rec")
    argv = w.build_run_argv("/home/alex/clips", "/home/alex/models", "hey alex", "hey_alex", [])
    assert "/home/alex/clips:/data/clips:ro" in argv
    assert "WAKE_SYNTHETIC_ONLY=1" not in argv


def test_wrapper_synthetic_run_needs_no_clips_folder(monkeypatch, tmp_path):
    """WAKE_SYNTHETIC_ONLY=1: a clips folder that doesn't exist is no error
    (the run goes on to Docker, which isn't there here: exit 3, not 2)."""
    w = _load(WRAPPER, "_ww_synth_main", monkeypatch, {"WAKE_SYNTHETIC_ONLY": "1"})
    monkeypatch.setattr(w, "docker_available", lambda: False)
    monkeypatch.setattr("sys.argv", ["train_wake_word.py", "--clips", str(tmp_path / "none"),
                                     "--phrase", "hey alex", "--slug", "hey_alex",
                                     "--out", str(tmp_path / "m" / "hey_alex.onnx")])
    assert w.main() == 3


def test_wrapper_recorded_run_still_needs_its_clips(monkeypatch, tmp_path):
    monkeypatch.delenv("WAKE_SYNTHETIC_ONLY", raising=False)
    w = _load(WRAPPER, "_ww_rec_main")
    monkeypatch.setattr(w, "docker_available", lambda: False)
    monkeypatch.setattr("sys.argv", ["train_wake_word.py", "--clips", str(tmp_path / "none"),
                                     "--phrase", "hey alex", "--slug", "hey_alex",
                                     "--out", str(tmp_path / "m" / "hey_alex.onnx")])
    assert w.main() == 2


# ── the container's entrypoint ────────────────────────────────────────────

def test_entrypoint_uses_no_clip_of_a_synthetic_word_even_if_mounted(monkeypatch, tmp_path):
    ep = _load(ENTRYPOINT, "_ep_synth")
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "clip_001.wav").write_bytes(b"somebody's voice")
    monkeypatch.setattr(ep, "CLIPS_IN", clips)
    monkeypatch.setenv("WAKE_SYNTHETIC_ONLY", "1")
    assert ep.real_wavs() == []
    work = tmp_path / "runs"
    assert ep.inject_real_clips(work, "hey_alex") == 0
    assert not list((work / "hey_alex" / "positive_train").glob("real_*.wav"))
    monkeypatch.delenv("WAKE_SYNTHETIC_ONLY")
    assert [p.name for p in ep.real_wavs()] == ["clip_001.wav"]


def _run_dir(root: Path) -> Path:
    d = root / "hey_alex"
    for sub in ep_synth_dirs():
        (d / sub).mkdir(parents=True)
        (d / sub / "a.wav").write_bytes(b"x")
    return d


def ep_synth_dirs():
    return ("positive_train", "positive_test", "negative_train", "negative_test")


def test_a_respelled_phrase_drops_the_old_synthetic_clips(tmp_path):
    """The run dir is kept per slug and --generate_clips skips a full
    folder: without this a respelling trained on the old spelling."""
    ep = _load(ENTRYPOINT, "_ep_spoken")
    d = _run_dir(tmp_path)
    for name in ep.FEATURE_FILES:
        (d / name).write_bytes(b"f")
    first = ep.spoken_fingerprint("hey alex", "")
    # A run dir from before the record: dropped once (can't tell what it said).
    assert ep.reset_for_spoken_phrase(d, first) is True
    assert not any((d / s).exists() for s in ep_synth_dirs())
    assert not any((d / n).exists() for n in ep.FEATURE_FILES)
    # Same phrase again: kept.
    (d / "positive_train").mkdir()
    (d / "positive_train" / "a.wav").write_bytes(b"x")
    assert ep.reset_for_spoken_phrase(d, first) is False
    assert (d / "positive_train" / "a.wav").exists()
    # Respelled, or said another way: dropped.
    assert ep.reset_for_spoken_phrase(d, ep.spoken_fingerprint("hey al ex", "")) is True
    assert not (d / "positive_train").exists()
    assert ep.spoken_fingerprint("hey alex", "") != ep.spoken_fingerprint("hey alex", "alex=ˈælɛks")
    assert ep.spoken_fingerprint("Hey Alex ", "") == ep.spoken_fingerprint("hey alex", "")


# ── the worker ────────────────────────────────────────────────────────────

def test_trainer_env_says_synthetic_only_for_that_word_only(monkeypatch):
    from domovoi.workers import wake_word_trainer as wwt

    monkeypatch.setenv("WAKE_SYNTHETIC_ONLY", "1")   # a stray value never leaks
    assert "WAKE_SYNTHETIC_ONLY" not in wwt.trainer_env(600)
    assert wwt.trainer_env(600, synthetic_only=True)["WAKE_SYNTHETIC_ONLY"] == "1"


def test_empty_clips_dir_is_empty(monkeypatch, tmp_path):
    from domovoi.workers import wake_word_trainer as wwt

    monkeypatch.setattr(wwt.settings, "wake_models_dir", str(tmp_path / "wake_models"))
    d = wwt.empty_clips_dir()
    (d / "stray.wav").write_bytes(b"x")
    (d / "sub").mkdir()
    (d / "sub" / "y.wav").write_bytes(b"y")
    assert wwt.empty_clips_dir() == d
    assert list(d.iterdir()) == []
    assert d == tmp_path / "wake_trainer" / "no_clips"


def test_count_wavs_counts_the_top_level_only(tmp_path):
    from domovoi.workers import wake_word_trainer as wwt

    (tmp_path / "a.wav").write_bytes(b"x")
    (tmp_path / "b.wav").write_bytes(b"x")
    (tmp_path / ".analysis").mkdir()
    (tmp_path / ".analysis" / "c.wav").write_bytes(b"x")
    assert wwt.count_wavs(tmp_path) == 2
    assert wwt.count_wavs(tmp_path / "missing") == 0


async def _seed(slug: str, *, synthetic: bool, clips: int = 0) -> int:
    from domovoi.db.repositories import WakeWordsRepository
    from domovoi.db.session import engine, session_scope

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM wake_words"))
    async with session_scope() as s:
        repo = WakeWordsRepository(s)
        wid = await repo.create(name=slug.replace("_", " "), slug=slug, phrase="hey alex",
                                synthetic_only=synthetic)
        for _ in range(clips):
            await repo.bump_clip_count(wid)
        assert await repo.mark_training(wid, 15) is True
    return wid


async def _get(wid: int) -> dict:
    from domovoi.db.repositories import WakeWordsRepository
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        return await WakeWordsRepository(s).get(wid)


def _fake_runner(seen: dict, models: Path, slug: str):
    from domovoi.workers.wake_word_trainer import TrainRun

    def run(argv, *, env, timeout, cancel, log_path=None, stop_argv=None, on_line=None):
        seen["argv"], seen["env"] = argv, env
        clips = Path(argv[argv.index("--clips") + 1])
        seen["clips_dir"] = clips
        seen["clips_seen"] = sorted(p.name for p in clips.glob("*.wav")) if clips.is_dir() else []
        models.mkdir(parents=True, exist_ok=True)
        (models / f"{slug}.onnx").write_bytes(b"onnx")
        return TrainRun(returncode=0, output="[entrypoint] DONE")

    return run


def _wire(monkeypatch, tmp_path):
    from domovoi.workers import wake_word_trainer as wwt

    monkeypatch.setattr(wwt.settings, "wake_word_trainer_enabled", True)
    monkeypatch.setattr(wwt.settings, "wake_models_dir", str(tmp_path / "wake_models"))
    monkeypatch.setattr(wwt.settings, "wake_clips_dir", str(tmp_path / "wake_clips"))
    monkeypatch.setattr(wwt.settings, "wake_word_train_command",
                        "train --clips {clips_dir} --phrase {phrase} --slug {slug} --out {out}")
    monkeypatch.setattr(wwt, "_SWEPT", True)
    monkeypatch.setattr(wwt, "phrase_pronunciations", lambda p: "")
    monkeypatch.setattr(wwt, "synthesis_ipa", lambda p: "")
    return wwt


@requires_db
@pytest.mark.asyncio
async def test_a_synthetic_word_trains_on_an_empty_folder_and_records_zero(monkeypatch, tmp_path):
    wwt = _wire(monkeypatch, tmp_path)
    wid = await _seed("hey_alex_synthetic", synthetic=True)
    # Even with recordings sitting in its own folder, none reach the trainer.
    own = tmp_path / "wake_clips" / "hey_alex_synthetic"
    own.mkdir(parents=True)
    (own / "clip_001.wav").write_bytes(b"somebody")
    seen: dict = {}
    monkeypatch.setattr(wwt, "run_train_command",
                        _fake_runner(seen, tmp_path / "wake_models", "hey_alex_synthetic"))

    assert await wwt.WakeWordTrainer().tick() == 1
    assert seen["clips_dir"] == tmp_path / "wake_trainer" / "no_clips"
    assert seen["clips_seen"] == []
    assert seen["env"]["WAKE_SYNTHETIC_ONLY"] == "1"
    row = await _get(wid)
    assert row["status"] == "ready" and row["trained_clips"] == 0
    assert row["synthetic_only"] is True
    # No training marker in its folder: it trained on none of those clips.
    assert not (own / ".trained.json").exists()


@requires_db
@pytest.mark.asyncio
async def test_a_recorded_word_records_how_many_recordings_it_used(monkeypatch, tmp_path):
    wwt = _wire(monkeypatch, tmp_path)
    wid = await _seed("hey_alex", synthetic=False, clips=15)
    raw = tmp_path / "wake_clips" / "hey_alex"
    raw.mkdir(parents=True)
    for i in range(1, 16):
        (raw / f"clip_{i:03d}.wav").write_bytes(b"x")
    seen: dict = {}
    monkeypatch.setattr(wwt.wake_clip_quality, "stage_selected_clips",
                        lambda d: (d, 0))   # nothing staged: the raw folder trains
    monkeypatch.setattr(wwt, "run_train_command",
                        _fake_runner(seen, tmp_path / "wake_models", "hey_alex"))

    assert await wwt.WakeWordTrainer().tick() == 1
    assert "WAKE_SYNTHETIC_ONLY" not in seen["env"]
    row = await _get(wid)
    assert row["status"] == "ready" and row["trained_clips"] == 15
    assert json.loads((raw / ".trained.json").read_text("utf-8"))["clips"] == 15


# ── the repository ────────────────────────────────────────────────────────

@requires_db
@pytest.mark.asyncio
async def test_repo_synthetic_word_trains_without_clips_and_phrase_is_held_while_training():
    from domovoi.db.repositories import WakeWordsRepository
    from domovoi.db.session import engine, session_scope

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM wake_words"))
    async with session_scope() as s:
        repo = WakeWordsRepository(s)
        rec = await repo.create(name="Hey Alex", slug="hey_alex", phrase="hey alex")
        syn = await repo.create(name="Hey Alex 2", slug="hey_alex_2", phrase="hey alex",
                                synthetic_only=True)
        assert await repo.mark_training(rec, 15) is False      # no clips: the gate holds
        assert await repo.mark_training(syn, 15) is True       # no gate
        # A training read the phrase: it can't change under it.
        assert await repo.update(syn, phrase="hey al ex") is False
        assert await repo.update(rec, phrase="hey al ex") is True
        assert (await repo.get(rec))["phrase"] == "hey al ex"
        assert (await repo.get(rec))["slug"] == "hey_alex"       # the rooms' key stays
        assert await repo.mark_ready(syn, "hey_alex_2", trained_clips=0) is True
        got = await repo.get(syn)
        assert got["trained_clips"] == 0 and got["synthetic_only"] is True


# ── the web routes ────────────────────────────────────────────────────────

@pytest.fixture()
def web(monkeypatch):
    import web.backend.api.wake_words as wake_api
    from domovoi.db.session import engine
    from web.backend.main import app

    async def _truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE wake_words RESTART IDENTITY CASCADE"))

    asyncio.run(_truncate())

    async def _unreachable(path, body=None, timeout=30.0, headers=None):
        return 0, None

    monkeypatch.setattr(wake_api, "post_admin", _unreachable)
    with TestClient(app, headers={"X-Requested-With": "domovoi-tests"}) as client:
        yield client


@requires_db
def test_web_create_and_train_a_synthetic_word_without_clips(web):
    r = web.post("/api/wake-words", json={"name": "Hey Alex", "phrase": "hey alex",
                                          "synthetic_only": True})
    assert r.status_code == 201, r.text
    assert r.json()["synthetic_only"] is True
    t = web.post(f"/api/wake-words/{r.json()['id']}/train")
    assert t.status_code == 200, t.text
    assert t.json()["status"] == "training"
    # A recorded word with no clips is still refused.
    r2 = web.post("/api/wake-words", json={"name": "Hey Sam", "phrase": "hey sam"})
    assert web.post(f"/api/wake-words/{r2.json()['id']}/train").status_code == 409


@requires_db
def test_web_synthetic_copy_is_its_own_word_and_only_one(web):
    src = web.post("/api/wake-words", json={"name": "Hey Alex", "phrase": "hey al ex"}).json()
    r = web.post(f"/api/wake-words/{src['id']}/synthetic-copy")
    assert r.status_code == 201, r.text
    copy = r.json()
    assert copy["slug"] == "hey_alex_synthetic" and copy["name"] == "Hey Alex (synthetic voices)"
    assert copy["phrase"] == "hey al ex"            # the respelling travels
    assert copy["synthetic_only"] is True and copy["status"] == "training"
    again = web.post(f"/api/wake-words/{src['id']}/synthetic-copy")
    assert again.status_code == 409 and "already exists" in again.json()["detail"]
    # The copy has none of its own.
    assert web.post(f"/api/wake-words/{copy['id']}/synthetic-copy").status_code == 409
    # The recorded word is untouched.
    words = {w["id"]: w for w in web.get("/api/wake-words").json()}
    assert words[src["id"]]["status"] == "recording" and not words[src["id"]]["synthetic_only"]


def test_synthetic_copy_slug_stays_within_80():
    from web.backend.api.wake_words import synthetic_copy_slug

    assert synthetic_copy_slug("hey_alex") == "hey_alex_synthetic"
    long = "a" * 80
    assert len(synthetic_copy_slug(long)) == 80 and synthetic_copy_slug(long).endswith("_synthetic")


@requires_db
def test_web_lists_what_each_model_was_made_from(web, monkeypatch, tmp_path):
    from domovoi.config import settings as core_settings

    monkeypatch.setattr(core_settings, "wake_clips_dir", str(tmp_path / "clips"))
    monkeypatch.setattr(core_settings, "wake_models_dir", str(tmp_path / "models"))

    async def _seed_rows() -> None:
        from domovoi.db.repositories import WakeWordsRepository
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            repo = WakeWordsRepository(s)
            a = await repo.create(name="Synth", slug="synth", phrase="hey alex", synthetic_only=True)
            await repo.mark_ready(a, "synth", trained_clips=0)
            b = await repo.create(name="Rec", slug="rec", phrase="hey alex")
            await repo.mark_ready(b, "rec", trained_clips=23)
            c = await repo.create(name="Old", slug="old", phrase="hey alex")
            await repo.mark_ready(c, "old")                  # before V047, no marker
            d = await repo.create(name="Marked", slug="marked", phrase="hey alex")
            await repo.mark_ready(d, "marked")               # before V047, with a marker

    asyncio.run(_seed_rows())
    (tmp_path / "clips" / "marked").mkdir(parents=True)
    (tmp_path / "clips" / "marked" / ".trained.json").write_text(
        json.dumps({"last_clip": 30, "clips": 30}), "utf-8")
    got = {w["slug"]: w for w in web.get("/api/wake-words").json()}
    assert (got["synth"]["trained_clips"], got["synth"]["trained_clips_from"]) == (0, "model")
    assert (got["rec"]["trained_clips"], got["rec"]["trained_clips_from"]) == (23, "model")
    assert (got["old"]["trained_clips"], got["old"]["trained_clips_from"]) == (None, None)
    assert (got["marked"]["trained_clips"], got["marked"]["trained_clips_from"]) == (30, "marker")


@requires_db
def test_web_respelling_changes_the_phrase_not_the_slug_and_waits_for_training(web):
    w = web.post("/api/wake-words", json={"name": "Hey Alex", "phrase": "hey alex",
                                          "synthetic_only": True}).json()
    r = web.patch(f"/api/wake-words/{w['id']}", json={"phrase": "hey al ex"})
    assert r.status_code == 200, r.text
    assert r.json()["phrase"] == "hey al ex" and r.json()["slug"] == "hey_alex"
    assert web.patch(f"/api/wake-words/{w['id']}", json={"phrase": "rm -rf; $(x)"}).status_code == 422
    web.post(f"/api/wake-words/{w['id']}/train")
    busy = web.patch(f"/api/wake-words/{w['id']}", json={"phrase": "hey alexander"})
    assert busy.status_code == 409 and "training" in busy.json()["detail"]
