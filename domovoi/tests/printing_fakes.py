"""Fake devices for the printing tests: an IPP printer and an eSCL scanner.

Both are ours (MIT), built on our own codec, bound to 127.0.0.1 only. The
scanner's XML is our own minimal document using the public eSCL
namespaces (no spec text); its pages are the JPEG fixture in
``fixtures/printing``.

FakeIppPrinter (asyncio, in the test's loop) scripts what real printers do
that a test needs: ``printer-state-reasons``, ``marker-*`` and
``printer-supply``, ``server-error-busy`` answers, refusing chunked bodies,
Brother's 0x0001 on Get-Job-Attributes, jobs that complete, abort or stop,
a bogus oversized answer, and TLS with a given certificate.

FakeScanner (a thread, so it also serves the Linux harness's plain
``python3``): capabilities, status, 201 + Location (optionally naming the
wrong host), JPEG pages then 404, N × 503 busy, feeder pages, feeder jam
and empty. Standard library only.
"""

from __future__ import annotations

import asyncio
import re
import ssl
import threading
import uuid as uuid_mod
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from domovoi.printing import ipp
from domovoi.printing.ipp import Attr

FIXTURES = Path(__file__).parent / "fixtures" / "printing"


def scan_page_jpeg() -> bytes:
    return (FIXTURES / "scan-page.jpg").read_bytes()


# ─── IPP printer ─────────────────────────────────────────────────────────


@dataclass
class FakeJob:
    id: int
    attrs: dict[str, list[Any]]
    format: str = ""
    data: bytes = b""
    state: int = 3
    polls: int = 0
    impressions: int = 0


