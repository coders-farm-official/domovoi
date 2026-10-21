"""The network side of printing (no database): mDNS packets, the LAN-only
check (the SSRF limits), the HTTP/1.1 client, the IPP client against our
fake printer (busy, chunked, oversized, TLS pin), the eSCL client against
our fake scanner, status words and options."""

from __future__ import annotations

import asyncio
import ipaddress
import shutil
import socket
import ssl
import struct
import subprocess

import pytest

from domovoi import net_safety
from domovoi.printing import capabilities as caps_mod
from domovoi.printing import escl, http11, ipp, lan_target, mdns, options
from domovoi.printing.client import IppClient, IppError, Printer
from domovoi.tests.printing_fakes import FakeIppPrinter, FakeScanner, scan_page_jpeg

# ─── mDNS ─────────────────────────────────────────────────────────────────


def _name(n: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in n.split(".")) + b"\x00"


def _rr(name: bytes, rtype: int, rdata: bytes) -> bytes:
    return name + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata


def _answer(instance="Office Printer._ipp._tcp.local", host="office.local", ip="192.168.1.50", port=631,
            txt=(b"rp=ipp/print", b"UUID=1234-ABCD", b"ty=Example LaserJet", b"Color=T", b"Duplex=F")) -> bytes:
    hdr = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 3)
    ptr = _rr(_name("_ipp._tcp.local"), 12, _name(instance))
    srv = _rr(_name(instance), 33, struct.pack("!HHH", 0, 0, port) + _name(host))
    txt_rd = b"".join(bytes([len(t)]) + t for t in txt)
    return hdr + ptr + srv + _rr(_name(instance), 16, txt_rd) + _rr(_name(host), 1, socket.inet_aton(ip))


def test_query_asks_ptr_for_every_service_type():
    q = mdns.build_query()
    is_response, _ = mdns.parse_packet(q)
    assert not is_response
    for t in mdns.SERVICE_TYPES:
        assert _name(t) in q


def test_an_answer_becomes_a_service_with_address_and_lowercased_txt():
    res = mdns.collect([(_answer(), "192.168.1.50", "multicast")])
    [svc] = res.services
    assert svc.port == 631 and svc.address == "192.168.1.50"
    assert svc.txt["uuid"] == "1234-ABCD" and svc.txt["color"] == "T"
    assert svc.label == "Office Printer"
    assert res.heard == {"192.168.1.50": {"multicast"}}


def test_compressed_names_and_pointer_loops():
    pkt = bytearray(struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0))
    pkt += _name("_ipp._tcp.local") + struct.pack("!HHIH", 12, 1, 1, 2) + b"\xc0\x0c"
    is_resp, recs = mdns.parse_packet(bytes(pkt))
    assert is_resp and recs[0].value == "_ipp._tcp.local"
    loop = bytearray(struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)) + b"\xc0\x0c"
    with pytest.raises(mdns.MdnsParseError):
        mdns.parse_packet(bytes(loop))


@pytest.mark.parametrize("cut", [13, 30, 60])
def test_truncated_packets_are_dropped_not_raised_through(cut):
    res = mdns.collect([(_answer()[:cut], "192.168.1.50", "unicast")])
    assert res.services == []


def test_srv_before_ptr_still_pairs_up():
    full = _answer()
    hdr = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
    ptr_only = hdr + _rr(_name("_ipp._tcp.local"), 12, _name("Office Printer._ipp._tcp.local"))
    res = mdns.collect([(full, "192.168.1.50", "unicast"), (ptr_only, "192.168.1.50", "multicast")])
    [svc] = res.services
    assert svc.heard_on == {"unicast", "multicast"}


# ─── The LAN-only check ──────────────────────────────────────────────────


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setattr(lan_target, "server_lan_ipv4", lambda: "192.168.1.20")
    monkeypatch.setattr(lan_target, "home_networks", lambda: [ipaddress.IPv4Network("192.168.1.0/24")])
    monkeypatch.setattr(lan_target, "own_addresses", lambda: {"192.168.1.20"})
    monkeypatch.setattr(net_safety, "allow_entries", lambda: ())
    looked_up = []

    def resolve_name(host):
        looked_up.append(host)
        return {"printer.local": ["192.168.1.50"], "evil.local": ["127.0.0.1"],
                "docker.local": ["172.17.0.2"], "split.local": ["192.168.1.50", "10.9.9.9"]}.get(host, [])

    monkeypatch.setattr(lan_target, "resolve_name", resolve_name)
    return looked_up


