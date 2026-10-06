"""The lyrics build after its four parts were merged (lyrics-build CONTRACT
§16): the checks no single builder could make on its own branch.

* Settings → Configuration → Library lists the three lyrics switches in
  their places among the music ones (B2's "Find songs by their words",
  B1's two LRCLIB rows).
* ``domovoi/main.py``'s worker-order comment names the workers in the
  order they are really registered (B1 and B2 each added theirs).
* The Android models read every field the web's lyrics routes send, under
  the same JSON names (B3 serves, B4 reads).
* End to end on the test lane (``requires_db``): an owner's ``.lrc`` read by
  the scan (B1), indexed (B2), found by lyric search (B2) and served by the
  web on the household tier with the core's REAL status dicts in its
  snapshot (B3).

The rest is DB-free. Every lyric in this file is invented.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi.config import settings
from domovoi.config_schema import EDITABLE_FIELDS
from domovoi.tests.auth_testkit import (
    HEADER,
    _db,  # noqa: F401 — fixture
    claim_admin,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "domovoi" / "main.py"
LYRICS_KT = (REPO / "android" / "app" / "src" / "main" / "java" / "com" / "domovoi"
             / "app" / "player" / "Lyrics.kt")


def test_the_library_group_lists_the_music_switches_in_order() -> None:
    labels = [f.label for f in EDITABLE_FIELDS if f.group == "Library"]
    wanted = [
        "Match names by sound",
        "Ask 'did you mean…?'",
        "Find songs by their words",
        "Look up other names on MusicBrainz",
        "Synced lyrics from LRCLIB",
        "Save lyrics as .lrc files",
    ]
    start = labels.index(wanted[0])
    assert labels[start:start + len(wanted)] == wanted


def test_only_the_lrclib_rows_need_the_internet() -> None:
    by_name = {f.name: f for f in EDITABLE_FIELDS}
    assert by_name["lyrics_search_enabled"].needs_internet is False
    assert by_name["lyrics_lrclib_enabled"].needs_internet is True
    assert by_name["lyrics_write_lrc"].needs_internet is True


def _lifespan_source() -> str:
    src = MAIN.read_text(encoding="utf-8")
    start = src.index("async def lifespan(")
    end = src.index("# ── Boot-time startup hooks", start)
    return src[start:end]


def test_the_worker_order_comment_matches_the_registrations() -> None:
    body = _lifespan_source()
    comment = re.search(
        r"# Registration order is the canonical start order.*?\n((?:\s*#.*\n)+?)\s*#\s*\n",
        body, re.S,
    )
    assert comment, "the worker-order comment is gone"
    said = re.findall(r"[a-z_]+", comment.group(1).replace("#", " "))
    # Every class registered in the lifespan, in order, and the module it
    # comes from (module-level or local imports, both in main.py).
    src = MAIN.read_text(encoding="utf-8")
    modules = dict(
        (cls, mod) for mod, cls in
        re.findall(r"from (domovoi\.workers\.\w+) import (\w+)", src)
    )
    registered = re.findall(r"WORKERS\.add_worker\((\w+)\(", body)
    names = [getattr(importlib.import_module(modules[cls]), cls).name for cls in registered]
    assert said == names
    assert names[-3:] == ["library_alias_fetch", "lyrics_scan", "lyrics_fetch"]
    assert names[names.index("memory_extractor") + 1] == "lyrics_index"


def _kotlin_fields(cls: str) -> set[str]:
    """The JSON names of a Kotlin @Serializable data class's constructor
    properties (``@SerialName`` when given, else the property name)."""
    src = LYRICS_KT.read_text(encoding="utf-8")
    m = re.search(r"data class " + cls + r"\(", src)
    assert m, f"no data class {cls} in Lyrics.kt"
    depth, i = 1, m.end()
    while depth:                      # the constructor's own closing paren
        depth += {"(": 1, ")": -1}.get(src[i], 0)
        i += 1
    params = src[m.end():i - 1]
    return {
        prop.group(2) or prop.group(3)
        for prop in re.finditer(
            r'(@Transient\s+)?(?:@SerialName\("([^"]+)"\)\s*)?va[lr]\s+(\w+)', params)
        if not prop.group(1)          # @Transient: never in the JSON
    }


def test_the_android_models_read_what_the_web_sends() -> None:
    from web.backend.schemas import LyricLine, LyricsDoc, RoomLyrics

    line = _kotlin_fields("LyricLine")
    assert line == set(LyricLine.model_fields)
    doc = _kotlin_fields("LyricsDoc")
    # The app has no use for updated_at; every other field is read.
    assert doc == set(LyricsDoc.model_fields) - {"updated_at"}
    room = _kotlin_fields("RoomLyrics")
    # read_at is informational (clients anchor on their own receipt time).
    assert room == set(RoomLyrics.model_fields) - {"read_at"}


# ─── End to end on the lane ───────────────────────────────────────────────

CHORUS = "the lantern hums beside the river door"
VERSE = "and every copper kettle sings at dawn"
OWNER_LRC = (
    "[ti:Lantern Song]\n[ar:The Example Band]\n"
    f"[00:12.40]{CHORUS}\n[00:16.85]{VERSE}\n[00:21.10]\n[00:21.30][01:05.00]{CHORUS}\n"
)


@requires_db
@pytest.mark.asyncio
async def test_an_owners_lrc_is_read_indexed_found_and_served(_db, db_session, tmp_path, monkeypatch) -> None:
    from domovoi.db.session import session_scope
    from domovoi.handlers.shared import library_match
    from domovoi.lyrics import status as lyrics_state
    from domovoi.lyrics.status import lyrics_status
    from domovoi.tests.lyrics_testkit import apply_v021, clear_lyrics
    from domovoi.tests.test_library_cover_art import silent_mp3
    from domovoi.workers import lyrics_index
    from domovoi.workers.lyrics_index import lyrics_index_status
    from domovoi.workers.lyrics_scan import LyricsScanner
    from web.backend.api import lyrics as lyrics_api
    from web.backend.domovoi_client import get_cached_snapshot, set_cached_snapshot

    await apply_v021()
    lyrics_state.reset_for_tests()
    music = tmp_path / "Music"
    music.mkdir()
    monkeypatch.setattr(settings, "music_dir", str(music))
    monkeypatch.setattr(settings, "lyrics_search_enabled", True)
    monkeypatch.setattr(lyrics_api, "_V021_READY", False)
    audio = music / "Lantern Song.mp3"
    audio.write_bytes(silent_mp3())
    (music / "Lantern Song.lrc").write_bytes(OWNER_LRC.encode("utf-8"))
    async with session_scope() as s:
        tid = (await s.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist, album, duration_sec) "
            "VALUES (:p, 'Lantern Song', 'The Example Band', 'Glass Harbor', 205) RETURNING id"
        ), {"p": str(audio)})).scalar_one()
    before = get_cached_snapshot()
    try:
        # B1: the scan reads the owner's file as source 1.
        assert (await LyricsScanner().tick())["read"] == 1
        # B2: the line index is built from what the view shows.
        assert (await lyrics_index.catch_up())["pending"] == 0
        async with session_scope() as s:
            lines = (await s.execute(text(
                "SELECT span, repeats FROM track_lyric_lines WHERE track_id = :id AND text = :t"),
                {"id": tid, "t": CHORUS})).all()
        assert [tuple(r) for r in lines] == [(1, 3)]          # sung three times
        # B2: the words find the song — exact plays, a word left out asks.
        library_match.reset_for_tests()
        async with session_scope() as s:
            sure = await library_match.resolve_lyrics(s, CHORUS, mode="explicit")
            near = await library_match.resolve_lyrics(
                s, "the lantern hums beside the door", mode="explicit")
            last = await library_match.resolve_lyrics(s, CHORUS, mode="fallback")
        assert sure.decision == "play" and tid in sure.best.track_ids
        assert near.decision == "ask" and tid in near.candidates[0].track_ids
        assert last.decision == "play" and last.reason == "lyrics_exact"
        # B3: the web serves it on the household tier; its status card reads
        # the core's REAL status dicts (B1's "lyrics", B2's "lyrics_index").
        set_cached_snapshot({"lyrics": lyrics_status(), "lyrics_index": lyrics_index_status()})
        async with web_client() as setup:
            await claim_admin(setup)
        token = await db_device_token()
        async with web_client(headers={HEADER: token}) as c:
            r = await c.get(f"/api/music/library/{tid}/lyrics")
            assert r.status_code == 200, r.text
            doc = r.json()
            assert (doc["status"], doc["source"], doc["source_label"]) == (
                "synced", "sidecar", "from Lantern Song.lrc")
            assert [(x["t"], x["text"]) for x in doc["lines"]] == [
                (12400, CHORUS), (16850, VERSE), (21100, ""), (21300, CHORUS), (65000, CHORUS)]
            assert doc["checking"] is False
            st = (await c.get("/api/music/lyrics/status")).json()
        assert st["tracks"] == 1 and st["with_lyrics"] == 1 and st["synced"] == 1
        assert st["by_source"] == {"sidecar": 1, "embedded": 0, "lrclib": 0}
        assert st["scan"]["state"] not in ("unknown", None)
        assert st["index"]["state"] not in ("unknown", None)
        assert st["index"]["pending"] == 0 and st["index"]["indexed"] == 1
        assert st["search_enabled"] is True
        assert st["lrclib"]["state"] == "off"            # the opt-in default
        async with web_client(headers={}) as anon:
            r = await anon.get(f"/api/music/library/{tid}/lyrics")
        assert r.status_code == 401 and CHORUS not in r.text
    finally:
        set_cached_snapshot(before)
        lyrics_state.reset_for_tests()
        await clear_lyrics()
