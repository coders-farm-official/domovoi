"""F-021 — a create-playlist entry point on the Playlists tab, four fields
posted in one request, and the cover colour rendered as a swatch.

The Playlists tab had no create control at all (its empty state told the
user to click a library row that, with an empty library, did not exist);
the only create form — inside the add-to-playlist drawer — took a name,
and ``POST /api/playlists`` accepted nothing else, so description, colour
and emoji could only be set by creating and then editing. The list row
tinted only the fallback icon with ``cover_color``, so a playlist with an
emoji never showed its colour (finding F-021, card MUS-06).

Two halves, both DB-free (never ``requires_db``, never skips):

* the API: ``PlaylistCreate`` carries the presentation fields and
  ``create_playlist`` INSERTs all four — checked against a fake session
  that records the SQL it is handed;
* the page: web/static/music.jsx driven through
  domovoi/tests/jsx_interact_harness.js — click "new playlist", fill the
  four fields, create, and read what was POSTed; the row swatch; the
  drawer's edit form posting through the same field mapping.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

from web.backend.api import playlists as playlists_api
from web.backend.schemas import PlaylistCreate

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/music.jsx"]


# ── the API half ─────────────────────────────────────────────────────

class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    """Records every (sql, params) pair; the INSERT returns a row shaped
    like the real RETURNING clause, the duplicate pre-check finds nothing."""

    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, clause, params=None):
        sql = str(clause)
        self.calls.append((sql, params))
        if "INSERT INTO playlists" in sql:
            p = params or {}
            return _Result((7, p["n"], None, p.get("description"),
                            p.get("cover_color"), p.get("cover_emoji")))
        return _Result(None)


@pytest.fixture
def fake_session(monkeypatch):
    session = _FakeSession()

    @asynccontextmanager
    async def scope():
        yield session

    monkeypatch.setattr(playlists_api, "session_scope", scope)
    return session


def _insert(session: _FakeSession) -> tuple[str, dict]:
    hits = [(sql, p) for sql, p in session.calls if "INSERT INTO playlists" in sql]
    assert len(hits) == 1, session.calls
    return hits[0]


def test_create_schema_takes_the_presentation_fields():
    payload = PlaylistCreate(name="Road Trip", description="summer drive",
                             cover_color="#ff8800", cover_emoji="🚗")
    assert payload.model_dump() == {"name": "Road Trip", "description": "summer drive",
                                    "cover_color": "#ff8800", "cover_emoji": "🚗"}
    assert PlaylistCreate(name="x").model_dump() == {"name": "x", "description": None,
                                                     "cover_color": None, "cover_emoji": None}


@pytest.mark.asyncio
async def test_create_inserts_all_four_fields_in_one_statement(fake_session):
    out = await playlists_api.create_playlist(PlaylistCreate(
        name="Road Trip", description="summer drive", cover_color="#ff8800", cover_emoji="🚗"))
    sql, params = _insert(fake_session)
    assert "INSERT INTO playlists (name, description, cover_color, cover_emoji)" in sql
    assert params == {"n": "Road Trip", "description": "summer drive",
                      "cover_color": "#ff8800", "cover_emoji": "🚗"}
    assert (out.name, out.description, out.cover_color, out.cover_emoji) == (
        "Road Trip", "summer drive", "#ff8800", "🚗")
    assert out.track_count == 0 and out.is_virtual is False
    assert any("pg_notify('playlists_changed'" in sql for sql, _ in fake_session.calls)


@pytest.mark.asyncio
async def test_create_with_a_name_only_still_works(fake_session):
    out = await playlists_api.create_playlist(PlaylistCreate(name="Just a name"))
    _, params = _insert(fake_session)
    assert params == {"n": "Just a name", "description": None, "cover_color": None, "cover_emoji": None}
    assert out.cover_color is None


@pytest.mark.asyncio
async def test_create_rejects_a_cover_color_that_could_escape_the_style(fake_session):
    with pytest.raises(HTTPException) as exc:
        await playlists_api.create_playlist(PlaylistCreate(name="x", cover_color="red; color: blue"))
    assert exc.value.status_code == 400
    assert "cover_color" in exc.value.detail
    assert fake_session.calls == []          # rejected before touching the DB


# ── the page half ────────────────────────────────────────────────────

PLAYLIST = {"id": 2, "name": "W1b Mix", "track_count": 1, "is_virtual": False,
            "description": "", "cover_color": "#ff8800", "cover_emoji": "🚗", "created_at": None}
FAVORITES = {"id": 0, "name": "Favorites", "track_count": 0, "is_virtual": True,
             "description": None, "cover_color": None, "cover_emoji": None, "created_at": None}
PAGE_API = {"GET /api/music/now-playing": [], "GET /api/music/library/stats": None,
            "GET /api/acquisitions?limit=100": None}

FILL_AND_CREATE = r"""
  h.render();
  await h.click({ type: 'button', text: 'Playlists' });
  const beforeOpen = { newButton: !!h.find({ type: 'button', text: 'new playlist' }),
                       nameField: !!h.find({ placeholder: 'name' }) };
  await h.click({ type: 'button', text: 'new playlist' });
  const fields = ['name', 'description (optional)', 'emoji'].map((ph) => !!h.find({ placeholder: ph }));
  const color = h.plain(h.find({ type: 'input', title: 'cover color' }));
  await h.type({ placeholder: 'name' }, 'Road Trip');
  await h.type({ placeholder: 'description (optional)' }, 'summer drive');
  await h.type({ type: 'input', title: 'cover color' }, '#ff8800');
  await h.type({ placeholder: 'emoji' }, '🚗');
  const preview = h.plain(h.findAll((el) => el.props['data-cover-color'] != null).pop());
  const previewEmoji = !!h.find({ type: 'span', text: '🚗' });
  await h.click({ type: 'button', text: 'create' });
  return { beforeOpen, fields, color, preview, previewEmoji,
           calls: h.calls, texts: h.text(),
           formStillOpen: !!h.find({ placeholder: 'name' }) };
