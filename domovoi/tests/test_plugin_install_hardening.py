"""Plugin install hardening riders (PLG-5): reserved slugs that would
shadow a core route, zip entry names Windows would rewrite or that name
an NTFS stream, and the parsed (not substring-matched) direct-dependency
check. DB-free by construction.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from domovoi.plugins_runtime.installer import InstallError, validate_zip_safety
from domovoi.plugins_runtime.lockfile import parse_lockfile
from domovoi.plugins_runtime.manifest import (
    RESERVED_SLUGS,
    ManifestError,
    parse_manifest,
)

MANIFEST = """
[plugin]
slug = "{slug}"
name = "Reserved"
version = "1.0.0"
publisher = "Coders Farm"
license = "MIT"
description = "reserved slug probe"
domovoi_api = ">=1.0,<2.0"

[entry_points]
core = "domovoi_plugin_{slug}.core"
"""


# ─── reserved slugs ───────────────────────────────────────────────────────


@pytest.mark.parametrize("slug", ["install", "manifest", "status", "static", "api"])
def test_slugs_that_are_core_route_segments_are_reserved(slug: str) -> None:
    assert slug in RESERVED_SLUGS
    with pytest.raises(ManifestError, match="reserved"):
        parse_manifest(MANIFEST.format(slug=slug))


@pytest.mark.parametrize("slug", ["core", "domovoi", "admin", "test", "public"])
def test_the_original_reserved_slugs_still_are(slug: str) -> None:
    with pytest.raises(ManifestError, match="reserved"):
        parse_manifest(MANIFEST.format(slug=slug))


def test_an_ordinary_slug_still_parses() -> None:
    assert parse_manifest(MANIFEST.format(slug="stations")).slug == "stations"


# ─── zip entry names ──────────────────────────────────────────────────────


def _zip_with(*names: str) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")
    buf.seek(0)
    return zipfile.ZipFile(buf)


@pytest.mark.parametrize(
    "name",
    [
        "pkg/notes.txt:hidden",          # NTFS alternate data stream
        "pkg/a:b",
        "C:evil.txt",                     # drive-relative (still refused)
        "pkg/trailing./core.py",          # component ending with a dot
        "pkg/trailing /core.py",          # component ending with a space
        "pkg/README.",                    # file ending with a dot
        "pkg/README ",                    # file ending with a space
        "dir./",                          # directory entry ending with a dot
    ],
)
def test_zip_entries_windows_would_rewrite_or_stream_are_refused(name: str) -> None:
    with pytest.raises(InstallError) as exc:
        validate_zip_safety(_zip_with("domovoi-plugin.toml", name))
    assert exc.value.code == "zip_bad_path"


@pytest.mark.parametrize(
    "name",
    [
        "pkg/core.py",
        "pkg/.hidden",
        "pkg/data.tar.gz",
        "pkg/sub dir/file name.txt",      # inner spaces are fine
        "pkg/v1.0.0/x",
    ],
)
def test_ordinary_entry_names_pass(name: str) -> None:
    validate_zip_safety(_zip_with("domovoi-plugin.toml", name))


# ─── lockfile lines are parsed, not substring-matched ─────────────────────


def test_lockfile_lines_are_parsed_into_pins() -> None:
    h = "--hash=sha256:" + "0" * 64
    reqs = parse_lockfile(f"xfoo==1.0 {h}\nfoo-bar==2.0 {h}\n")
    assert [(r.key, r.version) for r in reqs] == [("xfoo", "1.0"), ("foo-bar", "2.0")]
    # 'foo==1.0' is a substring of the first line but no parsed pin says so.
    assert ("foo", "1.0") not in {(r.key, r.version) for r in reqs}
