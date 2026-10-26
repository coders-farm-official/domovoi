"""The editors' fonts: the licence rules, the bundled set, the routes, the
internet gate, the file checks and the browser registry.

* Licence filter (``web.backend.fonts_store``): only OFL-1.1 and Apache-2.0,
  and only when METADATA.pb and the licence file agree. The UFL, CC BY-SA,
  a missing or unknown licence are refused. Reserved Font Names are read
  from the OFL header.
* The bundled set (``web/static/fonts``): every family is allowed, carries
  its licence file, every face is real WOFF2 that the CSS points at, a
  reserved-name family is never cut down, and docs/LICENSES.md lists them.
* The routes (``web/backend/api/fonts.py``): ``GET /api/fonts`` is the
  household read; the catalogue, fetch and remove are Bearer-only admin.
* The internet answer: under ``never`` nothing leaves (409, the shared
  refusal), and GitHub is never asked.
* "More fonts" end to end against a scripted GitHub: a family is checked,
  stored unchanged, recorded and served; a UFL one, a non-font file and an
  oversize one are refused and leave nothing behind (DB-backed half).
* The registry (``web/static/fonts.jsx``) compiled by the vendored Babel
  and run in node (``fonts_registry_harness.js``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import struct
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from domovoi import egress
from domovoi.config import settings as core_settings
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db, web_client
from domovoi.tests.conftest import requires_db
from web.backend import fonts_store as fs
from web.backend.api import fonts as fonts_api

REPO = Path(__file__).resolve().parents[2]
FONTS = REPO / "web" / "static" / "fonts"
ADMIN = "admin-session-token"
DEVICE = "lantern-river-kettle-harbor"
COMMIT = "a" * 40

OFL_TEXT = (
    "Copyright 2020 The Example Project Authors\n\n"
    "This Font Software is licensed under the SIL Open Font License, Version 1.1.\n"
    "This license is copied below, and is also available with a FAQ at:\n"
    "https://openfontlicense.org\n\n"
    "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007\n"
    "\"Reserved Font Name\" refers to any names specified as such after the copyright statement(s).\n"
)
OFL_RFN = OFL_TEXT.replace("Authors\n", "Authors with Reserved Font Name \"Lantern\".\n")
APACHE_TEXT = "                                 Apache License\n                           Version 2.0, January 2004\n"
UFL_TEXT = "UBUNTU FONT LICENCE Version 1.0\n\nPreamble\n"


def _sfnt(version: bytes, tables=(b"head", b"cmap", b"glyf"), body: int = 32) -> bytes:
    """A minimal font file whose table directory holds together."""
    n = len(tables)
    out = bytearray(version + struct.pack(">HHHH", n, 16, 0, 0))
    offset = 12 + 16 * n
    for tag in tables:
        out += struct.pack(">4sIII", tag, 0, offset, body)
        offset += body
    out += b"\x00" * (body * n)
    return bytes(out)


def _woff2(length_delta: int = 0) -> bytes:
    data = bytearray(b"wOF2" + b"\x00\x01\x00\x00" + b"\x00" * 4 + struct.pack(">H", 3) + b"\x00" * 50)
    data[8:12] = struct.pack(">I", len(data) + length_delta)
    return bytes(data)


TTF = _sfnt(b"\x00\x01\x00\x00")
OTF = _sfnt(b"OTTO", tables=(b"head", b"cmap", b"CFF "))


def _meta(name: str, licence: str = "OFL", category: str = "SERIF", files=None, axes=None) -> str:
    files = files or [("normal", 400, f"{name.replace(' ', '')}-Regular.ttf"),
                      ("normal", 700, f"{name.replace(' ', '')}-Bold.ttf")]
    out = [f'name: "{name}"', 'designer: "Example"', f'license: "{licence}"', f'category: "{category}"']
    for style, weight, fn in files:
        out += ["fonts {", f'  name: "{name}"', f'  style: "{style}"', f"  weight: {weight}",
                f'  filename: "{fn}"', '  copyright: "Copyright 2020 Example"', "}"]
    out.append('subsets: "latin"')
    for tag, lo, hi in axes or []:
        out += ["axes {", f'  tag: "{tag}"', f"  min_value: {lo}", f"  max_value: {hi}", "}"]
    return "\n".join(out) + "\n"


# ─── the licence filter ───────────────────────────────────────────────────


def test_an_ofl_family_passes():
    assert fs.licence_verdict("OFL", OFL_TEXT) == ("OFL-1.1", "")


def test_an_apache_family_passes():
    assert fs.licence_verdict("APACHE2", APACHE_TEXT) == ("Apache-2.0", "")


@pytest.mark.parametrize("meta", ["UFL", "CC-BY-SA", "", "PROPRIETARY", "GPL", "FREE FOR PERSONAL USE"])
def test_any_other_stated_licence_is_refused(meta):
    spdx, why = fs.licence_verdict(meta, OFL_TEXT)
    assert spdx is None and "only OFL and Apache" in why


def test_ufl_text_is_refused_even_when_metadata_says_ofl():
    spdx, why = fs.licence_verdict("OFL", UFL_TEXT)
    assert spdx is None and "does not match" in why
    assert fs.licence_text_kind(UFL_TEXT) is None


def test_a_licence_file_that_disagrees_or_is_missing_is_refused():
    assert fs.licence_verdict("OFL", APACHE_TEXT)[0] is None
    assert fs.licence_verdict("APACHE2", OFL_TEXT)[0] is None
    assert fs.licence_verdict("OFL", None) == (None, "its licence file is missing")
    assert fs.licence_verdict("OFL", "All rights reserved.")[0] is None


def test_reserved_font_names_come_from_the_header_only():
    assert fs.reserved_font_names(OFL_TEXT) == []  # the body defines the term; no name
    assert fs.reserved_font_names(OFL_RFN) == ["Lantern"]
    lib = ("Digitized data copyright (c) 2010 Google Corporation\n\twith Reserved Font Arimo, Tinos and Cousine.\n"
           "Copyright (c) 2012 Red Hat, Inc.\n\twith Reserved Font Name Liberation.\n\n" + OFL_TEXT)
    assert fs.reserved_font_names(lib) == ["Arimo", "Tinos", "Cousine", "Liberation"]
    adobe = "Copyright 2010 Adobe, with Reserved Font Name 'Source'. All Rights Reserved.\n\n" + OFL_TEXT
    assert fs.reserved_font_names(adobe) == ["Source"]


# ─── size and magic bytes ─────────────────────────────────────────────────


@pytest.mark.parametrize("data,kind", [
    (_sfnt(b"\x00\x01\x00\x00"), "ttf"), (_sfnt(b"true"), "ttf"),
    (_sfnt(b"OTTO", tables=(b"head", b"cmap", b"CFF ")), "otf"),
    (_woff2(), "woff2"), (b"wOFF" + _woff2()[4:], "woff"),
    (b"ttcf" + b"\x00" * 60, None), (b"<!DOCTYPE html>", None), (b"PK\x03\x04" + b"\x00" * 60, None),
    (b"\x89PNG" + b"\x00" * 60, None), (b"", None),
])
def test_only_real_font_signatures_are_fonts(data, kind):
    assert fs.sniff_font(data) == kind


@pytest.mark.parametrize("data", [
    b"true<html><script>alert(document.domain)</script></html>",   # four magic bytes and a page
    b"\x00\x01\x00\x00" + b"\x00" * 60,                            # no tables
    _sfnt(b"\x00\x01\x00\x00")[:-1],                                # last table runs past the end
    _sfnt(b"\x00\x01\x00\x00", tables=(b"head", b"glyf")),         # no cmap
    _sfnt(b"\x00\x01\x00\x00", tables=(b"head", b"cmap")),         # no outlines
    _sfnt(b"\x00\x01\x00\x00", tables=(b"he\x00d", b"cmap", b"glyf")),  # a tag that isn't text
    b"\x00\x01" + b"\xff\xff" + b"\x00" * 60,                        # an absurd table count
    _woff2(length_delta=1), _woff2(length_delta=-1),               # WOFF2 header length lies
])
def test_a_file_that_only_starts_like_a_font_is_refused(data):
    assert fs.sniff_font(data) is None


def test_the_bundled_and_drafts_fonts_pass_the_structure_check():
    for p in list(FONTS.rglob("*.woff2"))[:40] + list((REPO / "domovoi" / "drafts" / "fonts").glob("*.ttf")):
        assert fs.sniff_font(p.read_bytes()) in ("woff2", "ttf"), p


@pytest.mark.parametrize("name,ok", [
    ("Roboto-Italic[wdth,wght].ttf", True), ("OFL.txt", True), ("LICENSE", True),
    ("../etc/passwd", False), ("a/b.ttf", False), (".adding-x", False), ("x..y.ttf", False), ("", False),
    ("a\\b.ttf", False),
])
def test_file_names_are_checked_before_a_path_is_built(name, ok):
    assert fs.valid_filename(name) is ok


def test_the_caps_are_set():
    assert 4 * 1024 * 1024 <= fs.MAX_FILE_BYTES <= 32 * 1024 * 1024
    assert fs.MAX_FAMILY_BYTES >= fs.MAX_FILE_BYTES and fs.MAX_FILES == 4


# ─── METADATA and which files are taken ───────────────────────────────────


def test_a_static_family_takes_regular_bold_italic_and_bold_italic():
    files = [(s, w, f"X-{s}{w}.ttf") for s in ("normal", "italic") for w in range(100, 1000, 100)]
    meta = fs.parse_metadata(_meta("Example Sans", files=files))
    assert meta["name"] == "Example Sans" and meta["license"] == "OFL"
    picked = [(f["style"], f["weight"]) for f in fs.pick_files(meta)]
    assert picked == [("normal", 400), ("normal", 700), ("italic", 400), ("italic", 700)]


def test_a_variable_family_takes_its_variable_files():
    meta = fs.parse_metadata(_meta("Example", files=[
        ("normal", 400, "Example[wght].ttf"), ("italic", 400, "Example-Italic[wght].ttf")],
        axes=[("wght", 200.0, 800.0)]))
    picked = fs.pick_files(meta)
    assert [p["filename"] for p in picked] == ["Example[wght].ttf", "Example-Italic[wght].ttf"]
    assert fs.weight_range(meta, picked[0]) == (200, 800)


def test_a_one_weight_display_face_takes_that_weight():
    meta = fs.parse_metadata(_meta("Shout", category="DISPLAY", files=[("normal", 400, "Shout-Regular.ttf")]))
    assert [p["filename"] for p in fs.pick_files(meta)] == ["Shout-Regular.ttf"]
    assert fs.category_of(meta) == "display"


# ─── the bundled set ──────────────────────────────────────────────────────


def _manifest() -> dict:
    return json.loads((FONTS / "manifest.json").read_text(encoding="utf-8"))


def test_the_bundled_set_is_about_sixty_open_licence_families():
    m = _manifest()
    fams = m["families"]
    assert len(fams) >= 60
    assert {f["licence"] for f in fams} <= {"OFL-1.1", "Apache-2.0"}
    assert {f["category"] for f in fams} == set(fs.CATEGORY_ORDER)
    names = {f["family"] for f in fams}
    for must in ("Roboto", "Open Sans", "Lato", "Merriweather", "Fira Code", "Caveat", "Inter",
                 "JetBrains Mono", "Vast Shadow", "Liberation Sans", "Liberation Serif", "Liberation Mono",
                 "Carlito", "Caladea", "OpenDyslexic", "Atkinson Hyperlegible", "Comic Neue"):
        assert must in names, must
    assert re.fullmatch(r"[0-9a-f]{40}", m["google_fonts_commit"])


def test_every_bundled_family_carries_its_licence_and_real_woff2():
    total = 0
    for f in _manifest()["families"]:
        folder = FONTS / f["slug"]
        assert fs.valid_slug(f["slug"]), f["slug"]
        lic = (folder / f["licence_file"]).read_text(encoding="utf-8")
        assert fs.licence_verdict({"OFL-1.1": "OFL", "Apache-2.0": "APACHE2"}[f["licence"]], lic)[0] == f["licence"]
        assert (folder / "CHANGES.txt").is_file()
        assert f["faces"], f["family"]
        for face in f["faces"]:
            data = (folder / face["file"]).read_bytes()
            assert fs.sniff_font(data) == "woff2", face["file"]
            assert len(data) == face["bytes"]
            total += len(data)
        if f["reserved_names"]:
            # Kept whole (OFL-FAQ 2.2.1): no subset parts, not marked modified.
            assert not f["modified"] and not any(x.get("unicode_range") for x in f["faces"]), f["family"]
    assert total == _manifest()["bytes"]
    assert total < 40 * 1000 * 1000, f"{total / 1e6:.1f} MB: trim weights before families"


def test_no_stray_family_folders_and_no_ufl_source():
    listed = {f["slug"] for f in _manifest()["families"]}
    on_disk = {p.name for p in FONTS.iterdir() if p.is_dir()}
    assert on_disk == listed
    src = (REPO / "scripts" / "vendor" / "fonts.py").read_text(encoding="utf-8")
    assert re.search(r'GOOGLE_FONTS_COMMIT = "[0-9a-f]{40}"', src)
    paths = re.findall(r'\("((?:ofl|apache|ufl|cc-by-sa)/[a-z0-9]+)", "', src)
    assert paths and all(p.startswith(("ofl/", "apache/")) for p in paths)


def test_the_css_names_every_face_and_nothing_else():
    css = (FONTS / "fonts.css").read_text(encoding="utf-8")
    urls = set(re.findall(r"url\('(/fonts/[^']+)'\)", css))
    want = {f"/fonts/{f['slug']}/{x['file']}" for f in _manifest()["families"] for x in f["faces"]}
    assert urls == want
    assert css.count("@font-face") == len(want)
    assert "font-display: swap" in css


def test_licences_doc_lists_every_bundled_family():
    doc = (REPO / "docs" / "LICENSES.md").read_text(encoding="utf-8")
    block = doc.split("<!-- fonts:start", 1)[1].split("<!-- fonts:end -->", 1)[0]
    for f in _manifest()["families"]:
        assert f"| {f['family']} | {f['category']} | {f['licence']} |" in block, f["family"]


# ─── a scripted GitHub ────────────────────────────────────────────────────


class FakeGitHub:
    """Stands in for fonts_api._http_get: url -> bytes (404 when absent)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.asked: list[str] = []
        self.files[f"{fonts_api.GITHUB_API}/commits/main"] = COMMIT.encode()

    def family(self, top: str, slug: str, meta: str, licence: tuple[str, str] | None, fonts: dict[str, bytes]):
        base = f"{fonts_api.RAW_BASE}/{COMMIT}/{top}/{slug}/"
        self.files[base + "METADATA.pb"] = meta.encode()
        if licence:
            self.files[base + licence[0]] = licence[1].encode()
        for name, data in fonts.items():
            self.files[base + name] = data

    def trees(self, dirs: dict[str, list[str]]) -> None:
        root = [{"path": top, "type": "tree", "sha": hashlib.sha1(top.encode()).hexdigest()} for top in dirs]
        root.append({"path": "README.md", "type": "blob", "sha": "b" * 40})
        self.files[f"{fonts_api.GITHUB_API}/git/trees/{COMMIT}"] = json.dumps({"tree": root}).encode()
        for node in root[:-1]:
            kids = [{"path": d, "type": "tree", "sha": "d" * 40} for d in dirs[node["path"]]]
            self.files[f"{fonts_api.GITHUB_API}/git/trees/{node['sha']}"] = json.dumps({"tree": kids}).encode()

    async def __call__(self, url: str, max_bytes: int, accept: str | None = None):
        self.asked.append(url)
        if url not in self.files:
            return 404, b""
        data = self.files[url]
        if len(data) > max_bytes:
            raise fonts_api._TooLarge(len(data))
        return 200, data


