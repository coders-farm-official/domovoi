"""F-024 — the track drawer edits title / artist / album, and the PATCH
behind it writes only what changed.

The drawer rendered title, artist and album as static text; the only
``PATCH /api/music/library/{id}`` the SPA sent was ``{favorited}``, and
``TrackPatch`` accepted nothing else, so a mistagged track could never
be corrected from the dashboard (finding F-024, card MUS-10).

Two DB-free halves (never ``requires_db``, never skips):

* the API: ``TrackPatch`` takes the three tags; ``patch_track`` SETs the
  fields it was sent (a metadata edit also stamps ``enriched_at`` so the
  enricher's unenriched-rows sweep can't undo it; a favorite flip does
  not) — checked against a fake session that records the SQL;
* the page: web/static/music.jsx driven through
  domovoi/tests/jsx_interact_harness.js — open a row, click the pencil,
  change the title and artist, save, and read the one PATCH that went
  out and what the drawer shows afterwards.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from web.backend.api import music as music_api
from web.backend.schemas import TrackPatch

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/music.jsx"]

ADDED = datetime(2026, 9, 1, tzinfo=timezone.utc)


# ── the API half ─────────────────────────────────────────────────────

class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    """Records (sql, params); the UPDATE returns a RETURNING-shaped row
    reflecting the params it was given."""

    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, clause, params=None):
        sql = " ".join(str(clause).split())
        self.calls.append((sql, params))
        if "UPDATE library_tracks" in sql:
            p = params or {}
            return _Result((p["id"], "/music/hearth_06.mp3", p.get("title", "Warm Stones"),
                            p.get("artist", "Hearth Ensemble"), p.get("album", "By The Hearth"),
                            200, None, None, None, ADDED, "manual", None, p.get("favorited", False)))
        return _Result(None)


@pytest.fixture
def fake_session(monkeypatch):
    session = _FakeSession()

    @asynccontextmanager
    async def scope():
        yield session

    monkeypatch.setattr(music_api, "session_scope", scope)
    return session


def _update(session: _FakeSession) -> tuple[str, dict]:
    hits = [(sql, p) for sql, p in session.calls if "UPDATE library_tracks" in sql]
    assert len(hits) == 1, session.calls
    return hits[0]


def _set_clause(sql: str) -> str:
    return sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]


def test_track_patch_takes_the_three_tags_and_nothing_else():
    assert TrackPatch(title="Warm Stones", artist="Hearth", album="By The Hearth").model_dump(
        exclude_unset=True) == {"title": "Warm Stones", "artist": "Hearth", "album": "By The Hearth"}
    assert TrackPatch(title=None).model_dump(exclude_unset=True) == {"title": None}   # clears the tag
    # unknown keys never reach the SET clause
    assert TrackPatch(file_path="/etc/passwd").model_dump(exclude_unset=True) == {}


@pytest.mark.asyncio
async def test_patch_writes_only_the_changed_tags_and_pins_them(fake_session):
    out = await music_api.patch_track(6, TrackPatch(title="Warm Stones (live)", artist="Hearth"))
    sql, params = _update(fake_session)
    assert _set_clause(sql) == "title = :title, artist = :artist, enriched_at = NOW()"
    assert params == {"id": 6, "title": "Warm Stones (live)", "artist": "Hearth"}
    assert (out.title, out.artist, out.album) == ("Warm Stones (live)", "Hearth", "By The Hearth")
    assert not any("pg_notify('playlists_changed'" in s for s, _ in fake_session.calls)


@pytest.mark.asyncio
async def test_favorite_flip_does_not_touch_enriched_at(fake_session):
    await music_api.patch_track(6, TrackPatch(favorited=True))
    sql, params = _update(fake_session)
    assert _set_clause(sql) == "favorited = :favorited"
    assert params == {"id": 6, "favorited": True}
    assert any("pg_notify('playlists_changed', 'favorited')" in s for s, _ in fake_session.calls)


@pytest.mark.asyncio
async def test_empty_patch_is_a_400(fake_session):
    with pytest.raises(HTTPException) as exc:
        await music_api.patch_track(6, TrackPatch())
    assert exc.value.status_code == 400
    assert fake_session.calls == []


@pytest.mark.asyncio
async def test_missing_track_is_a_404(fake_session, monkeypatch):
    async def gone(clause, params=None):
        return _Result(None)
    monkeypatch.setattr(fake_session, "execute", gone)
    with pytest.raises(HTTPException) as exc:
        await music_api.patch_track(999, TrackPatch(title="x"))
    assert exc.value.status_code == 404


# ── the page half ────────────────────────────────────────────────────

TRACK = {"id": 6, "title": "Warm Stones", "artist": "Hearth Ensemble", "album": "By The Hearth",
         "duration_sec": 200, "file_path": "/music/hearth_06.mp3", "added_at": "2026-09-01T00:00:00Z",
         "added_via": "manual", "favorited": False, "source": None, "source_id": None, "enriched_at": None}
PAGE_API = {"GET /api/music/now-playing": [], "GET /api/playlists": [],
            "GET /api/music/library/stats": {"total_tracks": 1, "total_duration_sec": 200,
                                             "by_added_via": {"manual": 1}, "by_source": {"library": 1},
                                             "enriched_count": 0},
            "GET /api/acquisitions?limit=100": None,
            "GET /api/music/library?sort=added_desc&limit=12&offset=0": {"total": 1, "items": [TRACK]}}

EDIT_SCRIPT = r"""
  h.render();
  await h.click({ type: 'tr', nth: 1 });                       // the one library row
  const drawerOpen = h.text().includes('track · #6');
  const staticBefore = { title: !!h.find({ text: 'Warm Stones' }), field: !!h.find({ placeholder: 'title' }) };
  await h.click({ type: 'button', title: 'edit tags' });
  const fields = ['title', 'artist', 'album'].map((ph) => h.plain(h.find({ placeholder: ph })));
  const saveBeforeTyping = h.plain(h.find({ type: 'button', text: 'save' }));
  await h.type({ placeholder: 'title' }, 'Warm Stones (live)');
  await h.type({ placeholder: 'artist' }, '  Hearth  ');
  const saveAfterTyping = h.plain(h.find({ type: 'button', text: 'save' }));
  await h.click({ type: 'button', text: 'save' });
  return { drawerOpen, staticBefore, fields, saveBeforeTyping, saveAfterTyping,
           calls: h.calls, texts: h.text(), editing: !!h.find({ placeholder: 'title' }) };
