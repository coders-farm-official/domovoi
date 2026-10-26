"""Printers, printing and scanning through the real core routes (and the
web door), on the test database, against our fake printer and scanner.

Tiers: the household (device token) adds, prints, scans; only the admin
renames, removes, changes limits and clears the log; a guest gets nothing.
The fakes live on 127.0.0.1, which the LAN check refuses — each test names
its fake's exact port in the operator allowlist (``OUTBOUND_ALLOW_HOSTS``'s
reader), the same hatch the functional-test harness uses.
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi import net_safety
from domovoi.config import settings
from domovoi.db.session import engine
from domovoi.printing import discovery, mdns, raster, scans, store
from domovoi.tests.auth_testkit import (
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    core_client,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db
from domovoi.tests.printing_fakes import FakeIppPrinter, FakeScanner, fake_lan, real_lan, scan_page_jpeg

pytestmark = requires_db


def _pdf(pages: int = 3) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(pages):
        c.drawString(72, 700, "Page %d of a test letter" % (i + 1))
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
async def house(_db, tmp_path, monkeypatch):  # noqa: F811
    """Admin claimed, the household token, Documents in tmp, clean state."""
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path / "Documents"))
    (tmp_path / "Documents").mkdir()
    allowed: list[tuple[str, int]] = []
    monkeypatch.setattr(net_safety, "allow_entries", lambda: tuple(allowed))
    lan = fake_lan(monkeypatch)
    discovery.reset_state()
    scans.reset_state()
    # The route budgets are per process; earlier tests mustn't use them up.
    from domovoi.printing import routes

    routes.PREVIEW_LIMITER.reset()
    routes.DISCOVER_LIMITER.reset()
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE print_jobs, scan_jobs, printer_room_defaults, printers RESTART IDENTITY CASCADE"))
    await store.reset_limits()
    async with web_client() as w:
        admin = await claim_admin(w)
    token = await db_device_token()
    yield {"admin": admin, "device": {"X-Device-Token": token}, "allowed": allowed, "lan": lan,
           "docs": tmp_path / "Documents"}
    await store.reset_limits()


async def _add(c, house, fake, *, name="office printer"):
    house["lan"](fake.port)
    r = await c.post("/v1/printers", json={"address": "ipp://127.0.0.1:%d/ipp/print" % fake.port, "name": name},
                     headers=house["device"])
    assert r.status_code == 201, r.text
    return r.json()


async def _upload(c, house, path_name: str, data: bytes, *, preview=False, **meta):
    meta = {"filename": path_name, **meta}
    r = await c.post("/v1/print/preview" if preview else "/v1/print/jobs",
                     files={"file": (path_name, data, "application/octet-stream")},
                     data={"meta": json.dumps(meta)}, headers=house["device"])
    return r


async def _wait_job(c, house, job_id, states=("done", "failed", "canceled"), tries=200):
    for _ in range(tries):
        r = await c.get("/v1/print/jobs/%d" % job_id, headers=house["device"])
        assert r.status_code == 200
        if r.json()["state"] in states:
            return r.json()
        await asyncio.sleep(0.05)
    raise AssertionError("job %d never reached %s: %r" % (job_id, states, r.json()))


async def test_guests_get_nothing_and_the_household_reads(house):
    async with core_client() as c:
        for method, path in (("get", "/v1/printers"), ("get", "/v1/print/jobs"), ("post", "/v1/printers/discover"),
                             ("post", "/v1/scan/jobs"), ("get", "/v1/print/limits")):
            r = await getattr(c, method)(path, **({"json": {}} if method == "post" else {}))
            assert r.status_code == 401, (path, r.status_code)
        r = await c.get("/v1/printers", headers=house["device"])
        assert r.status_code == 200
        body = r.json()
    assert body["printers"] == [] and body["has_printer"] is False
    assert ".docx" in body["printable"]["pdf_first_exts"]
    assert body["limits"]["max_pages_per_job"] == 100


async def test_add_by_address_status_ink_and_tiers(house):
    fake = await FakeIppPrinter(reasons=["toner-low-report"]).start()
    try:
        async with core_client() as c:
            p = await _add(c, house, fake)
            assert p["can_print"] and p["color"] and p["duplex"] and p["is_default"]
            assert p["added_via"] == "typed"
            assert [s["name"] for s in p["supplies"]] == ["Black toner", "Cyan toner", "Magenta toner",
                                                           "Yellow toner"]
            assert p["status"]["state"] == "low on toner" and p["status"]["tone"] == "warn"
            assert "\x1b" not in p["status"]["message"]
            # The fake's printer-more-info names another host: no link.
            assert p["admin_url"] is None
            assert p["choices"]["sides"] == ["one-sided", "two-sided-long-edge", "two-sided-short-edge"]
            # the same printer again is refused
            r = await c.post("/v1/printers", json={"address": "ipp://127.0.0.1:%d/ipp/print" % fake.port},
                             headers=house["device"])
            assert r.status_code == 409 and "already added" in r.json()["detail"]
            # defaults and rooms are household; rename and remove are admin
            r = await c.patch("/v1/printers/%d" % p["id"], json={"rooms": ["kitchen"], "two_sided_default": True},
                              headers=house["device"])
            assert r.status_code == 200 and r.json()["rooms"] == ["kitchen"]
            assert r.json()["defaults"]["sides"] == "two-sided-long-edge"
            r = await c.post("/v1/printers/%d/rename" % p["id"], json={"name": "den"}, headers=house["device"])
            assert r.status_code == 401
            r = await c.delete("/v1/printers/%d" % p["id"], headers=house["device"])
            assert r.status_code == 401
            r = await c.post("/v1/printers/%d/rename" % p["id"], json={"name": "den printer"},
                             headers=bearer(house["admin"]))
            assert r.status_code == 200 and r.json()["name"] == "den printer"
            r = await c.post("/v1/printers/%d/identify" % p["id"], headers=house["device"])
            assert r.status_code == 200
            r = await c.delete("/v1/printers/%d" % p["id"], headers=bearer(house["admin"]))
            assert r.status_code == 200
    finally:
        await fake.close()


@pytest.mark.parametrize("address,detail", [
    ("8.8.8.8", "isn't on your home network"),
    ("127.0.0.250", "this server's own address"),
    ("ipp://printer.example.com/ipp/print", "isn't on your home network"),
    ("http://169.254.169.254/eSCL", "isn't on your home network"),
])
async def test_ssrf_shaped_addresses_are_refused(house, address, detail):
    async with core_client() as c:
        r = await c.post("/v1/printers", json={"address": address}, headers=house["device"])
    assert r.status_code == 400
    assert detail in r.json()["detail"]


async def test_something_that_is_not_a_printer_is_not_saved(house):
    async def http_200(reader, writer):
        await reader.read(65536)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 13\r\n\r\n<html></html>")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(http_200, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    house["lan"](port)
    try:
        async with core_client() as c:
            r = await c.post("/v1/printers", json={"address": "http://127.0.0.1:%d/eSCL" % port},
                             headers=house["device"])
            assert r.status_code == 409
            assert "answered like a printer" in r.json()["detail"]
            assert (await c.get("/v1/printers", headers=house["device"])).json()["printers"] == []
    finally:
        server.close()


async def test_print_a_pdf_straight_through_with_options(house):
    fake = await FakeIppPrinter().start()
    try:
        async with core_client() as c:
            p = await _add(c, house, fake)
            pdf = _pdf(3)
            r = await _upload(c, house, "Cover letter.pdf", pdf, preview=True,
                              options={"pages": "1-2", "sides": "two-sided-long-edge", "color": "monochrome"})
            assert r.status_code == 200, r.text
            pv = r.json()
            assert pv["pages_total"] == 3 and pv["pages"] == [1, 2] and pv["sheets"] == 1
            assert pv["summary"] == "2 pages · 1 sheet of paper · two-sided"
            assert len(pv["thumbs"]) == 2 and pv["over_limit"] is None
            r = await _upload(c, house, "Cover letter.pdf", pdf, printer_id=p["id"],
                              options={"pages": "1-2", "copies": 2, "sides": "two-sided-long-edge"},
                              who="Kitchen tablet")
            assert r.status_code == 202, r.text
            job = await _wait_job(c, house, r.json()["id"])
    finally:
        await fake.close()
    assert job["state"] == "done" and job["who"] == "Kitchen tablet" and job["title"] == "Cover letter"
    [fj] = fake.jobs.values()
    assert fj.format == "application/pdf" and fj.data == pdf          # unchanged
    assert fj.attrs["copies"] == [2] and fj.attrs["sides"] == ["two-sided-long-edge"]
    assert [(r.lower, r.upper) for r in fj.attrs["page-ranges"]] == [(1, 2)]
    assert fj.attrs["media-col"][0]["media-size"][0]["x-dimension"] == [21590]
    ops = [name for name, _ in fake.requests]
    assert ops.index("Validate-Job") < ops.index("Create-Job") < ops.index("Send-Document")
    assert fake.chunked_seen == 0


async def test_a_document_over_a_megabyte_gets_through_the_body_limit(house):
    """The core's transport guard caps bodies at 1 MB unless a route says
    otherwise; the print routes take up to 101 MB (the web reads 100)."""
    from domovoi.transport_guard import body_limit_for

    assert body_limit_for("/v1/print/jobs") > 100 * 1024 * 1024
    assert body_limit_for("/v1/print/preview") > 100 * 1024 * 1024
    fake = await FakeIppPrinter().start()
    try:
        async with core_client() as c:
            await _add(c, house, fake)
            big = _pdf(1) + b"%" + b"x" * (3 * 1024 * 1024) + b"\n"
            r = await _upload(c, house, "Big.pdf", big)
            assert r.status_code == 202, r.text
            job = await _wait_job(c, house, r.json()["id"])
    finally:
        await fake.close()
    assert job["state"] == "done"
    assert [j.data for j in fake.jobs.values()] == [big]


async def test_a_raster_only_printer_gets_pwg_with_turned_back_pages(house):
    fake = await FakeIppPrinter(formats=["image/pwg-raster", "image/jpeg"]).start()
    try:
        async with core_client() as c:
            await _add(c, house, fake)
            r = await _upload(c, house, "Lease.pdf", _pdf(3), options={"sides": "two-sided-long-edge",
                                                                        "color": "monochrome"})
            assert r.status_code == 202, r.text
            job = await _wait_job(c, house, r.json()["id"])
    finally:
        await fake.close()
    assert job["state"] == "done", job
    [fj] = fake.jobs.values()
    assert fj.format == "image/pwg-raster"
    v = raster.validate_pwg(fj.data, supported_res=["300x300dpi", "600x600dpi"], supported_types=["sgray_8"])
    assert v["valid"], v["problems"]
    assert [(p["CrossFeedTransform"], p["FeedTransform"]) for p in v["pages"]] == [(1, 1), (-1, -1), (1, 1)]
    assert v["pages"][0]["HWResolution"] == (300, 300)


async def test_word_files_and_unknown_kinds_are_refused_plainly(house):
    fake = await FakeIppPrinter().start()
    try:
        async with core_client() as c:
            await _add(c, house, fake)
            r = await _upload(c, house, "Letter.docx", b"PK..")
            assert r.status_code == 415
            assert r.json()["detail"].startswith("To print a Word file, save it as PDF")
            r = await _upload(c, house, "song.mp3", b"ID3")
            assert r.status_code == 415
    finally:
        await fake.close()


async def test_limits_are_enforced_and_only_the_admin_changes_them(house):
    fake = await FakeIppPrinter().start()
    try:
        async with core_client() as c:
            await _add(c, house, fake)
            r = await c.put("/v1/print/limits", json={"max_pages_per_job": 2}, headers=house["device"])
            assert r.status_code == 401
            r = await c.put("/v1/print/limits", json={"max_pages_per_job": 2, "history_days": 5},
                            headers=bearer(house["admin"]))
            assert r.status_code == 200 and r.json()["max_pages_per_job"] == 2 and r.json()["history_days"] == 90
            r = await _upload(c, house, "Long.pdf", _pdf(3), preview=True)
            assert "more than this house allows in one job (2)" in r.json()["over_limit"]
            r = await _upload(c, house, "Long.pdf", _pdf(3))
            assert r.status_code == 413
            assert r.json()["detail"] == ("That's 3 pages, more than this house allows in one job (2). "
                                          "An admin can change the limit.")
            r = await _upload(c, house, "Long.pdf", _pdf(1), options={"copies": 25})
            assert r.status_code == 413 and "25 copies" in r.json()["detail"]
            await c.put("/v1/print/limits", json={"max_pages_per_job": 100, "max_jobs_per_hour_per_device": 1},
                        headers=bearer(house["admin"]))
            r = await _upload(c, house, "One.pdf", _pdf(1))
            assert r.status_code == 202
            r = await _upload(c, house, "Two.pdf", _pdf(1))
            assert r.status_code == 429
    finally:
        await fake.close()


async def test_cancel_and_the_job_log(house):
    fake = await FakeIppPrinter(complete_after_polls=10_000).start()
    try:
        async with core_client() as c:
            await _add(c, house, fake)
            r = await _upload(c, house, "Slow.pdf", _pdf(1))
            job_id = r.json()["id"]
            await _wait_job(c, house, job_id, states=("printing",))
            r = await c.post("/v1/print/jobs/%d/cancel" % job_id, headers=house["device"])
            assert r.status_code == 200 and r.json()["state"] == "canceled"
            assert [j.state for j in fake.jobs.values()] == [7]
            jobs = (await c.get("/v1/print/jobs", headers=house["device"])).json()["jobs"]
            assert [j["title"] for j in jobs] == ["Slow"]
            assert (await c.delete("/v1/print/jobs", headers=house["device"])).status_code == 401
            r = await c.delete("/v1/print/jobs", headers=bearer(house["admin"]))
            assert r.status_code == 200 and r.json()["cleared"] == 1
    finally:
        await fake.close()


async def test_history_older_than_90_days_is_pruned():
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE print_jobs, scan_jobs RESTART IDENTITY CASCADE"))
    await store.insert_job({"title": "old", "state": "done"})
    await store.insert_job({"title": "new", "state": "done"})
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE print_jobs SET created_at = now() - interval '91 days' WHERE title = 'old'"))
    assert await store.prune_history() == 1
    assert [j["title"] for j in await store.list_jobs()] == ["new"]


async def test_a_voice_job_that_fails_is_said_once_in_its_room(house, monkeypatch):
    from domovoi.printing import jobs
    from domovoi.sdk import speech

    said: list[tuple[str, str]] = []

    class Room:
        room_id = "kitchen"

        async def announce(self, text_, **kw):
            said.append(("kitchen", text_))

    monkeypatch.setattr(speech, "_active_sessions_provider", lambda: {"kitchen": Room()})
    fake = await FakeIppPrinter(job_outcome="stop").start()
    try:
        async with core_client() as c:
            p = await _add(c, house, fake)
        (house["docs"] / "Letter.pdf").write_bytes(_pdf(1))
        row = await store.get_printer(p["id"])
        job = await jobs.submit(house["docs"] / "Letter.pdf", filename="Letter.pdf", title="Letter",
                                printer_id=row["id"], wanted={},
                                who=jobs.Who(label="kitchen (voice)", device_key="room:kitchen", source="voice",
                                             room_id="kitchen"))
        for _ in range(200):
            if said:
                break
            await asyncio.sleep(0.05)
        for _ in range(5):
            await jobs.follow(job["id"])
    finally:
        await fake.close()
    assert said == [("kitchen", "The office printer is out of paper.")]


async def test_test_page_prints_our_own_page(house):
    fake = await FakeIppPrinter().start()
    try:
        async with core_client() as c:
            p = await _add(c, house, fake)
            r = await c.post("/v1/printers/%d/test-page" % p["id"], headers=house["device"])
            assert r.status_code == 200, r.text
            job = await _wait_job(c, house, r.json()["id"])
    finally:
        await fake.close()
    assert job["state"] == "done" and job["source"] == "dashboard"
    [fj] = fake.jobs.values()
    assert fj.data.startswith(b"%PDF") and b"DeviceCMYK" not in fj.data


async def test_discovery_by_multicast_add_by_key_and_the_sweep_limit(house, monkeypatch):
    fake = await FakeIppPrinter().start()
    house["lan"](fake.port)

    def answer():
        def name(n):
            return b"".join(bytes([len(p)]) + p.encode() for p in n.split(".")) + b"\x00"

        def rr(nm, t, rd):
            return nm + struct.pack("!HHIH", t, 1, 120, len(rd)) + rd

        inst = "Example LaserJet._ipp._tcp.local"
        txt = b"".join(bytes([len(t)]) + t for t in (b"rp=ipp/print", ("UUID=" + fake.uuid).encode(),
                                                      b"ty=Example LaserJet 100", b"Color=T"))
        pkt = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 3)
        pkt += rr(name("_ipp._tcp.local"), 12, name(inst))
        pkt += rr(name(inst), 33, struct.pack("!HHH", 0, 0, fake.port) + name("lj.local"))
        pkt += rr(name(inst), 16, txt) + rr(name("lj.local"), 1, bytes([127, 0, 0, 1]))
        return mdns.collect([(pkt, "127.0.0.1", "multicast")])

    monkeypatch.setattr(discovery, "browse_mdns", answer)

    async def no_sweep():
        return []

    monkeypatch.setattr(discovery, "sweep", no_sweep)
    try:
        async with core_client() as c:
            r = await c.post("/v1/printers/discover", json={"look_again": False}, headers=house["device"])
            assert r.status_code == 200, r.text
            body = r.json()
            [f] = body["found"]
            assert f["via"] == "multicast" and f["prints"] and f["already_added"] is None
            d = body["diagnostics"]
            assert (d["found"], d["by_multicast"], d["by_unicast"], d["sweep"]) == (1, 1, 0, "skipped")
            r = await c.post("/v1/printers", json={"key": f["key"], "room_id": "kitchen"}, headers=house["device"])
            assert r.status_code == 201, r.text
            added = r.json()
            assert added["added_via"] == "multicast" and added["rooms"] == ["kitchen"]
            r = await c.post("/v1/printers/discover", json={"look_again": True}, headers=house["device"])
            assert r.json()["found"][0]["already_added"] == added["id"]
            # nothing new by multicast, so "Look again" sweeps
            assert r.json()["diagnostics"]["sweep"] == "ran"
            r = await c.post("/v1/printers/discover", json={"look_again": True}, headers=house["device"])
            assert r.json()["diagnostics"]["sweep"] == "rate_limited"
            assert r.json()["diagnostics"]["sweep_retry_sec"] > 0
            listing = (await c.get("/v1/printers", headers=house["device"])).json()
            assert listing["discovery"]["by_multicast"] == 1
    finally:
        await fake.close()


async def test_scan_glass_pages_into_one_pdf_in_documents(house):
    fake = FakeScanner()
    house["lan"](fake.port)
    try:
        async with core_client() as c:
            r = await c.post("/v1/printers", json={"address": "http://127.0.0.1:%d/eSCL" % fake.port},
                             headers=house["device"])
            assert r.status_code == 201, r.text
            assert r.json()["can_scan"] and not r.json()["can_print"]
            assert r.json()["scan"]["sources"] == ["platen", "adf"]
            r = await c.post("/v1/scan/jobs", json={}, headers=house["device"])
            assert r.status_code == 201, r.text
            s = r.json()
            assert s["state"] == "waiting" and s["pages"] == 1 and s["source_used"] == "platen"
            assert s["readable"] is False
            busy = await c.post("/v1/scan/jobs", json={}, headers=house["device"])
            assert busy.status_code == 409 and "busy" in busy.json()["detail"]
            r = await c.post("/v1/scan/jobs/%s/next" % s["id"], headers=house["device"])
            assert r.json()["pages"] == 2
            r = await c.post("/v1/scan/jobs/%s/finish" % s["id"], headers=house["device"])
            assert r.status_code == 200, r.text
            done = r.json()
            assert done["state"] == "done" and done["path"].startswith("Scans/Scan ")
            assert (await c.get("/v1/scan/jobs/../printers", headers=house["device"])).status_code in (404, 401)
    finally:
        fake.close()
    pdf = (house["docs"] / done["path"]).read_bytes()
    assert pdf.count(scan_page_jpeg()) >= 1
    assert "<pwg:InputSource>Platen</pwg:InputSource>" in fake.settings[0]
    assert "<scan:XResolution>300</scan:XResolution>" in fake.settings[0]
    async with engine.begin() as conn:
        row = (await conn.execute(text("SELECT state, pages, path FROM scan_jobs"))).first()
    assert tuple(row) == ("done", 2, done["path"])


async def test_a_loaded_feeder_is_used_and_the_reader_seam(house):
    fake = FakeScanner(adf_pages=3)
    house["lan"](fake.port)

    async def reader(path, ctx):
        return "It says hello."

    try:
        async with core_client() as c:
            await c.post("/v1/printers", json={"address": "http://127.0.0.1:%d/eSCL" % fake.port},
                         headers=house["device"])
            s = (await c.post("/v1/scan/jobs", json={"color": "grey"}, headers=house["device"])).json()
            assert s["source_used"] == "adf" and s["pages"] == 3
            assert "Grayscale8" in fake.settings[0]
            scans.register_reader(reader)
            done = (await c.post("/v1/scan/jobs/%s/finish" % s["id"], headers=house["device"])).json()
            assert done["readable"] is True
    finally:
        scans.register_reader(None)
        fake.close()


async def test_the_web_door_resolves_documents_and_refuses_secrets(house, monkeypatch):
    """The web resolves library_id + path with the Files tab's containment,
    then forwards the bytes to the core (an ASGI hop here)."""
    import httpx

    from domovoi.main import app as core_app
    from web.backend.api import printers as web_printers

    fake = await FakeIppPrinter().start()
    (house["docs"] / "Recipes").mkdir()
    (house["docs"] / "Recipes" / "Bread.pdf").write_bytes(_pdf(1))
    (house["docs"] / ".env").write_text("SECRET=1")

    real_client = httpx.AsyncClient

    def asgi_client(*a, **kw):
        kw.pop("timeout", None)
        return real_client(transport=httpx.ASGITransport(app=core_app), timeout=30.0)

    monkeypatch.setattr(web_printers.httpx, "AsyncClient", asgi_client)
    monkeypatch.setattr(web_printers, "domovoi_url", lambda: "http://core")
    try:
        async with core_client() as c:
            await _add(c, house, fake)
        hdrs = {"X-Requested-With": "t", **house["device"]}
        async with web_client() as w:
            r = await w.post("/api/print/jobs", json={"library_id": "core:documents", "path": "Recipes/Bread.pdf"},
                             headers={"X-Requested-With": "t"})
            assert r.status_code == 401                                     # guests: nothing
            r = await w.post("/api/print/jobs", json={"library_id": "core:documents", "path": ".env"},
                             headers=hdrs)
            assert r.status_code == 404
            r = await w.post("/api/print/jobs", json={"library_id": "core:documents", "path": "../../etc/passwd"},
                             headers=hdrs)
            assert r.status_code in (400, 403, 404)
            r = await w.post("/api/print/jobs", json={"library_id": "core:documents", "path": "Recipes/Bread.pdf"},
                             headers=hdrs)
            assert r.status_code == 202, r.text
            assert r.json()["title"] == "Bread"
            async with core_client() as c:
                job = await _wait_job(c, house, r.json()["id"])
            assert job["state"] == "done"
    finally:
        await fake.close()


async def test_documents_lookup_for_voice(house):
    from domovoi.printing import lookup

    d = house["docs"]
    (d / "Letters").mkdir()
    (d / "Letters" / "Cover letter - Acme.pdf").write_bytes(b"x")
    (d / "Banana bread.md").write_text("x")
    (d / "Bread rolls.txt").write_text("x")
    (d / "Lease.docx").write_bytes(b"x")
    (d / ".ssh").mkdir()
    (d / ".ssh" / "bread.txt").write_text("x")
    assert [x.rel for x in lookup.find("my cover letter")] == ["Letters/Cover letter - Acme.pdf"]
    assert sorted(x.title for x in lookup.find("bread")) == ["Banana bread", "Bread rolls"]
    assert lookup.find("lease") == []                  # Word files don't print
    assert lookup.resolve("../outside.pdf") is None
    assert lookup.resolve(".ssh/bread.txt") is None


# ─── Review round 1 ──────────────────────────────────────────────────────


def _announce(uuid: str, port: int, addr: str) -> mdns.BrowseResult:
    def name(n):
        return b"".join(bytes([len(p)]) + p.encode() for p in n.split(".")) + b"\x00"

    def rr(nm, t, rd):
        return nm + struct.pack("!HHIH", t, 1, 120, len(rd)) + rd

    inst = "Totally The Office Printer._ipp._tcp.local"
    txt = b"".join(bytes([len(t)]) + t for t in (b"rp=ipp/print", ("UUID=" + uuid).encode()))
    pkt = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 3)
    pkt += rr(name("_ipp._tcp.local"), 12, name(inst))
    pkt += rr(name(inst), 33, struct.pack("!HHH", 0, 0, port) + name("evil.local"))
    pkt += rr(name(inst), 16, txt) + rr(name("evil.local"), 1, bytes(int(o) for o in addr.split(".")))
    return mdns.collect([(pkt, addr, "multicast")])


async def test_announcing_a_uuid_does_not_move_a_saved_printer(house, monkeypatch):
    """Review proof: a LAN host announcing the saved printer's UUID took the
    next job (a doctor's letter). A move now needs the new address to
    answer as that printer, and the old one to be gone."""
    real = await FakeIppPrinter().start("127.0.0.1")
    rogue = await FakeIppPrinter().start("127.0.0.2")         # its own UUID
    house["lan"](rogue.port)
    try:
        async with core_client() as c:
            p = await _add(c, house, real)
            monkeypatch.setattr(discovery, "browse_mdns", lambda: _announce(real.uuid, rogue.port, "127.0.0.2"))
            assert (await c.post("/v1/printers/discover", json={"look_again": False},
                                 headers=house["device"])).status_code == 200
            row = (await c.get("/v1/printers", headers=house["device"])).json()["printers"][0]
            assert row["address"] == "127.0.0.1" and row["moved"] is None
            r = await _upload(c, house, "Letter to the doctor.pdf", _pdf(1))
            await _wait_job(c, house, r.json()["id"])
            assert list(real.jobs) and not rogue.jobs
            # The real printer goes away; the rogue still can't prove it's it.
            await real.close()
            await c.post("/v1/printers/discover", json={"look_again": False}, headers=house["device"])
            row = (await c.get("/v1/printers", headers=house["device"])).json()["printers"][0]
            assert row["address"] == "127.0.0.1"
            # The real printer comes back at a new address (DHCP): it proves
            # who it is there, so the row follows it, and Settings says so.
            moved = FakeIppPrinter(uuid=real.uuid)
            await moved.start("127.0.0.3")
            house["lan"](moved.port)
            monkeypatch.setattr(discovery, "browse_mdns", lambda: _announce(real.uuid, moved.port, "127.0.0.3"))
            await c.post("/v1/printers/discover", json={"look_again": False}, headers=house["device"])
            row = (await c.get("/v1/printers", headers=house["device"])).json()["printers"][0]
            assert row["address"] == "127.0.0.3" and row["id"] == p["id"]
            assert row["moved"] == "The office printer moved to 127.0.0.3."
            assert "The office printer moved to 127.0.0.3." in row["status"]["words"]
            await moved.close()
    finally:
        await real.close()
        await rogue.close()


async def test_the_operator_allowlist_is_for_an_admin_typed_address_only(house, monkeypatch):
    """Review: OUTBOUND_ALLOW_HOSTS let mDNS-announced loopback through."""
    real_lan(monkeypatch)                                       # loopback refused again
    fake = await FakeIppPrinter().start()
    house["allowed"].append(("127.0.0.1", fake.port))
    url = "ipp://127.0.0.1:%d/ipp/print" % fake.port
    try:
        async with core_client() as c:
            r = await c.post("/v1/printers", json={"address": url}, headers=house["device"])
            assert r.status_code == 400                         # a household device: no
            monkeypatch.setattr(discovery, "browse_mdns", lambda: _announce(fake.uuid, fake.port, "127.0.0.1"))
            r = await c.post("/v1/printers/discover", json={"look_again": False}, headers=house["device"])
            assert r.json()["found"] == []                      # announced: no
            r = await c.post("/v1/printers", json={"address": url}, headers=bearer(house["admin"]))
            assert r.status_code == 201, r.text                 # the admin typed it: yes
            [row] = await store.list_printers()
            assert row["operator_allowed"] is True
            r = await _upload(c, house, "Test.pdf", _pdf(1))
            assert r.status_code == 202
            assert (await _wait_job(c, house, r.json()["id"]))["state"] == "done"
    finally:
        await fake.close()


async def test_a_device_blocked_from_files_cannot_scan(house):
    """Review: only the web door applied Settings, Devices' file block."""
    fake = FakeScanner()
    house["lan"](fake.port)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO devices (device_id, name) VALUES ('dev-blocked-1', 'Hall tablet') "
                                "ON CONFLICT (device_id) DO NOTHING"))
        await conn.execute(text("INSERT INTO files_device_blocks (device_name, note) VALUES ('Hall tablet', 'kids') "
                                "ON CONFLICT DO NOTHING"))
    try:
        async with core_client() as c:
            await c.post("/v1/printers", json={"address": "http://127.0.0.1:%d/eSCL" % fake.port},
                         headers=house["device"])
            blocked = {**house["device"], "X-Domovoi-Device-Id": "dev-blocked-1"}
            r = await c.post("/v1/scan/jobs", json={}, headers=blocked)
            assert r.status_code == 403 and "can't scan to Documents" in r.json()["detail"]
            r = await c.post("/v1/scan/jobs", json={}, headers=house["device"])
            assert r.status_code == 201
            r = await c.post("/v1/scan/jobs/%s/finish" % r.json()["id"], headers=blocked)
            assert r.status_code == 403
    finally:
        fake.close()
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM files_device_blocks WHERE device_name = 'Hall tablet'"))
            await conn.execute(text("DELETE FROM devices WHERE device_id = 'dev-blocked-1'"))