"""

SCENARIOS = {
    "create_from_tab": {
        "files": FILES, "component": "MusicPage",
        "api": {**PAGE_API, "GET /api/playlists": [FAVORITES],
                "POST /api/playlists": {"id": 9, "name": "Road Trip", "track_count": 0}},
        "script": FILL_AND_CREATE,
    },
    # No playlists at all (the API returned nothing): the control must still be there.
    "create_from_empty": {
        "files": FILES, "component": "PlaylistsTab",
        "props": {"playlists": [], "loading": False}, "fnProps": ["onSelect", "onPlay", "onCreate", "fire"],
        "script": ("h.render(); const before = h.text(); await h.click({ type: 'button', text: 'new playlist' });"
                   " return { before, after: h.text(), nameField: !!h.find({ placeholder: 'name' }) };"),
    },
    "row_swatch": {
        "files": FILES, "component": "PlaylistsTab",
        "props": {"playlists": [FAVORITES, PLAYLIST], "loading": False},
        "fnProps": ["onSelect", "onPlay", "onCreate", "fire"],
        "script": ("h.render(); const sw = h.findAll((el) => el.props['data-cover-color'] != null).map(h.plain);"
                   " return { swatches: sw, texts: h.text() };"),
    },
    "edit_in_drawer": {
        "files": FILES, "component": "MusicPage",
        "api": {**PAGE_API, "GET /api/playlists": [FAVORITES, PLAYLIST],
                "GET /api/playlists/2/tracks": [], "PATCH /api/playlists/2": {**PLAYLIST, "name": "Coast Road"}},
        "script": r"""
          h.render();
          await h.click({ type: 'button', text: 'Playlists' });
          await h.click({ type: 'tr', nth: 2 });   // header row, Favorites, W1b Mix
          await h.click({ type: 'button', text: 'edit' });
          const prefilled = h.plain(h.find({ placeholder: 'name' }));
          await h.type({ placeholder: 'name' }, 'Coast Road');
          await h.type({ placeholder: 'description (optional)' }, '   ');
          await h.click({ type: 'button', text: 'save' });
          return { prefilled, calls: h.calls };
        """,
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


def test_playlists_tab_has_a_new_playlist_button_that_opens_the_form(driven):
    r = driven["create_from_tab"]
    assert r["beforeOpen"] == {"newButton": True, "nameField": False}
    assert r["fields"] == [True, True, True]
    assert r["color"]["props"].get("type") == "color"


def test_create_posts_all_four_fields_once(driven):
    r = driven["create_from_tab"]
    posts = [c for c in r["calls"] if c["method"] == "POST"]
    assert posts == [{"method": "POST", "path": "/api/playlists",
                      "body": {"name": "Road Trip", "description": "summer drive",
                               "cover_color": "#ff8800", "cover_emoji": "🚗"}}]
    assert "created Road Trip" in r["texts"]
    assert r["formStillOpen"] is False


def test_form_previews_the_swatch_while_typing(driven):
    r = driven["create_from_tab"]
    assert r["preview"]["props"]["data-cover-color"] == "#ff8800"
    assert r["previewEmoji"] is True        # the emoji sits in the swatch's span


def test_create_control_exists_with_no_playlists_at_all(driven):
    r = driven["create_from_empty"]
    assert "new playlist" in r["before"]
    assert any("create one above" in t for t in r["before"])
    assert r["nameField"] is True


def test_row_shows_the_colour_behind_the_emoji(driven):
    r = driven["row_swatch"]
    colours = [s["props"]["data-cover-color"] for s in r["swatches"]]
    assert colours == ["#ff8800"]            # Favorites has none; W1b Mix shows its colour
    assert "🚗" in r["texts"]


def test_drawer_edit_uses_the_same_form_and_field_mapping(driven):
    r = driven["edit_in_drawer"]
    assert r["prefilled"]["props"]["value"] == "W1b Mix"
    patches = [c for c in r["calls"] if c["method"] == "PATCH"]
    assert patches == [{"method": "PATCH", "path": "/api/playlists/2",
                        "body": {"name": "Coast Road", "description": None,
                                 "cover_color": "#ff8800", "cover_emoji": "🚗"}}]
