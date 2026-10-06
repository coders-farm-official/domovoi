"""Writing ``.lrc`` files (domovoi/lyrics/sidecar.py, contract §6
[W1]–[W18], §5.4 [O1]–[O3]).

DB-free: ``write_sidecar`` is the file half; the rows it is given are
plain values. The rule under test above all: Domovoi never overwrites,
deletes or follows a ``.lrc`` that is not its own, and never leaves a
temporary file behind. Every lyric line is INVENTED ([C2]).
"""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path

import pytest

from domovoi.lyrics import sidecar
from domovoi.lyrics.lrc import MAX_LRC_BYTES, format_lrc, parse_lrc
from domovoi.lyrics.sidecar import sha256_hex, target_name, write_sidecar

L1 = "the lantern hums beside the river door"
L2 = "and every copper kettle sings at dawn"


def _content(*lines: str) -> bytes:
    entries = [(1000 * (i + 1), s) for i, s in enumerate(lines or (L1, L2))]
    return format_lrc(entries, title="Lantern Song", artist="The Example Band", album=None,
                      duration_sec=205, version="1.0.0").encode("utf-8")


@pytest.fixture
def music(tmp_path) -> Path:
    root = tmp_path / "Music"
    (root / "The Example Band").mkdir(parents=True)
    return root


def _song(music: Path, name: str = "Lantern Song.flac") -> Path:
    p = music / "The Example Band" / name
    p.write_bytes(b"not really audio")
    return p


def _write(audio: Path, music: Path, content: bytes | None = None, *, state=None, name=None,
           sha=None, **kw) -> sidecar.WriteOutcome:
    return write_sidecar(audio, content if content is not None else _content(),
                         music_root=music.resolve(), lrc_state=state, lrc_name=name,
                         lrc_sha256=sha, **kw)


def _leftovers(directory: Path) -> list[str]:
    return [n for n in os.listdir(directory) if ".domovoi-" in n]


# ─── New files [W2], [W6], [W9], [W12] ────────────────────────────────────


def test_a_new_file_is_written_marked_and_complete(music) -> None:
    audio = _song(music)
    content = _content()
    out = _write(audio, music, content)
    assert out.state == "written" and out.name == "Lantern Song.lrc"
    assert out.sha256 == sha256_hex(content) and out.error is None and not out.stop
    target = audio.parent / "Lantern Song.lrc"
    assert target.read_bytes() == content
    back = parse_lrc(target.read_text(encoding="utf-8"))
    assert back.marked and back.synced == ((1000, L1), (2000, L2))
    assert _leftovers(audio.parent) == []
    assert target_name(audio) == "Lantern Song.lrc"
    assert target_name(Path("Glass Harbor.MP3")) == "Glass Harbor.lrc"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_mode_is_0644_through_the_umask(music) -> None:
    audio = _song(music)
    old = os.umask(0o022)
    try:
        _write(audio, music)
    finally:
        os.umask(old)
    assert stat.S_IMODE((audio.parent / "Lantern Song.lrc").stat().st_mode) == 0o644


# ─── Never somebody else's file [W4], [W10], [O2] ─────────────────────────


def test_an_owners_lrc_is_never_touched(music) -> None:
    audio = _song(music)
    theirs = audio.parent / "Lantern Song.lrc"
    theirs.write_bytes(b"[00:01.00]the owner's own words\n")
    before = theirs.stat()
    out = _write(audio, music)
    assert out.state == "exists"
    assert theirs.read_bytes() == b"[00:01.00]the owner's own words\n"
    assert theirs.stat().st_mtime_ns == before.st_mtime_ns
    assert _leftovers(audio.parent) == []


def test_an_owners_lrc_in_another_case_is_never_touched(music) -> None:
    audio = _song(music)
    theirs = audio.parent / "Lantern Song.LRC"
    theirs.write_bytes(b"[00:01.00]theirs\n")
    out = _write(audio, music)
    assert out.state == "exists"
    names = sorted(n for n in os.listdir(audio.parent) if n.lower().endswith(".lrc"))
    assert names == ["Lantern Song.LRC"]
    assert theirs.read_bytes() == b"[00:01.00]theirs\n"


def test_a_marked_file_that_was_edited_is_the_households_now(music) -> None:
    audio = _song(music)
    first = _write(audio, music)
    target = audio.parent / first.name
    target.write_bytes(target.read_bytes() + b"[00:09.00]a line they added\n")
    edited = target.read_bytes()
    out = _write(audio, music, _content(L2), state="written", name=first.name, sha=first.sha256)
    assert out.state == "edited"
    assert target.read_bytes() == edited                     # not overwritten
    # ... and once edited (or deleted), never written again [W5]
    for state in ("edited", "deleted"):
        again = _write(audio, music, _content(L2), state=state, name=first.name, sha=first.sha256)
        assert again.state is None
        assert target.read_bytes() == edited
    assert _leftovers(audio.parent) == []