@pytest.fixture
def github(monkeypatch, tmp_path):
    gh = FakeGitHub()
    monkeypatch.setattr(fonts_api, "_http_get", gh)
    monkeypatch.setattr(core_settings, "fonts_dir", str(tmp_path / "fonts"), raising=False)
    monkeypatch.setattr(fonts_api, "_catalogue_mem", None)
    return gh


@pytest.fixture
def auth(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN}, device_token=DEVICE)


@pytest.fixture
def no_rows(monkeypatch):
    async def none():
        return []

    monkeypatch.setattr(fonts_api, "_added_rows", none)


# ─── tiers (DB-free) ──────────────────────────────────────────────────────


async def test_the_list_is_a_household_read(auth, no_rows, github):
    async with web_client() as c:
        assert (await c.get("/api/fonts")).status_code == 401
        r = await c.get("/api/fonts", headers={HEADER: DEVICE})
    assert r.status_code == 200
    body = r.json()
    assert len(body["families"]) == len(_manifest()["families"])
    lora = next(f for f in body["families"] if f["family"] == "Lora")
    assert lora["source"] == "bundled" and lora["faces"][0]["url"].startswith("/fonts/lora/")
    assert body["css"] == "/fonts/fonts.css" and github.asked == []


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/fonts/catalogue", None),
    ("POST", "/api/fonts/fetch", {"family": "Gloock"}),
    ("DELETE", "/api/fonts/gloock", None),
])
async def test_catalogue_fetch_and_remove_are_admin_bearer_only(auth, no_rows, github, method, path, body):
    async with web_client() as c:
        kw = {"json": body} if body else {}
        nobody = await c.request(method, path, **kw)
        device = await c.request(method, path, headers={HEADER: DEVICE, "X-Requested-With": "t"}, **kw)
    async with web_client(cookies={COOKIE: ADMIN}) as c:
        cookie = await c.request(method, path, **kw)
    assert nobody.status_code == 401
    assert device.status_code in (401, 403)
    assert cookie.status_code == 403
    assert github.asked == [], "a refused request reached GitHub"


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/fonts/catalogue", None),
    ("POST", "/api/fonts/fetch", {"family": "Gloock"}),
])
async def test_internet_off_refuses_before_asking_github(auth, no_rows, github, method, path, body):
    with egress.override_policy("never"):
        async with web_client() as c:
            r = await c.request(method, path, headers={**bearer(ADMIN), "X-Requested-With": "t"},
                                **({"json": body} if body else {}))
    assert r.status_code == 409
    assert r.json()["detail"] == egress.TURNED_OFF_REASON
    assert r.headers[egress.REFUSAL_HEADER] == egress.REFUSAL_VALUE
    assert github.asked == []


