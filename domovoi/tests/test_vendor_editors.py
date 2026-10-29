"""The Documents editor's vendored bundles (web/static/vendor/{tiptap,yjs,mammoth}).

They are built once, from exact pinned versions, by
scripts/vendor/editors/build.mjs, which records every npm package inside
each bundle. These pin what that promised: exact pins, permissive licences
only (the dependency policy), a licence text beside every package, the
names in docs/LICENSES.md, and ONE Yjs for the page (the editor bundle
borrows window.Y; a second copy breaks co-editing silently).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
VENDOR = REPO / "web" / "static" / "vendor"
BUILD = REPO / "scripts" / "vendor" / "editors"
BUNDLES = {"tiptap": "tiptap.min.js", "yjs": "yjs.min.js", "mammoth": "mammoth.min.js"}
PERMISSIVE = {"MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "MIT AND Zlib"}


def test_every_source_is_pinned_exactly():
    pkg = json.loads((BUILD / "package.json").read_text(encoding="utf-8"))
    for name, ver in {**pkg["dependencies"], **pkg["devDependencies"]}.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", ver), f"{name} is not pinned exactly: {ver}"
    lock = json.loads((BUILD / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["packages"][""]["dependencies"] == pkg["dependencies"]


@pytest.mark.parametrize("bundle", sorted(BUNDLES))
def test_each_bundle_lists_its_packages_with_permissive_licences(bundle):
    folder = VENDOR / bundle
    assert (folder / BUNDLES[bundle]).stat().st_size > 10_000
    rows = json.loads((folder / "THIRD-PARTY.json").read_text(encoding="utf-8"))
    assert rows, bundle
    for row in rows:
        assert row["license"] in PERMISSIVE, row
        text = folder / "licenses" / (row["name"].replace("/", "__") + ".txt")
        assert text.is_file() and text.stat().st_size > 100, f"no licence text for {row['name']}"


def test_the_versions_named_in_the_licences_doc_are_the_bundled_ones():
    doc = (REPO / "docs" / "LICENSES.md").read_text(encoding="utf-8")
    versions = {r["name"]: r["version"] for b in BUNDLES
                for r in json.loads((VENDOR / b / "THIRD-PARTY.json").read_text(encoding="utf-8"))}
    for name, label in (("@tiptap/core", "Tiptap"), ("yjs", "Yjs"), ("y-prosemirror", "y-prosemirror"),
                        ("mammoth", "mammoth"), ("markdown-it", "markdown-it")):
        assert f"{label} {versions[name]}" in doc, f"docs/LICENSES.md should name {label} {versions[name]}"


def test_one_yjs_for_the_page():
    tiptap = json.loads((VENDOR / "tiptap" / "THIRD-PARTY.json").read_text(encoding="utf-8"))
    assert "yjs" not in {r["name"] for r in tiptap}, "the editor bundle must borrow window.Y, not carry Yjs"
    assert "y-prosemirror" in {r["name"] for r in tiptap}
    yjs = {r["name"]: r["version"] for r in json.loads((VENDOR / "yjs" / "THIRD-PARTY.json").read_text(encoding="utf-8"))}
    assert yjs["yjs"] == "13.6.33"
    assert "window.Y" in (VENDOR / "tiptap" / "tiptap.min.js").read_text(encoding="utf-8")[:2_000_000]


def test_the_editor_loads_the_bundles_from_this_server():
    jsx = (REPO / "web" / "static" / "doc_editor.jsx").read_text(encoding="utf-8")
    for b, f in BUNDLES.items():
        assert f"/vendor/{b}/{f}" in jsx
    assert "http://" not in re.sub(r"//.*|/\*[\s\S]*?\*/", "", jsx.split("const VENDOR")[1].split("};")[0])
