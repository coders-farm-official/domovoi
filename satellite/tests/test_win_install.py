"""The Windows installer (satellite/win_install.py, scripts/install-windows.ps1,
scripts/uninstall-windows.ps1) — against fakes and scratch folders only.

Nothing here touches this account's registry, Start menu, power plan or
%LOCALAPPDATA%: the registry and the shortcut go to a JSON stub, powercfg is
a fake, the install folder is a tmp dir, and the PowerShell scripts run with
their test seams (a registry stub file, core-PC signals pointed elsewhere).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from satellite import enrolment, win_install as wi

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "satellite" / "scripts"
LOCK = json.loads((ROOT / "satellite" / "windows-runtime.json").read_text(encoding="utf-8"))
FP = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"
WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows PowerShell")
POWERSHELL = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
              / "powershell.exe")


# ─── the lock ─────────────────────────────────────────────────────────────

def test_the_tree_s_lock_is_complete_and_every_entry_well_formed(tmp_path):
    bundle = tmp_path / "b"
    (bundle / "satellite").mkdir(parents=True)
    shutil.copy(ROOT / "satellite" / "windows-runtime.json", bundle / "satellite")
    lock = wi.load_lock(bundle)
    names = {w["name"] for w in lock["wheels"]}
    from packaging.markers import Marker
    from packaging.requirements import Requirement

    env = {"platform_machine": "AMD64", "sys_platform": "win32", "platform_system": "Windows",
           "os_name": "nt", "python_version": ".".join(lock["python"]["version"].split(".")[:2])}
    for req_file in ("requirements.txt", "requirements-windows.txt"):
        for line in (ROOT / "satellite" / req_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            req = Requirement(line)
            if req.marker is not None and not Marker(str(req.marker)).evaluate(env):
                continue
            assert req.name.lower() in names, f"{req.name} is not in the Windows lock"
            version = next(w["version"] for w in lock["wheels"] if w["name"] == req.name.lower())
            assert req.specifier.contains(version, prereleases=True), (req, version)
    assert "openwakeword" in names and "miniaudio" in names and "spidev" not in names
    assert lock["python"]["file"] == f"python-{lock['python']['version']}-embed-amd64.zip"
    assert lock["pip"]["file"].startswith("pip-")
    for rel in lock["remove_after_install"]:
        assert ".." not in Path(rel).parts and rel.endswith("-asio.dll")
    for w in lock["wheels"]:
        assert w["file"].endswith(("-none-any.whl", "-win_amd64.whl")), w["file"]
        assert "GPL" not in w["license"].replace("GPL-3.0-or-later WITH GCC-exception-3.1", ""), w


def test_the_core_reads_the_same_lock():
    from domovoi import satellite_windows

    core = satellite_windows.load_lock(ROOT / "satellite" / "windows-runtime.json")
    assert [e["file"] for e in satellite_windows.entries(core)] == [e["file"] for e in wi.lock_entries(LOCK)]


@pytest.mark.parametrize("field,value", [
    ("file", "..\\evil.whl"), ("file", "x.exe"), ("sha256", "abc"),
    ("url", "https://example.com/numpy.whl"), ("url", "http://files.pythonhosted.org/packages/x.whl"),
])
def test_a_malformed_lock_is_refused(tmp_path, field, value):
    doc = json.loads(json.dumps(LOCK))
    doc["wheels"][0][field] = value
    (tmp_path / "satellite").mkdir()
    (tmp_path / "satellite" / "windows-runtime.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(wi.InstallError):
        wi.load_lock(tmp_path)


# ─── pinned downloads: the core first, then upstream, each held to the pin ─

class Answer(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def entry_for(body: bytes, name="x-1.0-py3-none-any.whl") -> dict:
    return {"file": name, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body),
            "url": "https://files.pythonhosted.org/packages/aa/" + name}


def opener_for(answers: dict):
    seen = []

    def opener(url, timeout=None):
        seen.append(url)
        a = answers.get(url.split("/")[2] if "pythonhosted" not in url else "upstream")
        if isinstance(a, Exception):
            raise a
        return Answer(a)
    opener.seen = seen
    return opener


def test_the_core_s_copy_is_used_when_it_is_the_pinned_file(tmp_path):
    body = b"wheel bytes" * 100
    op = opener_for({"192.168.0.117:6370": body, "upstream": b"never"})
    assert wi.fetch_pinned(entry_for(body), tmp_path, core="http://192.168.0.117:6370", opener=op) == "core"
    assert (tmp_path / "x-1.0-py3-none-any.whl").read_bytes() == body
    assert op.seen == ["http://192.168.0.117:6370/v1/satellite-windows/files/x-1.0-py3-none-any.whl"]
    assert wi.fetch_pinned(entry_for(body), tmp_path, core="http://192.168.0.117:6370", opener=op) == "present"


def test_a_core_serving_other_bytes_is_passed_over_for_upstream(tmp_path):
    body = b"the real wheel" * 50
    op = opener_for({"192.168.0.117:6370": b"x" * len(body), "upstream": body})
    assert wi.fetch_pinned(entry_for(body), tmp_path, core="http://192.168.0.117:6370", opener=op) == "upstream"
    assert (tmp_path / "x-1.0-py3-none-any.whl").read_bytes() == body


def test_nobody_serving_the_pinned_file_stops_the_install(tmp_path):
    body = b"real"
    op = opener_for({"192.168.0.117:6370": b"fake", "upstream": OSError("offline")})
    dest = tmp_path / "wheels"
    with pytest.raises(wi.InstallError, match="could not get"):
        wi.fetch_pinned(entry_for(body), dest, core="http://192.168.0.117:6370", opener=op)
    assert not list(dest.iterdir()), "nothing unverified is left behind"


def test_a_download_larger_than_the_pin_is_cut_off(tmp_path):
    body = b"a" * 10
    op = opener_for({"upstream": b"a" * 10_000})
    with pytest.raises(wi.InstallError):
        wi.fetch_pinned(entry_for(body), tmp_path, core=None, opener=op)


# ─── the Python, the registry, power ──────────────────────────────────────

def test_the_embeddable_python_sees_its_zip_the_app_and_its_own_site_packages_only(tmp_path):
    """No ``import site``: the account's user site-packages (its .pth files,
    usercustomize) never load into the satellite (security review)."""
    (tmp_path / "python314._pth").write_text("python314.zip\n.\n#import site\n")
    wi.write_pth(tmp_path, ["..\\app"])
    got = (tmp_path / "python314._pth").read_bytes()
    assert got == b"python314.zip\r\n.\r\n..\\app\r\nLib\\site-packages\r\n"
    assert b"import site" not in got
    wi.write_pth(tmp_path, ["..\\Jos\u00e9"])
    assert (tmp_path / "python314._pth").read_bytes().decode("utf-8").splitlines()[2] == "..\\Jos\u00e9", \
        "UTF-8, no BOM: how Python reads a ._pth"
    with pytest.raises(wi.InstallError):
        wi.pth_name(tmp_path / "nowhere")


def test_registration_is_this_user_s_and_uninstall_runs_the_copy_outside_the_code(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    root = tmp_path / "Satellite"
    (root / "python").mkdir(parents=True)
    stub = tmp_path / "registry.json"
    wi.register(root, "Kid's Room", "3.14.8", wi.Registry(stub))
    doc = json.loads(stub.read_text())
    assert doc["run"][wi.APP_KEY] == f'"{root / "python" / "pythonw.exe"}" -m satellite.win_service'
    u = doc["uninstall"][wi.APP_KEY]
    assert u["InstallLocation"] == str(root) and u["DisplayName"] == "Domovoi Satellite (Kid's Room)"
    assert f'-File "{root / "uninstall.ps1"}"' in u["UninstallString"]
    assert "app" not in Path(u["UninstallString"].split('-File "')[1].rstrip('"')).parts, \
        "the uninstaller never runs from the tree code updates rewrite"
    assert u["QuietUninstallString"].endswith(" -Quiet") and u["NoModify"] == 1
    assert doc["shortcut"]["path"].endswith("Start Menu\\Programs\\Domovoi Satellite.lnk") or \
        doc["shortcut"]["path"].endswith("Start Menu/Programs/Domovoi Satellite.lnk")


QUERY = """Power Setting GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Sleep after)
  Minimum Possible Setting: 0x00000000
  Maximum Possible Setting: 0xffffffff
  Possible Settings increment: 0x00000001
  Possible Settings units: Seconds
