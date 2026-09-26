"""The install preview (design §3.2 step 8 / §7.5 — PLG-1): the satellite
root payload as its own section, and the plugin's ``@open_endpoint`` and
``@device_endpoint`` routes found by the subprocess AST scan — each tier
in its own list, and a route carrying both refused.

DB-free: ``stage_zip`` reaches the registry only at step 6, which is
faked here (no existing row, no orphan schema); the preview itself is
pure file inspection, and the pip subprocess is never needed because
these fixtures declare no core Python requirements.
"""

from __future__ import annotations

import io
import subprocess
import textwrap
import zipfile
from pathlib import Path

import pytest

from domovoi.plugins_runtime import installer
from domovoi.plugins_runtime import registry as reg
from domovoi.plugins_runtime.installer import InstallError, stage_zip
from domovoi.plugins_runtime.open_endpoints import (
    OpenEndpointScanError,
    collect_marked_endpoints,
    collect_open_endpoints,
    scan_device_endpoints,
    scan_marked_endpoints,
    scan_open_endpoints,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "compliments"
LC05_ZIP = Path(
    "C:/Users/Kamron/claude-exp/domovoi-project/functional-testing/"
    "plugin-fixtures/lc05/lc05-satpayload.zip"
)
H1 = "--hash=sha256:" + "0" * 64


# ─── fixtures ─────────────────────────────────────────────────────────────


CORE_WITH_OPEN_ROUTES = '''
from fastapi import APIRouter
from domovoi import sdk
from domovoi.sdk import open_endpoint

router = APIRouter()


@router.get("/state")
async def state():
    return {}


@router.post("/tune")
@open_endpoint
async def tune():
    return {"tuned": True}


@router.post("/gated")
async def gated():
    return {"gated": True}


def build(sdk_):
    r = APIRouter()

    @r.api_route("/multi", methods=["POST", "PUT"])
    @sdk.open_endpoint
    async def multi():
        return {}

    @r.delete("/nested/{item_id}")
    @open_endpoint
    async def nested(item_id: int):
        return {}

    return r


def register(ctx):
    ctx.add_router(router)
'''

WEB_WITH_OPEN_ROUTE = '''
from fastapi import APIRouter
from domovoi.webkit import open_endpoint

router = APIRouter()


@router.post("/play")
@open_endpoint
async def play():
    return {}


@router.post("/stations")
async def create_station():
    return {}


def register_web(ctx):
    ctx.add_router(router)
'''


WEB_WITH_DEVICE_ROUTES = '''
from fastapi import APIRouter
import domovoi.webkit as webkit
from domovoi.webkit import device_endpoint, open_endpoint

router = APIRouter()


@router.post("/play")
@device_endpoint
async def play():
    return {}


@router.patch("/stations/{station_id}")
@webkit.device_endpoint
async def edit_station(station_id: int):
    return {}


@router.post("/ping")
@open_endpoint
async def ping():
    return {}


@router.post("/fcc-import")
async def fcc_import():
    return {}


def register_web(ctx):
    ctx.add_router(router)
'''

BOTH_MARKERS = '''
from fastapi import APIRouter
from domovoi.webkit import device_endpoint, open_endpoint

router = APIRouter()


@router.post("/both")
@open_endpoint
@device_endpoint
async def both():
    return {}
'''


def _write_package(root: Path, slug: str, core: str, web: str | None = None) -> Path:
    pkg = root / f"domovoi_plugin_{slug}"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(textwrap.dedent(core), encoding="utf-8")
    if web is not None:
        (pkg / "web.py").write_text(textwrap.dedent(web), encoding="utf-8")
    return pkg


def _zip_of(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    return buf.getvalue()


def _compliments_files() -> dict[str, bytes]:
    return {
        f.relative_to(FIXTURE).as_posix(): f.read_bytes()
        for f in sorted(FIXTURE.rglob("*"))
        if f.is_file() and "__pycache__" not in f.parts
    }


@pytest.fixture
def staging(tmp_path: Path, monkeypatch):
    """Sandbox the staging dir and fake the two registry reads step 6
    makes (fresh slug, no orphan schema) so stage_zip runs without Postgres."""
    root = tmp_path / "staging"
    root.mkdir()
    monkeypatch.setattr(installer, "staging_root", lambda: root)

    async def _no_row(slug: str):
        return None

    async def _no_schema(slug: str):
        return False

    monkeypatch.setattr(reg, "get_plugin", _no_row)
    monkeypatch.setattr(reg, "plugin_schema_exists", _no_schema)

    def _boom(*a, **kw):  # pragma: no cover — pip must not run
        raise AssertionError("pip was invoked")

    monkeypatch.setattr(installer, "pip_dry_run", _boom)
    yield root
    installer._STAGED.clear()


# ─── the AST scan ─────────────────────────────────────────────────────────


def test_scan_finds_every_open_endpoint_route(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, "scandemo", CORE_WITH_OPEN_ROUTES, WEB_WITH_OPEN_ROUTE)
    found = scan_open_endpoints(pkg)
    routes = {(e["process"], e["method"], e["path"], e["function"]) for e in found}
    assert routes == {
        ("core", "POST", "/tune", "tune"),
        ("core", "POST,PUT", "/multi", "multi"),
        ("core", "DELETE", "/nested/{item_id}", "nested"),
        ("web", "POST", "/play", "play"),
    }
    # Gated routes and plain GETs are not opt-outs and are not listed.
    assert not any(e["function"] in ("gated", "state", "create_station") for e in found)
    assert all(e["module"].startswith("domovoi_plugin_scandemo.") for e in found)


def test_scan_reports_an_opt_out_without_a_literal_path(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, "scandemo", '''
        from domovoi.sdk import open_endpoint
        PATH = "/computed"

        @router.post(PATH)
        @open_endpoint
        async def computed():
            return {}

        @open_endpoint
        async def bare():
            return {}
    ''')
    found = {e["function"]: e for e in scan_open_endpoints(pkg)}
    assert found["computed"]["method"] == "POST" and found["computed"]["path"] is None
    assert found["bare"]["method"] is None and found["bare"]["path"] is None


def test_scan_runs_in_a_subprocess_and_agrees_with_the_walk(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, "scandemo", CORE_WITH_OPEN_ROUTES, WEB_WITH_OPEN_ROUTE)
    assert collect_open_endpoints(pkg) == scan_open_endpoints(pkg)


def test_scan_never_imports_the_package(tmp_path: Path) -> None:
    marker = tmp_path / "IMPORTED.marker"
    pkg = _write_package(tmp_path, "scandemo", f'''
        import pathlib
        pathlib.Path({str(marker)!r}).write_text("imported")
        from domovoi.sdk import open_endpoint

        @router.post("/x")
        @open_endpoint
        async def x():
            return {{}}
    ''')
    assert [e["function"] for e in collect_open_endpoints(pkg)] == ["x"]
    assert not marker.exists()


def test_scan_refuses_a_package_it_cannot_parse(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, "scandemo", "def broken(:\n    pass\n")
    with pytest.raises(OpenEndpointScanError, match="cannot parse"):
        collect_open_endpoints(pkg)


def test_scan_times_out_instead_of_hanging(tmp_path: Path, monkeypatch) -> None:
    pkg = _write_package(tmp_path, "scandemo", "x = 1\n")

    def _slow(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="scan", timeout=kw.get("timeout"))

    monkeypatch.setattr(subprocess, "run", _slow)
    with pytest.raises(OpenEndpointScanError, match="exceeded"):
        collect_open_endpoints(pkg, timeout=1)


def test_scan_lists_device_endpoints_in_their_own_list(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, "scandemo", CORE_WITH_OPEN_ROUTES, WEB_WITH_DEVICE_ROUTES)
    marked = scan_marked_endpoints(pkg)
    device = {(e["process"], e["method"], e["path"], e["function"]) for e in marked["device"]}
    assert device == {
        ("web", "POST", "/play", "play"),
        ("web", "PATCH", "/stations/{station_id}", "edit_station"),
    }
    # The open list is unchanged by the device markers beside it, and a
    # route on the admin default is in neither.
    assert {e["function"] for e in marked["open"]} == {"tune", "multi", "nested", "ping"}
    assert not any(
        e["function"] == "fcc_import" for e in marked["open"] + marked["device"]
    )
    assert marked["conflicts"] == []
    assert scan_device_endpoints(pkg) == marked["device"]
    assert collect_marked_endpoints(pkg) == marked


def test_scan_reports_both_markers_as_a_conflict(tmp_path: Path) -> None:
    pkg = _write_package(tmp_path, "scandemo", "x = 1\n", BOTH_MARKERS)
    marked = collect_marked_endpoints(pkg)
    assert [e["function"] for e in marked["conflicts"]] == ["both"]
    assert marked["open"] == [] and marked["device"] == []


def test_scan_follows_a_local_alias_of_either_marker(tmp_path: Path) -> None:
    """Renaming the import (or binding the marker to another name) must
    not take a route off the trust screen — for either tier."""
    pkg = _write_package(tmp_path, "scandemo", '''
        from domovoi import webkit
        from domovoi.sdk import open_endpoint as anyone
        from domovoi.webkit import device_endpoint as household

        pal = household
        also_open: object = webkit.open_endpoint

        @router.post("/a")
        @household
        async def a():
            return {}

        @router.post("/b")
        @pal
        async def b():
            return {}

        @router.post("/c")
        @anyone
        async def c():
            return {}

        @router.post("/d")
        @also_open
        async def d():
            return {}

        @router.post("/admin")
        async def admin_default():
            return {}
    ''')
    marked = scan_marked_endpoints(pkg)
    assert {e["function"] for e in marked["device"]} == {"a", "b"}
    assert {e["function"] for e in marked["open"]} == {"c", "d"}
    assert marked["conflicts"] == []
    assert collect_marked_endpoints(pkg) == marked


def test_bundled_radio_opts_nothing_out() -> None:
    assert scan_open_endpoints(REPO_ROOT / "plugins" / "radio" / "domovoi_plugin_radio") == []


def test_bundled_radio_lists_its_device_tier_routes() -> None:
    """What the trust screen would show for radio: the everyday mutations
    on the device tier, the FCC import absent (admin default)."""
    found = {
        (e["process"], e["method"], e["path"])
        for e in scan_device_endpoints(REPO_ROOT / "plugins" / "radio" / "domovoi_plugin_radio")
    }
    assert found == {
        ("web", "POST", "/play"),
        ("web", "POST", "/stations"),
        ("web", "PATCH", "/stations/{station_id}"),
        ("web", "DELETE", "/stations/{station_id}"),
        ("web", "POST", "/stations/{station_id}/resolve-simulcast"),
        ("core", "POST", "/stations/{station_id}/resolve-simulcast"),
    }


# ─── the preview ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_lists_the_open_endpoints_in_the_zip(staging) -> None:
    files = _compliments_files()
    manifest = files["domovoi-plugin.toml"].decode("utf-8").replace(
        'core = "domovoi_plugin_compliments.core"',
        'core = "domovoi_plugin_compliments.core"\n'
        'web = "domovoi_plugin_compliments.web"',
    )
    files["domovoi-plugin.toml"] = manifest.encode("utf-8")
    files["domovoi_plugin_compliments/core.py"] = (
        files["domovoi_plugin_compliments/core.py"].decode("utf-8")
        + textwrap.dedent('''

        from fastapi import APIRouter
        from domovoi.sdk import open_endpoint

        extra_router = APIRouter()

        @extra_router.post("/compliment-now")
        @open_endpoint
        async def compliment_now():
            return {}
        ''')
    ).encode("utf-8")
    files["domovoi_plugin_compliments/web.py"] = textwrap.dedent(WEB_WITH_OPEN_ROUTE).encode("utf-8")
    staged = await stage_zip(_zip_of(files))
    preview = staged.preview
    assert preview["slug"] == "compliments"
    listed = {(e["process"], e["method"], e["path"]) for e in preview["open_endpoints"]}
    assert listed == {("core", "POST", "/compliment-now"), ("web", "POST", "/play")}
    assert preview["device_endpoints"] == []
    assert preview["satellite"] is None


def _compliments_with_web(web: str) -> dict[str, bytes]:
    files = _compliments_files()
    files["domovoi-plugin.toml"] = files["domovoi-plugin.toml"].decode("utf-8").replace(
        'core = "domovoi_plugin_compliments.core"',
        'core = "domovoi_plugin_compliments.core"\n'
        'web = "domovoi_plugin_compliments.web"',
    ).encode("utf-8")
    files["domovoi_plugin_compliments/web.py"] = textwrap.dedent(web).encode("utf-8")
    return files


@pytest.mark.asyncio
async def test_preview_lists_the_device_endpoints_apart_from_the_open_ones(staging) -> None:
    staged = await stage_zip(_zip_of(_compliments_with_web(WEB_WITH_DEVICE_ROUTES)))
    preview = staged.preview
    device = {(e["process"], e["method"], e["path"]) for e in preview["device_endpoints"]}
    assert device == {("web", "POST", "/play"), ("web", "PATCH", "/stations/{station_id}")}
    opened = {(e["process"], e["method"], e["path"]) for e in preview["open_endpoints"]}
    assert opened == {("web", "POST", "/ping")}


@pytest.mark.asyncio
async def test_both_markers_on_one_route_refuse_the_install(staging) -> None:
    with pytest.raises(InstallError) as exc:
        await stage_zip(_zip_of(_compliments_with_web(BOTH_MARKERS)))
    assert exc.value.code == "endpoint_tier_conflict"
    assert "/both" in str(exc.value)
    assert list(staging.iterdir()) == []


@pytest.mark.asyncio
async def test_preview_of_the_plain_fixture_has_no_open_endpoints(staging) -> None:
    staged = await stage_zip(_zip_of(_compliments_files()))
    assert staged.preview["open_endpoints"] == []
    assert staged.preview["device_endpoints"] == []
    assert staged.preview["slug"] == "compliments"


@pytest.mark.asyncio
async def test_unparseable_package_refuses_the_install(staging) -> None:
    files = _compliments_files()
    files["domovoi_plugin_compliments/helper.py"] = b"def broken(:\n    pass\n"
    with pytest.raises(InstallError) as exc:
        await stage_zip(_zip_of(files))
    assert exc.value.code == "open_endpoint_scan_failed"
    assert "helper.py" in str(exc.value)
    assert list(staging.iterdir()) == []


SAT_MANIFEST = """
[plugin]
slug = "satdemo"
name = "Sat Demo"
version = "1.0.0"
publisher = "Coders Farm"
license = "MIT"
description = "satellite payload preview"
domovoi_api = ">=1.0,<2.0"

[entry_points]
core = "domovoi_plugin_satdemo.core"

[[handlers]]
name = "satdemo"
band = 400
label = "Sat Demo"
tone = "info"

[permissions]
satellite_root = true
warnings = ["Installs i2c-tools and a kernel overlay on every satellite."]

[satellite]
apt_packages = ["i2c-tools", "libasound2-plugins"]
pip_requirements = ["smbus2==0.4.3"]
pip_lockfile = "satellite-requirements.lock"
files_dir = "satellite_payload"
post_install = "satellite_payload/post_install.sh"
"""


@pytest.mark.asyncio
async def test_preview_states_the_satellite_root_payload(staging) -> None:
    files: dict[str, bytes | str] = {
        "domovoi-plugin.toml": SAT_MANIFEST,
        "domovoi_plugin_satdemo/__init__.py": "",
        "domovoi_plugin_satdemo/core.py": "def register(ctx):\n    pass\n",
        "satellite-requirements.lock": f"smbus2==0.4.3 {H1}\n",
        "satellite_payload/post_install.sh": "#!/bin/sh\necho hi\n",
        "satellite_payload/overlay.dtbo": b"\0" * (1024 * 1024),
        "satellite_payload/sub/readme.txt": "hello",
    }
    staged = await stage_zip(_zip_of(files))
    sat = staged.preview["satellite"]
    assert sat == {
        "apt_packages": ["i2c-tools", "libasound2-plugins"],
        "post_install": "satellite_payload/post_install.sh",
        "pip_requirements": ["smbus2==0.4.3"],
        # Counted exactly as the satellite channel serves them: the
        # files_dir tree (3 entries) plus post_install and the pip
        # lockfile under their manifest-declared names (2 more).
        "files_count": 5,
        "payload_mb": 1.0,
    }
    assert staged.preview["permissions"]["satellite_root"] is True


@pytest.mark.asyncio
@pytest.mark.skipif(not LC05_ZIP.is_file(), reason="LC-05 fixture zip not on this box")
async def test_preview_of_the_lc05_files_only_payload(staging) -> None:
    staged = await stage_zip(LC05_ZIP.read_bytes())
    sat = staged.preview["satellite"]
    assert sat["apt_packages"] == [] and sat["post_install"] is None
    assert sat["pip_requirements"] == []
    assert sat["files_count"] == 3
    assert sat["payload_mb"] >= 0
