"""Library tools (V058), DB-free: genre placeholders and labels, what a file
says about itself, playlists from files, the duplicates rules, stronger
song-recognition matching, review candidates, the cover-art writer and
worker, and the trash folder.

The examples are the real ones the owner's library showed on 2026-11-14
("People & Blogs" read as the genres "people" and "blogs"; ~240 duplicate
groups of downloads named "Artist - Title (Official Video)" beside tagged
copies).
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
from pathlib import Path

import pytest

from domovoi.config import settings
from domovoi.tests import media_writer as mw

JPEG_OLD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"o" * 300
JPEG_NEW = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"n" * 500
MBID = "6f0e7c58-6a49-4e0c-8a4e-6b1d2f9f8f11"
MBID_REL = "1a2b3c4d-1111-2222-3333-444455556666"
MBID_RG = "9f8e7d6c-aaaa-bbbb-cccc-ddddeeeeffff"


# ─── 1. Genres: video-site categories, labels ────────────────────────────


@pytest.mark.parametrize("value", [
    "People & Blogs", "Film & Animation", "Music", "Entertainment", "News & Politics",
    "Science & Technology", "Howto & Style", "Nonprofits & Activism", "pets & animals",
])
def test_a_video_site_category_is_no_genre(value) -> None:
    from domovoi.library_genres import display_genre, genre_keys

    assert genre_keys([value]) == []
    assert display_genre([value]) is None


def test_real_genres_with_an_ampersand_still_split_or_stay_whole() -> None:
    from domovoi.library_genres import genre_keys

    assert genre_keys(["Rock & Roll"]) == ["rock and roll"]
    assert genre_keys(["Hip-Hop & Rap"]) == ["hip hop"]
    assert genre_keys(["Jazz & Blues"]) == ["jazz", "blues"]
    # Only a whole value is a category: "Music" inside a list is a word.
    assert genre_keys(["People & Blogs", "Soul"]) == ["soul"]


@pytest.mark.parametrize(("key", "label"), [
    ("r&b", "R&B"), ("aor", "AOR"), ("edm", "EDM"), ("uk garage", "UK Garage"),
    ("dj mix", "DJ Mix"), ("uk r&b", "UK R&B"), ("alternative rock", "Alternative Rock"),
])
def test_genre_labels_write_acronyms_in_capitals(key, label) -> None:
    from domovoi.library_genres import genre_label

    assert genre_label(key) == label


def test_the_tag_version_is_bumped_so_every_row_is_read_again() -> None:
    from domovoi.library_genres import TAGS_VERSION

    assert TAGS_VERSION == 2
    sql = (Path(__file__).resolve().parents[1] / "db" / "migrations"
           / "V058__library_tools.sql").read_text(encoding="utf-8")
    assert "WHERE tags_version < 2" in sql


# ─── 5. What a file says about itself ────────────────────────────────────


def _mp3_with_ids(path: Path) -> Path:
    from domovoi.tests.media_writer import id3_tag, silent_mp3

    tag = id3_tag([
        mw.TIT2("Collab"),
        mw.text_frame("TPE1", "A"),
        mw.TXXX(desc="MusicBrainz Album Id", text=MBID_REL),
        mw.TXXX(desc="MusicBrainz Release Group Id", text=MBID_RG),
        mw.TXXX(desc="ARTISTS", text="A; B; a"),
        mw.TXXX(desc="PLAYLIST", text="Road Trip; Gym"),
    ])
    # A UFID frame by hand: owner, NUL, the id (ID3v2.4, syncsafe size).
    body = b"http://musicbrainz.org\x00" + MBID.encode()
    frame = b"UFID" + bytes([0, 0, 0, len(body)]) + b"\x00\x00" + body
    raw = tag[10:]
    new_size = len(raw) + len(frame)
    ss = bytes(((new_size >> 21) & 0x7F, (new_size >> 14) & 0x7F, (new_size >> 7) & 0x7F, new_size & 0x7F))
    path.write_bytes(tag[:6] + ss + frame + raw + silent_mp3())
    return path


def test_the_tag_reader_takes_the_files_own_ids_credits_and_playlists(tmp_path) -> None:
    from domovoi.workers.library_indexer import _read_tags

    meta = _read_tags(_mp3_with_ids(tmp_path / "x.mp3"))
    assert meta["mb_recording_id"] == MBID
    assert meta["mb_release_id"] == MBID_REL
    assert meta["mb_release_group_id"] == MBID_RG
    assert meta["artist_credits"] == ["A", "B"]          # "a" again is the same name
    assert meta["file_playlists"] == ["Road Trip", "Gym"]


def test_vorbis_ids_and_a_bad_id_is_ignored(tmp_path) -> None:
    from domovoi.workers.library_indexer import _read_tags

    p = tmp_path / "x.flac"
    p.write_bytes(mw.flac_file([("TITLE", "t"), ("MUSICBRAINZ_TRACKID", MBID.upper()),
                                ("MUSICBRAINZ_ALBUMID", "not-an-id"), ("ARTISTS", "X"),
                                ("ARTISTS", "Y")]))
    meta = _read_tags(p)
    assert meta["mb_recording_id"] == MBID              # lower-cased
    assert meta["mb_release_id"] is None
    assert meta["artist_credits"] == ["X", "Y"]


def test_split_names() -> None:
    from domovoi.library_file_ids import split_names

    assert split_names(["A; B;;  c ", "B", "D\x00E"]) == ["A", "B", "c", "D", "E"]
    assert split_names(["x‮y"]) == ["xy"]


# ─── 5. Playlists from files ─────────────────────────────────────────────


def test_m3u_entries_relative_absolute_and_file_urls(tmp_path) -> None:
    from domovoi.library_playlists_import import parse_m3u

    base = tmp_path / "Seal" / "Playlists"
    text = ("#EXTM3U\n#EXTINF:123,Song\n../../Artist/Song.mp3\n\n"
            f"{tmp_path / 'abs.mp3'}\nhttps://example.com/stream.mp3\n"
            f"file:///{(tmp_path / 'u.mp3').as_posix().lstrip('/')}\n")
    got = parse_m3u(text, base)
    assert got[0] == os.path.normpath(str(tmp_path / "Artist" / "Song.mp3"))
    assert got[1] == os.path.normpath(str(tmp_path / "abs.mp3"))
    assert len(got) == 3                                 # the web address is skipped
    assert os.path.normcase(got[2]) == os.path.normcase(os.path.normpath(str(tmp_path / "u.mp3")))


def test_playlists_from_m3u_files_and_tags_join_by_name(tmp_path) -> None:
    from domovoi.library_playlists_import import build_playlists

    a, b, c = (str(tmp_path / f"{n}.mp3") for n in "abc")
    tracks = [(1, a, None), (2, b, ["gym", "Chill"]), (3, c, ["Gym"])]
    got = build_playlists({"Gym": [c, a, str(tmp_path / "missing.mp3")]}, tracks)
    assert got == {"Gym": [3, 1, 2], "Chill": [2]}


def test_reading_playlist_files_skips_the_trash(tmp_path) -> None:
    from domovoi.library_playlists_import import read_playlist_files
    from domovoi.library_trash import TRASH_DIR_NAME

    (tmp_path / "Seal" / "Playlists").mkdir(parents=True)
    (tmp_path / "Seal" / "Playlists" / "Road Trip.m3u8").write_text("a.mp3\n", encoding="utf-8")
    (tmp_path / TRASH_DIR_NAME).mkdir()
    (tmp_path / TRASH_DIR_NAME / "Old.m3u").write_text("b.mp3\n", encoding="utf-8")
    assert list(read_playlist_files(tmp_path)) == ["Road Trip"]


# ─── 2. Duplicates ───────────────────────────────────────────────────────


def _copy(i, title, artist, dur, *, mb=None, size_kbps=None, album=None, art=False):
    from domovoi.library_duplicates import Copy

    size = int(dur * size_kbps * 1000 / 8) if size_kbps and dur else None
    return Copy(i, title, artist, album, dur, mb, size, art, added_order=i)


def test_song_key_ignores_an_uploads_decoration() -> None:
    from domovoi.library_duplicates import song_key

    want = song_key("In the End", "Linkin Park")
    assert want == song_key("Linkin Park - In the End (Official Video) [dQw4w9WgXcQ]", "Linkin Park")
    assert want == song_key("In The End - Lyrics", "LINKIN PARK feat. Someone")
    assert song_key("Numb", "Linkin Park") != want
    assert song_key("In the End", None) is None


def test_groups_and_what_they_are_called() -> None:
    from domovoi.library_duplicates import (
        KIND_DIFFERENT_LENGTH,
        KIND_ID_LENGTHS,
        KIND_SAME,
        find_groups,
    )

    copies = [
        _copy(1, "In the End", "Linkin Park", 216, size_kbps=320),
        _copy(2, "Linkin Park - In the End (Official Video)", "Linkin Park", 218, size_kbps=128),
        _copy(3, "Numb", "Linkin Park", 185, mb=MBID),
        _copy(4, "Numb (Live)", "Linkin Park", 230, mb=MBID),
        _copy(5, "Faint", "Linkin Park", 162),
        _copy(6, "Faint", "Linkin Park", 175),
        _copy(7, "Alone", "Someone", 200),
    ]
    groups = {tuple(c.id for c in g.copies): g for g in find_groups(copies)}
    assert set(groups) == {(1, 2), (3, 4), (5, 6)}
    assert groups[(1, 2)].kind == KIND_SAME
    assert groups[(3, 4)].kind == KIND_ID_LENGTHS
    assert groups[(5, 6)].kind == KIND_DIFFERENT_LENGTH
    assert groups[(1, 2)].best_id == 1                   # 320 kbps over 128


def test_the_best_copy_bitrate_then_identity_then_art() -> None:
    from domovoi.library_duplicates import best_copy

    # Same bitrate (within 5 %): identified beats tagged beats untagged.
    a = _copy(1, "S", "A", 200, size_kbps=256)
    b = _copy(2, "S", "A", 200, size_kbps=250, album="Album")
    c = _copy(3, "S", "A", 200, size_kbps=255, mb=MBID)
    assert best_copy([a, b, c]).id == 3
    assert best_copy([a, b]).id == 2
    # Then cover art.
    d = _copy(4, "S", "A", 200, size_kbps=256, art=True)
    assert best_copy([a, d]).id == 4
    # A clearly higher bitrate wins over everything.
    e = _copy(5, "S", "A", 200, size_kbps=320)
    assert best_copy([c, d, e]).id == 5


def test_a_group_key_changes_with_its_members() -> None:
    from domovoi.library_duplicates import group_key

    assert group_key([3, 1]) == group_key([1, 3])
    assert group_key([1, 3]) != group_key([1, 3, 9])


# ─── 2. The trash folder ─────────────────────────────────────────────────


def test_move_to_trash_and_restore(tmp_path) -> None:
    from domovoi.library_trash import TRASH_DIR_NAME, TrashError, move_to_trash, restore_file

    song = tmp_path / "Artist" / "Song.mp3"
    song.parent.mkdir()
    song.write_bytes(b"audio")
    dest = move_to_trash(song, tmp_path, stamp="20261114-120000")
    assert not song.exists() and dest.read_bytes() == b"audio"
    assert dest == tmp_path / TRASH_DIR_NAME / "20261114-120000" / "Artist" / "Song.mp3"
    assert (tmp_path / TRASH_DIR_NAME / ".mpdignore").read_text() == "*\n"
    with pytest.raises(TrashError):
        move_to_trash(dest, tmp_path)                   # already in the trash
    with pytest.raises(TrashError):
        move_to_trash(tmp_path.parent / "elsewhere.mp3", tmp_path)
    song.write_bytes(b"new")                            # the place is taken now
    with pytest.raises(TrashError):
        restore_file(dest, song, tmp_path)
    song.unlink()
    assert restore_file(dest, song, tmp_path) == song.resolve()
    assert song.read_bytes() == b"audio"


def test_the_indexer_never_walks_the_trash(tmp_path) -> None:
    from domovoi.library_trash import TRASH_DIR_NAME
    from domovoi.workers.library_indexer import _audio_files

    (tmp_path / "a.mp3").write_bytes(mw.silent_mp3())
    (tmp_path / TRASH_DIR_NAME / "x").mkdir(parents=True)
    (tmp_path / TRASH_DIR_NAME / "x" / "b.mp3").write_bytes(mw.silent_mp3())
    assert [p.name for p in _audio_files(tmp_path)] == ["a.mp3"]


# ─── 3. Stronger matching ────────────────────────────────────────────────


def _answer(*recs, score=0.95) -> dict:
    return {"status": "ok", "results": [{"id": "a", "score": score, "recordings": list(recs)}]}


def _rec(rid, title, artist, *, duration=None, sources=None, groups=None) -> dict:
    out = {"id": rid, "title": title, "artists": [{"name": artist}]}
    if duration is not None:
        out["duration"] = duration
    if sources is not None:
        out["sources"] = sources
    if groups is not None:
        out["releasegroups"] = groups
    return out


def test_more_submissions_win_among_equal_scores() -> None:
    from domovoi.workers.library_enricher import parse_answer, rank_candidates

    got = rank_candidates(parse_answer(_answer(
        _rec("r1", "Creep", "Radiohead", sources=3),
        _rec("r2", "Creep", "Radiohead", sources=41),
    )))
    assert [r.musicbrainz_recording_id for r in got] == ["r2", "r1"]
    assert got[0].sources == 41 and got[0].score == 0.95


def test_the_album_is_a_plain_album_over_a_compilation_or_single() -> None:
    from domovoi.workers.library_enricher import parse_answer

    [r] = parse_answer(_answer(_rec("r", "Creep", "Radiohead", groups=[
        {"id": "c", "title": "Greatest Hits", "type": "Album", "secondarytypes": ["Compilation"]},
        {"id": "s", "title": "Creep", "type": "Single"},
        {"id": "a", "title": "Pablo Honey", "type": "Album"},
    ])))
    assert (r.album, r.release_group_id) == ("Pablo Honey", "a")


def test_a_candidate_45_seconds_off_is_another_recording() -> None:
    from domovoi.workers.library_enricher import (
        EnrichmentResult,
        Lookup,
        Verdict,
        _agreeing,
        length_plausible,
    )

    assert length_plausible(200, 244) and not length_plausible(200, 246)
    assert length_plausible(None, 999) and length_plausible(200, None)
    live = EnrichmentResult("Creep", "Radiohead", musicbrainz_recording_id="live", source="acoustid",
                            length_sec=320, score=0.9)
    studio = EnrichmentResult("Creep", "Radiohead", musicbrainz_recording_id="studio", source="acoustid",
                              length_sec=236, score=0.9)
    look = Lookup(Verdict.MATCH, result=live, candidates=(live, studio))
    got = _agreeing(look, ("Creep", "Radiohead"), 238)
    assert got.verdict is Verdict.MATCH and got.result is studio
    only_live = Lookup(Verdict.MATCH, result=live, candidates=(live,))
    refused = _agreeing(only_live, None, 238)            # no names: the length still counts
    assert refused.verdict is Verdict.DISAGREED and "length" in refused.detail
    assert refused.candidates == (live,)


@pytest.mark.asyncio
async def test_acoustid_asks_for_lengths_sources_and_albums_and_keeps_near_misses(
        monkeypatch, tmp_path) -> None:
    from unittest.mock import patch

    from domovoi.workers import library_enricher as le

    monkeypatch.setattr(le.settings, "acoustid_api_key", "app-key")
    monkeypatch.setattr(le.settings, "library_enricher_acoustid_min_score", 0.7)
    seen = []

    def match(_k, _p, **kw):
        seen.append(kw.get("meta"))
        return _answer(_rec("r", "Jumper", "Third Eye Blind", duration=272.4), score=0.55)

    with patch("acoustid.match", match):
        got = await le._enrich_via_acoustid(tmp_path / "x.mp3")
    assert seen == [le.ACOUSTID_META] and "sources" in le.ACOUSTID_META
    assert got.verdict is le.Verdict.NO_MATCH
    assert [c.length_sec for c in got.candidates] == [272]          # kept for "Needs review"
    assert le.review_candidates(got)[0]["score"] == 0.55


def test_review_candidates_keep_five_each_once() -> None:
    from domovoi.workers.library_enricher import EnrichmentResult, Lookup, Verdict, review_candidates

    cands = tuple(EnrichmentResult(f"T{i % 6}", "A", musicbrainz_recording_id=f"r{i % 6}", score=0.9)
                  for i in range(12))
    got = review_candidates(Lookup(Verdict.DISAGREED, result=cands[0], candidates=cands))
    assert [c["recording_id"] for c in got] == ["r0", "r1", "r2", "r3", "r4"]
    assert set(got[0]) >= {"title", "artist", "album", "length_sec", "score", "sources"}


def test_musicbrainz_release_choice_official_album_earliest() -> None:
    from domovoi.clients.musicbrainz import choose_release, recording_details_of

    def rel(rid, title, status, primary, secondary, date, rg_date):
        return {"id": rid, "title": title, "status": status, "date": date,
                "release-group": {"id": rid.replace("1", "9"), "primary-type": primary,
                                  "secondary-types": secondary, "first-release-date": rg_date}}

    ids = [f"{n}1111111-1111-1111-1111-111111111111" for n in "abcde"]
    data = {"first-release-date": "1992-09-21", "releases": [
        rel(ids[0], "Creep (single)", "Official", "Single", [], "1992-09-21", "1992-09-21"),
        rel(ids[1], "Greatest Hits", "Official", "Album", ["Compilation"], "2008-06-02", "2008-06-02"),
        rel(ids[2], "Pablo Honey (reissue)", "Official", "Album", [], "2009-03-24", "1993-02-22"),
        rel(ids[3], "Pablo Honey", "Official", "Album", [], "1993-02-22", "1993-02-22"),
        rel(ids[4], "Live bootleg", "Bootleg", "Album", ["Live"], "1994-01-01", "1994-01-01"),
    ]}
    assert choose_release(data)["id"] == ids[3]
    d = recording_details_of(data)
    assert (d.album, d.release_id, d.year) == ("Pablo Honey", ids[3], 1993)
    assert recording_details_of({"first-release-date": "1999"}).year == 1999   # no releases


# ─── 6. The cover-art writer ─────────────────────────────────────────────


def _snapshot(tmp_path, name="s.json") -> Path:
    return tmp_path / "originals" / name


def test_mp3_cover_is_replaced_and_undo_restores_the_original(tmp_path) -> None:
    from domovoi import media_tags
    from domovoi.cover_art_writer import current_cover, undo, write_front_cover

    p = mw.write_mp3(tmp_path / "a.mp3", [
        mw.TIT2("Song"), mw.APIC(mime="image/jpeg", type=3, desc="mine", data=JPEG_OLD),
        mw.APIC(mime="image/jpeg", type=4, desc="back", data=b"\xff\xd8\xffBACK"),
    ])
    snap = _snapshot(tmp_path)
    res = write_front_cover(p, JPEG_NEW, snapshot_path=snap)
    assert res.had_art and current_cover(p) == JPEG_NEW
    f = media_tags.read(p)
    assert f.tags.first("TIT2") == "Song"
    assert sorted(x.type for x in f.tags.getall("APIC")) == [3, 4]      # the back cover stays
    saved = json.loads(snap.read_text())
    assert [base64.b64decode(fr["body"]).endswith(JPEG_OLD) for fr in saved["frames"]] == [True]
    # A second write keeps the FIRST snapshot.
    write_front_cover(p, JPEG_NEW + b"2", snapshot_path=snap)
    assert json.loads(snap.read_text())["frames"] == saved["frames"]
    undo(p, snap)
    pics = {x.type: (x.desc, x.data) for x in media_tags.read(p).tags.getall("APIC")}
    assert pics[3] == ("mine", JPEG_OLD) and 4 in pics


def test_a_bare_mp3_gets_a_tag_and_undo_gives_back_the_same_bytes(tmp_path) -> None:
    from domovoi.cover_art_writer import current_cover, undo, write_front_cover

    p = tmp_path / "b.mp3"
    p.write_bytes(mw.silent_mp3(30))
    before = p.read_bytes()
    res = write_front_cover(p, JPEG_NEW, snapshot_path=_snapshot(tmp_path))
    assert res.created_tag and not res.had_art and current_cover(p) == JPEG_NEW
    assert p.read_bytes().endswith(before)                  # the audio is untouched
    undo(p, _snapshot(tmp_path))
    assert p.read_bytes() == before


def test_flac_cover_write_and_undo(tmp_path) -> None:
    from domovoi import media_tags
    from domovoi.cover_art_writer import CoverWriteSkipped, current_cover, undo, write_front_cover

    p = tmp_path / "c.flac"
    p.write_bytes(mw.flac_file([("TITLE", "t")], pictures=[(3, "image/jpeg", JPEG_OLD)]))
    write_front_cover(p, JPEG_NEW, snapshot_path=_snapshot(tmp_path))
    assert current_cover(p) == JPEG_NEW and media_tags.read(p).tags.first("TITLE") == "t"
    with pytest.raises(CoverWriteSkipped, match="same"):        # nothing to do on the next sweep
        write_front_cover(p, JPEG_NEW, snapshot_path=_snapshot(tmp_path))
    undo(p, _snapshot(tmp_path))
    assert [(x.type, x.data) for x in media_tags.read(p).pictures] == [(3, JPEG_OLD)]


def test_only_missing_art_leaves_a_file_with_art_alone(tmp_path) -> None:
    from domovoi.cover_art_writer import CoverWriteSkipped, write_front_cover

    p = mw.write_mp3(tmp_path / "a.mp3", [mw.APIC(mime="image/jpeg", type=0, desc="", data=JPEG_OLD)])
    before = p.read_bytes()
    with pytest.raises(CoverWriteSkipped):
        write_front_cover(p, JPEG_NEW, snapshot_path=_snapshot(tmp_path), only_if_missing=True)
    assert p.read_bytes() == before


def test_a_file_that_changed_since_it_was_read_is_left_alone(tmp_path) -> None:
    from domovoi.cover_art_writer import CoverWriteSkipped, write_front_cover

    p = mw.write_mp3(tmp_path / "a.mp3", [mw.TIT2("x")])
    before = p.read_bytes()
    with pytest.raises(CoverWriteSkipped, match="changed"):
        write_front_cover(p, JPEG_NEW, snapshot_path=_snapshot(tmp_path), expect_stat=(1, 1))
    assert p.read_bytes() == before


def test_a_copy_that_does_not_verify_never_replaces_the_file(tmp_path, monkeypatch) -> None:
    from domovoi import cover_art_writer as cw

    p = mw.write_mp3(tmp_path / "a.mp3", [mw.TIT2("x")])
    before = p.read_bytes()
    monkeypatch.setattr(cw, "_hash_tail", lambda path, start: str(path))   # every hash differs
    with pytest.raises(cw.CoverWriteSkipped, match="audio differs"):
        cw.write_front_cover(p, JPEG_NEW, snapshot_path=_snapshot(tmp_path))
    assert p.read_bytes() == before
    assert [x.name for x in tmp_path.iterdir() if x.name.endswith(".domovoi-tmp")] == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX folder permissions")
def test_a_read_only_folder_is_skipped(tmp_path) -> None:
    from domovoi.cover_art_writer import CoverWriteSkipped, write_front_cover

    d = tmp_path / "ro"
    d.mkdir()
    p = mw.write_mp3(d / "a.mp3", [mw.TIT2("x")])
    os.chmod(d, 0o555)
    try:
        if os.access(d, os.W_OK):
            pytest.skip("running as a user who may write anywhere")
        with pytest.raises(CoverWriteSkipped, match="read-only"):
            write_front_cover(p, JPEG_NEW, snapshot_path=_snapshot(tmp_path))
    finally:
        os.chmod(d, 0o755)


def test_a_read_only_file_is_skipped(tmp_path, monkeypatch) -> None:
    from domovoi import cover_art_writer as cw

    p = mw.write_mp3(tmp_path / "a.mp3", [mw.TIT2("x")])
    monkeypatch.setattr(cw.os, "access", lambda path, mode: False)
    with pytest.raises(cw.CoverWriteSkipped, match="read-only"):
        cw.write_front_cover(p, JPEG_NEW, snapshot_path=_snapshot(tmp_path))


def test_formats_domovoi_does_not_write(tmp_path) -> None:
    from domovoi.cover_art_writer import writable_kind

    (tmp_path / "a.m4a").write_bytes(mw.bare_m4a())
    (tmp_path / "a.ogg").write_bytes(mw.bare_ogg("vorbis"))
    (tmp_path / "a.mp3").write_bytes(mw.silent_mp3())
    assert writable_kind(tmp_path / "a.m4a") is None
    assert writable_kind(tmp_path / "a.ogg") is None
    assert writable_kind(tmp_path / "a.mp3") == "id3"


def test_image_size_and_same_picture() -> None:
    from domovoi.cover_art_writer import image_size, same_picture

    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 1200, 1200) + b"\x08\x02\x00\x00\x00"
    assert image_size(png) == (1200, 1200)
    sof = b"\xff\xd8" + b"\xff\xe0\x00\x04JF" + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", 500, 600) + b"\x03" + b"\x00" * 9
    assert image_size(sof) == (600, 500)
    assert same_picture(JPEG_OLD, JPEG_OLD) and not same_picture(JPEG_OLD, JPEG_NEW)


# ─── 6. The cover-art worker's decisions ─────────────────────────────────


def test_the_archive_is_asked_release_then_release_group() -> None:
    from domovoi.workers.library_cover_art import CAA_BASE, candidate_urls

    got = [u for _k, _i, u in candidate_urls(MBID_REL, MBID_RG)]
    assert got == [f"{CAA_BASE}/release/{MBID_REL}/front-1200", f"{CAA_BASE}/release/{MBID_REL}/front-500",
                   f"{CAA_BASE}/release-group/{MBID_RG}/front-1200",
                   f"{CAA_BASE}/release-group/{MBID_RG}/front-500"]
    assert candidate_urls("../../etc", None) == []


@pytest.mark.asyncio
async def test_nothing_from_the_archive_leaves_the_file_as_it_was(monkeypatch) -> None:
    from domovoi import egress
    from domovoi.workers import library_cover_art as w

    asked = []

    class R:
        status_code = 404
        content = b""

    async def fetch(url, **kw):
        asked.append(url)
        return R()

    monkeypatch.setattr(settings, "library_cover_art_enabled", True)
    monkeypatch.setattr(w, "_probe_online", lambda: True)
    monkeypatch.setattr(w, "REQUEST_INTERVAL_SEC", 0)
    monkeypatch.setattr("domovoi.net_safety.fetch_bytes", fetch)
    with egress.override_policy("always"):
        assert await w.fetch_front(MBID_REL, MBID_RG) is None
    assert len(asked) == 4


@pytest.mark.asyncio
async def test_internet_never_sends_nothing(monkeypatch) -> None:
    from domovoi import egress
    from domovoi.workers import library_cover_art as w

    monkeypatch.setattr(settings, "library_cover_art_enabled", True)
    monkeypatch.setattr(w, "_probe_online", lambda: True)
    with egress.override_policy("never"):
        assert w.allowed() is False
        with pytest.raises(w.ArchiveUnavailable):
            await w.fetch_front(MBID_REL, None)


def test_the_switch_follows_the_internet_answer() -> None:
    from domovoi.config import PROFILE_BY_NAME

    pd = PROFILE_BY_NAME["library_cover_art_enabled"]
    assert (pd.always, pd.sometimes, pd.never, pd.applies) == (True, True, False, "live")
    assert settings.library_cover_art_mode == "replace"


def test_apply_cover_writes_caches_or_skips(tmp_path, monkeypatch) -> None:
    from domovoi.cover_art_writer import cover_cache_file, current_cover
    from domovoi.workers import library_cover_art as w

    monkeypatch.setattr(settings, "cover_art_dir", str(tmp_path / "cover_art"))
    monkeypatch.setattr(settings, "library_cover_art_mode", "replace")
    music = tmp_path / "music"
    mp3 = mw.write_mp3(music / "a" / "a.mp3", [mw.APIC(mime="image/jpeg", type=3, desc="", data=JPEG_OLD)])
    out = w.apply_cover(7, str(mp3), JPEG_NEW, "image/jpeg")
    assert out["state"] == "written" and out["had_art"] is True and current_cover(mp3) == JPEG_NEW
    assert Path(out["snapshot_path"]).is_relative_to(tmp_path / "cover_art")   # never the music folder
    assert w.apply_cover(7, str(mp3), JPEG_NEW, "image/jpeg")["state"] == "same"
    # A format Domovoi doesn't write, with no art of its own: Domovoi's copy.
    m4a = music / "b" / "b.m4a"
    m4a.parent.mkdir(parents=True)
    m4a.write_bytes(mw.bare_m4a())
    out = w.apply_cover(8, str(m4a), JPEG_NEW, "image/jpeg")
    assert out["state"] == "cached" and cover_cache_file(8)[0].read_bytes() == JPEG_NEW
    # "Only add missing art": a folder image counts as art.
    monkeypatch.setattr(settings, "library_cover_art_mode", "add_missing")
    bare = music / "c" / "c.mp3"
    bare.parent.mkdir()
    bare.write_bytes(mw.silent_mp3())
    (bare.parent / "cover.jpg").write_bytes(JPEG_OLD)
    out = w.apply_cover(9, str(bare), JPEG_NEW, "image/jpeg")
    assert out["state"] == "skipped" and bare.read_bytes() == mw.silent_mp3()
