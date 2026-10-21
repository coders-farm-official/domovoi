"""PWG Raster and URF (domovoi/printing/raster.py), checked with the
module's own independent strict reader, plus the drawing worker that feeds
them (pypdfium2, ReportLab) end to end."""

from __future__ import annotations

import asyncio
import hashlib
import io
import random

import pytest

from domovoi.printing import raster


def _page(w=40, h=30, bpp=3, seed=1, w_pt=None, h_pt=None, dpi=72):
    rnd = random.Random(seed)
    rows = []
    for y in range(h):
        if y % 7 == 0:
            rows.append(b"\xff" * (w * bpp))          # a white line (repeats)
        else:
            rows.append(bytes(rnd.choice((0, 255, 128)) for _ in range(w * bpp)))
    return raster.Page(w_pt or w, h_pt or h, w, h, bpp, b"".join(rows))


@pytest.mark.parametrize("bpp", [1, 3])
def test_line_codec_round_trips_and_never_emits_0x80(bpp):
    rnd = random.Random(bpp)
    for _ in range(200):
        n = rnd.randint(1, 400)
        line = bytes(rnd.choice((0, 1, 2, 255)) for _ in range(n * bpp))
        enc = raster.encode_line(line, bpp)
        buf = bytes([0]) + enc
        problems = []
        _off, dec = raster.decode_bitmap(buf, 0, n, 1, bpp, problems)
        assert not problems
        assert dec == line
        # 0x80 is never used as a count byte
        i = 0
        while i < len(enc):
            c = enc[i]
            assert c != 0x80
            i += 1 + (bpp if c < 128 else (257 - c) * bpp)


def test_long_runs_split_at_128():
    line = b"\x00" * 300
    enc = raster.encode_line(line, 1)
    assert enc == bytes([127, 0, 127, 0, 43, 0])


@pytest.mark.parametrize("typ,bpp", [("srgb_8", 3), ("sgray_8", 1)])
def test_pwg_stream_validates_and_bitmaps_match(typ, bpp):
    pages = [_page(bpp=bpp, seed=s) for s in (1, 2, 3)]
    buf = io.BytesIO()
    w = raster.PwgWriter(buf, dpi=72, typ=typ, total_pages=3)
    for p in pages:
        w.write(p)
    v = raster.validate_pwg(buf.getvalue(), supported_res=["72x72dpi"], supported_types=[typ])
    assert v["valid"], v["problems"]
    assert [p["bitmap_sha256"] for p in v["pages"]] == [hashlib.sha256(p.data).hexdigest() for p in pages]
    assert all(p["TotalPageCount"] == 3 for p in v["pages"])


def test_pwg_validator_catches_a_wrong_size():
    p = _page()
    bad = raster.Page(p.w_pt + 10, p.h_pt, p.width, p.height, p.bpp, p.data)
    buf = io.BytesIO()
    raster.PwgWriter(buf, dpi=72, typ="srgb_8", total_pages=1).write(bad)
    v = raster.validate_pwg(buf.getvalue())
    assert not v["valid"]
    assert any("PageSize" in x for x in v["problems"])


def test_urf_stream_validates():
    pages = [_page(seed=s) for s in (4, 5)]
    buf = io.BytesIO()
    w = raster.UrfWriter(buf, dpi=72, total_pages=2, sides="two-sided-long-edge")
    for p in pages:
        w.write(p)
    v = raster.validate_urf(buf.getvalue())
    assert v["valid"], v["problems"]
    assert v["page_count_field"] == 2
    assert v["pages"][0]["duplex"] == 2


@pytest.mark.parametrize("sheet_back,sides,expect", [
    ("normal", "two-sided-long-edge", (1, 1)),
    ("rotated", "two-sided-long-edge", (-1, -1)),
    ("rotated", "two-sided-short-edge", (1, 1)),
    ("flipped", "two-sided-long-edge", (1, -1)),
    ("flipped", "two-sided-short-edge", (-1, 1)),
    ("manual-tumble", "two-sided-long-edge", (1, 1)),
    ("manual-tumble", "two-sided-short-edge", (-1, -1)),
])
def test_back_side_transforms_follow_sheet_back(sheet_back, sides, expect):
    assert raster.back_transform(sheet_back, sides) == expect


