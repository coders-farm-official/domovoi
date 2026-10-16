"""scripts/linux/update-verifier.sh, the update unit's root-owned verifier
(delta security review 2026-10-16, S4 / REV-10), held to the scripts that
share its code and to its own rules.

Its behaviour is tested in the bash harnesses (test-apply-update.sh's
verifier_* cases, test-provision.sh's rerun_* cases,
test-install-update-unit.sh's verifier_* cases); this file checks what
must stay true of the text: provision.sh carries the verified-copy block
character for character (it runs alone in the Windows installer's distro,
with no copy of the verifier beside it), and every git root runs reads
neither replacement objects nor grafts (rc2 review C3).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from domovoi.tests.test_apply_update_script import BASH, requires_bash

REPO_ROOT = Path(__file__).resolve().parents[2]
LINUX = REPO_ROOT / "scripts" / "linux"
VERIFIER = LINUX / "update-verifier.sh"
PROVISION = LINUX / "provision.sh"
APPLY = LINUX / "apply-update.sh"

BEGIN = "# >>> verified-copy block >>>\n"
END = "# <<< verified-copy block <<<\n"


def _block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text.count(BEGIN) == 1 and text.count(END) == 1, f"{path.name}: one verified-copy block"
    return text[text.index(BEGIN):text.index(END) + len(END)]


def test_verifier_is_lf_only():
    assert b"\r\n" not in VERIFIER.read_bytes()


@requires_bash
def test_verifier_parses():
    proc = subprocess.run([BASH, "-n", str(VERIFIER)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_provision_carries_the_same_block():
    assert _block(PROVISION) == _block(VERIFIER)


def test_the_copy_holds_what_root_runs():
    """scripts/linux (the verifier itself, apply-update.sh, its helpers, the
    units and drop-ins, the pin manifest) and the doc whose Ollama pin
    provision.sh reads; never the tests."""
    block = _block(VERIFIER)
    assert "VX_PATHS=(scripts/linux docs/LINUX_HOST.md)\n" in block
    assert "VX_SKIP=scripts/linux/tests/\n" in block
    # Plain files only: a link or a submodule refuses the copy.
    assert "100644|100755) ;;" in block
    assert "! -type f ! -type d" in block


def test_root_git_reads_no_replacements_and_no_grafts():
    block = _block(VERIFIER)
    m = re.search(r"^vx_git\(\) \{\n(.*?)\n\}$", block, re.M | re.S)
    assert m, "vx_git() not found"
    body = m.group(1)
    assert "GIT_NO_REPLACE_OBJECTS=1" in body and "GIT_GRAFT_FILE=/dev/null" in body
    assert "GIT_CONFIG_NOSYSTEM=1" in body
    assert 'git -C "$VX_MIRROR"' in body
    # Only HEAD and the remote-tracking branches come into the mirror.
    assert "'+HEAD:refs/domovoi/head' '+refs/remotes/origin/*:refs/remotes/origin/*'" in block
    assert "refs/replace" not in re.sub(r"^#.*$", "", block, flags=re.M)
    # Every other git call in the block goes through vx_git.
    code = re.sub(r"^\s*#.*$", "", block, flags=re.M)
    bare = [l for l in code.splitlines() if re.search(r"(^|[;|&(]\s*|\s)git\s", l) and "vx_git" not in l
            and 'git -C "$VX_MIRROR"' not in l and "upload-pack" not in l]
    assert bare == [], bare
    apply = APPLY.read_text(encoding="utf-8")
    m = re.search(r"^git_root\(\) \{\n(.*?)\n\}$", apply, re.M | re.S)
    assert m, "git_root() not found"
    assert 'GIT_NO_REPLACE_OBJECTS=1 GIT_GRAFT_FILE=/dev/null git -c advice.graftFileDeprecated=false "${cfg[@]}" -C "$GIT_ROOT_DIR"' in m.group(1)
    # And for every git the scripts run, the service user's included (wf/rc2-channels).
    for script in (VERIFIER, APPLY, PROVISION, LINUX / "install-update-unit.sh"):
        assert "\nexport GIT_NO_REPLACE_OBJECTS=1 GIT_GRAFT_FILE=/dev/null\n" in script.read_text(encoding="utf-8"), script.name


def test_the_checkout_is_read_by_the_service_user():
    """git upload-pack, the side of the fetch that opens the checkout, runs
    as the service user; root's side only takes in a pack."""
    block = _block(VERIFIER)
    assert 'up="runuser -u $VX_USER -- git upload-pack"' in block
    assert "transfer.fsckObjects true" in block and "fetch.fsckObjects true" in block


def test_provision_reads_root_files_from_the_copy():
    text = PROVISION.read_text(encoding="utf-8")
    for fn in ("gpu_runtime_script() { src_file scripts/linux/gpu-runtime.sh; }",
               "os_jobs_script() { src_file scripts/linux/os-jobs.sh; }",
               "journald_dropin_src() { src_file scripts/linux/units/journald-domovoi.conf; }",
               'bash "$(src_file scripts/linux/install-update-unit.sh)"',
               'load_manifest "$(src_file scripts/linux/provision-manifest.conf)"',
               'doc=$(src_file docs/LINUX_HOST.md)'):
        assert fn in text, fn
    # Nothing that root runs or installs is named in the checkout itself
    # (the one exception, a die message, names the path for a person).
    code = re.sub(r"^\s*#.*$", "", text, flags=re.M)
    hits = [l for l in code.splitlines() if "$CHECKOUT/scripts/linux" in l and "die " not in l]
    assert hits == [], hits
