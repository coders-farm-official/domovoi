"""Settings → Satellites → Add a spare device (S1, 2026-10-13).

The web route that hands an admin the enrolment payload, the Linux install
command and what the QR code carries; the command's shape (it runs the
bundle only when its SHA-256 matches, and quotes the payload safely); the
vendored QR library's pin; and the dashboard card, driven through
jsx_interact_harness.js. No DB: the room lists and the core hop are
replaced.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from satellite import enrolment
from web.backend.api import satellites as web_sats
from web.backend.schemas import SpareDeviceEnrolRequest

REPO = Path(__file__).resolve().parents[2]
FP = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"
SHA = "ab" * 32
WIN_RUNTIME = {"ready": False, "python": "3.14.8", "files": 28, "missing": ["python-3.14.8-embed-amd64.zip"],
               "bytes_total": 95968382, "bytes_cached": 0,
               "fetch": {"running": False, "started_at": None, "finished_at": None, "ok": None, "message": None}}
LINUX_RUNTIME = {"ready": False, "files": 2, "bytes_total": 65627774, "bytes_cached": 0,
                 "pythons": [{"machine": "x86_64", "version": "3.14.8", "size": 36299807},
                             {"machine": "aarch64", "version": "3.13.16", "size": 29327967}],
                 "missing": ["cpython-3.14.8+20261009-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
                             "cpython-3.13.16+20261009-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz"],
                 "fetch": {"running": False, "started_at": None, "finished_at": None, "ok": None, "message": None}}


class Req:
    headers: dict = {}
    cookies: dict = {}
    query_params: dict = {}
    client = None


IDLE = {"running": False, "started_at": None, "finished_at": None, "ok": None, "message": None}
APP_NONE = {"release": None, "apks": [], "ready": False, "missing": [], "bytes_total": 0, "bytes_cached": 0,
            "upload_max": 0, "internet_off": False, "fetch": IDLE}
APP_APKS = [
    {"abi": "arm64-v8a", "file": "domovoi-9.9.9-arm64-v8a.apk", "size": 79409584, "ready": False,
     "path": "/v1/android-app/files/domovoi-9.9.9-arm64-v8a.apk"},
    {"abi": "armeabi-v7a", "file": "domovoi-9.9.9-armeabi-v7a.apk", "size": 47927662, "ready": False,
     "path": "/v1/android-app/files/domovoi-9.9.9-armeabi-v7a.apk"},
]
APP_MISSING = dict(APP_NONE, release={"version_name": "9.9.9", "version_code": 99, "cert_sha256": "ab" * 32},
                   apks=APP_APKS, missing=[a["file"] for a in APP_APKS], bytes_total=127337246,
                   upload_max=79409584)
APP_READY = dict(APP_MISSING, ready=True, missing=[], bytes_cached=127337246,
                 apks=[dict(a, ready=True) for a in APP_APKS])


@pytest.fixture
def core(monkeypatch):
    answers = {
        "/v1/health": (200, {"status": "ok", "identity": {"fingerprint": FP, "algorithm": "ed25519"}}),
        "/v1/satellite-code/bundle.sha256": (200, {"sha256": SHA, "size": 812345, "files": 70}),
        "/v1/admin/satellite-windows": (200, WIN_RUNTIME),
        "/v1/admin/android-app": (200, APP_NONE),
        "/v1/admin/satellite-linux": (200, LINUX_RUNTIME),
    }
    posted: list[str] = []
    answers["posted"] = posted

    async def get_admin(path, headers=None, **kw):
        return answers[path]

    async def post_admin(path, body=None, headers=None, **kw):
        posted.append(path)
        return 200, dict(answers["/v1/admin/android-app"][1], started=True,
                         fetch=dict(IDLE, running=True))

    monkeypatch.setattr(web_sats, "post_admin", post_admin)

    async def rooms():
        return [{"room_id": "kitchen"}, {"room_id": "office"}]

    async def pairings():
        return {"kitchen": {"paired_at": None, "last_seen_at": None}}

    monkeypatch.setattr(web_sats, "get_admin", get_admin)
    monkeypatch.setattr(web_sats, "_list_rooms", rooms)
    monkeypatch.setattr(web_sats, "_list_pairings", pairings)
    monkeypatch.setattr(web_sats.satellite_adoption, "advertised_domovoi_url", lambda: "ws://192.168.0.117:6370")
    return answers


async def test_the_route_answers_with_a_payload_the_device_parses(core):
    got = await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Kid's Room"), Req())
    parsed = enrolment.parse(got["payload"])
    assert parsed.core == "http://192.168.0.117:6370" and parsed.fp == FP
    assert (got["room"], got["room_id"]) == ("Kid's Room", "kids_room")
    assert got["bundle_sha256"] == SHA and got["conflict"] is None
    assert got["command"] == web_sats.install_command(parsed.core, SHA, got["payload"])
    assert got["command_windows"] == web_sats.install_command_windows(parsed.core, SHA, got["payload"])
    assert got["windows_runtime"] == WIN_RUNTIME
    assert got["linux_runtime"] == LINUX_RUNTIME


async def test_a_core_that_does_not_say_gives_no_linux_runtime(core):
    """An older core (no /v1/admin/satellite-linux): the card just says nothing."""
    core["/v1/admin/satellite-linux"] = (404, {"detail": "Not Found"})
    got = await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Den"), Req())
    assert got["linux_runtime"] is None and got["command"]


async def test_a_taken_room_is_said_before_the_device_is_refused(core):
    paired = await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Kitchen"), Req())
    assert "already paired" in paired["conflict"]
    known = await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Office"), Req())
    assert "already exists" in known["conflict"]


async def test_an_admin_can_give_another_address_but_never_one_off_the_home_network(core, monkeypatch):
    got = await web_sats.spare_device_enrolment(
        SpareDeviceEnrolRequest(room="Den", core="http://10.0.0.5:6370"), Req())
    assert enrolment.parse(got["payload"]).core == "http://10.0.0.5:6370"
    for outside in ("https://domovoi.example.com", "http://8.8.8.8:6370"):
        with pytest.raises(HTTPException) as e:
            await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Den", core=outside), Req())
        assert e.value.detail["reason"] == "not_home_network"
    # A box that found no LAN address advertises loopback: said, not handed out.
    monkeypatch.setattr(web_sats.satellite_adoption, "advertised_domovoi_url", lambda: "ws://127.0.0.1:6370")
    with pytest.raises(HTTPException) as e:
        await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Den"), Req())
    assert e.value.detail["reason"] == "not_home_network"


async def test_a_name_with_no_room_id_is_a_422_with_the_reason(core):
    with pytest.raises(HTTPException) as e:
        await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="!!!"), Req())
    assert e.value.status_code == 422 and e.value.detail["reason"] == "bad_room"
    with pytest.raises(HTTPException) as e:
        await web_sats.spare_device_enrolment(
            SpareDeviceEnrolRequest(room="Den", core="ftp://10.0.0.5"), Req())
    assert e.value.detail["reason"] == "bad_core"


async def test_no_identity_is_a_503_and_a_frozen_channel_passes_its_423_on(core):
    core["/v1/health"] = (200, {"status": "ok"})
    with pytest.raises(HTTPException) as e:
        await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Den"), Req())
    assert e.value.status_code == 503
    core["/v1/health"] = (200, {"identity": {"fingerprint": FP}})
    core["/v1/satellite-code/bundle.sha256"] = (423, {"detail": "frozen"})
    resp = await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Den"), Req())
    assert resp.status_code == 423


def test_the_route_is_an_admin_s():
    from domovoi.admin_auth import require_admin_mutation

    route = next(r for r in web_sats.router.routes if r.path == "/api/satellites/enrolment")
    assert any(d.dependency is require_admin_mutation for d in route.dependencies)


# ─── the command ──────────────────────────────────────────────────────────

def test_the_command_checks_the_digest_before_it_runs_anything():
    payload = enrolment.build("http://192.168.0.117:6370", FP, "Kid's Room")
    cmd = web_sats.install_command("http://192.168.0.117:6370", SHA, payload)
    fetch = cmd.index("wget")
    check = cmd.index("sha256sum -c")
    unpack = cmd.index("tar -xzf")
    run = cmd.index("install-pc.sh")
    assert fetch < check < unpack < run
    assert f"'{SHA}  '" in cmd and "&&" in cmd[check:run]
    assert "'" + payload + "'" in cmd, "the payload is one shell word"
    assert "http://192.168.0.117:6370/v1/satellite-code/bundle.tar.gz" in cmd
    assert "sudo" not in cmd, "the command itself takes no privilege"


def _find_bash() -> str | None:
    """A real bash: Git Bash on Windows, never WSL's launcher."""
    if sys.platform != "win32":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        for up in (Path(git).parent.parent, Path(git).parent.parent.parent):
            candidates.append(up / "bin" / "bash.exe")
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file() and "system32" not in str(c).lower():
            return str(c)
    return None