Current AC Power Setting Index: 0x00000708
Current DC Power Setting Index: 0x00000384
"""


class PowerCfg:
    def __init__(self, fail_set=False):
        self.calls, self.fail_set = [], fail_set

    def __call__(self, argv, **kw):
        self.calls.append(argv[1:])
        if argv[1] == "/getactivescheme":
            return SimpleNamespace(returncode=0, stdout="Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)", stderr="")
        if argv[1] == "/query":
            return SimpleNamespace(returncode=0, stdout=QUERY, stderr="")
        if argv[1] == "/setacvalueindex" and self.fail_set:
            return SimpleNamespace(returncode=1, stdout="", stderr="Access is denied.")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_power_is_asked_and_no_means_nothing_changes():
    pc = PowerCfg()
    assert wi.change_power(lambda q: False, run=pc) == {} and pc.calls == []


def test_yes_changes_only_the_plugged_in_value_and_records_what_was_there():
    pc = PowerCfg()
    got = wi.change_power(lambda q: True, run=pc)
    assert got == {"scheme": "381b4222-f694-41f0-9685-ff5bb260df2e", "standby": 0x708, "hibernate": 0x708, "lid": 0x708}
    sets = [c for c in pc.calls if c[0] == "/setacvalueindex"]
    assert sets and all(c[-1] == "0" for c in sets)
    assert not any(c[0] == "/setdcvalueindex" for c in pc.calls), "on battery it still sleeps"
    assert pc.calls[-1] == ["/setactive", "381b4222-f694-41f0-9685-ff5bb260df2e"]


def test_a_refused_power_change_is_said_and_the_install_goes_on(capsys):
    assert wi.change_power(lambda q: True, run=PowerCfg(fail_set=True)) == {}
    assert "could not change" in capsys.readouterr().out


# ─── install(): nothing is written before the core proves who it is ───────

def _args(tmp_path, payload, **kw):
    base = dict(payload=payload, bundle=str(ROOT), staged_python=str(tmp_path / "py"),
                root=str(tmp_path / "root"), permissions="ask", power="no", yes=True, no_start=True,
                wait=0, registry_file=str(tmp_path / "reg.json"))
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_core_that_can_not_prove_its_identity_writes_nothing(tmp_path, monkeypatch):
    from satellite import server_identity

    payload = enrolment.build("http://192.168.0.117:6370", FP, "Den")

    def refuse(e, opener=None, timeout=None):
        raise server_identity.IdentityError("signed by someone else")

    monkeypatch.setattr(enrolment, "verify", refuse)
    with pytest.raises(wi.InstallError) as e:
        wi.install(_args(tmp_path, payload))
    assert e.value.code == 3
    assert not (tmp_path / "root").exists() and not (tmp_path / "reg.json").exists()


@pytest.mark.parametrize("payload", ["not a code", "domovoi-room:1?core=http%3A%2F%2F8.8.8.8%3A6370&fp=x&room=Den"])
def test_a_refused_code_writes_nothing(tmp_path, payload):
    with pytest.raises(wi.InstallError) as e:
        wi.install(_args(tmp_path, payload))
    assert e.value.code == 2 and not (tmp_path / "root").exists()


# ─── the PowerShell scripts ───────────────────────────────────────────────

@pytest.mark.parametrize("name", ["install-windows.ps1", "uninstall-windows.ps1"])
def test_the_scripts_are_ascii_so_windows_powershell_5_reads_them_right(name):
    (SCRIPTS / name).read_bytes().decode("ascii")


def _ps(args, *, env=None, timeout=120):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, timeout=timeout)


@windows_only
@pytest.mark.parametrize("name", ["install-windows.ps1", "uninstall-windows.ps1"])
def test_the_scripts_parse(name):
    r = _ps(["-Command", "$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
             f"'{SCRIPTS / name}', [ref]$t, [ref]$e); $e.Count"])
    assert r.stdout.strip() == "0", r.stdout + r.stderr


NOTHING = json.dumps({"service": "", "registry": "", "programdata": "", "profile": "", "probe": ""})


@windows_only
def test_a_pc_that_runs_the_domovoi_server_is_refused_before_anything(tmp_path):
    marker = tmp_path / "ProgramData-Domovoi"
    marker.mkdir()
    root = tmp_path / "Satellite"
    payload = enrolment.build("http://192.168.0.117:6370", FP, "Den")
    signals = json.loads(NOTHING)
    signals["programdata"] = str(marker)
    r = _ps(["-File", str(SCRIPTS / "install-windows.ps1"), payload, "-InstallRoot", str(root),
             "-TestCorePcSignals", json.dumps(signals)])
    assert r.returncode == 5, r.stdout + r.stderr
    assert "never installed on the PC that runs the Domovoi server" in r.stderr
    assert not root.exists()


@windows_only
@pytest.mark.parametrize("marker", ["server-identity.json", "device-token.txt", "setup-code.txt"])
def test_a_server_s_files_in_this_account_count_as_a_server(tmp_path, marker):
    profile = tmp_path / ".domovoi"
    profile.mkdir()
    (profile / marker).write_text("{}")
    signals = json.loads(NOTHING)
    signals["profile"] = str(profile)
    r = _ps(["-File", str(SCRIPTS / "install-windows.ps1"), "domovoi-room:1?core=x", "-InstallRoot",
             str(tmp_path / "S"), "-TestCorePcSignals", json.dumps(signals)])
    assert r.returncode == 5 and marker in r.stderr


@windows_only
@pytest.mark.parametrize("payload", ["", "loopback", "domovoi-room:1?core=http%3A%2F%2F8.8.8.8%3A6370&fp=x&room=Den",
                                     "domovoi-room:1?fp=x&room=Den"])
def test_a_code_off_the_home_network_is_refused_before_any_download(tmp_path, payload, served):
    """W30: refused, and nothing fetched — the "core" in the code (here a
    loopback server, which is not the home network) is never asked for
    the Python, and no staging folder is made."""
    if payload == "loopback":
        payload = enrolment_payload_for(served.url)
    bundle = _bundle_copy(tmp_path, served)
    args = ["-File", str(bundle / "satellite" / "scripts" / "install-windows.ps1")] + ([payload] if payload else [])
    r = _ps(args + ["-InstallRoot", str(tmp_path / "S"), "-TestCorePcSignals", NOTHING], timeout=60)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "not on the home network" in r.stderr or "give the enrolment code" in r.stderr, r.stderr
    assert not (tmp_path / "S").exists()
    assert not (bundle / "stage").exists() and "Getting Python" not in r.stdout
    assert served.requests == [], "nothing was downloaded"


def _fake_install(root: Path, victim: Path) -> None:
    (root / "python").mkdir(parents=True)
    (root / "python" / "pythonw.exe").write_bytes(b"")
    (root / "app" / "satellite").mkdir(parents=True)
    (root / "app" / "satellite" / "client.py").write_text("x")
    (root / ".domovoi").mkdir()
    (root / ".domovoi" / "config.toml").write_text("[device]\n")
    (root / ".domovoi" / "pairing_token").write_text("secret")
    (root / ".domovoi-satellite-root").write_text("x")
    (root / "install-windows.json").write_text(json.dumps({"power": {"scheme": "not-a-guid", "lid": 1}}))
    shutil.copy(SCRIPTS / "uninstall-windows.ps1", root / "uninstall.ps1")
    victim.mkdir()
    (victim / "precious.txt").write_text("keep me")
    # A link inside the install folder must never be followed out of it.
    subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "app" / "link"), str(victim)],
                   check=True, capture_output=True)


@windows_only
@pytest.mark.parametrize("purge", [False, True])
def test_uninstall_removes_only_what_is_its_own(tmp_path, purge):
    root = tmp_path / "LocalAppData" / "Domovoi" / "Satellite"
    victim = tmp_path / "Documents"
    _fake_install(root, victim)
    stub = tmp_path / "registry.json"
    stub.write_text(json.dumps({
        "run": {"DomovoiSatellite": f'"{root}\\python\\pythonw.exe" -m satellite.win_service'},
        "uninstall": {"DomovoiSatellite": {"InstallLocation": str(root)}},
        "shortcut": {"target": f"{root}\\python\\pythonw.exe"},
        "someone_else": {"x": 1},
    }), encoding="utf-8")
    args = ["-File", str(root / "uninstall.ps1"), "-Quiet", "-TestRegistryFile", str(stub)]
    r = _ps(args + (["-Purge"] if purge else []))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (victim / "precious.txt").read_text() == "keep me", "the junction was not followed"
    left = json.loads(stub.read_text(encoding="utf-8-sig"))
    assert set(left) == {"someone_else"}
    if purge:
        assert not root.exists()
    else:
        assert sorted(p.name for p in root.iterdir()) == [".domovoi", ".domovoi-satellite-root"]
        assert sorted(p.name for p in (root / ".domovoi").iterdir()) == ["config.toml", "pairing_token"]


@windows_only
def test_purge_removes_only_the_satellite_s_own_files(tmp_path):
    root = tmp_path / "Satellite"
    _fake_install(root, tmp_path / "victim")
    (root / "notes.txt").write_text("someone's")
    (root / ".domovoi" / "server-identity.json").write_text("{}")      # a server's
    (root / ".domovoi" / "sounds" / "voices").mkdir(parents=True)
    (root / ".domovoi" / "sounds" / "voices" / "amy.mp3").write_bytes(b"x")
    (root / ".domovoi" / "config.toml.bak-20261013-120000").write_text("old")
    r = _ps(["-File", str(root / "uninstall.ps1"), "-Quiet", "-Purge", "-TestRegistryFile", str(tmp_path / "r.json")])
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.name for p in root.iterdir()) == [".domovoi", ".domovoi-satellite-root", "notes.txt"]
    left = sorted(p.name for p in (root / ".domovoi").iterdir())
    assert left == ["server-identity.json", "sounds"], left
    assert (root / ".domovoi" / "sounds" / "voices" / "amy.mp3").exists()


def test_the_uninstaller_s_list_covers_every_satellite_file():
    """The list Linux's purge uses (satellite/scripts/purge-satellite-files.py,
    which root runs from its own copy), and a server's markers."""
    import re as _re
    import runpy

    purge = runpy.run_path(str(SCRIPTS / "purge-satellite-files.py"), run_name="purge_list")
    text = (SCRIPTS / "uninstall-windows.ps1").read_text(encoding="ascii")
    block = text[text.index("$files = @("):text.index(")", text.index("$files = @("))]
    names = set(_re.findall(r"'([^']+)'", block))
    assert set(purge["SATELLITE_FILES"]) | set(purge["SATELLITE_DIRS"]) <= names, \
        set(purge["SATELLITE_FILES"]) - names
    assert {"config.toml.bak", "server-names.json"} <= names
    for marker in purge["CORE_MARKERS"]:
        assert f"'{marker}'" in text, "the uninstaller knows a server's files"


