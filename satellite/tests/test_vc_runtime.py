"""Microsoft's C++ runtime and Smart App Control on a Windows satellite.

onnxruntime 1.31's Windows wheel (the wake word) imports msvcp140.dll and
msvcp140_1.dll, at least the 14.44 it was built with, and carries neither;
Windows doesn't always have them, and this test PC's own are 14.42. The
installer checks before any download and the engine at every start, each
saying what is missing and where to get it; an app-local copy beside the
satellite's Python counts (that is what --local-vc-runtime would place,
behind a flag until the owner decides). Smart App Control's state is read
(never changed) and said plainly. Scratch folders only; nothing installed.
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
from pathlib import Path
from types import SimpleNamespace

import pytest

from satellite import engine_platform, win_install as wi
# The real check: the satellite conftest neutralizes engine_platform's for
# every other test (it is about this machine, not the engine).
from satellite.engine_platform import windows_runtime_problem as real_check

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "satellite" / "scripts" / "install-windows.ps1"
WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows' own DLLs and PowerShell")
POWERSHELL = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
              / "powershell.exe")
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"


def test_the_installer_and_the_engine_hold_the_same_minimum():
    text = SCRIPT.read_text(encoding="ascii")
    m = re.search(r"\$VcRuntimeMinimum = \[version\]'(\d+)\.(\d+)'", text)
    assert m and (int(m.group(1)), int(m.group(2))) == engine_platform.VC_RUNTIME_MIN
    for name in engine_platform.VC_RUNTIME_DLLS:
        assert f"'{name}'" in text
    assert engine_platform.VC_REDIST_URL in text
    assert wi.VC_RUNTIME_FILES == engine_platform.VC_RUNTIME_DLLS


def _versions(table):
    return lambda path: table.get(Path(path).name + "@" + Path(path).parent.name)


def test_the_engine_says_what_is_missing_and_where_to_get_it(tmp_path):
    py, sysdir = tmp_path / "python", tmp_path / "System32"
    py.mkdir()
    sysdir.mkdir()
    why = real_check(py, sysdir, version_of=lambda p: (14, 44, 0, 0))
    assert why.startswith("Microsoft's Visual C++ runtime is not on this PC (msvcp140.dll, msvcp140_1.dll missing)")
    assert engine_platform.VC_REDIST_URL in why and "Traceback" not in why
    for name in engine_platform.VC_RUNTIME_DLLS:
        (sysdir / name).write_bytes(b"x")
    assert real_check(py, sysdir, version_of=lambda p: (14, 44, 35211, 0)) is None
    old = real_check(py, sysdir, version_of=lambda p: (14, 42, 34438, 0))
    assert old.startswith("Microsoft's Visual C++ runtime on this PC is older than 14.44 (msvcp140.dll 14.42")
    assert real_check(py, sysdir, version_of=lambda p: None).count("unknown") == 2


def test_an_app_local_copy_beside_the_python_is_the_one_that_counts(tmp_path):
    """Windows loads the copy beside python.exe before System32's."""
    py, sysdir = tmp_path / "python", tmp_path / "System32"
    for d in (py, sysdir):
        d.mkdir()
        for name in engine_platform.VC_RUNTIME_DLLS:
            (d / name).write_bytes(b"x")
    ver = {"python": (14, 44, 35211, 0), "System32": (14, 42, 34438, 0)}
    assert real_check(py, sysdir, version_of=lambda p: ver[Path(p).parent.name]) is None
    ver = {"python": (14, 40, 0, 0), "System32": (14, 44, 0, 0)}
    assert "older than 14.44" in real_check(py, sysdir, version_of=lambda p: ver[Path(p).parent.name])


@windows_only
def test_this_pc_s_runtime_is_read_from_the_files_themselves():
    got = real_check(SYSTEM32 / "nowhere", SYSTEM32)
    have = __import__("satellite.win32", fromlist=["x"]).file_version(str(SYSTEM32 / "msvcp140.dll"))
    if have is None:
        assert got and "missing" in got
    elif tuple(have[:2]) < engine_platform.VC_RUNTIME_MIN:
        assert got and f"msvcp140.dll {have[0]}.{have[1]}" in got
    else:
        assert got is None


