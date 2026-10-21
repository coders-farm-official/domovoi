"""The Windows satellite Setup (windows/installer/DomovoiSatelliteSetup.iss)
and its worker (windows/installer/satellite-setup.ps1), 2026-10-21.

The worker is run for real against a fake Domovoi server on 127.0.0.1
(a thread here, holding a test identity key): the setup code it trades,
the signed answer it checks, the bundle it downloads and checks, and the
install-windows.ps1 it runs, which in the bundle served here is a
stand-in that writes down the arguments it got. The ``core`` fixture is
the same fake WITHOUT the key, answering in the server's place with the
genuine fingerprint (review of wf/win-sat-installer, round 1): nothing it
serves may run. When Inno
Setup 6 is on this PC, the Setup itself is compiled into a tmp folder and
run silently against the same fake, once with the code on its command line
and once with it in its file name, as a download from the dashboard's link
is named. Nothing here installs a satellite, or touches this account's
registry, Start menu, power plan or %LOCALAPPDATA%.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from domovoi import satellite_setup
from satellite import _ed25519, enrolment, server_identity

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "windows" / "installer"
ISS = INSTALLER / "DomovoiSatelliteSetup.iss"
WORKER = INSTALLER / "satellite-setup.ps1"
#: The test server's identity key, and an impostor's.
SEED = bytes(range(32))
IMPOSTOR = bytes(range(1, 33))
PUBLIC = _ed25519.public_key(SEED)
FP = server_identity.fingerprint_for(PUBLIC)
PAYLOAD = enrolment.build("http://192.168.1.20:6370", FP, "Küche")
CODE = satellite_setup.code_for(PAYLOAD)
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
    get = text.index("[DomovoiSatelliteSetup.Http]::Get(($base + $BundlePath)")
    assert text.index("Resolve-Code $base $setupCode") < get, "the proof comes first"
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
    """A Domovoi server for the worker. Codes: GOOD (installs), BAD (the
    installer refuses), SWAP (the bundle served is not the one signed for).
    ``key`` is the seed it signs with: the server's own (SEED), or an
    impostor's, which still names the genuine fingerprint in its payload.
    ``unsigned``: it answers without a signature at all. Every request is
    written down."""

    def __init__(self, tmp: Path, key: bytes = SEED, unsigned: bool = False):
        self.out = tmp / "recorded.json"
        payloads = {"good": PAYLOAD, "bad": enrolment.build("http://192.168.1.20:6370", FP, "Bad Room"),
                    "swap": enrolment.build("http://192.168.1.20:6370", FP, "Swap Room")}
        self.payloads = {satellite_setup.code_for(v): v for v in payloads.values()}
        self.GOOD, self.BAD, self.SWAP = (satellite_setup.code_for(payloads[k]) for k in ("good", "bad", "swap"))
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
                core.seen.append(self.path.split("?")[0])
                m = re.fullmatch(r"/v1/satellite-setup/codes/([A-Z0-9]+)(?:\?challenge=([0-9a-f]{32}))?", self.path)
                if m and m.group(1) in core.bundles:
                    code, challenge = m.group(1), m.group(2) or ""
                    core.served = code
                    digest = hashlib.sha256(core.bundles[core.GOOD if code == core.SWAP else code]).hexdigest()
                    payload = core.payloads[code]
                    doc = {"format": 1, "code": code, "payload": payload, "core": "http://192.168.1.20:6370",
                           "room": enrolment.parse(payload).room, "room_id": enrolment.parse(payload).room_id,
                           "fingerprint": FP, "bundle_sha256": digest, "bundle_size": 1,
                           "bundle_path": "/v1/satellite-code/bundle.tar.gz", "expires_at": 1.0}
                    if not unsigned:
                        msg = satellite_setup.setup_message(challenge, code, payload, digest)
                        doc.update(challenge=challenge, algorithm="ed25519",
                                   public_key=base64.b64encode(_ed25519.public_key(key)).decode(),
                                   signature=base64.b64encode(_ed25519.sign(key, msg)).decode())
                    return self._send(200, json.dumps(doc, ensure_ascii=False).encode("utf-8"))
                if m:
                    return self._send(404, json.dumps({"detail": "that setup code is not known here"}).encode())
                if self.path == "/v1/satellite-code/bundle.tar.gz":
                    body = core.bundles[core.served]
                    if core.served == core.SWAP:
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
def server(tmp_path):
    """The household's Domovoi server: it holds the identity key."""
    c = FakeCore(tmp_path)
    yield c
    c.httpd.shutdown()


@pytest.fixture
def core(tmp_path):
    """Something answering in the server's place: the genuine payload and
    fingerprint, but not the key (it signs with its own)."""
    c = FakeCore(tmp_path, key=IMPOSTOR)
    yield c
    c.httpd.shutdown()


