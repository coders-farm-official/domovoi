"""GET /api/media/items/{id}/details (1.5.0, the Photos Info sheet;
web/backend/api/media_details.py).

Pinned: the answer's shape for a photo and a video (frames, transcript,
summary, pages); an unanalysed item; a failed one with its error code;
untrusted text returned exactly as stored; and the gates: a guest gets
401, places only while locations are on and never on a shared screen,
faces only while faces are on and never on a shared screen, and a face in
a hidden group only for an admin. The UI: test_web_photos_details.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from domovoi.db.session import session_scope
from domovoi.media_understanding import switches
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_media_understanding import house  # noqa: F401 — fixture

EVIL = "<img src=x onerror=alert(1)> **bold** [link](javascript:alert(2))"
DEVICE = "detailstest-kitchen"


async def _sw(**changes: Any) -> None:
    async with session_scope() as s:
        await switches.write(s, {**switches.DEFAULTS, "enabled": True, **changes})


async def _item(rel: str, kind: str = "image", **cols: Any) -> int:
    base = {"size_bytes": 2048, "mtime": 1_700_000_000.0, "state": "done"}
    base.update(cols)
    names = ", ".join(base)
    vals = ", ".join(f":{k}" for k in base)
    async with session_scope() as s:
        return int((await s.execute(text(
            f"INSERT INTO media_items (library_id, rel_path, kind, {names}) "
            f"VALUES ('core:pictures', :rel, :kind, {vals}) RETURNING id"),
            {"rel": rel, "kind": kind, **base})).scalar())


async def _sql(sql: str, **params: Any) -> Any:
    async with session_scope() as s:
        r = await s.execute(text(sql), params)
        return r.scalar() if r.returns_rows else None


@pytest.fixture
async def dh(house, monkeypatch):  # noqa: F811
    from domovoi.tests.auth_testkit import install_fake_db

    async with session_scope() as s:
        await s.execute(text("INSERT INTO devices (device_id, name, shared_screen) VALUES (:d, 'Kitchen', true) "
                             "ON CONFLICT (device_id) DO UPDATE SET shared_screen = true"), {"d": DEVICE})
    install_fake_db(monkeypatch, admin=True, sessions={"adm"}, device_token="household")
    yield house
    async with session_scope() as s:
        await s.execute(text("DELETE FROM devices WHERE device_id = :d"), {"d": DEVICE})
        await s.execute(text("TRUNCATE face_groups RESTART IDENTITY CASCADE"))
        await s.execute(text("DELETE FROM people WHERE name LIKE 'DT %'"))


async def _get(c, item_id: int, who: str = "household", shared: bool = False, **params: Any):
    from domovoi.tests.auth_testkit import HEADER, bearer

    h = {**c.headers}
    if who == "household":
        h[HEADER] = "household"
    elif who == "admin":
        h.update(bearer("adm"))
    if shared:
        h["X-Domovoi-Device-Id"] = DEVICE
    return await c.get(f"/api/media/items/{item_id}/details", headers=h, params=params)


async def _photo_with_everything() -> dict[str, int]:
    from datetime import datetime, timezone

    pid = await _item("Trips/2024/<img src=x onerror=alert(1)>.jpg", taken_at=datetime(2024, 7, 1, 10, tzinfo=timezone.utc),
                      place_name="Portland, Oregon, United States",
                      analysed_at=datetime(2024, 7, 2, 3, tzinfo=timezone.utc), vision_model="vision-small:1b",
                      size_bytes=2_500_000)
    await _sql("UPDATE media_items SET gps = CAST(:g AS jsonb) WHERE id = :i", g='{"lat": 45.5, "lon": -122.6}', i=pid)
    await _sql("INSERT INTO media_frames (item_id, idx, at_s, caption, tags, has_text, ocr_text, model) "
               "VALUES (:i, 0, NULL, :c, ARRAY['<b>dog</b>', 'beach'], true, :o, 'vision-small:1b')",
               i=pid, c=EVIL, o="<script>alert(3)</script>")
    alex = int(await _sql("INSERT INTO people (name) VALUES ('DT alex') RETURNING id"))
    g_named = int(await _sql("INSERT INTO face_groups (person_id) VALUES (:p) RETURNING id", p=alex))
    g_open = int(await _sql("INSERT INTO face_groups DEFAULT VALUES RETURNING id"))
    g_hidden = int(await _sql("INSERT INTO face_groups (hidden) VALUES (true) RETURNING id"))
    for gid, person, confirmed in ((g_named, alex, True), (g_open, None, False), (g_hidden, None, False)):
        await _sql("INSERT INTO media_faces (item_id, box, embedding, model, group_id, person_id, confirmed, quality) "
                   "VALUES (:i, ARRAY[0.1, 0.1, 0.2, 0.2]::real[], '\\x00'::bytea, 'm', :g, :p, :c, 0.9) RETURNING id",
                   i=pid, g=gid, p=person, c=confirmed)
    return {"photo": pid, "alex": alex, "named": g_named, "open": g_open, "hidden": g_hidden}


@requires_db
async def test_a_photos_details_shape_and_verbatim_text(dh) -> None:
    from domovoi.tests.auth_testkit import web_client

    ids = await _photo_with_everything()
    await _sw(faces=True, places=True)
    async with web_client() as c:
        r = await _get(c, ids["photo"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["id"] == ids["photo"] and d["kind"] == "image"
    assert d["name"] == "<img src=x onerror=alert(1)>.jpg" and d["folder"] == "Trips/2024"
    assert d["library_id"] == "core:pictures" and d["library"] == "Pictures"
    assert d["size_bytes"] == 2_500_000 and d["date_from"] == "photo"
    assert d["taken_at"].startswith("2024-07-01T10:00:00")
    assert d["understanding"] == {"on": True, "faces_shown": True, "places_shown": True}
    a = d["analysis"]
    assert a["state"] == "done" and a["error_code"] is None and a["vision_model"] == "vision-small:1b"
    assert a["analysed_at"].startswith("2024-07-02T03:00:00")
    # Untrusted text exactly as stored: no escaping, no markdown, no stripping.
    assert d["might_show"] == EVIL
    assert d["tags"] == ["<b>dog</b>", "beach"]
    assert d["text"] == "<script>alert(3)</script>" and d["text_cut"] is False
    assert d["place"] == "Portland, Oregon, United States"
    assert "45.5" not in r.text and "122.6" not in r.text and "gps" not in r.text
    assert "embedding" not in r.text and "centroid" not in r.text and "box" not in r.text
    # The household: the named person and the unnamed group; the hidden
    # (stranger's) group is left out, uncounted.
    faces = d["faces"]
    assert [(f["name"], f["group_id"], f["confirmed"]) for f in faces] == [
        ("DT alex", ids["named"], True), (None, ids["open"], False)]
    assert all("hidden" not in f for f in faces)
    assert "frames" not in d and "transcript" not in d and "summary" not in d


@requires_db
async def test_who_sees_what(dh) -> None:
    from domovoi.tests.auth_testkit import web_client

    ids = await _photo_with_everything()
    await _sw(faces=True, places=True)
    async with web_client() as c:
        # A guest: nothing.
        assert (await _get(c, ids["photo"], who="guest")).status_code == 401
        # An admin also sees the face in the hidden group, marked.
        d = (await _get(c, ids["photo"], who="admin")).json()
        assert sorted((f["group_id"], f["hidden"]) for f in d["faces"]) == sorted(
            [(ids["named"], False), (ids["open"], False), (ids["hidden"], True)])
        # A shared screen (household or admin): no place, no faces.
        for who in ("household", "admin"):
            r = await _get(c, ids["photo"], who=who, shared=True)
            d = r.json()
            assert r.status_code == 200
            assert "place" not in d and "faces" not in d, who
            assert d["understanding"] == {"on": True, "faces_shown": False, "places_shown": False}
            assert "DT alex" not in r.text and "Portland" not in r.text
            assert d["might_show"] == EVIL
        assert (await _get(c, 999_999)).status_code == 404
    # Faces off, places off: neither, for anyone.
    await _sw(faces=False, places=False)
    async with web_client() as c:
        for who in ("household", "admin"):
            r = await _get(c, ids["photo"], who=who)
            d = r.json()
            assert "place" not in d and "faces" not in d
            assert d["understanding"]["faces_shown"] is False and d["understanding"]["places_shown"] is False
            assert "DT alex" not in r.text and "Portland" not in r.text
    # Faces on, places off.
    await _sw(faces=True, places=False)
    async with web_client() as c:
        d = (await _get(c, ids["photo"])).json()
        assert "place" not in d and len(d["faces"]) == 2


@requires_db
async def test_a_videos_frames_transcript_and_summary(dh, monkeypatch) -> None:
    from domovoi.tests.auth_testkit import web_client
    from web.backend.api import media_details

    vid = await _item("clips/party.mp4", kind="video", duration_s=125.0, vision_model="vision-small:1b",
                      frame_pass="quick")
    for n in range(5):
        await _sql("INSERT INTO media_frames (item_id, idx, at_s, caption, tags, has_text, model) "
                   "VALUES (:i, :n, :t, :c, ARRAY['party'], false, 'v')",
                   i=vid, n=n, t=float(n * 10), c=f"frame {n} <i>cake</i>")
    for n in range(7):
        await _sql("INSERT INTO media_segments (item_id, idx, start_s, end_s, text) VALUES (:i, :n, :a, :b, :t)",
                   i=vid, n=n, a=float(n * 3), b=float(n * 3 + 2), t=f"line {n} <a href=x>here</a>")
    await _sql("INSERT INTO media_summaries (item_id, summary, suggested_title, model) VALUES (:i, :s, :t, 'qa')",
               i=vid, s="**a birthday** <img src=x>", t="Party <em>time</em>")
    await _sw()
    async with web_client() as c:
        d = (await _get(c, vid)).json()
        assert d["kind"] == "video" and d["duration_s"] == 125.0 and d["analysis"]["frame_pass"] == "quick"
        assert [f["at_s"] for f in d["frames"]] == [0.0, 10.0, 20.0, 30.0, 40.0]
        assert d["frames"][1] == {"at_s": 10.0, "might_show": "frame 1 <i>cake</i>", "tags": ["party"], "text": None}
        assert d["frames_page"] == {"offset": 0, "limit": media_details.FRAMES_DEFAULT, "total": 5,
                                    "more": False, "next": None}
        assert d["transcript"][0] == {"start_s": 0.0, "end_s": 2.0, "text": "line 0 <a href=x>here</a>"}
        assert d["transcript_page"]["total"] == 7
        assert d["summary"] == {"text": "**a birthday** <img src=x>", "suggested_title": "Party <em>time</em>",
                                "model": "qa"}
        # Paged: a page of 3, then the rest.
        d = (await _get(c, vid, segments_limit=3, frames_limit=2)).json()
        assert len(d["transcript"]) == 3 and d["transcript_page"] == {
            "offset": 0, "limit": 3, "total": 7, "more": True, "next": 3}
        assert len(d["frames"]) == 2 and d["frames_page"]["next"] == 2
        d = (await _get(c, vid, segments_from=6, segments_limit=3)).json()
        assert [t["text"] for t in d["transcript"]] == ["line 6 <a href=x>here</a>"]
        assert d["transcript_page"]["more"] is False
        # Caps: a page larger than the cap is refused.
        r = await _get(c, vid, segments_limit=media_details.SEGMENTS_MAX + 1)
        assert r.status_code == 422
        r = await _get(c, vid, frames_limit=media_details.FRAMES_MAX + 1)
        assert r.status_code == 422


@requires_db
async def test_an_unanalysed_and_a_failed_item(dh) -> None:
    from domovoi.tests.auth_testkit import web_client

    new = await _item("new/IMG_0001.jpg", state="pending")
    bad = await _item("bad/broken.jpg", state="failed", error_code="MU-PHOTO-3", attempts=3)
    vid = await _item("new/clip.mp4", kind="video", state="pending")
    await _sw()
    async with web_client() as c:
        r = await _get(c, new)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["analysis"]["state"] == "pending" and d["analysis"]["analysed_at"] is None
        assert d["might_show"] is None and d["tags"] == [] and d["text"] is None
        assert d["date_from"] == "file" and d["taken_at"].startswith("2023-11-14")
        d = (await _get(c, bad)).json()
        assert d["analysis"]["state"] == "failed" and d["analysis"]["error_code"] == "MU-PHOTO-3"
        assert d["analysis"]["attempts"] == 3
        d = (await _get(c, vid)).json()
        assert d["frames"] == [] and d["transcript"] == [] and d["summary"] is None
        assert d["frames_page"]["total"] == 0 and d["transcript_page"]["more"] is False
    # Understanding off: the file's facts still come, and the switch says so.
    await _sw(enabled=False)
    async with web_client() as c:
        d = (await _get(c, new)).json()
        assert d["understanding"]["on"] is False and d["name"] == "IMG_0001.jpg"
