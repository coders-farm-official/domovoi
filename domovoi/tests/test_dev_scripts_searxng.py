"""dev.sh / dev.ps1 start the search helper when the box is answered Yes or
Sometimes (fix B8), and still bootstrap ``.env`` through env_bootstrap.

Both scripts are RUN here, against PATH shims for ``python`` and
``docker`` that only log (nothing starts, nothing is pulled): bash via Git
Bash (as in test_apply_update_script.py), PowerShell via powershell.exe.
Each is skipped where its shell is missing. The static checks need
neither.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "domovoi" / "scripts"
DEV_SH = SCRIPTS / "dev.sh"
DEV_PS1 = SCRIPTS / "dev.ps1"
START = "docker compose up -d searxng"


def _find_bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        for up in (Path(git).parent.parent, Path(git).parent.parent.parent):
            candidates.append(up / "bin" / "bash.exe")
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file() and "system32" not in str(c).lower():
            return str(c)
    return None


BASH = _find_bash()
POWERSHELL = shutil.which("powershell") if sys.platform == "win32" else shutil.which("pwsh")


# ─── static ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [DEV_SH, DEV_PS1])
def test_policy_gated_start_follows_flyway_and_bootstrap_stays(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    assert "python -m domovoi.env_bootstrap" in src
    assert "python -m domovoi.egress --print-policy" in src
    assert START in src
    assert src.index("docker compose run --rm flyway") < src.index("--print-policy") < src.index(START)
    assert "always" in src and "sometimes" in src
    assert "DOMOVOI_MANAGE_SEARXNG" in src
    # The start never stops the script: a failure is a warning.
    assert ("warning" in src.lower())
    assert b"\r\n" not in path.read_bytes()


# ─── dev.sh, run ──────────────────────────────────────────────────────────


def _bash_shims(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "python").write_text(
        "#!/usr/bin/env bash\n"
        'echo "python $*" >>"$SHIM_LOG"\n'
        'if [ "${2-}" = domovoi.egress ]; then printf \'%s\\n\' "${SHIM_POLICY-}"; fi\n'
        "exit 0\n",
        encoding="utf-8", newline="\n",
    )
    (d / "docker").write_text(
        "#!/usr/bin/env bash\n"
        'echo "docker $*" >>"$SHIM_LOG"\n'
        'if [ "${SHIM_DOCKER_FAIL-}" = 1 ] && [ "$*" = "compose up -d searxng" ]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8", newline="\n",
    )


def _run_dev_sh(tmp_path: Path, policy: str, *, fail: bool = False, manage: str | None = None):
    shims = tmp_path / "shims"
    _bash_shims(shims)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    env = {**os.environ, "SHIM_LOG": str(log), "SHIM_POLICY": policy,
           "SHIM_DOCKER_FAIL": "1" if fail else "", "SHIM_DIR": str(shims)}
    env.pop("DOMOVOI_MANAGE_SEARXNG", None)
    if manage is not None:
        env["DOMOVOI_MANAGE_SEARXNG"] = manage
    wrapper = (
        'd="$SHIM_DIR"; if command -v cygpath >/dev/null 2>&1; then d=$(cygpath -u "$d"); '
        'export SHIM_LOG=$(cygpath -u "$SHIM_LOG"); fi; chmod +x "$d"/*; '
        'PATH="$d:$PATH" exec bash "$0"'
    )
    proc = subprocess.run([BASH, "-c", wrapper, str(DEV_SH)], capture_output=True,
                          text=True, env=env, timeout=120)
    return proc, log.read_text(encoding="utf-8").splitlines()


@pytest.mark.skipif(BASH is None, reason="no usable bash")
@pytest.mark.parametrize("policy", ["always", "sometimes"])
def test_dev_sh_starts_searxng_for_yes_or_sometimes(tmp_path, policy) -> None:
    proc, calls = _run_dev_sh(tmp_path, policy)
    assert proc.returncode == 0, proc.stderr
    assert calls[0] == "python -m domovoi.env_bootstrap"
    assert "docker compose run --rm flyway" in calls
    assert f"docker {START.removeprefix('docker ')}" in calls
    assert calls.index("docker compose run --rm flyway") < calls.index("docker compose up -d searxng")
    assert calls[-1] == "python -m domovoi.main"


@pytest.mark.skipif(BASH is None, reason="no usable bash")
@pytest.mark.parametrize("policy", ["", "never"])
def test_dev_sh_leaves_searxng_alone_otherwise(tmp_path, policy) -> None:
    proc, calls = _run_dev_sh(tmp_path, policy)
    assert proc.returncode == 0, proc.stderr
    assert "python -m domovoi.egress --print-policy" in calls
    assert "docker compose up -d searxng" not in calls
    assert calls[-1] == "python -m domovoi.main"


@pytest.mark.skipif(BASH is None, reason="no usable bash")
def test_dev_sh_a_failed_start_is_only_a_warning(tmp_path) -> None:
    proc, calls = _run_dev_sh(tmp_path, "always", fail=True)
    assert proc.returncode == 0, proc.stderr
    assert "could not start the search helper" in proc.stderr
    assert calls[-1] == "python -m domovoi.main"


@pytest.mark.skipif(BASH is None, reason="no usable bash")
def test_dev_sh_opt_out(tmp_path) -> None:
    proc, calls = _run_dev_sh(tmp_path, "always", manage="0")
    assert proc.returncode == 0, proc.stderr
    assert not any("domovoi.egress" in c for c in calls)
    assert "docker compose up -d searxng" not in calls


# ─── dev.ps1, run ─────────────────────────────────────────────────────────


def _cmd_shims(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "python.cmd").write_text(
        "@echo off\r\n"
        "echo python %*>>\"%SHIM_LOG%\"\r\n"
        "if \"%2\"==\"domovoi.egress\" if defined SHIM_POLICY echo %SHIM_POLICY%\r\n"
        "exit /b 0\r\n",
        encoding="ascii", newline="",
    )
    (d / "docker.cmd").write_text(
        "@echo off\r\n"
        "echo docker %*>>\"%SHIM_LOG%\"\r\n"
        "if \"%SHIM_DOCKER_FAIL%\"==\"1\" if \"%1\"==\"compose\" if \"%4\"==\"searxng\" exit /b 1\r\n"
        "exit /b 0\r\n",
        encoding="ascii", newline="",
    )


def _run_dev_ps1(tmp_path: Path, policy: str, *, fail: bool = False):
    shims = tmp_path / "shims"
    _cmd_shims(shims)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="ascii")
    env = {**os.environ, "SHIM_LOG": str(log), "SHIM_POLICY": policy,
           "SHIM_DOCKER_FAIL": "1" if fail else "",
           "PATH": str(shims) + os.pathsep + os.environ.get("PATH", "")}
    env.pop("DOMOVOI_MANAGE_SEARXNG", None)
    if not policy:
        env.pop("SHIM_POLICY")
    proc = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(DEV_PS1)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    calls = [c.strip() for c in log.read_text(encoding="ascii", errors="replace").splitlines()]
    return proc, calls


needs_powershell = pytest.mark.skipif(
    POWERSHELL is None or sys.platform != "win32", reason="Windows PowerShell only")


@needs_powershell
def test_dev_ps1_starts_searxng_for_sometimes(tmp_path) -> None:
    proc, calls = _run_dev_ps1(tmp_path, "sometimes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls[0] == "python -m domovoi.env_bootstrap"
    assert calls.index("docker compose run --rm flyway") < calls.index("docker compose up -d searxng")
    assert calls[-1] == "python -m domovoi.main"


@needs_powershell
def test_dev_ps1_leaves_searxng_alone_when_unanswered(tmp_path) -> None:
    proc, calls = _run_dev_ps1(tmp_path, "")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "docker compose up -d searxng" not in calls
    assert calls[-1] == "python -m domovoi.main"


@needs_powershell
def test_dev_ps1_a_failed_start_is_only_a_warning(tmp_path) -> None:
    proc, calls = _run_dev_ps1(tmp_path, "always", fail=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "could not start the search helper" in (proc.stdout + proc.stderr)
    assert calls[-1] == "python -m domovoi.main"