@pytest.mark.parametrize("addr", [
    "8.8.8.8", "127.0.0.1", "127.1", "0x7f000001", "2130706433", "169.254.169.254", "172.17.0.2",
    "10.0.0.5", "192.168.2.50", "192.168.1.20", "192.168.1.255", "192.168.1.0", "224.0.0.251",
    "100.64.0.1", "::1", "fd00::1", "fe80::1",
])
def test_refused_addresses(home, addr):
    with pytest.raises(lan_target.NotOnLan):
        lan_target.resolve(addr, 631)


def test_a_home_address_passes(home):
    assert lan_target.resolve("192.168.1.50", 631) == "192.168.1.50"
    assert lan_target.resolve("::ffff:192.168.1.50", 631) == "192.168.1.50"


def test_names_resolve_once_and_every_answer_must_pass(home):
    assert lan_target.resolve("printer.local", 631) == "192.168.1.50"
    for bad in ("evil.local", "docker.local", "split.local"):
        with pytest.raises(lan_target.NotOnLan):
            lan_target.resolve(bad, 631)


def test_public_names_are_refused_before_any_lookup(home):
    with pytest.raises(lan_target.NotOnLan):
        lan_target.resolve("printer.example.com", 631)
    assert "printer.example.com" not in home


def test_ports_are_limited_unless_an_admin_typed_the_url(home):
    with pytest.raises(lan_target.NotOnLan, match="usual ports"):
        lan_target.vet("ipp", "192.168.1.50", 8631, "/ipp/print")
    with pytest.raises(lan_target.NotOnLan):
        lan_target.vet("http", "192.168.1.50", 22, "/eSCL")
    t = lan_target.vet("ipp", "192.168.1.50", 8631, "/ipp/print", allow_any_port=True)
    assert t.address == "192.168.1.50" and t.uri == "ipp://192.168.1.50:8631/ipp/print"
    assert lan_target.vet("http", "192.168.1.50", 9095, "/eSCL").port == 9095


def test_the_operator_allowlist_needs_an_exact_port(home, monkeypatch):
    monkeypatch.setattr(net_safety, "allow_entries", lambda: (("127.0.0.1", 8631), ("127.0.0.1", None)))
    assert lan_target.vet("ipp", "127.0.0.1", 8631, "/ipp/print").address == "127.0.0.1"
    with pytest.raises(lan_target.NotOnLan):
        lan_target.vet("ipp", "127.0.0.1", 631, "/ipp/print")


@pytest.mark.parametrize("text,host,port,scheme,path", [
    ("192.168.1.50", "192.168.1.50", None, None, None),
    ("192.168.1.50:631", "192.168.1.50", 631, None, None),
    ("printer.local", "printer.local", None, None, None),
    ("ipp://192.168.1.50/ipp/print", "192.168.1.50", None, "ipp", "/ipp/print"),
    ("ipps://printer.local:443/ipp/print", "printer.local", 443, "ipps", "/ipp/print"),
    ("http://192.168.1.51:8080/eSCL", "192.168.1.51", 8080, "http", "/eSCL"),
])
def test_typed_addresses_parse(text, host, port, scheme, path):
    t = lan_target.parse_typed(text)
    assert (t.host, t.port, t.scheme, t.path) == (host, port, scheme, path)


@pytest.mark.parametrize("text", [
    "", "ftp://192.168.1.50/", "http://user:pw@192.168.1.50/eSCL", "http://192.168.1.50/eSCL?x=1",
    "192.168.1.50:99999", "a/b", "http://192.168.1.50/../etc", "file:///etc/passwd",
])
def test_typed_addresses_that_are_refused(text):
    with pytest.raises(lan_target.NotOnLan):
        lan_target.parse_typed(text)


def test_linux_routes_use_the_default_interface_only(tmp_path, monkeypatch):
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t0101A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t0001A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
        "docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    )
    monkeypatch.setattr(lan_target, "PROC_ROUTE", route)
    assert lan_target._linux_routes() == [ipaddress.IPv4Network("192.168.1.0/24")]


# ─── HTTP/1.1 ────────────────────────────────────────────────────────────


