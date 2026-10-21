"""Small documents made in the test, for the chat's document readers.

Every file here is written from scratch (no fixture files, no personal
data): a PDF built object by object, a .docx and an .odt as zips of
hand-written XML, an .xlsx through openpyxl, and the hostile ones (a zip
bomb, XML entities, a PDF that isn't one).
"""

from __future__ import annotations

import io
import zipfile

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _pdf_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[tuple[float, float, str]]], image_pages: tuple[int, ...] = (),
             size: tuple[int, int] = (612, 792), font_size: float = 11) -> bytes:
    """A PDF with one page per entry of ``pages`` (each a list of (x, y,
    text) lines in Helvetica); a page index in ``image_pages`` also gets a
    full-page grey image (a scan has an image and no text)."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    catalog = add(b"")          # filled in below
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids = []
    for i, lines in enumerate(pages):
        ops = []
        res = f"/Font << /F1 {font} 0 R >>"
        if i in image_pages:
            img = add(b"<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceGray "
                      b"/BitsPerComponent 8 /Length 64 >>\nstream\n" + bytes([200] * 64) + b"\nendstream")
            res += f" /XObject << /Im1 {img} 0 R >>"
            ops.append(f"q {size[0] - 100} 0 0 {size[1] - 100} 50 50 cm /Im1 Do Q")
        for x, y, t in lines:
            ops.append(f"BT /F1 {font_size} Tf {x} {y} Td ({_pdf_str(t)}) Tj ET")
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {size[0]} {size[1]}] "
                   f"/Resources << {res} >> /Contents {content} 0 R >>".encode())
        kids.append(page)
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode()
    objs[pages_obj - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                           f"/Count {len(kids)} >>").encode()
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def text_page(paragraph: str, top: float = 740, width: int = 90) -> list[tuple[float, float, str]]:
    """``paragraph`` wrapped into lines down a page."""
    words, lines, cur = paragraph.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return [(72, top - 14 * i, ln) for i, ln in enumerate(lines)]


def _zip(files: dict[str, bytes | str], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, data in files.items():
            z.writestr(name, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()


def _w(body: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:body>{body}'
            f'<w:sectPr><w:headerReference w:type="default" r:id="rIdH"/>'
            f'<w:footerReference w:type="default" r:id="rIdF"/></w:sectPr></w:body></w:document>')


def _p(text: str, style: str | None = None, extra: str = "") -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/>{extra}</w:pPr>' if style else (f"<w:pPr>{extra}</w:pPr>" if extra else "")
    return f"<w:p>{ppr}<w:r><w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"


DOCX_STYLES = (f'<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="{W_NS}">'
               '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>'
               '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
               '</w:styles>')


def make_docx(body_xml: str | None = None, *, document_xml: str | None = None) -> bytes:
    """A .docx: by default a short lease with a title, two headings, a
    tracked insertion and deletion, a list item, a table, a header, a
    footer and a footnote."""
    if body_xml is None:
        body_xml = (
            _p("Residential Lease", "Title")
            + _p("Rent", "Heading1")
            + "<w:p><w:r><w:t xml:space=\"preserve\">Rent is $1,200 a month, due within </w:t></w:r>"
              "<w:ins w:id=\"1\" w:author=\"A\"><w:r><w:t>five</w:t></w:r></w:ins>"
              "<w:del w:id=\"2\" w:author=\"A\"><w:r><w:delText>ten</w:delText></w:r></w:del>"
              "<w:r><w:t xml:space=\"preserve\"> days.</w:t></w:r>"
              "<w:r><w:footnoteReference w:id=\"1\"/></w:r></w:p>"
            + _p("Paid by bank transfer.", extra="<w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"1\"/></w:numPr>")
            + _p("Deposit", "Heading1")
            + _p("The security deposit is $1,500, returned within 30 days.")
            + "<w:tbl><w:tr><w:tc>" + _p("Item") + "</w:tc><w:tc>" + _p("Amount") + "</w:tc></w:tr>"
              "<w:tr><w:tc>" + _p("Key") + "</w:tc><w:tc>" + _p("$25") + "</w:tc></w:tr></w:tbl>"
        )
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdH" Type="x/header" Target="header1.xml"/>'
            '<Relationship Id="rIdF" Type="x/footer" Target="footer1.xml"/>'
            '<Relationship Id="rIdX" Type="x/hyperlink" Target="http://127.0.0.1:9/never" TargetMode="External"/>'
            '</Relationships>')
    hdr = f'<?xml version="1.0"?><w:hdr xmlns:w="{W_NS}">{_p("12 Elm Street lease")}</w:hdr>'
    ftr = f'<?xml version="1.0"?><w:ftr xmlns:w="{W_NS}">{_p("Initials ____")}</w:ftr>'
    notes = (f'<?xml version="1.0"?><w:footnotes xmlns:w="{W_NS}">'
             '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
             f'<w:footnote w:id="1">{_p("Late fees start on day six.")}</w:footnote></w:footnotes>')
    return _zip({
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "word/document.xml": document_xml or _w(body_xml),
        "word/styles.xml": DOCX_STYLES,
        "word/_rels/document.xml.rels": rels,
        "word/header1.xml": hdr,
        "word/footer1.xml": ftr,
        "word/footnotes.xml": notes,
    })


ODT_NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
          'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
          'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"')


def make_odt(content_xml: str | None = None) -> bytes:
    if content_xml is None:
        content_xml = (
            f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {ODT_NS}><office:body><office:text>'
            '<text:h text:outline-level="1">Rent</text:h>'
            '<text:p>Rent is<text:s text:c="2"/>$1,200<text:tab/>a month.<text:note><text:note-citation>1</text:note-citation>'
            '<text:note-body><text:p>Late fees start on day six.</text:p></text:note-body></text:note></text:p>'
            '<text:list><text:list-item><text:p>Paid by bank transfer.</text:p></text:list-item></text:list>'
            '<table:table><table:table-row><table:table-cell><text:p>Item</text:p></table:table-cell>'
            '<table:table-cell><text:p>Amount</text:p></table:table-cell></table:table-row>'
            '<table:table-row><table:table-cell><text:p>Key</text:p></table:table-cell>'
            '<table:table-cell><text:p>$25</text:p></table:table-cell></table:table-row></table:table>'
            '</office:text></office:body></office:document-content>')
    styles = (f'<?xml version="1.0"?><office:document-styles {ODT_NS}><office:master-styles>'
              '<style:master-page style:name="Standard"><style:header><text:p>12 Elm Street lease</text:p>'
              '</style:header></style:master-page></office:master-styles></office:document-styles>')
    return _zip({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": content_xml,
                 "styles.xml": styles})


def make_xlsx(rows: list[list[object]] | None = None, *, sheet: str = "Budget", formulas: bool = False) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows or [["Item", "Amount"], ["Rent", 1200], ["Power", 85.5], ["Water", 40]]:
        ws.append(r)
    if formulas:
        ws.append(["Total", "=SUM(B2:B4)"])
    second = wb.create_sheet("Notes")
    second.append(["Note"])
    second.append(["Paid in full"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def zip_bomb_docx(inflated_mb: int = 24) -> bytes:
    """A .docx whose document part inflates from a few KB to ``inflated_mb``
    MB (zeros compress about a thousand times)."""
    body = b"<w:document xmlns:w=\"%s\"><w:body>" % W_NS.encode() + b" " * (inflated_mb * 1024 * 1024) + b"</w:body></w:document>"
    return _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": body})


def too_many_parts_docx(n: int = 5100) -> bytes:
    files: dict[str, bytes | str] = {f"word/media/x{i}.xml": "<a/>" for i in range(n)}
    files["word/document.xml"] = _w(_p("hello"))
    return _zip(files, compression=zipfile.ZIP_STORED)


BILLION_LAUGHS = (
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
    '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
    '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>'
    f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>&lol3;</w:t></w:r></w:p></w:body></w:document>')

EXTERNAL_ENTITY = (
    '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "http://127.0.0.1:9/secret">]>'
    f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>')

UTF16_DOCTYPE = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d [<!ENTITY x "y">]>'
                 f'<w:document xmlns:w="{W_NS}"><w:body/></w:document>').encode("utf-16")