def _bomb_jpeg() -> bytes:
    """A real JPEG whose header claims 65,000 x 65,000 pixels."""
    data = bytearray(scan_page_jpeg())
    i = data.index(b"\xff\xc0")
    struct.pack_into("!HH", data, i + 5, 65000, 65000)
    return bytes(data)


@pytest.mark.parametrize("page", [b"\xff\xd8\xff\xe0" + b"not really a jpeg" * 10, _bomb_jpeg()],
                         ids=["truncated", "decompression-bomb"])
async def test_a_bad_scanner_page_ends_the_scan_plainly(house, page):
    """Review proof: a page that starts like a JPEG but isn't escaped as a
    PIL error, left the session 'scanning' and the scanner 'busy'."""
    fake = FakeScanner()
    fake.page = page
    house["lan"](fake.port)
    try:
        async with core_client() as c:
            await c.post("/v1/printers", json={"address": "http://127.0.0.1:%d/eSCL" % fake.port},
                         headers=house["device"])
            r = await c.post("/v1/scan/jobs", json={}, headers=house["device"])
            assert r.status_code == 409
            assert r.json()["detail"] == "The scanner sent a page Domovoi can't read."
            assert all(s.state not in ("scanning", "waiting") for s in scans._sessions.values())
            fake.page = scan_page_jpeg()
            r = await c.post("/v1/scan/jobs", json={}, headers=house["device"])
            assert r.status_code == 201, r.text                 # not wedged "busy"
    finally:
        fake.close()