BASH = _find_bash()


@pytest.mark.skipif(BASH is None, reason="needs bash (Git Bash on Windows)")
def test_the_command_is_valid_shell_and_the_payload_survives_it(tmp_path):
    payload = enrolment.build("http://[fd00::17]:6370", FP, "Grandma’s Study & Den")
    cmd = web_sats.install_command("http://[fd00::17]:6370", SHA, payload)
    assert subprocess.run([BASH, "-n", "-c", cmd]).returncode == 0
    # bash hands install-pc.sh the payload as one word, exactly as built.
    last = cmd.rsplit(" && ", 1)[1]
    script = tmp_path / "words.sh"
    script.write_bytes(b'printf "%s" "${@: -1}"\n')
    run = last.replace('bash "$d/satellite/scripts/install-pc.sh"', f"bash '{script.as_posix()}'")
    out = subprocess.run([BASH, "-c", run], capture_output=True, encoding="utf-8").stdout
    assert out == payload
    assert enrolment.parse(out).room == "Grandma’s Study & Den"


# ─── the Windows command (2026-10-13) ─────────────────────────────────────

def test_the_windows_command_checks_the_digest_before_it_runs_anything():
    payload = enrolment.build("http://192.168.0.117:6370", FP, "Kid's Room")
    cmd = web_sats.install_command_windows("http://192.168.0.117:6370", SHA, payload)
    fetch = cmd.index("Invoke-WebRequest")
    check = cmd.index("Get-FileHash")
    stop = cmd.index("throw")
    unpack = cmd.index(r"System32\tar.exe")
    run = cmd.index("install-windows.ps1")
    assert fetch < check < stop < unpack < run
    assert f"-ne '{SHA}'" in cmd
    assert "'" + payload + "'" in cmd, "the payload is one single-quoted PowerShell string"
    assert "$ErrorActionPreference='Stop'" in cmd and cmd.startswith("& {"), "scoped to the command"
    assert "-ExecutionPolicy Bypass -File" in cmd and "Set-ExecutionPolicy" not in cmd
    assert "RunAs" not in cmd and "-Verb" not in cmd, "the command takes no privilege"
    assert "http://192.168.0.117:6370/v1/satellite-code/bundle.tar.gz" in cmd


