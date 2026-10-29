"""Bundle the pinned open-source Univer sheet editor into web/static/vendor/univer.

The dashboard has no build step: every third-party library is committed
under web/static/vendor as plain browser files and served by the box
itself. Univer 1.x ships only ES modules / CJS (no UMD), so this script
bundles it ONCE with esbuild and the result is committed. A Domovoi box
never runs this; it needs Node/npm and network access exactly once, on a
developer machine.

Pins: package.json + package-lock.json beside this file (exact versions,
sha512 integrity). Only Apache-2.0 @univerjs packages are listed: no
@univerjs-pro, no preset-sheets-advanced. The script refuses to finish if
anything that ends up in the bundle is not under a permissive licence.

Output (web/static/vendor/univer/):
  univer.js                 ES module entry; sets window.DomovoiUniverLib
  chunks/*.js               shared chunks esbuild splits out, if any (the
                            hyphenation patterns are left out on purpose:
                            build.mjs)
  univer.css                the presets' stylesheets
  LICENSE                   Univer's Apache-2.0 licence text
  THIRD-PARTY-NOTICES.txt   every bundled package, version, licence, text

Usage:
    python scripts/vendor/univer/vendor_univer.py
    git add web/static/vendor/univer && git commit
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
VENDOR_DIR = REPO_ROOT / "web" / "static" / "vendor" / "univer"
UNIVER_VERSION = "1.2.0"  # keep in lockstep with package.json and entry.js
CSS_PACKAGES = (
    "@univerjs/preset-sheets-core",
    "@univerjs/preset-sheets-sort",
    "@univerjs/preset-sheets-filter",
)
PERMISSIVE = {"MIT", "ISC", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD"}


def _run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, shell=(sys.platform == "win32"))


def _package_of(input_path: str) -> str | None:
    parts = input_path.replace("\\", "/").split("node_modules/")
    if len(parts) < 2:
        return None
    seg = parts[-1].split("/")
    return "/".join(seg[:2]) if seg[0].startswith("@") else seg[0]


def _licence_text(pkg_dir: Path) -> str:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "license", "LICENCE"):
        p = pkg_dir / name
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace").strip()
    return ""


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="domovoi-univer-"))
    try:
        for name in ("package.json", "package-lock.json", "entry.js", "safe_image.js", "build.mjs"):
            shutil.copy2(HERE / name, work / name)
        _run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], work)
        out = work / "out"
        # build.mjs: esbuild, plus emptying engine-render's hyphenation-pattern
        # loaders, so none of those patterns (mixed licences) is bundled.
        _run(["node", "build.mjs", str(out), str(work / "meta.json")], work)
        meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
        packages: set[str] = set()
        for output in meta["outputs"].values():
            for inp in output.get("inputs", {}):
                pkg = _package_of(inp)
                if pkg:
                    packages.add(pkg)

        notices: list[str] = [
            "Third-party code bundled into web/static/vendor/univer/ by "
            "scripts/vendor/univer/vendor_univer.py. Univer's hyphenation patterns "
            "(@univerjs/engine-render, from TeX hyph-utf8) are deliberately NOT included: "
            "scripts/vendor/univer/build.mjs empties their loader table.\n"
        ]
        bad: list[str] = []
        for pkg in sorted(packages):
            pkg_dir = work / "node_modules" / pkg
            pj = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
            lic = pj.get("license") or ""
            if lic not in PERMISSIVE:
                bad.append(f"{pkg}@{pj.get('version')}: {lic or 'no licence'}")
            notices.append("=" * 72)
            notices.append(f"{pkg} {pj.get('version')}  ({lic})")
            notices.append("=" * 72)
            notices.append(_licence_text(pkg_dir) or f"Licensed under {lic}.")
            notices.append("")
        if bad:
            print("Refusing: not permissive:\n  " + "\n  ".join(bad), file=sys.stderr)
            return 1

        if VENDOR_DIR.exists():
            shutil.rmtree(VENDOR_DIR)
        VENDOR_DIR.mkdir(parents=True)
        shutil.copytree(out, VENDOR_DIR, dirs_exist_ok=True)
        css = []
        for pkg in CSS_PACKAGES:
            css.append(f"/* {pkg} {UNIVER_VERSION} (Apache-2.0) */\n")
            css.append((work / "node_modules" / pkg / "lib" / "index.css").read_text(encoding="utf-8"))
        (VENDOR_DIR / "univer.css").write_bytes("".join(css).encode("utf-8"))
        shutil.copy2(work / "node_modules" / "@univerjs" / "core" / "LICENSE", VENDOR_DIR / "LICENSE")
        (VENDOR_DIR / "THIRD-PARTY-NOTICES.txt").write_bytes("\n".join(notices).encode("utf-8"))
        total = sum(p.stat().st_size for p in VENDOR_DIR.rglob("*") if p.is_file())
        print(f"wrote {VENDOR_DIR} ({total / 1e6:.1f} MB, {len(packages)} packages)")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
