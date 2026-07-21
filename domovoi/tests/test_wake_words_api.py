"""Tests for the web backend's /api/wake-words/* router (Feature 5).

Lives under ``domovoi/tests`` because that's where the test-DB
fixtures + conftest safety net are wired; ``web/backend/`` has no test
directory of its own. Drives the live web app through FastAPI's
TestClient so the full request → repo → NOTIFY pipeline is exercised.

The record/stop/push routes proxy the core over HTTP; with no
domovoi running in the suite, ``post_admin`` returns a connection
failure → ``bridge_response`` maps it to 502 — which is exactly what we
assert (the proxy wiring is correct even with the backend down).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from web.backend.main import app


# wake_words isn't in the conftest truncate list (it's reference-ish data,
# like voices), and TestClient doesn't request the db_session fixture
# anyway. Clear it before every test so prior rows can't leak into
# "empty DB" / count assertions.
@pytest.fixture(autouse=True)
def _clean_wake_words():
    from domovoi.db.session import engine

    async def _truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE wake_words RESTART IDENTITY CASCADE"))

    asyncio.run(_truncate())
    yield


def _create(client: TestClient, name: str = "Hey Domovoi", phrase: str = "hey domovoi", **kw):
    body = {"name": name, "phrase": phrase, **kw}
    return client.post("/api/wake-words", json=body)


def _plant_clip(path, *, good: bool = True, seed: int = 0) -> None:
    """Write a real 16 kHz mono int16 WAV: a tone 'phrase' in low noise (good),
    or pure low noise (poor / no-speech)."""
    import numpy as np

    from domovoi import wake_clip_quality as wq

    sr = wq.SAMPLE_RATE
    n = int(2.0 * sr)
    rng = np.random.default_rng(seed)
    sig = rng.normal(0.0, 30.0, n)
    if good:
        start, tlen = int(0.7 * sr), int(0.6 * sr)
        t = np.arange(tlen)
        sig[start : start + tlen] += 9830.0 * np.sin(2 * np.pi * 220.0 * t / sr)
    wq._write_wav_int16(path, sig)


@requires_db
def test_list_empty_db_returns_empty_list() -> None:
    with TestClient(app) as client:
        r = client.get("/api/wake-words")
        assert r.status_code == 200
        assert r.json() == []


@requires_db
def test_create_persists_and_returns_full_row() -> None:
    with TestClient(app) as client:
        r = _create(client, name="Hey Domovoi", phrase="hey domovoi", threshold=0.6)
        assert r.status_code == 201, r.text
        payload = r.json()
        assert payload["id"] > 0
        assert payload["name"] == "Hey Domovoi"
        # slug = voice_slug(name): lowercased, non-alnum collapsed to "_".
        assert payload["slug"] == "hey_domovoi"
        assert payload["phrase"] == "hey domovoi"
        assert payload["threshold"] == pytest.approx(0.6)
        assert payload["status"] == "recording"
        assert payload["is_default"] is False
        assert payload["clip_count"] == 0
        assert payload["model_ref"] is None

        # Shows up in the list.
        listing = client.get("/api/wake-words").json()
        assert [w["id"] for w in listing] == [payload["id"]]


@requires_db
def test_create_defaults_threshold_to_half() -> None:
    with TestClient(app) as client:
        r = _create(client, name="Computer", phrase="computer")
        assert r.status_code == 201
        assert r.json()["threshold"] == pytest.approx(0.5)


@requires_db
def test_create_duplicate_name_slug_returns_409() -> None:
    with TestClient(app) as client:
        assert _create(client, name="Hey Domovoi", phrase="hey domovoi").status_code == 201
        # Same name → same slug → collision.
        dup = _create(client, name="Hey Domovoi", phrase="different phrase")
        assert dup.status_code == 409
        # A different name that slugifies to the same slug also collides.
        dup2 = _create(client, name="HEY domovoi", phrase="x")
        assert dup2.status_code == 409


@requires_db
def test_patch_threshold_and_rename() -> None:
    with TestClient(app) as client:
        wid = _create(client, name="Athena", phrase="hey athena").json()["id"]

        r = client.patch(f"/api/wake-words/{wid}", json={"threshold": 0.8})
        assert r.status_code == 200
        assert r.json()["threshold"] == pytest.approx(0.8)

        r = client.patch(f"/api/wake-words/{wid}", json={"name": "Athena 2"})
        assert r.status_code == 200
        assert r.json()["name"] == "Athena 2"


@requires_db
def test_patch_empty_body_returns_400() -> None:
    with TestClient(app) as client:
        wid = _create(client, name="Bishop", phrase="hey bishop").json()["id"]
        r = client.patch(f"/api/wake-words/{wid}", json={})
        assert r.status_code == 400


@requires_db
def test_patch_set_default_on_unready_returns_409() -> None:
    """A freshly-created wake word is in 'recording' with no model; it
    can't be made default until it's trained ('ready')."""
    with TestClient(app) as client:
        wid = _create(client, name="Cortana", phrase="hey cortana").json()["id"]
        r = client.patch(f"/api/wake-words/{wid}", json={"set_default": True})
        assert r.status_code == 409


@requires_db
def test_train_with_too_few_clips_returns_409() -> None:
    """The Train button enqueues training, which the repo refuses below
    ``wake_word_min_clips`` — surfaced as a 409. A fresh row has 0 clips."""
    with TestClient(app) as client:
        wid = _create(client, name="Vesper", phrase="hey vesper").json()["id"]
        r = client.post(f"/api/wake-words/{wid}/train")
        assert r.status_code == 409


def _bump_clip_count(wid: int, n: int) -> None:
    from domovoi.db.repositories import WakeWordsRepository
    from domovoi.db.session import session_scope

    async def _go() -> None:
        async with session_scope() as s:
            repo = WakeWordsRepository(s)
            for _ in range(n):
                await repo.bump_clip_count(wid)

    asyncio.run(_go())


@requires_db
def test_train_after_enough_clips_promotes_to_training(tmp_path, monkeypatch) -> None:
    """Once enough clips are recorded AND selected, train enqueues the row
    (status → 'training'). Plants real clips on disk (the trainer + the
    selected-gate read the filesystem) and syncs the DB counter."""
    from domovoi.config import settings
    from domovoi.config import settings as core_settings

    clips_root = tmp_path / "wake_clips"
    monkeypatch.setattr(core_settings, "wake_clips_dir", str(clips_root))

    with TestClient(app) as client:
        created = _create(client, name="Sentinel", phrase="hey sentinel").json()
        wid, slug = created["id"], created["slug"]
        n = settings.wake_word_min_clips
        sub = clips_root / slug
        sub.mkdir(parents=True)
        for i in range(1, n + 1):
            _plant_clip(sub / f"clip_{i:03d}.wav", good=True, seed=i)
        _bump_clip_count(wid, n)

        r = client.post(f"/api/wake-words/{wid}/train")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "training"


@requires_db
def test_train_gated_on_selected_count(tmp_path, monkeypatch) -> None:
    """Enough clips exist, but if they're all DESELECTED the word can't train
    (the trainer consumes only the selected set). Re-selecting unblocks it."""
    from domovoi.config import settings
    from domovoi.config import settings as core_settings

    clips_root = tmp_path / "wake_clips"
    monkeypatch.setattr(core_settings, "wake_clips_dir", str(clips_root))

    with TestClient(app) as client:
        created = _create(client, name="Gatekeeper", phrase="hey gatekeeper").json()
        wid, slug = created["id"], created["slug"]
        n = settings.wake_word_min_clips
        sub = clips_root / slug
        sub.mkdir(parents=True)
        for i in range(1, n + 1):
            _plant_clip(sub / f"clip_{i:03d}.wav", good=True, seed=i)
        _bump_clip_count(wid, n)

        # Deselect everything → 409 despite enough clips existing.
        client.post(f"/api/wake-words/{wid}/clips/selection", json={"selected": False})
        r = client.post(f"/api/wake-words/{wid}/train")
        assert r.status_code == 409

        # Re-select all → training enqueues.
        client.post(f"/api/wake-words/{wid}/clips/selection", json={"selected": True})
        r = client.post(f"/api/wake-words/{wid}/train")
        assert r.status_code == 200, r.text


@requires_db
def test_delete_recording_row_succeeds() -> None:
    with TestClient(app) as client:
        wid = _create(client, name="Disposable", phrase="hey disposable").json()["id"]
        r = client.delete(f"/api/wake-words/{wid}")
        assert r.status_code == 204
        # Gone from the list.
        listing = client.get("/api/wake-words").json()
        assert all(w["id"] != wid for w in listing)


@requires_db
def test_delete_unknown_returns_409() -> None:
    """delete returns None for a missing row → the router maps that to 409
    (same code as the default-protected case)."""
    with TestClient(app) as client:
        r = client.delete("/api/wake-words/999999")
        assert r.status_code == 409


@requires_db
def test_delete_default_returns_409() -> None:
    """The default wake word can't be deleted; the repo refuses → 409."""
    from domovoi.db.repositories import WakeWordsRepository
    from domovoi.db.session import session_scope

    with TestClient(app) as client:
        wid = _create(client, name="DefaultWord", phrase="hey default").json()["id"]

        async def _make_ready_default() -> None:
            async with session_scope() as s:
                repo = WakeWordsRepository(s)
                for _ in range(3):
                    await repo.bump_clip_count(wid)
                await repo.mark_training(wid, 1)
                await repo.mark_ready(wid, "default_word")
                await repo.set_default(wid)

        asyncio.run(_make_ready_default())

        r = client.delete(f"/api/wake-words/{wid}")
        assert r.status_code == 409


