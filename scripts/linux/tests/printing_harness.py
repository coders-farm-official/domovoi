"""The Python half of test-printing.sh: drive Domovoi's own IPP client and
encoders against a real ippeveprinter, and its eSCL client against our fake
scanner. Standard library only (the harness image has no venv): it imports
only the stdlib modules of domovoi/printing/.

    python3 printing_harness.py CASE PORT SPOOL_DIR

Each case prints what it checked and exits 0 when everything held, 1 when
something didn't (with the reason), 2 on a usage error.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from domovoi.printing import capabilities, escl, http11, ipp, raster  # noqa: E402
from domovoi.printing.client import IppClient, Printer  # noqa: E402
from domovoi.printing.ipp import Attr  # noqa: E402

FAILED: list[str] = []


def check(what: str, ok: bool) -> None:
    print(("    ok   " if ok else "    FAIL ") + what)
    if not ok:
        FAILED.append(what)


def tiny_pdf(pages: int) -> bytes:
    """A small valid PDF, written by hand (no PDF library in the image)."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join("%d 0 R" % (3 + 2 * i) for i in range(pages))
    objs.append(("<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, pages)).encode())
    for i in range(pages):
        content = ("BT /F1 24 Tf 72 700 Td (Domovoi harness page %d) Tj ET" % (i + 1)).encode()
        objs.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                     "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>"
                     % (4 + 2 * i)).encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def synthetic_page(n: int, dpi: int, gray: bool) -> raster.Page:
    """A Letter page at ``dpi``: white with a dark band whose position says
    which page it is and an asymmetric corner mark (so a turned back page
    is visibly different from an unturned one)."""
    w, h = round(612 * dpi / 72), round(792 * dpi / 72)
    bpp = 1 if gray else 3
    white = b"\xff" * bpp
    dark = b"\x20" * bpp
    rows = []
    band = (h // 6) * n
    for y in range(h):
        if band <= y < band + 20:
            rows.append(dark * w)
        elif y < 40:
            rows.append(dark * 60 + white * (w - 60))
        else:
            rows.append(white * w)
    return raster.Page(612, 792, w, h, bpp, b"".join(rows))


def latest_spool(spool: Path, suffix: str) -> Path | None:
    files = sorted((p for p in spool.rglob("*" + suffix)), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


async def wait_job(client: IppClient, job_id: int, timeout: float = 60.0) -> int:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        ja = await client.get_job_attributes(job_id)
        state = ipp.first(ja, "job-state", 0)
        if state in ipp.JOB_TERMINAL:
            return state
        await asyncio.sleep(0.2)
    return 0


async def case_attributes(c: IppClient, spool: Path) -> None:
    attrs = await c.get_printer_attributes()
    caps = capabilities.summarize(attrs)
    check("printer-uuid present", bool(caps["uuid"]))
    check("formats listed: %s" % caps["formats"], bool(caps["formats"]))
    st = capabilities.status_of(attrs, "harness printer")
    check("status in plain words: %s" % st["state"], st["state"] in ("ready", "printing"))
    v = await c.validate_job([Attr.of(ipp.KEYWORD, "sides", "one-sided"), ipp.media_col(21590, 27940),
                              Attr(ipp.RANGE, "page-ranges", [ipp.Range(1, 1)]),
                              Attr.of(ipp.INTEGER, "copies", 1)],
                             document_format=caps["formats"][0], job_name="validate")
    check("Validate-Job with media-col, page-ranges, copies: %s" % v.status_name, v.ok)


async def case_pdf(c: IppClient, spool: Path) -> None:
    pdf = tiny_pdf(3)
    jid = await c.create_job([Attr.of(ipp.INTEGER, "copies", 2), ipp.media_col(21590, 27940),
                              Attr(ipp.RANGE, "page-ranges", [ipp.Range(1, 2)])], job_name="harness pdf")
    await c.send_document(jid, [pdf], document_format="application/pdf")
    state = await wait_job(c, jid)
    check("PDF job completed (state %s)" % ipp.JOB_STATES.get(state, state), state == 9)
    kept = latest_spool(spool, ".pdf")
    check("the PDF arrived unchanged", kept is not None and kept.read_bytes() == pdf)


async def _raster_job(c: IppClient, spool: Path, fmt: str, sides: str) -> None:
    attrs = await c.get_printer_attributes()
    caps = capabilities.summarize(attrs)
    gray = "srgb_8" not in caps["pwg_types"] if fmt == "pwg" else False
    dpi = (caps["pwg_resolutions"] or [300])[0] if fmt == "pwg" else 300
    pages = [synthetic_page(n, dpi, gray) for n in (1, 2, 3)]
    sheet_back = caps["sheet_back"]
    buf = io.BytesIO()
    if fmt == "pwg":
        w = raster.PwgWriter(buf, dpi=dpi, typ="sgray_8" if gray else "srgb_8", total_pages=3, sides=sides,
                             sheet_back=sheet_back)
    else:
        w = raster.UrfWriter(buf, dpi=dpi, total_pages=3, sides=sides)
    for p in pages:
        w.write(p)
    data = buf.getvalue()
    mime = "image/pwg-raster" if fmt == "pwg" else "image/urf"
    job_attrs = [ipp.media_col(21590, 27940)]
    if sides != "one-sided":
        job_attrs.append(Attr.of(ipp.KEYWORD, "sides", sides))
    jid = await c.create_job(job_attrs, job_name="harness %s" % fmt)
    await c.send_document(jid, [data], document_format=mime)
    state = await wait_job(c, jid, timeout=180.0)
    check("%s job completed through ippeveprinter's own reader (state %s)"
          % (fmt, ipp.JOB_STATES.get(state, state)), state == 9)
    kept = latest_spool(spool, ".pwg" if fmt == "pwg" else ".urf")
    check("the %s file was kept" % fmt, kept is not None)
    if kept is None:
        return
    blob = kept.read_bytes()
    check("kept bytes are what we sent", hashlib.sha256(blob).digest() == hashlib.sha256(data).digest())
    if fmt == "pwg":
        v = raster.validate_pwg(blob, supported_res=["%dx%ddpi" % (dpi, dpi)],
                                supported_types=caps["pwg_types"])
        check("3 valid PWG headers at %d dpi: %s" % (dpi, v["problems"][:2]), v["valid"] and len(v["pages"]) == 3)
        want = [(1, 1), raster.back_transform(sheet_back, sides), (1, 1)] if sides != "one-sided" else [(1, 1)] * 3
        got = [(p["CrossFeedTransform"], p["FeedTransform"]) for p in v["pages"]]
        check("back pages follow sheet-back %r: %s" % (sheet_back, got), got == want)
        if sides != "one-sided":
            turned = raster.transform_page(pages[1], *want[1])
            check("the back page's pixels are turned to match",
                  v["pages"][1]["bitmap_sha256"] == hashlib.sha256(turned.data).hexdigest())
    else:
        v = raster.validate_urf(blob)
        check("valid URF, 3 pages: %s" % v["problems"][:2], v["valid"] and v["page_count_field"] == 3)


async def case_pwg_duplex(c: IppClient, spool: Path) -> None:
    await _raster_job(c, spool, "pwg", "two-sided-long-edge")


async def case_urf(c: IppClient, spool: Path) -> None:
    await _raster_job(c, spool, "urf", "one-sided")


async def case_cancel(c: IppClient, spool: Path) -> None:
    jid = await c.create_job([ipp.media_col(21590, 27940)], job_name="harness cancel")
    r = await c.cancel_job(jid)
    check("Cancel-Job: %s" % r.status_name, r.ok)
    ja = await c.get_job_attributes(jid)
    check("the job is canceled", ipp.first(ja, "job-state") == 7)


async def case_escl(_c, _spool: Path) -> None:
    from domovoi.tests.printing_fakes import FakeScanner, scan_page_jpeg

    fake = FakeScanner(busy_503=2, wrong_location_host=True, adf_pages=0)
    try:
        client = escl.EsclClient(http11.Endpoint("127.0.0.1", fake.port))

        async def fast(_s):
            return None

        client.sleep = fast
        caps = await client.capabilities()
        check("capabilities: %s, %s" % (caps.make_and_model, caps.sources()), caps.sources() == ["platen", "adf"])
        job = await client.start(escl.scan_settings_xml(source="platen", color_mode="RGB24", resolution=300,
                                                        width=2550, height=3300))
        check("the wrong Location host was dropped: %s" % job, job.startswith("/eSCL/ScanJobs/"))
        page = await client.next_page(job, first=True)
        check("a JPEG page after two 503s", page == scan_page_jpeg())
        await client.delete(job)
    finally:
        fake.close()
    feeder = FakeScanner(adf_pages=3)
    try:
        client = escl.EsclClient(http11.Endpoint("127.0.0.1", feeder.port))
        job = await client.start(escl.scan_settings_xml(source="adf", color_mode="Grayscale8", resolution=300,
                                                        width=2550, height=3300))
        n = 0
        while await client.next_page(job, first=n == 0) is not None:
            n += 1
        check("the feeder gave 3 pages, then 404", n == 3)
    finally:
        feeder.close()
    jam = FakeScanner(adf_state="ScannerAdfJam")
    try:
        client = escl.EsclClient(http11.Endpoint("127.0.0.1", jam.port))
        st = await client.status()
        check("a jammed feeder is reported", st.adf_jammed)
    finally:
        jam.close()


CASES = {
    "attributes": case_attributes, "pdf": case_pdf, "pwg_duplex": case_pwg_duplex, "urf": case_urf,
    "cancel": case_cancel, "escl": case_escl,
}


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in CASES:
        print("usage: printing_harness.py {%s} PORT SPOOL" % "|".join(CASES), file=sys.stderr)
        return 2
    port = int(argv[2])
    client = IppClient(Printer(address="127.0.0.1", port=port, path="/ipp/print", host="127.0.0.1"), timeout=60)
    asyncio.run(CASES[argv[1]](client, Path(argv[3])))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