@dataclass
class FakeIppPrinter:
    formats: list[str] = field(default_factory=lambda: ["application/pdf", "image/pwg-raster", "image/urf",
                                                         "image/jpeg"])
    uuid: str = field(default_factory=lambda: str(uuid_mod.uuid4()))
    make_and_model: str = "Example LaserJet 100"
    reasons: list[str] = field(default_factory=lambda: ["none"])
    printer_state: int = 3
    markers: dict[str, list[Any]] | None = None
    supply: list[str] | None = None
    sides: list[str] = field(default_factory=lambda: ["one-sided", "two-sided-long-edge", "two-sided-short-edge"])
    color: bool = True
    busy_answers: int = 0
    refuse_chunked: bool = True
    job_outcome: str = "complete"      # complete | abort | stop
    complete_after_polls: int = 1
    get_job_status: int = 0x0001       # Brother answers ignored-or-substituted
    oversize: bool = False
    operations: list[int] = field(default_factory=lambda: [2, 4, 5, 6, 8, 9, 10, 11, 60])
    copies_max: int = 99
    unsupported_on_validate: list[str] = field(default_factory=list)
    ssl_context: ssl.SSLContext | None = None
    jobs: dict[int, FakeJob] = field(default_factory=dict)
    requests: list[tuple[str, dict[str, list[Any]]]] = field(default_factory=list)
    chunked_seen: int = 0
    port: int = 0
    _server: Any = None
    _next_job: int = 100

    async def start(self, host: str = "127.0.0.1") -> "FakeIppPrinter":
        self._server = await asyncio.start_server(self._client, host, 0, ssl=self.ssl_context)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def printer_attrs(self) -> list[Attr]:
        a = [
            Attr.of(ipp.URI, "printer-uri-supported", "ipp://192.168.1.50/ipp/print"),
            Attr.of(ipp.URI, "printer-uuid", "urn:uuid:" + self.uuid),
            Attr.of(ipp.TEXT, "printer-make-and-model", self.make_and_model),
            Attr.of(ipp.TEXT, "printer-location", "upstairs"),
            Attr.of(ipp.ENUM, "printer-state", self.printer_state),
            Attr.of(ipp.KEYWORD, "printer-state-reasons", self.reasons),
            Attr.of(ipp.TEXT, "printer-state-message", "Ready\x1b[31m"),
            Attr.of(ipp.MIME_TYPE, "document-format-supported", self.formats),
            Attr.of(ipp.KEYWORD, "sides-supported", self.sides),
            Attr.of(ipp.KEYWORD, "sides-default", "one-sided"),
            Attr.of(ipp.KEYWORD, "print-color-mode-supported",
                    ["auto", "color", "monochrome"] if self.color else ["monochrome"]),
            Attr.of(ipp.KEYWORD, "print-color-mode-default", "color" if self.color else "monochrome"),
            Attr.of(ipp.BOOLEAN, "color-supported", self.color),
            Attr.of(ipp.KEYWORD, "media-default", "na_letter_8.5x11in"),
            Attr.of(ipp.KEYWORD, "media-ready", ["na_letter_8.5x11in"]),
            Attr.of(ipp.KEYWORD, "media-supported", ["na_letter_8.5x11in", "iso_a4_210x297mm",
                                                     "na_legal_8.5x14in"]),
            Attr(ipp.RESOLUTION, "pwg-raster-document-resolution-supported",
                 [ipp.Resolution(300, 300), ipp.Resolution(600, 600)]),
            Attr.of(ipp.KEYWORD, "pwg-raster-document-type-supported", ["sgray_8", "srgb_8"]),
            Attr.of(ipp.KEYWORD, "pwg-raster-document-sheet-back", "rotated"),
            Attr.of(ipp.KEYWORD, "urf-supported", ["V1.4", "W8", "SRGB24", "RS300-600", "DM1", "PQ4"]),
            Attr.of(ipp.RANGE, "copies-supported", ipp.Range(1, self.copies_max)),
            Attr.of(ipp.BOOLEAN, "page-ranges-supported", True),
            Attr.of(ipp.ENUM, "operations-supported", self.operations),
            Attr.of(ipp.KEYWORD, "identify-actions-supported", ["flash"]),
            Attr.of(ipp.URI, "printer-more-info", "http://192.168.1.50/"),
            Attr.of(ipp.KEYWORD, "print-scaling-supported", ["auto", "fit", "fill", "none"]),
        ]
        markers = self.markers if self.markers is not None else {
            "marker-names": ["BK", "C", "M", "Y"], "marker-levels": [60, 100, 90, 8],
            "marker-low-levels": [10, 10, 10, 10], "marker-colors": ["#000000", "#00FFFF", "#FF00FF", "#FFFF00"],
            "marker-types": ["toner", "toner", "toner", "toner"],
        }
        for name, vals in markers.items():
            tag = ipp.INTEGER if name in ("marker-levels", "marker-low-levels", "marker-high-levels") else (
                ipp.KEYWORD if name == "marker-types" else ipp.NAME)
            a.append(Attr(tag, name, list(vals)))
        if self.supply is not None:
            a.append(Attr(ipp.OCTET_STRING, "printer-supply", [s.encode() for s in self.supply]))
        if self.oversize:
            a.append(Attr(ipp.TEXT, "printer-info", ["x" * 30000] * 40))
        return a

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            headers = {}
            while True:
                h = await reader.readline()
                if h in (b"\r\n", b"\n", b""):
                    break
                k, _, v = h.decode("latin-1").partition(":")
                headers[k.strip().lower()] = v.strip()
            if "chunked" in headers.get("transfer-encoding", "").lower():
                self.chunked_seen += 1
                if self.refuse_chunked:
                    writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                    await writer.drain()
                    return
            n = int(headers.get("content-length", "0"))
            body = await reader.readexactly(n)
            status, out = self.handle(body)
            writer.write(b"HTTP/1.1 %d OK\r\nContent-Type: application/ipp\r\nContent-Length: %d\r\n"
                         b"Connection: close\r\n\r\n" % (status, len(out)) + out)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            pass
        finally:
            writer.close()

    def _parse_request(self, body: bytes) -> tuple[int, int, dict[str, list[Any]], dict[str, list[Any]], bytes]:
        # Reuse the decoder: a request has the same layout as a response
        # (op where status is). Find where the attributes end to get the data.
        end = _attrs_end(body)
        msg = ipp.decode(body[:end])
        return msg.status, msg.request_id, msg.group(ipp.OPERATION), msg.group(ipp.JOB), body[end:]

    def handle(self, body: bytes) -> tuple[int, bytes]:
        op, rid, opg, jobg, data = self._parse_request(body)
        name = {v: k for k, v in ipp.OPS.items()}.get(op, hex(op))
        self.requests.append((name, opg | {"_job": jobg}))  # type: ignore[operator]
        base = [Attr.of(ipp.CHARSET, "attributes-charset", "utf-8"),
                Attr.of(ipp.LANGUAGE, "attributes-natural-language", "en")]

        def reply(status: int, *groups: tuple[int, list[Attr]]) -> tuple[int, bytes]:
            return 200, ipp.encode_request(status, rid, [(ipp.OPERATION, base), *groups], version=(2, 0))

        if self.busy_answers > 0:
            self.busy_answers -= 1
            return reply(ipp.STATUS_BUSY)
        if name == "Get-Printer-Attributes":
            return reply(0, (ipp.PRINTER, self.printer_attrs()))
        if name == "Validate-Job":
            uns = [Attr.of(ipp.KEYWORD, n, "x") for n in self.unsupported_on_validate if n in jobg]
            if uns:
                return reply(ipp.STATUS_ATTRIBUTES_NOT_SUPPORTED, (ipp.UNSUPPORTED, uns))
            return reply(0)
        if name in ("Create-Job", "Print-Job"):
            if op not in self.operations:
                return reply(ipp.STATUS_OPERATION_NOT_SUPPORTED)
            fmt = ipp.first(opg, "document-format", "")
            if name == "Print-Job" and fmt not in self.formats:
                return reply(ipp.STATUS_FORMAT_NOT_SUPPORTED)
            self._next_job += 1
            job = FakeJob(id=self._next_job, attrs=jobg)
            self.jobs[job.id] = job
            if name == "Print-Job":
                job.format, job.data, job.state = fmt, data, 5
            return reply(0, (ipp.JOB, [Attr.of(ipp.INTEGER, "job-id", job.id),
                                       Attr.of(ipp.ENUM, "job-state", job.state)]))
        if name == "Send-Document":
            job = self.jobs.get(ipp.first(opg, "job-id", -1))
            if job is None:
                return reply(0x0406)
            fmt = ipp.first(opg, "document-format", "")
            if fmt not in self.formats:
                return reply(ipp.STATUS_FORMAT_NOT_SUPPORTED)
            if job.state == 7:
                return reply(0x0407)
            job.format, job.data, job.state = fmt, data, 5
            return reply(0, (ipp.JOB, [Attr.of(ipp.INTEGER, "job-id", job.id)]))
        if name == "Get-Job-Attributes":
            job = self.jobs.get(ipp.first(opg, "job-id", -1))
            if job is None:
                return reply(0x0406)
            if job.state == 5:
                job.polls += 1
                if job.polls >= self.complete_after_polls:
                    job.state = {"complete": 9, "abort": 8, "stop": 6}[self.job_outcome]
                    job.impressions = 1
                    if self.job_outcome == "stop":
                        self.reasons = ["media-empty-error"]
                        self.printer_state = 5
            return reply(self.get_job_status, (ipp.JOB, [
                Attr.of(ipp.INTEGER, "job-id", job.id), Attr.of(ipp.ENUM, "job-state", job.state),
                Attr.of(ipp.KEYWORD, "job-state-reasons", ["none"]),
                Attr.of(ipp.INTEGER, "job-impressions-completed", job.impressions)]))
        if name == "Cancel-Job":
            job = self.jobs.get(ipp.first(opg, "job-id", -1))
            if job is None:
                return reply(0x0406)
            job.state = 7
            return reply(0)
        if name == "Identify-Printer":
            return reply(0)
        return reply(ipp.STATUS_OPERATION_NOT_SUPPORTED)


