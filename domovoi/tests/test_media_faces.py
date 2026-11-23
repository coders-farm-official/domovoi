"""Media understanding, slice 4: faces (domovoi/media_understanding/faces.py,
face_engine.py, face_models.py; web/backend/api/faces.py).

The models are never fetched here: the detector and recogniser are fakes
(raw YuNet-shaped outputs for the decoder, fixed embeddings for the
grouping), and the pinned fetch runs against a fake fetcher.
"""

from __future__ import annotations

import hashlib
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import text

from domovoi import egress
from domovoi.db.session import session_scope
from domovoi.media_understanding import face_engine as E
from domovoi.media_understanding import face_models as M
from domovoi.media_understanding import faces as F
from domovoi.media_understanding import runner as R
from domovoi.media_understanding import switches
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_media_understanding import (  # noqa: F401 — fixtures
    _count,
    _file,
    _items,
    _quiet_house,
    _runner,
    _sync,
    house,
)

REPO = Path(__file__).resolve().parents[2]
NIGHT = datetime(2026, 11, 22, 23, 0)


# ─── vectors for fake faces ──────────────────────────────────────────────

_rng = np.random.default_rng(7)


def _base() -> np.ndarray:
    return E.unit(_rng.normal(size=128))


def _near(base: np.ndarray, spread: float = 0.4) -> np.ndarray:
    return E.unit(base + _rng.normal(size=128) * spread / np.sqrt(128))


def _found(emb: np.ndarray, q: float = 0.9) -> E.Found:
    return E.Found([0.1, 0.1, 0.2, 0.2], 0.95, q, emb)


# ─── YuNet decoding, alignment, the engine (pure) ────────────────────────


def _yunet_outputs(w: int, h: int, faces: list[tuple[int, int, int, float, float]]) -> dict[str, np.ndarray]:
    """Raw YuNet outputs for a w x h input with faces at (stride, row,
    col, box w px, box h px), centred on their cell."""
    out = {}
    for s in E.STRIDES:
        n = (w // s) * (h // s)
        out[f"cls_{s}"] = np.zeros((1, n, 1), np.float32)
        out[f"obj_{s}"] = np.zeros((1, n, 1), np.float32)
        out[f"bbox_{s}"] = np.zeros((1, n, 4), np.float32)
        out[f"kps_{s}"] = np.zeros((1, n, 10), np.float32)
    for s, r, c, bw, bh in faces:
        i = r * (w // s) + c
        out[f"cls_{s}"][0, i, 0] = 0.98
        out[f"obj_{s}"][0, i, 0] = 0.95
        out[f"bbox_{s}"][0, i] = [0.0, 0.0, np.log(bw / s), np.log(bh / s)]
        # Landmarks: the alignment template, scaled to the box, as offsets in cells.
        tpl = (E.TEMPLATE - 56.0) / 112.0
        pts = np.stack([tpl[:, 0] * bw / s, tpl[:, 1] * bh / s], axis=1)
        out[f"kps_{s}"][0, i] = pts.reshape(-1)
    return out


def test_yunet_outputs_decode_to_boxes_landmarks_and_scores() -> None:
    out = _yunet_outputs(640, 480, [(16, 10, 20, 96.0, 112.0), (32, 3, 4, 64.0, 64.0)])
    found = E.decode_yunet(out, 640, 480)
    assert len(found) == 2
    by_w = {round(float(d.box[2])): d for d in found}
    big = by_w[96]
    assert np.allclose(big.box, [20 * 16 - 48, 10 * 16 - 56, 96, 112], atol=1e-3)
    assert big.score == pytest.approx(np.sqrt(0.98 * 0.95), rel=1e-5)
    assert big.landmarks.shape == (5, 2)
    # Below the score floor: nothing.
    weak = _yunet_outputs(640, 480, [(8, 1, 1, 50.0, 50.0)])
    weak["obj_8"][:] *= 0.5
    assert E.decode_yunet(weak, 640, 480) == []


def test_overlapping_detections_are_suppressed() -> None:
    boxes = np.array([[0, 0, 100, 100], [5, 5, 100, 100], [300, 300, 50, 50]], np.float32)
    assert E.nms(boxes, np.array([0.9, 0.95, 0.8])) == [1, 2]


def test_alignment_recovers_a_rotation_and_scale() -> None:
    theta, k, t = 0.3, 2.5, np.array([40.0, -12.0])
    R2 = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    src = (E.TEMPLATE - t) @ np.linalg.inv(k * R2).T
    M2 = E.similarity_transform(src, E.TEMPLATE)
    assert np.allclose(src @ M2[:, :2].T + M2[:, 2], E.TEMPLATE, atol=1e-6)


class _Node:
    def __init__(self, name: str, shape: list[Any]) -> None:
        self.name, self.shape = name, shape


class _FakeDetector:
    def __init__(self, faces, shape=(1, 3, "h", "w")) -> None:
        self.faces, self.shape, self.seen = faces, list(shape), []

    def get_inputs(self):
        return [_Node("input", self.shape)]

    def get_outputs(self):
        return [_Node(f"{k}_{s}", []) for k in ("cls", "obj", "bbox", "kps") for s in E.STRIDES]

    def run(self, _names, feed):
        blob = feed["input"]
        self.seen.append(blob.shape)
        _n, _c, h, w = blob.shape
        out = _yunet_outputs(w, h, self.faces)
        return [out[o.name] for o in self.get_outputs()]


class _FakeRecognizer:
    def __init__(self, vec: np.ndarray) -> None:
        self.vec, self.seen = vec, []

    def get_inputs(self):
        return [_Node("data", [1, 3, 112, 112])]

    def run(self, _names, feed):
        self.seen.append(feed["data"].shape)
        return [self.vec[None] * 3.0]


def test_the_engine_finds_aligns_and_embeds_with_fake_sessions() -> None:
    from PIL import Image

    vec = _base()
    det = _FakeDetector([(16, 10, 20, 96.0, 112.0), (8, 2, 2, 16.0, 16.0)])   # one too small
    rec = _FakeRecognizer(vec)
    eng = E.FaceEngine(det, rec)
    img = Image.new("RGB", (1280, 960), (120, 90, 60))
    found = eng.faces(img)
    assert len(found) == 1
    f = found[0]
    # The picture was shown to the detector at 640 x 480 (a multiple of 32).
    assert det.seen == [(1, 3, 480, 640)]
    assert rec.seen == [(1, 3, 112, 112)]
    assert np.allclose(f.embedding, vec, atol=1e-6) and abs(np.linalg.norm(f.embedding) - 1) < 1e-5
    assert f.box == pytest.approx([(320 - 48) / 640, (160 - 56) / 480, 96 / 640, 112 / 480], abs=1e-4)
    assert 0 < f.quality <= f.score
    # A fixed-size detector input is honoured too.
    det2 = _FakeDetector([(16, 10, 20, 96.0, 112.0)], shape=(1, 3, 640, 640))
    assert len(E.FaceEngine(det2, rec).faces(img)) == 1
    assert det2.seen == [(1, 3, 640, 640)]


def test_pictures_are_decoded_upright_and_bounded() -> None:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4000, 1000), (1, 2, 3)).save(buf, "JPEG")
    im = E.load_picture(buf.getvalue())
    assert max(im.size) <= E.DECODE_SIZE and im.mode == "RGB"


