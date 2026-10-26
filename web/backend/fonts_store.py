"""Fonts for the document and sheet editors: the rules both sets follow.

Two sets of font families reach the editors (owner decisions 2026-10-26,
design-notes EDITORS-V1-PLAN):

* the **bundled** set, about 70 families fetched once by
  ``scripts/vendor/fonts.py`` from the google/fonts repository (and two
  upstreams), committed under ``web/static/fonts/<slug>/`` with each
  family's licence text beside its files, and listed in
  ``web/static/fonts/manifest.json``;
* the **added** set: families an admin fetches for the whole house from
  the same repository through "More fonts" (``web/backend/api/fonts.py``),
  stored under :data:`fonts_dir` and recorded in the ``fonts`` table
  (V041).

Only two licences are allowed, the SIL Open Font License 1.1 and
Apache-2.0 (owner rule: every font must be ours to use and pass on). A
family is accepted only when BOTH its ``METADATA.pb`` says so AND its
licence file reads as that licence; the Ubuntu Font Licence, CC BY-SA and
anything unknown are refused. The vendor script imports these functions,
so the bundled set and "More fonts" are judged by the same code.

Pure functions only (no DB, no network): the routes and the vendor script
do the fetching.
"""

from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path
from typing import Any, Iterable

#: METADATA.pb ``license`` value -> the SPDX id we record. Nothing else passes.
ALLOWED_LICENCES: dict[str, str] = {"OFL": "OFL-1.1", "APACHE2": "Apache-2.0"}

#: The google/fonts directories a family may come from (``ufl/`` and
#: ``cc-by-sa/`` are never read).
ALLOWED_DIRS: tuple[str, ...] = ("ofl", "apache")

#: The licence file each directory carries, by METADATA licence.
LICENCE_FILES: dict[str, str] = {"OFL": "OFL.txt", "APACHE2": "LICENSE.txt"}

#: METADATA.pb category -> the editors' five groups.
CATEGORIES: dict[str, str] = {
    "SANS_SERIF": "sans",
    "SERIF": "serif",
    "MONOSPACE": "mono",
    "DISPLAY": "display",
    "HANDWRITING": "handwriting",
}
CATEGORY_ORDER: tuple[str, ...] = ("sans", "serif", "mono", "display", "handwriting")

#: An added family's files, each and together. The largest family in the
#: catalogue that anyone would pick for a letter (Noto Serif's two
#: variable files) is about 6 MB; the caps leave room and stop a runaway.
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_FAMILY_BYTES = 40 * 1024 * 1024
#: How many files one added family may have (static families keep four:
#: regular, bold, italic, bold italic; variable ones two).
MAX_FILES = 4
#: The house-wide caps on added families: how many, and their files in all.
MAX_ADDED_FAMILIES = 200
MAX_ADDED_BYTES = 1536 * 1024 * 1024

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_DIR = REPO_ROOT / "web" / "static" / "fonts"
MANIFEST_PATH = BUNDLED_DIR / "manifest.json"

_SLUG_RE = re.compile(r"^[a-z0-9]{1,64}$")
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\],]{0,127}$")