def _attrs_end(body: bytes) -> int:
    """Offset just past end-of-attributes-tag in a request."""
    import struct

    off = 8
    while off < len(body):
        tag = body[off]
        off += 1
        if tag == ipp.END:
            return off
        if tag < 0x10:
            continue
        nl = struct.unpack("!H", body[off:off + 2])[0]
        off += 2 + nl
        vl = struct.unpack("!H", body[off:off + 2])[0]
        off += 2 + vl
    return off


# ─── eSCL scanner ────────────────────────────────────────────────────────

PWG = "http://www.pwg.org/schemas/2010/12/sm"
SCAN = "http://schemas.hp.com/imaging/escl/2011/05/03"
_INPUT_CAPS = (
    "<scan:MinWidth>16</scan:MinWidth><scan:MaxWidth>2550</scan:MaxWidth>"
    "<scan:MinHeight>16</scan:MinHeight><scan:MaxHeight>3508</scan:MaxHeight>"
    "<scan:SettingProfiles><scan:SettingProfile>"
    "<scan:ColorModes><scan:ColorMode>Grayscale8</scan:ColorMode><scan:ColorMode>RGB24</scan:ColorMode>"
    "</scan:ColorModes>"
    "<scan:DocumentFormats><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>"
    "<scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt></scan:DocumentFormats>"
    "<scan:SupportedResolutions><scan:DiscreteResolutions>"
    "<scan:DiscreteResolution><scan:XResolution>150</scan:XResolution><scan:YResolution>150</scan:YResolution>"
    "</scan:DiscreteResolution>"
    "<scan:DiscreteResolution><scan:XResolution>300</scan:XResolution><scan:YResolution>300</scan:YResolution>"
    "</scan:DiscreteResolution>"
    "</scan:DiscreteResolutions></scan:SupportedResolutions>"
    "</scan:SettingProfile></scan:SettingProfiles>"
)


