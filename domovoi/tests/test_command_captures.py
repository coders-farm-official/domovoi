"""Opt-in command recordings for end-of-turn tuning (design notes 2026-09-28).

Owner decision: collect real household command audio OPT-IN PER ROOM, only
an admin turns it on, every recording is deleted after 14 days. What is
pinned here, DB-free (the database half is test_command_captures_api.py):

* nothing is kept unless the room is opted in — and even then only a
  wake_word / followup command that reached the router, never a wake-word
  training clip, a chat-mode turn, a barge-in, a push-to-talk or a turn
  captured during a drop-in call;
* what a recording is: a 16 kHz mono WAV of the capture exactly as it
  arrived, and a sidecar with the room, time, trigger, duration, the
  satellite's end reason and frame counts, the room's listen settings, the
  final transcript, the matched handler and path, the stage timings — and
  no person, session or embedding;
* the opt-in is asked again after the write, so an opt-out that raced it
  still wins;
* retention (14 days, clamped), the disk cap (oldest first, across rooms),
  the sweep of rooms no longer opted in, and the pruner worker;
* containment: room and capture ids never name a path outside the
  directory, a symlinked room directory is refused, deletion never touches
  a file this module did not name, and the directory is refused outright
  when it sits in or around one something serves;
* the Files surface can't reach it through a library rooted above the
  config dir.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import types
import wave
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from domovoi import command_captures as cc
from domovoi import streaming
from domovoi.config import Settings, settings
from domovoi.models import Response
from domovoi.streaming import StreamSession
from domovoi.workers.command_capture_pruner import CommandCapturePruner

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "domovoi" / "db" / "migrations"

SPOKEN = "set a timer for ten minutes for the pasta"
PCM = (b"\x10\x00\xf0\xff" * 8000)  # 1 s of 16 kHz int16


def _root() -> Path:
    return Path(settings.command_captures_dir)


def _sidecar(room_id: str = "kitchen", when: datetime | None = None, **over):
    meta = {
        "captured_at": when or datetime.now(timezone.utc),
        "end_reason": "vad_silence_after_speech",
        "capture": {"frames": 90, "voiced_frames": 40},
        "in_call": False,
    }
    side = cc.build_sidecar(
        room_id=room_id, pcm_len=len(PCM), trigger="wake_word", meta=meta,
        listen={"silence_timeout": 1.2}, transcript=SPOKEN, matched_handler="timer",
        matched_path="fast", interrupted=False, timings={"stt_ms": 640},
    )
    side.update(over)
    return side


def _write(room_id: str = "kitchen", when: datetime | None = None, pcm: bytes = PCM) -> str:
    return cc.write_capture(room_id, pcm, _sidecar(room_id, when))


# ─── defaults: off, 14 days, a private directory ─────────────────────────


def test_the_defaults_are_off_fourteen_days_and_a_private_dir() -> None:
    d = Settings()
    assert Path(d.command_captures_dir) == Path.home() / ".domovoi" / "captures"
    assert d.command_capture_retention_days == 14
    assert d.command_capture_max_mb == 500
    # There is no setting that turns recording on: only a V016 row does.
    assert not any("enabled" in name for name in Settings.model_fields if name.startswith("command_capture"))


def test_v016_is_a_table_whose_rows_are_the_opt_in() -> None:
    v016 = MIGRATIONS / "V016__command_capture_rooms.sql"
    assert v016.is_file()
    sql = "\n".join(
        line for line in v016.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    ).strip()
    assert sql.startswith("CREATE TABLE IF NOT EXISTS command_capture_rooms (")
    assert "room_id    TEXT PRIMARY KEY" in sql
    assert b"\r\n" not in v016.read_bytes()


def test_retention_is_clamped_to_fourteen_days(monkeypatch) -> None:
    monkeypatch.setattr(settings, "command_capture_retention_days", 30)
    assert cc.retention_days() == 14
    monkeypatch.setattr(settings, "command_capture_retention_days", 0)
    assert cc.retention_days() == 1
    monkeypatch.setattr(settings, "command_capture_retention_days", 3)
    assert cc.retention_days() == 3


# ─── which turns may be kept ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("trigger", "in_call", "ok"),
    [
        ("wake_word", False, True),
        ("followup", False, True),
        ("wake_word", True, False),     # a drop-in call: the other room is on the speaker
        ("wake_clip", False, False),    # wake-word training clip
        ("chat", False, False),         # chat mode
        ("barge_in", False, False),     # carries the reply's own audio
        ("push_to_talk", False, False), # a button ended it, not silence
        (None, False, False),
    ],
)
def test_only_command_turns_are_candidates(trigger, in_call, ok) -> None:
    assert cc.eligible(trigger, in_call=in_call) is ok


def test_the_utterance_end_fields_are_cleaned() -> None:
    meta = cc.capture_meta({
        "exit_reason": "vad_silence_after_speech", "frames": 90, "voiced_frames": 41,
        "trailing_silent_frames": 40, "silence_limit_frames": 40, "greeting_played": True,
    }, in_call=False)
    assert meta["end_reason"] == "vad_silence_after_speech"
    assert meta["capture"] == {
        "frames": 90, "voiced_frames": 41,
        "trailing_silent_frames": 40, "silence_limit_frames": 40,
    }
    junk = cc.capture_meta({
        "exit_reason": "../../etc", "frames": -1, "voiced_frames": True,
        "trailing_silent_frames": "40", "silence_limit_frames": 10**9,
    }, in_call=True)
    assert junk["end_reason"] is None and junk["capture"] is None and junk["in_call"] is True
    # An old satellite: none of the fields.
    old = cc.capture_meta({"greeting_played": False}, in_call=False)
    assert old["end_reason"] is None and old["capture"] is None


def test_the_sidecar_carries_the_turn_and_nobody_s_identity() -> None:
    when = datetime(2026, 9, 28, 10, 15, 30, 123000, tzinfo=timezone.utc)
    side = _sidecar(when=when)
    assert set(side) == {
        "version", "id", "room_id", "captured_at", "trigger", "duration_ms",
        "sample_rate", "end_reason", "capture", "listen", "transcript",
        "matched_handler", "matched_path", "interrupted", "timings", "label",
        "labeled_at",
    }
    assert side["id"].startswith("20260928T101530123Z-")
    assert cc.capture_time(side["id"]) == when
    assert side["duration_ms"] == 1000 and side["sample_rate"] == 16_000
    assert side["transcript"] == SPOKEN
    assert side["label"] is None
    for forbidden in ("person", "person_id", "session", "session_id", "embedding", "speaker"):
        assert forbidden not in json.dumps(list(side))


def test_listen_settings_are_the_listen_keys_only() -> None:
    cfg = {"listen.silence_timeout": 0.8, "listen.vad_aggressiveness": 3,
           "wifi.poll_sec": 60, "listen.bad": {"x": 1}}
    assert cc.listen_settings(cfg) == {"silence_timeout": 0.8, "vad_aggressiveness": 3}
    assert cc.listen_settings(None) == {}


# ─── containment ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("room_id", [
    "kitchen", "living_room", "garage-2",
])
def test_a_boring_room_id_is_its_own_directory(room_id) -> None:
    assert cc.room_dir_name(room_id) == room_id
    assert cc.room_dir(room_id).parent == _root().resolve()


@pytest.mark.parametrize("room_id", [
    "..", ".", "../etc", "a/b", "a\\b", "C:", "Living Room", "con", "NUL", "",
    "x" * 70, "_underscore", "\u00e9t\u00e9",
])
def test_any_other_room_id_gets_a_hashed_directory_inside_the_root(room_id) -> None:
    name = cc.room_dir_name(room_id)
    assert name.startswith("_") and len(name) == 17
    assert cc.room_dir(room_id).parent == _root().resolve()


@pytest.mark.parametrize("capture_id", [
    "..", "../x", "x/../../y", "/etc/passwd", "C:\\x", "20260928T101530123Z-3f9a1c2",
    "20260928T101530123Z-3f9a1c2b.wav", "20260928T101530123Z-3F9A1C2B", "",
])
def test_anything_but_a_capture_id_names_no_path(capture_id) -> None:
    assert cc.capture_paths("kitchen", capture_id) is None


def test_a_room_directory_that_is_a_symlink_out_is_refused(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    _root().mkdir(parents=True)
    try:
        os.symlink(outside, _root() / "kitchen", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges on this host")
    with pytest.raises(ValueError):
        cc.room_dir("kitchen")
    assert cc.capture_paths("kitchen", "20260928T101530123Z-3f9a1c2b") is None
    with pytest.raises(ValueError):
        _write("kitchen")
    assert list(outside.iterdir()) == []


def test_the_default_directory_is_safe_and_a_served_one_is_refused(monkeypatch, tmp_path) -> None:
    assert cc.root_problem() is None
    music = tmp_path / "Music"
    monkeypatch.setattr(settings, "music_dir", str(music))
    for bad in (music / "captures", music):
        monkeypatch.setattr(settings, "command_captures_dir", str(bad))
        assert "music_dir" in (cc.root_problem() or "")
    # A directory AROUND a served one is just as bad: <room>/ could BE it.
    monkeypatch.setattr(settings, "command_captures_dir", str(tmp_path))
    assert "music_dir" in (cc.root_problem() or "")
    monkeypatch.setattr(settings, "command_captures_dir", str(Path.home()))
    assert cc.root_problem()
    monkeypatch.setattr(settings, "command_captures_dir", str(Path.home() / ".domovoi"))
    assert cc.root_problem()
    monkeypatch.setattr(settings, "command_captures_dir", str(REPO_ROOT / "web" / "static" / "caps"))
    assert "repo_dir" in (cc.root_problem() or "")
    monkeypatch.setattr(settings, "command_captures_dir", Path(tmp_path.anchor).as_posix())
    assert cc.root_problem()


def test_an_unsafe_directory_is_never_written_or_swept(monkeypatch, tmp_path) -> None:
    music = tmp_path / "Music"
    (music / "kitchen").mkdir(parents=True)
    song = music / "kitchen" / "20260101T000000000Z-0000abcd.wav"
    song.write_bytes(b"a real file that happens to look like a capture")
    monkeypatch.setattr(settings, "music_dir", str(music))
    monkeypatch.setattr(settings, "command_captures_dir", str(music))
    with pytest.raises(cc.CaptureDirRefused):
        _write("kitchen")
    assert cc.purge_expired() == 0 and cc.enforce_cap(0) == 0 and cc.purge_rooms_not_in([]) == 0
    assert cc.list_captures() == []
    assert song.exists()


def test_a_directory_that_turned_unsafe_still_forgets_what_it_kept(monkeypatch, tmp_path) -> None:
    """Recordings kept while the directory was fine, then a media library
    configured around it: recording stops, but the 14 days still hold for
    the recordings provably ours — and nothing else there is touched."""
    now = datetime.now(timezone.utc)
    caps = tmp_path / "srv" / "caps"
    monkeypatch.setattr(settings, "command_captures_dir", str(caps))
    old = _write("kitchen", when=now - timedelta(days=15))
    fresh = _write("kitchen", when=now - timedelta(days=1))
    lookalike = cc.room_dir("kitchen") / "20260101T000000000Z-0000abcd.wav"
    lookalike.write_bytes(b"somebody's song")
    os.utime(lookalike, (1_000_000_000, 1_000_000_000))
    monkeypatch.setattr(settings, "music_dir", str(tmp_path / "srv"))
    assert "music_dir" in (cc.root_problem() or "")
    assert cc.purge_expired(now) == 1
    assert not cc.capture_paths("kitchen", old)[0].exists()
    assert not cc.capture_paths("kitchen", old)[1].exists()
    assert cc.capture_paths("kitchen", fresh)[0].exists()
    assert lookalike.exists(), "no sidecar vouches for it, so it is not ours"


@pytest.mark.parametrize("relative", ["captures", "./captures", ""])
def test_a_relative_directory_is_refused(monkeypatch, relative) -> None:
    """The core writes and the dashboard deletes, each from its own working
    directory: a relative setting would make an opt-out delete nothing."""
    monkeypatch.setattr(settings, "command_captures_dir", relative)
    assert "absolute" in (cc.root_problem() or "")
    with pytest.raises(cc.CaptureDirRefused):
        _write("kitchen")


def test_the_default_directory_is_inside_no_files_library() -> None:
    """The Files surface only ever serves its libraries, and the recordings
    are in none of them."""
    from web.backend.api import files_security as fs

    root = Path(Settings().command_captures_dir).resolve()
    for _lib, _label, _icon, attr, *_rest in fs.CORE_LIBRARIES:
        lib = Path(getattr(Settings(), attr)).expanduser().resolve()
        assert not str(root).startswith(str(lib) + os.sep), attr
    assert fs._is_sensitive(root, fs._allowed_under_config())


def test_a_library_rooted_above_the_config_dir_cannot_reach_into_it(monkeypatch, tmp_path) -> None:
    """A plugin that declares the home directory as a media library used to
    serve ``.domovoi/<anything>`` to a typed path; now nothing under the
    config dir but its media subdirs is reachable."""
    from web.backend.api import files_security as fs

    home = (tmp_path / "home").resolve()
    config = home / ".domovoi"
    (config / "captures" / "kitchen").mkdir(parents=True)
    (config / "audiobooks").mkdir()
    monkeypatch.setattr(fs, "CONFIG_DIR", config)
    monkeypatch.setattr(settings, "audiobooks_dir", str(config / "audiobooks"))
    with pytest.raises(HTTPException) as e:
        fs.safe_join(home, ".domovoi/captures/kitchen/20260928T101530123Z-3f9a1c2b.wav")
    assert e.value.status_code == 404
    with pytest.raises(HTTPException):
        fs.safe_join(home, ".domovoi/device-token.txt")
    assert fs.safe_join(home, ".domovoi/audiobooks/a.m4b") == config / "audiobooks" / "a.m4b"
    assert fs.safe_join(home, "Music/a.mp3") == home / "Music" / "a.mp3"


def test_a_moved_directory_is_still_no_files_download(monkeypatch, tmp_path) -> None:
    """COMMAND_CAPTURES_DIR moved outside the config dir — onto a drive the
    Files tab lists, or under a plugin's library root, neither of which
    root_problem can know about — is still refused by every Files path."""
    from web.backend.api import files_security as fs

    drive = (tmp_path / "usb").resolve()
    caps = drive / "domovoi-captures"
    (caps / "kitchen").mkdir(parents=True)
    (drive / "films").mkdir()
    monkeypatch.setattr(settings, "command_captures_dir", str(caps))
    cid = "20260928T101530123Z-3f9a1c2b"
    for rel in (f"domovoi-captures/kitchen/{cid}.wav", "domovoi-captures/kitchen",
                "domovoi-captures"):
        with pytest.raises(HTTPException) as e:
            fs.safe_join(drive, rel)
        assert e.value.status_code == 404, rel
    assert fs.safe_join(drive, "films/a.mkv") == drive / "films" / "a.mkv"
    # A library rooted INSIDE the recordings lists nothing at all.
    with pytest.raises(HTTPException):
        fs.safe_join(caps / "kitchen", "")
    with pytest.raises(HTTPException):
        fs.safe_join(caps / "kitchen", f"{cid}.json")


def test_the_tree_walks_leave_the_recordings_and_the_config_dir_out(monkeypatch, tmp_path) -> None:
    """safe_join guards a path somebody typed; a directory zip, an import
    copy and a listing walk the children of a folder it allowed. None of
    them may carry ~/.domovoi (a library rooted above it) or the recordings
    (wherever they were moved)."""
    import zipfile

    from web.backend.api import files as files_api
    from web.backend.api import files_security as fs

    home = (tmp_path / "home").resolve()
    config = home / ".domovoi"
    (config / "wake_clips" / "hey").mkdir(parents=True)
    (config / "wake_clips" / "hey" / "clip_001.wav").write_bytes(b"voice")
    (config / "device-token.txt").write_text("secret", encoding="utf-8")
    moved = home / "usb" / "caps"
    (moved / "kitchen").mkdir(parents=True)
    (moved / "kitchen" / "20260928T101530123Z-3f9a1c2b.wav").write_bytes(b"speech")
    (home / "Music").mkdir()
    (home / "Music" / "a.mp3").write_bytes(b"song")
    monkeypatch.setattr(fs, "CONFIG_DIR", config)
    monkeypatch.setattr(settings, "command_captures_dir", str(moved))

    resp = files_api._zip_directory(home, home)
    names = set(zipfile.ZipFile(io.BytesIO(resp.body)).namelist())
    assert names == {"Music/a.mp3"}, names

    dest = tmp_path / "imported"
    dest.mkdir()
    skipped: list[str] = []
    files_api._confined_copytree(home, dest, home, [5000], [10**9], skipped)
    copied = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert copied == {"Music/a.mp3"}, copied
    assert ".domovoi" in skipped and "caps" in skipped

    private = fs.private_path_check()
    assert private(config / "device-token.txt") and private(moved / "kitchen")
    assert not private(home / "Music" / "a.mp3")


# ─── the files ───────────────────────────────────────────────────────────


def test_a_recording_is_the_capture_as_a_wav_plus_its_sidecar() -> None:
    cid = _write()
    wav, side = cc.capture_paths("kitchen", cid)
    with wave.open(str(wav), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16_000)
        assert w.readframes(w.getnframes()) == PCM
    data = json.loads(side.read_text(encoding="utf-8"))
    assert data["id"] == cid and data["room_id"] == "kitchen"
    assert data["transcript"] == SPOKEN
    if sys.platform != "win32":
        assert wav.stat().st_mode & 0o777 == 0o600
        assert side.stat().st_mode & 0o777 == 0o600
        assert wav.parent.stat().st_mode & 0o777 == 0o700
        assert _root().stat().st_mode & 0o777 == 0o700
    listed = cc.list_captures()
    assert [c["id"] for c in listed] == [cid]
    assert listed[0]["bytes"] == wav.stat().st_size


def test_listing_skips_incomplete_expired_and_misfiled_recordings() -> None:
    good = _write()
    half = _write()
    cc.capture_paths("kitchen", half)[0].unlink()          # audio gone
    old = _write(when=datetime.now(timezone.utc) - timedelta(days=15))
    misfiled = _sidecar("garage")
    cc.write_capture("kitchen", PCM, misfiled)              # sidecar says garage
    garage_side = cc.capture_paths("kitchen", misfiled["id"])[1]
    assert garage_side.exists()
    ids = [c["id"] for c in cc.list_captures()]
    assert ids == [good]
    assert old not in ids


def test_labels_are_the_three_verdicts_or_none() -> None:
    cid = _write()
    for label in ("cut_off", "fine", "waited_too_long", None):
        side = cc.set_label("kitchen", cid, label)
        assert side["label"] == label
        assert (side["labeled_at"] is None) == (label is None)
        on_disk = json.loads(cc.capture_paths("kitchen", cid)[1].read_text(encoding="utf-8"))
        assert on_disk["label"] == label
    with pytest.raises(ValueError):
        cc.set_label("kitchen", cid, "meh")
    assert cc.set_label("kitchen", "20260101T000000000Z-00000000", "fine") is None
    assert cc.set_label("kitchen", "../x", "fine") is None


def test_delete_one_and_delete_a_room_leave_foreign_files_alone() -> None:
    a, b = _write(), _write()
    other = _write("garage")
    foreign = cc.room_dir("kitchen") / "notes.txt"
    foreign.write_text("not ours", encoding="utf-8")
    assert cc.delete_capture("kitchen", a) is True
    assert cc.delete_capture("kitchen", a) is False
    assert cc.delete_room("kitchen") == 1
    assert foreign.exists(), "only capture-shaped names are ever deleted"
    assert cc.capture_paths("kitchen", b)[0].exists() is False
    assert [c["id"] for c in cc.list_captures()] == [other]


def test_retention_deletes_what_is_older_than_fourteen_days() -> None:
    now = datetime.now(timezone.utc)
    fresh = _write(when=now - timedelta(days=13, hours=23))
    stale = _write(when=now - timedelta(days=14, minutes=1))
    other_room = _write("garage", when=now - timedelta(days=20))
    assert cc.purge_expired(now) == 2
    assert [c["id"] for c in cc.list_captures(now=now)] == [fresh]
    for rid, cid in (("kitchen", stale), ("garage", other_room)):
        wav, side = cc.capture_paths(rid, cid)
        assert not wav.exists() and not side.exists()
    assert not cc.room_dir("garage").exists(), "an emptied room directory goes too"


def test_retention_clears_orphans_and_stale_half_writes(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    orphan = _write(when=now - timedelta(hours=2))
    cc.capture_paths("kitchen", orphan)[0].unlink()
    young_orphan = _write(when=now)
    cc.capture_paths("kitchen", young_orphan)[1].unlink()
    tmp = cc.room_dir("kitchen") / "20260101T000000000Z-0000abcd.wav.tmp"
    tmp.write_bytes(b"x")
    old = 1_000_000_000
    os.utime(tmp, (old, old))
    cc.purge_expired(now)
    assert not cc.capture_paths("kitchen", orphan)[1].exists()
    assert cc.capture_paths("kitchen", young_orphan)[0].exists(), "a write in progress is left alone"
    assert not tmp.exists()


def test_the_disk_cap_deletes_the_oldest_first_across_rooms() -> None:
    now = datetime.now(timezone.utc)
    ids = [
        ("garage", _write("garage", when=now - timedelta(minutes=5))),
        ("kitchen", _write("kitchen", when=now - timedelta(minutes=4))),
        ("garage", _write("garage", when=now - timedelta(minutes=3))),
        ("kitchen", _write("kitchen", when=now - timedelta(minutes=2))),
    ]
    one = sum(p.stat().st_size for p in cc.capture_paths(*ids[0]))
    assert cc.enforce_cap(one * 2 + 10) == 2
    kept = {c["id"] for c in cc.list_captures(now=now)}
    assert kept == {ids[2][1], ids[3][1]}
    assert cc.enforce_cap(10**9) == 0


def test_the_cap_is_enforced_on_every_write(monkeypatch) -> None:
    monkeypatch.setattr(settings, "command_capture_max_mb", 1)
    big = b"\x00\x01" * (400 * 1024)            # 800 KB
    now = datetime.now(timezone.utc)
    first = cc._write_and_cap("kitchen", big, _sidecar(when=now - timedelta(seconds=2)))
    second = cc._write_and_cap("kitchen", big, _sidecar(when=now))
    assert [c["id"] for c in cc.list_captures()] == [second]
    assert first != second


def test_retention_also_runs_on_the_write_path(monkeypatch) -> None:
    """The pruner is a worker, and workers are skipped under USE_STUBS; a
    core that records must forget on its own too — at most once per
    pruner interval, so a busy room doesn't rescan the disk every turn."""
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(cc, "_last_write_purge", None)
    stale = _write(when=now - timedelta(days=15))
    cc._write_and_cap("kitchen", PCM, _sidecar(when=now))
    assert not cc.capture_paths("kitchen", stale)[0].exists()
    stale_again = _write(when=now - timedelta(days=15))
    cc._write_and_cap("kitchen", PCM, _sidecar(when=now))
    assert cc.capture_paths("kitchen", stale_again)[0].exists(), "throttled to the pruner interval"
    monkeypatch.setattr(cc, "_last_write_purge", None)
    cc._write_and_cap("kitchen", PCM, _sidecar(when=now))
    assert not cc.capture_paths("kitchen", stale_again)[0].exists()