# ─── METADATA.pb ─────────────────────────────────────────────────────────


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def parse_metadata(text: str) -> dict[str, Any]:
    """The parts of a google/fonts ``METADATA.pb`` (protobuf text format)
    the editors need: name, licence, categories, the font files and the
    axes. A tolerant reader of the flat format the repository uses, not a
    general protobuf parser; unknown fields and blocks are skipped."""
    out: dict[str, Any] = {"name": "", "license": "", "categories": [], "fonts": [],
                           "axes": [], "subsets": []}
    stack: list[tuple[str, dict[str, Any]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("{"):
            name = line[:-1].strip()
            stack.append((name, {}))
            continue
        if line == "}":
            if not stack:
                continue
            name, block = stack.pop()
            if not stack:
                if name == "fonts":
                    out["fonts"].append(block)
                elif name == "axes":
                    out["axes"].append(block)
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), _unquote(value)
        if stack:
            stack[-1][1][key] = value
            continue
        if key == "name":
            out["name"] = value
        elif key == "license":
            out["license"] = value
        elif key == "category":
            out["categories"].append(value)
        elif key == "subsets":
            out["subsets"].append(value)
    fonts = []
    for f in out["fonts"]:
        try:
            weight = int(float(f.get("weight", "400")))
        except ValueError:
            weight = 400
        fonts.append({
            "name": f.get("name", out["name"]),
            "style": "italic" if f.get("style") == "italic" else "normal",
            "weight": weight,
            "filename": f.get("filename", ""),
        })
    out["fonts"] = fonts
    axes = []
    for a in out["axes"]:
        try:
            axes.append({"tag": a.get("tag", ""), "min": float(a.get("min_value", "0")),
                         "max": float(a.get("max_value", "0"))})
        except ValueError:
            continue
    out["axes"] = axes
    return out


def category_of(meta: dict[str, Any]) -> str:
    """The first METADATA category we know, as one of :data:`CATEGORY_ORDER`
    ("sans" when none is given)."""
    for c in meta.get("categories") or []:
        if c in CATEGORIES:
            return CATEGORIES[c]
    return "sans"


# ─── Licences ────────────────────────────────────────────────────────────


def licence_text_kind(text: str) -> str | None:
    """``"OFL"`` / ``"APACHE2"`` when the licence file reads as that
    licence, else None (the Ubuntu Font Licence, CC BY-SA, a bespoke or
    empty file)."""
    t = (text or "").upper()
    if "UBUNTU FONT LICENCE" in t or "UBUNTU FONT LICENSE" in t:
        return None
    if "SIL OPEN FONT LICENSE" in t and "VERSION 1.1" in t:
        return "OFL"
    if "APACHE LICENSE" in t and "VERSION 2.0" in t:
        return "APACHE2"
    return None


def licence_verdict(meta_licence: str, licence_text: str | None) -> tuple[str | None, str]:
    """(SPDX id, "") when the family may be used, (None, why) when not.

    Both have to agree: the METADATA.pb ``license`` field must be ``OFL``
    or ``APACHE2``, and the licence file shipped beside the fonts must
    read as that same licence. A family whose licence file is missing is
    refused, since we could not ship the licence text with it."""
    key = (meta_licence or "").strip().upper()
    if key not in ALLOWED_LICENCES:
        shown = meta_licence.strip() if meta_licence and meta_licence.strip() else "no licence stated"
        return None, f"its licence ({shown}) is not one Domovoi uses: only OFL and Apache fonts"
    if licence_text is None:
        return None, "its licence file is missing"
    kind = licence_text_kind(licence_text)
    if kind != key:
        return None, "its licence file does not match the licence it states"
    return ALLOWED_LICENCES[key], ""


# "with Reserved Font Name "Lato"", "Reserved Font Names: A, B", and the
# Liberation file's "with Reserved Font Arimo, Tinos and Cousine".
_RFN_RE = re.compile(r"Reserved\s+Font\s+(?:Names?\b\s*:?\s*)?(.+)", re.IGNORECASE)


def reserved_font_names(ofl_text: str) -> list[str]:
    """The Reserved Font Names an OFL file declares in its copyright lines
    (above "This Font Software is licensed under…"). The licence body
    itself defines the term, so only the header is read. A family with
    one is shipped byte-for-byte (WOFF2 compression at most, OFL-FAQ
    2.2.1), never subset or instanced: those make a Modified Version,
    which may not carry a reserved name."""
    header = re.split(r"This Font Software is licensed", ofl_text or "", maxsplit=1, flags=re.I)[0]
    names: list[str] = []
    for m in _RFN_RE.finditer(header):
        chunk = m.group(1).split("\n")[0]
        quoted = re.findall(r"[\"'“‘]([^\"'”’]+)[\"'”’]", chunk)
        if quoted:
            names.extend(q.strip() for q in quoted if q.strip())
            continue
        chunk = re.split(r"\.(?:\s|$)", chunk)[0]
        for part in re.split(r",|\band\b", chunk):
            name = part.strip().strip('".“”\'').strip()
            if name and name.lower() not in ("s", "name", "names"):
                names.append(name)
    if not names and re.search(r"Reserved\s+Font\s+Name", header, re.I):
        names.append("(unnamed)")
    return names


# ─── Files ───────────────────────────────────────────────────────────────


_SFNT_MAX_TABLES = 128


def _sfnt_ok(data: bytes) -> bool:
    """A TrueType/OpenType table directory that holds together: a sane
    table count, printable tags, every table inside the file, and the
    tables a font can't do without (head, cmap, and outlines)."""
    if len(data) < 12:
        return False
    (num,) = struct.unpack(">H", data[4:6])
    if not 1 <= num <= _SFNT_MAX_TABLES or len(data) < 12 + 16 * num:
        return False
    tags = set()
    for i in range(num):
        tag, _check, offset, length = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
        if not all(0x20 <= b <= 0x7E for b in tag):
            return False
        if offset < 12 + 16 * num or offset + length > len(data):
            return False
        tags.add(tag)
    return b"head" in tags and b"cmap" in tags and bool(tags & {b"glyf", b"CFF ", b"CFF2"})


def _woff_ok(data: bytes) -> bool:
    """WOFF/WOFF2 header: the file's own length field matches the file,
    and it says it holds some tables."""
    if len(data) < 44:
        return False
    (length,) = struct.unpack(">I", data[8:12])
    (num,) = struct.unpack(">H", data[12:14])
    return length == len(data) and 1 <= num <= _SFNT_MAX_TABLES


def sniff_font(data: bytes) -> str | None:
    """The font format, or None when the bytes are not a font that holds
    together: ``ttf`` (0x00010000 or ``true``), ``otf`` (``OTTO``) with a
    table directory that fits the file (:func:`_sfnt_ok`), ``woff2``
    (``wOF2``) / ``woff`` (``wOFF``) whose header length is the file's
    (:func:`_woff_ok`). A ``ttcf`` collection is refused: a browser cannot
    pick a face out of one. Four signature bytes are not enough: a page
    that merely starts with ``true`` is refused."""
    data = bytes(data)
    head = data[:4]
    try:
        if head in (b"\x00\x01\x00\x00", b"true"):
            return "ttf" if _sfnt_ok(data) else None
        if head == b"OTTO":
            return "otf" if _sfnt_ok(data) else None
        if head == b"wOF2":
            return "woff2" if _woff_ok(data) else None
        if head == b"wOFF":
            return "woff" if _woff_ok(data) else None
    except struct.error:
        return None
    return None


CSS_FORMATS = {"ttf": "truetype", "otf": "opentype", "woff2": "woff2", "woff": "woff"}


def slugify(family: str) -> str:
    """The directory name google/fonts uses for a family: lowercase letters
    and digits only ("Source Sans 3" -> "sourcesans3")."""
    return re.sub(r"[^a-z0-9]", "", (family or "").lower())[:64]


def valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug or ""))


