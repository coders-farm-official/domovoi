"""Vendor the pinned Excalidraw UMD build into web/static/vendor/excalidraw.

The dashboard has no bundler (UMD + Babel-in-browser) and makes zero
external requests, so every third-party library it uses is committed under
web/static/vendor. Excalidraw is the one that never was: drawings.jsx
pointed at a path under a `dist/` directory, the packaging section of
.gitignore ignores `dist/` at any depth, and the bundle therefore could not
be committed — every clone 404'd and the Drawings editor never loaded
(finding F-003). The path no longer has a dist/ segment and the vendor tree
is explicitly un-ignored; this script is how the files get there.

Excalidraw dropped its UMD build at v0.18.0 (ESM-only), so the version is
pinned to the LAST UMD release, 0.17.6, and must stay in lockstep with
EXCALIDRAW_VERSION in web/static/drawings.jsx.

Usage:
    python scripts/vendor_excalidraw.py          # fetch if missing
    python scripts/vendor_excalidraw.py --force  # re-fetch over existing

Then commit web/static/vendor/excalidraw/ — a fetch is a one-time step for
the repo, not a build step for each box. Needs network access exactly once;
the box that runs Domovoi never does.
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Keep in lockstep with EXCALIDRAW_VERSION / EXCALIDRAW_ASSET_BASE in
# web/static/drawings.jsx (domovoi/tests/test_vendor_excalidraw.py asserts it).
EXCALIDRAW_VERSION = "0.17.6"
VENDOR_DIR = REPO_ROOT / "web" / "static" / "vendor" / "excalidraw"
UMD_FILENAME = "excalidraw.production.min.js"

TARBALL_URL = (
    "https://registry.npmjs.org/@excalidraw/excalidraw/-/"
    f"excalidraw-{EXCALIDRAW_VERSION}.tgz"
)
# Inside the npm tarball everything lives under package/dist/. The dev
# assets are a second full copy of the fonts for the development build —
# tens of megabytes the dashboard never requests.
_MEMBER_PREFIX = "package/dist/"
_SKIP_DIRS = ("excalidraw-assets-dev/",)


def _wanted(name: str) -> str | None:
    """Return the vendor-relative path for a tar member, or None to skip."""
    if not name.startswith(_MEMBER_PREFIX):
        return None
    rel = name[len(_MEMBER_PREFIX):]
    if not rel or rel.startswith(_SKIP_DIRS):
        return None
    # Refuse anything that would escape VENDOR_DIR (absolute, .., drive).
    parts = Path(rel).parts
    if any(p in ("..", "") or Path(p).is_absolute() for p in parts):
        return None
    return rel


def fetch(force: bool = False) -> int:
    umd = VENDOR_DIR / UMD_FILENAME
    if umd.is_file() and not force:
        print(f"already vendored: {umd} (use --force to re-fetch)")
        return 0

    print(f"downloading {TARBALL_URL}")
    with urllib.request.urlopen(TARBALL_URL, timeout=120) as resp:  # noqa: S310
        blob = resp.read()
    print(f"  {len(blob) / 1_048_576:.1f} MiB")

    if force and VENDOR_DIR.is_dir():
        for child in VENDOR_DIR.iterdir():
            if child.name == "README.md":
                continue          # the checked-in note about this directory
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    written = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            rel = _wanted(member.name)
            if rel is None:
                continue
            target = VENDOR_DIR / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            written += 1

    if not umd.is_file():
        print(f"ERROR: {UMD_FILENAME} not found in the tarball", file=sys.stderr)
        return 1
    print(f"wrote {written} files into {VENDOR_DIR}")
    print("now: git add web/static/vendor/excalidraw && git commit")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="re-fetch even if the bundle is already vendored")
    args = ap.parse_args()
    return fetch(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