async def test_the_list_says_when_internet_is_off(auth, no_rows, github):
    with egress.override_policy("never"):
        async with web_client() as c:
            r = await c.get("/api/fonts", headers={HEADER: DEVICE})
    assert r.status_code == 200 and r.json()["internet_off"] is True


async def test_the_real_http_path_goes_through_the_egress_gate(monkeypatch):
    """No stand-in: under never the egress client refuses GitHub itself."""
    with egress.override_policy("never"):
        with pytest.raises(egress.InternetTurnedOff):
            await fonts_api._http_get("https://raw.githubusercontent.com/google/fonts/x", 10)


async def test_a_bundled_family_is_not_fetched_again(auth, no_rows, github):
    async with web_client() as c:
        r = await c.post("/api/fonts/fetch", json={"family": "Lora"}, headers=bearer(ADMIN))
    assert r.status_code == 409 and "comes with Domovoi" in r.json()["detail"]
    assert github.asked == []


async def test_bundled_files_are_served_and_odd_names_are_not(auth):
    lora = next(f for f in _manifest()["families"] if f["slug"] == "lora")
    async with web_client() as c:
        ok = await c.get(f"/fonts/lora/{lora['faces'][0]['file']}")
        lic = await c.get(f"/fonts/lora/{lora['licence_file']}")
        bad = [await c.get(p) for p in ("/fonts/lora/..%2F..%2Fmanifest.json", "/fonts/..%2Fvendor/x.woff2",
                                        "/fonts/lora/manifest.json", "/fonts/LORA/OFL.txt")]
        css = await c.get("/fonts/fonts.css")
    assert ok.status_code == 200 and ok.headers["content-type"] == "font/woff2"
    assert ok.content[:4] == b"wOF2" and "max-age" in ok.headers["cache-control"]
    assert lic.status_code == 200 and lic.headers["content-type"].startswith("text/plain")
    assert all(r.status_code == 404 for r in bad), [r.status_code for r in bad]
    assert css.status_code == 200 and "@font-face" in css.text