def valid_filename(name: str) -> bool:
    """A font or licence file name we would store and serve: no path
    separators, no leading dot, nothing but the characters google/fonts
    file names use (``Roboto-Italic[wdth,wght].ttf``)."""
    return bool(_FILE_RE.match(name or "")) and ".." not in name


def is_variable(meta: dict[str, Any]) -> bool:
    return any("[" in (f.get("filename") or "") for f in meta.get("fonts") or [])


def pick_files(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Which of a family's font files to take: every variable file (one
    upright, one italic at most), or, for a static family, the regular,
    bold, italic and bold italic that exist (the nearest weight when a
    family has no exact 400 or 700; display faces often have one weight)."""
    fonts = [f for f in meta.get("fonts") or [] if f.get("filename")]
    if not fonts:
        return []
    if is_variable(meta):
        seen: dict[str, dict[str, Any]] = {}
        for f in fonts:
            if "[" in f["filename"] and f["style"] not in seen:
                seen[f["style"]] = f
        return [seen[s] for s in ("normal", "italic") if s in seen]
    picked: list[dict[str, Any]] = []
    for style in ("normal", "italic"):
        pool = [f for f in fonts if f["style"] == style]
        if not pool:
            continue
        regular = min(pool, key=lambda f: (abs(f["weight"] - 400), f["weight"]))
        picked.append(regular)
        heavier = [f for f in pool if f["weight"] > regular["weight"] and f["weight"] >= 600]
        if heavier:
            bold = min(heavier, key=lambda f: (abs(f["weight"] - 700), f["weight"]))
            picked.append(bold)
    return picked[:MAX_FILES]


def weight_range(meta: dict[str, Any], entry: dict[str, Any]) -> tuple[int, int]:
    """The weights one file covers: the ``wght`` axis for a variable file,
    else its own weight."""
    if "[" in (entry.get("filename") or ""):
        for a in meta.get("axes") or []:
            if a.get("tag") == "wght":
                return int(a["min"]), int(a["max"])
    w = int(entry.get("weight") or 400)
    return w, w


# ─── The bundled set ─────────────────────────────────────────────────────


def bundled_manifest(path: Path | None = None) -> dict[str, Any]:
    """``web/static/fonts/manifest.json`` (written by scripts/vendor/fonts.py),
    or an empty set when it is missing (a backend-only checkout)."""
    p = Path(path) if path is not None else MANIFEST_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"families": []}


def bundled_slugs(manifest: dict[str, Any] | None = None) -> set[str]:
    m = manifest if manifest is not None else bundled_manifest()
    return {f.get("slug", "") for f in m.get("families") or []}


def bundled_names(manifest: dict[str, Any] | None = None) -> set[str]:
    m = manifest if manifest is not None else bundled_manifest()
    return {(f.get("family") or "").lower() for f in m.get("families") or []}


def css_family(name: str) -> str:
    """A family name as a CSS string, quoted, with quotes and backslashes
    escaped (a name comes from METADATA, which is data)."""
    return '"' + re.sub(r'(["\\])', r"\\\1", re.sub(r"[\x00-\x1f\x7f<>]", "", name)) + '"'


def face_rules(family: str, faces: Iterable[dict[str, Any]], url_base: str) -> str:
    """@font-face rules for one family. Each face is ``{file, style,
    weight: [lo, hi], format, unicode_range?}``; ``url_base`` ends in ``/``."""
    out = []
    for face in faces:
        lo, hi = face["weight"]
        weight = f"{lo}" if lo == hi else f"{lo} {hi}"
        rule = [
            "@font-face {",
            f"  font-family: {css_family(family)};",
            f"  font-style: {face['style']};",
            f"  font-weight: {weight};",
            "  font-display: swap;",
            f"  src: url('{url_base}{face['file']}') format('{CSS_FORMATS[face['format']]}');",
        ]
        if face.get("unicode_range"):
            rule.append(f"  unicode-range: {face['unicode_range']};")
        rule.append("}")
        out.append("\n".join(rule))
    return "\n".join(out) + ("\n" if out else "")


def fonts_dir() -> Path:
    """Where added families are stored: ``FONTS_DIR``, else
    ``~/.domovoi/fonts`` (beside the other server-fetched files)."""
    from domovoi.config import settings

    return Path(os.path.expanduser(getattr(settings, "fonts_dir", "") or "~/.domovoi/fonts"))
