"""Run the hermetic harness for scripts/linux/install-update-unit.sh, and
hold the script to the doc it automates.

The installer is bash, so its tests are too
(scripts/linux/tests/test-install-update-unit.sh: throwaway git repos, a
fake root directory, and PATH shims for systemctl, visudo, sudo, curl, git,
docker, id and friends). This wrapper keeps them in the suite, with the same
bash rules as test_apply_update_script.py: Git for Windows' bash on Windows,
never System32's WSL launcher.

The script writes the unit and the sudoers rule that docs/LINUX_HOST.md
gives for doing it by hand, and the rule has to allow exactly what the
core's Restart button runs (domovoi/self_restart.py). Those three are
compared here, so neither the doc nor the script can drift alone.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

from domovoi import self_restart
from domovoi.tests.test_apply_update_script import BASH, requires_bash

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "linux" / "install-update-unit.sh"
HARNESS = REPO_ROOT / "scripts" / "linux" / "tests" / "test-install-update-unit.sh"
LINUX_HOST = REPO_ROOT / "docs" / "LINUX_HOST.md"

# What the Restart button runs through sudo once the unit is installed.
GRANT_CMD = " ".join(["/usr/bin/systemctl", *self_restart._action("update")])


def test_scripts_are_lf_only():
    """A CRLF in a bash script is a syntax error on the Linux host."""
    for path in (SCRIPT, HARNESS):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} has CRLF line endings"


@requires_bash
def test_scripts_parse():
    for path in (SCRIPT, HARNESS):
        proc = subprocess.run([BASH, "-n", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, f"{path.name}: {proc.stderr}"


@requires_bash
def test_install_update_unit_harness():
    proc = subprocess.run([BASH, str(HARNESS)], capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-2000:]
    assert " 0 failed" in proc.stdout


def _script_unit(repo: str) -> str:
    """The unit the script writes for a checkout at ``repo``."""
    m = re.search(
        r"^unit_text\(\) \{\n  cat <<EOF\n(.*?)\nEOF\n\}$",
        SCRIPT.read_text(encoding="utf-8"),
        re.M | re.S,
    )
    assert m, f"unit_text() not found in {SCRIPT.name}"
    body = m.group(1).replace("$REPO", repo)
    assert "$" not in body, "the unit may only interpolate the checkout path"
    return body


def _doc_units() -> list[str]:
    """Every copy of domovoi-update.service in LINUX_HOST.md: the reference
    block and the heredoc in the manual steps."""
    doc = LINUX_HOST.read_text(encoding="utf-8")
    units = re.findall(
        r"\*\*`/etc/systemd/system/domovoi-update\.service`\*\*:\n\n```ini\n(.*?)\n```",
        doc,
        re.S,
    )
    # The heredoc sits indented in a numbered list; dedent it.
    units += [
        textwrap.dedent(body)
        for body in re.findall(
            r"^ *sudo tee /etc/systemd/system/domovoi-update\.service >/dev/null <<'EOF'\n(.*?)\n *EOF$",
            doc,
            re.M | re.S,
        )
    ]
    return units


def test_unit_is_the_docs_unit():
    units = _doc_units()
    assert len(units) == 2, "expected the reference unit and the manual steps' heredoc"
    for unit in units:
        assert _script_unit("/opt/domovoi") == unit


def test_unit_names_the_checkout_it_updates():
    assert "ExecStart=/bin/bash /srv/dv/scripts/linux/apply-update.sh" in _script_unit("/srv/dv")


@requires_bash
def test_grant_is_what_the_restart_button_runs():
    """The rule allows exactly the command self_restart probes and fires,
    and the doc's manual rule is the same one."""
    proc = subprocess.run(
        [BASH, "-c",
         'eval "$(grep -E "^(SYSTEMCTL_PATH|UNIT_NAME|GRANT_ARGV)=" "$1")"; printf %s "${GRANT_ARGV[*]}"',
         "_", SCRIPT.as_posix()],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == GRANT_CMD

    doc = LINUX_HOST.read_text(encoding="utf-8")
    rules = re.findall(r"domovoi ALL=\(root\) NOPASSWD: (.*?domovoi-update\.service)", doc)
    assert rules, "no update-unit rule in LINUX_HOST.md"
    assert set(rules) == {GRANT_CMD}
    # The harness asserts the installed file holds exactly this line.
    assert f'GRANT_CMD="{GRANT_CMD}"' in HARNESS.read_text(encoding="utf-8")