@requires_db
def test_clips_endpoint_returns_quality_objects(tmp_path, monkeypatch) -> None:
    """GET /{id}/clips returns per-clip quality/verdict/trim/envelope/selection,
    lazily analyzing any clip that lacks a sidecar."""
    from domovoi.config import settings as core_settings

    clips_root = tmp_path / "wake_clips"
    monkeypatch.setattr(core_settings, "wake_clips_dir", str(clips_root))

    with TestClient(app) as client:
        created = _create(client, name="Clipper", phrase="hey clipper").json()
        wid, slug = created["id"], created["slug"]

        # No dir yet → empty, but the enriched envelope is present.
        r = client.get(f"/api/wake-words/{wid}/clips")
        assert r.status_code == 200
        body = r.json()
        assert body["slug"] == slug and body["count"] == 0
        assert body["clips"] == [] and body["selected_count"] == 0
        assert body["min_clips"] >= 1

        # Plant a good clip + a poor (no-speech) clip.
        sub = clips_root / slug
        sub.mkdir(parents=True)
        _plant_clip(sub / "clip_001.wav", good=True)
        _plant_clip(sub / "clip_002.wav", good=False)

        body = client.get(f"/api/wake-words/{wid}/clips").json()
        assert body["count"] == 2
        assert [c["name"] for c in body["clips"]] == ["clip_001.wav", "clip_002.wav"]
        good, poor = body["clips"]
        assert good["verdict"] == "good" and good["selected"] is True
        assert good["has_trimmed"] is True and len(good["envelope"]) == 48
        assert "snr_db" in good["metrics"]
        assert poor["verdict"] == "poor" and poor["selected"] is False  # auto-deselected
        assert body["selected_count"] == 1