def test_rooms_no_longer_opted_in_are_swept() -> None:
    k = _write("kitchen")
    _write("garage")
    _write("Living Room")
    assert cc.purge_rooms_not_in(["kitchen"]) == 2
    assert [c["id"] for c in cc.list_captures()] == [k]
    assert cc.purge_rooms_not_in(["kitchen", "Living Room"]) == 0


def test_usage_counts_recordings_and_bytes_per_room() -> None:
    _write("kitchen")
    _write("kitchen")
    _write("garage")
    u = cc.usage()
    assert u["count"] == 3
    assert u["by_dir"]["kitchen"]["count"] == 2
    assert u["bytes"] == sum(v["bytes"] for v in u["by_dir"].values())
    assert u["cap_bytes"] == 500 * 1024 * 1024 and u["retention_days"] == 14
    # Past its 14 days but not swept yet: still on disk, no longer listed.
    _write("garage", when=datetime.now(timezone.utc) - timedelta(days=15))
    later = cc.usage()
    assert later["count"] == 3 and later["by_dir"]["garage"]["count"] == 1
    assert later["bytes"] > u["bytes"]


# ─── keep(): the core's entry point ──────────────────────────────────────


def _opt_in(monkeypatch, answers):
    """room_opted_in answers from a list (the last one repeats)."""
    seen: list[str] = []

    async def fake(room_id: str) -> bool:
        seen.append(room_id)
        return answers[min(len(seen), len(answers)) - 1]

    monkeypatch.setattr(cc, "room_opted_in", fake)
    return seen