async def test_the_catalogue_lists_only_ofl_and_apache(auth, no_rows, github):
    github.trees({"ofl": ["gloock", "sneaky"], "apache": ["kosugi"], "ufl": ["ubuntu"]})
    github.family("ofl", "gloock", _meta("Gloock"), None, {})
    github.family("ofl", "sneaky", _meta("Sneaky", licence="UFL"), None, {})
    github.family("apache", "kosugi", _meta("Kosugi", licence="APACHE2", category="SANS_SERIF"), None, {})
    github.family("ufl", "ubuntu", _meta("Ubuntu", licence="UFL"), None, {})
    async with web_client() as c:
        r = await c.get("/api/fonts/catalogue", headers=bearer(ADMIN))
    assert r.status_code == 200, r.text
    got = {f["family"]: f for f in r.json()["families"]}
    assert set(got) == {"Gloock", "Kosugi"}
    assert got["Kosugi"]["licence"] == "Apache-2.0" and got["Gloock"]["category"] == "serif"
    assert not any("/ufl/" in u for u in github.asked), "the ufl/ folder was read"
    assert (Path(core_settings.fonts_dir) / "catalogue.json").is_file()


async def test_redirects_are_not_followed_off_github(monkeypatch):
    """A 302 from raw.githubusercontent.com to a machine in the house is an
    answer we don't use, never a second request (review round 1)."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(302, headers={"Location": "http://192.168.1.1/cgi-bin/luci?x=1"})
        return httpx.Response(200, content=b"router admin page")

    real = egress.async_client

    def patched(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(**kw)

    monkeypatch.setattr(fonts_api.egress, "async_client", patched)
    with egress.override_policy("always"):
        status, body = await fonts_api._http_get(f"{fonts_api.RAW_BASE}/{COMMIT}/ofl/x/METADATA.pb", 1024)
        with pytest.raises(fonts_api.FontsUnavailable):
            await fonts_api._get(f"{fonts_api.RAW_BASE}/{COMMIT}/ofl/x/METADATA.pb", 1024)
    assert status == 302 and body == b""
    assert all("192.168.1.1" not in u for u in seen), seen


async def test_an_oversize_github_answer_is_the_plain_error_not_a_500(auth, no_rows, github, monkeypatch):
    github.trees({"ofl": ["gloock"]})
    monkeypatch.setattr(fonts_api, "TREE_MAX_BYTES", 10)
    async with web_client() as c:
        r = await c.get("/api/fonts/catalogue", headers=bearer(ADMIN))
    assert r.status_code == 502 and "GitHub" in r.json()["detail"]


async def test_refresh_rebuilds_at_most_every_ten_minutes(auth, no_rows, github):
    github.trees({"ofl": ["gloock"]})
    github.family("ofl", "gloock", _meta("Gloock"), None, {})
    async with web_client() as c:
        first = await c.get("/api/fonts/catalogue", headers=bearer(ADMIN))
        asked = len(github.asked)
        again = await c.get("/api/fonts/catalogue?refresh=true", headers=bearer(ADMIN))
    assert first.status_code == again.status_code == 200
    assert len(github.asked) == asked, "a refresh minutes after a build asked GitHub again"


async def test_a_catalogue_build_has_a_deadline(auth, no_rows, github, monkeypatch):
    async def slow(url, max_bytes, accept=None):
        await asyncio.sleep(5)
        return 404, b""

    monkeypatch.setattr(fonts_api, "_http_get", slow)
    monkeypatch.setattr(fonts_api, "CATALOGUE_DEADLINE_SEC", 0.2)
    async with web_client() as c:
        r = await c.get("/api/fonts/catalogue", headers=bearer(ADMIN))
    assert r.status_code == 502


def test_the_picker_asks_nothing_when_a_document_opens():
    """Opening a document must cause no egress (review round 1): the
    registry and picker have no lookup, and the only routes fonts.jsx
    calls by itself are the list; the catalogue and an add need a click."""
    src = (REPO / "web" / "static" / "fonts.jsx").read_text(encoding="utf-8")
    assert "/api/fonts/lookup" not in src and "lookup" not in src
    picker = src.split("const Picker = (", 1)[1].split("return { Picker", 1)[0]
    # The picker's only network call is the admin's click: F.add.
    assert not re.search(r"\bapi(Get|Post|Delete|Fetch)\(|\bfetch\(", picker)
    assert picker.count("F.add(") == 1 and "const addForHouse" in picker


async def test_github_down_is_a_plain_error(auth, no_rows, github, monkeypatch):
    async def down(url, max_bytes, accept=None):
        raise OSError("no route to host")

    monkeypatch.setattr(fonts_api, "_http_get", down)
    async with web_client() as c:
        r = await c.get("/api/fonts/catalogue", headers=bearer(ADMIN))
    assert r.status_code == 502 and "GitHub" in r.json()["detail"]


# ─── "More fonts" end to end (DB-backed: the fonts table, V041) ──────────


@pytest.fixture
async def fonts_table():
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM fonts"))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM fonts"))


def _gloock(github, fonts=None, licence=("OFL.txt", OFL_RFN), meta=None):
    github.family("ofl", "gloock", meta or _meta("Gloock"), licence,
                  fonts if fonts is not None else {"Gloock-Regular.ttf": TTF, "Gloock-Bold.ttf": OTF})


@requires_db
async def test_an_ofl_family_is_added_stored_unchanged_and_served(auth, github, fonts_table):
    _gloock(github)
    async with web_client() as c:
        r = await c.post("/api/fonts/fetch", json={"family": "Gloock"}, headers=bearer(ADMIN))
        assert r.status_code == 200, r.text
        fam = r.json()["family"]
        again = await c.post("/api/fonts/fetch", json={"family": "gloock"}, headers=bearer(ADMIN))
        listed = (await c.get("/api/fonts", headers={HEADER: DEVICE})).json()["families"]
        served = await c.get(fam["faces"][0]["url"])
        lic = await c.get(fam["licence_url"])
    assert r.json()["added"] is True and again.json()["added"] is False
    assert fam["source"] == "fetched" and fam["licence"] == "OFL-1.1" and fam["weights"] == [400, 700]
    folder = Path(core_settings.fonts_dir) / "gloock"
    stored = sorted(p.name for p in folder.iterdir())
    assert stored == ["OFL.txt", "gloock-normal-400.ttf", "gloock-normal-700.otf"]
    assert (folder / "gloock-normal-400.ttf").read_bytes() == TTF  # unchanged
    assert any(f["family"] == "Gloock" and f["source"] == "fetched" for f in listed)
    assert served.status_code == 200 and served.content == TTF and served.headers["content-type"] == "font/ttf"
    assert lic.status_code == 200 and "Reserved Font Name" in lic.text
    from domovoi.db.session import engine

    async with engine.begin() as conn:
        row = (await conn.execute(text(
            "SELECT licence, source_path, source_commit, bytes FROM fonts WHERE slug='gloock'"))).one()
    assert tuple(row) == ("OFL-1.1", "ofl/gloock", COMMIT, len(TTF) + len(OTF))
    assert [k for k in Path(core_settings.fonts_dir).iterdir() if k.name.startswith(".adding")] == []


@requires_db
@pytest.mark.parametrize("case", ["ufl-metadata", "ufl-text", "no-licence-file", "only-in-ufl"])
async def test_a_family_that_is_not_ofl_or_apache_is_refused(auth, github, fonts_table, case):
    if case == "ufl-metadata":
        _gloock(github, meta=_meta("Gloock", licence="UFL"))
    elif case == "ufl-text":
        _gloock(github, licence=("OFL.txt", UFL_TEXT))
    elif case == "no-licence-file":
        _gloock(github, licence=None)
    else:
        github.family("ufl", "gloock", _meta("Gloock", licence="UFL"), ("UFL.txt", UFL_TEXT),
                      {"Gloock-Regular.ttf": TTF})
    async with web_client() as c:
        r = await c.post("/api/fonts/fetch", json={"family": "Gloock"}, headers=bearer(ADMIN))
    assert r.status_code == 422, r.text
    assert "Can't add Gloock" in r.json()["detail"]
    assert not any(u.endswith(".ttf") for u in github.asked), "a font file was downloaded"
    root = Path(core_settings.fonts_dir)
    assert not root.exists() or [p.name for p in root.iterdir()] == []


@requires_db
async def test_a_file_that_is_not_a_font_is_refused_and_nothing_kept(auth, github, fonts_table):
    _gloock(github, fonts={"Gloock-Regular.ttf": TTF, "Gloock-Bold.ttf": b"<!DOCTYPE html><html>"})
    async with web_client() as c:
        r = await c.post("/api/fonts/fetch", json={"family": "Gloock"}, headers=bearer(ADMIN))
        listed = (await c.get("/api/fonts", headers={HEADER: DEVICE})).json()["families"]
    assert r.status_code == 422 and "not a font" in r.json()["detail"]
    assert [p.name for p in Path(core_settings.fonts_dir).iterdir()] == []
    assert not any(f["family"] == "Gloock" for f in listed)


@requires_db
async def test_an_oversize_file_is_refused(auth, github, fonts_table, monkeypatch):
    monkeypatch.setattr(fs, "MAX_FILE_BYTES", 32)
    _gloock(github)
    async with web_client() as c:
        r = await c.post("/api/fonts/fetch", json={"family": "Gloock"}, headers=bearer(ADMIN))
    assert r.status_code == 422 and "too large" in r.json()["detail"]
    assert [p.name for p in Path(core_settings.fonts_dir).iterdir()] == []


@requires_db
async def test_a_page_that_starts_with_true_is_not_a_font(auth, github, fonts_table):
    html = b"true<html><script>alert(document.domain)</script></html>"
    _gloock(github, fonts={"Gloock-Regular.ttf": html, "Gloock-Bold.ttf": TTF})
    async with web_client() as c:
        r = await c.post("/api/fonts/fetch", json={"family": "Gloock"}, headers=bearer(ADMIN))
    assert r.status_code == 422 and "not a font" in r.json()["detail"]
    assert [p.name for p in Path(core_settings.fonts_dir).iterdir()] == []


def _faces(github, n):
    for i in range(n):
        name = f"Face{chr(97 + i)}"
        github.family("ofl", name.lower(), _meta(name), ("OFL.txt", OFL_TEXT),
                      {f"{name}-Regular.ttf": TTF, f"{name}-Bold.ttf": TTF})


@requires_db
async def test_the_house_has_a_cap_on_added_families(auth, github, fonts_table, monkeypatch):
    monkeypatch.setattr(fs, "MAX_ADDED_FAMILIES", 2)
    _faces(github, 3)
    async with web_client() as c:
        codes = [(await c.post("/api/fonts/fetch", json={"family": f"Face{c_}"}, headers=bearer(ADMIN))).status_code
                 for c_ in "abc"]
        third = await c.post("/api/fonts/fetch", json={"family": "Facec"}, headers=bearer(ADMIN))
    assert codes == [200, 200, 409]
    assert "2 added font families" in third.json()["detail"]
    assert not (Path(core_settings.fonts_dir) / "facec").exists()


@requires_db
async def test_the_house_has_a_cap_on_added_bytes(auth, github, fonts_table, monkeypatch):
    monkeypatch.setattr(fs, "MAX_ADDED_BYTES", 3 * len(TTF))
    _faces(github, 2)
    async with web_client() as c:
        a = await c.post("/api/fonts/fetch", json={"family": "Facea"}, headers=bearer(ADMIN))
        b = await c.post("/api/fonts/fetch", json={"family": "Faceb"}, headers=bearer(ADMIN))
    assert a.status_code == 200
    # One file's room is left: the second family's two files don't fit.
    assert b.status_code == 422 and "too large" in b.json()["detail"]
    assert not (Path(core_settings.fonts_dir) / "faceb").exists()


@requires_db
async def test_stale_staging_folders_are_swept(auth, github, fonts_table):
    root = Path(core_settings.fonts_dir)
    (root / ".adding-gloock-deadbeef").mkdir(parents=True)
    (root / ".adding-gloock-deadbeef" / "x.ttf").write_bytes(b"half")
    assert fonts_api.sweep_staging() == 1 and not (root / ".adding-gloock-deadbeef").exists()
    (root / ".adding-old-0000").mkdir()
    _gloock(github)
    async with web_client() as c:
        r = await c.post("/api/fonts/fetch", json={"family": "Gloock"}, headers=bearer(ADMIN))
    assert r.status_code == 200
    assert sorted(p.name for p in root.iterdir()) == ["gloock"]


@requires_db
async def test_an_add_has_a_deadline_and_frees_the_lock(auth, github, fonts_table, monkeypatch):
    _gloock(github)
    real = github.__call__

    async def trickle(url, max_bytes, accept=None):
        if url.endswith(".ttf"):
            await asyncio.sleep(5)
        return await real(url, max_bytes, accept)

    monkeypatch.setattr(fonts_api, "_http_get", trickle)
    monkeypatch.setattr(fonts_api, "ADD_DEADLINE_SEC", 0.3)
    async with web_client() as c:
        r = await c.post("/api/fonts/fetch", json={"family": "Gloock"}, headers=bearer(ADMIN))
        assert r.status_code == 504
        assert not fonts_api._add_lock.locked()
        monkeypatch.setattr(fonts_api, "_http_get", github)
        ok = await c.post("/api/fonts/fetch", json={"family": "Gloock"}, headers=bearer(ADMIN))
    assert ok.status_code == 200
    assert [p.name for p in Path(core_settings.fonts_dir).iterdir()] == ["gloock"]


@requires_db
async def test_an_unknown_family_is_a_404(auth, github, fonts_table):
    async with web_client() as c:
        r = await c.post("/api/fonts/fetch", json={"family": "No Such Face"}, headers=bearer(ADMIN))
    assert r.status_code == 404


@requires_db
async def test_remove_deletes_the_row_and_the_files(auth, github, fonts_table):
    _gloock(github)
    async with web_client() as c:
        assert (await c.post("/api/fonts/fetch", json={"family": "Gloock"}, headers=bearer(ADMIN))).status_code == 200
        bundled = await c.delete("/api/fonts/lora", headers=bearer(ADMIN))
        r = await c.delete("/api/fonts/gloock", headers=bearer(ADMIN))
        gone = await c.delete("/api/fonts/gloock", headers=bearer(ADMIN))
        served = await c.get("/fonts/gloock/gloock-normal-400.ttf")
    assert bundled.status_code == 409
    assert r.status_code == 200 and r.json() == {"removed": "Gloock"}
    assert gone.status_code == 404 and served.status_code == 404
    assert not (Path(core_settings.fonts_dir) / "gloock").exists()


@requires_db
async def test_the_realtime_snapshot_names_added_families(auth, github, fonts_table):
    from web.backend import realtime

    assert await realtime.StatePollLoop._CHANNEL_HELPERS["fonts"]() == []
    _gloock(github)
    async with web_client() as c:
        await c.post("/api/fonts/fetch", json={"family": "Gloock"}, headers=bearer(ADMIN))
    snap = await realtime.StatePollLoop._CHANNEL_HELPERS["fonts"]()
    assert [s["slug"] for s in snap] == ["gloock"]
    assert realtime.NOTIFY_CHANNEL_TO_REALTIME["fonts_changed"] == "fonts"


# ─── the browser registry ─────────────────────────────────────────────────


def _registry() -> dict:
    node = shutil.which("node")
    assert node, "node is required to run the registry the way the browser does"
    out = subprocess.run([node, str(Path(__file__).with_name("fonts_registry_harness.js")), str(REPO)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_registry_compiles_and_keeps_its_interface():
    r = _registry()
    for name in ("list", "ensure", "onChange", "load", "status", "familiesInHTML", "missingInHTML",
                 "recent", "markUsed", "add", "remove", "setFetcher", "Picker"):
        assert name in r["exports"], name
    assert r["pickerIsFunction"] and r["settingsPanel"]
    assert r["listBeforeLoad"] == 0
    assert r["listKeys"] == ["category", "family", "italic", "licence", "slug", "source", "weights"]
    assert r["listAfterLoad"][0] == "Lora|serif|bundled|400,700"
    assert r["listAfterLoad"][2].endswith("|fetched|400,700")


def test_the_registry_loads_faces_only_when_asked():
    r = _registry()
    assert r["ensureLora"] is True and r["ensureGloock"] is True and r["ensureMissing"] is False
    tags = [(h["tag"], h["marker"]) for h in r["head"]]
    assert tags == [("link", "bundled"), ("style", "gloock"), ("style", "office-substitutes")]
    assert r["head"][0]["href"] == "/fonts/fonts.css"
    assert "src:url('/fonts/gloock/" in r["head"][1]["css"]
    assert 'local("Arial")' in r["head"][2]["css"]
    assert "italic 700 1em \"Lora\"" in r["loadsAsked"]


def test_the_registry_marks_what_a_document_lacks():
    r = _registry()
    assert r["status"] == {"lora": "installed", "loraQuoted": "installed", "arial": "substitute",
                           "calibri": "missing", "comic": "missing", "serif": "generic", "empty": "generic"}
    assert r["html"] == ["Lora", "Comic Sans MS", "Gloock", "Papyrus"]
    assert r["missing"] == ["Comic Sans MS"]
    assert r["stack"] == {"lora": '"Lora", serif', "unknown": '"Papyrus", sans-serif'}


def test_the_registry_tells_the_editors_when_fonts_change():
    r = _registry()
    assert r["changesAfterFirstLoad"] == 1
    assert r["changesAfterEvent"] == 1 and r["hasOi"] == "installed"
    assert r["changesAfterSameList"] == 1, "an unchanged list fired onChange"
    assert r["afterRefusal"] == 4, "a failed re-read dropped the list"
    assert r["recent"] == ["Lora", "Gloock"]
