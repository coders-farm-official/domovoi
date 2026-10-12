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
    if p.is_file() and "build" not in p.relative_to(WIN).parts and p.suffix in {".cs", ".iss", ".ps1", ".xml", ".py", ".txt"}
)
ART = WIN / "installer" / "art"

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
    for n in ("DomovoiSetup.iss", "pins.iss", "build.ps1", "Tests.cs", "Host.cs", "Uninstall.cs", "UninstallPlan.cs", "WslList.cs", "Processes.cs",
              "PortScan.cs", "ServiceWait.cs", "ProfileTask.cs", "Logs.cs", "make_art.py"):
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


def _provision_bundled() -> bool:
    return re.search(r"^#define ProvisionBundled\s*$", PINS.read_text(encoding="ascii"), re.M) is not None


def test_provision_pin_is_the_file_setup_ships_or_fetches():
    """Bundled: the pin is this tree's scripts/linux/provision.sh, packed
    into Setup (and fetched from nowhere). Not bundled: the file at the
    pinned commit, downloaded."""
    s = SCRIPT.read_text(encoding="ascii")
    if not _provision_bundled():
        pytest.skip("provision.sh is fetched from the pinned commit (the next test)")
    assert _pins()["ProvisionSource"] == "..\\..\\scripts\\linux\\provision.sh"
    shipped = (WIN / "installer" / _pins()["ProvisionSource"].replace("\\", "/")).resolve()
    assert shipped == (REPO_ROOT / "scripts" / "linux" / "provision.sh").resolve()
    data = shipped.read_bytes()
    assert b"\r" not in data, "provision.sh must ship with LF endings"
    assert hashlib.sha256(data).hexdigest() == _pins()["ProvisionSha256"], "pins.iss ProvisionSha256 is not this tree's provision.sh"
    assert '#ifdef ProvisionBundled\nSource: "{#ProvisionSource}"; Flags: dontcopy\n#endif' in s.replace(
        "; provision.sh shipped inside Setup (pins.iss), extracted to {tmp} before\n; the downloads and handed to DomovoiHost by the helper's job step.\n", "")
    assert "ExtractTemporaryFile('provision.sh');" in s
    # The installer's fixes need these; the pinned commit alone may not have them.
    text = data.decode("utf-8")
    assert "\n  --stop-before STEP " in text and "start_diagnostics()" in text


def test_provision_pin_is_the_file_at_the_pinned_commit():
    if _provision_bundled():
        pytest.skip("provision.sh ships inside Setup (the test above)")
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
    # The dashboard the finish page opens: localhost, the AGENTS.md port,
    # with this build's mark so an old cache-first service worker can't
    # answer it from its cache (owner fixes 2026-10-12, item 5).
    assert "DashboardUrl = 'http://localhost:6369'" in s
    assert "OpenUrl = 'http://localhost:6369/?installed={#Copy(DomovoiCommit, 1, 7)}-{#BuildStamp}'" in s
    assert "ShellExecAsOriginalUser('open', OpenUrl," in s and "ShellExecAsOriginalUser('open', DashboardUrl," not in s
    assert "PrivilegesRequired=admin" in s and "ArchitecturesAllowed=x64os" in s and "MinVersion=10.0.22621" in s


def test_the_current_service_worker_takes_over_at_once():
    """Item 5: a browser with an older dashboard worker gets the current one
    as soon as it opens a page the old cache can't answer."""
    sw = (REPO_ROOT / "web" / "static" / "sw.js").read_text(encoding="utf-8")
    assert "self.skipWaiting()" in sw and "self.clients.claim()" in sw
    assert "shellNetworkFirst" in sw


def _ico_sizes(data: bytes) -> list[int]:
    import struct

    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1)
    sizes = []
    for i in range(count):
        w, h = struct.unpack_from("<BB", data, 6 + 16 * i)
        assert w == h
        sizes.append(w or 256)
    return sizes


