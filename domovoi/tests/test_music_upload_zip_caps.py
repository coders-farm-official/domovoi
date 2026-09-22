"""Zip decompression caps on POST /api/music/library/upload (OPS-5).

An archive whose members DECLARE more than the per-file or total cap is
refused with 413 before a single member is inflated. DB-free: the endpoint
function is called directly with a starlette ``UploadFile`` (no app
lifespan, no ``requires_db``), so this can never skip. The archives are
built with a forged central directory so the test itself never has to
allocate a gigabyte.
"""

from __future__ import annotations

import io
import struct
import zipfile

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

import web.backend.api.music as music_api

# ─── building archives with forged declared sizes ─────────────────────────

_EOCD_SIG = b"PK\x05\x06"


def _zip_with_declared_size(name: str, declared: int, data: bytes = b"x" * 16) -> bytes:
    """A stored (uncompressed) single-member zip whose central-directory
    ``file_size`` field says ``declared`` instead of the truth. zipfile
    reads ``ZipInfo.file_size`` from the central directory, which is
    exactly what the cap checks; the local header is left honest so the
    file is otherwise valid."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(name, data)
    raw = bytearray(buf.getvalue())
    eocd = raw.rfind(_EOCD_SIG)
    assert eocd >= 0
    cd_offset = struct.unpack_from("<I", raw, eocd + 16)[0]
    # Central directory header: uncompressed size is the 4 bytes at +24.
    assert raw[cd_offset:cd_offset + 4] == b"PK\x01\x02"
    struct.pack_into("<I", raw, cd_offset + 24, declared)
    return bytes(raw)


def _upload(name: str, payload: bytes) -> UploadFile:
    return UploadFile(io.BytesIO(payload), filename=name, size=len(payload))


@pytest.fixture
def music_dir(tmp_path, monkeypatch):
    from domovoi.config import settings

    monkeypatch.setattr(settings, "music_dir", str(tmp_path), raising=False)
    return tmp_path


@pytest.fixture
def reindex_calls(monkeypatch):
    calls: list[str] = []

    async def _ok(path, body=None):
        calls.append(path)
        return 200, {"queued": True}

    monkeypatch.setattr(music_api, "post_admin", _ok)
    return calls


# ─── the caps ─────────────────────────────────────────────────────────────

def test_forged_declared_size_is_what_zipfile_reports() -> None:
    """Guard the test's own trick: the cap reads ``ZipInfo.file_size``."""
    big = music_api._MAX_ZIP_MEMBER_BYTES + 1
    info = zipfile.ZipFile(io.BytesIO(_zip_with_declared_size("a.mp3", big))).infolist()[0]
    assert info.file_size == big


async def test_member_over_the_per_file_cap_returns_413_before_extraction(
    music_dir, reindex_calls, monkeypatch
) -> None:
    reads: list[str] = []
    real_read = zipfile.ZipFile.read

    def spy_read(self, member, *a, **kw):
        reads.append(getattr(member, "filename", str(member)))
        return real_read(self, member, *a, **kw)

    monkeypatch.setattr(zipfile.ZipFile, "read", spy_read)
    payload = _zip_with_declared_size("huge.flac", music_api._MAX_ZIP_MEMBER_BYTES + 1)

    with pytest.raises(HTTPException) as exc:
        await music_api.upload_to_library(files=[_upload("album.zip", payload)])

    assert exc.value.status_code == 413
    assert "huge.flac" in exc.value.detail
    assert reads == [], "a member was inflated before the cap fired"
    assert not (music_dir / "uploads").exists() or not any((music_dir / "uploads").iterdir())
    assert reindex_calls == []


async def test_members_over_the_total_cap_return_413(music_dir, reindex_calls) -> None:
    """Each member sits under the per-file cap; together they declare more
    than the archive cap."""
    per = music_api._MAX_ZIP_MEMBER_BYTES
    n = music_api._MAX_ZIP_TOTAL_BYTES // per + 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for i in range(n):
            zf.writestr(f"disc{i}.wav", b"x")
    raw = bytearray(buf.getvalue())
    # Forge every central-directory entry's uncompressed size to the per-file cap.
    pos = raw.find(b"PK\x01\x02")
    while pos >= 0:
        struct.pack_into("<I", raw, pos + 24, per)
        pos = raw.find(b"PK\x01\x02", pos + 4)

    with pytest.raises(HTTPException) as exc:
        await music_api.upload_to_library(files=[_upload("box.zip", bytes(raw))])

    assert exc.value.status_code == 413
    assert "decompressed" in exc.value.detail
    assert reindex_calls == []


async def test_too_many_members_still_returns_413(music_dir, reindex_calls) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for i in range(music_api._MAX_ZIP_MEMBERS + 1):
            zf.writestr(f"t{i}.mp3", b"x")
    with pytest.raises(HTTPException) as exc:
        await music_api.upload_to_library(files=[_upload("many.zip", buf.getvalue())])
    assert exc.value.status_code == 413
    assert "file cap" in exc.value.detail


async def test_archive_within_the_caps_is_extracted(music_dir, reindex_calls) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.mp3", b"aaa")
        zf.writestr("cover.jpg", b"img")
    result = await music_api.upload_to_library(files=[_upload("album.zip", buf.getvalue())])
    assert result.saved == 1 and result.files == ["a.mp3"]
    assert (music_dir / "uploads" / "a.mp3").read_bytes() == b"aaa"
    assert reindex_calls == ["/v1/admin/library/reindex"]


def test_caps_are_generous_enough_for_real_audio() -> None:
    """The point is bombs, not boxed sets: an hour of 96 kHz/24-bit stereo
    WAV (~1.04 GB) is the ceiling per file, and four of them per archive."""
    assert music_api._MAX_ZIP_MEMBER_BYTES >= 1 * 1024 ** 3
    assert music_api._MAX_ZIP_TOTAL_BYTES >= 4 * music_api._MAX_ZIP_MEMBER_BYTES