@requires_db
def test_clip_audio_raw_and_trimmed(tmp_path, monkeypatch) -> None:
    from domovoi.config import settings as core_settings

    clips_root = tmp_path / "wake_clips"
    monkeypatch.setattr(core_settings, "wake_clips_dir", str(clips_root))

    with TestClient(app) as client:
        created = _create(client, name="Audio", phrase="hey audio").json()
        wid, slug = created["id"], created["slug"]
        sub = clips_root / slug
        sub.mkdir(parents=True)
        _plant_clip(sub / "clip_001.wav", good=True)

        raw = client.get(f"/api/wake-words/{wid}/clips/clip_001.wav/audio")
        assert raw.status_code == 200
        assert raw.headers["content-type"] == "audio/wav"
        assert raw.content[:4] == b"RIFF"

        trimmed = client.get(f"/api/wake-words/{wid}/clips/clip_001.wav/audio?variant=trimmed")
        assert trimmed.status_code == 200
        assert trimmed.content[:4] == b"RIFF"
        assert len(trimmed.content) < len(raw.content)  # trimmed is shorter

        assert client.get(f"/api/wake-words/{wid}/clips/nope.wav/audio").status_code == 404
        # Unknown variant → 422 (Query pattern validation).
        assert client.get(f"/api/wake-words/{wid}/clips/clip_001.wav/audio?variant=x").status_code == 422
        # Path traversal → 400.
        assert client.get(f"/api/wake-words/{wid}/clips/%2e%2e/audio").status_code == 400