def _ps(args, *, timeout=180):
    # From the temp folder: a PowerShell that finds no %LOCALAPPDATA% writes
    # its module cache under the current folder, and that must not be the repo.
    return subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          cwd=tempfile.gettempdir())


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
    ("DomovoiSatelliteSetup_K7M4QX2P9TRA_192.168.1.20_6370.exe", "K7M4QX2P9TRA", "http://192.168.1.20:6370"),
    ("DomovoiSatelliteSetup_k7m4qx2p9tra_192.168.1.20_6370 (1).exe", "K7M4QX2P9TRA", "http://192.168.1.20:6370"),
    ("DomovoiSatelliteSetup_K7M4QX2P9TRA_domovoi.local_6370.exe", "K7M4QX2P9TRA", "http://domovoi.local:6370"),
    ("DomovoiSatelliteSetup_K7M4QX2P9TRA.exe", "K7M4QX2P9TRA", ""),
    # Off the home network: the code is read, the server never.
    ("DomovoiSatelliteSetup_K7M4QX2P9TRA_8.8.8.8_6370.exe", "K7M4QX2P9TRA", ""),
    ("DomovoiSatelliteSetup_K7M4QX2P9TRA_evil.example.com_6370.exe", "K7M4QX2P9TRA", ""),
    ("DomovoiSatelliteSetup.exe", "", ""),
    ("DomovoiSatelliteSetup_K7M4QX2P.exe", "", ""),
    ("setup.exe", "", ""),
])
def test_the_file_name_gives_the_code_and_a_home_server(tmp_path, name, code, server):
    status, got, _ = _worker(tmp_path, "-Name", name)
    assert status == 0 and got == {"code": code, "server": server}


@windows_only
def test_a_code_resolves_to_its_room_and_the_typed_address_is_completed(tmp_path, server):
    # "127.0.0.1:port" typed without http://; the code as people read it.
    typed = satellite_setup.display_code(CODE).lower()
    status, got, out = _worker(tmp_path, "-Resolve", "-Server", f"127.0.0.1:{server.port}", "-Code", typed)
    assert status == 0, out
    assert got["result"] == "ok" and got["room"] == "Küche" and got["fingerprint"] == FP
    assert got["server"] == server.url and got["core"] == "http://192.168.1.20:6370"
    assert server.seen == [f"/v1/satellite-setup/codes/{CODE}"]
    assert "approval_code" not in got


@windows_only
def test_an_unknown_code_says_so(tmp_path, server):
    status, got, _ = _worker(tmp_path, "-Resolve", "-Server", server.url, "-Code", "ZZZZ-ZZZZ-ZZZZ")
    assert status == 2 and got["result"] == "error"
    assert "not known here" in got["error"] and "new code" in got["error"]


@windows_only
@pytest.mark.parametrize("server", ["8.8.8.8", "https://domovoi.example.com", "http://192.168.1.20:6370/x",
                                    "user@192.168.1.20", ""])
def test_a_server_off_the_home_network_is_never_contacted(tmp_path, server):
    status, got, _ = _worker(tmp_path, "-Resolve", "-Server", server, "-Code", CODE)
    assert status == 2 and "home network" in got["error"]


@windows_only
def test_a_malformed_code_is_refused_before_anything(tmp_path, server):
    status, got, _ = _worker(tmp_path, "-Resolve", "-Server", server.url, "-Code", "K7M4")
    assert status == 2 and "12 letters" in got["error"] and server.seen == []


@windows_only
def test_install_runs_the_bundle_s_installer_with_the_payload_and_the_choices(tmp_path, server):
    root = tmp_path / "root"
    status, got, out = _worker(tmp_path, "-Install", "-Server", server.url, "-Code", server.GOOD,
                               "-Permissions", "automatic", "-Power", "standby", "-Work", str(tmp_path),
                               "-InstallRoot", str(root))
    assert status == 0, out
    assert got["result"] == "ok" and got["install_exit"] == "0" and got["approved"] == "0"
    assert server.recorded() == {"payload": PAYLOAD, "yes": True, "permissions": "automatic", "power": "standby",
                               "wait": 0, "root": str(root)}
    assert server.seen == [f"/v1/satellite-setup/codes/{CODE}", "/v1/satellite-code/bundle.tar.gz"]
    assert "Installed." in out, "the installer's lines come through"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("domovoi-")], "the download is cleaned up"


@windows_only
def test_a_bundle_that_is_not_the_announced_one_runs_nothing(tmp_path, server):
    status, got, _ = _worker(tmp_path, "-Install", "-Server", server.url, "-Code", server.SWAP, "-Work", str(tmp_path),
                             "-InstallRoot", str(tmp_path / "root"))
    assert status == 3 and "not the download the server signed for this code" in got["error"]
    assert server.recorded() is None