async def test_any_failure_inside_a_scan_ends_it(house, monkeypatch):
    fake = FakeScanner()
    house["lan"](fake.port)

    def boom(*_a, **_k):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(scans, "_spool_page", boom)
    try:
        async with core_client() as c:
            await c.post("/v1/printers", json={"address": "http://127.0.0.1:%d/eSCL" % fake.port},
                         headers=house["device"])
            r = await c.post("/v1/scan/jobs", json={}, headers=house["device"])
            assert r.status_code == 409 and "went wrong" in r.json()["detail"]
            assert not [s for s in scans._sessions.values() if s.state == "scanning"]
    finally:
        fake.close()


async def test_scan_pages_are_spooled_to_disk_and_the_log_lists_scans(house):
    fake = FakeScanner(adf_pages=2)
    house["lan"](fake.port)
    try:
        async with core_client() as c:
            await c.post("/v1/printers", json={"address": "http://127.0.0.1:%d/eSCL" % fake.port},
                         headers=house["device"])
            s = (await c.post("/v1/scan/jobs", json={}, headers=house["device"])).json()
            session = scans._sessions[s["id"]]
            assert all(isinstance(p, Path) and p.is_file() for p in session.pages)
            spool = session.spool
            done = (await c.post("/v1/scan/jobs/%s/finish" % s["id"], headers=house["device"])).json()
            assert not spool.exists()                           # dropped once saved
            log = (await c.get("/v1/print/jobs", headers=house["device"])).json()
            assert [x["path"] for x in log["scans"]] == [done["path"]]
            assert log["scans"][0]["pages"] == 2
    finally:
        fake.close()


