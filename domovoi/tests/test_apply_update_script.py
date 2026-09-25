"""Run the hermetic harness for scripts/linux/apply-update.sh.

The update unit's script is bash, so its tests are too
(scripts/linux/tests/test-apply-update.sh: throwaway git repos plus PATH
shims for systemctl, docker, pg_dump, pg_restore, psql and curl). This
wrapper keeps them in the suite. On Windows it needs Git for Windows' bash;
System32's bash.exe is the WSL launcher, which would run the harness in a
different machine entirely, so it is never used.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "linux" / "apply-update.sh"
HARNESS = REPO_ROOT / "scripts" / "linux" / "tests" / "test-apply-update.sh"


def _find_bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        # <root>\cmd\git.exe or <root>\mingw64\bin\git.exe → <root>\bin\bash.exe
        for up in (Path(git).parent.parent, Path(git).parent.parent.parent):
            candidates.append(up / "bin" / "bash.exe")
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file() and "system32" not in str(c).lower():
            return str(c)
    return None


BASH = _find_bash()
requires_bash = pytest.mark.skipif(BASH is None, reason="no usable bash (Git Bash on Windows)")


def test_scripts_are_lf_only():
    """A CRLF in a bash script is a syntax error on the Linux host."""
    for path in (SCRIPT, HARNESS):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} has CRLF line endings"


@requires_bash
def test_script_parses():
    proc = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@requires_bash
def test_apply_update_harness():
    proc = subprocess.run(
        [BASH, str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "HARNESS_PYTHON": sys.executable},
    )
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-2000:]
    assert " 0 failed" in proc.stdout
