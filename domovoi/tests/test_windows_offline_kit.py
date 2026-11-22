"""Windows Setup installing from an offline kit (release 1.5.0, P3;
docs/WINDOWS_HOST.md, "Installing without internet").

The Windows side reads kits in C# (windows/common/OfflineKit.cs, tested by
windows/tests/KitTests.cs against windows/tests/kit-fixture). This file
keeps that C# honest against the Python side it mirrors:

* the fixture is exactly what the real writer (domovoi.offline_kit.write_kit)
  makes, and the real checker accepts it with the test release key, so the
  C# reader is tested on a kit in the real format, split file included;
* Setup's list of the parts every install needs names what provision.sh's
  kit_check refuses without, in the same words;
* the kit builder (P1) puts WSL and Ubuntu where Setup looks for them;
* Setup's script never downloads in kit mode, and carries the kit across
  the restart.

Regenerate the fixture after a format change:
    python domovoi/tests/test_windows_offline_kit.py --write
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from domovoi.offline_kit import Component, Source, write_kit  # noqa: E402

FIXTURE = REPO / "windows" / "tests" / "kit-fixture"
SIG = REPO / "windows" / "tests" / "release-sig"
OFFLINE_KIT_CS = REPO / "windows" / "common" / "OfflineKit.cs"
KIT_TESTS_CS = REPO / "windows" / "tests" / "KitTests.cs"
SCRIPT = REPO / "windows" / "installer" / "DomovoiSetup.iss"
PINS = REPO / "windows" / "installer" / "pins.iss"
PROVISION = REPO / "scripts" / "linux" / "provision.sh"
COMMIT = "c0ffee0123456789abcdef0123456789abcdef01"   # release-sig/manifest-1.0.1.json's

# The fixture's stand-in files. KitTests.cs pins the Windows ones by these
# exact bytes (its UbuntuBytes, MsiBytes and ProvisionBytes).
UBUNTU = b"fixture: Ubuntu's WSL image\n"
MSI = b"fixture: WSL's MSI\n"
PROVISION_SH = b"#!/bin/bash\n# fixture\nusage() {\n  --offline-kit DIR     install from the offline kit at DIR\n}\n"


def _components() -> list[Component]:
    def c(kind, name, licence, *files):
        return Component(kind=kind, name=name, version="1.0.1", licence=licence,
                         files=tuple(Source(p, b) for p, b in files), pinned_by="fixture")
    return [
        c("release", "DomovoiSetup.exe", "MIT",
          ("release/DomovoiSetup.exe", (SIG / "setup.bin").read_bytes()),
          ("release/DomovoiSetup.manifest.json", (SIG / "manifest-1.0.1.json").read_bytes()),
          ("release/DomovoiSetup.manifest.json.sig", (SIG / "manifest-1.0.1.json.sig").read_bytes())),
        c("linux-installer", "install.sh", "MIT",
          ("linux/install.sh", b"#!/bin/bash\n# fixture\n"),
          ("linux/provision.sh", PROVISION_SH)),
        # 5000 bytes with part_size 4000: stored as two parts.
        c("source", "Domovoi's source", "MIT", ("source/domovoi.bundle", bytes(range(250)) * 20)),
        c("apt", "Ubuntu's packages", "LicenseRef-distro-archive",
          ("apt/amd64/ubuntu/dists/resolute/InRelease", b"fixture\n")),
        c("apt", "Docker's packages", "LicenseRef-distro-archive",
          ("apt/amd64/docker/dists/resolute/InRelease", b"fixture\n"),
          ("apt/amd64/docker/gpg", b"fixture key\n")),
        c("python-wheels", "wheels", "MIT", ("python/cp314-x86_64/fixture-1.0-py3-none-any.whl", b"fixture wheel\n")),
        c("container-image", "postgres", "PostgreSQL", ("images/postgres_16.15-trixie.tar", b"fixture image\n")),
        c("ollama-runtime", "ollama", "MIT", ("ollama/ollama-linux-amd64.tar.zst", b"fixture ollama\n")),
        c("whisper-model", "small.en", "MIT", ("speech/whisper-small.en/model.bin", b"fixture whisper\n")),
        c("piper-voice", "fixture", "MIT", ("speech/piper/fixture.onnx", b"fixture voice\n")),
        c("speaker-model", "fixture", "Apache-2.0", ("speech/speaker/fixture.onnx", b"fixture speaker\n")),
        c("windows-part", "ubuntu-26.04.1-wsl-amd64.wsl", "LicenseRef-distro-archive",
          ("windows/ubuntu-26.04.1-wsl-amd64.wsl", UBUNTU)),
        c("windows-part", "wsl.2.7.14.0.x64.msi", "MIT", ("windows/wsl.2.7.14.0.x64.msi", MSI)),
    ]


def build_fixture(dest: Path) -> None:
    write_kit(dest, version="1.0.1", commit=COMMIT, components=_components(), targets=["pc-x86_64"],
              includes=[], created_at="2026-11-22T00:00:00Z", part_size=4000)


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_fixture_is_what_the_real_writer_makes(tmp_path):
    build_fixture(tmp_path / "kit")
    want, have = _tree(tmp_path / "kit"), _tree(FIXTURE)
    assert sorted(have) == sorted(want), "regenerate: python domovoi/tests/test_windows_offline_kit.py --write"
    for p in want:
        assert have[p] == want[p], f"{p} differs; regenerate: python domovoi/tests/test_windows_offline_kit.py --write"
    # The C# tests read the split bundle and a real release signature.
    assert "source/domovoi.bundle.part01" in have and "source/domovoi.bundle.part02" in have


def test_the_real_checker_accepts_the_fixture_with_the_test_key():
    from domovoi.offline_kit.checker import verify
    report = verify(FIXTURE, release_key=(SIG / "test-key.pub").read_text(encoding="ascii"))
    assert report.ok, report.errors


def test_the_real_checker_refuses_it_with_the_release_key():
    """What the C# test does with another key: the release key isn't the
    fixture's, so both sides refuse it."""
    from domovoi.offline_kit.checker import verify
    report = verify(FIXTURE)
    assert not report.ok