async def test_preview_and_discover_are_budgeted(house, monkeypatch):
    from domovoi import admin_auth
    from domovoi.printing import routes

    monkeypatch.setattr(routes, "PREVIEW_LIMITER", admin_auth.SlidingWindowLimiter(max_per_window=1, window_sec=60))
    monkeypatch.setattr(routes, "DISCOVER_LIMITER", admin_auth.SlidingWindowLimiter(max_per_window=2, window_sec=60))
    monkeypatch.setattr(discovery, "browse_mdns", lambda: mdns.BrowseResult(services=[], heard={}, errors=[]))
    fake = await FakeIppPrinter().start()
    try:
        async with core_client() as c:
            await _add(c, house, fake)
            assert (await _upload(c, house, "A.pdf", _pdf(1), preview=True)).status_code == 200
            r = await _upload(c, house, "A.pdf", _pdf(1), preview=True)
            assert r.status_code == 429
            codes = [(await c.post("/v1/printers/discover", json={}, headers=house["device"])).status_code
                     for _ in range(3)]
            assert codes == [200, 200, 429]
    finally:
        await fake.close()


async def test_the_hourly_limit_holds_for_jobs_arriving_together(house):
    fake = await FakeIppPrinter().start()
    try:
        async with core_client() as c:
            await _add(c, house, fake)
            await c.put("/v1/print/limits", json={"max_jobs_per_hour_per_device": 1}, headers=bearer(house["admin"]))
            results = await asyncio.gather(*(_upload(c, house, "T%d.pdf" % i, _pdf(1)) for i in range(3)))
            assert sorted(r.status_code for r in results) == [202, 429, 429]
    finally:
        await fake.close()


