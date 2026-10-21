"""The eSCL ("AirScan", Mopria eSCL Scan) client: HTTP plus XML.

Built from open behaviour only — Google's Apache-2.0 Go client
(github.com/stapelberg/airscan) and captured device XML. Mopria's spec is
confidential under its licence, so no spec text and no code from GPL
clients is in this repo (plan part 5).

The flow::

    GET  /eSCL/ScannerCapabilities   sources, colour modes, resolutions
    GET  /eSCL/ScannerStatus         pwg:State, scan:AdfState
    POST /eSCL/ScanJobs              201 Created + Location
    GET  <job>/NextDocument          200 page; 503 busy (retry); 404 no more
    DELETE <job>                     done (sent anyway)

Quirks: the host in ``Location`` is rewritten to the address we dialled
(some devices name the wrong one), and only the job path is kept; a 503 is
retried every second up to 30 times; some Xerox models answer 404/410
*temporarily* right after the job starts, so the first page tolerates a
few of those.

Privacy: we never fetch anyone else's job (``ScannerStatus`` can list
them) and never act on a push-scan ``DestinationUri``.

XML from a scanner is data: at most :data:`MAX_XML` bytes, and any document
with a DOCTYPE or an ENTITY is refused before parsing (no entity expansion,
no external entities) — the standard library parser then has nothing
dangerous left to do. Standard library only.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from domovoi.printing import http11

PWG = "http://www.pwg.org/schemas/2010/12/sm"
SCAN = "http://schemas.hp.com/imaging/escl/2011/05/03"
MAX_XML = 256 * 1024
MAX_PAGE = 60 * 1024 * 1024
BUSY_RETRIES = 30
_JOB_PATH = re.compile(r"^/[A-Za-z0-9._~/-]{1,200}$")


class EsclError(Exception):
    def __init__(self, message: str, *, kind: str = "error", http_status: int = 0) -> None:
        super().__init__(message)
        self.kind = kind        # error | busy | jam | empty | gone
        self.http_status = http_status


def parse_xml(data: bytes) -> ET.Element:
    if len(data) > MAX_XML:
        raise EsclError("the scanner's answer was too large")
    head = data[:4096].lower()
    if b"<!doctype" in data.lower() or b"<!entity" in head or b"<!entity" in data.lower():
        raise EsclError("the scanner's answer had a DOCTYPE; refused")
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise EsclError("the scanner's answer didn't make sense") from e


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_all(el: ET.Element, name: str) -> list[ET.Element]:
    return [e for e in el.iter() if _local(e.tag) == name]


def _text(el: ET.Element | None, cap: int = 200) -> str:
    if el is None or el.text is None:
        return ""
    return "".join(ch for ch in el.text.strip()[:cap] if ch >= " " and ch != "\x7f")


@dataclass
class SourceCaps:
    color_modes: list[str] = field(default_factory=list)
    resolutions: list[int] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    max_width: int = 2550     # 1/300 inch
    max_height: int = 3508


@dataclass
class Capabilities:
    make_and_model: str
    uuid: str
    admin_uri: str
    platen: SourceCaps | None
    adf: SourceCaps | None
    adf_duplex: bool

    def sources(self) -> list[str]:
        out = []
        if self.platen:
            out.append("platen")
        if self.adf:
            out.append("adf")
            if self.adf_duplex:
                out.append("adf_duplex")
        return out

    def to_json(self) -> dict:
        def caps(c: SourceCaps | None):
            return None if c is None else {
                "color_modes": c.color_modes, "resolutions": c.resolutions,
                "formats": c.formats, "max_width": c.max_width, "max_height": c.max_height,
            }
        return {
            "make_and_model": self.make_and_model, "uuid": self.uuid,
            "platen": caps(self.platen), "adf": caps(self.adf), "adf_duplex": self.adf_duplex,
        }


def _source_caps(el: ET.Element) -> SourceCaps:
    sc = SourceCaps()
    for e in _find_all(el, "ColorMode"):
        v = _text(e, 40)
        if v and v not in sc.color_modes:
            sc.color_modes.append(v)
    for e in _find_all(el, "XResolution"):
        try:
            r = int(_text(e, 10))
        except ValueError:
            continue
        if 50 <= r <= 2400 and r not in sc.resolutions:
            sc.resolutions.append(r)
    for name in ("DocumentFormatExt", "DocumentFormat"):
        for e in _find_all(el, name):
            v = _text(e, 60)
            if v and v not in sc.formats:
                sc.formats.append(v)
    for attr, name in (("max_width", "MaxWidth"), ("max_height", "MaxHeight")):
        found = _find_all(el, name)
        if found:
            try:
                setattr(sc, attr, max(16, min(int(_text(found[0], 10)), 30000)))
            except ValueError:
                pass
    sc.resolutions.sort()
    return sc


def parse_capabilities(data: bytes) -> Capabilities:
    root = parse_xml(data)
    if _local(root.tag) != "ScannerCapabilities":
        raise EsclError("that isn't a scanner")
    mm = _find_all(root, "MakeAndModel")
    uu = _find_all(root, "UUID")
    admin = _find_all(root, "AdminURI")
    platen = _find_all(root, "PlatenInputCaps")
    adf_simplex = _find_all(root, "AdfSimplexInputCaps")
    adf_duplex = _find_all(root, "AdfDuplexInputCaps")
    adf = adf_simplex or adf_duplex
    return Capabilities(
        make_and_model=_text(mm[0] if mm else None, 120) or "Scanner",
        uuid=_text(uu[0] if uu else None, 80).lower().removeprefix("urn:uuid:"),
        admin_uri=_text(admin[0] if admin else None, 300),
        platen=_source_caps(platen[0]) if platen else None,
        adf=_source_caps(adf[0]) if adf else None,
        adf_duplex=bool(adf_duplex),
    )


@dataclass
class Status:
    state: str       # Idle | Processing | Testing | Stopped | Down
    adf_state: str   # ScannerAdfLoaded | ScannerAdfEmpty | ScannerAdfJam | ... | ""

    @property
    def adf_loaded(self) -> bool:
        return self.adf_state == "ScannerAdfLoaded"

    @property
    def adf_jammed(self) -> bool:
        return self.adf_state in ("ScannerAdfJam", "ScannerAdfMispick", "ScannerAdfHatchOpen")


def parse_status(data: bytes) -> Status:
    root = parse_xml(data)
    st = [e for e in root if _local(e.tag) == "State"]
    adf = _find_all(root, "AdfState")
    return Status(state=_text(st[0] if st else None, 40), adf_state=_text(adf[0] if adf else None, 40))


def scan_settings_xml(
    *, source: str, color_mode: str, resolution: int, width: int, height: int,
    duplex: bool = False, document_format: str = "image/jpeg",
) -> bytes:
    """Our own ScanSettings document (public namespaces; no spec text)."""
    input_source = {"platen": "Platen", "adf": "Feeder", "adf_duplex": "Feeder"}[source]
    duplex_xml = "<scan:Duplex>%s</scan:Duplex>" % ("true" if duplex else "false") if source != "platen" else ""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<scan:ScanSettings xmlns:pwg="%s" xmlns:scan="%s">'
        "<pwg:Version>2.0</pwg:Version>"
        "<scan:Intent>Document</scan:Intent>"
        "<pwg:ScanRegions><pwg:ScanRegion>"
        "<pwg:Height>%d</pwg:Height><pwg:Width>%d</pwg:Width>"
        "<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>"
        "<pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>"
        "</pwg:ScanRegion></pwg:ScanRegions>"
        "<pwg:InputSource>%s</pwg:InputSource>%s"
        "<scan:ColorMode>%s</scan:ColorMode>"
        "<scan:XResolution>%d</scan:XResolution><scan:YResolution>%d</scan:YResolution>"
        "<pwg:DocumentFormat>%s</pwg:DocumentFormat>"
        "<scan:DocumentFormatExt>%s</scan:DocumentFormatExt>"
        "</scan:ScanSettings>"
    ) % (PWG, SCAN, height, width, input_source, duplex_xml, color_mode, resolution, resolution,
         document_format, document_format)
    return xml.encode("utf-8")


class EsclClient:
    def __init__(self, endpoint: http11.Endpoint, base_path: str = "/eSCL", *, timeout: float = 30.0) -> None:
        self.endpoint = endpoint
        self.base = "/" + base_path.strip("/") if base_path.strip("/") else ""
        self.timeout = timeout
        self.sleep = asyncio.sleep  # patched by tests

    async def _get(self, path: str, max_bytes: int = MAX_XML) -> http11.HttpResponse:
        return await http11.request(self.endpoint, "GET", path, max_bytes=max_bytes, timeout=self.timeout)

    async def capabilities(self) -> Capabilities:
        r = await self._get(self.base + "/ScannerCapabilities")
        if r.status != 200:
            raise EsclError("the scanner answered HTTP %d" % r.status, http_status=r.status)
        return parse_capabilities(r.body)

    async def status(self) -> Status:
        r = await self._get(self.base + "/ScannerStatus")
        if r.status != 200:
            raise EsclError("the scanner answered HTTP %d" % r.status, http_status=r.status)
        return parse_status(r.body)

    async def start(self, settings_xml: bytes) -> str:
        """Start a job; the job's PATH on this scanner (the Location host is
        ignored: we keep talking to the address we dialled)."""
        r = await http11.request(
            self.endpoint, "POST", self.base + "/ScanJobs",
            headers={"Content-Type": "text/xml"}, body=[settings_xml], max_bytes=MAX_XML,
            timeout=self.timeout,
        )
        if r.status == 503:
            raise EsclError("the scanner is busy", kind="busy", http_status=503)
        if r.status == 409:
            raise EsclError("the scanner refused the job", kind="jam", http_status=409)
        if r.status not in (200, 201):
            raise EsclError("the scanner answered HTTP %d" % r.status, http_status=r.status)
        loc = r.headers.get("location", "")
        try:
            path = urlsplit(loc).path if "://" in loc else loc
        except ValueError:
            path = ""
        if not _JOB_PATH.match(path or "") or ".." in path:
            raise EsclError("the scanner gave a job address that doesn't make sense")
        return path.rstrip("/")

    async def next_page(self, job_path: str, *, first: bool = False) -> bytes | None:
        """One page (bytes), or None when there are no more. 503 is retried."""
        gone = 0
        for _ in range(BUSY_RETRIES):
            r = await http11.request(
                self.endpoint, "GET", job_path + "/NextDocument", max_bytes=MAX_PAGE,
                timeout=max(self.timeout, 120.0),
            )
            if r.status == 200:
                if not r.body:
                    raise EsclError("the scanner sent an empty page")
                return r.body
            if r.status == 503:
                await self.sleep(1.0)
                continue
            if r.status in (404, 410):
                if first and gone < 3:
                    gone += 1
                    await self.sleep(1.0)
                    continue
                return None
            raise EsclError("the scanner answered HTTP %d" % r.status, http_status=r.status)
        raise EsclError("the scanner stayed busy", kind="busy")

    async def delete(self, job_path: str) -> None:
        try:
            await http11.request(self.endpoint, "DELETE", job_path, max_bytes=MAX_XML, timeout=10.0)
        except http11.HttpError:
            pass