"""

SCENARIOS = {
    "edit": {
        "files": FILES, "component": "MusicPage",
        "api": {**PAGE_API,
                "PATCH /api/music/library/6": {**TRACK, "title": "Warm Stones (live)", "artist": "Hearth",
                                               "enriched_at": "2026-09-22T00:00:00Z"}},
        "script": EDIT_SCRIPT,
    },
    "cancel": {
        "files": FILES, "component": "MusicPage", "api": PAGE_API,
        "script": r"""
          h.render();
          await h.click({ type: 'tr', nth: 1 });
          await h.click({ type: 'button', title: 'edit tags' });
          await h.type({ placeholder: 'title' }, 'nope');
          await h.click({ type: 'button', text: 'cancel' });
          return { calls: h.calls, editing: !!h.find({ placeholder: 'title' }),
                   title: !!h.find({ text: 'Warm Stones' }) };
        """,
    },
    # A drawer without an editor (no onEdit) shows no pencil — plugin
    # embeds and the old call shape keep working.
    "no_editor": {
        "files": FILES, "component": "Drawer",
        "props": {"track": TRACK, "rooms": ["office"]},
        "fnProps": ["onClose", "onDelete", "onPlayInRoom", "onBrowserPlay", "onQueueTrack"],
        "script": "h.render(); return { pencil: !!h.find({ type: 'button', title: 'edit tags' }) };",
    },
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def test_pencil_turns_the_header_into_prefilled_fields(driven):
    r = driven["edit"]
    assert r["drawerOpen"] and r["staticBefore"] == {"title": True, "field": False}
    assert [f["props"]["value"] for f in r["fields"]] == ["Warm Stones", "Hearth Ensemble", "By The Hearth"]
    assert r["saveBeforeTyping"]["props"].get("disabled") is True      # nothing changed yet
    assert not r["saveAfterTyping"]["props"].get("disabled")


def test_save_patches_only_the_changed_tags_trimmed(driven):
    r = driven["edit"]
    patches = [c for c in r["calls"] if c["method"] == "PATCH"]
    assert patches == [{"method": "PATCH", "path": "/api/music/library/6",
                        "body": {"title": "Warm Stones (live)", "artist": "Hearth"}}]


def test_drawer_shows_the_saved_row_and_leaves_edit_mode(driven):
    r = driven["edit"]
    assert r["editing"] is False
    assert "Warm Stones (live)" in r["texts"] and "Hearth" in r["texts"]
    assert 'updated "Warm Stones (live)"' in r["texts"]


def test_cancel_sends_nothing_and_keeps_the_old_tags(driven):
    r = driven["cancel"]
    assert [c for c in r["calls"] if c["method"] == "PATCH"] == []
    assert r["editing"] is False and r["title"] is True


def test_drawer_without_an_editor_has_no_pencil(driven):
    assert driven["no_editor"]["pencil"] is False