def test_a_quote_in_a_value_can_not_end_the_powershell_string():
    assert web_sats._ps_quote("it's") == "'it''s'"


POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")
def test_the_windows_command_parses_and_the_payload_reaches_the_script_as_one_argument(tmp_path):
    payload = enrolment.build("http://[fd00::17]:6370", FP, "Grandma’s Study & Den")
    cmd = web_sats.install_command_windows("http://[fd00::17]:6370", SHA, payload)
    r = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command",
                        "$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseInput("
                        "$env:CMD, [ref]$t, [ref]$e); $e.Count"],
                       capture_output=True, text=True, env={**os.environ, "CMD": cmd})
    assert r.stdout.strip() == "0", r.stdout + r.stderr
    # The last step, with the installer replaced by a script that writes
    # down the arguments it was given.
    d = tmp_path / "d"
    (d / "satellite" / "scripts").mkdir(parents=True)
    out = tmp_path / "args.txt"
    (d / "satellite" / "scripts" / "install-windows.ps1").write_text(
        f"Set-Content -LiteralPath '{out}' -Encoding UTF8 -Value ($args.Count.ToString() + '|' + $args[0])",
        encoding="utf-8")
    last = cmd[cmd.index(r'& "$env:SystemRoot\System32\WindowsPowerShell'):-2]
    r = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command",
                        f"$d='{d}'; {last}"], capture_output=True, text=True)
    count, _, got = out.read_text(encoding="utf-8-sig").strip().partition("|")
    assert count == "1" and got == payload, r.stderr
    assert enrolment.parse(got).room == "Grandma’s Study & Den"


async def test_the_windows_files_status_and_fetch_go_to_the_core_as_an_admin(monkeypatch):
    seen = []

    async def get_admin(path, headers=None, **kw):
        seen.append(("GET", path))
        return 200, WIN_RUNTIME

    async def post_admin(path, body=None, headers=None, **kw):
        seen.append(("POST", path))
        return 200, {"started": True, **WIN_RUNTIME}

    monkeypatch.setattr(web_sats, "get_admin", get_admin)
    monkeypatch.setattr(web_sats, "post_admin", post_admin)
    assert (await web_sats.windows_runtime_status(Req())) == WIN_RUNTIME
    assert (await web_sats.windows_runtime_fetch(Req()))["started"] is True
    assert seen == [("GET", "/v1/admin/satellite-windows"), ("POST", "/v1/admin/satellite-windows/fetch")]
    from domovoi.admin_auth import require_admin_mutation, require_admin_read

    routes = {r.path: r for r in web_sats.router.routes}
    assert any(d.dependency is require_admin_read for d in routes["/api/satellites/windows-runtime"].dependencies)
    fetch = routes["/api/satellites/windows-runtime/fetch"]
    assert any(d.dependency is require_admin_mutation for d in fetch.dependencies) and "POST" in fetch.methods