def test_the_install_folder_is_empty_or_an_earlier_install(tmp_path):
    wi.check_install_root(tmp_path / "new")
    (tmp_path / "new").mkdir()
    wi.check_install_root(tmp_path / "new")
    earlier = tmp_path / "earlier"
    earlier.mkdir()
    (earlier / wi.ROOT_MARKER).write_text("x")
    (earlier / "python").mkdir()
    wi.check_install_root(earlier)
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "taxes.pdf").write_text("x")
    with pytest.raises(wi.InstallError) as e:
        wi.check_install_root(docs)
    assert e.value.code == 2
    server = tmp_path / "server"
    (server / ".domovoi").mkdir(parents=True)
    (server / wi.ROOT_MARKER).write_text("x")
    (server / ".domovoi" / "device-token.txt").write_text("t")
    with pytest.raises(wi.InstallError, match="belongs to a Domovoi server"):
        wi.check_install_root(server)


@windows_only
def test_uninstall_refuses_a_folder_that_is_not_an_install(tmp_path):
    folder = tmp_path / "Documents"
    folder.mkdir()
    (folder / "precious.txt").write_text("keep me")
    r = _ps(["-File", str(SCRIPTS / "uninstall-windows.ps1"), "-Root", str(folder), "-Quiet"])
    assert r.returncode == 2 and (folder / "precious.txt").exists()


