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
              "PortScan.cs", "ServiceWait.cs", "ProfileTask.cs", "Logs.cs", "make_art.py",
              "TrayApp.cs", "CardWriter.cs", "BootText.cs", "CardZip.cs", "TrayTests.cs", "tray.manifest.xml"):
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
    # GitHub raw serves the blob as committed: these exact bytes.
    assert hashlib.sha256(proc.stdout).hexdigest() == _pins()["ProvisionSha256"]
    assert b"\r" not in proc.stdout, "provision.sh at the pinned commit must have LF endings"
    # What the installer relies on (the port gate before the start, and the
    # start-failure diagnostics in provision.log); the reason fb62fa4's
    # builds carried their own copy.
    text = proc.stdout.decode("utf-8")
    assert "\n  --stop-before STEP " in text and "start_diagnostics()" in text


def _git_bytes(*args: str) -> bytes | None:
    git = shutil.which("git")
    if not git:
        return None
    proc = subprocess.run([git, "-C", str(REPO_ROOT), *args], capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


def _needed_files(script: str) -> set[str]:
    """The repository files a provision.sh reads from the checkout it
    clones: its helpers and units under scripts/linux, the locks, the
    compose file, the room image's Dockerfile, pyproject.toml and the doc
    that pins Ollama."""
    need = {m for m in re.findall(r"scripts/linux/(?!tests/)[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?\.(?:sh|conf|service)", script)}
    m = re.search(r"^SHIPPED_UNITS=\(([^)]*)\)", script, re.M)
    for unit in (m.group(1).split() if m else []):
        need.add(f"scripts/linux/units/{unit}")
    for name in re.findall(r"^(?:NODEPS_)?LOCK_NAME=(\S+)$", script, re.M):
        need.add(name)
    for path in re.findall(r"\$CHECKOUT/((?:domovoi|docs)/[A-Za-z0-9_-][A-Za-z0-9_.-]*|pyproject\.toml)\b", script):
        need.add(path)
    need.discard("domovoi/.env")      # made by the env step, not shipped
    return need


def _missing_at(commit: str, script: str) -> list[str]:
    return sorted(p for p in _needed_files(script) if _git_bytes("cat-file", "-e", f"{commit}:{p}") is None)


def test_the_pins_move_together():
    """DomovoiCommit, ProvisionSha256 and the provision.sh Setup runs belong
    to each other (delta security review 2026-10-16, N9): the script Setup
    runs hashes to the pin, and every file that script reads from the
    checkout exists at the pinned commit (batch 11's provision.sh needs
    scripts/linux/units/journald-domovoi.conf, which rc1's 4d6b414 lacks).
    windows\\build.ps1 -Pin moves them in one go."""
    commit = _pins()["DomovoiCommit"]
    if _git_bytes("cat-file", "-e", f"{commit}^{{commit}}") is None:
        pytest.skip(f"commit {commit[:7]} is not in this clone")
    if _provision_bundled():
        script = (REPO_ROOT / "scripts" / "linux" / "provision.sh").read_bytes()
    else:
        script = _git_bytes("cat-file", "blob", f"{commit}:scripts/linux/provision.sh")
    assert script is not None
    assert hashlib.sha256(script).hexdigest() == _pins()["ProvisionSha256"]
    text = script.decode("utf-8")
    assert len(_needed_files(text)) >= 10, sorted(_needed_files(text))
    assert _missing_at(commit, text) == [], f"files provision.sh reads that {commit[:7]} lacks"


def test_a_bundled_script_ahead_of_its_commit_is_caught():
    """The rule above, on the pair that broke after batch 11: this tree's
    provision.sh with rc1's commit."""
    rc1 = "4d6b4147f7d99d84fd0fcac2fdcb769c164eec92"
    if _git_bytes("cat-file", "-e", f"{rc1}^{{commit}}") is None:
        pytest.skip("rc1's commit is not in this clone")
    tree = (REPO_ROOT / "scripts" / "linux" / "provision.sh").read_text(encoding="utf-8")
    assert "scripts/linux/units/journald-domovoi.conf" in _missing_at(rc1, tree)


def test_the_channel_is_stable_only_for_a_release():
    """Setup follows stable on a release build (the household gets releases
    only); a pin that isn't a release's commit follows main."""
    pins = _pins()
    assert pins["DomovoiChannel"] in ("stable", "main")
    if pins["DomovoiChannel"] != "stable":
        return
    commit = pins["DomovoiCommit"]
    tags = _git_bytes("tag", "--points-at", commit, "--list", "v[0-9]*")
    if tags is None or _git_bytes("cat-file", "-e", f"{commit}^{{commit}}") is None:
        pytest.skip(f"commit {commit[:7]} is not in this clone")
    assert tags.strip(), f"DomovoiChannel is stable, but no v* tag names {commit[:7]}"


def test_the_job_follows_the_channel():
    s = SCRIPT.read_text(encoding="ascii")
    assert "--ref {#DomovoiCommit} --track-branch {#DomovoiChannel} --lan-ip auto" in s
    assert '#if DomovoiChannel == "stable"' in s and "{#ChannelWords}" in s


def test_the_changes_page_leads_with_the_changes_and_ends_with_the_undo():
    """Owner feedback 2026-10-16: the changes first; the way to undo them
    last."""
    s = SCRIPT.read_text(encoding="ascii")
    body = s[s.index("function ConsentText: String;"):]
    body = body[: body.index("\nend;")]
    assert "S := '';\n  N := 1;" in body
    assert "undoes every change below" not in body
    last = [line.strip() for line in body.splitlines() if line.strip().startswith("S := S +")][-1]
    assert last == "S := S + 'You can undo all of this later from Settings > Apps > Installed apps > Domovoi.';"


requires_pin_tool = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None or shutil.which("git") is None,
    reason="build.ps1 -Pin runs in Windows PowerShell, with git",
)