async def test_the_linux_python_status_and_fetch_go_to_the_core_as_an_admin(monkeypatch):
    seen = []

    async def get_admin(path, headers=None, **kw):
        seen.append(("GET", path))
        return 200, LINUX_RUNTIME

    async def post_admin(path, body=None, headers=None, **kw):
        seen.append(("POST", path))
        return 200, {"started": True, **LINUX_RUNTIME}

    monkeypatch.setattr(web_sats, "get_admin", get_admin)
    monkeypatch.setattr(web_sats, "post_admin", post_admin)
    assert (await web_sats.linux_runtime_status(Req())) == LINUX_RUNTIME
    assert (await web_sats.linux_runtime_fetch(Req()))["started"] is True
    assert seen == [("GET", "/v1/admin/satellite-linux"), ("POST", "/v1/admin/satellite-linux/fetch")]
    from domovoi.admin_auth import require_admin_mutation, require_admin_read

    routes = {r.path: r for r in web_sats.router.routes}
    assert any(d.dependency is require_admin_read for d in routes["/api/satellites/linux-runtime"].dependencies)
    fetch = routes["/api/satellites/linux-runtime/fetch"]
    assert any(d.dependency is require_admin_mutation for d in fetch.dependencies) and "POST" in fetch.methods


async def test_a_refused_fetch_passes_the_core_s_answer_on(monkeypatch):
    async def post_admin(path, body=None, headers=None, **kw):
        return 503, {"detail": "the Linux Python lock is unreadable"}

    monkeypatch.setattr(web_sats, "post_admin", post_admin)
    resp = await web_sats.linux_runtime_fetch(Req())
    assert resp.status_code == 503


# ─── the vendored QR library ──────────────────────────────────────────────

def test_the_qr_library_is_the_pinned_mit_release():
    js = (REPO / "web" / "static" / "vendor" / "qrcode" / "qrcode.js").read_bytes()
    readme = (REPO / "web" / "static" / "vendor" / "qrcode" / "README.md").read_text(encoding="utf-8")
    assert hashlib.sha256(js).hexdigest() in readme
    assert b"Licensed under the MIT license" in js
    assert "MIT License" in (REPO / "web" / "static" / "vendor" / "qrcode" / "LICENSE.txt").read_text()


# ─── the card ─────────────────────────────────────────────────────────────

HARNESS = Path(__file__).with_name("jsx_interact_harness.js")
FILES = ["web/static/components.jsx", "web/static/satellite_media.jsx",
         "web/static/satellites.jsx", "web/static/settings.jsx"]

_SETUP = r"""
const __st = setTimeout;
setTimeout = (fn, ms, ...a) => { const t = __st(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
const __si = setInterval;
setInterval = (fn, ms, ...a) => { const t = __si(fn, ms, ...a); if (t && t.unref) t.unref(); return t; };
localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
window.addEventListener = () => {}; window.removeEventListener = () => {};
window.__settle = async (h, n = 6) => { for (let i = 0; i < n; i++) { await h.settle(); h.rerender(); } };
window.__deep = (n) => {
  if (n == null || typeof n === 'boolean') return '';
  if (typeof n === 'string' || typeof n === 'number') return String(n);
  if (Array.isArray(n)) return n.map(window.__deep).join('');
  return n.props ? window.__deep(n.props.children) : '';
};
"""

_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const isBtn = (label) => (e) => e.type === 'button' && w.__deep(e).includes(label);
if (!h.find(isBtn('add a spare device'))) return { card: false };
await h.click(isBtn('add a spare device')); await w.__settle(h);
h.type({ placeholder: "room name, e.g. Kid's Room" }, "Kid's Room");
await w.__settle(h);
await h.click(isBtn('make the code')); await w.__settle(h, 10);
const card = h.find((e) => String((e.props && e.props.className) || '').includes('spare-device-card'));
const svg = h.find((e) => e.type === 'svg' && String(e.props.className || '').includes('spare-qr'));
const rects = svg ? h.findAll((e) => e.type === 'rect' && h.ancestors(e).includes(svg)).length : 0;
const win = h.find((e) => String((e.props && e.props.className) || '').includes('spare-windows'));
const winText = win ? w.__deep(win) : '';
if (win) { await h.click(isBtn('get the Windows files')); await w.__settle(h, 10); }
const lpy = h.find((e) => String((e.props && e.props.className) || '').includes('spare-linux-python'));
const linuxText = lpy ? w.__deep(lpy) : '';
if (lpy) { await h.click(isBtn('get the Linux Python')); await w.__settle(h, 10); }
const lpyAfter = h.find((e) => String((e.props && e.props.className) || '').includes('spare-linux-python'));
return { card: true, text: w.__deep(card), calls: h.calls, rects, winText, linuxText,
         linuxAfter: lpyAfter ? w.__deep(lpyAfter) : '' };