@pytest.mark.asyncio
async def test_keep_writes_only_for_an_opted_in_room(monkeypatch) -> None:
    _opt_in(monkeypatch, [False])
    assert await cc.keep("kitchen", PCM, _sidecar()) is None
    assert not _root().exists()
    _opt_in(monkeypatch, [True])
    cid = await cc.keep("kitchen", PCM, _sidecar())
    assert [c["id"] for c in cc.list_captures()] == [cid]


@pytest.mark.asyncio
async def test_an_opt_out_that_races_the_write_wins(monkeypatch) -> None:
    seen = _opt_in(monkeypatch, [True, False])
    assert await cc.keep("kitchen", PCM, _sidecar()) is None
    assert seen == ["kitchen", "kitchen"]
    assert cc.list_captures() == []
    assert not any(p.suffix == ".wav" for p in _root().rglob("*"))


@pytest.mark.asyncio
async def test_the_opt_in_check_fails_closed(monkeypatch) -> None:
    @asynccontextmanager
    async def broken():
        raise RuntimeError("database away")
        yield  # pragma: no cover

    monkeypatch.setattr(cc, "session_scope", broken)
    assert await cc.room_opted_in("kitchen") is False
    assert await cc.keep("kitchen", PCM, _sidecar()) is None


@pytest.mark.asyncio
async def test_the_pruner_skips_the_room_sweep_when_the_list_is_unreadable(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    _write("garage")
    _write("kitchen", when=now - timedelta(days=20))

    async def unreadable(session=None):
        raise RuntimeError("database away")

    monkeypatch.setattr(cc, "opted_in_rooms", unreadable)
    result = await CommandCapturePruner().tick()
    assert result == {"expired": 1, "opted_out": 0, "over_cap": 0}
    assert [c["room_id"] for c in cc.list_captures()] == ["garage"]

    async def nobody(session=None):
        return {}

    monkeypatch.setattr(cc, "opted_in_rooms", nobody)
    result = await CommandCapturePruner().tick()
    assert result["opted_out"] == 1
    assert cc.list_captures() == []


def test_the_pruner_is_registered_on_the_core() -> None:
    src = (REPO_ROOT / "domovoi" / "main.py").read_text(encoding="utf-8")
    assert 'WORKERS.add_worker(CommandCapturePruner(), owner="core")' in src
    assert CommandCapturePruner.interval_setting == "command_capture_pruner_interval_sec"
    assert Settings().command_capture_pruner_interval_sec == 3600.0


# ─── through the streaming layer ─────────────────────────────────────────


class _FakeWS:
    def __init__(self) -> None:
        self.app = types.SimpleNamespace(
            state=types.SimpleNamespace(
                satellite_voice={}, wifi_status={}, satellite_volume={},
                satellite_config={"kitchen": {"listen.silence_timeout": 0.8,
                                              "listen.vad_aggressiveness": 3}},
                greeting_phrases=[], resumable_music={}, current_playlist={},
                active_sessions={}, probe=types.SimpleNamespace(online=True),
            )
        )
        self.sent_text: list[dict] = []
        self.sent_bytes: list[bytes] = []

    async def send_text(self, data: str) -> None:
        self.sent_text.append(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)


def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16_000)
        w.writeframes(pcm)
    return buf.getvalue()