def test_connected_components_over_cosine() -> None:
    a, b = _base(), _base()
    E_ = np.stack([_near(a), _near(b), _near(a), _near(b), _near(a)])
    labels = F.components(E_, chunk=2)
    assert labels[0] == labels[2] == labels[4] != labels[1] == labels[3]


# ─── the pinned model fetch ──────────────────────────────────────────────


def _tiny_pins(monkeypatch) -> dict[str, bytes]:
    blobs = {"det.onnx": b"detector-bytes", "rec.onnx": b"recognizer-bytes!"}
    pins = tuple(
        M.Pin(role, name, f"https://example.org/{name}", hashlib.sha256(data).hexdigest(), len(data), lic)
        for (name, data), role, lic in zip(blobs.items(), ("detector", "recognizer"), ("MIT", "Apache-2.0")))
    monkeypatch.setattr(M, "PINS", pins)
    M._reset_for_tests()
    return blobs


def test_the_real_pins_are_the_opencv_zoo_files_by_sha256() -> None:
    assert M.DETECTOR.file == "face_detection_yunet_2023mar.onnx" and M.DETECTOR.licence == "MIT"
    assert M.RECOGNIZER.file == "face_recognition_sface_2021dec.onnx" and M.RECOGNIZER.licence == "Apache-2.0"
    for p in M.PINS:
        assert len(p.sha256) == 64 and p.size > 0
        assert p.url.startswith("https://") and M.ZOO_COMMIT in p.url and p.url.endswith(p.file)
        assert "insightface" not in p.url.lower()
    # Never in the repo.
    import subprocess

    tracked = subprocess.run(["git", "ls-files", "*.onnx"], cwd=REPO, capture_output=True, text=True).stdout
    assert "yunet" not in tracked.lower() and "sface" not in tracked.lower()
    # Recorded with their licences.
    lic = (REPO / "docs" / "LICENSES.md").read_text(encoding="utf-8")
    for p in M.PINS:
        assert p.sha256 in lic and p.file in lic


def test_the_fetch_checks_size_and_sha256(tmp_path, monkeypatch) -> None:
    blobs = _tiny_pins(monkeypatch)
    calls = []

    def good(url, dest, *, sha256, size):
        calls.append(url)
        dest.write_bytes(blobs[dest.name])

    with egress.override_policy("always"):
        M.download(tmp_path, fetch=good)
    assert M.ready(tmp_path) and len(calls) == 2
    M.download(tmp_path, fetch=good)                     # nothing missing: no fetch
    assert len(calls) == 2

    # A file that doesn't match its pin is refused and never left in place.
    bad_dir = tmp_path / "bad"

    def tampered(url, dest, *, sha256, size):
        dest.write_bytes(b"x" * size)

    with egress.override_policy("always"), pytest.raises(M.ModelsUnavailable):
        M.download(bad_dir, fetch=tampered)
    assert not M.ready(bad_dir)
    assert not any(bad_dir.glob("*.onnx"))
    assert "checksum" in (M.last_error() or "")


