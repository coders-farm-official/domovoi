"""Pictures from users are opened safely (2026-10-16 delta security review
S7, and round 3's OPS-16): domovoi/image_safety.py, the chat's pictures
(web/backend/api/chat.py) and the picture libraries' thumbnails
(web/backend/api/images.py).

The review's proofs, as regressions:

* a 164 KB PNG of 169 megapixels decoded to 350 MB in the web process
  (Pillow only warns below ~179 MP, and Domovoi never set its limit);
* an EPS named ``cat.png`` was opened by Pillow as EPS, which runs
  Ghostscript where it is installed.

Now ``Image.MAX_IMAGE_PIXELS`` is 40 million in both processes, every
decode tries only the formats it expects and refuses more than 40 MP from
the header, the chat upload refuses what isn't such a picture, and the
decodes run in the bounded ``pictures`` pool. DB-free.
"""

from __future__ import annotations

import asyncio
import inspect
import io
import json
import struct
import threading
import tracemalloc
import zlib
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import EpsImagePlugin, Image
from starlette.datastructures import UploadFile

from domovoi import executors, image_safety
from web.backend.api import chat as chat_api
from web.backend.api import images as images_api

TOKEN = "0123456789abcdef0123456789abcdef"
EPS = (b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 4000 4000\n"
       b"newpath 0 0 moveto 10 10 lineto stroke\nshowpage\n%%EOF\n")


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def bomb_png(width: int, height: int) -> bytes:
    """A black greyscale PNG of width x height, a few hundred KB however
    many pixels: built row by row, never held decoded."""
    row = b"\x00" * (width + 1)
    co = zlib.compressobj(9)
    data = b"".join(co.compress(row) for _ in range(height)) + co.flush()
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + _chunk(b"IDAT", data) + _chunk(b"IEND", b""))