"""


def _drive(scenarios: dict, tmp: Path) -> dict:
    node = shutil.which("node")
    assert node, "node is required to drive web/static JSX"
    spec = tmp / "scenarios.json"
    spec.write_text(json.dumps(scenarios), encoding="utf-8")
    proc = subprocess.run([node, str(HARNESS), str(REPO), f"@{spec}"],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    broken = {k: v["__harness_error"] for k, v in out.items() if isinstance(v, dict) and "__harness_error" in v}
    assert not broken, broken
    return out


@pytest.fixture(scope="module")
def card(tmp_path_factory) -> dict:
    payload = enrolment.build("http://192.168.0.117:6370", FP, "Kid's Room")
    answer = {
        "payload": payload, "room": "Kid's Room", "room_id": "kids_room",
        "core": "http://192.168.0.117:6370", "fingerprint": FP, "bundle_sha256": SHA,
        "bundle_size": 812345, "command": web_sats.install_command("http://192.168.0.117:6370", SHA, payload),
        "command_windows": web_sats.install_command_windows("http://192.168.0.117:6370", SHA, payload),
        "windows_runtime": WIN_RUNTIME,
        "linux_runtime": LINUX_RUNTIME,
        "conflict": None,
    }
    qr_js = (REPO / "web" / "static" / "vendor" / "qrcode" / "qrcode.js").read_text(encoding="utf-8")
    api = {
        "GET /api/satellites": [], "GET /api/satellites/approvals": [],
        "GET /api/satellites/pending": [], "GET /api/capabilities": {"host_kind": "linux"},
        "POST /api/satellites/enrolment": answer,
        "POST /api/satellites/windows-runtime/fetch": dict(WIN_RUNTIME, started=True,
                                                          fetch=dict(WIN_RUNTIME["fetch"], running=True)),
        "GET /api/satellites/windows-runtime": dict(WIN_RUNTIME, ready=True, missing=[],
                                                   bytes_cached=95968382),
        "POST /api/satellites/linux-runtime/fetch": dict(LINUX_RUNTIME, started=True,
                                                        fetch=dict(LINUX_RUNTIME["fetch"], running=True)),
        "GET /api/satellites/linux-runtime": dict(LINUX_RUNTIME, ready=True, missing=[], bytes_cached=65627774),
    }
    admin = {"files": FILES, "component": "SatellitesPanel", "api": api, "script": _SCRIPT,
             "setup": _SETUP + qr_js + "\nwindow.qrcode = qrcode;\n"}
    guest = dict(admin, setup=_SETUP + "Auth = { status: {}, subscribe: () => () => {}, "
                 "isLoggedIn: () => false, headers: () => ({}), modalOpen: false };")
    return _drive({"admin": admin, "guest": guest}, tmp_path_factory.mktemp("spare-card"))


def test_an_admin_gets_the_command_and_the_qr(card):
    r = card["admin"]
    assert r["card"] is True
    assert ["POST", "/api/satellites/enrolment", {"room": "Kid's Room"}] in [
        [c.get("method"), c.get("path"), c.get("body")] for c in r["calls"]] or any(
        "enrolment" in json.dumps(c) for c in r["calls"])
    assert "kids_room" in r["text"] and "sha256sum -c" in r["text"]
    assert r["rects"] > 100, "the QR code is drawn"


def test_someone_not_signed_in_as_admin_sees_no_card(card):
    assert card["guest"]["card"] is False


def test_the_card_gives_windows_its_command_the_unsigned_warning_and_the_files_button(card):
    r = card["admin"]
    text = r["winText"]
    assert "Get-FileHash" in text and "install-windows.ps1" in text
    assert "Not signed yet" in text and "antivirus" in text and "microphone-in-use" in text
    assert "not as administrator" in text or "Run as administrator" in text
    assert "get the Windows files" in text and "96 MB" in text
    assert any("windows-runtime/fetch" in json.dumps(c) for c in r["calls"]), "the button asks the server"


# ─── the phone's join link and the app this server hands out (2026-10-15) ──

async def test_the_qr_is_a_join_link_the_app_parses_like_the_payload(core):
    got = await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Kid's Room"), Req())
    assert got["join_url"] == "http://192.168.0.117:6370/join#" + got["payload"]
    assert enrolment.parse(got["join_url"]) == enrolment.parse(got["payload"])
    assert got["android_app"] == APP_NONE
    assert core["posted"] == [], "no release: nothing to fetch"


@pytest.mark.parametrize("policy", ["always", "", "sometimes", "never"])
@pytest.mark.parametrize("app", [APP_MISSING, dict(APP_MISSING, fetch=dict(IDLE, ok=False, message="HTTP 404")),
                                 APP_READY], ids=["missing", "failed_before", "ready"])
async def test_making_a_code_never_downloads_the_app(core, monkeypatch, policy, app):
    """Owner decision 2026-10-15: the server downloads the app only when an
    admin presses the button, whatever Settings -> Internet says."""
    from domovoi import egress

    core["/v1/admin/android-app"] = (200, app)
    monkeypatch.setattr(egress, "policy", lambda: policy)
    got = await web_sats.spare_device_enrolment(SpareDeviceEnrolRequest(room="Den"), Req())
    assert core["posted"] == []
    assert got["android_app"] == app


async def test_the_app_routes_go_to_the_core_with_the_right_tiers(monkeypatch):
    seen = []

    async def get_admin(path, headers=None, **kw):
        seen.append(("GET", path))
        return 200, APP_MISSING

    async def post_admin(path, body=None, headers=None, **kw):
        seen.append(("POST", path))
        return 200, dict(APP_MISSING, started=True)

    monkeypatch.setattr(web_sats, "get_admin", get_admin)
    monkeypatch.setattr(web_sats, "post_admin", post_admin)
    assert (await web_sats.android_app_status(Req())) == APP_MISSING
    assert (await web_sats.android_app_fetch(Req()))["started"] is True
    assert seen == [("GET", "/v1/admin/android-app"), ("POST", "/v1/admin/android-app/fetch")]
    from domovoi.admin_auth import require_admin_mutation, require_admin_read
    from web.backend.api import devices

    routes = {r.path: r for r in web_sats.router.routes}
    assert any(d.dependency is require_admin_read for d in routes["/api/satellites/android-app"].dependencies)
    for path in ("/api/satellites/android-app/fetch", "/api/satellites/android-app/upload"):
        assert any(d.dependency is require_admin_mutation for d in routes[path].dependencies), path
        assert routes[path].methods == {"POST"}
    household = next(r for r in devices.router.routes if r.path == "/api/devices/android-app")
    assert not household.dependencies, "open, like the page it points at"


class _StreamReq(Req):
    def __init__(self, body: bytes, content_type: str):
        self.headers = {"content-type": content_type, "content-length": str(len(body))}
        self._body = body

    async def stream(self):
        for i in range(0, len(self._body), 7):
            yield self._body[i:i + 7]


async def test_the_upload_is_streamed_to_the_core_and_its_answer_passed_back(monkeypatch):
    import httpx

    sent = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            sent["url"], sent["headers"] = url, headers
            sent["body"] = b"".join([c async for c in content])
            return httpx.Response(422, json={"detail": {"reason": "unknown", "message": "not one of them"}})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("DOMOVOI_URL", "http://127.0.0.1:6370")
    body = b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x.apk\"\r\n\r\nPK\r\n--b--\r\n"
    resp = await web_sats.android_app_upload(_StreamReq(body, "multipart/form-data; boundary=b"))
    assert sent["url"] == "http://127.0.0.1:6370/v1/admin/android-app/upload"
    assert sent["body"] == body and sent["headers"]["Content-Type"].startswith("multipart/form-data")
    assert sent["headers"]["Content-Length"] == str(len(body))
    assert resp.status_code == 422 and json.loads(resp.body)["detail"]["reason"] == "unknown"
    with pytest.raises(HTTPException) as e:
        await web_sats.android_app_upload(_StreamReq(b"{}", "application/json"))
    assert e.value.status_code == 415


async def test_household_phones_get_the_join_page_without_a_room_code(monkeypatch):
    from web.backend.api import devices
    from web.backend import domovoi_client

    async def get_admin(path, headers=None, **kw):
        assert path == "/v1/android-app" and not headers, "the core's public view, no credential"
        return 200, {"release": APP_READY["release"], "apks": APP_READY["apks"], "ready": True}

    monkeypatch.setattr(domovoi_client, "get_admin", get_admin)
    monkeypatch.setattr(web_sats.satellite_adoption, "advertised_domovoi_url", lambda: "ws://192.168.0.117:6370")
    got = await devices.android_app_for_household_phones()
    assert got["join_url"] == "http://192.168.0.117:6370/join" and got["ready"] is True
    monkeypatch.setattr(web_sats.satellite_adoption, "advertised_domovoi_url", lambda: "ws://127.0.0.1:6370")
    got = await devices.android_app_for_household_phones()
    assert got["join_url"] is None and "home network" in got["join_problem"]


_PHONE_SCRIPT = r"""
const w = h.global('window'); h.render(); await w.__settle(h);
const isBtn = (label) => (e) => e.type === 'button' && w.__deep(e).includes(label);
await h.click(isBtn('add a spare device')); await w.__settle(h);
h.type({ placeholder: "room name, e.g. Kid's Room" }, "Kid's Room");
await w.__settle(h);
await h.click(isBtn('make the code')); await w.__settle(h, 10);
const phone = h.find((e) => String((e.props && e.props.className) || '').includes('spare-phone'));
const app = () => h.find((e) => String((e.props && e.props.className) || '').includes('spare-app'));
const out = { text: (phone ? w.__deep(phone) : '') + ' | ' + (app() ? w.__deep(app()) : ''),
              qr: w.__qrData.slice(), buttons: [] };