def _pin_tool(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess, Path]:
    copy = tmp_path / "pins.iss"
    copy.write_bytes(PINS.read_bytes())
    proc = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(BUILD),
         *args, "-PinOnly", "-PinsFile", str(copy)],
        capture_output=True, text=True, timeout=120,
    )
    return proc, copy


@requires_pin_tool
def test_the_pin_tool_reproduces_the_pins(tmp_path):
    """Moving the pins to the commit and channel they already name changes
    nothing: every pin in pins.iss is what build.ps1 -Pin derives from
    DomovoiCommit."""
    pins = _pins()
    if _git_bytes("cat-file", "-e", f"{pins['DomovoiCommit']}^{{commit}}") is None:
        pytest.skip("the pinned commit is not in this clone")
    proc, copy = _pin_tool(tmp_path, "-Pin", pins["DomovoiCommit"], "-Channel", pins["DomovoiChannel"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert copy.read_bytes() == PINS.read_bytes()


@requires_pin_tool
def test_the_pin_tool_pins_a_release_tag(tmp_path):
    rc1 = "4d6b4147f7d99d84fd0fcac2fdcb769c164eec92"
    if _git_bytes("rev-parse", "--verify", "--quiet", "refs/tags/v1.0.0-rc1") is None:
        pytest.skip("the v1.0.0-rc1 tag is not in this clone")
    proc, copy = _pin_tool(tmp_path, "-Pin", "v1.0.0-rc1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    text = copy.read_text(encoding="ascii")
    assert f'#define DomovoiCommit "{rc1}"' in text
    assert '#define DomovoiChannel "stable"' in text
    blob = _git_bytes("cat-file", "blob", f"{rc1}:scripts/linux/provision.sh")
    assert f'#define ProvisionSha256 "{hashlib.sha256(blob).hexdigest()}"' in text
    assert "\n; #define ProvisionBundled\n" in text and "\n#define DashboardTabRoutes\n" in text
    assert b"\r" not in copy.read_bytes()


@requires_pin_tool
def test_the_pin_tool_refuses_a_branch_and_a_stable_non_release(tmp_path):
    proc, copy = _pin_tool(tmp_path, "-Pin", "main")
    assert proc.returncode != 0 and "never a branch" in proc.stdout + proc.stderr
    assert copy.read_bytes() == PINS.read_bytes()
    pinned = _pins()["DomovoiCommit"]
    if _git_bytes("cat-file", "-e", f"{pinned}^{{commit}}") is None:
        pytest.skip("the pinned commit is not in this clone")
    if (_git_bytes("tag", "--points-at", pinned, "--list", "v[0-9]*") or b"").strip():
        pytest.skip("the pinned commit is a release")
    proc, copy = _pin_tool(tmp_path, "-Pin", pinned, "-Channel", "stable")
    assert proc.returncode != 0 and "is not a release" in proc.stdout + proc.stderr
    assert copy.read_bytes() == PINS.read_bytes()


@requires_pin_tool
def test_the_pin_tool_fails_without_origin_main(tmp_path):
    """rc2 review C9: a clone with no origin/main can't tell whether the
    commit is on main's history, so it refuses rather than skip the check."""
    repo = tmp_path / "repo"
    (repo / "windows" / "installer").mkdir(parents=True)
    (repo / "windows" / "build.ps1").write_bytes(BUILD.read_bytes())
    (repo / "windows" / "installer" / "pins.iss").write_bytes(PINS.read_bytes())
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "x"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, env=env,
                         capture_output=True, text=True).stdout.strip()
    proc = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
         str(repo / "windows" / "build.ps1"), "-Pin", sha, "-Channel", "main", "-PinOnly"],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode != 0 and "has no origin/main" in proc.stdout + proc.stderr
    assert (repo / "windows" / "installer" / "pins.iss").read_bytes() == PINS.read_bytes()


def test_every_download_estimate_agrees():
    """Setup's pages, the helper's no-internet message and the doc give the
    same "about X-Y GB" for what an install downloads."""
    found = []
    for path in (SCRIPT, WIN / "helper" / "Preflight.cs", DOC):
        found += [(path.name, m) for m in re.findall(r"(\d+(?:\.\d+)?-\d+(?:\.\d+)? GB)", path.read_text(encoding="utf-8"))]
    assert len(found) >= 5, found
    assert len({m for _, m in found}) == 1, found


def test_the_script_uses_the_pins_and_the_ports():
    s = SCRIPT.read_text(encoding="ascii")
    for key in ("ProvisionUrl", "ProvisionSha256", "UbuntuUrl", "UbuntuSha256", "WslMsiUrl", "WslMsiSha256", "DomovoiCommit"):
        assert "{#" + key + "}" in s, key
    assert '#include "pins.iss"' in s
    # The dashboard the finish page opens: localhost, the AGENTS.md port,
    # with this build's mark so an old cache-first service worker can't
    # answer it from its cache (owner fixes 2026-10-12, item 5), and the
    # page as a hash route (Settings > Configuration with the first-run
    # admin setup, or Settings for a dashboard without tab addresses).
    assert "DashboardUrl = 'http://localhost:6369'" in s
    assert "OpenUrl = 'http://localhost:6369/?installed={#Copy(DomovoiCommit, 1, 7)}-{#BuildStamp}#{#DashboardRoute}'" in s
    assert '#ifdef DashboardTabRoutes\n  #define DashboardRoute "settings/configuration/setup"\n#else\n  #define DashboardRoute "settings"\n#endif' in s
    assert "ShellExecAsOriginalUser('open', OpenUrl," in s and "ShellExecAsOriginalUser('open', DashboardUrl," not in s
    assert "PrivilegesRequired=admin" in s and "ArchitecturesAllowed=x64os" in s and "MinVersion=10.0.22621" in s


def _tab_routes_pinned() -> bool:
    return bool(re.search(r"^#define DashboardTabRoutes\s*$", PINS.read_text(encoding="ascii"), re.M))


def test_the_tab_routes_pin_matches_the_pinned_commit():
    """"Open Domovoi" goes to #settings/configuration/setup only when the
    dashboard at the pinned commit has that address (SETUP_ROUTE, from
    wf/settings-deeplinks); an older one would land on Home, so it gets
    #settings. Owner, 2026-10-12: the link must land somewhere sensible
    until the addresses are on main."""
    commit = _pins()["DomovoiCommit"]
    git = shutil.which("git")
    if not git:
        pytest.skip("no git")
    proc = subprocess.run(
        [git, "-C", str(REPO_ROOT), "cat-file", "-p", f"{commit}:web/static/components.jsx"],
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"commit {commit[:7]} is not in this clone")
    has_routes = b"const SETUP_ROUTE = 'settings/configuration/setup';" in proc.stdout
    assert _tab_routes_pinned() == has_routes, (
        f"pins.iss DashboardTabRoutes is {'on' if _tab_routes_pinned() else 'off'}, but the dashboard at "
        f"{commit[:7]} {'has' if has_routes else 'does not have'} the tab addresses")


def _finish_section() -> str:
    s = SCRIPT.read_text(encoding="ascii")
    return s[s.index("{ -- the finish page"):s.index("{ -- the install (after the files are copied)")]


def test_the_finish_page_walks_through_the_admin_account_by_the_dashboards_labels():
    """Owner, 2026-10-12: the last page says exactly what to do on the page
    it opens. The labels are the dashboard's own (web/static/components.jsx
    LoginModal, settings.jsx AdminSection; the dashboard's
    test_web_settings_routes.py holds it to them)."""
    f = _finish_section()
    steps = f[f.index("function FinishSteps"):f.index("function GpuNames")]
    for label in ('"first-run admin setup"', '"set up admin"', '"setup code"', '"confirm password"',
                  '"set password"', "at least 10 characters", "Admin box", "Settings > Configuration",
                  "the Configuration tab"):
        assert label in steps, label
    assert "CodeHint.Caption := FinishSteps;" in f
    # "It copies the code": Open Domovoi does, before it opens the page.
    click = f[f.index("procedure OpenDomovoiClick"):]
    click = click[: click.index("\nend;")]
    assert click.index("CopyCodeClick(Sender);") < click.index("ShellExecAsOriginalUser('open', OpenUrl,")
    assert "shown only here; it works for 24 hours" in f


def test_the_finish_page_claims_no_graphics_card_setup_did_not_find():
    """The first install said "AI models run on: NVIDIA ..., NVIDIA ..."
    while the core reported no GPUs. The line now says what runs where from
    what Setup found, and never more: speech recognition is always on the
    processor (provision.sh writes WHISPER_DEVICE=cpu), and an unknown
    answer names no card."""
    f = _finish_section()
    assert "AI models run on" not in f
    models = f[f.index("function ModelsText"):]
    models = models[: models.index("\nend;")]
    assert "Speech recognition runs on the processor." in models
    assert "No NVIDIA graphics card found: chat, voice-command and speech recognition models all run on the processor." in models
    assert "Setup could not check for an NVIDIA graphics card" in models
    assert "ModelsText(GpuRaw, TierText)" in f
    prov = (REPO_ROOT / "scripts" / "linux" / "provision.sh").read_text(encoding="utf-8")
    assert 'echo "WHISPER_DEVICE=cpu"' in prov


def test_uninstall_messages_are_plain_words():
    """Owner, 2026-10-12: "a one-time startup task, DomovoiRemoveProfile,
    deletes it ... (error 32)" was too technical for the final dialog. The
    dialogs show the helper's notice_*= lines (UninstallMessages, unit
    tested in Tests.cs); the technical lines stay in the list of steps and
    the log."""
    s = SCRIPT.read_text(encoding="ascii")
    body = s[s.index("procedure CurUninstallStepChanged"):]
    for key in ("notice_restart", "notice_left", "notice_still"):
        assert f"KvValue(UninstLines, '{key}')" in body, key
    assert "JoinLines(Left" not in body and "JoinLines(AtRestart" not in body
    assert "DomovoiRemoveProfile" not in body and "there is nothing for you to do:" not in body
    helper = (WIN / "helper" / "Uninstall.cs").read_text(encoding="ascii")
    for key in ("notice_restart", "notice_left", "notice_still", "leftover_plain", "restart_plain"):
        assert f'o.Kv("{key}"' in helper, key
    # The list of steps still shows the details.
    assert "AddDetail('At the next restart: '" in s and "AddDetail('Left over: '" in s


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
        "?installed=", "--stop-before", "#settings/configuration/setup", "DashboardTabRoutes",
        "first-run admin setup", "set up admin", "set password",
        "DomovoiTray.exe", "The tray icon", "Prepare a satellite card", "Keep this PC awake",
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


def test_setup_installs_starts_and_removes_the_tray():
    """The tray icon (owner, 2026-10-17: Setup is the main way it arrives):
    installed with the program, started at every account's sign-in by one
    machine-wide Run value the uninstaller removes, started for the person
    running Setup once the install worked (as them, not elevated), and
    stopped, every account's, before Setup replaces it or the uninstaller
    removes Domovoi."""
    s = SCRIPT.read_text(encoding="ascii")
    assert r'Source: "..\build\bin\DomovoiTray.exe"; DestDir: "{app}"; Flags: ignoreversion' in s
    run = [line for line in s.splitlines() if 'ValueName: "DomovoiTray"' in line]
    assert len(run) == 1 and run[0].startswith(r'Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"')
    assert r'""{app}\DomovoiTray.exe"" --autostart' in run[0] and "Flags: uninsdeletevalue" in run[0]
    stop = s[s.index("procedure StopTrays;"):]
    stop = stop[: stop.index("\nend;")]
    assert r"ExpandConstant('{sys}\taskkill.exe'), '/F /IM DomovoiTray.exe'" in stop
    start = s[s.index("procedure StartTray;"):]
    start = start[: start.index("\nend;")]
    assert r"ExecAsOriginalUser(ExpandConstant('{app}\DomovoiTray.exe')" in start
    assert "InstallOk := MachineSetup;\n    if InstallOk then\n      StartTray;" in s
    prep = s[s.index("function PrepareToInstall"):]
    prep = prep[: prep.index("\nend;")]
    assert prep.index("'host-stop'") < prep.index("StopTrays;")
    un = s[s.index("procedure CurUninstallStepChanged"):]
    assert un.index("StopTrays;") < un.index("ExecAndLogOutput(HelperExe, 'uninstall'")
    assert r"RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Domovoi\Tray')" in un
    assert r'Name: "{autoprograms}\Domovoi"; Filename: "{app}\DomovoiTray.exe"; Parameters: "--open"' in s
    assert "DomovoiTray.exe" in BUILD.read_text(encoding="ascii")


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
    if any(p.is_file() for p in iscc):
        # Both Setups: the server's and the satellite's (2026-10-21).
        for name in ("DomovoiSetup.exe", "DomovoiSatelliteSetup.exe"):
            assert re.search(r"^built .*\\" + re.escape(name) + r" \(\d+ bytes, sha256 [0-9a-f]{64}\)$",
                             proc.stdout, re.M), name