@requires_db
def test_clip_selection_individual_and_bulk(tmp_path, monkeypatch) -> None:
    from domovoi.config import settings as core_settings

    clips_root = tmp_path / "wake_clips"
    monkeypatch.setattr(core_settings, "wake_clips_dir", str(clips_root))

    with TestClient(app) as client:
        created = _create(client, name="Selector", phrase="hey selector").json()
        wid, slug = created["id"], created["slug"]
        sub = clips_root / slug
        sub.mkdir(parents=True)
        _plant_clip(sub / "clip_001.wav", good=True)
        _plant_clip(sub / "clip_002.wav", good=False)

        # Auto: good selected, poor not.
        assert client.get(f"/api/wake-words/{wid}/clips").json()["selected_count"] == 1

        # Deselect the good one individually.
        r = client.patch(f"/api/wake-words/{wid}/clips/clip_001.wav", json={"selected": False})
        assert r.status_code == 200 and r.json()["selected"] is False
        assert client.get(f"/api/wake-words/{wid}/clips").json()["selected_count"] == 0

        # Bulk select all.
        r = client.post(f"/api/wake-words/{wid}/clips/selection", json={"selected": True})
        assert r.json()["selected_count"] == 2

        # Bulk deselect only the poor ones.
        r = client.post(
            f"/api/wake-words/{wid}/clips/selection",
            json={"selected": False, "only_verdict": "poor"},
        )
        assert r.json()["selected_count"] == 1
        body = client.get(f"/api/wake-words/{wid}/clips").json()
        by_name = {c["name"]: c for c in body["clips"]}
        assert by_name["clip_001.wav"]["selected"] is True
        assert by_name["clip_002.wav"]["selected"] is False


@requires_db
def test_reanalyze_preserves_user_selection(tmp_path, monkeypatch) -> None:
    from domovoi.config import settings as core_settings

    clips_root = tmp_path / "wake_clips"
    monkeypatch.setattr(core_settings, "wake_clips_dir", str(clips_root))

    with TestClient(app) as client:
        created = _create(client, name="Reanalyze", phrase="hey reanalyze").json()
        wid, slug = created["id"], created["slug"]
        sub = clips_root / slug
        sub.mkdir(parents=True)
        _plant_clip(sub / "clip_001.wav", good=True)

        client.patch(f"/api/wake-words/{wid}/clips/clip_001.wav", json={"selected": False})
        r = client.post(f"/api/wake-words/{wid}/clips/reanalyze")
        assert r.status_code == 200 and r.json()["count"] == 1
        # The user's deselect survives a forced recompute.
        assert client.get(f"/api/wake-words/{wid}/clips").json()["selected_count"] == 0


@requires_db
def test_delete_clip_removes_analysis_artifacts(tmp_path, monkeypatch) -> None:
    from domovoi import wake_clip_quality as wq
    from domovoi.config import settings as core_settings

    clips_root = tmp_path / "wake_clips"
    monkeypatch.setattr(core_settings, "wake_clips_dir", str(clips_root))

    with TestClient(app) as client:
        created = _create(client, name="Artifacts", phrase="hey artifacts").json()
        wid, slug = created["id"], created["slug"]
        sub = clips_root / slug
        sub.mkdir(parents=True)
        raw = sub / "clip_001.wav"
        _plant_clip(raw, good=True)

        # Listing generates the sidecar + trimmed audit copy.
        client.get(f"/api/wake-words/{wid}/clips")
        assert wq._sidecar_path(raw).is_file()
        assert wq.trimmed_path(raw).is_file()

        assert client.delete(f"/api/wake-words/{wid}/clips/clip_001.wav").status_code == 204
        assert not raw.exists()
        assert not wq._sidecar_path(raw).is_file()
        assert not wq.trimmed_path(raw).is_file()


@requires_db
def test_delete_one_clip(tmp_path, monkeypatch) -> None:
    from domovoi.config import settings as core_settings

    clips_root = tmp_path / "wake_clips"
    monkeypatch.setattr(core_settings, "wake_clips_dir", str(clips_root))

    with TestClient(app) as client:
        wid = _create(client, name="Snipper", phrase="hey snipper").json()["id"]
        sub = clips_root / "snipper"
        sub.mkdir(parents=True)
        (sub / "clip_001.wav").write_bytes(b"a")

        r = client.delete(f"/api/wake-words/{wid}/clips/clip_001.wav")
        assert r.status_code == 204
        assert not (sub / "clip_001.wav").exists()

        # Deleting a missing clip → 404.
        r = client.delete(f"/api/wake-words/{wid}/clips/clip_999.wav")
        assert r.status_code == 404