@windows_only
def test_the_installer_s_refusal_is_said_in_its_own_words(tmp_path, server):
    status, got, _ = _worker(tmp_path, "-Install", "-Server", server.url, "-Code", server.BAD, "-Work", str(tmp_path),
                             "-InstallRoot", str(tmp_path / "root"))
    assert status == 4 and got["install_exit"] == "3"
    assert got["error"].startswith("the server at that address did not prove it is the one in the code")


@windows_only
@pytest.mark.parametrize("mode", ["impostor_key", "unsigned"])
def test_a_host_answering_in_the_server_s_place_runs_nothing(tmp_path, mode):
    """Review round 1: a host on the LAN (or on the path) that serves the
    genuine payload and fingerprint and a bundle of its own. Without the
    server's key it can't sign, so nothing is downloaded or run."""
    rogue = FakeCore(tmp_path, key=IMPOSTOR, unsigned=(mode == "unsigned"))
    try:
        for verb in ("-Resolve", "-Install"):
            status, got, _ = _worker(tmp_path, verb, "-Server", rogue.url, "-Code", rogue.GOOD,
                                     "-Work", str(tmp_path), "-InstallRoot", str(tmp_path / "root"))
            assert status == 3 and "did not prove it is the Domovoi server" in got["error"], got
        assert rogue.recorded() is None and "/v1/satellite-code/bundle.tar.gz" not in rogue.seen
    finally:
        rogue.httpd.shutdown()


@windows_only
def test_an_answer_for_another_payload_than_the_code_is_refused(tmp_path, server):
    """A server (its own key, properly signed) that answers a code with a
    payload that code isn't for: a code commits to its payload."""
    server.payloads[server.GOOD] = enrolment.build("http://192.168.1.20:6370", FP, "Elsewhere")
    status, got, _ = _worker(tmp_path, "-Resolve", "-Server", server.url, "-Code", server.GOOD)
    assert status == 3 and "did not prove" in got["error"]


@windows_only
def test_the_worker_never_goes_through_the_system_proxy():
    text = WORKER.read_text(encoding="ascii")
    assert "req.Proxy = null;" in text and "req.AllowAutoRedirect = false;" in text
    assert "Invoke-WebRequest" not in text and "Invoke-RestMethod" not in text and "WebClient" not in text


def test_the_worker_s_code_and_fingerprint_are_the_server_s():
    """The C# in the worker against the Python it twins, through its own
    tests: code_for and the RFC 8032 vector (see the parse-and-run tests)."""
    text = WORKER.read_text(encoding="ascii")
    assert '"domovoi-satellite-setup-v1`n"' in text
    assert satellite_setup.SETUP_CONTEXT == b"domovoi-satellite-setup-v1"


@windows_only
def test_the_worker_s_ed25519_and_code_agree_with_python(tmp_path):
    seed = bytes(range(7, 39))
    pub = _ed25519.public_key(seed)
    msg = "domovoi-satellite-setup-v1\nab\nc\u00fcd".encode("utf-8")
    sig = _ed25519.sign(seed, msg)
    bad = bytes([sig[0] ^ 1]) + sig[1:]
    script = tmp_path / "probe.ps1"
    worker = WORKER.read_text(encoding="ascii")
    src = worker[worker.index("$ProofSource = @'"):worker.index("function Get-Answer")]
    script.write_text(src + (
        "Use-Proof\n"
        f"$pub=[Convert]::FromBase64String('{base64.b64encode(pub).decode()}')\n"
        f"$msg=[Convert]::FromBase64String('{base64.b64encode(msg).decode()}')\n"
        f"'good=' + [DomovoiSatelliteSetup.Ed25519]::Verify($pub, $msg, [Convert]::FromBase64String('{base64.b64encode(sig).decode()}'))\n"
        f"'bad=' + [DomovoiSatelliteSetup.Ed25519]::Verify($pub, $msg, [Convert]::FromBase64String('{base64.b64encode(bad).decode()}'))\n"
        "'fp=' + [DomovoiSatelliteSetup.Proof]::Fingerprint($pub)\n"
        f"'code=' + [DomovoiSatelliteSetup.Proof]::CodeFor([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{base64.b64encode(PAYLOAD.encode()).decode()}')))\n"),
        encoding="ascii")
    r = _ps(["-File", str(script)])
    lines = dict(line.split("=", 1) for line in r.stdout.split() if "=" in line)
    assert lines == {"good": "True", "bad": "False", "fp": server_identity.fingerprint_for(pub), "code": CODE}, r.stdout + r.stderr


VECTORS = REPO / "domovoi" / "tests" / "fixtures" / "ed25519-setup-vectors.json"


