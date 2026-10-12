"""The Windows installer (windows/): its unit tests, and the files it must
agree with.

The installer is C# (.NET Framework 4.8.1, built with the compiler that
ships in Windows) and an Inno Setup 6 script, so its own tests are C#
too (windows/tests/Tests.cs, run by windows/build.ps1). This wrapper runs
that build on Windows and keeps the platform-neutral rules everywhere:
ASCII and LF in every source file, the pins well formed, provision.sh's
pin equal to the file at the pinned commit, and the repo's naming rules.

Nothing here installs anything: build.ps1 only writes windows/build/.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WIN = REPO_ROOT / "windows"
PINS = WIN / "installer" / "pins.iss"
SCRIPT = WIN / "installer" / "DomovoiSetup.iss"
BUILD = WIN / "build.ps1"
DOC = REPO_ROOT / "docs" / "WINDOWS_HOST.md"

SOURCES = sorted(
    p
    for p in WIN.rglob("*")
    if p.is_file() and "build" not in p.relative_to(WIN).parts and p.suffix in {".cs", ".iss", ".ps1", ".xml"}
)

POWERSHELL = shutil.which("powershell.exe") if sys.platform == "win32" else None
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
requires_windows_build = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None or not CSC.is_file(),
    reason="the installer builds on Windows only (in-box .NET Framework C# compiler)",
)


def _pins() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in PINS.read_text(encoding="ascii").splitlines():
        m = re.match(r'^#define (\w+) "([^"]*)"$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_sources_exist():
    names = {p.name for p in SOURCES}
    for n in ("DomovoiSetup.iss", "pins.iss", "build.ps1", "Tests.cs", "Host.cs", "Uninstall.cs", "UninstallPlan.cs", "WslList.cs", "Processes.cs"):
        assert n in names, n


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_sources_are_ascii_with_lf(path: Path):
    """LF like the rest of the repo (csc, ISCC and PowerShell 5.1 all read
    it), and ASCII, so no tool guesses an encoding."""
    data = path.read_bytes()
    assert b"\r" not in data, f"{path.name} has CR characters"
    data.decode("ascii")


def test_no_reserved_names():
    """AGENTS.md conventions 1 and 2, over the installer and its doc."""
    banned = re.compile(r"har[l]ey|ric[h]ard|orche[s]trator|yout[u]be|yt[-_]?d[l]p", re.I)
    for path in [*SOURCES, DOC]:
        text = path.read_text(encoding="utf-8")
        assert not banned.search(text), f"{path.name} names something the repo must not"


def test_pins_are_well_formed():
    p = _pins()
    assert re.fullmatch(r"[0-9a-f]{40}", p["DomovoiCommit"])
    for key in ("ProvisionSha256", "UbuntuSha256", "WslMsiSha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", p[key]), key
    assert p["DomovoiRepoUrl"] == "https://github.com/coders-farm-official/domovoi"
    text = PINS.read_text(encoding="ascii")
    # provision.sh comes from the pinned commit, never a branch.
    assert '"https://raw.githubusercontent.com/coders-farm-official/domovoi/" + DomovoiCommit + "/scripts/linux/provision.sh"' in text
    assert p["UbuntuName"] == "ubuntu-26.04.1-wsl-amd64.wsl"


def test_provision_pin_is_the_file_at_the_pinned_commit():
    commit = _pins()["DomovoiCommit"]
    git = shutil.which("git")
    if not git:
        pytest.skip("no git")
    proc = subprocess.run(
        [git, "-C", str(REPO_ROOT), "cat-file", "-p", f"{commit}:scripts/linux/provision.sh"],
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"commit {commit[:7]} is not in this clone")
    assert hashlib.sha256(proc.stdout).hexdigest() == _pins()["ProvisionSha256"]


def test_the_script_uses_the_pins_and_the_ports():
    s = SCRIPT.read_text(encoding="ascii")
    for key in ("ProvisionUrl", "ProvisionSha256", "UbuntuUrl", "UbuntuSha256", "WslMsiUrl", "WslMsiSha256", "DomovoiCommit"):
        assert "{#" + key + "}" in s, key
    assert '#include "pins.iss"' in s
    # The dashboard the finish page opens: localhost, the AGENTS.md port.
    assert "DashboardUrl = 'http://localhost:6369'" in s
    assert "PrivilegesRequired=admin" in s and "ArchitecturesAllowed=x64os" in s and "MinVersion=10.0.22621" in s


def test_the_doc_names_every_machine_change():
    doc = DOC.read_text(encoding="utf-8")
    for needle in (
        "domovoi-svc", "DomovoiHost", "C:\\ProgramData\\Domovoi", "C:\\Program Files\\Domovoi",
        "Domovoi-Dashboard-6369", "SeServiceLogonRight", ".wslconfig", "C:\\Users\\Public\\Domovoi",
        "SpecialAccounts\\UserList", "trust boundary", "DHCP reservation", "HKLM\\SOFTWARE\\Domovoi",
        "Smart App Control", "VerifiedAndReputablePolicyState", "Windows protected your PC", "More info", "Run anyway",
        "every WSL VM on the PC", "Domovoi-kept-", "The **third** run",
    ):
        assert needle in doc, needle


def test_nothing_promises_a_signature():
    """Code signing is on hold (owner, 2026-10-11): no "signed helper",
    "signed installer" or "signed binaries" anywhere in the installer or its
    doc ("unsigned" is fine)."""
    claim = re.compile(r"(?<![A-Za-z])signed (helper|installer|binar|exe|setup|build)", re.I)
    for path in [*SOURCES, DOC]:
        text = path.read_text(encoding="utf-8")
        assert not claim.search(text), f"{path.name}: {claim.search(text).group(0)!r}"


def test_smart_app_control_is_checked_before_anything_else():
    s = SCRIPT.read_text(encoding="ascii")
    init = s[s.index("function InitializeSetup"):]
    init = init[: init.index("\nend;")]
    assert "VerifiedAndReputablePolicyState" in init and "(Sac = 1)" in init
    assert init.index("VerifiedAndReputablePolicyState") < init.index("ExtractTemporaryFile")


def test_uninstall_aborts_unless_the_helper_finished():
    """Review finding 1: any non-zero exit of the helper's uninstall stops
    Inno before it deletes the program and the Apps entry."""
    s = SCRIPT.read_text(encoding="ascii")
    body = s[s.index("procedure CurUninstallStepChanged"):]
    assert "if Code = 0 then\n    Exit;" in body
    assert body.rstrip().endswith("Abort;\nend;")


@requires_windows_build
def test_build_script_parses():
    cmd = (
        "$e = $null; $t = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{BUILD}', [ref]$t, [ref]$e); "
        "if ($e.Count) { $e | ForEach-Object { $_.Message }; exit 1 }"
    )
    proc = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@requires_windows_build
def test_build_and_unit_tests():
    """Compiles DomovoiHost.exe and DomovoiSetupHelper.exe and runs the C#
    unit tests; also compiles DomovoiSetup.exe when Inno Setup 6 is here."""
    iscc = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
    ]
    args = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(BUILD)]
    if not any(p.is_file() for p in iscc):
        args.append("-SkipInstaller")
    proc = subprocess.run(args, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-3000:]
    assert re.search(r"^\d+ passed, 0 failed$", proc.stdout, re.M), proc.stdout[-3000:]