@requires_db
def test_delete_clip_path_traversal_rejected(tmp_path, monkeypatch) -> None:
    """A crafted clip name that escapes the clip dir → 400, not an
    arbitrary-file unlink."""
    from domovoi.config import settings as core_settings

    clips_root = tmp_path / "wake_clips"
    monkeypatch.setattr(core_settings, "wake_clips_dir", str(clips_root))

    with TestClient(app) as client:
        wid = _create(client, name="Guarded", phrase="hey guarded").json()["id"]
        # A bare ".." segment gets collapsed by the HTTP client before it
        # reaches the route, so percent-encode it to keep the escaping
        # name literal and actually exercise the router's path guard.
        r = client.delete(f"/api/wake-words/{wid}/clips/%2e%2e")
        # 400 (traversal rejected, since ".." resolves to the parent which
        # isn't relative-to the clip dir) is the guarded outcome — what
        # must NOT happen is a 204 success on an escaping path.
        assert r.status_code == 400


# ─── Proxy routes (domovoi unreachable → 502) ────────────────────────
# These stub ``post_admin`` to the connection-failure sentinel ``(0, None)``
# rather than relying on no domovoi being up: Domovoi runs the
# core natively on :6370, so a live one would answer with a real
# status (e.g. 404 for a prod-DB row miss) and the env-dependent assertion
# would flake. ``bridge_response(0, ...)`` is what maps "unreachable" → 502.


def _stub_admin_down(monkeypatch) -> None:
    import web.backend.api.wake_words as wake_api

    async def _down(path, body=None, timeout=30.0):
        return 0, None

    monkeypatch.setattr(wake_api, "post_admin", _down)


@requires_db
def test_record_start_proxies_502_when_domovoi_down(monkeypatch) -> None:
    _stub_admin_down(monkeypatch)
    with TestClient(app) as client:
        wid = _create(client, name="Proxy A", phrase="hey proxy a").json()["id"]
        r = client.post(f"/api/wake-words/{wid}/record/start", json={"room_id": "kitchen"})
        assert r.status_code == 502


@requires_db
def test_record_stop_proxies_502_when_domovoi_down(monkeypatch) -> None:
    _stub_admin_down(monkeypatch)
    with TestClient(app) as client:
        wid = _create(client, name="Proxy B", phrase="hey proxy b").json()["id"]
        r = client.post(f"/api/wake-words/{wid}/record/stop", json={"room_id": "kitchen"})
        assert r.status_code == 502


@requires_db
def test_push_proxies_502_when_domovoi_down(monkeypatch) -> None:
    _stub_admin_down(monkeypatch)
    with TestClient(app) as client:
        wid = _create(client, name="Proxy C", phrase="hey proxy c").json()["id"]
        r = client.post(f"/api/wake-words/{wid}/push", json={"room_id": "kitchen"})
        assert r.status_code == 502


@requires_db
def test_record_start_forwards_room_and_id_to_admin(monkeypatch) -> None:
    """The proxy must forward the wake_word_id (path) + room_id (body) to
    the core admin endpoint, and pass its status through."""
    import web.backend.api.wake_words as wake_api

    captured: dict = {}

    async def _fake_post_admin(path, body=None, timeout=30.0):
        captured["path"] = path
        captured["body"] = body
        return 200, {"announced_to": ["kitchen"]}

    monkeypatch.setattr(wake_api, "post_admin", _fake_post_admin)

    with TestClient(app) as client:
        wid = _create(client, name="Forwarder", phrase="hey forwarder").json()["id"]
        r = client.post(f"/api/wake-words/{wid}/record/start", json={"room_id": "garage"})
        assert r.status_code == 200

    assert captured["path"] == "/v1/admin/wake/record/start"
    assert captured["body"] == {"room_id": "garage", "wake_word_id": wid}


@requires_db
def test_score_proxies_502_when_domovoi_down(monkeypatch) -> None:
    _stub_admin_down(monkeypatch)
    with TestClient(app) as client:
        wid = _create(client, name="Scorer A", phrase="hey scorer a").json()["id"]
        r = client.post(f"/api/wake-words/{wid}/score")
        assert r.status_code == 502


@requires_db
def test_score_forwards_wake_word_id_to_admin(monkeypatch) -> None:
    import web.backend.api.wake_words as wake_api

    captured: dict = {}

    async def _fake_post_admin(path, body=None, timeout=30.0):
        captured["path"] = path
        captured["body"] = body
        return 200, {"available": True, "summary": {"raw_recall": 0.9}}

    monkeypatch.setattr(wake_api, "post_admin", _fake_post_admin)

    with TestClient(app) as client:
        wid = _create(client, name="Scorer B", phrase="hey scorer b").json()["id"]
        r = client.post(f"/api/wake-words/{wid}/score")
        assert r.status_code == 200

    assert captured["path"] == "/v1/admin/wake/score"
    assert captured["body"] == {"wake_word_id": wid}