def test_a_marked_file_from_another_install_is_never_overwritten(music) -> None:
    audio = _song(music)
    target = audio.parent / "Lantern Song.lrc"
    target.write_bytes(_content(L2))                          # marked, but not this row's
    out = _write(audio, music, _content())
    assert out.state == "exists"
    assert target.read_bytes() == _content(L2)


def test_a_deleted_file_is_noted_and_never_written_again(music) -> None:
    audio = _song(music)
    first = _write(audio, music)
    (audio.parent / first.name).unlink()
    out = _write(audio, music, _content(L2), state="written", name=first.name, sha=first.sha256)
    assert out.state == "deleted"
    assert not (audio.parent / first.name).exists()


def test_a_directory_at_the_target_counts_as_existing(music) -> None:
    audio = _song(music)
    (audio.parent / "Lantern Song.lrc").mkdir()
    assert _write(audio, music).state == "exists"
    assert (audio.parent / "Lantern Song.lrc").is_dir()


def test_symlinks_at_the_target_are_never_followed(music, tmp_path) -> None:
    audio = _song(music)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_bytes(b"untouched")
    link = audio.parent / "Lantern Song.lrc"
    try:
        os.symlink(elsewhere, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable here")
    assert _write(audio, music).state == "exists"
    assert elsewhere.read_bytes() == b"untouched"
    link.unlink()
    os.symlink(tmp_path / "gone.txt", link)                   # broken
    assert _write(audio, music).state == "exists"
    assert not (tmp_path / "gone.txt").exists()


def test_two_audio_files_with_one_stem_the_first_written_wins(music) -> None:
    mp3 = _song(music, "Lantern Song.mp3")
    flac = _song(music, "Lantern Song.flac")
    first = _write(mp3, music)
    assert first.state == "written"
    second = _write(flac, music, _content(L2))
    assert second.state == "exists"
    assert (music / "The Example Band" / "Lantern Song.lrc").read_bytes() == _content()


# ─── Refreshing Domovoi's own file [W7], [W8] ─────────────────────────────


def test_domovois_own_file_is_refreshed_when_the_lyrics_change(music) -> None:
    audio = _song(music)
    first = _write(audio, music)
    newer = _content(L2, L1)
    out = _write(audio, music, newer, state="written", name=first.name, sha=first.sha256)
    assert out.state == "written" and out.sha256 == sha256_hex(newer)
    assert (audio.parent / first.name).read_bytes() == newer
    assert _leftovers(audio.parent) == []


def test_the_same_bytes_are_not_written_again(music) -> None:
    audio = _song(music)
    content = _content()
    first = _write(audio, music, content)
    target = audio.parent / first.name
    os.utime(target, ns=(1_000_000_000, 1_000_000_000))
    out = _write(audio, music, content, state="written", name=first.name, sha=first.sha256)
    assert out.state is None and out.error is None
    assert target.stat().st_mtime_ns == 1_000_000_000


def test_a_refresh_rechecks_the_file_right_before_replacing(music, monkeypatch) -> None:
    """The household edits the file between Domovoi's first look and its
    replace: the replace does not happen."""
    audio = _song(music)
    first = _write(audio, music)
    target = audio.parent / first.name
    real_write_new = sidecar._write_new_file

    def write_then_edit(path, content):
        real_write_new(path, content)
        target.write_bytes(b"[00:01.00]edited meanwhile\n")

    monkeypatch.setattr(sidecar, "_write_new_file", write_then_edit)
    out = _write(audio, music, _content(L2), state="written", name=first.name, sha=first.sha256)
    assert out.state == "edited"
    assert target.read_bytes() == b"[00:01.00]edited meanwhile\n"
    assert _leftovers(audio.parent) == []


def test_another_lrc_beside_domovois_own_leaves_both_alone(music) -> None:
    if sys.platform == "win32" or sys.platform == "darwin":
        pytest.skip("needs a case-sensitive file system")
    audio = _song(music)
    first = _write(audio, music)
    theirs = audio.parent / "Lantern Song.LRC"
    theirs.write_bytes(b"[00:01.00]theirs\n")
    out = _write(audio, music, _content(L2), state="written", name=first.name, sha=first.sha256)
    assert out.state is None
    assert (audio.parent / first.name).read_bytes() == _content()


# ─── Where it may write [W3], [W16] ───────────────────────────────────────


def test_outside_music_dir_is_refused(music, tmp_path) -> None:
    stray = tmp_path / "Elsewhere"
    stray.mkdir()
    audio = stray / "Glass Harbor.mp3"
    audio.write_bytes(b"x")
    out = _write(audio, music)
    assert (out.state, out.error) == ("failed", "outside_music_dir")
    assert os.listdir(stray) == ["Glass Harbor.mp3"]


def test_a_missing_audio_file_gets_nothing(music) -> None:
    audio = music / "The Example Band" / "gone.mp3"
    assert _write(audio, music).state is None
    assert os.listdir(audio.parent) == []


# ─── Races and file systems without hard links [W6] ───────────────────────


def test_a_file_appearing_between_the_check_and_the_link_wins(music) -> None:
    audio = _song(music)
    target = audio.parent / "Lantern Song.lrc"

    def racing_link(src, dst):
        Path(dst).write_bytes(b"[00:01.00]theirs, just now\n")
        os.link(src, dst)

    out = _write(audio, music, link=racing_link)
    assert out.state == "exists"
    assert target.read_bytes() == b"[00:01.00]theirs, just now\n"
    assert _leftovers(audio.parent) == []


@pytest.mark.parametrize("code", [errno.EXDEV, errno.EPERM, getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
                                  errno.ENOSYS])
def test_without_hard_links_the_target_is_created_exclusively(music, code) -> None:
    audio = _song(music)

    def no_links(src, dst):
        raise OSError(code, os.strerror(code))

    out = _write(audio, music, link=no_links)
    assert out.state == "written"
    assert (audio.parent / "Lantern Song.lrc").read_bytes() == _content()
    assert _leftovers(audio.parent) == []


def test_the_windows_no_links_error_uses_the_fallback(music) -> None:
    audio = _song(music)

    def fat_volume(src, dst):
        e = OSError(errno.EINVAL, "Incorrect function")
        e.winerror = 1
        raise e

    assert _write(audio, music, link=fat_volume).state == "written"


def test_the_fallback_never_clobbers_either(music) -> None:
    audio = _song(music)
    target = audio.parent / "Lantern Song.lrc"

    def no_links_and_a_race(src, dst):
        target.write_bytes(b"[00:01.00]theirs\n")
        raise OSError(errno.EXDEV, "cross-device")

    out = _write(audio, music, link=no_links_and_a_race)
    assert out.state == "exists"
    assert target.read_bytes() == b"[00:01.00]theirs\n"
    assert _leftovers(audio.parent) == []


# ─── Failures [W13] ───────────────────────────────────────────────────────


def test_permission_denied(music, monkeypatch) -> None:
    audio = _song(music)

    def denied(path):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(sidecar, "_open_new", denied)
    out = _write(audio, music)
    assert (out.state, out.error, out.stop) == ("failed", "permission", False)
    assert not (audio.parent / "Lantern Song.lrc").exists()


def test_a_full_disk_stops_the_round_and_leaves_nothing(music, monkeypatch) -> None:
    audio = _song(music)

    def full(fd, data):
        os.write(fd, data[:10])                      # a partial write, then the disk is full
        os.close(fd)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(sidecar, "_fill", full)
    out = _write(audio, music)
    assert (out.state, out.error, out.stop) == ("failed", "io", True)
    assert sorted(os.listdir(audio.parent)) == ["Lantern Song.flac"]


def test_a_full_disk_in_the_fallback_removes_only_its_own_partial_file(music, monkeypatch) -> None:
    audio = _song(music)
    calls = {"n": 0}
    real_fill = sidecar._fill

    def full_on_the_second(fd, data):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_fill(fd, data)               # the temporary file
        os.write(fd, data[:10])
        os.close(fd)
        raise OSError(errno.ENOSPC, "No space left on device")

    def no_links(src, dst):
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(sidecar, "_fill", full_on_the_second)
    out = _write(audio, music, link=no_links)
    assert (out.state, out.stop) == ("failed", True)
    assert sorted(os.listdir(audio.parent)) == ["Lantern Song.flac"]


def test_a_name_too_long(music, monkeypatch) -> None:
    audio = _song(music)

    def too_long(path):
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(sidecar, "_open_new", too_long)
    assert _write(audio, music).error == "name"


def test_other_io_errors(music, monkeypatch) -> None:
    audio = _song(music)

    def broken_link(src, dst):
        raise OSError(errno.EIO, "I/O error")

    out = _write(audio, music, link=broken_link)
    assert (out.state, out.error) == ("failed", "io")
    assert _leftovers(audio.parent) == []


def test_lyrics_too_big_to_read_back_are_never_written(music) -> None:
    audio = _song(music)
    huge = b"[re:Domovoi]\n" + b"[00:01.00]" + b"x" * MAX_LRC_BYTES + b"\n"
    out = _write(audio, music, huge)
    assert (out.state, out.error) == ("failed", "io")
    first = _write(audio, music)
    out = _write(audio, music, huge, state="written", name=first.name, sha=first.sha256)
    assert out.state is None                       # Domovoi's file stays Domovoi's
    assert (audio.parent / first.name).read_bytes() == _content()


def test_never_raises_on_an_unlistable_folder(music, tmp_path) -> None:
    audio = tmp_path / "Music" / "no such folder" / "x.mp3"
    assert _write(audio, music).state is None      # the audio file is not there either
