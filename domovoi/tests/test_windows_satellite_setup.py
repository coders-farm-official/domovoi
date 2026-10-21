"""The Windows satellite Setup (windows/installer/DomovoiSatelliteSetup.iss)
and its worker (windows/installer/satellite-setup.ps1), 2026-10-21.

The worker is run for real against a fake Domovoi server on 127.0.0.1
(a thread here): the setup code it trades, the bundle it downloads and
checks, and the install-windows.ps1 it runs, which in the bundle served
here is a stand-in that writes down the arguments it got. When Inno
Setup 6 is on this PC, the Setup itself is compiled into a tmp folder and
run silently against the same fake, once with the code on its command line
and once with it in its file name, as a download from the dashboard's link
is named. Nothing here installs a satellite, or touches this account's
registry, Start menu, power plan or %LOCALAPPDATA%.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from satellite import enrolment

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "windows" / "installer"
ISS = INSTALLER / "DomovoiSatelliteSetup.iss"
WORKER = INSTALLER / "satellite-setup.ps1"
FP = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"
PAYLOAD = enrolment.build("http://192.168.1.20:6370", FP, "Küche")
WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows PowerShell")
POWERSHELL = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
              / "powershell.exe")


# ─── the files ────────────────────────────────────────────────────────────

def test_the_setup_is_per_user_and_leaves_nothing_of_its_own():
    iss = ISS.read_text(encoding="ascii")
    for line in ("PrivilegesRequired=lowest", "CreateAppDir=no", "Uninstallable=no", "ArchitecturesAllowed=x64os",
                 "OutputBaseFilename=DomovoiSatelliteSetup"):
        assert re.search(rf"^{re.escape(line)}$", iss, re.M), line
    # Its one file is the worker, into {tmp} only.
    files = iss.split("[Files]", 1)[1].split("[", 1)[0]
    entries = [ln for ln in files.splitlines() if ln.strip() and not ln.lstrip().startswith(";")]
    assert entries == ['Source: "{#Worker}"; Flags: dontcopy'], entries
    assert "[Registry]" not in iss and "[Icons]" not in iss and "[Run]" not in iss
    assert "IsAdmin" in iss, "an elevated Setup stops"
    # The signing hook, off unless build.ps1 -Sign defines it.
    assert re.search(r"^#ifdef SignSetup\nSignTool=domovoi\n#endif$", iss, re.M)


def test_the_worker_is_ascii_and_runs_windows_own_programs_by_full_path():
    text = WORKER.read_text(encoding="ascii")
    assert r'"$env:SystemRoot\System32\tar.exe"' in text
    assert r'"$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"' in text
    assert "Set-ExecutionPolicy" not in text and "RunAs" not in text
    iss = ISS.read_text(encoding="ascii")
    assert r"PsExe = '{sys}\WindowsPowerShell\v1.0\powershell.exe';" in iss


def test_the_worker_checks_the_digest_before_it_runs_anything():
    text = WORKER.read_text(encoding="ascii")
    get = text.index("Invoke-WebRequest -UseBasicParsing -Uri ($base + $BundlePath)")
    check = text.index("Get-FileHash", get)
    unpack = text.index("tar.exe", check)
    run = text.index("install-windows.ps1", unpack)
    assert get < check < unpack < run


def test_the_setup_says_the_same_version_as_the_server_setup():
    def one(path, pattern):
        found = re.findall(pattern, path.read_text(encoding="ascii"), re.M)
        assert len(found) == 1, (path, pattern, found)
        return found[0]

    for pattern in (r'^#define AppVersion "([^"]+)"$', r"^VersionInfoVersion=(\S+)$"):
        assert one(ISS, pattern) == one(INSTALLER / "DomovoiSetup.iss", pattern)


@windows_only
def test_the_worker_parses():
    r = _ps(["-Command", "$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
             f"'{WORKER}', [ref]$t, [ref]$e); $e.Count"])
    assert r.stdout.strip() == "0", r.stdout + r.stderr


# ─── the fake server ──────────────────────────────────────────────────────

RECORDER = r"""
param([Parameter(Position = 0)] [string]$Payload = '', [switch]$Yes, [string]$Permissions = '',
      [string]$Power = '', [int]$Wait = -1, [string]$InstallRoot = '')
$doc = @{ payload = $Payload; yes = [bool]$Yes; permissions = $Permissions; power = $Power; wait = $Wait;
          root = $InstallRoot }