@windows_only
def test_the_engine_refuses_plainly_at_start(tmp_path, capsys, monkeypatch):
    """No "DLL load failed" traceback: one line, exit 78, the tray's
    reason (not-started.txt)."""
    from satellite import not_started
    from satellite.tests._client_import import import_client

    client = import_client()
    monkeypatch.setattr(engine_platform, "windows_runtime_problem", real_check)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python" / "python.exe"))
    monkeypatch.setenv("SystemRoot", str(tmp_path))          # an empty "System32": nothing there
    cfg = tmp_path / "config.toml"
    cfg.write_text('[device]\nprofile = "windows_pc"\n[satellite]\nroom_id = "den"\n'
                   'server_fingerprint = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"\n', encoding="utf-8")
    assert client.main(["--config", str(cfg)]) == 78
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("domovoi-satellite: not starting: Microsoft's Visual C++ runtime")
    assert "aka.ms/vc14/vc_redist.x64.exe" in err[0]
    assert "Visual C++" in (not_started.read() or "")


# ─── the installer's own functions, taken from the script by PowerShell's parser ──

def _ps(script: str, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, timeout=120)


def _with_functions(body: str, *names: str) -> str:
    return (
        "$ErrorActionPreference = 'Stop'; $t = $null; $e = $null; "
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:SCRIPT, [ref]$t, [ref]$e); "
        + "".join(
            f"$fn = $ast.FindAll({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] "
            f"-and $n.Name -eq '{name}' }}, $true)[0]; . ([ScriptBlock]::Create($fn.Extent.Text)); "
            for name in names)
        + body)


@windows_only
@pytest.mark.parametrize("have,minimum,want", [
    ("both", "14.0", None), ("both", "99.0", "older than 99.0"), ("none", "14.0", "missing"),
    ("one", "14.0", "msvcp140_1.dll missing"),
])
def test_the_installer_s_runtime_check(tmp_path, have, minimum, want):
    d = tmp_path / "sys"
    d.mkdir()
    if have in ("both", "one"):
        shutil.copy(SYSTEM32 / "msvcp140.dll", d)          # a real DLL, with its real version
    if have == "both":
        shutil.copy(SYSTEM32 / "msvcp140_1.dll", d)
    r = _ps(_with_functions("$p = Get-VcRuntimeProblem $env:DIR ([version]$env:MIN); if ($p) { $p } else { 'NONE' }",
                            "Get-VcRuntimeProblem"), env={"SCRIPT": str(SCRIPT), "DIR": str(d), "MIN": minimum})
    out = r.stdout.strip()
    assert r.returncode == 0, r.stderr
    if want is None:
        assert out == "NONE"
    else:
        assert want in out and "aka.ms/vc14/vc_redist.x64.exe" in out and "Nothing was changed" in out, out


@windows_only
def test_the_installer_refuses_before_any_download(tmp_path):
    """The real script, on this PC: with a minimum above what any PC has,
    it stops with exit 6 and its sentence, before the Python is fetched."""
    from urllib.parse import quote

    bundle = tmp_path / "bundle"
    (bundle / "satellite" / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, bundle / "satellite" / "scripts")
    shutil.copy(ROOT / "satellite" / "windows-runtime.json", bundle / "satellite")
    signals = tmp_path / "signals.json"
    signals.write_text(json.dumps({"service": "", "registry": "", "programdata": "", "profile": "", "probe": ""}))
    payload = ("domovoi-room:1?core=" + quote("http://domovoi-test-nowhere.home.arpa:6370", safe="")
               + "&fp=" + quote("SHA256:" + "A" * 43, safe="") + "&room=Den")
    r = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                        str(bundle / "satellite" / "scripts" / "install-windows.ps1"), payload,
                        "-InstallRoot", str(tmp_path / "S"), "-TestCorePcSignals", str(signals),
                        "-TestVcRuntimeMinimum", "99.0"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 6, r.stdout + r.stderr
    assert "Visual C++ runtime on this PC is older than 99.0" in r.stderr
    assert not (bundle / "stage").exists() and "Getting Python" not in r.stdout