def test_the_csharp_tests_pin_the_fixtures_bytes():
    cs = KIT_TESTS_CS.read_text(encoding="ascii")
    for name, raw in (("UbuntuBytes", UBUNTU), ("MsiBytes", MSI), ("ProvisionBytes", PROVISION_SH)):
        lit = raw.decode("ascii").replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
        assert f'{name} = "{lit}"' in cs, name


def _provision_missing() -> list[str]:
    """The names provision.sh's kit_check gives each missing part."""
    text = PROVISION.read_text(encoding="utf-8")
    body = text[text.index("kit_check() {"):]
    body = body[:body.index('\n  if [ ${#missing[@]} -gt 0 ]; then')]
    return re.findall(r'missing\+=\("([^"]+)"\)', body)


def test_setups_missing_parts_name_what_provision_refuses_without():
    """A household reads one message, whichever finds a gap first: Setup
    before it changes anything, or provision.sh in the distro."""
    cs = OFFLINE_KIT_CS.read_text(encoding="ascii")
    body = cs[cs.index("public static List<string> MissingParts"):]
    body = body[:body.index("return missing;")]
    consts = {"BundlePath": "source/domovoi.bundle", "ProvisionPath": "linux/provision.sh"}
    names = []
    for arg in re.findall(r"missing\.Add\((.+?)\);", body):
        for k, v in consts.items():
            arg = arg.replace(f'" + {k} + "', v)
        names.append(arg.strip('"'))
    for want in _provision_missing():
        want = want.replace("$t", "amd64").replace("$MACHINE_ARCH", "x86_64")
        assert want in names, f"Setup's MissingParts lacks provision.sh's {want!r}"


def test_the_kit_builder_puts_wsl_and_ubuntu_where_setup_looks():
    """P1 writes each pins.iss Windows file at windows/<its name>, and the
    names are the ones Setup reads (UbuntuName, WslMsiName)."""
    from domovoi.kit_builder import pins as kpins
    pins = kpins.parse_iss(PINS.read_text(encoding="ascii"))
    parts = (REPO / "domovoi" / "kit_builder" / "parts.py").read_text(encoding="utf-8")
    assert 'f"windows/{fp.name}"' in parts
    assert 'for key in ("WslMsi", "Ubuntu")' in (REPO / "domovoi" / "kit_builder" / "pins.py").read_text(encoding="utf-8")
    s = SCRIPT.read_text(encoding="ascii")
    assert "'windows\\{#UbuntuName}'" in s and "'windows\\{#WslMsiName}'" in s
    assert pins["UbuntuName"].endswith(".wsl") and pins["WslMsiName"].endswith(".msi")


def _pascal(s: str, header: str) -> str:
    block = s[s.index(header):]
    return block[: block.index("\nend;")]


def test_kit_mode_never_downloads():
    s = SCRIPT.read_text(encoding="ascii")
    # Every download goes through AddDownload, which refuses in kit mode.
    assert s.count("DownloadPage.Add(") == 1
    guard = _pascal(s, "procedure AddDownload(")
    assert "DownloadPage.Add(" in guard and "RaiseException(" in guard and "KitRoot <> ''" in guard
    assert "DownloadPage.Download;" in s and s.count("DownloadPage.Download;") == 1
    run = _pascal(s, "function RunDownloads")
    assert "RaiseException(" in run and "KitRoot <> ''" in run
    # The preflight in kit mode: no internet check, the helper's guard on.
    assert "'preflight --skip-internet --offline'" in s
    # Every helper call that writes the job, or checks the kit, says --offline.
    assert "' --offline'" in s


def test_the_kit_crosses_the_restart():
    s = SCRIPT.read_text(encoding="ascii")
    entry = _pascal(s, "function CreateRunOnceEntry(")
    assert "' /kit=' + AddQuotes(RemoveBackslash(KitRoot))" in entry


def test_setup_checks_the_kit_before_it_changes_anything():
    s = SCRIPT.read_text(encoding="ascii")
    pre = _pascal(s, "function RunPreflight: Boolean;")
    assert "kit-check" in s and "RunKitCheck" in pre
    check = _pascal(s, "function RunKitCheck")
    for key in ("--commit {#DomovoiCommit}", "--ubuntu-sha256 {#UbuntuSha256}", "--wsl-msi-sha256 {#WslMsiSha256}"):
        assert key in check, key


def test_the_kit_mode_words_are_plain():
    s = SCRIPT.read_text(encoding="ascii")
    assert "Installing from the offline kit. No internet needed." in s


if __name__ == "__main__" and "--write" in sys.argv:
    import shutil
    with tempfile.TemporaryDirectory() as t:
        build_fixture(Path(t) / "kit")
        if FIXTURE.exists():
            shutil.rmtree(FIXTURE)
        shutil.copytree(Path(t) / "kit", FIXTURE)
    print(f"wrote {FIXTURE}")