def test_no_fetch_when_internet_is_off(tmp_path, monkeypatch) -> None:
    _tiny_pins(monkeypatch)
    calls = []
    with egress.override_policy("never"), pytest.raises(egress.InternetTurnedOff):
        M.download(tmp_path, fetch=lambda *a, **k: calls.append(a))
    assert calls == []


class _HKRunner:
    def __init__(self, faces: bool, night: bool = False) -> None:
        self.sw = {**switches.DEFAULTS, "enabled": True, "faces": faces}
        self.night = night

    def in_night(self) -> bool:
        return self.night


async def test_models_are_fetched_only_when_faces_are_turned_on(monkeypatch) -> None:
    fetched = []
    M._reset_for_tests()

    async def fake_fetch() -> None:
        fetched.append(1)

    monkeypatch.setattr(M, "ready", lambda base=None: False)
    monkeypatch.setattr(F, "_fetch_models", fake_fetch)
    monkeypatch.setattr(F, "_fetch_task", None)
    await F.housekeeping(_HKRunner(faces=False))
    assert fetched == []
    await F.housekeeping(_HKRunner(faces=True))
    await F._fetch_task
    assert fetched == [1]
    # And the analyser waits for the models.
    assert not F.FaceAnalyser().wanted(None, {"faces": True})
    monkeypatch.setattr(M, "ready", lambda base=None: True)
    assert F.FaceAnalyser().wanted(None, {"faces": True})
    assert not F.FaceAnalyser().wanted(None, {"faces": False})


def test_the_web_never_fetches_and_the_models_have_their_own_folder() -> None:
    src = (REPO / "web" / "backend" / "api" / "faces.py").read_text(encoding="utf-8")
    assert "download(" not in src
    from domovoi.config import settings

    assert Path(settings.face_models_dir).name == "face_models"


# ─── the database ────────────────────────────────────────────────────────


class _FakeEngine:
    """Faces by picture size: each test picture has its own width."""

    def __init__(self) -> None:
        self.by_size: dict[tuple[int, int], list[E.Found]] = {}

    def faces(self, image) -> list[E.Found]:
        return list(self.by_size.get(image.size, []))


@pytest.fixture
async def fh(house, monkeypatch):
    """The ``house`` with faces on, models "on disk", a fake engine, and
    the analyser registered."""
    eng = _FakeEngine()
    F.set_engine(eng)
    monkeypatch.setattr(M, "ready", lambda base=None: True)
    async with session_scope() as s:
        await s.execute(text("DELETE FROM household_settings WHERE key = :k"), {"k": F.STATE_KEY})
        sw = await switches.read(s)
        await switches.write(s, {**sw, "faces": True})
    R.register(F.FaceAnalyser())
    made: list[int] = []
    yield {"lib": house, "engine": eng, "people": made}
    F.set_engine(None)
    R.unregister_housekeeping("faces")
    async with session_scope() as s:
        await s.execute(text("TRUNCATE face_groups, face_denylist RESTART IDENTITY CASCADE"))
        await s.execute(text("DELETE FROM devices WHERE device_id LIKE 'facetest-%'"))
        await s.execute(text("DELETE FROM people WHERE name LIKE 'FT %'"))
        await s.execute(text("DELETE FROM household_settings WHERE key = :k"), {"k": F.STATE_KEY})


def _png(lib, rel: str, width: int, mtime: float) -> None:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, 50), (width % 255, 3, 4)).save(buf, "PNG")
    _file(lib, rel, buf.getvalue(), mtime=mtime)


_width = [100]


async def _photos(fh, faces_by_rel: dict[str, list[np.ndarray]]) -> None:
    """Pictures with these faces, analysed in the order given (the runner
    takes the newest file first, so the first is made newest)."""
    for n, (rel, embs) in enumerate(faces_by_rel.items()):
        _width[0] += 1
        w = _width[0]
        _png(fh["lib"], rel, w, 1_900_000_000 - n)
        fh["engine"].by_size[(w, 50)] = [_found(e) for e in embs]
    await _sync(fh["lib"])
    r = _runner(now=lambda: NIGHT)
    while await r.step():
        pass


async def _person(name: str) -> int:
    async with session_scope() as s:
        return int((await s.execute(text("INSERT INTO people (name) VALUES (:n) RETURNING id"),
                                    {"n": name})).scalar())


async def _faces() -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT f.id, f.group_id, f.person_id, f.confirmed, f.rejected_person_ids, i.rel_path, f.at_s, f.end_s "
            "FROM media_faces f JOIN media_items i ON i.id = f.item_id ORDER BY f.id"))).mappings().all()
    return [dict(r) for r in rows]