def small(fmt: str, size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    im = Image.new("RGB", size, (10, 120, 200))
    (im.convert("P") if fmt == "GIF" else im).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
def no_ghostscript(monkeypatch) -> list:
    """Every way Pillow reaches EPS: recorded. The list must stay empty."""
    calls: list = []
    real_open = EpsImagePlugin.EpsImageFile._open

    def spy_open(self, *a, **kw):
        calls.append("EpsImageFile._open")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(EpsImagePlugin.EpsImageFile, "_open", spy_open)
    monkeypatch.setattr(EpsImagePlugin, "Ghostscript", lambda *a, **kw: calls.append("Ghostscript"))
    return calls


@pytest.fixture
def uploads(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "chat_uploads"
    d.mkdir()
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", d)
    return d


# ─── the limit, in both processes ────────────────────────────────────────


def test_pillows_limit_is_set_explicitly() -> None:
    assert image_safety.MAX_IMAGE_PIXELS == 40_000_000
    assert Image.MAX_IMAGE_PIXELS == 40_000_000


def test_both_processes_set_it_at_startup(monkeypatch) -> None:
    from domovoi import main as core_main
    from web.backend import main as web_main

    for lifespan in (core_main.lifespan, web_main.lifespan):
        assert "image_safety.configure_pillow()" in inspect.getsource(lifespan)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10**12)
    image_safety.configure_pillow()
    assert Image.MAX_IMAGE_PIXELS == 40_000_000


@pytest.mark.parametrize("formats", [image_safety.PICTURE_FORMATS, image_safety.LIBRARY_FORMATS])
def test_no_format_that_runs_a_program_is_ever_tried(formats) -> None:
    assert not {"EPS", "PS", "PDF", "WMF", "EMF", "MPEG", "GRIB", "BUFR", "HDF5", "FITS"} & set(formats)


# ─── the chat: what the model gets ───────────────────────────────────────


@pytest.mark.parametrize("size", [(7000, 7000), (13000, 13000)])
def test_a_picture_over_40_megapixels_is_refused_before_it_is_decoded(uploads, size) -> None:
    """49 MP (Pillow alone would only warn) and the review's 169 MP (which
    decoded to 350 MB): refused from the header, nothing decoded, nothing
    sent to the model."""
    src = uploads / f"{TOKEN}.png"
    src.write_bytes(bomb_png(*size))
    assert src.stat().st_size < 1_000_000
    tracemalloc.start()
    try:
        with pytest.raises(image_safety.PictureRefused) as e:
            chat_api._vision_bytes(src)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert e.value.too_large
    assert peak < 8 * 1024 * 1024, f"{peak / 2**20:.0f} MiB"
    assert [p.name for p in uploads.iterdir()] == [src.name]          # no copy made


def test_an_eps_named_png_never_reaches_ghostscript(uploads, no_ghostscript) -> None:
    """The review's ``cat.png``: an upload from before the check below. It
    is not a picture the chat decodes, so it goes to the model as it is
    (as one Pillow can't read always did), and EPS is never tried."""
    src = uploads / f"{TOKEN}.png"
    src.write_bytes(EPS)
    assert chat_api._vision_bytes(src) == EPS
    assert no_ghostscript == []


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP", "BMP"])
def test_every_picture_kind_the_chat_takes_still_works(uploads, fmt) -> None:
    src = uploads / f"{TOKEN}.{fmt.lower()}"
    src.write_bytes(small(fmt, (2000, 1000)))
    out = Image.open(io.BytesIO(chat_api._vision_bytes(src)))
    assert out.size == (1280, 640)


async def test_the_models_pictures_are_decoded_in_the_pictures_pool(uploads, monkeypatch) -> None:
    (uploads / f"{TOKEN}.png").write_bytes(small("PNG", (2000, 1000)))
    big = "fedcba9876543210fedcba9876543210"
    (uploads / f"{big}.png").write_bytes(bomb_png(7000, 7000))
    seen: list[str] = []
    real = chat_api._vision_bytes

    def spy(src):
        seen.append(threading.current_thread().name)
        return real(src)

    monkeypatch.setattr(chat_api, "_vision_bytes", spy)

    class _Rows:
        def all(self):
            # (id, role, content, images), as _history_for_model selects them.
            return [(1, "user", "what is this?", [{"token": TOKEN}, {"token": big}])]

    class _Session:
        async def execute(self, *a, **kw):
            return _Rows()

    @asynccontextmanager
    async def scope():
        yield _Session()

    monkeypatch.setattr(chat_api, "session_scope", scope)
    history = await chat_api._history_for_model(1)
    assert len(history[0]["images"]) == 1                     # the 49 MP one left out
    assert seen and all(t.startswith("domovoi-pictures") for t in seen)


# ─── the chat: the upload ────────────────────────────────────────────────


async def _upload(name: str, data: bytes) -> dict:
    return await chat_api.upload(UploadFile(io.BytesIO(data), filename=name))


async def _refused(name: str, data: bytes) -> tuple[int, dict]:
    """The status and JSON body of an upload the route refuses."""
    r = await _upload(name, data)
    assert isinstance(r, JSONResponse), r
    return r.status_code, json.loads(r.body)


@pytest.mark.parametrize("name,data", [
    ("cat.png", EPS),
    ("notes.png", b"just some text, named like a picture"),
    ("cat.jpg", b"\xff\xd8\xff\xe0 but nothing after"),
])
async def test_an_upload_that_isnt_a_picture_is_refused(uploads, no_ghostscript, name, data) -> None:
    status, body = await _refused(name, data)
    assert status == 415 and "JPEG, PNG, GIF, WebP or BMP" in body["detail"]
    assert body["code"] == "unsupported_format" and body["format"] is None
    assert list(uploads.iterdir()) == [] and no_ghostscript == []


async def test_an_upload_over_40_megapixels_is_refused_with_its_size(uploads) -> None:
    status, body = await _refused("huge.png", bomb_png(13000, 13000))
    assert status == 413
    assert "megapixels" in body["detail"] and "at most 40" in body["detail"]
    assert body["code"] == "too_many_pixels" and body["max_megapixels"] == 40
    assert list(uploads.iterdir()) == []


# ─── the upload's refusal, for the clients to word (2026-10-16) ──────────
# Many phones save camera photos as HEIC (some as AVIF). The app converts
# them; the dashboard can't, so it words the refusal from `code` and
# `format`. The bytes below are each format's real opening box/marker.

def _ftyp(major: bytes, *compatible: bytes) -> bytes:
    body = major + b"\x00\x00\x00\x00" + b"".join(compatible)
    return (8 + len(body)).to_bytes(4, "big") + b"ftyp" + body + b"\x00\x00\x00\x08free" + b"\x00" * 64


HEIC = _ftyp(b"heic", b"mif1", b"heic")
HEIF = _ftyp(b"mif1", b"mif1")
AVIF = _ftyp(b"avif", b"mif1", b"avif", b"miaf")
# Samsung's and others' HEIC: a generic major brand, HEVC among the compatible ones.
HEIC_MIF1_MAJOR = _ftyp(b"mif1", b"mif1", b"heic", b"miaf")
AVIF_SEQUENCE = _ftyp(b"avis", b"avis", b"msf1")


@pytest.mark.parametrize("name,data,fmt,word", [
    ("IMG_0001.HEIC", HEIC, "HEIC", "HEIC"),
    ("photo.jpg", HEIC, "HEIC", "HEIC"),          # what the app used to send
    ("photo.jpg", HEIC_MIF1_MAJOR, "HEIC", "HEIC"),
    ("x.heif", HEIF, "HEIF", "HEIF"),
    ("pic.avif", AVIF, "AVIF", "AVIF"),
    ("pic.png", AVIF_SEQUENCE, "AVIF", "AVIF"),
    ("scan.tif", b"II*\x00" + b"\x00" * 60, "TIFF", "TIFF"),
    ("p.jxl", b"\x00\x00\x00\x0cJXL \r\n\x87\n" + b"\x00" * 20, "JPEG XL", "JPEG XL"),
])
async def test_a_camera_format_is_refused_by_name(uploads, name, data, fmt, word) -> None:
    status, body = await _refused(name, data)
    assert status == 415
    assert body == {
        "code": "unsupported_format", "format": fmt,
        "detail": f"That picture is in {word} format, which the chat can't read. "
                  "Send a JPEG, PNG, GIF, WebP or BMP.",
    }
    assert list(uploads.iterdir()) == []


@pytest.mark.parametrize("name,data", [
    ("cat.pdf", b"%PDF-1.7\n"),
    ("nothing", b""),
    # An allowed kind under a name the chat doesn't take is not named as
    # "its format" (that would read "JPEG, which the chat can't read").
    ("cat.jfif", small("JPEG")),
])
async def test_an_unnamed_kind_is_refused_plainly(uploads, name, data) -> None:
    status, body = await _refused(name, data)
    assert status == 415 and body["code"] == "unsupported_format" and body["format"] is None
    assert body["detail"] == ("That file isn't a picture the chat can read. "
                              "Send a JPEG, PNG, GIF, WebP or BMP.")


def test_the_file_cap_is_20_mb() -> None:
    assert chat_api._MAX_UPLOAD_BYTES == 20 * 2**20


async def test_a_file_over_the_cap_is_refused_with_the_cap(uploads, monkeypatch) -> None:
    monkeypatch.setattr(chat_api, "_MAX_UPLOAD_BYTES", 2**20)       # 1 MB here, 20 on a server
    status, body = await _refused("big.jpg", small("JPEG", (400, 400)) + b"\x00" * 2**20)
    assert status == 413
    assert body == {"code": "file_too_large", "max_mb": 1,
                    "detail": "That picture is too large for the chat: more than 1 MB."}
    assert list(uploads.iterdir()) == []


def test_every_refusal_code_is_one_the_clients_word() -> None:
    assert chat_api.UPLOAD_REFUSAL_CODES == ("unsupported_format", "too_many_pixels", "file_too_large")


def test_the_refusal_body_over_http(uploads) -> None:
    """The same body over HTTP, as the dashboard and the phone read it (the
    chat router alone, its device check waived: DB-free)."""
    from domovoi.admin_auth import require_device

    app = FastAPI()
    app.include_router(chat_api.router)
    app.dependency_overrides[require_device] = lambda: None
    with TestClient(app) as c:
        r = c.post("/api/chat/uploads", files={"file": ("IMG_0001.heic", io.BytesIO(HEIC), "image/heic")})
    assert r.status_code == 415
    assert r.json()["code"] == "unsupported_format" and r.json()["format"] == "HEIC"
    assert r.json()["detail"].startswith("That picture is in HEIC format")


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP", "BMP"])
def test_the_sniffer_names_every_kind_the_chat_takes(fmt) -> None:
    assert chat_api.picture_format_of(small(fmt)[:64]) == fmt


@pytest.mark.parametrize("fmt,ext", [("JPEG", ".jpg"), ("PNG", ".png"), ("GIF", ".gif"),
                                     ("WEBP", ".webp"), ("BMP", ".bmp")])
async def test_a_real_picture_uploads(uploads, fmt, ext) -> None:
    out = await _upload(f"pic{ext}", small(fmt))
    assert (uploads / f"{out['token']}{ext}").is_file()


# ─── the picture libraries' thumbnails ───────────────────────────────────


def test_a_thumbnail_tries_only_picture_formats(tmp_path, no_ghostscript) -> None:
    src = tmp_path / "cat.png"
    src.write_bytes(EPS)
    assert images_api._make_thumb(src, tmp_path / "t.webp", 160) is False
    assert no_ghostscript == []


def test_a_thumbnail_is_not_made_of_a_bomb(tmp_path) -> None:
    src = tmp_path / "huge.png"
    src.write_bytes(bomb_png(13000, 13000))
    assert images_api._make_thumb(src, tmp_path / "t.webp", 160) is False


@pytest.mark.parametrize("fmt,ext", [("JPEG", ".jpg"), ("PNG", ".png"), ("WEBP", ".webp"),
                                     ("GIF", ".gif"), ("BMP", ".bmp"), ("ICO", ".ico")])
def test_thumbnails_still_come_from_every_library_kind(tmp_path, fmt, ext) -> None:
    src = tmp_path / f"a{ext}"
    src.write_bytes(small(fmt, (256, 128)))
    dest = tmp_path / "t.webp"
    assert images_api._make_thumb(src, dest, 160) is True
    assert 0 < max(Image.open(dest).size) <= 160


def test_the_pictures_pool_is_bounded() -> None:
    assert executors.POOL_SIZES["pictures"] == 3


def test_thumbnails_run_in_the_pictures_pool() -> None:
    assert 'executors.run("pictures", _make_thumb' in inspect.getsource(images_api.thumb)
    assert asyncio.iscoroutinefunction(images_api.thumb)