@windows_only
def test_uninstall_leaves_an_entry_that_points_elsewhere(tmp_path):
    root = tmp_path / "Satellite"
    _fake_install(root, tmp_path / "victim")
    stub = tmp_path / "registry.json"
    other = {"run": {"DomovoiSatellite": r'"C:\Other\pythonw.exe" -m satellite.win_service'},
             "uninstall": {"DomovoiSatellite": {"InstallLocation": r"C:\Other"}}}
    stub.write_text(json.dumps(other), encoding="utf-8")
    r = _ps(["-File", str(root / "uninstall.ps1"), "-Quiet", "-TestRegistryFile", str(stub)])
    assert r.returncode == 0, r.stderr
    assert json.loads(stub.read_text(encoding="utf-8-sig")) == other


def test_installing_again_keeps_the_person_s_original_power_values():
    first = {"scheme": "381b4222-f694-41f0-9685-ff5bb260df2e", "standby": 1800, "hibernate": 3600}
    again = {"scheme": "381b4222-f694-41f0-9685-ff5bb260df2e", "standby": 0, "hibernate": 0, "lid": 1}
    assert wi.merge_power(first, again) == {"scheme": first["scheme"], "standby": 1800, "hibernate": 3600, "lid": 1}
    other_plan = dict(again, scheme="8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")
    assert wi.merge_power(first, other_plan) == other_plan
    assert wi.merge_power(None, again) == again



