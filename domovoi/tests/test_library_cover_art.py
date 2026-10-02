"""Library cover art — ``GET /api/music/library/{track_id}/cover``.

Owner decision 2026-10-02: the cover comes from the file itself — the
picture built into it, else an image in its album folder (cover / folder /
front / album, any case, jpg / jpeg / png / webp) — read on request. No
online lookup, no extraction job, no table, no stored copies.

DB-FREE by construction: the one query the route makes (track id → file
path) is answered by a fake ``session_scope``, and the REAL web app is
driven over ASGI, middleware and all, so nothing here can hide behind
``requires_db``. The media are generated: silent MPEG frames under an ID3
tag, a bare FLAC STREAMINFO, a minimal MP4 box tree — mutagen writes the
pictures into them, exactly as a tagger would.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import struct
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi.config import settings
from domovoi.tests.auth_testkit import install_fake_db, web_app
from web.backend.api import music as music_api

needs_mutagen = pytest.mark.skipif(
    importlib.util.find_spec("mutagen") is None,
    reason="mutagen (the [real-clients] extra) writes the tagged fixtures",
)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"j" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"p" * 200
WEBP = b"RIFF" + struct.pack("<I", 200) + b"WEBPVP8 " + b"w" * 192
BACK_JPEG = b"\xff\xd8\xff\xe1" + b"back cover" + b"b" * 100

# The Beelink's library really does name files like this (a full-width
# colon and a double space) — the route must not care.
ODD_NAME = "B-Boy Bouillabaisse：  A Year And A Day [3tGbSk9Oi3I].mp3"


# ── generated media ──────────────────────────────────────────────────

def silent_mp3(frames: int = 8) -> bytes:
    """MPEG-1 Layer III, 128 kbps, 44.1 kHz, mono, all-zero side info: a
    real (silent) stream mutagen syncs to."""
    header = bytes([0xFF, 0xFB, 0x90, 0xC4])
    frame_len = 144 * 128000 // 44100
    return (header + b"\x00" * (frame_len - 4)) * frames


def _atom(name: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + name + payload


def _full_atom(name: bytes, flags: int, payload: bytes) -> bytes:
    return _atom(name, struct.pack(">I", flags) + payload)


def bare_m4a() -> bytes:
    """ftyp + a moov with one sound track + an empty mdat."""
    mvhd = _full_atom(b"mvhd", 0, struct.pack(">IIII", 0, 0, 1000, 1000) + b"\x00" * 80)
    mdhd = _full_atom(b"mdhd", 0, struct.pack(">IIIIHH", 0, 0, 44100, 44100, 0, 0))
    hdlr = _full_atom(b"hdlr", 0, b"\x00" * 4 + b"soun" + b"\x00" * 13)
    trak = _atom(b"trak", _full_atom(b"tkhd", 7, b"\x00" * 80) + _atom(b"mdia", mdhd + hdlr))
    return (_atom(b"ftyp", b"M4A \x00\x00\x00\x00M4A mp42isom")
            + _atom(b"moov", mvhd + trak) + _atom(b"mdat", b"\x00" * 16))


def bare_flac() -> bytes:
    """``fLaC`` and one (last) STREAMINFO block: 44.1 kHz, mono, 16 bit."""
    info = struct.pack(">HH", 4096, 4096) + b"\x00" * 6
    info += ((44100 << 44) | (15 << 36)).to_bytes(8, "big") + b"\x00" * 16
    return b"fLaC" + b"\x80" + len(info).to_bytes(3, "big") + info


def _ogg(packets: list[tuple[bytes, int]]) -> bytes:
    """One logical Ogg stream, a packet per page (mutagen writes the CRCs)."""
    from mutagen.ogg import OggPage

    out = b""
    for seq, (packet, granule) in enumerate(packets):
        page = OggPage()
        page.serial, page.sequence, page.position = 0x0D0E, seq, granule
        page.first, page.last = seq == 0, seq == len(packets) - 1
        page.packets = [packet]
        out += page.write()
    return out


def bare_ogg(kind: str) -> bytes:
    """An Opus or a Vorbis stream: identification header, an empty comment
    header (and Vorbis' setup header), then one audio packet."""
    if kind == "opus":
        head = b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", 312, 48000, 0, 0)
        tags = b"OpusTags" + struct.pack("<I", 4) + b"test" + struct.pack("<I", 0)
        return _ogg([(head, 0), (tags, 0), (b"\xf8\xff\xfe", 960)])
    ident = b"\x01vorbis" + struct.pack("<IBIiii", 0, 1, 44100, 0, 128000, 0) + bytes([0xB8, 1])
    comment = b"\x03vorbis" + struct.pack("<I", 4) + b"test" + struct.pack("<I", 0) + b"\x01"
    return _ogg([(ident, 0), (comment, 0), (b"\x05vorbis" + b"\x00" * 8, 0), (b"\x00" * 4, 44100)])


def tagged_ogg(path: Path, kind: str, comments: dict[str, list[str]]) -> Path:
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bare_ogg(kind))
    f = (OggOpus if kind == "opus" else OggVorbis)(str(path))
    for field, values in comments.items():
        f[field] = values
    f.save()
    return path


