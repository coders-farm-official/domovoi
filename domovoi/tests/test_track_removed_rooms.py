"""A song taken out of the library leaves what is playing it (S2-18, the
owner's rule, Mac test sitting 2026-11-16).

A song deleted while it played stayed in the dashboard's Now Playing, and
the room played on. Pinned here, DB-free:

* a room's player: the song playing (or paused) with something after it
  goes on to that; with nothing after it, the room stops and its satellite
  is told; every copy leaves the queue; a room not playing it only loses
  the queued copies;
* the web's delete tells the core which file went (``also_file`` or not);
* playing a song whose row or file is gone says so in plain words;
* the dashboard's own player drops a gone song on a library change, and
  says what happened (static checks; the page's behaviour is the browser's).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from domovoi import music_track_removed as mtr

REPO = Path(__file__).resolve().parents[2]


class FakeMPD:
    def __init__(self, files, *, current=None, state="play"):
        self.queue = [{"id": 100 + i, "pos": i, "file": f} for i, f in enumerate(files)]
        self.current = current
        self.state = state
        self.calls: list[tuple] = []

    async def queue_list(self):
        return [dict(e, pos=i) for i, e in enumerate(self.queue)]

    async def playback_snapshot(self):
        if self.state == "stop" or self.current is None:
            return {"state": "stop", "songid": None, "file": None, "pos": None}
        e = self.queue[self.current]
        return {"state": self.state, "songid": e["id"], "file": e["file"], "pos": self.current}

    async def play_id(self, song_id, *, paused=False):
        self.calls.append(("play_id", song_id, paused))
        return True

    async def stop(self):
        self.calls.append(("stop",))
        self.state = "stop"

    async def queue_remove(self, ids):
        self.calls.append(("remove", sorted(ids)))
        self.queue = [e for e in self.queue if e["id"] not in ids]
        return list(ids)


async def test_a_playing_song_with_one_after_it_goes_on_to_that():
    mpd = FakeMPD(["a.mp3", "gone.mp3", "c.mp3", "gone.mp3"], current=1)
    out = await mtr.drop_from_room("office", mpd, {"gone.mp3"})
    assert out == {"room_id": "office", "action": "next", "removed": 2}
    assert mpd.calls[0] == ("play_id", 102, False)
    assert mpd.calls[1] == ("remove", [101, 103])


async def test_the_only_song_stops_the_room():
    mpd = FakeMPD(["gone.mp3"], current=0)
    out = await mtr.drop_from_room("office", mpd, {"gone.mp3"})
    assert out["action"] == "stop"
    assert mpd.calls == [("stop",), ("remove", [100])]


async def test_the_last_song_in_the_queue_stops_the_room_not_the_one_before():
    mpd = FakeMPD(["a.mp3", "gone.mp3"], current=1)
    out = await mtr.drop_from_room("office", mpd, {"gone.mp3"})
    assert out["action"] == "stop" and ("play_id", 100, False) not in mpd.calls


async def test_a_paused_room_stays_paused_on_the_next_song():
    mpd = FakeMPD(["gone.mp3", "b.mp3"], current=0, state="pause")
    await mtr.drop_from_room("office", mpd, {"gone.mp3"})
    assert mpd.calls[0] == ("play_id", 101, True)


async def test_a_queued_copy_only_leaves_the_queue():
    mpd = FakeMPD(["a.mp3", "gone.mp3"], current=0)
    out = await mtr.drop_from_room("office", mpd, {"gone.mp3"})
    assert out["action"] is None and mpd.calls == [("remove", [101])]


async def test_a_room_without_it_is_left_alone():
    mpd = FakeMPD(["a.mp3"], current=0)
    assert await mtr.drop_from_room("office", mpd, {"gone.mp3"}) is None
    assert mpd.calls == []


async def test_every_room_and_the_satellite_is_told_to_stop(tmp_path, monkeypatch):
    from domovoi.clients import mpd as mpd_mod
    from domovoi import room_music

    music = tmp_path / "music"
    monkeypatch.setattr(mtr.settings, "music_dir", str(music))
    office = FakeMPD(["Album/gone.mp3"], current=0)
    kitchen = FakeMPD(["Album/gone.mp3", "Album/next.mp3"], current=0)
    broken = FakeMPD([])

    async def boom():
        raise OSError("refused")

    broken.queue_list = boom
    monkeypatch.setattr(mpd_mod, "iter_mpd_clients",
                        lambda: [("office", office), ("broken", broken), ("kitchen", kitchen)])
    told: list[tuple] = []

    async def dispatch(app, response, room_id, **kw):
        told.append((room_id, response.music_action))

    monkeypatch.setattr(room_music, "dispatch_music", dispatch)
    done = await mtr.drop_removed_tracks(object(), [str(music / "Album" / "gone.mp3"), "/elsewhere/x.mp3"])
    assert [(d["room_id"], d["action"]) for d in done] == [("office", "stop"), ("kitchen", "next")]
    assert told == [("office", "stop")]


def test_a_path_outside_the_music_folder_is_no_uri(tmp_path, monkeypatch):
    monkeypatch.setattr(mtr.settings, "music_dir", str(tmp_path))
    assert mtr.mpd_uri_for_library_path(str(tmp_path / "a" / "b.mp3")) == "a/b.mp3"
    assert mtr.mpd_uri_for_library_path(str(tmp_path.parent / "x.mp3")) is None
    assert mtr.mpd_uri_for_library_path("") is None


# ── the web's delete tells the core ───────────────────────────────────


class _Result:
    def __init__(self, row=None, rowcount=0):
        self._row, self.rowcount = row, rowcount

    def first(self):
        return self._row


class _Session:
    def __init__(self, path):
        self.path, self.sql = path, []

    async def execute(self, clause, params=None):
        sql = " ".join(str(clause).split())
        self.sql.append(sql)
        if sql.startswith("SELECT file_path"):
            return _Result((self.path,) if self.path else None)
        if sql.startswith("DELETE FROM library_tracks"):
            return _Result(rowcount=1 if self.path else 0)
        return _Result()


@pytest.fixture
def web_delete(monkeypatch):
    from web.backend.api import music as music_api

    sent: list[tuple] = []

    async def post_admin(path, body=None, **kw):
        sent.append((path, body))
        return 200, {"rooms": []}

    monkeypatch.setattr(music_api, "post_admin", post_admin)
    monkeypatch.setattr(music_api, "auth_forward_headers", lambda request: {})

    def run(path):
        session = _Session(path)

        @asynccontextmanager
        async def scope():
            yield session

        monkeypatch.setattr(music_api, "session_scope", scope)
        return music_api, session

    return run, sent


async def test_deleting_a_library_row_tells_the_rooms(web_delete):
    run, sent = web_delete
    music_api, session = run("/music/Album/gone.mp3")
    await music_api.delete_track(7, request=None, also_file=False)
    assert sent == [("/v1/admin/music/tracks-removed", {"file_paths": ["/music/Album/gone.mp3"]})]
    assert any("pg_notify('library_changed'" in s for s in session.sql)


async def test_a_missing_row_tells_nobody(web_delete):
    from fastapi import HTTPException

    run, sent = web_delete
    music_api, _ = run(None)
    with pytest.raises(HTTPException) as e:
        await music_api.delete_track(7, request=None, also_file=False)
    assert e.value.status_code == 404 and sent == []


async def test_a_core_that_doesnt_answer_doesnt_undo_the_delete(web_delete, monkeypatch):
    run, _ = web_delete
    music_api, _ = run("/music/x.mp3")

    async def down(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr(music_api, "post_admin", down)
    await music_api.delete_track(7, request=None, also_file=False)   # no raise


# ── playing a song that is gone ───────────────────────────────────────


def test_a_file_known_to_be_gone_is_not_played(tmp_path):
    from domovoi.main import _library_file_present

    (tmp_path / "here.mp3").write_bytes(b"x")
    assert _library_file_present(str(tmp_path / "here.mp3")) is True
    assert _library_file_present(str(tmp_path / "gone.mp3")) is False
    # A folder that isn't there (an unplugged drive) is not "deleted".
    assert _library_file_present(str(tmp_path / "no-drive" / "a.mp3")) is True


def test_the_play_refusals_are_plain_words():
    src = (REPO / "domovoi" / "main.py").read_text(encoding="utf-8")
    assert "That song isn't in the library any more." in src
    assert "That song's file isn't on this server any more." in src
    assert "not found in library_tracks" not in src


def test_the_dashboard_player_drops_a_gone_song():
    src = (REPO / "web" / "static" / "player.jsx").read_text(encoding="utf-8")
    assert "ev.type !== 'library.indexer.changed'" in src
    assert "dropGoneRef.current.check([item.trackId])" in src, "a failed load is checked too"
    assert "goneNotice" in src and "isn't in the library any more" in src
    assert "if (browser) stop();" in src
