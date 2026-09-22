"""F-026 — a plugin page on a core route is refused, with a message that
names the route, and never gets a nav item that opens the wrong page.

The dashboard shell resolves a route core-first, so a plugin declaring
``web.pages[].route = "videos"`` loaded fine, got a sidebar entry ("W1b
Videos") and clicking it opened the core Videos page — no toast, no
server error, nothing in DomovoiPluginErrors (finding F-026, card
PLG-07). Now:

* the core refuses it at install / enable / boot — §13.2 contract check 7
  (``check_web_routes``): a hash-slug route that collides with neither a
  core route, another page of the same plugin nor an enabled plugin's
  page. The failure names the route and the core page; ``enable``
  answers ``{enabled: false, status: "load_error", error}`` and the
  Plugins page shows that instead of "enabled";
* the web host drops such a page from the frontend manifest and carries
  the same message as ``page_errors`` (manifest + /api/plugins), so a
  row written before the check existed cannot put a lying nav item on
  screen.

The core's reserved set (``CORE_WEB_ROUTES``) is pinned here to
index.html's ``window.DomovoiCore.pages``, the sidebar table and
plugin_host's ``CORE_NAV`` so the three cannot drift apart.

DB-free throughout (never ``requires_db``, never skips): the loader is
driven with ``foreign_corpus=[] / foreign_web_routes=[]`` and
``update_registry_status=False`` like the other contract tests; the
host is a fresh ``PluginHost`` with rows set by hand; the page half
needs ``node`` and fails, not skips, without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from domovoi import bootstrap
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.plugins_runtime import contracts
from domovoi.plugins_runtime.contracts import ContractError, ContractReport, check_web_routes
from domovoi.plugins_runtime.loader import LOADER
from domovoi.plugins_runtime.manifest import CORE_WEB_ROUTES, WEB_ROUTE_RE, parse_manifest
from web.backend import plugin_host

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "web" / "static"
HARNESS = Path(__file__).with_name("jsx_interact_harness.js")


def _manifest(slug: str, pages: list[tuple[str, str]]) -> str:
    page_blocks = "".join(
        f'\n[[web.pages]]\nroute = "{route}"\npage = "{page}"\nnav_label = "{page}"\n'
        for route, page in pages
    )
    return textwrap.dedent(
        f"""
        [plugin]
        slug = "{slug}"
        name = "{slug}"
        version = "1.0.0"
        publisher = "tests"
        license = "MIT"
        description = "generated test plugin"
        domovoi_api = ">=1.0,<2.0"

        [entry_points]
        core = "domovoi_plugin_{slug}.core"

        [web]
        scripts = ["web/static/{slug}.jsx"]
        """
    ) + page_blocks


def _routes_report(slug: str, pages: list[tuple[str, str]], foreign=()) -> ContractReport:
    report = ContractReport()
    check_web_routes(slug, parse_manifest(_manifest(slug, pages)), list(foreign), report)
    return report


# ── check 7 on its own ───────────────────────────────────────────────

def test_a_core_route_is_refused_naming_route_and_core_page():
    report = _routes_report("w1b", [("videos", "W1bVideosPluginPage")])
    assert len(report.errors) == 1
    msg = report.errors[0]
    assert "'videos'" in msg and "#videos" in msg and "W1bVideosPluginPage" in msg
    assert "core videos page" in msg
    assert "pick another route" in msg


@pytest.mark.parametrize("route", sorted(CORE_WEB_ROUTES))
def test_every_core_route_is_refused(route):
    assert _routes_report("w1b", [(route, "P")]).errors, route


def test_a_free_route_passes():
    assert _routes_report("radio", [("stations", "RadioPage")]).ok()


@pytest.mark.parametrize("route", ["My Page", "#x", "Videos", "a/b", "x" * 65])
def test_a_route_that_is_not_a_hash_slug_is_refused(route):
    # (an empty route is already a manifest parse error, not a contract failure)
    report = _routes_report("w1b", [(route, "P")])
    assert report.errors and "not a valid route" in report.errors[0]
    assert not WEB_ROUTE_RE.match(route)


def test_two_pages_on_one_route_in_the_same_plugin_are_refused():
    report = _routes_report("w1b", [("w1b", "One"), ("w1b", "Two")])
    assert any("'One'" in e and "'Two'" in e and "'w1b'" in e for e in report.errors)


def test_another_enabled_plugins_route_is_refused_but_own_rows_are_not():
    report = _routes_report("w1b", [("stations", "P")], foreign=[("stations", "radio")])
    assert any("'radio'" in e and "'stations'" in e for e in report.errors)
    # the registry may still list the candidate's previous version
    assert _routes_report("w1b", [("w1b", "P")], foreign=[("w1b", "w1b")]).ok()


# ── the loader refuses the load (install step 13 / enable / boot) ──────

@pytest.fixture(autouse=True)
def _dlls_registered():
    bootstrap.register_nvidia_dlls()
    yield


def _write_plugin(parent: Path, slug: str, route: str) -> Path:
    root = parent / slug
    pkg = root / f"domovoi_plugin_{slug}"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    manifest = _manifest(slug, [(route, f"{slug.capitalize()}Page")]).replace(
        "[entry_points]",
        f'[[handlers]]\nname = "{slug}"\nband = 400\nrequires_network = "no"\nlabel = "{slug}"\n\n[entry_points]',
    )
    (root / "domovoi-plugin.toml").write_text(manifest, encoding="utf-8")
    (pkg / "core.py").write_text(
        textwrap.dedent(
            f"""
            import re

            from domovoi.sdk import FastPath, Handler, HandlerDisplay, Response


            class TestHandler(Handler):
                name = "{slug}"
                priority_band = 400
                display = HandlerDisplay(label="{slug}")
                requires_network = "no"
                tool_schema = {{
                    "name": "{slug}",
                    "description": "test",
                    "parameters": {{"type": "object", "properties": {{}},
                                    "required": []}},
                }}

                def __init__(self):
                    self.fast_paths = [
                        FastPath(re.compile(r"^{slug} ping$"), TestHandler._go)
                    ]

                async def _go(self, m, ctx, session) -> Response:
                    return Response(text="ok")

                async def execute(self, intent, ctx, session) -> Response:
                    return Response(text="ok")


            def register(ctx):
                ctx.add_handler(TestHandler())
            """
        ),
        encoding="utf-8",
    )
    return root


async def _load(root: Path, slug: str, foreign_routes=()):
    manifest = parse_manifest((root / "domovoi-plugin.toml").read_text(encoding="utf-8"))
    return await LOADER.load_plugin(
        slug=slug, install_dir=root, manifest=manifest,
        foreign_corpus=[], foreign_web_routes=list(foreign_routes),
        update_registry_status=False,
    )


@pytest.mark.asyncio
async def test_loader_refuses_a_plugin_page_on_a_core_route(tmp_path: Path) -> None:
    root = _write_plugin(tmp_path, "routeclash", "videos")
    with pytest.raises(ContractError) as exc:
        await _load(root, "routeclash")
    joined = " ".join(exc.value.errors)
    assert "'videos'" in joined and "#videos" in joined and "RouteclashPage" in joined
    assert "routeclash" not in HANDLER_BY_NAME        # torn back down
    assert "routeclash" in str(exc.value)             # the enable/confirm `error` string


@pytest.mark.asyncio
async def test_loader_refuses_a_route_another_enabled_plugin_uses(tmp_path: Path) -> None:
    root = _write_plugin(tmp_path, "latepage", "stations")
    with pytest.raises(ContractError) as exc:
        await _load(root, "latepage", foreign_routes=[("stations", "radio")])
    assert any("'radio'" in e for e in exc.value.errors)
    assert "latepage" not in HANDLER_BY_NAME


@pytest.mark.asyncio
async def test_loader_accepts_a_free_route(tmp_path: Path) -> None:
    root = _write_plugin(tmp_path, "routeok", "routeok")
    await _load(root, "routeok")
    try:
        assert "routeok" in HANDLER_BY_NAME
    finally:
        await LOADER.unload_plugin("routeok")


# ── the reserved set cannot drift from the shell ─────────────────────

def test_reserved_routes_match_the_shell_and_the_web_host():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    block = re.search(r"window\.DomovoiCore\.pages = \{(.*?)\};", html, re.S).group(1)
    shell_routes = set(re.findall(r"^\s*([a-z]+):\s*\(\)", block, re.M))
    assert shell_routes == set(CORE_WEB_ROUTES)
    assert set(plugin_host.CORE_ROUTES) == set(CORE_WEB_ROUTES)
    assert set(plugin_host.CORE_NAV) | {"manual"} == set(CORE_WEB_ROUTES)
    components = (STATIC / "components.jsx").read_text(encoding="utf-8")
    nav_block = re.search(r"const CORE_NAV_ITEMS = \[(.*?)\];", components, re.S).group(1)
    assert set(re.findall(r"route: '([a-z]+)'", nav_block)) <= set(CORE_WEB_ROUTES)


def test_core_and_web_host_fail_with_the_same_words():
    assert (contracts.web_route_collision_message("videos", "W1bVideosPluginPage")
            == plugin_host.web_route_collision_message("videos", "W1bVideosPluginPage"))


# ── the web host never serves the lying nav entry ────────────────────

def _row(slug: str, pages: list[dict]) -> dict:
    return {
        "slug": slug, "name": slug, "version": "1.0.0", "publisher": "t", "license": "MIT",
        "enabled": True, "bundled": False, "install_source": "zip", "source_ref": None,
        "install_dir": f"/nowhere/{slug}", "status": "ok", "last_error": None,
        "installed_at": None, "updated_at": None,
        "manifest": {"web": {"scripts": [f"web/static/{slug}.jsx"], "pages": pages}},
    }


def test_frontend_manifest_drops_the_colliding_page_and_says_why():
    host = plugin_host.PluginHost()
    host.rows = {"w1b": _row("w1b", [
        {"route": "videos", "page": "W1bVideosPluginPage", "nav_label": "W1b Videos"},
        {"route": "w1b", "page": "W1bPage", "nav_label": "W1b"},
    ])}
    (entry,) = host.frontend_manifest()["plugins"]
    assert [pg["route"] for pg in entry["pages"]] == ["w1b"]
    assert entry["page_errors"] == [plugin_host.web_route_collision_message("videos", "W1bVideosPluginPage")]
    assert "core videos page" in entry["page_errors"][0]


def test_frontend_manifest_is_untouched_for_a_clean_plugin():
    host = plugin_host.PluginHost()
    host.rows = {"radio": _row("radio", [{"route": "stations", "page": "RadioPage", "nav_label": "Radio"}])}
    (entry,) = host.frontend_manifest()["plugins"]
    assert [pg["route"] for pg in entry["pages"]] == ["stations"]
    assert entry["page_errors"] == []


@pytest.mark.asyncio
async def test_api_plugins_listing_carries_the_page_errors(monkeypatch):
    from web.backend.api import plugins as plugins_api

    monkeypatch.setattr(plugins_api.HOST, "rows", {"w1b": _row("w1b", [
        {"route": "videos", "page": "W1bVideosPluginPage", "nav_label": "W1b Videos"}])})
    (entry,) = (await plugins_api.list_installed())["plugins"]
    assert entry["page_errors"] and "'videos'" in entry["page_errors"][0]


# ── the Plugins page shows the refusal instead of "enabled" ──────────

ROW = {"slug": "w1b", "name": "W1b", "version": "1.0.0", "publisher": "t", "license": "MIT",
       "enabled": False, "bundled": False, "install_source": "zip", "status": "load_error",
       "last_error": None, "permissions": {}, "provides": [], "consumes": [], "handlers": [],
       "pages": [], "android_capabilities": [], "web_load_error": None,
       "page_errors": ["web page 'W1bVideosPluginPage' uses route 'videos', which is the dashboard's core videos page (#videos)"]}
REFUSAL = {"enabled": False, "slug": "w1b", "status": "load_error",
           "error": "plugin 'w1b' failed 1 contract check(s): web page 'W1bVideosPluginPage' uses route 'videos'"}

SCENARIOS = {
    "enable_refused": {
        "files": ["web/static/components.jsx", "web/static/plugins.jsx"], "component": "PluginsPage",
        "api": {"GET /api/plugins": {"plugins": [ROW]}, "POST /api/plugins/w1b/enable": REFUSAL},
        "script": ("h.render(); const before = h.text(); await h.click({ type: 'button', text: 'enable' });"
                   " return { before, after: h.text(), calls: h.calls.map((c) => `${c.method} ${c.path}`) };"),
    },
    "enable_ok": {
        "files": ["web/static/components.jsx", "web/static/plugins.jsx"], "component": "PluginsPage",
        "api": {"GET /api/plugins": {"plugins": [{**ROW, "status": "ok", "page_errors": []}]},
                "POST /api/plugins/w1b/enable": {"enabled": True, "slug": "w1b"}},
        "script": "h.render(); await h.click({ type: 'button', text: 'enable' }); return { after: h.text() };",
    },
}


@pytest.fixture(scope="module")
def driven() -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX (see jsxcheck)"
    proc = subprocess.run(
        [node, str(HARNESS), str(REPO_ROOT), json.dumps(SCENARIOS)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if "__harness_error" in v}
    assert not broken, broken
    return out


def test_plugins_page_lists_the_page_error_on_the_row(driven):
    assert any(t.startswith("web page: ") and "'videos'" in t for t in driven["enable_refused"]["before"])


def test_refused_enable_toasts_the_reason_not_enabled(driven):
    r = driven["enable_refused"]
    assert r["calls"] == ["POST /api/plugins/w1b/enable"]
    assert any(t.startswith("enable failed: ") and "'videos'" in t for t in r["after"]), r["after"]
    assert "enabled W1b" not in r["after"]


def test_successful_enable_still_says_enabled(driven):
    assert "enabled W1b" in driven["enable_ok"]["after"]