def tagged_mp3(path: Path, pictures: list[tuple[int, str, bytes]]) -> Path:
    from mutagen.id3 import APIC, ID3

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(silent_mp3())
    tags = ID3()
    for i, (ptype, mime, data) in enumerate(pictures):
        tags.add(APIC(encoding=3, mime=mime, type=ptype, desc=f"pic{i}", data=data))
    tags.save(str(path))
    return path


def tagged_m4a(path: Path, data: bytes, png: bool) -> Path:
    from mutagen.mp4 import MP4, MP4Cover

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bare_m4a())
    f = MP4(str(path))
    if f.tags is None:
        f.add_tags()
    fmt = MP4Cover.FORMAT_PNG if png else MP4Cover.FORMAT_JPEG
    f.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
    f.save()
    return path


def tagged_flac(path: Path, data: bytes, mime: str) -> Path:
    from mutagen.flac import FLAC, Picture

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bare_flac())
    f = FLAC(str(path))
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, mime, data
    f.add_picture(pic)
    f.save()
    return path


# ── the harness: a real web app, a fake library table ──────────────

class _Row:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class Library:
    """MUSIC_DIR in tmp plus the ``library_tracks`` rows the route reads."""

    def __init__(self, root: Path):
        self.root = root
        self.rows: dict[int, str] = {}

    def add(self, path: Path | str) -> int:
        track_id = len(self.rows) + 1
        self.rows[track_id] = str(path)
        return track_id


@pytest.fixture
def library(tmp_path, monkeypatch) -> Library:
    music = tmp_path / "Music"
    music.mkdir()
    monkeypatch.setattr(settings, "music_dir", str(music))
    lib = Library(music)

    class _Session:
        async def execute(self, clause, params=None):
            sql = " ".join(str(clause).split())
            assert sql == "SELECT file_path FROM library_tracks WHERE id = :id", sql
            path = lib.rows.get(params["id"])
            return _Row((path,) if path is not None else None)

    @asynccontextmanager
    async def scope():
        yield _Session()

    monkeypatch.setattr(music_api, "session_scope", scope)
    # A CLAIMED install with a household token: the cover must still answer
    # a caller holding neither (an <img> tag, the phone's lock screen).
    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="device-token")
    return lib


def get(track_id: int, headers: dict | None = None):
    async def go():
        async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as c:
            return await c.get(f"/api/music/library/{track_id}/cover", headers=headers or {})
    return asyncio.run(go())