@pytest.fixture
def turn(monkeypatch):
    """A voice turn with every stage faked and the opt-in answered by the
    test (``state["opted_in"]``)."""
    state = {"opted_in": True, "transcript": SPOKEN, "routed": 0}

    class _Whisper:
        async def transcribe(self, pcm: bytes) -> str:
            return state["transcript"]

    class _TTS:
        async def synthesize(self, text, *, engine=None, voice=None) -> bytes:
            return _wav(b"\x01\x02" * 200)

    async def _identify(pcm):
        return None

    async def _route(intent, ctx, session):
        state["routed"] += 1
        return Response(text="Timer set for 10 minutes.", matched_handler="timer",
                        matched_path="fast", online=True)

    @asynccontextmanager
    async def _no_db():
        yield None

    async def _merge(s, row_id, patch):
        return None

    async def _opted(room_id: str) -> bool:
        return state["opted_in"]

    monkeypatch.setattr(streaming, "route", _route)
    monkeypatch.setattr(streaming, "merge_post_route", _merge)
    monkeypatch.setattr(streaming, "session_scope", _no_db)
    monkeypatch.setattr(streaming, "get_whisper_client", lambda: _Whisper())
    monkeypatch.setattr("domovoi.voice_identifier.identify", _identify)
    monkeypatch.setattr(streaming, "get_tts_client", lambda: _TTS())
    monkeypatch.setattr(cc, "room_opted_in", _opted)
    return state


