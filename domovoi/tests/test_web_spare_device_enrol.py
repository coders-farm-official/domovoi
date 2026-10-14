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


class Req:
    headers: dict = {}
    cookies: dict = {}
    query_params: dict = {}
    client = None


@pytest.fixture
def core(monkeypatch):
    answers = {
        "/v1/health": (200, {"status": "ok", "identity": {"fingerprint": FP, "algorithm": "ed25519"}}),
        "/v1/satellite-code/bundle.sha256": (200, {"sha256": SHA, "size": 812345, "files": 70}),
    }

    async def get_admin(path, headers=None, **kw):
        return answers[path]

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
    from domovoi.admin_auth import require_admin_read

    route = next(r for r in web_sats.router.routes if r.path == "/api/satellites/enrolment")
    assert any(d.dependency is require_admin_read for d in route.dependencies)


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
return { card: true, text: w.__deep(card), calls: h.calls, rects };
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
        "conflict": None,
    }
    qr_js = (REPO / "web" / "static" / "vendor" / "qrcode" / "qrcode.js").read_text(encoding="utf-8")
    api = {
        "GET /api/satellites": [], "GET /api/satellites/approvals": [],
        "GET /api/satellites/pending": [], "GET /api/capabilities": {"host_kind": "linux"},
        "POST /api/satellites/enrolment": answer,
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