async def test_a_paper_size_the_printer_does_not_take_is_refused(house):
    fake = await FakeIppPrinter().start()
    try:
        async with core_client() as c:
            await _add(c, house, fake)
            for media in ("custom_x_60x60in", "custom_x_30x30in"):
                r = await _upload(c, house, "A.pdf", _pdf(1), preview=True, options={"media": media})
                assert r.status_code == 400, (media, r.text)
    finally:
        await fake.close()


def test_orphaned_scan_spool_is_cleaned_at_start(tmp_path, monkeypatch):
    """A core that stopped mid-scan left its spooled pages behind; the next
    start removes them, and only ours, and only old ones."""
    import os
    import time

    monkeypatch.setattr(scans.tempfile, "gettempdir", lambda: str(tmp_path))
    root = scans.spool_root()
    root.mkdir()
    old, fresh, foreign = root / "scan-old", root / "scan-fresh", root / "not-ours"
    for d in (old, fresh, foreign):
        d.mkdir()
        (d / "page-001.jpg").write_bytes(b"x")
    outside = tmp_path / "scan-elsewhere"
    outside.mkdir()
    long_ago = time.time() - scans.ORPHAN_AFTER_SEC - 60
    for d in (old, foreign, outside):
        os.utime(d, (long_ago, long_ago))
    assert scans.clean_orphaned_spool() == 1
    assert not old.exists() and fresh.exists() and foreign.exists() and outside.exists()