def files_under(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


# ── embedded pictures ────────────────────────────────────────────────

@needs_mutagen
def test_mp3_serves_its_front_cover_over_an_earlier_picture(library):
    mp3 = tagged_mp3(library.root / "Beastie Boys - Greatest Hits" / ODD_NAME,
                     [(0, "image/png", PNG), (4, "image/jpeg", BACK_JPEG), (3, "image/jpeg", JPEG)])
    r = get(library.add(mp3))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == JPEG


@needs_mutagen
@pytest.mark.parametrize("order", ["other-first", "back-first"])
def test_without_a_front_cover_an_other_picture_beats_a_back_cover(library, order):
    pics = [(0, "image/png", PNG), (4, "image/jpeg", BACK_JPEG)]
    mp3 = tagged_mp3(library.root / "a" / "t.mp3", pics if order == "other-first" else pics[::-1])
    r = get(library.add(mp3))
    assert (r.status_code, r.headers["content-type"], r.content) == (200, "image/png", PNG)


@needs_mutagen
def test_a_back_cover_alone_still_counts(library):
    mp3 = tagged_mp3(library.root / "a" / "t.mp3", [(4, "image/jpeg", BACK_JPEG)])
    assert get(library.add(mp3)).content == BACK_JPEG


@needs_mutagen
def test_content_type_comes_from_the_bytes_not_the_tag(library):
    # Taggers write "image/jpg", "JPG" or nothing at all.
    mp3 = tagged_mp3(library.root / "a" / "t.mp3", [(3, "", PNG)])
    r = get(library.add(mp3))
    assert (r.status_code, r.headers["content-type"], r.content) == (200, "image/png", PNG)


@needs_mutagen
@pytest.mark.parametrize("png", [True, False], ids=["png", "jpeg"])
def test_m4a_covr(library, png):
    data = PNG if png else JPEG
    m4a = tagged_m4a(library.root / "Album" / "01 Track.m4a", data, png=png)
    r = get(library.add(m4a))
    assert r.status_code == 200
    assert r.headers["content-type"] == ("image/png" if png else "image/jpeg")
    assert r.content == data


@needs_mutagen
def test_flac_picture_block(library):
    flac = tagged_flac(library.root / "Album" / "01.flac", WEBP, "image/webp")
    r = get(library.add(flac))
    assert (r.status_code, r.headers["content-type"], r.content) == (200, "image/webp", WEBP)


@needs_mutagen
@pytest.mark.parametrize("kind", ["opus", "vorbis"])
def test_ogg_metadata_block_picture(library, kind):
    """Ogg Opus / Vorbis carry the cover as a base64 FLAC picture block in
    METADATA_BLOCK_PICTURE (the live library has .ogg files)."""
    import base64

    from mutagen.flac import Picture

    back, front = Picture(), Picture()
    back.type, back.mime, back.data = 4, "image/jpeg", BACK_JPEG
    front.type, front.mime, front.data = 3, "image/png", PNG
    blocks = [base64.b64encode(p.write()).decode("ascii") for p in (back, front)]
    path = tagged_ogg(library.root / "Ogg" / f"01.{'opus' if kind == 'opus' else 'ogg'}", kind,
                      {"metadata_block_picture": blocks})
    r = get(library.add(path))
    assert (r.status_code, r.headers["content-type"], r.content) == (200, "image/png", PNG)


@needs_mutagen
def test_ogg_legacy_coverart_field(library):
    """The older bare-base64 COVERART comment, still written by some taggers;
    an unreadable METADATA_BLOCK_PICTURE beside it is skipped, not fatal."""
    import base64

    path = tagged_ogg(library.root / "Ogg" / "02.ogg", "vorbis",
                      {"metadata_block_picture": ["!!not base64!!"], "coverart": [base64.b64encode(JPEG).decode()]})
    r = get(library.add(path))
    assert (r.status_code, r.headers["content-type"], r.content) == (200, "image/jpeg", JPEG)


@needs_mutagen
def test_embedded_art_wins_over_the_folder_image(library):
    mp3 = tagged_mp3(library.root / "Album" / "t.mp3", [(3, "image/jpeg", JPEG)])
    (mp3.parent / "cover.png").write_bytes(PNG)
    assert get(library.add(mp3)).content == JPEG


def test_wma_picture_unpacks():
    """``WM/Picture``: type byte, uint32 LE length, UTF-16LE MIME and
    description (NUL-terminated), then the image."""
    raw = (bytes([3]) + len(JPEG).to_bytes(4, "little")
           + "image/jpeg".encode("utf-16-le") + b"\x00\x00"
           + "Front".encode("utf-16-le") + b"\x00\x00" + JPEG)
    assert music_api._asf_picture(raw) == (3, JPEG)
    assert music_api._asf_picture(raw[:-1]) is None      # truncated image
    assert music_api._asf_picture(b"\x03") is None


# ── the album-folder fallback ────────────────────────────────────────

def test_folder_image_when_the_file_has_none(library):
    album = library.root / "Beastie Boys - Greatest Hits"
    album.mkdir()
    (album / ODD_NAME).write_bytes(silent_mp3())
    (album / "Cover.JPG").write_bytes(JPEG)          # any case
    (album / "folder.png").write_bytes(PNG)          # lower preference
    (album / "back.jpg").write_bytes(BACK_JPEG)      # not a cover name
    r = get(library.add(album / ODD_NAME))
    assert (r.status_code, r.headers["content-type"], r.content) == (200, "image/jpeg", JPEG)
    assert r.headers["content-length"] == str(len(JPEG))


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["album.webp", "front.png", "folder.jpeg"], "folder.jpeg"),
        (["FRONT.PNG", "Album.jpg"], "FRONT.PNG"),
        (["album.webp"], "album.webp"),
        (["cover.png", "cover.jpeg", "cover.jpg"], "cover.jpg"),
    ],
)
def test_folder_name_preference(library, names, expected):
    album = library.root / "Album"
    album.mkdir()
    (album / "t.mp3").write_bytes(silent_mp3())
    bodies = {}
    for i, name in enumerate(names):
        body = (WEBP if name.lower().endswith(".webp") else PNG if name.lower().endswith(".png") else JPEG) + bytes([i])
        (album / name).write_bytes(body)
        bodies[name] = body
    r = get(library.add(album / "t.mp3"))
    assert r.status_code == 200 and r.content == bodies[expected]