async def _say(sess: StreamSession, trigger: str, end: dict | None = None) -> None:
    """utterance_start → PCM → utterance_end, as the satellite frames it,
    then wait for the turn to finish."""
    await sess._on_control({"type": "utterance_start", "trigger": trigger})
    for i in range(0, len(PCM), 960):
        await sess._on_audio(PCM[i:i + 960])
    frame = {"type": "utterance_end", "greeting_played": False}
    frame.update(end or {})
    await sess._on_control(frame)
    if sess._response_task is not None:
        await sess._response_task


END = {"exit_reason": "vad_silence_after_speech", "frames": 74, "voiced_frames": 30,
       "trailing_silent_frames": 27, "silence_limit_frames": 27}


@pytest.mark.asyncio
async def test_an_opted_in_room_keeps_its_command_with_everything_the_sidecar_needs(turn) -> None:
    ws = _FakeWS()
    sess = StreamSession(ws, "kitchen")  # type: ignore[arg-type]
    await _say(sess, "wake_word", END)
    assert [f["type"] for f in ws.sent_text] == ["transcript", "response_start", "response_end"]
    (c,) = cc.list_captures()
    assert c["room_id"] == "kitchen" and c["trigger"] == "wake_word"
    assert c["transcript"] == SPOKEN
    assert (c["matched_handler"], c["matched_path"]) == ("timer", "fast")
    assert c["end_reason"] == "vad_silence_after_speech"
    assert c["capture"] == {"frames": 74, "voiced_frames": 30,
                            "trailing_silent_frames": 27, "silence_limit_frames": 27}
    assert c["listen"] == {"silence_timeout": 0.8, "vad_aggressiveness": 3}
    assert c["duration_ms"] == 1000 and c["interrupted"] is False
    assert {"capture_audio_ms", "stt_ms", "identify_ms", "route_ms", "tts_first_ms", "total_ms"} <= set(c["timings"])
    wav = cc.capture_paths("kitchen", c["id"])[0]
    with wave.open(str(wav), "rb") as w:
        assert w.readframes(w.getnframes()) == PCM, "the whole capture, byte for byte"


