"""``_safe_target`` must behave identically on POSIX and Windows.

The original guard relied on the JOIN escaping the base to reject an
absolute input. That only works where a drive letter survives the join
(Windows). On POSIX, ``lstrip("/")`` rewrites ``/etc/passwd`` into the
relative ``etc/passwd``, which lands inside DOCUMENTS_DIR and passes the
containment check — so identical input 400'd on one OS and was silently
reinterpreted on the other.

Containment was never broken; consistency was. These lock in both halves:
absolute/drive-qualified input is refused everywhere, and ordinary
relative paths still resolve.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from web.backend.api.documents import _safe_target


@pytest.fixture(autouse=True)
def docs_root(tmp_path, monkeypatch):
    import web.backend.api.documents as mod

    monkeypatch.setattr(mod, "_documents_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",                  # POSIX absolute
        "/tmp/somewhere_else/x.md",
        "C:/Windows/system32/x.md",     # Windows drive, forward slashes
        r"C:\Windows\system32\x.md",    # Windows drive, backslashes
        "c:x.md",                       # drive-relative
        "//server/share/x.md",          # UNC
        r"\\server\share\x.md",
    ],
)
def test_absolute_and_drive_qualified_are_rejected(bad):
    with pytest.raises(HTTPException) as ei:
        _safe_target(bad)
    assert ei.value.status_code == 400


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_is_rejected(bad):
    with pytest.raises(HTTPException) as ei:
        _safe_target(bad)
    assert ei.value.status_code == 400


def test_traversal_out_of_base_is_rejected():
    with pytest.raises(HTTPException) as ei:
        _safe_target("../../etc/passwd")
    assert ei.value.status_code == 400


@pytest.mark.parametrize(
    "good",
    [
        "notes.md",
        "sub/dir/notes.md",
        "sub/../notes.md",   # normalizes back inside the base
        "spaces in name.md",
    ],
)
def test_ordinary_relative_paths_still_resolve(good, docs_root):
    target = _safe_target(good)
    assert target.is_relative_to(docs_root), f"{target} escaped {docs_root}"
