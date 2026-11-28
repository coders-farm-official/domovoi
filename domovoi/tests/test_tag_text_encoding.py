"""Tag reading never stores a lone surrogate (2026-11-28).

A row's artist was seen as "Ne\\udc90Yo feat. ...": the byte 0x90 of "Ne‐Yo"
(U+2010 is E2 80 90 in UTF-8) standing alone. Where text like that can come
from: Python's surrogateescape decoding of a POSIX file name that isn't
UTF-8 (the indexer's last-resort title and artist come from the file name),
and a tagger that wrote UTF-8 or Windows-1252 bytes into an ID3 frame
marked Latin-1, which reads back as C1 control characters. Postgres
refuses a lone surrogate outright, so the row was never written; a C1
character was stored as is.

:func:`library_indexer.clean_tag_text` decodes what can be decoded and puts
U+FFFD where nothing can, flagging the row; the re-match's dry run lists
such rows as "bad encoding".
"""

from __future__ import annotations

import pytest

from domovoi.tests import media_writer as mw
from domovoi.workers import library_indexer as li


@pytest.mark.parametrize(
    ("raw", "clean", "bad"),
    [
        # surrogateescape of UTF-8 bytes: decoded properly
        ("Ne\udce2\udc80\udc90Yo", "Ne‐Yo", False),
        # a lone byte that decodes as nothing: U+FFFD, flagged
        ("Ne\udc90Yo feat. Fabolous", "Ne�Yo feat. Fabolous", True),
        # surrogateescape of Windows-1252: decoded as that
        ("Don\udc92t", "Don’t", False),
        # UTF-8 written into a Latin-1 frame
        ("Neâ\x80\x90Yo", "Ne‐Yo", False),
        ("BeyoncÃ©", "Beyoncé", False),
        # Windows-1252 written into a Latin-1 frame
        ("Don\x92t Stop", "Don’t Stop", False),
        ("Ne\x90Yo", "Ne�Yo", True),
        # a surrogate that isn't an escaped byte
        ("a\ud800b", "a�b", True),
        # plain text, including real Latin-1, is left alone
        ("Beyoncé", "Beyoncé", False),
        ("Sigur Rós", "Sigur Rós", False),
        ("Ne‐Yo", "Ne‐Yo", False),
        (None, None, False),
    ],
)
def test_clean_tag_text(raw, clean, bad) -> None:
    got, lost = li.clean_tag_text(raw)
    assert (got, lost) == (clean, bad)
    if got is not None:
        assert not any("\ud800" <= ch <= "\udfff" for ch in got)
        got.encode("utf-8")   # storable


def test_a_latin1_frame_holding_utf8_reads_as_utf8(tmp_path) -> None:
    p = mw.write_mp3(tmp_path / "Should Be You.mp3", [
        mw.TIT2("Should Be You"),
        mw.text_frame("TPE1", "Ne‐Yo".encode("utf-8").decode("latin-1"), encoding=0),
    ])
    tags = li._read_tags(p)
    assert tags["artist"] == "Ne‐Yo" and "bad_encoding" not in tags


def test_an_undecodable_byte_is_replaced_and_flagged(tmp_path) -> None:
    p = mw.write_mp3(tmp_path / "Should Be You.mp3", [
        mw.TIT2("Should Be You"), mw.text_frame("TPE1", "Ne\x90Yo", encoding=0),
    ])
    tags = li._read_tags(p)
    assert tags["artist"] == "Ne�Yo" and tags["bad_encoding"] is True
    meta = li._resolve_metadata_for(p)
    assert meta["artist"] == "Ne�Yo" and meta["bad_encoding"] is True


def test_a_file_name_that_isnt_utf8_never_gives_a_lone_surrogate(tmp_path, monkeypatch) -> None:
    # What a POSIX file system hands Python for the name bytes "Ne\x90Yo -
    # Should Be You.mp3" (surrogateescape); written here as any name, with
    # the stem read as it would be.
    p = tmp_path / "plain.mp3"
    p.write_bytes(b"")
    monkeypatch.setattr(li, "_fallback_filename_parse",
                        lambda _path, _known=None: ("Ne\udc90Yo", "Should Be You"))
    meta = li._resolve_metadata_for(p)
    assert meta["artist"] == "Ne�Yo" and meta["title"] == "Should Be You"
    assert meta["bad_encoding"] is True
    for value in (meta["artist"], meta["title"]):
        value.encode("utf-8")


def test_clean_tags_are_not_flagged(tmp_path) -> None:
    p = mw.write_mp3(tmp_path / "x.mp3", [mw.TIT2("Perfect"), mw.text_frame("TPE1", "Ed Sheeran")])
    meta = li._resolve_metadata_for(p)
    assert (meta["title"], meta["artist"], meta["bad_encoding"]) == ("Perfect", "Ed Sheeran", False)