@windows_only
def test_the_worker_s_ed25519_against_the_vector_suite(tmp_path):
    """Review round 2's 53 vectors: RFC 8032, openssl-signed keys, and the
    negatives (flipped bits, non-canonical S, malformed sizes and points,
    small-order keys and R). One PowerShell run checks them all."""
    cases = json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"]
    assert len(cases) >= 53 and sum(1 for c in cases if c.get("small_order")) >= 2
    worker = WORKER.read_text(encoding="ascii")
    src = worker[worker.index("$ProofSource = @'"):worker.index("function Get-Answer")]
    script = tmp_path / "vectors.ps1"
    script.write_text(src + (
        "Use-Proof\n"
        "function B([string]$h) { $b = New-Object byte[] ($h.Length / 2); "
        "for ($i = 0; $i -lt $b.Length; $i++) { $b[$i] = [Convert]::ToByte($h.Substring(2 * $i, 2), 16) }; ,$b }\n"
        f"foreach ($c in (Get-Content -Raw -LiteralPath '{VECTORS}' | ConvertFrom-Json).vectors) {{\n"
        "  'V ' + [int][DomovoiSatelliteSetup.Ed25519]::Verify((B $c.pub), (B $c.msg), (B $c.sig))\n"
        "}\n"), encoding="ascii")
    r = _ps(["-File", str(script)], timeout=600)
    got = [line[2:] == "1" for line in r.stdout.splitlines() if line.startswith("V ")]
    assert len(got) == len(cases), r.stdout[-2000:] + r.stderr[-2000:]
    wrong = [c["name"] for c, g in zip(cases, got) if g != c["expect"]]
    assert wrong == []


def test_the_python_twin_agrees_except_where_the_worker_is_stricter():
    """satellite/_ed25519.py on the same vectors: the same verdict, but for
    the small-order key it still accepts (the worker refuses it)."""
    cases = json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"]
    for c in cases:
        got = _ed25519.verify(bytes.fromhex(c["pub"]), bytes.fromhex(c["msg"]), bytes.fromhex(c["sig"]))
        assert got == c["python_twin"], c["name"]
        if not c.get("small_order"):
            assert got == c["expect"], c["name"]


@windows_only
def test_what_the_wizard_shows_comes_from_the_signed_payload(tmp_path, server):
    """Review round 2: a host in front of the real server relabels the
    answer's plain fields (room, core). The wizard's room and server are
    read from the payload the code and the signature hold, so the relabel
    shows nowhere."""
    import urllib.request

    class Front(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            with urllib.request.urlopen(server.url + self.path) as r:
                body = r.read()
            if self.path.startswith("/v1/satellite-setup/codes/"):
                d = json.loads(body)
                d.update(room="Front Door", core="http://192.168.1.99:6370", fingerprint="SHA256:" + "A" * 43)
                body = json.dumps(d).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    front = ThreadingHTTPServer(("127.0.0.1", 0), Front)
    threading.Thread(target=front.serve_forever, daemon=True).start()
    try:
        status, got, out = _worker(tmp_path, "-Resolve", "-Server", f"http://127.0.0.1:{front.server_address[1]}",
                                   "-Code", server.GOOD)
    finally:
        front.shutdown()
    assert status == 0, out
    assert (got["room"], got["core"], got["fingerprint"]) == ("Küche", "http://192.168.1.20:6370", FP)
    assert got["code"] == CODE


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
                          capture_output=True, timeout=300, cwd=tempfile.gettempdir()).returncode


def test_the_silent_setup_installs_with_the_code_on_its_command_line(setup_exe, server, tmp_path):
    root = tmp_path / "root"
    code = _run_setup(setup_exe, f"/SERVER={server.url}", f"/CODE={server.GOOD}", "/PERMISSIONS=ask", "/POWER=yes",
                      f"/INSTALLROOT={root}")
    assert code == 0
    assert server.recorded() == {"payload": PAYLOAD, "yes": True, "permissions": "ask", "power": "yes", "wait": 0,
                               "root": str(root)}


def test_a_setup_named_by_the_dashboard_s_link_needs_nothing_typed(setup_exe, server, tmp_path):
    named = tmp_path / f"DomovoiSatelliteSetup_{server.GOOD}_127.0.0.1_{server.port} (1).exe"
    shutil.copyfile(setup_exe, named)
    root = tmp_path / "root"
    assert _run_setup(named, f"/INSTALLROOT={root}") == 0
    got = server.recorded()
    assert got and got["payload"] == PAYLOAD and got["permissions"] == "ask" and got["power"] == "no"


def test_a_refused_install_fails_the_silent_setup(setup_exe, server, tmp_path):
    assert _run_setup(setup_exe, f"/SERVER={server.url}", f"/CODE={server.BAD}",
                      f"/INSTALLROOT={tmp_path / 'root'}") != 0