@requires_db
async def test_the_analyser_stores_faces_and_groups_them_as_they_arrive(fh) -> None:
    a, b = _base(), _base()
    await _photos(fh, {"1.png": [_near(a), _near(b)], "2.png": [_near(a)], "3.png": [_near(a)],
                       "4.png": []})
    rows = await _faces()
    assert [r["rel_path"] for r in rows] == ["1.png", "1.png", "2.png", "3.png"]
    ga = {rows[i]["group_id"] for i in (0, 2, 3)}
    assert len(ga) == 1                                    # a's three faces: one group
    assert rows[1]["group_id"] not in ga                   # b: its own
    assert all(r["person_id"] is None and not r["confirmed"] for r in rows)
    assert await _count("face_groups") == 2
    items = await _items()
    assert all(i["state"] == "done" for i in items.values())
    async with session_scope() as s:
        st = (await s.execute(text(
            "SELECT count(*) FROM media_item_stages WHERE analyser = 'faces' AND state = 'done'"))).scalar()
        box = (await s.execute(text("SELECT box, quality, length(embedding) FROM media_faces LIMIT 1"))).first()
    assert st == 4
    assert list(box[0]) == pytest.approx([0.1, 0.1, 0.2, 0.2]) and box[1] == pytest.approx(0.9)
    assert box[2] == 512


@requires_db
async def test_strangers_are_unnamed_and_small_groups_are_behind_show_more(fh) -> None:
    a, stranger = _base(), _base()
    await _photos(fh, {f"{i}.png": [_near(a)] + ([_near(stranger)] if i == 0 else []) for i in range(4)})
    async with session_scope() as s:
        shown = await F.unnamed_groups(s)
        more = await F.unnamed_groups(s, more=True)
    assert [g["faces"] for g in shown] == [4]
    assert sorted(g["faces"] for g in more) == [1, 4]


@requires_db
async def test_naming_confirms_and_new_faces_are_suggested_not_confirmed(fh) -> None:
    a = _base()
    await _photos(fh, {"1.png": [_near(a)], "2.png": [_near(a)]})
    gid = (await _faces())[0]["group_id"]
    async with session_scope() as s:
        pid = await F.person_for_name(s, "FT Sarah")
        await F.name_group(s, gid, pid)
        assert await F.person_for_name(s, " ft  sarah ") == pid      # the same person, not a new one
    assert all(r["person_id"] == pid and r["confirmed"] for r in await _faces())
    await _photos(fh, {"3.png": [_near(a)]})
    new = [r for r in await _faces() if r["rel_path"] == "3.png"][0]
    assert new["person_id"] == pid and not new["confirmed"]            # suggested
    async with session_scope() as s:
        await F.confirm_face(s, new["id"])
        items = await F.items_for_person(s, pid)
        assert await F.person_ids_with_faces(s, "ft sarah") == [pid]
    assert {i["rel_path"] for i in items} == {"1.png", "2.png", "3.png"}
    assert all(i["confirmed"] for i in items)


@requires_db
async def test_not_this_person_is_never_suggested_for_them_again(fh) -> None:
    a = _base()
    await _photos(fh, {"1.png": [_near(a)], "2.png": [_near(a)]})
    pid = await _person("FT Ann")
    async with session_scope() as s:
        await F.name_group(s, (await _faces())[0]["group_id"], pid)
    await _photos(fh, {"3.png": [_near(a)]})
    fid = [r for r in await _faces() if r["rel_path"] == "3.png"][0]["id"]
    async with session_scope() as s:
        out = await F.reject_face(s, fid)
        assert out["rejected_person_id"] == pid
    f = [r for r in await _faces() if r["id"] == fid][0]
    assert f["person_id"] is None and pid in f["rejected_person_ids"]
    async with session_scope() as s:
        await F.regroup(s)                                           # the night pass respects it too
    f = [r for r in await _faces() if r["id"] == fid][0]
    assert f["person_id"] is None
    async with session_scope() as s:
        with pytest.raises(F.Refused):
            await F.reject_face(s, fid)                              # nobody's: nothing to reject


@requires_db
async def test_the_nightly_regroup_never_moves_confirmed_faces(fh) -> None:
    a, b = _base(), _base()
    await _photos(fh, {"1.png": [_near(a)], "2.png": [_near(a)], "3.png": [_near(b)], "4.png": [_near(b)]})
    rows = await _faces()
    pid = await _person("FT Bo")
    async with session_scope() as s:
        await F.name_group(s, rows[0]["group_id"], pid)
        # Scatter the free faces as a bad incremental pass might have left them.
        await s.execute(text("UPDATE media_faces SET group_id = NULL WHERE NOT confirmed"))
        # A confirmed face that doesn't look like the rest: the household said so.
        odd = (await s.execute(text(
            "INSERT INTO media_faces (item_id, box, embedding, model, group_id, person_id, confirmed) "
            "SELECT item_id, box, :e, model, group_id, person_id, true FROM media_faces WHERE id = :f "
            "RETURNING id"), {"e": F.to_bytes(_base()), "f": rows[0]["id"]})).scalar()
    before = {r["id"]: (r["group_id"], r["person_id"]) for r in await _faces() if r["confirmed"]}
    async with session_scope() as s:
        out = await F.regroup(s)
    after = await _faces()
    assert {r["id"]: (r["group_id"], r["person_id"]) for r in after if r["confirmed"]} == before
    assert odd in before
    bs = [r for r in after if r["rel_path"] in ("3.png", "4.png")]
    assert bs[0]["group_id"] == bs[1]["group_id"] is not None and bs[0]["person_id"] is None
    assert out["faces"] == 2