[IO.File]::WriteAllText('@OUT@', ($doc | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
Write-Host 'Checking the Domovoi server named in the code...'
if ('@FAIL@' -eq '1') {
    [Console]::Error.WriteLine('install: the server at that address did not prove it is the one in the code, so nothing was installed')
    exit 3
}
Write-Host 'Installed.'
exit 0
"""


def _bundle(out: Path, fail: bool) -> bytes:
    script = RECORDER.replace("@OUT@", str(out)).replace("@FAIL@", "1" if fail else "0").encode("ascii")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("satellite/scripts/install-windows.ps1")
        info.size = len(script)
        tar.addfile(info, io.BytesIO(script))
    return buf.getvalue()


class FakeCore:
    """Codes GOOD (installs), BADX (the installer refuses) and SWAP (the
    bundle is not the one announced); every request is written down."""

    GOOD, BAD, SWAP = "K7M4QX2P", "BADX2222", "SWAP3333"

    def __init__(self, tmp: Path):
        self.out = tmp / "recorded.json"
        self.bundles = {self.GOOD: _bundle(self.out, False), self.BAD: _bundle(self.out, True),
                        self.SWAP: _bundle(self.out, False)}
        self.served = self.GOOD
        self.seen: list[str] = []
        core = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body: bytes, ctype="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                core.seen.append(self.path)
                m = re.fullmatch(r"/v1/satellite-setup/codes/([A-Z0-9]+)", self.path)
                if m and m.group(1) in core.bundles:
                    code = m.group(1)
                    core.served = code
                    digest = hashlib.sha256(core.bundles[self.GOOD if code == self.SWAP else code]).hexdigest()
                    doc = {"format": 1, "payload": PAYLOAD, "core": "http://192.168.1.20:6370", "room": "Küche",
                           "room_id": "kuche", "fingerprint": FP, "bundle_sha256": digest,
                           "bundle_size": 1, "bundle_path": "/v1/satellite-code/bundle.tar.gz",
                           "expires_at": 1.0}
                    return self._send(200, json.dumps(doc, ensure_ascii=False).encode("utf-8"))
                if m:
                    return self._send(404, json.dumps({"detail": "that setup code is not known here"}).encode())
                if self.path == "/v1/satellite-code/bundle.tar.gz":
                    body = core.bundles[core.served]
                    if core.served == self.SWAP:
                        body = body + b"\0"
                    return self._send(200, body, "application/gzip")
                return self._send(404, b'{"detail": "Not Found"}')

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def recorded(self) -> dict | None:
        try:
            return json.loads(self.out.read_text(encoding="utf-8"))
        except OSError:
            return None


@pytest.fixture
def core(tmp_path):
    c = FakeCore(tmp_path)
    yield c
    c.httpd.shutdown()


def _ps(args, *, timeout=180):
    return subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def _worker(tmp: Path, *args: str) -> tuple[int, dict[str, str], str]:
    out = tmp / "result.txt"
    out.unlink(missing_ok=True)
    r = _ps(["-File", str(WORKER), "-Out", str(out), *args])
    result = {}
    if out.is_file():
        for line in out.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            result[k] = v
    return r.returncode, result, r.stdout + r.stderr


# ─── the worker ───────────────────────────────────────────────────────────

@windows_only
@pytest.mark.parametrize(("name", "code", "server"), [
    ("DomovoiSatelliteSetup_K7M4QX2P_192.168.1.20_6370.exe", "K7M4QX2P", "http://192.168.1.20:6370"),
    ("DomovoiSatelliteSetup_k7m4qx2p_192.168.1.20_6370 (1).exe", "K7M4QX2P", "http://192.168.1.20:6370"),
    ("DomovoiSatelliteSetup_K7M4QX2P_domovoi.local_6370.exe", "K7M4QX2P", "http://domovoi.local:6370"),
    ("DomovoiSatelliteSetup_K7M4QX2P.exe", "K7M4QX2P", ""),
    # Off the home network: the code is read, the server never.
    ("DomovoiSatelliteSetup_K7M4QX2P_8.8.8.8_6370.exe", "K7M4QX2P", ""),
    ("DomovoiSatelliteSetup_K7M4QX2P_evil.example.com_6370.exe", "K7M4QX2P", ""),
    ("DomovoiSatelliteSetup.exe", "", ""),
    ("DomovoiSatelliteSetup_TOOSHORT1.exe", "", ""),
    ("setup.exe", "", ""),
])
def test_the_file_name_gives_the_code_and_a_home_server(tmp_path, name, code, server):
    status, got, _ = _worker(tmp_path, "-Name", name)
    assert status == 0 and got == {"code": code, "server": server}


@windows_only
def test_a_code_resolves_to_its_room_and_the_typed_address_is_completed(tmp_path, core):
    # "127.0.0.1:port" typed without http://; the code as people read it.
    status, got, out = _worker(tmp_path, "-Resolve", "-Server", f"127.0.0.1:{core.port}", "-Code", "k7m4-qx2p")
    assert status == 0, out
    assert got["result"] == "ok" and got["room"] == "Küche" and got["fingerprint"] == FP
    assert got["server"] == core.url and got["core"] == "http://192.168.1.20:6370"
    assert core.seen == ["/v1/satellite-setup/codes/K7M4QX2P"]
    assert "approval_code" not in got


@windows_only
def test_an_unknown_code_says_so(tmp_path, core):
    status, got, _ = _worker(tmp_path, "-Resolve", "-Server", core.url, "-Code", "ZZZZ-ZZZZ")
    assert status == 2 and got["result"] == "error"
    assert "not known here" in got["error"] and "new code" in got["error"]


@windows_only
@pytest.mark.parametrize("server", ["8.8.8.8", "https://domovoi.example.com", "http://192.168.1.20:6370/x",
                                    "user@192.168.1.20", ""])
def test_a_server_off_the_home_network_is_never_contacted(tmp_path, server):
    status, got, _ = _worker(tmp_path, "-Resolve", "-Server", server, "-Code", "K7M4QX2P")
    assert status == 2 and "home network" in got["error"]


@windows_only
def test_a_malformed_code_is_refused_before_anything(tmp_path, core):
    status, got, _ = _worker(tmp_path, "-Resolve", "-Server", core.url, "-Code", "K7M4")
    assert status == 2 and "8 letters" in got["error"] and core.seen == []


@windows_only
def test_install_runs_the_bundle_s_installer_with_the_payload_and_the_choices(tmp_path, core):
    root = tmp_path / "root"
    status, got, out = _worker(tmp_path, "-Install", "-Server", core.url, "-Code", core.GOOD,
                               "-Permissions", "automatic", "-Power", "standby", "-Work", str(tmp_path),
                               "-InstallRoot", str(root))
    assert status == 0, out
    assert got["result"] == "ok" and got["install_exit"] == "0" and got["approved"] == "0"
    assert core.recorded() == {"payload": PAYLOAD, "yes": True, "permissions": "automatic", "power": "standby",
                               "wait": 0, "root": str(root)}
    assert core.seen == ["/v1/satellite-setup/codes/K7M4QX2P", "/v1/satellite-code/bundle.tar.gz"]
    assert "Installed." in out, "the installer's lines come through"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("domovoi-")], "the download is cleaned up"


@windows_only
def test_a_bundle_that_is_not_the_announced_one_runs_nothing(tmp_path, core):
    status, got, _ = _worker(tmp_path, "-Install", "-Server", core.url, "-Code", core.SWAP, "-Work", str(tmp_path),
                             "-InstallRoot", str(tmp_path / "root"))
    assert status == 3 and "not the download the server announced" in got["error"]
    assert core.recorded() is None


@windows_only
def test_the_installer_s_refusal_is_said_in_its_own_words(tmp_path, core):
    status, got, _ = _worker(tmp_path, "-Install", "-Server", core.url, "-Code", core.BAD, "-Work", str(tmp_path),
                             "-InstallRoot", str(tmp_path / "root"))
    assert status == 4 and got["install_exit"] == "3"
    assert got["error"].startswith("the server at that address did not prove it is the one in the code")


# ─── the Setup itself, when Inno Setup is here ────────────────────────────

def _iscc() -> Path | None:
    for c in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
              Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
              Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe"):
        if c.is_file():
            return c
    return None


ISCC = _iscc() if WINDOWS else None


@pytest.fixture(scope="module")
def setup_exe(tmp_path_factory) -> Path:
    if ISCC is None:
        pytest.skip("Inno Setup 6 is not on this PC")
    out = tmp_path_factory.mktemp("setup")
    r = subprocess.run([str(ISCC), "/Q", f"/O{out}", str(ISS)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    exe = out / "DomovoiSatelliteSetup.exe"
    assert exe.read_bytes()[:2] == b"MZ"
    return exe


def _run_setup(exe: Path, *args: str) -> int:
    return subprocess.run([str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", *args],
                          capture_output=True, timeout=300).returncode


def test_the_silent_setup_installs_with_the_code_on_its_command_line(setup_exe, core, tmp_path):
    root = tmp_path / "root"
    code = _run_setup(setup_exe, f"/SERVER={core.url}", f"/CODE={core.GOOD}", "/PERMISSIONS=ask", "/POWER=yes",
                      f"/INSTALLROOT={root}")
    assert code == 0
    assert core.recorded() == {"payload": PAYLOAD, "yes": True, "permissions": "ask", "power": "yes", "wait": 0,
                               "root": str(root)}


def test_a_setup_named_by_the_dashboard_s_link_needs_nothing_typed(setup_exe, core, tmp_path):
    named = tmp_path / f"DomovoiSatelliteSetup_{core.GOOD}_127.0.0.1_{core.port} (1).exe"
    shutil.copyfile(setup_exe, named)
    root = tmp_path / "root"
    assert _run_setup(named, f"/INSTALLROOT={root}") == 0
    got = core.recorded()
    assert got and got["payload"] == PAYLOAD and got["permissions"] == "ask" and got["power"] == "no"


def test_a_refused_install_fails_the_silent_setup(setup_exe, core, tmp_path):
    assert _run_setup(setup_exe, f"/SERVER={core.url}", f"/CODE={core.BAD}",
                      f"/INSTALLROOT={tmp_path / 'root'}") != 0