@pytest.mark.asyncio
async def test_a_followup_answer_is_kept_and_an_old_satellite_says_no_end_reason(turn) -> None:
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    await _say(sess, "followup")
    (c,) = cc.list_captures()
    assert c["trigger"] == "followup" and c["end_reason"] is None and c["capture"] is None


@pytest.mark.asyncio
async def test_nothing_is_kept_for_a_room_that_is_not_opted_in(turn) -> None:
    turn["opted_in"] = False
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    await _say(sess, "wake_word", END)
    await _say(sess, "followup", END)
    assert turn["routed"] == 2
    assert cc.list_captures() == []
    assert not _root().exists(), "not even a directory"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["barge_in", "chat", "push_to_talk"])
async def test_other_triggers_are_never_kept_even_when_opted_in(turn, trigger) -> None:
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    await _say(sess, trigger, END)
    assert turn["routed"] == 1
    assert cc.list_captures() == []


@pytest.mark.asyncio
async def test_a_wake_word_training_clip_is_never_kept(turn, monkeypatch) -> None:
    saved: list[bytes] = []

    async def _save(pcm: bytes) -> None:
        saved.append(pcm)

    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    sess.wake_recording = object()  # armed
    monkeypatch.setattr(sess, "_save_wake_clip", _save)
    await _say(sess, "wake_clip", END)
    assert saved == [PCM] and turn["routed"] == 0
    assert cc.list_captures() == []