def test_only_the_tracks_own_folder_counts(library):
    (library.root / "cover.jpg").write_bytes(JPEG)           # the parent's
    album = library.root / "Album" / "Disc 1"
    album.mkdir(parents=True)
    (album / "t.mp3").write_bytes(silent_mp3())
    assert get(library.add(album / "t.mp3")).status_code == 404


def test_a_folder_image_that_is_not_an_image_is_skipped(library):
    album = library.root / "Album"
    album.mkdir()
    (album / "t.mp3").write_bytes(silent_mp3())
    (album / "cover.jpg").write_bytes(b"<html><script>alert(1)</script></html>")
    (album / "folder.jpg").write_bytes(JPEG)
    r = get(library.add(album / "t.mp3"))
    assert (r.status_code, r.content) == (200, JPEG)


def test_a_folder_image_that_is_not_a_regular_file_is_never_opened(library, monkeypatch):
    """A cover.jpg that is a FIFO (or a device) would block the worker
    thread on open() forever; only regular files are read. Faked so it runs
    where mkfifo doesn't exist."""
    album = library.root / "Album"
    album.mkdir()
    (album / "t.mp3").write_bytes(silent_mp3())
    (album / "cover.jpg").write_bytes(JPEG)              # stands in for the FIFO
    (album / "folder.png").write_bytes(PNG)
    real_stat, real_open = Path.stat, Path.open

    def stat(self, *a, **kw):
        st = real_stat(self, *a, **kw)
        if self.name == "cover.jpg":
            return os.stat_result((0o010644, *tuple(st)[1:6], 4096, *tuple(st)[7:10]))
        return st

    def open_(self, *a, **kw):
        assert self.name != "cover.jpg", "opened a FIFO"
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "open", open_)
    found = music_api._folder_image(album / "t.mp3", library.root.resolve())
    assert found is not None and found[0].name == "folder.png"


# ── no art ───────────────────────────────────────────────────────────

def test_no_art_is_a_quiet_cacheable_404_and_nothing_is_stored(library, tmp_path):
    album = library.root / "No Art"
    album.mkdir()
    (album / "t.mp3").write_bytes(silent_mp3())
    (album / "notes.txt").write_text("liner notes")
    before = files_under(tmp_path)
    r = get(library.add(album / "t.mp3"))
    assert r.status_code == 404
    assert "etag" not in r.headers
    assert r.headers["cache-control"] == "public, max-age=300"
    assert files_under(tmp_path) == before      # no sentinel, no copy, anywhere


@needs_mutagen
def test_svg_or_unknown_picture_bytes_are_never_served(library):
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    mp3 = tagged_mp3(library.root / "a" / "t.mp3", [(3, "image/svg+xml", svg), (0, "image/jpeg", b"not really")])
    assert get(library.add(mp3)).status_code == 404


