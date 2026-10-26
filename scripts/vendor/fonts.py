"""Vendor the bundled font set for the document and sheet editors.

Writes ``web/static/fonts/<slug>/`` for every family in :data:`FAMILIES`
(the WOFF2 files and the family's licence text), plus
``web/static/fonts/manifest.json`` (what the server lists),
``web/static/fonts/fonts.css`` (the @font-face rules; a browser downloads a
face only when text in that family is drawn) and the fonts table in
``docs/LICENSES.md`` (between the ``fonts:start``/``fonts:end`` markers).

Where the fonts come from:

* the google/fonts repository at the pinned commit :data:`GOOGLE_FONTS_COMMIT`
  (``ofl/`` and ``apache/`` only);
* Liberation 2.1.5 from its release tarball (pinned by SHA-256);
* OpenDyslexic from its repository at a pinned commit (its own WOFF2 files).

Licences: OFL-1.1 and Apache-2.0 only. Each family is checked with
``web.backend.fonts_store.licence_verdict`` (METADATA.pb AND the licence
file must agree) before anything is written; a family that fails stops the
run. The same function decides "More fonts" on a running server.

What is done to the files:

* a family **without** a Reserved Font Name: the axes other than weight are
  fixed at their defaults, then the font is cut into a Latin and a Latin
  Extended part (the browser fetches only the part a page's text needs,
  through unicode-range) and saved as WOFF2. That is a Modified Version
  under the OFL (allowed, and it keeps the OFL) or a modified file under
  Apache-2.0 (allowed, with the notice in the family's CHANGES.txt);
* a family **with** a Reserved Font Name (Lato, Liberation, OpenDyslexic…):
  the font is kept whole and only WOFF2-compressed, which the OFL FAQ (2.2.1)
  says is not a modification, so the name stays usable. OpenDyslexic's own
  WOFF2 files are copied as they are.

Usage (needs network once, and fontTools + brotli, both MIT; they are a
vendoring tool only, the server never needs them)::

    python -m venv .fontvenv && .fontvenv/Scripts/pip install fonttools==4.60.1 brotli==1.1.0
    .fontvenv/Scripts/python scripts/vendor/fonts.py --cache <dir>     # fetch + build
    python scripts/vendor/fonts.py --docs-only                          # rewrite LICENSES.md from the manifest

Then commit ``web/static/fonts/`` and ``docs/LICENSES.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from web.backend import fonts_store as fs  # noqa: E402

GOOGLE_FONTS_COMMIT = "e7db6ca603fd4862010383ff17ffd6cc4ffac1c0"  # google/fonts main, 2026-10-23
RAW = f"https://raw.githubusercontent.com/google/fonts/{GOOGLE_FONTS_COMMIT}/"

LIBERATION_VERSION = "2.1.5"
LIBERATION_URL = ("https://github.com/liberationfonts/liberation-fonts/files/7261482/"
                  "liberation-fonts-ttf-2.1.5.tar.gz")
LIBERATION_SHA256 = "7191c669bf38899f73a2094ed00f7b800553364f90e2637010a69c0e268f25d0"

OPENDYSLEXIC_COMMIT = "1824da5c0e41dc3e13ffc7f3a636dcaf695d61b7"
OPENDYSLEXIC_RAW = f"https://raw.githubusercontent.com/antijingoist/opendyslexic/{OPENDYSLEXIC_COMMIT}/"

OUT_DIR = REPO_ROOT / "web" / "static" / "fonts"
DOC = REPO_ROOT / "docs" / "LICENSES.md"
DOC_START = "<!-- fonts:start (written by scripts/vendor/fonts.py) -->"
DOC_END = "<!-- fonts:end -->"

# The same ranges Google Fonts serves as "latin" and "latin-ext" (and the
# dashboard's own Inter/JetBrains Mono CSS uses).
LATIN = ("U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,"
         "U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD")
LATIN_EXT = ("U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,"
             "U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,"
             "U+2113,U+2C60-2C7F,U+A720-A7FF")
SUBSETS = (("latin", LATIN), ("latin-ext", LATIN_EXT))

# (google/fonts directory, editors' category, note shown in LICENSES.md)
FAMILIES: list[tuple[str, str, str]] = [
    # sans
    ("ofl/roboto", "sans", ""),
    ("ofl/opensans", "sans", ""),
    ("ofl/lato", "sans", ""),
    ("ofl/montserrat", "sans", ""),
    ("ofl/sourcesans3", "sans", ""),
    ("ofl/notosans", "sans", ""),
    ("ofl/inter", "sans", "also the dashboard's own face"),
    ("ofl/ibmplexsans", "sans", ""),
    ("ofl/spacegrotesk", "sans", ""),
    ("ofl/atkinsonhyperlegible", "sans", "made for low-vision readers"),
    ("ofl/lexend", "sans", "made for easier reading"),
    ("ofl/poppins", "sans", ""),
    ("ofl/nunito", "sans", ""),
    ("ofl/raleway", "sans", ""),
    ("ofl/worksans", "sans", ""),
    ("ofl/ptsans", "sans", ""),
    ("ofl/firasans", "sans", ""),
    ("ofl/dmsans", "sans", ""),
    ("ofl/figtree", "sans", ""),
    ("ofl/arimo", "sans", "same widths as Arial"),
    ("ofl/carlito", "sans", "same widths as Calibri"),
    # serif
    ("ofl/notoserif", "serif", ""),
    ("ofl/merriweather", "serif", ""),
    ("ofl/lora", "serif", ""),
    ("ofl/playfairdisplay", "serif", ""),
    ("ofl/ebgaramond", "serif", ""),
    ("ofl/librebaskerville", "serif", ""),
    ("ofl/crimsonpro", "serif", ""),
    ("ofl/ibmplexserif", "serif", ""),
    ("ofl/ptserif", "serif", ""),
    ("ofl/sourceserif4", "serif", ""),
    ("apache/robotoslab", "serif", ""),
    ("ofl/bitter", "serif", ""),
    ("ofl/cormorantgaramond", "serif", ""),
    ("ofl/spectral", "serif", ""),
    ("ofl/caladea", "serif", "same widths as Cambria"),
    # mono
    ("ofl/robotomono", "mono", ""),
    ("ofl/firacode", "mono", ""),
    ("ofl/ibmplexmono", "mono", ""),
    ("ofl/jetbrainsmono", "mono", "also the dashboard's own code face"),
    ("ofl/sourcecodepro", "mono", ""),
    ("ofl/inconsolata", "mono", ""),
    ("ofl/spacemono", "mono", ""),
    ("ofl/courierprime", "mono", ""),
    ("ofl/cousine", "mono", "same widths as Courier New"),
    # display
    ("ofl/oswald", "display", ""),
    ("ofl/bebasneue", "display", ""),
    ("ofl/anton", "display", ""),
    ("ofl/abrilfatface", "display", ""),
    ("ofl/lobster", "display", ""),
    ("ofl/righteous", "display", ""),
    ("ofl/bungee", "display", ""),
    ("ofl/cinzel", "display", ""),
    ("ofl/comfortaa", "display", ""),
    ("ofl/fredoka", "display", ""),
    ("ofl/vastshadow", "display", "also the dashboard's display face"),
    # handwriting
    ("ofl/caveat", "handwriting", ""),
    ("ofl/dancingscript", "handwriting", ""),
    ("ofl/pacifico", "handwriting", ""),
    ("ofl/patrickhand", "handwriting", ""),
    ("ofl/comicneue", "handwriting", ""),
    ("ofl/indieflower", "handwriting", ""),
    ("ofl/shadowsintolight", "handwriting", ""),
    ("ofl/kalam", "handwriting", ""),
    ("ofl/greatvibes", "handwriting", ""),
    ("apache/satisfy", "handwriting", ""),
    ("ofl/amaticsc", "handwriting", ""),
    ("apache/permanentmarker", "handwriting", ""),
    ("ofl/architectsdaughter", "handwriting", ""),
]

# Upstream families (not in google/fonts).
LIBERATION = [
    ("Liberation Sans", "liberationsans", "sans", "LiberationSans", "same widths as Arial"),
    ("Liberation Serif", "liberationserif", "serif", "LiberationSerif", "same widths as Times New Roman"),
    ("Liberation Mono", "liberationmono", "mono", "LiberationMono", "same widths as Courier New"),
]
OPENDYSLEXIC_FILES = [  # (file in compiled/, style, weight)
    ("OpenDyslexic-Regular.woff2", "normal", 400),
    ("OpenDyslexic-Bold.woff2", "normal", 700),
    ("OpenDyslexic-Italic.woff2", "italic", 400),
    ("OpenDyslexic-Bold-Italic.woff2", "italic", 700),
]

CHANGES_TEXT = """Changes made by Domovoi (scripts/vendor/fonts.py)
===================================================