def _png_size(data: bytes) -> tuple[int, int]:
    import struct

    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_the_artwork_is_domovois_and_complete():
    """Item 1: Setup, the uninstaller and Installed apps carry Domovoi's
    icon (16-256 px), and the wizard its pictures at every size Inno Setup
    6.6+ uses; all made by make_art.py, which the script and build use."""
    s = SCRIPT.read_text(encoding="ascii")
    assert "SetupIconFile=art\\domovoi.ico" in s
    assert "UninstallDisplayIcon={app}\\domovoi.ico" in s
    assert 'Source: "art\\domovoi.ico"; DestDir: "{app}"' in s
    assert _ico_sizes((ART / "domovoi.ico").read_bytes()) == [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    wizard = [(202, 386), (269, 515), (336, 643), (403, 772), (430, 824), (498, 953), (534, 1022)]
    small = [58, 77, 97, 116, 124, 143, 159]
    assert "WizardImageFile=" + ",".join(f"art\\wizard-{w}x{h}.png" for w, h in wizard) in s
    assert "WizardSmallImageFile=" + ",".join(f"art\\small-{n}.png" for n in small) in s
    for w, h in wizard:
        assert _png_size((ART / f"wizard-{w}x{h}.png").read_bytes()) == (w, h)
    for n in small:
        assert _png_size((ART / f"small-{n}.png").read_bytes()) == (n, n)
    made = {p.name for p in ART.iterdir() if p.suffix in (".png", ".ico")}
    assert made == {"domovoi.ico", *(f"wizard-{w}x{h}.png" for w, h in wizard), *(f"small-{n}.png" for n in small)}
    assert "installer\\art\\domovoi.ico" in BUILD.read_text(encoding="ascii")


def test_the_artwork_is_what_make_art_makes():
    """No hand-edited binaries: the files are make_art.py's output from
    docs/assets/domovoi-icon.png (compared pixel for pixel), with the Pillow
    version requirements.txt pins."""
    pil = pytest.importorskip("PIL")
    if pil.__version__ != "12.3.0":
        pytest.skip(f"make_art.py needs Pillow 12.3.0, this venv has {pil.__version__}")
    proc = subprocess.run([sys.executable, str(ART / "make_art.py"), "--check"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_doc_names_every_machine_change():
    doc = DOC.read_text(encoding="utf-8")
    for needle in (
        "domovoi-svc", "DomovoiHost", "C:\\ProgramData\\Domovoi", "C:\\Program Files\\Domovoi",
        "Domovoi-Dashboard-6369", "SeServiceLogonRight", ".wslconfig", "C:\\Users\\Public\\Domovoi",
        "SpecialAccounts\\UserList", "trust boundary", "DHCP reservation", "HKLM\\SOFTWARE\\Domovoi",
        "Smart App Control", "VerifiedAndReputablePolicyState", "Windows protected your PC", "More info", "Run anyway",
        "every WSL VM on the PC", "Domovoi-kept-", "The **third** run",
        "Copy logs", "Documents\\Domovoi logs", "DomovoiRemoveProfile", "wsl --shutdown", "as `domovoi-svc`",
        "?installed=", "--stop-before",
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


def test_uninstall_shows_its_steps_and_no_dead_advice():
    """Items 7 and 9: the uninstaller follows the helper's progress lines as
    they come (ExecAndLogOutput), and never tells the user to run an
    uninstaller that has already removed itself."""
    s = SCRIPT.read_text(encoding="ascii")
    body = s[s.index("procedure CurUninstallStepChanged"):]
    assert "ExecAndLogOutput(HelperExe, 'uninstall'" in body and "@UninstallOutput" in body
    assert "ExecAndCaptureOutput(HelperExe, 'uninstall'" not in s
    assert "'progress='" in s and "ProgressBar.Position" in s
    four = body[body.index("if Code = 4 then"):body.index("if Code = 3 then")]
    assert "again" not in four, four
    for src in (WIN / "helper" / "Uninstall.cs", WIN / "common" / "UninstallPlan.cs"):
        text = src.read_text(encoding="ascii")
        assert "run the uninstaller again" not in text and "then run the uninstaller" not in text, src.name


def test_every_error_offers_copy_logs():
    """Item 4: the install's error dialogs, the finish page after a
    failure, and the uninstaller's messages."""
    s = SCRIPT.read_text(encoding="ascii")
    assert s.count("['Retry', 'Copy logs', 'Stop Setup']") == 1 and s.count("RetryOrStop(") >= 3
    assert "CopyLogsButton.Caption := 'Copy logs'" in s
    assert "['OK', 'Copy logs']" in s
    assert "TaskDialogMsgBox(Title + ': it did not work'" not in s
    assert s.count("copy-logs --kind ") == 1


def test_copy_logs_never_opens_a_folder_elevated():
    """Review of 2026-10-12, item 1: the elevated uninstaller only names the
    folder; Setup opens it as the original user, and only a real folder
    (not a link), with a trailing backslash."""
    s = SCRIPT.read_text(encoding="ascii")
    body = s[s.index("procedure CopyLogs"):]
    body = body[: body.index("\nend;")]
    assert "ShellExec(" not in body.replace("ShellExecAsOriginalUser(", "")
    assert "if not IsUninstaller and IsPlainDir(Folder) then" in body
    assert "ShellExecAsOriginalUser('open', AddBackslash(Folder)," in body
    assert "((A and $400) = 0)" in s  # IsPlainDir: no reparse point


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