def test_unknown_track_and_missing_file_are_404(library):
    assert get(4242).status_code == 404
    assert get(library.add(library.root / "gone.mp3")).status_code == 404


# ── the size cap ─────────────────────────────────────────────────────

@needs_mutagen
def test_oversized_embedded_picture_falls_back_to_the_folder(library, monkeypatch):
    monkeypatch.setattr(music_api, "COVER_MAX_BYTES", 1024)
    big = JPEG + b"x" * 2048
    mp3 = tagged_mp3(library.root / "Album" / "t.mp3", [(3, "image/jpeg", big)])
    tid = library.add(mp3)
    assert get(tid).status_code == 404                     # nothing small enough
    (mp3.parent / "cover.png").write_bytes(PNG)
    r = get(tid)
    assert (r.status_code, r.content) == (200, PNG)


@needs_mutagen
def test_oversized_front_cover_yields_to_a_smaller_picture(library, monkeypatch):
    monkeypatch.setattr(music_api, "COVER_MAX_BYTES", 1024)
    mp3 = tagged_mp3(library.root / "a" / "t.mp3",
                     [(3, "image/jpeg", JPEG + b"x" * 2048), (0, "image/png", PNG)])
    assert get(library.add(mp3)).content == PNG


def test_oversized_folder_image_is_ignored(library, monkeypatch):
    monkeypatch.setattr(music_api, "COVER_MAX_BYTES", 1024)
    album = library.root / "Album"
    album.mkdir()
    (album / "t.mp3").write_bytes(silent_mp3())
    (album / "cover.jpg").write_bytes(JPEG + b"x" * 2048)
    tid = library.add(album / "t.mp3")
    assert get(tid).status_code == 404
    (album / "front.png").write_bytes(PNG)                 # a smaller one further down the list
    assert get(tid).content == PNG


def test_the_default_cap_is_eight_mib():
    assert music_api.COVER_MAX_BYTES == 8 * 1024 * 1024


# ── containment ──────────────────────────────────────────────────────

@needs_mutagen
def test_a_row_outside_music_dir_is_refused(library, tmp_path):
    secret = tagged_mp3(tmp_path / "elsewhere" / "secret.mp3", [(3, "image/jpeg", JPEG)])
    for path in (secret, library.root / ".." / "elsewhere" / "secret.mp3"):
        r = get(library.add(path))
        assert r.status_code == 400, path
        assert JPEG not in r.content


def test_a_folder_image_symlinked_out_of_the_library_is_not_followed(library, tmp_path):
    outside = tmp_path / "private.jpg"
    outside.write_bytes(JPEG)
    album = library.root / "Album"
    album.mkdir()
    (album / "t.mp3").write_bytes(silent_mp3())
    try:
        os.symlink(outside, album / "cover.jpg")
    except (OSError, NotImplementedError) as e:  # Windows without symlink rights
        pytest.skip(f"cannot create a symlink here: {e}")
    r = get(library.add(album / "t.mp3"))
    assert r.status_code == 404 and JPEG not in r.content


def test_folder_image_containment_on_any_platform(library, tmp_path, monkeypatch):
    """The same guard without needing symlink rights: a cover.jpg that
    RESOLVES outside MUSIC_DIR (what a symlink does) is passed over for the
    next candidate, never read."""
    outside = tmp_path / "private.jpg"
    outside.write_bytes(JPEG)
    album = library.root / "Album"
    album.mkdir()
    (album / "t.mp3").write_bytes(silent_mp3())
    (album / "cover.jpg").write_bytes(b"stand-in for a link")
    (album / "folder.png").write_bytes(PNG)
    real_resolve = Path.resolve

    def resolve(self, strict=False):
        if self.name == "cover.jpg":
            return real_resolve(outside, strict=strict)
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    root = real_resolve(library.root)
    found = music_api._folder_image(album / "t.mp3", root)
    assert found is not None and found[0].name == "folder.png"
    (album / "folder.png").unlink()
    assert music_api._folder_image(album / "t.mp3", root) is None


# ── caching: strong ETag, Cache-Control, 304 ─────────────────────────