@requires_db
async def test_hide_merge_and_split(fh) -> None:
    a, b, c = _base(), _base(), _base()
    await _photos(fh, {"1.png": [_near(a)], "2.png": [_near(b)], "3.png": [_near(c)], "4.png": [_near(c)]})
    rows = await _faces()
    ga, gb, gc = rows[0]["group_id"], rows[1]["group_id"], rows[2]["group_id"]
    async with session_scope() as s:
        await F.hide_group(s, gb, True)
        assert [g["group_id"] for g in await F.unnamed_groups(s, hidden=True)] == [gb]
        out = await F.merge_groups(s, ga, [gb])
        assert out["person_id"] is None
        pid = await _person("FT Cy")
        await F.name_group(s, gc, pid)
        with pytest.raises(F.Refused):
            await F.hide_group(s, (await F.people_with_faces(s))[0]["group_id"], True)
        split = await F.split_faces(s, [rows[3]["id"]])
    moved = [r for r in await _faces() if r["id"] == rows[3]["id"]][0]
    assert moved["group_id"] == split["group_id"] and moved["person_id"] is None
    assert moved["confirmed"] and pid in moved["rejected_person_ids"]   # pinned, and not Cy
    assert {r["group_id"] for r in await _faces() if r["id"] in (rows[0]["id"], rows[1]["id"])} == {ga}


@requires_db
async def test_merging_two_named_people_moves_the_faces(fh) -> None:
    a = _base()
    await _photos(fh, {"1.png": [_near(a)], "2.png": [_near(_base())]})
    rows = await _faces()
    p1, p2 = await _person("FT Dee"), await _person("FT D")
    async with session_scope() as s:
        g1 = (await F.name_group(s, rows[0]["group_id"], p1))["group_id"]
        g2 = (await F.name_group(s, rows[1]["group_id"], p2))["group_id"]
        await F.merge_groups(s, g1, [g2])
    assert {r["person_id"] for r in await _faces()} == {p1}
    async with session_scope() as s:
        assert (await s.execute(text("SELECT count(*) FROM people WHERE id = :p"), {"p": p2})).scalar() == 1


@requires_db
async def test_forget_my_face_deletes_denylists_and_is_not_relearned(fh) -> None:
    a, b = _base(), _base()
    await _photos(fh, {"1.png": [_near(a)], "2.png": [_near(a), _near(b)], "3.png": [_near(a)]})
    pid = await _person("FT Eve")
    rows = await _faces()
    async with session_scope() as s:
        await F.name_group(s, rows[0]["group_id"], pid)
        # One more of her, left in a stray unnamed group.
        await s.execute(text("UPDATE media_faces SET person_id = NULL, confirmed = false, group_id = NULL "
                             "WHERE id = :f"), {"f": rows[3]["id"]})
        out = await F.forget_person(s, pid)
    assert out["denylisted"] == 1 and out["faces"] == 3 and out["groups"] == 1
    left = await _faces()
    assert len(left) == 1 and left[0]["id"] == rows[2]["id"]         # only b
    assert await _count("face_denylist") == 1
    async with session_scope() as s:
        assert (await s.execute(text("SELECT count(*) FROM people WHERE id = :p"), {"p": pid})).scalar() == 1
    # New photos of her are not learned; others are.
    await _photos(fh, {"4.png": [_near(a), _near(b)]})
    assert [r["rel_path"] for r in await _faces()] == ["2.png", "4.png"]
    # Nor does the video hook keep her.
    vf = await F.VideoFaces.start((await _items())["4.png"]["id"])
    fh["engine"].by_size[(9, 9)] = [_found(_near(a))]
    from PIL import Image

    assert await vf.frame(5.0, Image.new("RGB", (9, 9))) == 0


@requires_db
async def test_video_frames_become_appearances_with_moments(fh) -> None:
    from PIL import Image

    a, b = _base(), _base()
    await _photos(fh, {"v.png": []})
    item = (await _items())["v.png"]["id"]
    eng = fh["engine"]
    eng.by_size[(10, 10)] = [_found(_near(a), 0.5)]
    eng.by_size[(11, 10)] = [_found(_near(a), 0.8), _found(_near(b))]
    vf = await F.VideoFaces.start(item)
    frames = [(0.0, (10, 10)), (1.0, (11, 10)), (2.5, (11, 10)), (10.0, (10, 10)), (35.0, (10, 10)),
              (100.0, (10, 10))]
    saved = [await vf.frame(t, Image.new("RGB", size)) for t, size in frames]
    # 1.0 s is under the 2 s gap; at 2.5 s b appears; a is one appearance
    # (each sighting under 30 s after the last) until a 65 s gap, then a
    # second one.
    assert saved == [1, 0, 1, 0, 0, 1]
    rows = await _faces()
    # One row per appearance, first to last sighting.
    assert [(r["at_s"], r["end_s"]) for r in rows] == [(0.0, 35.0), (2.5, 2.5), (100.0, 100.0)]
    async with session_scope() as s:
        q = (await s.execute(text("SELECT quality FROM media_faces WHERE id = :f"), {"f": rows[0]["id"]})).scalar()
    assert q == pytest.approx(0.8)                                   # the better look kept
    # A restart carries on after the last saved moment.
    vf2 = await F.VideoFaces.start(item)
    assert vf2.resume_after == 100.0 and await vf2.frame(50.0, Image.new("RGB", (10, 10))) == 0
    pid = await _person("FT Fay")
    async with session_scope() as s:
        await F.name_group(s, rows[0]["group_id"], pid)
        items = await F.items_for_person(s, pid)
    assert items[0]["moments"] == [0.0, 100.0]
    assert items[0]["appearances"] == [{"at_s": 0.0, "end_s": 35.0}, {"at_s": 100.0, "end_s": 100.0}]
    # A confirmed appearance still grows its end; its best look stays as confirmed.
    async with session_scope() as s:
        await s.execute(text("UPDATE media_faces SET confirmed = true WHERE id = :f"), {"f": rows[2]["id"]})
    vf3 = await F.VideoFaces.start(item)
    vf3.open.append(F._Sighting(rows[2]["id"], F.unit(eng.by_size[(10, 10)][0].embedding), 0.5, 100.0))
    vf3.resume_after = None
    assert await vf3.frame(120.0, Image.new("RGB", (10, 10))) == 0
    assert [(r["at_s"], r["end_s"]) for r in await _faces()][2] == (100.0, 120.0)


