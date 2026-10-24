"""Microsoft's C++ runtime shipped with the Windows satellite, and Smart App
Control.

onnxruntime 1.31's Windows wheel (the wake word) imports msvcp140.dll and
msvcp140_1.dll, at least the 14.44 it was built with, and carries neither;
Windows doesn't always have them (this test PC's own are 14.42). Owner's
decision 2026-10-14: the satellite ships both beside its python.exe, taken
from the pinned msvc-runtime wheel (Microsoft's unmodified, signed files),
each DLL held to its own SHA-256. The engine's check at start is the
fallback, for an install whose copies have gone. Smart App Control's state
is read (never changed) and said plainly. Scratch folders only.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from satellite import engine_platform, win_install as wi
# The real check: the satellite conftest neutralizes engine_platform's for
# every other test (it is about this machine, not the engine).
from satellite.engine_platform import windows_runtime_problem as real_check

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "satellite" / "scripts" / "install-windows.ps1"
LOCK = json.loads((ROOT / "satellite" / "windows-runtime.json").read_text(encoding="utf-8"))
WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows' own DLLs and PowerShell")
POWERSHELL = (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
              / "powershell.exe")


# ─── the lock ─────────────────────────────────────────────────────────────

def test_the_lock_pins_microsoft_s_runtime_14_44_or_newer():
    vc = LOCK["vc_runtime"]
    assert vc["name"] == "msvc-runtime" and vc["file"].endswith("-cp314-cp314-win_amd64.whl")
    assert vc["url"].startswith("https://files.pythonhosted.org/packages/") and vc["url"].endswith("/" + vc["file"])
    assert "Distributable Code" in vc["license"] and "unmodified" in vc["license"]
    dlls = wi.vc_runtime_dlls(LOCK)
    assert tuple(sorted(d["file"] for d in dlls)) == tuple(sorted(engine_platform.VC_RUNTIME_DLLS))
    for d in dlls:
        major, minor = (int(p) for p in d["version"].split(".")[:2])
        assert (major, minor) >= engine_platform.VC_RUNTIME_MIN, d
    assert wi.VC_RUNTIME_FILES == engine_platform.VC_RUNTIME_DLLS
    # The lock tool pins the same package and the same DLLs.
    import runpy

    tool = runpy.run_path(str(ROOT / "scripts" / "dev" / "windows_runtime_lock.py"), run_name="lock_tool")
    assert tool["VC_RUNTIME"] == (vc["name"], vc["version"])
    assert tool["VC_RUNTIME_DLLS"] == engine_platform.VC_RUNTIME_DLLS
    assert tool["VC_RUNTIME_MIN"] == engine_platform.VC_RUNTIME_MIN


def test_the_core_caches_the_runtime_wheel_like_the_others():
    from domovoi import satellite_windows

    core = satellite_windows.load_lock(ROOT / "satellite" / "windows-runtime.json")
    assert LOCK["vc_runtime"]["file"] in [e["file"] for e in satellite_windows.entries(core)]


@pytest.mark.parametrize("change", [
    lambda vc: vc["dlls"].pop(),
    lambda vc: vc["dlls"][0].update(file="evil.dll"),
    lambda vc: vc["dlls"][0].update(member="msvc_runtime-14.44.35112.data/data/Scripts/msvcp140.dll"),
    lambda vc: vc["dlls"][0].update(member="../../msvcp140.dll"),
    lambda vc: vc["dlls"][0].update(sha256="nope"),
    lambda vc: vc["dlls"][0].update(size=0),
])
def test_a_malformed_runtime_pin_is_refused(change):
    doc = json.loads(json.dumps(LOCK))
    change(doc["vc_runtime"])
    with pytest.raises(wi.InstallError):
        wi.vc_runtime_dlls(doc)
    with pytest.raises(wi.InstallError, match="pins no Microsoft C\\+\\+ runtime"):
        wi.vc_runtime_dlls({})


# ─── placing them ─────────────────────────────────────────────────────────

def _fake_wheel(tmp_path: Path, bodies: dict[str, bytes]) -> dict:
    """A lock whose vc_runtime points at a wheel holding ``bodies``."""
    doc = json.loads(json.dumps(LOCK))
    vc = doc["vc_runtime"]
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    with zipfile.ZipFile(wheels / vc["file"], "w") as z:
        for d in vc["dlls"]:
            z.writestr(d["member"], bodies.get(d["file"], b""))
    for d in vc["dlls"]:
        body = bodies.get(d["file"], b"")
        d.update(sha256=hashlib.sha256(body).hexdigest(), size=len(body))
    return doc


def test_the_dlls_land_beside_the_python_each_checked(tmp_path):
    bodies = {"msvcp140.dll": b"MZ runtime one", "msvcp140_1.dll": b"MZ runtime two"}
    doc = _fake_wheel(tmp_path, bodies)
    (tmp_path / "python").mkdir()
    wi.place_vc_runtime(tmp_path, doc, tmp_path / "wheels")
    for name, body in bodies.items():
        assert (tmp_path / "python" / name).read_bytes() == body


def test_a_member_that_is_not_the_pinned_file_is_refused(tmp_path):
    doc = _fake_wheel(tmp_path, {"msvcp140.dll": b"MZ one", "msvcp140_1.dll": b"MZ two"})
    doc["vc_runtime"]["dlls"][0]["sha256"] = "0" * 64
    (tmp_path / "python").mkdir()
    with pytest.raises(wi.InstallError, match="not the pinned file"):
        wi.place_vc_runtime(tmp_path, doc, tmp_path / "wheels")
    assert not (tmp_path / "python" / "msvcp140.dll").exists()


def test_the_install_fetches_the_runtime_wheel_and_places_it_before_pip(tmp_path, monkeypatch):
    """The order inside install(): fetched with the wheels (core, then
    PyPI, held to its pin), the DLLs placed, then pip."""
    from satellite import enrolment, server_identity  # noqa: F401

    calls = []
    monkeypatch.setattr(enrolment, "verify", lambda e, opener=None, timeout=None: e.fp)
    monkeypatch.setattr(wi, "stop_running", lambda root: None)
    monkeypatch.setattr(wi, "protect_root", lambda root: None)
    monkeypatch.setattr(wi, "replace_dir", lambda src, dest: dest.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(wi, "write_pth", lambda *a, **k: None)
    monkeypatch.setattr(wi, "fetch_pinned", lambda entry, d, **k: calls.append(("fetch", entry["file"])) or "core")
    monkeypatch.setattr(wi, "place_vc_runtime", lambda root, lock, d: calls.append(("place",)))

    def pip(root, d, lock, run=None):
        calls.append(("pip",))
        raise wi.InstallError("stop here", 9)

    monkeypatch.setattr(wi, "pip_install", pip)
    payload = enrolment.build("http://192.168.1.20:6370", "SHA256:" + "A" * 43, "Den")
    args = __import__("types").SimpleNamespace(
        payload=payload, bundle=str(ROOT), staged_python=str(tmp_path / "py"), root=str(tmp_path / "root"),
        permissions="ask", power="no", yes=True, no_start=True, wait=0, registry_file=str(tmp_path / "r.json"),
        profile=str(tmp_path))
    with pytest.raises(wi.InstallError, match="stop here"):
        wi.install(args)
    fetched = [c[1] for c in calls if c[0] == "fetch"]
    assert fetched[-1] == LOCK["vc_runtime"]["file"]
    assert calls[-2:] == [("place",), ("pip",)]


# ─── the engine's fallback check ──────────────────────────────────────────

def test_the_engine_says_what_is_missing_and_what_to_do(tmp_path):
    py, sysdir = tmp_path / "python", tmp_path / "System32"
    py.mkdir()
    sysdir.mkdir()
    why = real_check(py, sysdir, version_of=lambda p: (14, 44, 0, 0))
    assert why.startswith("Microsoft's Visual C++ runtime is not on this PC (msvcp140.dll, msvcp140_1.dll missing)")
    assert "the copies the room software installs beside its Python are gone" in why
    assert "Paste the install command" in why and engine_platform.VC_REDIST_URL in why
    for name in engine_platform.VC_RUNTIME_DLLS:
        (sysdir / name).write_bytes(b"x")
    assert real_check(py, sysdir, version_of=lambda p: (14, 44, 35211, 0)) is None
    assert "older than 14.44 (msvcp140.dll 14.42" in real_check(py, sysdir, version_of=lambda p: (14, 42, 34438, 0))


def test_the_shipped_copies_beside_the_python_are_the_ones_that_count(tmp_path):
    """Windows loads the copy beside python.exe before System32's: this
    PC's System32 has 14.42, the shipped copies are 14.44."""
    py, sysdir = tmp_path / "python", tmp_path / "System32"
    for d in (py, sysdir):
        d.mkdir()
        for name in engine_platform.VC_RUNTIME_DLLS:
            (d / name).write_bytes(b"x")
    ver = {"python": (14, 44, 35211, 0), "System32": (14, 42, 34438, 0)}
    assert real_check(py, sysdir, version_of=lambda p: ver[Path(p).parent.name]) is None


