"""``domovoi plugin`` — the sanctioned local dev loop (design §3.8).

Subcommands:

* ``domovoi plugin new <slug>``   — scaffold manifest + package + entry
  stubs + tests/conftest.py, with the manifest and code generated from
  ONE answer set so the §2.2 duplication is machine-produced.
* ``domovoi plugin dev <path>``   — register the plugin IN PLACE
  (``install_source='dev'``, no copy/zip/staging): same manifest
  validation, but skips the pip dry-run/lockfile requirement, version
  monotonicity, and the trust screen. Code changes still need a core
  restart (§3.4); ``--watch`` polls for changes and prints the reminder.
* ``domovoi plugin pack <path>``  — validate (lockfile check included)
  and produce the installable §2.1-layout zip.

Console script wired in pyproject: ``domovoi = domovoi.plugins_runtime.cli:main``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import zipfile
from pathlib import Path

from domovoi.plugins_runtime.manifest import (
    ManifestError,
    PluginManifest,
    check_web_import_hygiene,
    parse_manifest,
    validate_plugin_dir,
)
from domovoi.plugins_runtime.migrations import PluginMigrationRunner

_SCAFFOLD_MANIFEST = '''[plugin]
slug = "{slug}"
name = "{title}"
version = "0.1.0"
publisher = "you"
license = "MIT"
description = "Describe what {title} does — shown on the install preview."
domovoi_api = ">=1.0,<2.0"

[entry_points]
core = "domovoi_plugin_{slug}.core"

[[handlers]]
name = "{slug}"
band = 400
requires_network = "no"
label = "{title}"
tone = "info"
corpus = ["{slug} example phrase"]

[permissions]
warnings = []
'''

_SCAFFOLD_CORE = '''"""Core entry point for the {slug} plugin."""

import re

from domovoi.sdk import FastPath, Handler, HandlerDisplay, Response

_EXAMPLE_RE = re.compile(r"^{slug} example phrase$")


class {cls}Handler(Handler):
    name = "{slug}"
    priority_band = 400                       # general plugin space (§4.2)
    display = HandlerDisplay(label="{title}", tone="info")
    requires_network = "no"
    tool_schema = {{
        "name": "{slug}",
        "description": "Run the {slug} action. Example: '{slug} example phrase'.",
        "parameters": {{"type": "object", "properties": {{}}, "required": []}},
    }}

    def __init__(self) -> None:
        self.fast_paths = [FastPath(_EXAMPLE_RE, {cls}Handler._example)]

    async def _example(self, m, ctx, session) -> Response:
        return Response(text="Hello from {slug}!")

    async def execute(self, intent, ctx, session) -> Response:
        return Response(text="Hello from {slug}!")


def register(ctx):
    ctx.add_handler({cls}Handler())
'''

_SCAFFOLD_CONFTEST = '''import sys
from pathlib import Path

import pytest

from domovoi.sdk.testing import make_stub_sdk

# Make domovoi_plugin_{slug} importable when tests run from this dir.
sys.path.insert(0, str(Path(__file__).parents[1]))


@pytest.fixture
def stub_sdk():
    """A fully stubbed PluginSDK (design §13.1): in-memory event bus,
    recording playback/acquisition/sessions doubles, togglable
    connectivity — handler and worker tick() tests need no DB, no core."""
    return make_stub_sdk("{slug}")
'''


def _cmd_new(args: argparse.Namespace) -> int:
    slug = args.slug
    from domovoi.plugins_runtime.manifest import SLUG_RE, RESERVED_SLUGS

    if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
        print(f"error: {slug!r} is not a legal plugin slug", file=sys.stderr)
        return 2
    root = Path(args.dir or ".").resolve() / slug
    if root.exists():
        print(f"error: {root} already exists", file=sys.stderr)
        return 2
    title = slug.replace("_", " ").title()
    cls = title.replace(" ", "")
    pkg = root / f"domovoi_plugin_{slug}"
    (pkg).mkdir(parents=True)
    (root / "domovoi-plugin.toml").write_text(
        _SCAFFOLD_MANIFEST.format(slug=slug, title=title), encoding="utf-8"
    )
    (root / "README.md").write_text(f"# {title}\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        _SCAFFOLD_CORE.format(slug=slug, title=title, cls=cls), encoding="utf-8"
    )
    (root / "migrations").mkdir()
    tests = root / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text(
        _SCAFFOLD_CONFTEST.format(slug=slug), encoding="utf-8"
    )
    print(f"scaffolded plugin {slug!r} at {root}")
    print(f"next: domovoi plugin dev {root}")
    return 0


def _validate_dir(path: Path) -> PluginManifest:
    manifest_path = path / "domovoi-plugin.toml"
    if not manifest_path.is_file():
        raise ManifestError(f"no domovoi-plugin.toml at {path}")
    manifest = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    errors = validate_plugin_dir(path, manifest)
    errors += check_web_import_hygiene(path, manifest)
    if errors:
        raise ManifestError("; ".join(errors))
    return manifest


async def _dev_register(path: Path, manifest: PluginManifest) -> None:
    from domovoi.plugins_runtime import registry as reg

    runner = PluginMigrationRunner(
        manifest.slug, path / manifest.migrations_dir
    )
    await runner.apply_all()
    existing = await reg.get_plugin(manifest.slug)
    if existing is None:
        await reg.insert_plugin(
            slug=manifest.slug, name=manifest.name, version=manifest.version,
            publisher=manifest.publisher, license=manifest.license,
            domovoi_api=manifest.domovoi_api, enabled=True, bundled=False,
            install_source="dev", source_ref=None,
            install_dir=str(path), manifest=manifest.raw,
        )
    elif existing.install_source == "dev":
        await reg.update_plugin(
            manifest.slug, version=manifest.version, enabled=True,
            install_dir=str(path), manifest=manifest.raw, status="ok",
            last_error=None,
        )
    else:
        raise ManifestError(
            f"plugin {manifest.slug!r} is installed via "
            f"{existing.install_source!r} — uninstall it before registering "
            f"a dev copy"
        )


def _tree_mtime(path: Path) -> float:
    latest = 0.0
    for f in path.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            latest = max(latest, f.stat().st_mtime)
    return latest


def _cmd_dev(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()
    try:
        manifest = _validate_dir(path)
        asyncio.run(_dev_register(path, manifest))
    except ManifestError as e:
        print(f"validation failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — surface DB trouble plainly
        print(f"dev registration failed: {e}", file=sys.stderr)
        return 1
    print(
        f"registered {manifest.slug!r} v{manifest.version} in place "
        f"(install_source=dev).\n"
        f"NOTE: the core loads plugin code at startup — (re)start the core "
        f"now; code changes always need a core restart (design §3.4/§3.8)."
    )
    if not args.watch:
        return 0
    print("watching for changes (Ctrl-C to stop) …")
    last = _tree_mtime(path)
    try:
        while True:
            time.sleep(2)
            now = _tree_mtime(path)
            if now > last:
                last = now
                try:
                    _validate_dir(path)
                    print(
                        "change detected — validation OK; restart the core "
                        "to load the new code"
                    )
                except ManifestError as e:
                    print(f"change detected — validation FAILED: {e}")
    except KeyboardInterrupt:
        return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    path = Path(args.path).resolve()
    try:
        manifest = _validate_dir(path)
    except ManifestError as e:
        print(f"validation failed: {e}", file=sys.stderr)
        return 1
    out = Path(
        args.output or f"{manifest.slug}-{manifest.version}.zip"
    ).resolve()
    # Never stage dev/build detritus into the installable zip. An in-tree
    # virtualenv (thousands of entries with deep paths) both bloats the
    # package and trips Windows' MAX_PATH during staging (WinError 206).
    skip_dirs = {
        "__pycache__", ".git", ".pytest_cache", ".mypy_cache",
        ".venv", "venv", "env", ".tox", "dist", "build", "node_modules",
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(path.rglob("*")):
            if f.is_dir():
                continue
            rel = f.relative_to(path)
            if any(part in skip_dirs for part in rel.parts):
                continue
            zf.write(f, rel.as_posix())
    # Plain ASCII output — Windows consoles default to cp1252 (host is
    # Windows-first; an arrow glyph here crashes the command).
    print(f"packed {manifest.slug} v{manifest.version} -> {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="domovoi", description="Domovoi developer CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    plugin = sub.add_parser("plugin", help="plugin dev tooling (design §3.8)")
    psub = plugin.add_subparsers(dest="plugin_command", required=True)

    p_new = psub.add_parser("new", help="scaffold a new plugin")
    p_new.add_argument("slug")
    p_new.add_argument("--dir", help="parent directory (default: cwd)")
    p_new.set_defaults(fn=_cmd_new)

    p_dev = psub.add_parser("dev", help="register a plugin dir in place")
    p_dev.add_argument("path")
    p_dev.add_argument(
        "--watch", action="store_true",
        help="poll for file changes and re-validate (restart reminder)",
    )
    p_dev.set_defaults(fn=_cmd_dev)

    p_pack = psub.add_parser("pack", help="produce the installable zip")
    p_pack.add_argument("path")
    p_pack.add_argument("-o", "--output")
    p_pack.set_defaults(fn=_cmd_pack)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