def _album_with_folder_art(library) -> tuple[int, Path]:
    album = library.root / "Album"
    album.mkdir()
    (album / "t.mp3").write_bytes(silent_mp3())
    (album / "cover.jpg").write_bytes(JPEG)
    return library.add(album / "t.mp3"), album


def test_strong_etag_and_cache_control(library):
    tid, _ = _album_with_folder_art(library)
    r = get(tid)
    etag = r.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"') and not etag.startswith("W/")
    assert r.headers["cache-control"] == "public, max-age=86400"
    assert get(tid).headers["etag"] == etag                # stable while nothing changes


@pytest.mark.parametrize("form", ["{e}", "W/{e}", '"other", {e}', "*"])
def test_if_none_match_is_a_304_with_no_body(library, form):
    tid, _ = _album_with_folder_art(library)
    etag = get(tid).headers["etag"]
    r = get(tid, {"If-None-Match": form.format(e=etag)})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag
    assert r.headers["cache-control"] == "public, max-age=86400"


def test_a_stale_etag_gets_the_picture(library):
    tid, _ = _album_with_folder_art(library)
    r = get(tid, {"If-None-Match": '"not-it"'})
    assert (r.status_code, r.content) == (200, JPEG)


def test_the_etag_follows_the_audio_file_and_the_folder_image(library):
    tid, album = _album_with_folder_art(library)
    first = get(tid).headers["etag"]

    audio = album / "t.mp3"
    st = audio.stat()
    os.utime(audio, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))   # re-tagged
    second = get(tid).headers["etag"]
    assert second != first
    assert get(tid, {"If-None-Match": first}).status_code == 200

    (album / "cover.jpg").write_bytes(JPEG + b"new art")                   # cover swapped
    r = get(tid, {"If-None-Match": second})
    assert r.status_code == 200 and r.content == JPEG + b"new art"
    assert r.headers["etag"] not in (first, second)


def test_a_matching_etag_is_answered_before_any_tag_is_read(library, monkeypatch):
    tid, _ = _album_with_folder_art(library)
    etag = get(tid).headers["etag"]

    def boom(path):
        raise AssertionError("parsed tags for a 304")

    monkeypatch.setattr(music_api, "_read_embedded_cover", boom)
    assert get(tid, {"If-None-Match": etag}).status_code == 304


# ── off the event loop ───────────────────────────────────────────────

def test_the_tag_read_and_the_folder_scan_run_off_the_event_loop(library, monkeypatch):
    tid, _ = _album_with_folder_art(library)
    seen: dict[str, int] = {}
    real_probe, real_read = music_api._probe_cover, music_api._read_embedded_cover

    def probe(*a):
        seen["probe"] = threading.get_ident()
        return real_probe(*a)

    def read(*a):
        seen["read"] = threading.get_ident()
        return real_read(*a)

    monkeypatch.setattr(music_api, "_probe_cover", probe)
    monkeypatch.setattr(music_api, "_read_embedded_cover", read)

    async def go():
        seen["loop"] = threading.get_ident()
        async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test") as c:
            return await c.get(f"/api/music/library/{tid}/cover")

    assert asyncio.run(go()).status_code == 200
    assert seen["probe"] != seen["loop"] and seen["read"] != seen["loop"]


# ── the tier ─────────────────────────────────────────────────────────

def _route(path: str):
    return next(r for r in web_app.routes
                if getattr(r, "path", None) == path and "GET" in getattr(r, "methods", ()))


def test_same_tier_as_the_library_listing():
    """Open, exactly like ``GET /api/music/library`` (and ``/audio``): the
    dashboard's <img> tags and the phone's media session fetch the picture
    by URL and can't attach a credential."""
    listing = _route("/api/music/library")
    cover = _route("/api/music/library/{track_id}/cover")
    deps = lambda r: sorted(d.call.__name__ for d in r.dependant.dependencies)  # noqa: E731
    assert deps(cover) == deps(listing) == []


@needs_mutagen
def test_a_claimed_install_serves_it_without_credentials(library):
    mp3 = tagged_mp3(library.root / "a" / "t.mp3", [(3, "image/jpeg", JPEG)])
    assert get(library.add(mp3)).status_code == 200      # no token, no cookie, no bearer