@requires_db
async def test_faces_off_deletes_everything_and_on_again_starts_from_zero(fh, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import bearer, install_fake_db, web_client

    a = _base()
    await _photos(fh, {"1.png": [_near(a)], "2.png": [_near(a)]})
    pid = await _person("FT Gil")
    async with session_scope() as s:
        await F.name_group(s, (await _faces())[0]["group_id"], pid)
        await F.write_state(s, requeued=True)
    install_fake_db(monkeypatch, admin=True, sessions={"adm"})
    adm = bearer("adm")
    async with web_client() as c:
        r = await c.put("/api/media-understanding/settings", headers={**c.headers, **adm}, json={"faces": False})
        assert r.status_code == 409
        r = await c.put("/api/media-understanding/settings", headers={**c.headers, **adm},
                        json={"faces": False, "confirm_delete_faces": True})
        assert r.status_code == 200
    assert await _count("media_faces") == 0 and await _count("face_groups") == 0
    async with session_scope() as s:
        assert (await s.execute(text(
            "SELECT count(*) FROM media_item_stages WHERE analyser = 'faces'"))).scalar() == 0
        assert await F.read_state(s) == {}
        # While off nothing is stored, even by a step already under way.
        item = (await _items())["1.png"]["id"]
        with pytest.raises(F.FacesOff):
            await F.store(s, item, [_found(a)])
    # On again: the photos are queued for faces once the models are here.
    async with web_client() as c:
        await c.put("/api/media-understanding/settings", headers={**c.headers, **adm}, json={"faces": True})
    r = _runner(now=lambda: NIGHT)
    await F.housekeeping(type("X", (), {"sw": {"faces": True}, "in_night": lambda self: False})())
    assert all(i["state"] == "pending" for i in (await _items()).values())
    while await r.step():
        pass
    assert await _count("media_faces") == 2


@requires_db
async def test_housekeeping_regroups_once_a_night(fh, monkeypatch) -> None:
    calls = []

    async def fake_regroup(s):
        calls.append(1)
        return {"faces": 0, "suggested": 0, "groups_made": 0}

    monkeypatch.setattr(F, "regroup", fake_regroup)
    day, night = _HKRunner(True, night=False), _HKRunner(True, night=True)
    await F.housekeeping(day)
    assert calls == []
    await F.housekeeping(night)
    await F.housekeeping(night)
    assert calls == [1]


# ── the routes and their tiers ───────────────────────────────────────────


async def _seed_route_data(fh) -> dict[str, int]:
    a, b, c = _base(), _base(), _base()
    await _photos(fh, {"1.png": [_near(a)], "2.png": [_near(a)], "3.png": [_near(b)], "4.png": [_near(c)],
                       "5.png": [_near(c)]})
    rows = await _faces()
    pid = await _person("FT Hal")
    async with session_scope() as s:
        named = (await F.name_group(s, rows[3]["group_id"], pid))["group_id"]
    return {"ga": rows[0]["group_id"], "fa": rows[0]["id"], "gb": rows[2]["group_id"], "named": named,
            "pid": pid, "fnamed": rows[3]["id"], "fnamed2": rows[4]["id"]}


@requires_db
async def test_the_faces_routes_tier_matrix(fh, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db, web_client

    d = await _seed_route_data(fh)
    install_fake_db(monkeypatch, admin=True, sessions={"adm"}, device_token="household")
    creds = {"guest": {}, "household": {HEADER: "household"}, "admin": bearer("adm")}
    reads = ["/api/faces/status", "/api/faces/people", f"/api/faces/groups/{d['ga']}",
             f"/api/faces/people/{d['pid']}/items"]
    async with web_client() as c:
        for who, h in creds.items():
            for path in reads:
                r = await c.get(path, headers={**c.headers, **h})
                assert r.status_code == (401 if who == "guest" else 200), (who, path, r.status_code)
                assert "embedding" not in r.text and "centroid" not in r.text
            r = await c.get("/api/faces/hidden", headers={**c.headers, **h})
            assert r.status_code == {"guest": 401, "household": 403, "admin": 200}[who]
        hh = {**c.headers, HEADER: "household"}
        ad = {**c.headers, **bearer("adm")}
        # Admin-only for the household: merging or splitting a named person,
        # unhiding, moving a named group to someone else.
        for method, path, body in [
            ("POST", "/api/faces/merge", {"into": d["named"], "groups": [d["gb"]]}),
            ("POST", "/api/faces/split", {"face_ids": [d["fnamed"]]}),
            ("POST", f"/api/faces/groups/{d['gb']}/hide", {"hidden": False}),
            ("POST", f"/api/faces/groups/{d['named']}/name", {"name": "FT Someone Else"}),
        ]:
            r = await c.request(method, path, headers=hh, json=body)
            assert r.status_code == 403, (path, r.status_code, r.text)
            r = await c.request(method, path, headers={**c.headers}, json=body)
            assert r.status_code == 401, path
        # The household's own: name, confirm, not this person, hide, unnamed merge/split.
        r = await c.post(f"/api/faces/groups/{d['ga']}/name", headers=hh, json={"name": "FT Ida"})
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/faces/face/{d['fa']}/not-this-person", headers=hh)
        assert r.status_code == 200
        r = await c.post(f"/api/faces/face/{d['fa']}/confirm", headers=hh, json={"person_id": d["pid"]})
        assert r.status_code == 200 and r.json()["person_id"] == d["pid"]
        r = await c.post(f"/api/faces/groups/{d['gb']}/hide", headers=hh, json={"hidden": True})
        assert r.status_code == 200
        r = await c.get(f"/api/faces/groups/{d['gb']}", headers=hh)
        assert r.status_code == 403                                   # hidden: admin only
        r = await c.post(f"/api/faces/groups/{d['gb']}/hide", headers=ad, json={"hidden": False})
        assert r.status_code == 200
        r = await c.post("/api/faces/split", headers=ad, json={"face_ids": [d["fnamed2"]]})
        assert r.status_code == 200
        new = r.json()["group_id"]
        r = await c.post("/api/faces/merge", headers=hh, json={"into": d["gb"], "groups": [new]})
        assert r.status_code == 200, r.text
        r = await c.post("/api/faces/merge", headers=ad, json={"into": d["named"], "groups": [d["gb"]]})
        assert r.status_code == 200
        for bad in ({"into": 0, "groups": [1]}, {"into": d["named"], "groups": []}, {"into": "x"}):
            assert (await c.post("/api/faces/merge", headers=ad, json=bad)).status_code == 422
        # A person's photos load as the page scrolls: has_more, next_cursor.
        path = f"/api/faces/people/{d['pid']}/items"
        every = (await c.get(path, headers=hh)).json()
        assert every["has_more"] is False and every["next_cursor"] is None and len(every["items"]) >= 2
        one = (await c.get(path, headers=hh, params={"limit": 1})).json()
        assert one["has_more"] is True and one["next_cursor"] == "1" and len(one["items"]) == 1
        nxt = (await c.get(path, headers=hh, params={"limit": 1, "cursor": "1"})).json()
        assert nxt["items"][0]["item_id"] == every["items"][1]["item_id"]
        assert (await c.get(path, headers=hh, params={"cursor": "x"})).status_code == 422


@requires_db
async def test_shared_screens_and_guests_see_nothing(fh, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db, web_client

    d = await _seed_route_data(fh)
    async with session_scope() as s:
        await s.execute(text("INSERT INTO devices (device_id, name, shared_screen) "
                             "VALUES ('facetest-kitchen', 'Kitchen', true)"))
    install_fake_db(monkeypatch, admin=True, sessions={"adm"}, device_token="household")
    async with web_client() as c:
        for h in ({HEADER: "household"}, bearer("adm")):
            hdr = {**c.headers, **h, "X-Domovoi-Device-Id": "facetest-kitchen"}
            for path in ("/api/faces/status", "/api/faces/people", f"/api/faces/people/{d['pid']}/items"):
                r = await c.get(path, headers=hdr)
                assert r.status_code == 403 and "shared screen" in r.json()["detail"]
            r = await c.post(f"/api/faces/face/{d['fa']}/confirm", headers=hdr, json={"person_id": d["pid"]})
            assert r.status_code == 403
            r = await c.post(f"/api/faces/people/{d['pid']}/forget", headers=hdr)
            assert r.status_code == 403
        r = await c.get("/api/faces/people")
        assert r.status_code == 401
    assert await _count("face_denylist") == 0


@requires_db
async def test_forget_is_for_admins_and_the_persons_own_claimed_device(fh, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db, web_client
    from web.backend.api import devices as devices_api

    d = await _seed_route_data(fh)
    other = await _person("FT Jo")
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    async with session_scope() as s:
        for dev, pid, at in (("facetest-hal", d["pid"], old), ("facetest-jo", other, old),
                             ("facetest-new", d["pid"], datetime.now(timezone.utc))):
            await s.execute(text(
                "INSERT INTO devices (device_id, name, owner_person_id, claim_key_hash, owner_claimed_at) "
                "VALUES (:d, 'Phone', :p, :k, :t)"),
                {"d": dev, "p": pid, "k": devices_api.claim_key_hash("key-" + dev), "t": at})
    install_fake_db(monkeypatch, admin=True, sessions={"adm"}, device_token="household")
    path = f"/api/faces/people/{d['pid']}/forget"
    async with web_client() as c:
        hh = {**c.headers, HEADER: "household"}
        assert (await c.post(path, headers=hh)).status_code == 403                    # no device
        for dev in ("facetest-jo", "facetest-new"):                                   # not hers / too fresh
            r = await c.post(path, headers={**hh, "X-Domovoi-Device-Id": dev,
                                            devices_api.CLAIM_KEY_HEADER: "key-" + dev})
            assert r.status_code == 403
        r = await c.post(path, headers={**hh, "X-Domovoi-Device-Id": "facetest-hal",
                                        devices_api.CLAIM_KEY_HEADER: "wrong"})
        assert r.status_code == 403
        assert await _count("face_denylist") == 0
        r = await c.post(path, headers={**hh, "X-Domovoi-Device-Id": "facetest-hal",
                                        devices_api.CLAIM_KEY_HEADER: "key-facetest-hal"})
        assert r.status_code == 200 and r.json()["denylisted"] == 1
        # An admin can forget anyone (a child's face included).
        r = await c.post(f"/api/faces/people/{other}/forget", headers={**c.headers, **bearer("adm")})
        assert r.status_code == 200
        assert (await c.post("/api/faces/people/999999/forget",
                             headers={**c.headers, **bearer("adm")})).status_code == 404


@requires_db
async def test_face_routes_answer_409_while_faces_are_off(fh, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import HEADER, install_fake_db, web_client

    async with session_scope() as s:
        sw = await switches.read(s)
        await switches.write(s, {**sw, "faces": False})
    install_fake_db(monkeypatch, admin=True, sessions={"adm"}, device_token="household")
    async with web_client() as c:
        hh = {**c.headers, HEADER: "household"}
        assert (await c.get("/api/faces/people", headers=hh)).status_code == 409
        assert (await c.post("/api/faces/split", headers=hh, json={"face_ids": [1]})).status_code == 409
        st = (await c.get("/api/faces/status", headers=hh)).json()
        assert st["enabled"] is False


# ─── no export of face data ──────────────────────────────────────────────

FACE_TABLES = ("media_faces", "face_groups", "face_denylist")
#: The only code that may read or write the face tables. Exports
#: (chat drafts, documents, Friend Houses conversations), the help desk,
#: support sessions and plugins (no privilege on core tables) are not on it.
FACE_TABLE_READERS = {
    "domovoi/media_understanding/faces.py",
    "domovoi/media_understanding/catalog.py",       # deletes results with the file
    "web/backend/api/faces.py",
    "web/backend/api/media_understanding.py",       # counts, and faces off
}


def test_face_tables_are_touched_only_by_the_faces_code() -> None:
    import re

    pat = re.compile(r"\b(" + "|".join(FACE_TABLES) + r")\b")
    import subprocess

    files = subprocess.run(["git", "ls-files", "*.py", "*.js", "*.jsx", "*.sh"], cwd=REPO,
                           capture_output=True, text=True).stdout.split()
    assert files
    hits = set()
    for rel in files:
        if "/tests/" in rel or rel.startswith(("ios/", "android/")):
            continue
        p = REPO / rel
        if p.is_file() and pat.search(p.read_text(encoding="utf-8", errors="replace")):
            hits.add(rel)
    assert hits <= FACE_TABLE_READERS, sorted(hits - FACE_TABLE_READERS)


def test_no_route_answers_with_an_embedding() -> None:
    from fastapi.routing import APIRoute

    from domovoi.main import app as core_app
    from web.backend.main import app as web_app

    for app in (core_app, web_app):
        for r in app.routes:
            if isinstance(r, APIRoute) and "export" in r.path:
                src = Path(r.endpoint.__code__.co_filename).read_text(encoding="utf-8")
                assert not any(t in src for t in FACE_TABLES), r.path
    face_routes = [r for r in web_app.routes if isinstance(r, APIRoute) and r.path.startswith("/api/faces")]
    assert face_routes and not any("export" in r.path or "download" in r.path for r in face_routes)
    src = (REPO / "web" / "backend" / "api" / "faces.py").read_text(encoding="utf-8")
    fsrc = (REPO / "domovoi" / "media_understanding" / "faces.py").read_text(encoding="utf-8")
    # The read helpers never select an embedding or a centroid.
    for fn in ("people_with_faces", "unnamed_groups", "group_faces", "items_for_person"):
        body = fsrc.split(f"async def {fn}(")[1].split("\nasync def ")[0]
        assert "embedding" not in body and "centroid" not in body, fn
    assert "FACE_COLUMNS" in fsrc and "embedding" not in fsrc.split("FACE_COLUMNS = ")[1].split("\n\n")[0]
    assert "embedding" not in src.replace("embedding or a centroid", "")
