"""Do the first-party plugins' locks agree with the core's?

The plugin installer refuses a plugin whose lock pins a package at a
different version than the one the core has installed
(``requirements_conflict``). A household that meets it can only ask the
publisher for a new build, so a release checks this first.

Give it plugin zips (as ``domovoi plugin pack`` builds them) or plain lock
files. For every package pinned in a plugin lock AND in the core's
``requirements-linux-py314.lock`` or ``requirements.lock``, the versions
must be equal. Exit status 0 when they all are, 1 with a table of the
differences when not, 2 for a file it can't read::

    python scripts/release/check_plugin_locks.py media-plugin-1.2.0.zip other-1.0.0.zip
    python scripts/release/check_plugin_locks.py plugin/requirements.lock

``--core-lock PATH`` (repeatable) replaces the two default core locks.
Standard library only.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE_LOCKS = ("requirements-linux-py314.lock", "requirements.lock")

_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;\\]+)")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(text: str) -> dict[str, tuple[str, str]]:
    """{normalized name: (name as written, version)} for each ``name==version``."""
    pins: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        m = _PIN.match(line)
        if m:
            pins[normalize(m.group(1))] = (m.group(1), m.group(2))
    return pins


def lock_from_zip(path: Path) -> str:
    """The text of the lockfile the plugin's manifest names (default
    ``requirements.lock``), found at the zip root or one folder down."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        manifests = [n for n in names if n.count("/") <= 1 and n.endswith("domovoi-plugin.toml")]
        if not manifests:
            raise ValueError("no domovoi-plugin.toml")
        manifest = min(manifests, key=len)
        base = manifest[: -len("domovoi-plugin.toml")]
        data = tomllib.loads(z.read(manifest).decode("utf-8"))
        reqs = data.get("requirements") or {}
        lock_name = reqs.get("lockfile") or "requirements.lock"
        if lock_name not in {n[len(base):] for n in names if n.startswith(base)}:
            if not reqs.get("python"):
                return ""      # a plugin with no Python requirements has no lock
            raise ValueError(f"lockfile {lock_name!r} not in the zip")
        return z.read(base + lock_name).decode("utf-8")


def load_plugin_lock(path: Path) -> str:
    if path.suffix.lower() == ".zip":
        return lock_from_zip(path)
    return path.read_text(encoding="utf-8")


def mismatches(
    plugin_pins: dict[str, tuple[str, str]],
    core: dict[str, dict[str, tuple[str, str]]],
) -> list[tuple[str, str, str, str]]:
    """(package, plugin version, core lock name, core version) per difference."""
    rows = []
    for key, (name, version) in sorted(plugin_pins.items()):
        for lock_name, pins in core.items():
            if key in pins and pins[key][1] != version:
                rows.append((name, version, lock_name, pins[key][1]))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("plugins", nargs="+", type=Path, help="plugin .zip or lock file")
    ap.add_argument("--core-lock", action="append", type=Path,
                    help="a core lock to compare with (default: the two in the repo root)")
    args = ap.parse_args(argv)

    core_paths = args.core_lock or [ROOT / n for n in CORE_LOCKS]
    core: dict[str, dict[str, tuple[str, str]]] = {}
    try:
        for p in core_paths:
            core[p.name] = parse_pins(p.read_text(encoding="utf-8"))
    except OSError as e:
        print(f"cannot read core lock: {e}", file=sys.stderr)
        return 2

    failed = False
    for plugin in args.plugins:
        try:
            pins = parse_pins(load_plugin_lock(plugin))
        except (OSError, ValueError, zipfile.BadZipFile, tomllib.TOMLDecodeError) as e:
            print(f"{plugin}: cannot read its lock: {e}", file=sys.stderr)
            return 2
        rows = mismatches(pins, core)
        if not rows:
            print(f"ok    {plugin}")
            continue
        failed = True
        print(f"FAIL  {plugin}")
        header = ("package", "plugin lock", "core lock file", "core pins")
        table = [header, *rows]
        widths = [max(len(r[i]) for r in table) for i in range(4)]
        for r in table:
            print("      " + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    if failed:
        print("\nRecompile the plugin's lock against this release: "
              "pip-compile -c requirements-linux-py314.lock ... (docs/PLUGIN_DEVELOPMENT.md)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
