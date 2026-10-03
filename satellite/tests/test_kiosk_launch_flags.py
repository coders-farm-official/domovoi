"""The video satellite's kiosk Chromium sends no traffic nobody asked for
(fix B10): background networking, component updates, hyperlink-auditing
pings and Domain Reliability reports are off, on every internet answer.

The launch script is checked as text, and RUN under bash against stand-ins
for ``cage`` (which records the argv it was given) and the satellite venv's
python (which prints the kiosk URL): nothing graphical starts. DB-free.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kiosk_launch.sh"
QUIET_FLAGS = (
    "--disable-background-networking",
    "--disable-component-update",
    "--no-pings",
    "--disable-domain-reliability",
)
KEPT_FLAGS = (
    "--kiosk",
    "--noerrdialogs",
    "--disable-session-crashed-bubble",
    "--autoplay-policy=no-user-gesture-required",
)


def _launch_line() -> str:
    """The `exec cage -- ...` command, continuation lines joined."""
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("exec cage --")
    cmd = []
    for line in text[start:].splitlines():
        cmd.append(line.rstrip().removesuffix("\\").strip())
        if not line.rstrip().endswith("\\"):
            break
    return " ".join(cmd)


def test_quiet_flags_are_on_the_chromium_line() -> None:
    line = _launch_line()
    for flag in QUIET_FLAGS + KEPT_FLAGS:
        assert flag in line, flag
    # Flags before the URL: Chromium reads anything after it as more URLs.
    assert line.rstrip().endswith('"$URL"')
    assert all(line.index(f) < line.index('"$URL"') for f in QUIET_FLAGS)


def test_each_flag_is_explained_in_the_header() -> None:
    header = SCRIPT.read_text(encoding="utf-8").split("set -eu", 1)[0]
    for flag in QUIET_FLAGS:
        assert f"#   {flag}" in header, flag


def test_script_is_lf_only() -> None:
    assert b"\r\n" not in SCRIPT.read_bytes()


def _find_bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    git = shutil.which("git")
    candidates = []
    if git:
        for up in (Path(git).parent.parent, Path(git).parent.parent.parent):
            candidates.append(up / "bin" / "bash.exe")
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file() and "system32" not in str(c).lower():
            return str(c)
    return None


BASH = _find_bash()


@pytest.mark.skipif(BASH is None, reason="no usable bash")
def test_the_script_hands_chromium_exactly_these_flags(tmp_path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "argv.txt"
    (bin_dir / "cage").write_text(
        '#!/bin/sh\nfor a in "$@"; do printf \'%s\\n\' "$a"; done >"$ARGV_FILE"\n',
        encoding="utf-8", newline="\n",
    )
    (bin_dir / "chromium").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
    (bin_dir / "venv-python").write_text(
        "#!/bin/sh\necho http://domovoi.local:6369/display.html?room=den\n",
        encoding="utf-8", newline="\n",
    )
    wrapper = (
        'd="$BIN_DIR"; a="$ARGV_FILE"; if command -v cygpath >/dev/null 2>&1; then '
        'd=$(cygpath -u "$d"); a=$(cygpath -u "$a"); fi; chmod +x "$d"/*; '
        'export ARGV_FILE="$a" DOMOVOI_VENV_PY="$d/venv-python" DOMOVOI_KIOSK_BROWSER=chromium; '
        'PATH="$d:$PATH" exec sh "$0"'
    )
    env = {**os.environ, "BIN_DIR": str(bin_dir), "ARGV_FILE": str(argv_file), "HOME": str(tmp_path)}
    proc = subprocess.run([BASH, "-c", wrapper, str(SCRIPT)], capture_output=True,
                          text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    argv = argv_file.read_text(encoding="utf-8").splitlines()
    assert argv[:2] == ["--", "chromium"]
    for flag in QUIET_FLAGS + KEPT_FLAGS:
        assert flag in argv, flag
    assert argv[-1] == "http://domovoi.local:6369/display.html?room=den"