out.buttons = app() ? h.findAll((e) => e.type === 'button' && h.ancestors(e).includes(app())).map(w.__deep) : [];
if (w.__act === 'fetch') { await h.click(isBtn('Download the app to this server')); await w.__settle(h, 10); }
if (w.__act === 'upload') {
  const input = h.find((e) => e.type === 'input' && e.props.type === 'file');
  await input.props.onChange({ target: { files: [{ name: 'domovoi-9.9.9-arm64-v8a.apk' }] } });
  await w.__settle(h, 10);
}
out.after = app() ? w.__deep(app()) : '';
out.calls = h.calls;
return out;
"""

_QR_SPY = r"""
window.__qrData = [];
const __qr = qrcode;
window.qrcode = (...a) => { const q = __qr(...a); const add = q.addData.bind(q); q.addData = (t) => { window.__qrData.push(t); return add(t); }; return q; };
"""


@pytest.fixture(scope="module")
def phone(tmp_path_factory) -> dict:
    payload = enrolment.build("http://192.168.0.117:6370", FP, "Kid's Room")
    base = {
        "payload": payload, "join_url": enrolment.join_link(payload), "room": "Kid's Room", "room_id": "kids_room",
        "core": "http://192.168.0.117:6370", "fingerprint": FP, "bundle_sha256": SHA, "bundle_size": 812345,
        "command": "c", "command_windows": None, "windows_runtime": None, "conflict": None,
    }
    qr_js = (REPO / "web" / "static" / "vendor" / "qrcode" / "qrcode.js").read_text(encoding="utf-8")
    common = {"GET /api/satellites": [], "GET /api/satellites/approvals": [],
              "GET /api/satellites/pending": [], "GET /api/capabilities": {"host_kind": "linux"}}

    def scenario(app, act="", extra=None):
        api = dict(common, **{"POST /api/satellites/enrolment": dict(base, android_app=app)}, **(extra or {}))
        return {"files": FILES, "component": "SatellitesPanel", "api": api, "script": _PHONE_SCRIPT,
                "setup": _SETUP + qr_js + "\nwindow.qrcode = qrcode;\n" + _QR_SPY + f"\nwindow.__act = {act!r};\n"}

    def card(app, extra=None, guest=False):
        auth = ("Auth = { status: {}, subscribe: () => () => {}, isLoggedIn: () => false, "
                "headers: () => ({}), modalOpen: false };") if guest else ""
        return {"files": FILES, "component": "GetTheAppCard",
                "api": {"GET /api/devices/android-app": app, **(extra or {})},
                "setup": _SETUP + auth + qr_js + "\nwindow.qrcode = qrcode;\n" + _QR_SPY,
                "script": "const w = h.global('window'); h.render(); await w.__settle(h); "
                          "return { text: h.text().join(' '), qr: w.__qrData.slice() };"}

    ready_public = {"join_url": "http://192.168.0.117:6370/join", "join_problem": None,
                    "release": APP_READY["release"], "apks": APP_READY["apks"], "ready": True}
    return _drive({
        "none": scenario(APP_NONE),
        "missing": scenario(APP_MISSING, "fetch",
                            {"POST /api/satellites/android-app/fetch": dict(
                                APP_MISSING, fetch=dict(IDLE, running=True, bytes_done=31_000_000,
                                                        bytes_todo=127_337_246))}),
        "failed": scenario(dict(APP_MISSING, fetch=dict(IDLE, ok=False, message="domovoi-9.9.9-arm64-v8a.apk: HTTP 404")),
                           "fetch", {"POST /api/satellites/android-app/fetch": {
                               "__error": {"status": 423, "message": "paused by the container guard"}}}),
        "offline": scenario(dict(APP_MISSING, internet_off=True), "upload",
                            {"UPLOAD /api/satellites/android-app/upload": dict(
                                APP_READY, accepted="domovoi-9.9.9-arm64-v8a.apk")}),
        "ready": scenario(APP_READY),
        "card_ready": card(ready_public),
        "card_none": card(dict(ready_public, release=None, apks=[], ready=False)),
        "card_missing": card(dict(ready_public, ready=False), {"GET /api/satellites/android-app": APP_MISSING}),
        "card_missing_guest": card(dict(ready_public, ready=False), guest=True),
    }, tmp_path_factory.mktemp("spare-phone"))


def test_the_phone_qr_carries_the_join_link(phone):
    payload = enrolment.build("http://192.168.0.117:6370", FP, "Kid's Room")
    assert phone["none"]["qr"] == [enrolment.join_link(payload)]
    assert "phone's camera" in phone["none"]["text"]


def test_no_release_is_said_plainly_with_no_buttons(phone):
    r = phone["none"]
    assert "No app release yet" in r["text"]
    assert r["buttons"] == [], "no dead buttons"


def test_a_missing_app_waits_for_the_download_button_then_shows_progress(phone):
    r = phone["missing"]
    assert "The app isn’t on this server yet (Domovoi 9.9.9: 64-bit 79 MB, 32-bit 48 MB)" in r["text"]
    assert any("Download the app to this server (about 127 MB)" in b for b in r["buttons"])
    assert any("upload an APK" in b for b in r["buttons"])
    assert [c.get("path") for c in r["calls"] if c.get("method") == "POST"].count(
        "/api/satellites/android-app/fetch") == 1, "one download, and only on the press"
    assert "Downloading Domovoi 9.9.9 to this server: 31 MB of 127 MB" in r["after"]


def test_a_failed_download_stays_on_the_card_with_the_button(phone):
    r = phone["failed"]
    assert "The download failed: domovoi-9.9.9-arm64-v8a.apk: HTTP 404" in r["text"]
    assert any("Download the app to this server" in b for b in r["buttons"])
    # Pressing it again and being refused: the reason stays beside the button.
    assert "paused by the container guard" in r["after"] and "The download failed" in r["after"]


def test_with_internet_off_only_the_upload_is_offered_and_it_lands(phone):
    r = phone["offline"]
    assert "Internet access is off" in r["text"] and "domovoi-9.9.9-arm64-v8a.apk" in r["text"]
    assert not any("Download the app" in b for b in r["buttons"]) and any("upload an APK" in b for b in r["buttons"])
    assert any(c.get("method") == "UPLOAD" and c.get("path") == "/api/satellites/android-app/upload" for c in r["calls"])
    assert "This server hands out Domovoi 9.9.9" in r["after"]


def test_a_ready_server_says_what_it_hands_out(phone):
    r = phone["ready"]
    assert "This server hands out Domovoi 9.9.9 (64-bit 79 MB, 32-bit 48 MB)" in r["text"]
    assert r["buttons"] == []


def test_settings_devices_offers_the_app_to_household_phones(phone):
    r = phone["card_ready"]
    assert "Get the Domovoi app" in r["text"] and "http://192.168.0.117:6370/join" in r["text"]
    assert r["qr"] == ["http://192.168.0.117:6370/join"]
    assert "No app release yet" in phone["card_none"]["text"] and phone["card_none"]["qr"] == []
    admin = phone["card_missing"]
    assert "Download the app to this server (about 127 MB)" in admin["text"] and admin["qr"] == []
    guest = phone["card_missing_guest"]
    assert "Ask whoever runs Domovoi to press “Download the app to this server”" in guest["text"]
    assert "Download the app to this server (about" not in guest["text"], "no button a guest can't use"


def test_the_card_says_older_linux_gets_its_own_python_and_offers_to_fetch_it(card):
    r = card["admin"]
    text = r["linuxText"]
    assert "Ubuntu 22.04" in text and "Debian 11" in text and "Fedora" in text
    assert "3.14.8 / 3.13.16" in text and "66 MB" in text
    assert "get the Linux Python" in text and "from GitHub itself" in text
    assert any("linux-runtime/fetch" in json.dumps(c) for c in r["calls"]), "the button asks the server"
    assert "debian or ubuntu" not in r["text"]