@pytest.mark.asyncio
async def test_a_command_during_a_drop_in_call_is_never_kept(turn) -> None:
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    sess.dropin_peer = types.SimpleNamespace(room_id="garage")  # type: ignore[assignment]
    await _say(sess, "wake_word", END)
    assert turn["routed"] == 1
    assert cc.list_captures() == []


@pytest.mark.asyncio
async def test_a_call_the_peer_hung_up_mid_capture_is_still_never_kept(turn) -> None:
    """The capture opened during the call ("hey jarvis, hang up" — or any
    command) and the other room hung up before it closed: the audio still
    carries that room, so the call is judged over the whole capture."""
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    sess.dropin_peer = types.SimpleNamespace(room_id="garage")  # type: ignore[assignment]
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
    await sess._on_audio(PCM)
    sess.dropin_peer = None                  # the peer's hang-up
    await sess._on_control({"type": "utterance_end", **END})
    await sess._response_task
    assert turn["routed"] == 1
    assert cc.list_captures() == []
    # The latch is per utterance: the next command, with no call, is kept.
    await _say(sess, "wake_word", END)
    assert len(cc.list_captures()) == 1


@pytest.mark.asyncio
async def test_a_call_that_starts_and_ends_mid_capture_is_never_kept(turn, monkeypatch) -> None:
    """Another room drops in while this one is mid-command, and the call is
    gone again before the capture closes."""
    monkeypatch.setattr(settings, "dropin_silence_timeout_sec", 0)

    async def _no_music(_sess) -> None:
        return None

    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    caller = StreamSession(_FakeWS(), "garage")  # type: ignore[arg-type]
    caller.ws.app.state.dropin_lock = asyncio.Lock()
    caller.ws.app.state.active_dropins = {}
    caller.ws.app.state.satellite_full_duplex = {}
    for s in (sess, caller):
        monkeypatch.setattr(s, "_suppress_music_for", _no_music)
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
    await sess._on_audio(PCM)
    await caller._begin_dropin(sess)         # the real pairing path
    assert sess.dropin_peer is caller
    sess.dropin_peer = caller.dropin_peer = None   # and hung up again
    await sess._on_control({"type": "utterance_end", **END})
    await sess._response_task
    assert turn["routed"] == 1
    assert cc.list_captures() == []