@windows_only
def test_smart_app_control_is_read_not_changed_and_said_plainly():
    r = _ps(_with_functions("Get-SmartAppControlState; '---'; "
                            "foreach ($s in 'on', 'evaluation', 'off', 'unknown') { "
                            "$n = Get-SmartAppControlNote $s; if ($n) { $n } else { 'NONE' } }",
                            "Get-SmartAppControlState", "Get-SmartAppControlNote"), env={"SCRIPT": str(SCRIPT)})
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert lines[0] in ("on", "off", "evaluation", "unknown") and lines[1] == "---"
    on, evaluation, off, unknown = lines[2:6]
    assert on.startswith("Smart App Control is on. It may block parts of the satellite")
    assert "evaluation mode" in evaluation and "may turn it on later" in evaluation
    assert off == "NONE" and unknown == "NONE"
    text = SCRIPT.read_text(encoding="ascii")
    assert "Set-ItemProperty" not in text and "New-ItemProperty" not in text, "read only"


# ─── --local-vc-runtime (not yet available: the lock pins none) ───────────

def test_the_lock_pins_no_runtime_yet_and_the_flag_says_so(tmp_path):
    lock = json.loads((ROOT / "satellite" / "windows-runtime.json").read_text(encoding="utf-8"))
    assert wi.vc_runtime_entries(lock) == [], "shipping Microsoft's DLLs is the owner's decision"
    (tmp_path / "python").mkdir()
    with pytest.raises(wi.InstallError, match="no pinned Visual C\\+\\+ runtime files yet") as e:
        wi.place_vc_runtime(tmp_path, lock, core=None, opener=lambda *a, **k: pytest.fail("fetched"))
    assert e.value.code == 6


class _Answer(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_with_pinned_files_they_land_beside_the_python_checked(tmp_path):
    bodies = {"msvcp140.dll": b"MZ runtime one", "msvcp140_1.dll": b"MZ runtime two"}
    lock = {"vc_runtime": [{"file": n, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b),
                            "url": f"https://example.invalid/vc/{n}"} for n, b in bodies.items()]}
    (tmp_path / "python").mkdir()
    seen = []

    def opener(url, timeout=None):
        seen.append(url)
        return _Answer(bodies[url.rsplit("/", 1)[1]])

    wi.place_vc_runtime(tmp_path, lock, core=None, opener=opener)
    for n, b in bodies.items():
        assert (tmp_path / "python" / n).read_bytes() == b
    # A tampered body is refused, nothing placed.
    (tmp_path / "python" / "msvcp140.dll").unlink()
    shutil.rmtree(tmp_path / "cache")
    with pytest.raises(wi.InstallError, match="could not get msvcp140.dll"):
        wi.place_vc_runtime(tmp_path, lock, core=None, opener=lambda url, timeout=None: _Answer(b"MZ evil one!!!"))
    assert not (tmp_path / "python" / "msvcp140.dll").exists()


@pytest.mark.parametrize("entry", [
    {"file": "evil.dll", "sha256": "0" * 64, "size": 1, "url": "https://x/evil.dll"},
    {"file": "msvcp140.dll", "sha256": "nope", "size": 1, "url": "https://x/msvcp140.dll"},
    {"file": "msvcp140.dll", "sha256": "0" * 64, "size": 1, "url": "http://x/msvcp140.dll"},
    {"file": "msvcp140.dll", "sha256": "0" * 64, "size": 0, "url": "https://x/msvcp140.dll"},
])
def test_a_malformed_runtime_entry_is_refused(entry):
    with pytest.raises(wi.InstallError, match="malformed"):
        wi.vc_runtime_entries({"vc_runtime": [entry]})


def test_the_flag_is_off_unless_given():
    text = SCRIPT.read_text(encoding="ascii")
    assert "if ($LocalVcRuntime) { $argv += '--local-vc-runtime' }" in text
    assert "if (-not $LocalVcRuntime) {" in text, "the system-wide check runs unless the flag is given"