@windows_only
def test_the_engine_refuses_plainly_at_start_when_its_copies_are_gone(tmp_path, capsys, monkeypatch):
    from satellite import not_started
    from satellite.tests._client_import import import_client

    client = import_client()
    monkeypatch.setattr(engine_platform, "windows_runtime_problem", real_check)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python" / "python.exe"))
    monkeypatch.setenv("SystemRoot", str(tmp_path))          # an empty "System32"
    cfg = tmp_path / "config.toml"
    cfg.write_text('[device]\nprofile = "windows_pc"\n[satellite]\nroom_id = "den"\n'
                   'server_fingerprint = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"\n', encoding="utf-8")
    assert client.main(["--config", str(cfg)]) == 78
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and err[0].startswith("domovoi-satellite: not starting: Microsoft's Visual C++ runtime")
    assert "Visual C++" in (not_started.read() or "")


# ─── Smart App Control ────────────────────────────────────────────────────

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
def test_smart_app_control_is_read_not_changed_and_said_plainly():
    r = _ps(_with_functions("Get-SmartAppControlState; '---'; "
                            "foreach ($s in 'on', 'evaluation', 'off', 'unknown') { "
                            "$n = Get-SmartAppControlNote $s; if ($n) { $n } else { 'NONE' } }",
                            "Get-SmartAppControlState", "Get-SmartAppControlNote"), env={"SCRIPT": str(SCRIPT)})
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert lines[0] in ("on", "off", "evaluation", "unknown") and lines[1] == "---"
    on, evaluation, off, unknown = lines[2:6]
    assert on.startswith("Smart App Control is on. It may block parts of the room software")
    assert "evaluation mode" in evaluation and "may turn it on later" in evaluation
    assert off == "NONE" and unknown == "NONE"
    text = SCRIPT.read_text(encoding="ascii")
    assert "Set-ItemProperty" not in text and "New-ItemProperty" not in text, "read only"


def test_no_flag_and_no_system_wide_refusal_left_in_the_installer():
    text = SCRIPT.read_text(encoding="ascii")
    assert "VcRuntime" not in text and "--local-vc-runtime" not in (ROOT / "satellite" / "win_install.py").read_text(
        encoding="utf-8")