@pytest.mark.asyncio
async def test_a_chat_mode_turn_is_never_kept(turn, monkeypatch) -> None:
    class _Sessions:
        def __init__(self, s):
            pass

        async def get_context(self, session_id):
            return {"conversational_mode": True}

    chatted: list[str] = []

    async def _chat_turn(**kw):
        chatted.append(kw["transcript"])

    monkeypatch.setattr(streaming, "SessionRepository", _Sessions)
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    sess.session_id = "00000000-0000-0000-0000-000000000001"  # type: ignore[assignment]
    monkeypatch.setattr(sess, "_run_chat_turn", _chat_turn)
    await _say(sess, "wake_word", END)
    assert chatted == [SPOKEN] and turn["routed"] == 0
    assert cc.list_captures() == []


@pytest.mark.asyncio
async def test_a_blank_capture_is_not_a_command(turn) -> None:
    turn["transcript"] = "   "
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    await _say(sess, "wake_word", END)
    assert turn["routed"] == 0 and cc.list_captures() == []


@pytest.mark.asyncio
async def test_a_turn_talked_over_is_still_kept_and_says_so(turn, monkeypatch) -> None:
    class _SlowTTS:
        async def synthesize(self, text, *, engine=None, voice=None) -> bytes:
            await asyncio.sleep(0.2)
            return _wav(b"\x01\x02" * 200)

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _SlowTTS())
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
    await sess._on_audio(PCM)
    await sess._on_control({"type": "utterance_end", **END})
    task = sess._response_task
    await asyncio.sleep(0.05)               # routed, synthesizing
    await sess._on_control({"type": "barge_in"})
    await task                              # the turn winds down on its own
    assert sess.ws.sent_text[-1]["interrupted"] is True
    (c,) = cc.list_captures()
    assert c["interrupted"] is True and c["transcript"] == SPOKEN


@pytest.mark.asyncio
async def test_a_second_cancel_cannot_lose_the_recording(turn, monkeypatch) -> None:
    """barge_in and the satellite's utterance_start right behind it are two
    cancels of one turn; the second lands while the turn awaits its writes."""
    class _SlowTTS:
        async def synthesize(self, text, *, engine=None, voice=None) -> bytes:
            await asyncio.sleep(0.2)
            return _wav(b"\x01\x02" * 200)

    gate = asyncio.Event()
    real_keep = cc.keep

    async def slow_keep(room_id, pcm, sidecar):
        await gate.wait()
        return await real_keep(room_id, pcm, sidecar)

    monkeypatch.setattr(streaming, "get_tts_client", lambda: _SlowTTS())
    monkeypatch.setattr(cc, "keep", slow_keep)
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    await sess._on_control({"type": "utterance_start", "trigger": "wake_word"})
    await sess._on_audio(PCM)
    await sess._on_control({"type": "utterance_end", **END})
    task = sess._response_task
    await asyncio.sleep(0.05)
    task.cancel()                          # barge_in
    await asyncio.sleep(0.02)              # parked on the recording write
    task.cancel()                          # utterance_start
    with pytest.raises(asyncio.CancelledError):
        await task
    gate.set()
    for _ in range(100):
        if cc.list_captures():
            break
        await asyncio.sleep(0.01)
    (c,) = cc.list_captures()
    assert c["interrupted"] is True


@pytest.mark.asyncio
async def test_the_turn_log_never_carries_the_words(turn, caplog) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="domovoi.command_captures")
    sess = StreamSession(_FakeWS(), "kitchen")  # type: ignore[arg-type]
    await _say(sess, "wake_word", END)
    lines = [r.getMessage() for r in caplog.records if "command capture kept" in r.getMessage()]
    assert len(lines) == 1 and "room=kitchen" in lines[0]
    assert "pasta" not in lines[0]