# ─── security review of wf/sat-windows (8c402e6) ──────────────────────────

import base64  # noqa: E402
import http.server  # noqa: E402
import threading  # noqa: E402


def _ps_encoded(script: str, *, env=None, timeout=120):
    """Run a PowerShell script passed as -EncodedCommand (nothing in it is
    re-quoted on the command line)."""
    return _ps(["-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()], env=env, timeout=timeout)


class Served:
    """A loopback HTTP server that records every request and answers each
    with ``body`` (or ``json``)."""

    def __init__(self, body: bytes = b"not python"):
        self.body = body
        self.requests: list[str] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.requests.append(self.path)
                self.send_response(200)
                kind = "application/json" if outer.body.startswith(b"{") else "application/octet-stream"
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def served():
    s = Served()
    yield s
    s.close()


def enrolment_payload_for(core: str) -> str:
    from urllib.parse import quote

    return f"domovoi-room:1?core={quote(core, safe='')}&fp={quote(FP, safe='')}&room=Den"


def _bundle_copy(tmp_path: Path, served: "Served", **python) -> Path:
    """The installer and its lock in a scratch bundle; the lock's Python
    entry names what ``served`` serves (so nothing real is ever fetched)."""
    bundle = tmp_path / "bundle"
    (bundle / "satellite" / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS / "install-windows.ps1", bundle / "satellite" / "scripts")
    lock = json.loads(json.dumps(LOCK))
    lock["python"].update(sha256=hashlib.sha256(served.body).hexdigest().upper(), size=len(served.body),
                          url="https://www.python.org/ftp/python/3.14.8/" + lock["python"]["file"])
    lock["python"].update(python)
    (bundle / "satellite" / "windows-runtime.json").write_text(json.dumps(lock), encoding="utf-8")
    return bundle


def test_pip_installs_by_hash_only_from_the_checked_wheels(tmp_path):
    """W25: --require-hashes, --no-index, only the wheels folder."""
    seen = {}

    def run(argv, **kw):
        seen["argv"], seen["env"] = argv, kw.get("env")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    root = tmp_path / "root"
    (root / "python").mkdir(parents=True)
    wi.pip_install(root, tmp_path, LOCK, run=run)
    argv = seen["argv"]
    for flag in ("--require-hashes", "--no-index", "--no-deps", "--only-binary=:all:"):
        assert flag in argv, flag
    assert argv[argv.index("--find-links") + 1] == str(tmp_path)
    reqs = (tmp_path / "requirements-locked.txt").read_text(encoding="utf-8").splitlines()
    assert len(reqs) == len(LOCK["wheels"]) and all("--hash=sha256:" in r for r in reqs)
    assert seen["env"]["PIP_CONFIG_FILE"] == os.devnull


def test_the_install_folder_must_be_inside_the_account_s_own_folder(tmp_path):
    home = tmp_path / "Users" / "Jos\u00e9"
    (home / "AppData" / "Local").mkdir(parents=True)
    wi.check_install_root(home / "AppData" / "Local" / "Domovoi" / "Satellite", home)
    for outside in (tmp_path / "Satellite", tmp_path / "Users" / "other" / "Satellite", home):
        with pytest.raises(wi.InstallError, match="not inside your own user folder") as e:
            wi.check_install_root(outside, home)
        assert e.value.code == 2


@windows_only
@pytest.mark.parametrize("where", ["root", "parent"])
def test_a_junction_on_the_way_is_refused(tmp_path, where):
    """W26 / review note: Path.is_symlink() misses junctions."""
    home = tmp_path / "profile"
    target = tmp_path / "elsewhere"
    target.mkdir()
    home.mkdir()
    link = home / ("Satellite" if where == "root" else "Domovoi")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    root = link if where == "root" else link / "Satellite"
    with pytest.raises(wi.InstallError, match="link or junction"):
        wi.check_install_root(root, home)
    if where == "root":
        with pytest.raises(wi.InstallError, match="link or junction"):
            wi.check_install_root(root)  # no profile given: the folder itself is still checked


def test_a_symlinked_folder_is_refused(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "Satellite"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("this account may not create symlinks")
    with pytest.raises(wi.InstallError, match="link or junction"):
        wi.check_install_root(link, tmp_path)


@windows_only
def test_the_install_folder_gets_a_dacl_of_its_own(tmp_path):
    from satellite import win32

    root = tmp_path / "Satellite"
    (root / "python").mkdir(parents=True)
    (root / "python" / "python.exe").write_bytes(b"")
    wi.protect_root(root)
    sid = win32.current_user_sid()
    sddl = win32.path_dacl_sddl(str(root))
    assert sddl.startswith("D:P"), "nothing inherited from the folder above"
    aces = sorted(sddl.split("(")[1:])
    assert aces == sorted([f"A;OICI;FA;;;{sid})", "A;OICI;FA;;;SY)", "A;OICI;FA;;;BA)"]), sddl
    child = win32.path_dacl_sddl(str(root / "python" / "python.exe"))
    assert "AU" not in child and "WD" not in child and f";;;{sid})" in child, child


def _install_order(tmp_path, monkeypatch, *, proven: bool):
    from satellite import server_identity

    calls = []

    def verify(e, opener=None, timeout=None):
        if not proven:
            raise server_identity.IdentityError("signed by someone else")
        return e.fp

    monkeypatch.setattr(enrolment, "verify", verify)
    monkeypatch.setattr(wi, "stop_running", lambda root: calls.append("stop"))
    monkeypatch.setattr(wi, "protect_root", lambda root: calls.append("protect"))

    def replace_dir(src, dest):
        calls.append("write")
        raise wi.InstallError("stop the test here", 9)

    monkeypatch.setattr(wi, "replace_dir", replace_dir)
    payload = enrolment.build("http://192.168.0.117:6370", FP, "Den")
    with pytest.raises(wi.InstallError):
        wi.install(_args(tmp_path, payload, profile=str(tmp_path)))
    return calls


def test_a_running_satellite_is_stopped_only_after_the_proof(tmp_path, monkeypatch):
    """Review note: install-windows.ps1 stopped the installed satellite
    before the server had proven anything; win_install stops it after."""
    assert _install_order(tmp_path, monkeypatch, proven=False) == []
    assert _install_order(tmp_path, monkeypatch, proven=True) == ["stop", "protect", "write"]
    assert "Stop-Satellite" not in (SCRIPTS / "install-windows.ps1").read_text(encoding="ascii")


@windows_only
def test_an_administrator_window_is_refused(tmp_path):
    """W27, through the refusal-only test switch (it can't make a refused
    install pass)."""
    r = _ps(["-File", str(SCRIPTS / "install-windows.ps1"), "x", "-InstallRoot", str(tmp_path / "S"),
             "-TestAsAdministrator", "-TestCorePcSignals", NOTHING])
    assert r.returncode == 2 and "runs as administrator" in r.stderr, r.stdout + r.stderr
    assert not (tmp_path / "S").exists()


@windows_only
def test_the_server_s_windows_service_counts_as_a_server(tmp_path):
    """W28: a service of the signal's name exists (EventLog is on every
    Windows), so the PC counts as one running the server."""
    signals = json.loads(NOTHING)
    signals["service"] = "EventLog"
    r = _ps(["-File", str(SCRIPTS / "install-windows.ps1"), "x", "-InstallRoot", str(tmp_path / "S"),
             "-TestCorePcSignals", json.dumps(signals)])
    assert r.returncode == 5 and "Windows service (EventLog)" in r.stderr, r.stdout + r.stderr


@windows_only
@pytest.mark.parametrize("answer,refused", [(b'{"status": "ok", "bot_name": "Domovoi"}', True),
                                            (b'{"status": "ok"}', False)])
def test_a_server_answering_on_this_pc_counts_as_a_server(tmp_path, answer, refused):
    """W29: the probe of this PC's own core port."""
    srv = Served(answer)
    try:
        signals = json.loads(NOTHING)
        signals["probe"] = srv.url + "/v1/health"
        r = _ps(["-File", str(SCRIPTS / "install-windows.ps1"), "-InstallRoot", str(tmp_path / "S"),
                 "-TestCorePcSignals", json.dumps(signals)])
    finally:
        srv.close()
    assert srv.requests == ["/v1/health"]
    if refused:
        assert r.returncode == 5 and "answers on this PC" in r.stderr, r.stdout + r.stderr
    else:
        assert r.returncode == 2 and "give the enrolment code" in r.stderr, r.stdout + r.stderr


@windows_only
def test_a_lock_naming_a_python_from_elsewhere_is_refused_before_any_download(tmp_path, served):
    """W32: the Python must come from python.org; a lock naming anything
    else stops before anything is fetched."""
    bundle = _bundle_copy(tmp_path, served, url=served.url + "/python.zip")
    payload = enrolment_payload_for("http://domovoi-test-nowhere.home.arpa:6370")
    r = _ps(["-File", str(bundle / "satellite" / "scripts" / "install-windows.ps1"), payload,
             "-InstallRoot", str(tmp_path / "S"), "-TestCorePcSignals", NOTHING], timeout=60)
    assert r.returncode == 1 and "unexpected Python" in r.stderr, r.stdout + r.stderr
    assert served.requests == [] and not (bundle / "stage").exists()


def _get_pinned(tmp_path, served: "Served", sha: str, size: int):
    """install-windows.ps1's own Get-Pinned, taken from the script by
    PowerShell's parser and run against a loopback server."""
    dest = tmp_path / "python.zip"
    script = (
        "$ErrorActionPreference = 'Stop'; $t = $null; $e = $null; "
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:SCRIPT, [ref]$t, [ref]$e); "
        "$fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $n.Name -eq 'Get-Pinned' }, $true)[0]; "
        "function Say([string]$Text = '') { Write-Host $Text }; "
        "function Fail([string]$Text, [int]$Code = 1) { [Console]::Error.WriteLine(\"install: $Text\"); exit $Code }; "
        ". ([ScriptBlock]::Create($fn.Extent.Text)); "
        "$entry = [pscustomobject]@{ file = 'python.zip'; url = $env:URL; size = [long]$env:SIZE; sha256 = $env:SHA }; "
        "$from = Get-Pinned $entry $env:DEST ''; Write-Host \"GOT $from\""
    )
    env = {"SCRIPT": str(SCRIPTS / "install-windows.ps1"), "URL": served.url + "/python.zip", "SIZE": str(size),
           "SHA": sha, "DEST": str(dest)}
    return _ps_encoded(script, env=env, timeout=60), dest


@windows_only
def test_the_python_download_is_held_to_its_pinned_hash(tmp_path, served):
    """W31: bytes of the right size but another hash are thrown away and
    the install stops; the pinned ones are kept."""
    good = hashlib.sha256(served.body).hexdigest().upper()
    r, dest = _get_pinned(tmp_path, served, "0" * 64, len(served.body))
    assert r.returncode == 1 and "could not get python.zip" in r.stderr, r.stdout + r.stderr
    assert "did not serve the pinned file" in r.stdout and not dest.exists()
    r, dest = _get_pinned(tmp_path, served, good, len(served.body))
    assert r.returncode == 0 and "GOT " + served.url in r.stdout, r.stdout + r.stderr
    assert dest.read_bytes() == served.body


@windows_only
def test_the_staged_python_s_pth_is_utf8_without_a_bom(tmp_path):
    """Review item 3: the bundle sits under %TEMP%, inside the account's
    folder, whose name may be anything; -Encoding Ascii wrote "Jos?"."""
    import re as _re

    text = (SCRIPTS / "install-windows.ps1").read_text(encoding="ascii")
    m = _re.search(r"\n(\s*\$zipname = .*?UTF8Encoding\(\$false\)\)\))", text, _re.S)
    assert m, "the ._pth writer"
    py = tmp_path / "python"
    py.mkdir()
    (py / "python314._pth").write_text("python314.zip\n.\n#import site\n")
    bundle = str(tmp_path / "Jos\u00e9" / "domovoi-0123")
    script = "$pth = @(Get-Item -LiteralPath $env:PTH); $BundleRoot = $env:BUNDLE; " + m.group(1)
    r = _ps_encoded(script, env={"PTH": str(py / "python314._pth"), "BUNDLE": bundle})
    assert r.returncode == 0, r.stdout + r.stderr
    raw = (py / "python314._pth").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8").splitlines() == ["python314.zip", ".", bundle]
    assert "import site" not in raw.decode("utf-8")


# ─── PowerShell quoting: every character PowerShell takes for a quote ──────

def _ps_value(script: str) -> str:
    r = _ps_encoded("[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); " + script)
    assert r.returncode == 0, r.stderr[-600:]
    return r.stdout.lstrip("﻿").rstrip("\r\n")


@windows_only
@pytest.mark.parametrize("value", ["it's", "O\u2019Brien", "\u2018x\u2019", "a\u201ab\u201bc", "''\u2019\u2019", "$(calc)"])
def test_quoted_values_come_back_exactly(value):
    """Review note: PowerShell treats U+2018..U+201B as single quotes; the
    quoting helpers double them all, so a value can't end the string."""
    from web.backend.api import satellites as web_sats

    assert _ps_value("$x = " + wi.ps_quote(value) + "; $x") == value
    assert _ps_value("$x = " + web_sats._ps_quote(value) + "; $x") == value


@windows_only
def test_the_start_menu_shortcut_script_takes_any_profile_name(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kw):
        seen["script"] = argv[-1]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(wi.subprocess, "run", fake_run)
    root = Path("C:\\Users\\O\u2019Brien\\AppData\\Local\\Domovoi\\Satellite")
    wi.Registry(None).set_shortcut(tmp_path / "x.lnk", str(root / "python" / "pythonw.exe"),
                                   "-m satellite.win_service", str(root / "app"), str(root / "python" / "pythonw.exe"))
    monkeypatch.undo()          # subprocess.run is the real one again, for PowerShell's parser
    r = _ps_encoded("$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::ParseInput("
                    "$env:SCRIPT, [ref]$t, [ref]$e); $e.Count", env={"SCRIPT": seen["script"]})
    assert r.stdout.strip() == "0", r.stdout + r.stderr
    # And the shortcut's target comes back exactly.
    target = _ps_value(seen["script"].split(";")[1].strip().replace("$s.TargetPath=", "$x = ") + "; $x")
    assert target == str(root / "python" / "pythonw.exe")


@windows_only
def test_powercfg_is_windows_own_by_its_full_path():
    pc = PowerCfg()
    pc_argv = []
    wi.change_power(lambda q: True, run=lambda argv, **kw: pc_argv.append(argv[0]) or pc(argv, **kw))
    assert pc_argv and all(a.lower().endswith("\\system32\\powercfg.exe") for a in pc_argv), pc_argv
    text = (SCRIPTS / "uninstall-windows.ps1").read_text(encoding="ascii")
    assert "& powercfg" not in text and "System32\\powercfg.exe" in text



def test_no_environment_variable_steers_the_installer():
    """Review note: the core-PC signals were read from an environment
    variable an account could leave set; now only an explicit parameter
    (never passed by the dashboard's command) points them elsewhere."""
    text = (SCRIPTS / "install-windows.ps1").read_text(encoding="ascii")
    assert "DOMOVOI_TEST_COREPC_SIGNALS" not in text
    assert "$env:DOMOVOI" not in text
