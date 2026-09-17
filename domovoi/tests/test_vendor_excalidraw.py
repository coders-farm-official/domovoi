"""The vendored Excalidraw bundle must be committable — and committed.

The dashboard has no build step: every third-party library it loads is
served from web/static/vendor so the box makes zero external requests.
Excalidraw was the exception that nobody could see. drawings.jsx pointed at
``/vendor/excalidraw/dist/excalidraw.production.min.js``, and the packaging
section of .gitignore ignores ``dist/`` at ANY depth — so the bundle could
not be added to the repo even by someone who had fetched it. Every clone
404'd and the Drawings editor sat on "Loading Excalidraw…" forever with
nothing but a console error (finding F-003).

Two invariants, both of which the old layout broke:
  * the vendor path the page loads has no ``dist/`` segment, and
  * git does not ignore that path.

Pure file/`git check-ignore` checks — no DB, no `requires_db`, this must
never skip.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAWINGS_JSX = REPO_ROOT / "web" / "static" / "drawings.jsx"
VENDOR_SCRIPT = REPO_ROOT / "scripts" / "vendor_excalidraw.py"
UMD_FILENAME = "excalidraw.production.min.js"


def _jsx_const(name: str) -> str:
    """Read a top-level `const NAME = '...'` out of drawings.jsx."""
    src = DRAWINGS_JSX.read_text(encoding="utf-8")
    m = re.search(rf"^const {name} = '([^']*)';", src, re.MULTILINE)
    assert m, f"{name} not found in {DRAWINGS_JSX}"
    return m.group(1)


def _asset_base() -> str:
    base = _jsx_const("EXCALIDRAW_ASSET_BASE")
    assert base.startswith("/vendor/") and base.endswith("/"), base
    return base


def _repo_path_for(url_path: str) -> str:
    """'/vendor/excalidraw/x.js' -> 'web/static/vendor/excalidraw/x.js'."""
    return "web/static" + url_path


def _git_ignores(rel_path: str) -> bool | None:
    """True/False, or None when git cannot answer (no git, no .git dir).

    `git check-ignore -v` prints the last matching pattern even when that
    pattern is a negation, and its exit status does not distinguish the
    two — so read the pattern, not the exit code.
    """
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        return None
    proc = subprocess.run(
        ["git", "check-ignore", "-v", "--no-index", "--", rel_path],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode not in (0, 1):
        return None
    line = proc.stdout.strip()
    if not line:
        return False                       # no pattern matched at all
    pattern = line.split("\t")[0].rsplit(":", 1)[-1]
    return not pattern.startswith("!")


def test_vendor_path_has_no_dist_segment() -> None:
    """`dist/` is ignored at any depth by the packaging rules, so a vendored
    bundle must never live under one. This is the assertion that can run
    anywhere — no git required."""
    base = _asset_base()
    assert "/dist/" not in base, (
        f"EXCALIDRAW_ASSET_BASE={base!r} points under a dist/ directory, which "
        ".gitignore ignores at any depth — the bundle can never be committed"
    )


def test_git_does_not_ignore_the_vendored_bundle() -> None:
    """The real check: whatever path the page loads, git must accept it."""
    rel = _repo_path_for(_asset_base() + UMD_FILENAME)
    ignored = _git_ignores(rel)
    assert ignored is not True, (
        f"{rel} is gitignored — the Excalidraw bundle cannot reach a clone"
    )


def test_git_does_not_ignore_the_vendor_tree() -> None:
    """Belt and braces: the whole vendor tree is checked in on purpose."""
    for rel in (
        "web/static/vendor/excalidraw/README.md",
        "web/static/vendor/excalidraw/excalidraw-assets/Cascadia.woff2",
    ):
        assert _git_ignores(rel) is not True, f"{rel} is gitignored"


def test_vendor_directory_documents_how_to_populate_it() -> None:
    """The bundle itself is fetched, not carried in git history, so the
    directory must exist and say how to fill it."""
    base = _asset_base()
    readme = REPO_ROOT / _repo_path_for(base) / "README.md"
    assert readme.is_file(), f"missing {readme}"
    assert "scripts/vendor_excalidraw.py" in readme.read_text(encoding="utf-8")


def _vendor_script_module():
    spec = importlib.util.spec_from_file_location("vendor_excalidraw", VENDOR_SCRIPT)
    assert spec and spec.loader, f"cannot load {VENDOR_SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fetch_script_targets_the_path_the_page_loads() -> None:
    """A fetcher that writes somewhere else is worse than none."""
    mod = _vendor_script_module()
    assert mod.VENDOR_DIR == REPO_ROOT / _repo_path_for(_asset_base().rstrip("/"))
    assert mod.UMD_FILENAME == UMD_FILENAME


def test_fetch_script_pins_the_same_version_as_the_page() -> None:
    """0.17.6 is the last UMD release; 0.18+ is ESM-only and cannot load in
    a dashboard with no bundler."""
    mod = _vendor_script_module()
    assert mod.EXCALIDRAW_VERSION == _jsx_const("EXCALIDRAW_VERSION") == "0.17.6"
