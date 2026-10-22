"""Every font file Domovoi ships carries its licence text, and is credited.

The dashboard, the design folder and the drafts ship font files. Each one
is under the SIL Open Font License, which asks for the licence text to
travel with the font, so every directory holding a font holds an OFL text
naming that font, and docs/LICENSES.md names the font. A font with no
licence on record (the old Cash Currency display face) fails here. Pure
file reads, plus one temp directory for the Excalidraw fetch script.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LICENSES = REPO / "docs" / "LICENSES.md"
FONT_SUFFIXES = {".ttf", ".otf", ".woff", ".woff2"}

# Font file stem prefix -> (the name its licence text and LICENSES.md use,
# where its licence text lives relative to the font's own directory).
KNOWN = {
    "inter-": ("Inter", "."),
    "jetbrains-mono-": ("JetBrains Mono", "."),
    "PlayfairDisplaySC-": ("Playfair Display", "."),
    "Liberation": ("Liberation", "."),
    # Excalidraw's own fonts, in excalidraw-assets/, whose contents the
    # fetch script replaces; their texts sit one level up, next to its README.
    "Virgil": ("Virgil", ".."),
    "Cascadia": ("Cascadia Code", ".."),
    "Assistant-": ("Assistant", ".."),
}


def _shipped_fonts() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
         "web", "docs", "domovoi", "android/app/src"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return [REPO / p for p in out.split("\0")
            if p and Path(p).suffix.lower() in FONT_SUFFIXES and (REPO / p).is_file()]


def _known(font: Path) -> tuple[str, str]:
    for prefix, entry in KNOWN.items():
        if font.name.startswith(prefix):
            return entry
    raise AssertionError(
        f"{font.relative_to(REPO)}: a font with no licence on record. Ship only fonts whose "
        "licence allows it (SIL OFL is fine), with the licence text beside it, and add it here "
        "and to docs/LICENSES.md")


def test_there_are_fonts_to_check():
    names = {f.name for f in _shipped_fonts()}
    assert "inter-latin-wght-normal.woff2" in names
    assert "LiberationSans-Regular.ttf" in names


def test_every_shipped_font_has_its_ofl_text_beside_it():
    for font in _shipped_fonts():
        name, where = _known(font)
        folder = (font.parent / where).resolve()
        texts = [p for p in folder.iterdir()
                 if p.is_file() and (p.name.startswith("OFL") or p.name.startswith("LICENSE-OFL"))]
        assert texts, f"{font.relative_to(REPO)}: no OFL text in {folder.relative_to(REPO)}"
        hits = [p for p in texts if name in p.read_text(encoding="utf-8")
                and "SIL Open Font License" in p.read_text(encoding="utf-8")]
        assert hits, f"{font.relative_to(REPO)}: no OFL text in {folder.relative_to(REPO)} names {name}"


def test_every_shipped_font_is_credited_in_licenses_md():
    doc = LICENSES.read_text(encoding="utf-8")
    for font in _shipped_fonts():
        name, _ = _known(font)
        if name == "Playfair Display":
            name = "Playfair Display SC"
        assert name in doc, f"{name} ({font.relative_to(REPO)}) is not named in docs/LICENSES.md"


def test_cash_currency_is_gone():
    hits = subprocess.run(
        ["git", "grep", "-i", "-l", "-I", "cash.currency\\|cash_currency", "--", "web", "docs", "android", "windows"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.split()
    assert hits == [], f"Cash Currency (no licence on record) is still referenced: {hits}"


def test_the_excalidraw_refetch_keeps_the_font_licences(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "vendor_excalidraw", REPO / "scripts" / "vendor_excalidraw.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "README.md").write_text("note", encoding="utf-8")
    (tmp_path / "OFL-Virgil.txt").write_text("ofl", encoding="utf-8")
    (tmp_path / "excalidraw.production.min.js").write_text("js", encoding="utf-8")
    (tmp_path / "excalidraw-assets").mkdir()
    (tmp_path / "excalidraw-assets" / "Virgil.woff2").write_bytes(b"x")
    mod.clear_vendor_dir(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["OFL-Virgil.txt", "README.md"]