async def _serve(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def test_chunked_answers_are_read_and_bodies_go_with_content_length():
    seen = {}

    async def handler(reader, writer):
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            head += await reader.read(1)
        seen["head"] = head.decode()
        n = int([ln.split(":")[1] for ln in seen["head"].split("\r\n") if ln.lower().startswith("content-length")][0])
        seen["body"] = await reader.readexactly(n)
        writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n")
        await writer.drain()
        writer.close()

    async def go(tmp_file):
        server, port = await _serve(handler)
        try:
            ep = http11.Endpoint("127.0.0.1", port)
            r = await http11.request(ep, "POST", "/ipp/print", body=[b"abc", tmp_file])
        finally:
            server.close()
        return r

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "doc"
        f.write_bytes(b"0123456789")
        r = asyncio.run(go(f))
    assert r.body == b"hello world"
    assert "Content-Length: 13" in seen["head"]
    assert "chunked" not in seen["head"].lower() and "expect" not in seen["head"].lower()
    assert seen["body"] == b"abc0123456789"


def test_answers_over_the_cap_are_refused():
    async def handler(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5000000\r\n\r\n")
        await writer.drain()
        writer.close()

    async def go():
        server, port = await _serve(handler)
        try:
            with pytest.raises(http11.TooLarge):
                await http11.request(http11.Endpoint("127.0.0.1", port), "GET", "/", max_bytes=1000)
        finally:
            server.close()

    asyncio.run(go())


def test_header_injection_is_refused():
    async def go():
        with pytest.raises(http11.HttpError):
            await http11.request(http11.Endpoint("127.0.0.1", 9), "GET", "/x\r\nHost: evil")

    asyncio.run(go())


def test_nothing_listening_is_connect_failed():
    async def go():
        with pytest.raises(http11.ConnectFailed):
            await http11.request(http11.Endpoint("127.0.0.1", 9), "GET", "/", connect_timeout=1.0)

    asyncio.run(go())


# ─── IPP client against the fake printer ─────────────────────────────────


def _printer(fake, tls=False, pin=None):
    return Printer(address="127.0.0.1", port=fake.port, path="/ipp/print", host="127.0.0.1", tls=tls,
                   cert_sha256=pin)


def test_get_printer_attributes_and_capabilities():
    async def go():
        fake = await FakeIppPrinter().start()
        try:
            attrs = await IppClient(_printer(fake)).get_printer_attributes()
        finally:
            await fake.close()
        return fake, attrs

    fake, attrs = asyncio.run(go())
    caps = caps_mod.summarize(attrs)
    assert caps["uuid"] == fake.uuid
    assert caps["color"] and caps["duplex"]
    assert "application/pdf" in caps["formats"]
    assert caps["pwg_resolutions"] == [300, 600]
    assert caps["sheet_back"] == "rotated"
    assert caps["copies_max"] == 99
    assert caps["admin_url"] == "http://192.168.1.50/"
    assert fake.chunked_seen == 0


def test_busy_is_retried_and_the_brother_status_is_success():
    async def go():
        fake = await FakeIppPrinter(busy_answers=2).start()
        client = IppClient(_printer(fake))
        orig = asyncio.sleep

        async def fast_sleep(_s):
            await orig(0)

        try:
            import domovoi.printing.client as client_mod

            client_mod.asyncio.sleep, saved = fast_sleep, client_mod.asyncio.sleep
            try:
                attrs = await client.get_printer_attributes()
            finally:
                client_mod.asyncio.sleep = saved
            jid = await client.print_job([], [b"%PDF-1.4"], document_format="application/pdf", job_name="t")
            ja = await client.get_job_attributes(jid)
        finally:
            await fake.close()
        return attrs, ja

    attrs, ja = asyncio.run(go())
    assert ipp.first(attrs, "printer-uuid")
    assert ipp.first(ja, "job-state") == 9


def test_an_oversized_answer_is_refused():
    async def go():
        fake = await FakeIppPrinter(oversize=True).start()
        try:
            with pytest.raises(http11.TooLarge):
                await IppClient(_printer(fake)).get_printer_attributes()
        finally:
            await fake.close()

    asyncio.run(go())


def _self_signed(tmp_path, name):
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    key, crt = tmp_path / (name + ".key"), tmp_path / (name + ".crt")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", "/CN=printer.local", "-keyout", str(key), "-out", str(crt)],
                   check=True, capture_output=True)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    return ctx


def test_ipps_certificate_is_pinned_and_a_change_sends_nothing(tmp_path):
    first_ctx = _self_signed(tmp_path, "a")
    second_ctx = _self_signed(tmp_path, "b")

    async def go():
        fake = await FakeIppPrinter(ssl_context=first_ctx).start()
        try:
            client = IppClient(_printer(fake, tls=True))
            await client.get_printer_attributes()
            pin = client.last_cert_sha256
            assert pin and len(pin) == 64
            await IppClient(_printer(fake, tls=True, pin=pin)).get_printer_attributes()
        finally:
            await fake.close()
        other = await FakeIppPrinter(ssl_context=second_ctx).start()
        try:
            with pytest.raises(http11.CertificateChanged):
                await IppClient(_printer(other, tls=True, pin=pin)).print_job(
                    [], [b"secret letter"], document_format="application/pdf", job_name="t")
        finally:
            await other.close()
        return other

    other = asyncio.run(go())
    assert other.requests == []          # nothing was sent to the impostor


# ─── eSCL against the fake scanner ───────────────────────────────────────


def _escl(fake):
    c = escl.EsclClient(http11.Endpoint("127.0.0.1", fake.port))

    async def no_sleep(_s):
        return None

    c.sleep = no_sleep
    return c


def test_scanner_capabilities_status_and_a_glass_page():
    fake = FakeScanner(busy_503=2, wrong_location_host=True)
    try:
        async def go():
            c = _escl(fake)
            caps = await c.capabilities()
            st = await c.status()
            job = await c.start(escl.scan_settings_xml(source="platen", color_mode="RGB24", resolution=300,
                                                       width=2550, height=3300))
            page = await c.next_page(job, first=True)
            await c.delete(job)
            return caps, st, job, page

        caps, st, job, page = asyncio.run(go())
    finally:
        fake.close()
    assert caps.make_and_model == "Example ScanJet 10" and caps.sources() == ["platen", "adf"]
    assert caps.platen.resolutions == [150, 300]
    assert st.state == "Idle"
    assert job.startswith("/eSCL/ScanJobs/")          # the wrong Location host was dropped
    assert page == scan_page_jpeg()
    assert "<pwg:InputSource>Platen</pwg:InputSource>" in fake.settings[0]


def test_feeder_pages_then_404_and_a_jam_is_refused():
    fake = FakeScanner(adf_pages=3)
    try:
        async def go():
            c = _escl(fake)
            job = await c.start(escl.scan_settings_xml(source="adf", color_mode="Grayscale8", resolution=300,
                                                       width=2550, height=3300))
            pages = []
            while True:
                p = await c.next_page(job, first=not pages)
                if p is None:
                    break
                pages.append(p)
            return pages

        assert len(asyncio.run(go())) == 3
    finally:
        fake.close()
    jam = FakeScanner(adf_state="ScannerAdfJam")
    try:
        async def go2():
            c = _escl(jam)
            assert (await c.status()).adf_jammed
            with pytest.raises(escl.EsclError):
                await c.start(escl.scan_settings_xml(source="adf", color_mode="RGB24", resolution=300,
                                                     width=2550, height=3300))

        asyncio.run(go2())
    finally:
        jam.close()


def test_scanner_xml_with_a_doctype_is_refused():
    fake = FakeScanner(doctype=True)
    try:
        with pytest.raises(escl.EsclError, match="DOCTYPE"):
            asyncio.run(_escl(fake).capabilities())
    finally:
        fake.close()
    with pytest.raises(escl.EsclError):
        escl.parse_xml(b"<a>" + b"x" * (escl.MAX_XML + 1) + b"</a>")


# ─── Status words, supplies, options ─────────────────────────────────────


def test_marker_supplies_and_low_levels():
    attrs = {"marker-names": ["BK", "C", "M", "Y"], "marker-levels": [60, -3, 90, 8],
             "marker-low-levels": [10, 10, 10, 10], "marker-colors": ["#000000", "#00FFFF", "red", "#FFFF00"],
             "marker-types": ["toner"] * 4}
    s = caps_mod.supplies_of(attrs)
    assert [x["name"] for x in s] == ["Black toner", "Cyan toner", "Magenta toner", "Yellow toner"]
    assert s[1]["level"] is None                # -3: "some left", never a number
    assert s[2]["color"] is None                # not #rrggbb: no swatch
    assert s[3]["low"] is True
    spoken = caps_mod.spoken_supplies("office printer", s)
    assert spoken.startswith("The office printer: black about 60 percent")
    assert "yellow is at 8 percent" in spoken


def test_printer_supply_is_preferred():
    attrs = {"printer-supply": ["type=tonerCartridge;maxcapacity=100;level=40;colorantname=black;"],
             "printer-supply-description": ["Black Toner Cartridge"], "marker-names": ["ignored"]}
    s = caps_mod.supplies_of(attrs)
    assert s == [{"name": "Black Toner Cartridge", "level": 40, "color": "#000000", "kind": "toner", "low": False}]


@pytest.mark.parametrize("reasons,state,tone", [
    (["none"], "ready", "ok"),
    (["media-empty-error"], "out of paper", "err"),
    (["media-jam-error", "toner-low-report"], "jammed", "err"),
    (["toner-low-report"], "low on toner", "warn"),
    (["door-open-error"], "cover open", "err"),
    (["marker-waste-full-error"], "needs service", "err"),
])
def test_state_reasons_in_plain_words(reasons, state, tone):
    st = caps_mod.status_of({"printer-state": [3], "printer-state-reasons": reasons}, "office printer")
    assert (st["state"], st["tone"]) == (state, tone)


def test_offline_words():
    st = caps_mod.status_of(None, "office printer", offline=True)
    assert st["words"] == ["The office printer isn't answering. Is it switched on?"]


@pytest.mark.parametrize("zone,paper", [
    ("America/New_York", options.LETTER), ("America/Toronto", options.LETTER), ("Asia/Manila", options.LETTER),
    ("America/Mexico_City", options.LETTER), ("Europe/London", options.A4), ("America/Sao_Paulo", options.A4),
    ("Australia/Sydney", options.A4), ("Eastern Standard Time Eastern Daylight Time", options.LETTER),
])
def test_paper_by_country(zone, paper):
    assert options.household_paper(zone) == paper


def test_page_ranges():
    assert options.parse_page_ranges("1-3, 5, 7-") == [(1, 3), (5, 5), (7, 99999)]
    for bad in ("0", "3-1", "a", "1--2"):
        with pytest.raises(options.OptionError):
            options.parse_page_ranges(bad)


def test_format_choice_and_adjustments():
    caps = {"formats": ["image/urf", "image/jpeg"], "urf": ["W8", "SRGB24", "RS600"], "sides": ["one-sided"],
            "color": False, "media_default": "iso_a4_210x297mm"}
    r = options.resolve(caps, {"sides": "two-sided-long-edge", "color": "color"}, two_sided=None, color=None,
                        max_copies=20)
    assert (r.format, r.dpi, r.sides, r.color, r.media) == ("image/urf", 600, "one-sided", "monochrome",
                                                            "iso_a4_210x297mm")
    assert len(r.adjustments) == 2
    caps2 = {"formats": ["application/pdf", "image/pwg-raster"], "pwg_resolutions": [300, 600],
             "sides": ["one-sided", "two-sided-long-edge"], "sides_default": "one-sided", "color": True,
             "color_default": "color"}
    r2 = options.resolve(caps2, {}, two_sided=True, color=False, max_copies=20)
    assert (r2.format, r2.sides, r2.color) == ("application/pdf", "two-sided-long-edge", "monochrome")
    assert options.pick_format(caps2, force_raster=True) == ("image/pwg-raster", 300)
    with pytest.raises(options.OptionError):
        options.pick_format({"formats": ["application/postscript"]})


def test_sheets_and_summary():
    assert options.sheets_for(3, 2, "two-sided-long-edge") == 4
    assert options.summary(3, 2, "two-sided-long-edge", 1) == "3 pages · 2 sheets of paper · two-sided"


def test_job_attributes_carry_media_col_and_ranges():
    caps = {"sides": ["one-sided", "two-sided-long-edge"], "color_modes": ["color", "monochrome"],
            "print_scaling": ["fit"]}
    r = options.Resolved(sides="two-sided-long-edge", color="monochrome", media="na_letter_8.5x11in", copies=2,
                         pages_spec="1-2", format="application/pdf")
    attrs = options.job_attributes(r, caps, send_copies=True, page_ranges=[(1, 2)])
    names = [a.name for a in attrs]
    assert names == ["copies", "sides", "print-color-mode", "media-col", "page-ranges", "print-scaling"]
    msg = ipp.decode(ipp.encode_request(0, 1, [(ipp.JOB, attrs)]))
    assert msg.group(ipp.JOB)["media-col"][0]["media-size"][0] == {"x-dimension": [21590], "y-dimension": [27940]}