def test_back_pages_are_turned_and_the_header_says_so():
    pages = [_page(seed=s) for s in (1, 2, 3)]
    buf = io.BytesIO()
    w = raster.PwgWriter(buf, dpi=72, typ="srgb_8", total_pages=3, sides="two-sided-long-edge",
                         sheet_back="rotated")
    for p in pages:
        w.write(p)
    v = raster.validate_pwg(buf.getvalue())
    assert v["valid"], v["problems"]
    fronts_backs = [(p["CrossFeedTransform"], p["FeedTransform"]) for p in v["pages"]]
    assert fronts_backs == [(1, 1), (-1, -1), (1, 1)]
    assert all(p["Duplex"] == 1 for p in v["pages"])
    turned = raster.transform_page(pages[1], -1, -1)
    assert v["pages"][1]["bitmap_sha256"] == hashlib.sha256(turned.data).hexdigest()
    # A 180° turn twice is the page again.
    assert raster.transform_page(turned, -1, -1).data == pages[1].data


def test_render_worker_draws_exact_pixels_and_fits_the_paper(tmp_path):
    """A Letter test page drawn for A4 paper: exact A4 pixels at the dpi
    (PDFium's rounded-up bitmap is cropped), grey and colour, and the
    worker's Pillow back-side transform matches the pure one."""
    from domovoi.printing import render

    async def go():
        with render.Workdir() as w:
            tp = {"printer_name": "office printer", "model": "Example", "paper_label": "Letter",
                  "sides_label": "one-sided", "color_label": "colour", "color": True}
            prep = await render.prepare(w, kind="testpage", paper=[612, 792], title="t", testpage=tp)
            assert prep["pages_total"] == 1 and len(prep["thumbs"]) == 1
            assert prep["thumbs"][0]["src"].startswith("data:image/png;base64,")
            out = {}
            for fmt, gray in (("pwg", False), ("pwg", True), ("urf", False)):
                await render.output(w, format=fmt, dpi=150, paper=[595, 842], gray=gray)
                out[(fmt, gray)] = w.output().read_bytes()
            return out

    out = asyncio.run(go())
    for (fmt, gray), data in out.items():
        v = raster.validate_pwg(data) if fmt == "pwg" else raster.validate_urf(data)
        assert v["valid"], (fmt, gray, v["problems"])
        page = v["pages"][0]
        if fmt == "pwg":
            assert (page["Width"], page["Height"]) == (round(595 * 150 / 72), round(842 * 150 / 72))
            assert page["type"] == ("sgray_8" if gray else "srgb_8")


def test_worker_refuses_what_it_cannot_draw(tmp_path):
    from domovoi.printing import render

    async def go():
        with render.Workdir() as w:
            w.input().write_bytes(b"%PDF-1.4 this is not a pdf")
            with pytest.raises(render.RenderFailed):
                await render.prepare(w, kind="pdf", paper=[612, 792], title="t")
            w.input().write_bytes(b"col\n")
            with pytest.raises(render.RenderFailed, match="pages"):
                (w.path / "input").write_bytes(b"a,b\n1,2\n")
                await render.prepare(w, kind="table", paper=[612, 792], title="t", pages="9", ext=".csv")

    asyncio.run(go())


def test_tables_text_and_images_become_pdfs(tmp_path):
    from PIL import Image

    from domovoi.printing import render

    async def prepare(kind, data, **kw):
        with render.Workdir() as w:
            w.input().write_bytes(data)
            return await render.prepare(w, kind=kind, paper=[612, 792], title="t", **kw)

    wide = "\n".join(",".join("column number %d" % c for c in range(30)) for _ in range(5)).encode()
    r = asyncio.run(prepare("table", wide, ext=".csv"))
    assert r["notes"] == ["Printed as a plain table."]
    assert r["pages_total"] >= 2           # split left to right past 7 pt
    md = b"# Title\n\nSome **bold** text.\n\n- one\n- two\n\n```\ncode\n```\n" * 30
    assert asyncio.run(prepare("markdown", md))["pages_total"] >= 2
    buf = io.BytesIO()
    Image.new("RGB", (800, 400), (200, 30, 30)).save(buf, "PNG")
    r = asyncio.run(prepare("image", buf.getvalue()))
    assert r["pages_total"] == 1
    assert r["sizes"][0] == [612, 792]


def test_scan_pdf_embeds_the_scanner_jpegs_byte_for_byte():
    from domovoi.printing import templates
    from domovoi.tests.printing_fakes import scan_page_jpeg

    jpeg = scan_page_jpeg()
    pdf = templates.scans_pdf([jpeg, jpeg], page_size=(612, 792), title="Scan")
    assert pdf.count(b"/DCTDecode") >= 1
    assert jpeg in pdf
    assert b"ASCII85" not in pdf