class FakeScanner:
    def __init__(self, *, busy_503: int = 0, adf_pages: int = 0, adf_state: str | None = None,
                 wrong_location_host: bool = False, uuid: str | None = None, doctype: bool = False,
                 die_after_pages: int | None = None) -> None:
        self.uuid = uuid or str(uuid_mod.uuid4())
        self.busy_503, self.adf_pages = busy_503, adf_pages
        self.adf_state = adf_state or ("ScannerAdfLoaded" if adf_pages else "ScannerAdfEmpty")
        self.wrong_location_host = wrong_location_host
        self.doctype = doctype
        self.die_after_pages = die_after_pages
        self.jobs: dict[str, dict[str, Any]] = {}
        self.state = "Idle"
        self.log: list[str] = []
        self.settings: list[str] = []
        self.sent = 0
        self.page = scan_page_jpeg()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                fake.log.append(self.requestline)

            def _send(self, code: int, body: bytes = b"", ctype: str = "text/xml",
                      extra: dict[str, str] | None = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/eSCL/ScannerCapabilities":
                    doctype = '<!DOCTYPE x [<!ENTITY a "aaaa">]>' if fake.doctype else ""
                    xml = ('<?xml version="1.0" encoding="UTF-8"?>%s<scan:ScannerCapabilities xmlns:pwg="%s" '
                           'xmlns:scan="%s"><pwg:Version>2.63</pwg:Version>'
                           "<pwg:MakeAndModel>Example ScanJet 10</pwg:MakeAndModel><scan:UUID>%s</scan:UUID>"
                           "<scan:Platen><scan:PlatenInputCaps>%s</scan:PlatenInputCaps></scan:Platen>"
                           "<scan:Adf><scan:AdfSimplexInputCaps>%s</scan:AdfSimplexInputCaps></scan:Adf>"
                           "</scan:ScannerCapabilities>") % (doctype, PWG, SCAN, fake.uuid, _INPUT_CAPS, _INPUT_CAPS)
                    return self._send(200, xml.encode())
                if self.path == "/eSCL/ScannerStatus":
                    xml = ('<?xml version="1.0" encoding="UTF-8"?><scan:ScannerStatus xmlns:pwg="%s" xmlns:scan="%s">'
                           "<pwg:Version>2.63</pwg:Version><pwg:State>%s</pwg:State>"
                           "<scan:AdfState>%s</scan:AdfState></scan:ScannerStatus>") % (
                        PWG, SCAN, fake.state, fake.adf_state)
                    return self._send(200, xml.encode())
                m = re.fullmatch(r"/eSCL/ScanJobs/([0-9a-f-]{36})/NextDocument", self.path)
                if m and m.group(1) in fake.jobs:
                    job = fake.jobs[m.group(1)]
                    if fake.busy_503 > 0:
                        fake.busy_503 -= 1
                        return self._send(503)
                    if fake.die_after_pages is not None and fake.sent >= fake.die_after_pages:
                        return self._send(500)
                    if job["left"] <= 0:
                        fake.state = "Idle"
                        return self._send(404)
                    job["left"] -= 1
                    fake.sent += 1
                    return self._send(200, fake.page, "image/jpeg")
                return self._send(404)

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/eSCL/ScanJobs":
                    return self._send(404)
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(min(n, 65536)).decode("utf-8", "replace")
                fake.settings.append(body)
                if fake.state != "Idle":
                    return self._send(503)
                src = re.search(r"<pwg:InputSource>(\w+)</pwg:InputSource>", body)
                src_name = src.group(1) if src else "Platen"
                if src_name == "Feeder" and fake.adf_state != "ScannerAdfLoaded":
                    return self._send(409)
                jid = str(uuid_mod.uuid4())
                left = fake.adf_pages if src_name == "Feeder" else 1
                if src_name == "Feeder":
                    fake.adf_pages = 0
                    fake.adf_state = "ScannerAdfEmpty"
                fake.jobs[jid] = {"source": src_name, "left": left}
                fake.state = "Processing"
                host = "203.0.113.9" if fake.wrong_location_host else self.headers.get("Host", "127.0.0.1")
                return self._send(201, extra={"Location": "http://%s/eSCL/ScanJobs/%s" % (host, jid)})

            def do_DELETE(self) -> None:  # noqa: N802
                m = re.fullmatch(r"/eSCL/ScanJobs/([0-9a-f-]{36})", self.path)
                if m and fake.jobs.pop(m.group(1), None) is not None:
                    fake.state = "Idle"
                    return self._send(200)
                return self._send(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# ─── A pretend home network on loopback ──────────────────────────────────


_REAL_LAN: dict = {}


def _snapshot_real_lan() -> None:
    from domovoi.printing import lan_target

    if not _REAL_LAN:
        _REAL_LAN.update({k: getattr(lan_target, k) for k in (
            "_REFUSED_V4", "_PRIVATE_V4", "home_networks", "own_addresses", "server_lan_ipv4",
            "IPP_PORTS", "ESCL_PORTS")})


def real_lan(monkeypatch, home: str = "192.168.1.0/24", me: str = "192.168.1.20") -> None:
    """Undo :func:`fake_lan` for one test: loopback is refused again, and
    the home network is ``home`` with the server at ``me``."""
    import ipaddress

    from domovoi.printing import lan_target

    _snapshot_real_lan()
    for k in ("_REFUSED_V4", "_PRIVATE_V4", "IPP_PORTS", "ESCL_PORTS"):
        monkeypatch.setattr(lan_target, k, _REAL_LAN[k])
    monkeypatch.setattr(lan_target, "home_networks", lambda: [ipaddress.IPv4Network(home)])
    monkeypatch.setattr(lan_target, "own_addresses", lambda: {me})
    monkeypatch.setattr(lan_target, "server_lan_ipv4", lambda: me)


def fake_lan(monkeypatch):
    """Make 127.0.0.0/24 the "home network" for one test, so the fakes on
    127.0.0.x pass the LAN check the way a real printer on 192.168.1.x
    would (no operator allowlist involved), and the server itself is
    127.0.0.250. Returns ``open_port(port)``: the fakes listen on random
    ports, which the usual-ports rule would refuse."""
    import ipaddress

    from domovoi.printing import lan_target

    _snapshot_real_lan()
    loop = ipaddress.IPv4Network("127.0.0.0/8")
    monkeypatch.setattr(lan_target, "_REFUSED_V4", tuple(n for n in lan_target._REFUSED_V4 if n != loop))
    monkeypatch.setattr(lan_target, "_PRIVATE_V4", lan_target._PRIVATE_V4 + (loop,))
    monkeypatch.setattr(lan_target, "home_networks", lambda: [ipaddress.IPv4Network("127.0.0.0/24")])
    monkeypatch.setattr(lan_target, "own_addresses", lambda: {"127.0.0.250"})
    monkeypatch.setattr(lan_target, "server_lan_ipv4", lambda: "127.0.0.250")
    ipp_ports, escl_ports = set(lan_target.IPP_PORTS), set(lan_target.ESCL_PORTS)
    monkeypatch.setattr(lan_target, "IPP_PORTS", ipp_ports)
    monkeypatch.setattr(lan_target, "ESCL_PORTS", escl_ports)

    def open_port(port: int) -> None:
        ipp_ports.add(port)
        escl_ports.add(port)

    return open_port