The font files in this folder were made from {source}
as follows:

{steps}

Nothing else was changed: the outlines, the names and the copyright
notices inside the fonts are the originals'. The licence beside this file
({licence_file}) still applies to every file here.
"""


# ─── fetching ─────────────────────────────────────────────────────────


class Fetcher:
    def __init__(self, cache: Path) -> None:
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)

    def get(self, url: str) -> bytes:
        key = hashlib.sha256(url.encode()).hexdigest()[:24] + "-" + url.rsplit("/", 1)[-1][-60:]
        key = "".join(c if c.isalnum() or c in "-._" else "_" for c in key)
        path = self.cache / key
        if path.exists():
            return path.read_bytes()
        req = urllib.request.Request(url, headers={"User-Agent": "domovoi-vendor-fonts"})
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 (fixed hosts)
            data = r.read()
        path.write_bytes(data)
        return data

    def text(self, url: str) -> str:
        return self.get(url).decode("utf-8", "replace")


def _quote(name: str) -> str:
    return urllib.request.quote(name, safe="/")


# ─── transforming ─────────────────────────────────────────────────────


def _font_from(data: bytes):
    from fontTools.ttLib import TTFont

    return TTFont(io.BytesIO(data), recalcBBoxes=False, recalcTimestamp=False)


def _codepoints(ranges: str) -> set[int]:
    out: set[int] = set()
    for part in ranges.split(","):
        part = part.strip().upper().removeprefix("U+")
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a, 16), int(b, 16) + 1))
        else:
            out.add(int(part, 16))
    return out


def compress_whole(data: bytes) -> bytes:
    """WOFF2 of the font as it is: table data unchanged apart from the
    WOFF2 transform and compression (OFL-FAQ 2.2.1)."""
    from fontTools.ttLib import woff2

    out = io.BytesIO()
    woff2.compress(io.BytesIO(data), out)
    return out.getvalue()


def subset_parts(data: bytes) -> list[tuple[str, str, bytes]]:
    """[(subset name, unicode-range, woff2 bytes)] for a font we may modify:
    non-weight axes fixed at their defaults, then one file per subset that
    the font has characters for."""
    from fontTools import subset
    from fontTools.varLib import instancer

    base = _font_from(data)
    if "fvar" in base:
        pins = {a.axisTag: a.defaultValue for a in base["fvar"].axes if a.axisTag != "wght"}
        if pins:
            base = instancer.instantiateVariableFont(base, pins)
    buf = io.BytesIO()
    base.save(buf)
    pinned = buf.getvalue()
    cmap = set(base.getBestCmap() or {})
    parts = []
    for name, ranges in SUBSETS:
        wanted = _codepoints(ranges)
        have = cmap & wanted
        # A part is worth a request only with real letters in it, not just
        # the few shared marks both ranges carry.
        letters = {c for c in have if c > 0x7F and c not in (0x304, 0x308, 0x329)}
        if name != "latin" and len(letters) < 20:
            continue
        font = _font_from(pinned)
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["*"]
        opts.name_IDs = ["*"]
        opts.name_languages = ["*"]
        opts.name_legacy = True
        opts.notdef_outline = True
        opts.glyph_names = False
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=wanted)
        sub.subset(font)
        out = io.BytesIO()
        font.flavor = "woff2"
        font.save(out)
        parts.append((name, ranges, out.getvalue()))
    return parts


# ─── per family ───────────────────────────────────────────────────────


def _weights_list(lo: int, hi: int) -> list[int]:
    if lo == hi:
        return [lo]
    return [w for w in range(100, 1000, 100) if lo <= w <= hi]


def _family_entry(family: str, slug: str, category: str, spdx: str, licence_file: str,
                  source: str, source_url: str, reserved: list[str], modified: bool,
                  faces: list[dict[str, Any]], note: str) -> dict[str, Any]:
    weights: set[int] = set()
    styles: set[str] = set()
    for f in faces:
        weights.update(_weights_list(*f["weight"]))
        styles.add(f["style"])
    return {
        "family": family, "slug": slug, "category": category, "licence": spdx,
        "licence_file": licence_file, "source": source, "source_url": source_url,
        "reserved_names": reserved, "modified": modified, "note": note,
        "weights": sorted(weights), "italic": "italic" in styles,
        "bytes": sum(f["bytes"] for f in faces), "faces": faces,
    }


def build_google(fetch: Fetcher, path: str, category: str, note: str) -> dict[str, Any]:
    top, dirname = path.split("/")
    assert top in fs.ALLOWED_DIRS, path
    meta = fs.parse_metadata(fetch.text(RAW + path + "/METADATA.pb"))
    lic_name = fs.LICENCE_FILES.get(meta["license"].upper())
    lic_text = fetch.text(RAW + path + "/" + lic_name) if lic_name else None
    spdx, why = fs.licence_verdict(meta["license"], lic_text)
    if not spdx:
        raise SystemExit(f"{path}: refused, {why}")
    reserved = fs.reserved_font_names(lic_text) if spdx == "OFL-1.1" else []
    family, slug = meta["name"], dirname
    out = OUT_DIR / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / lic_name).write_bytes(lic_text.encode("utf-8"))
    faces: list[dict[str, Any]] = []
    for entry in fs.pick_files(meta):
        raw = fetch.get(RAW + path + "/" + _quote(entry["filename"]))
        if fs.sniff_font(raw) not in ("ttf", "otf"):
            raise SystemExit(f"{path}/{entry['filename']}: not a font file")
        lo, hi = fs.weight_range(meta, entry)
        stem = f"{slug}-{entry['style']}-{lo}" + (f"-{hi}" if hi != lo else "")
        if reserved:
            data = compress_whole(raw)
            name = stem + ".woff2"
            (out / name).write_bytes(data)
            faces.append({"file": name, "style": entry["style"], "weight": [lo, hi],
                          "format": "woff2", "bytes": len(data), "from": entry["filename"]})
        else:
            for sub_name, ranges, data in subset_parts(raw):
                name = f"{stem}-{sub_name}.woff2"
                (out / name).write_bytes(data)
                faces.append({"file": name, "style": entry["style"], "weight": [lo, hi],
                              "format": "woff2", "unicode_range": ranges, "bytes": len(data),
                              "from": entry["filename"]})
    if not faces:
        raise SystemExit(f"{path}: no font files")
    src = f"google/fonts {path} at {GOOGLE_FONTS_COMMIT[:12]}"
    url = f"https://github.com/google/fonts/tree/{GOOGLE_FONTS_COMMIT}/{path}"
    if reserved:
        steps = ("* each file was compressed to WOFF2, whole (no characters or features\n"
                 "  removed: this family has a Reserved Font Name, so it is not modified).")
    else:
        steps = ("* in a variable font, every axis except weight was fixed at its default;\n"
                 "* each font was cut into a Latin and a Latin Extended part (characters\n"
                 "  outside those ranges removed);\n"
                 "* each part was saved as WOFF2.")
    (out / "CHANGES.txt").write_bytes(CHANGES_TEXT.format(
        source=src, steps=steps, licence_file=lic_name).encode("utf-8"))
    return _family_entry(family, slug, category, spdx, lic_name, src, url, reserved,
                         not reserved, faces, note)


def build_liberation(fetch: Fetcher) -> list[dict[str, Any]]:
    blob = fetch.get(LIBERATION_URL)
    if hashlib.sha256(blob).hexdigest() != LIBERATION_SHA256:
        raise SystemExit("Liberation tarball: SHA-256 does not match the pin")
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile():
                files[m.name.rsplit("/", 1)[-1]] = tar.extractfile(m).read()
    lic = files["LICENSE"].decode("utf-8")
    spdx, why = fs.licence_verdict("OFL", lic)
    if not spdx:
        raise SystemExit(f"Liberation: refused, {why}")
    reserved = fs.reserved_font_names(lic)
    entries = []
    for family, slug, category, stem, note in LIBERATION:
        out = OUT_DIR / slug
        out.mkdir(parents=True, exist_ok=True)
        (out / "LICENSE").write_bytes(lic.encode("utf-8"))
        faces = []
        for suffix, style, weight in (("Regular", "normal", 400), ("Bold", "normal", 700),
                                      ("Italic", "italic", 400), ("BoldItalic", "italic", 700)):
            raw = files[f"{stem}-{suffix}.ttf"]
            data = compress_whole(raw)
            name = f"{slug}-{style}-{weight}.woff2"
            (out / name).write_bytes(data)
            faces.append({"file": name, "style": style, "weight": [weight, weight],
                          "format": "woff2", "bytes": len(data), "from": f"{stem}-{suffix}.ttf"})
        src = f"Liberation {LIBERATION_VERSION} release (liberation-fonts-ttf-{LIBERATION_VERSION}.tar.gz)"
        steps = ("* each file was compressed to WOFF2, whole (no characters or features\n"
                 "  removed: this family has a Reserved Font Name, so it is not modified).")
        (out / "CHANGES.txt").write_bytes(CHANGES_TEXT.format(
            source=src, steps=steps, licence_file="LICENSE").encode("utf-8"))
        entries.append(_family_entry(
            family, slug, category, spdx, "LICENSE", src,
            "https://github.com/liberationfonts/liberation-fonts/releases/tag/" + LIBERATION_VERSION,
            reserved, False, faces, note))
    return entries


def build_opendyslexic(fetch: Fetcher) -> dict[str, Any]:
    lic = fetch.text(OPENDYSLEXIC_RAW + "OFL.txt")
    spdx, why = fs.licence_verdict("OFL", lic)
    if not spdx:
        raise SystemExit(f"OpenDyslexic: refused, {why}")
    slug = "opendyslexic"
    out = OUT_DIR / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "OFL.txt").write_bytes(lic.encode("utf-8"))
    faces = []
    for fname, style, weight in OPENDYSLEXIC_FILES:
        data = fetch.get(OPENDYSLEXIC_RAW + "compiled/" + fname)
        if fs.sniff_font(data) != "woff2":
            raise SystemExit(f"OpenDyslexic {fname}: not WOFF2")
        name = f"{slug}-{style}-{weight}.woff2"
        (out / name).write_bytes(data)
        faces.append({"file": name, "style": style, "weight": [weight, weight],
                      "format": "woff2", "bytes": len(data), "from": fname})
    src = f"antijingoist/opendyslexic at {OPENDYSLEXIC_COMMIT[:12]}"
    (out / "CHANGES.txt").write_bytes(CHANGES_TEXT.format(
        source=src, steps="* none: the project's own WOFF2 files, copied as they are (renamed).",
        licence_file="OFL.txt").encode("utf-8"))
    return _family_entry("OpenDyslexic", slug, "sans", spdx, "OFL.txt", src,
                         f"https://github.com/antijingoist/opendyslexic/tree/{OPENDYSLEXIC_COMMIT}",
                         fs.reserved_font_names(lic), False, faces, "made for readers with dyslexia")


# ─── outputs ──────────────────────────────────────────────────────────


def write_css(families: list[dict[str, Any]]) -> None:
    parts = ["/* Written by scripts/vendor/fonts.py: the editors' bundled fonts.\n"
             " * Each face downloads only when text in that family (and, through\n"
             " * unicode-range, in that part of the alphabet) is drawn. */\n"]
    for fam in families:
        parts.append(fs.face_rules(fam["family"], fam["faces"], f"/fonts/{fam['slug']}/"))
    (OUT_DIR / "fonts.css").write_bytes("\n".join(parts).encode("utf-8"))


def docs_table(manifest: dict[str, Any]) -> str:
    fams = manifest["families"]
    total = sum(f["bytes"] for f in fams)
    lines = [
        DOC_START,
        f"The editors' {len(fams)} bundled font families "
        f"({total / 1_000_000:.1f} MB in `web/static/fonts/`). Each family's folder "
        "holds its licence text and a `CHANGES.txt` that says what was done to "
        "the files. A family with a Reserved Font Name is only compressed, never "
        "cut down, so it keeps its name. Fonts an admin adds with \"More fonts\" "
        "come from the same repository, are checked the same way (OFL or Apache "
        "only) and are stored unchanged with their licence file.",
        "",
        "| Family | Group | Licence | Reserved name | From |",
        "|---|---|---|---|---|",
    ]
    for f in sorted(fams, key=lambda f: (fs.CATEGORY_ORDER.index(f["category"]), f["family"].lower())):
        rfn = ", ".join(f["reserved_names"]) or "none"
        lines.append(f"| {f['family']} | {f['category']} | {f['licence']} | {rfn} | {f['source']} |")
    lines.append(DOC_END)
    return "\n".join(lines)


def write_docs(manifest: dict[str, Any]) -> None:
    text = DOC.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    block = docs_table(manifest).replace("\n", nl)
    if DOC_START in text:
        head, rest = text.split(DOC_START, 1)
        _, tail = rest.split(DOC_END, 1)
        text = head + block + tail
    else:
        anchor = "## What the licences mean"
        assert anchor in text, "LICENSES.md has no 'What the licences mean' heading"
        text = text.replace(anchor, "### The editors' fonts" + nl + nl + block + nl + nl + anchor, 1)
    DOC.write_bytes(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--cache", type=Path, default=REPO_ROOT / ".font-cache",
                    help="where downloads are kept between runs (outside the repo is best)")
    ap.add_argument("--only", action="append", default=[], help="build just this google/fonts path")
    ap.add_argument("--docs-only", action="store_true", help="rewrite LICENSES.md from manifest.json")
    args = ap.parse_args(argv)
    if args.docs_only:
        write_docs(fs.bundled_manifest())
        return 0
    fetch = Fetcher(args.cache)
    if not args.only:
        for child in OUT_DIR.iterdir() if OUT_DIR.exists() else []:
            if child.is_dir():
                shutil.rmtree(child)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    families: list[dict[str, Any]] = []
    for path, category, note in FAMILIES:
        if args.only and path not in args.only:
            continue
        fam = build_google(fetch, path, category, note)
        print(f"{fam['family']:<26} {fam['licence']:<11} {fam['bytes'] / 1000:8.0f} kB"
              f"  {'whole' if fam['reserved_names'] else 'subset'}", flush=True)
        families.append(fam)
    if args.only:
        return 0
    families += build_liberation(fetch)
    families.append(build_opendyslexic(fetch))
    names = [f["family"].lower() for f in families]
    assert len(names) == len(set(names)), "a family is listed twice"
    families.sort(key=lambda f: f["family"].lower())
    manifest = {
        "google_fonts_commit": GOOGLE_FONTS_COMMIT,
        "families": families,
        "bytes": sum(f["bytes"] for f in families),
    }
    (OUT_DIR / "manifest.json").write_bytes(
        (json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    write_css(families)
    write_docs(manifest)
    print(f"{len(families)} families, {manifest['bytes'] / 1_000_000:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
