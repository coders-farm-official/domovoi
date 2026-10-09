"""Run the hermetic harness for scripts/linux/provision.sh, and hold the
script to the files it must agree with.

The provisioning script is bash, so its tests are too
(scripts/linux/tests/test-provision.sh: a fake root directory, a throwaway
origin repository and PATH shims for apt, systemd, docker, curl, Ollama's
API and the venv's python). This wrapper keeps them in the suite with the
same bash rules as test_apply_update_script.py: Git for Windows' bash on
Windows, never System32's WSL launcher.

The script writes Ollama's unit as docs/LINUX_HOST.md gives it, reads the
Ollama release from that doc, installs the units shipped in
scripts/linux/units/ and reads the rest of its pins from
scripts/linux/provision-manifest.conf. The checks below keep those in
step, so neither side can drift alone.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from domovoi.tests.test_apply_update_script import BASH, requires_bash

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "linux" / "provision.sh"
MANIFEST = REPO_ROOT / "scripts" / "linux" / "provision-manifest.conf"
HARNESS = REPO_ROOT / "scripts" / "linux" / "tests" / "test-provision.sh"
LINUX_HOST = REPO_ROOT / "docs" / "LINUX_HOST.md"


def test_files_are_lf_only():
    """A CRLF in a bash script is a syntax error on the Linux host."""
    for path in (SCRIPT, MANIFEST, HARNESS):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} has CRLF line endings"


@requires_bash
def test_scripts_parse():
    for path in (SCRIPT, HARNESS):
        proc = subprocess.run([BASH, "-n", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, f"{path.name}: {proc.stderr}"


@requires_bash
def test_provision_harness():
    # About 30 cases, each a whole provisioning run against shims: several
    # minutes under Git Bash. The bound is for a hang.
    proc = subprocess.run([BASH, str(HARNESS)], capture_output=True, text=True, timeout=3000)
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-2000:]
    assert " 0 failed" in proc.stdout


def _manifest() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        assert sep and key not in out, line
        out[key] = value
    return out


def test_manifest_has_every_key_the_script_reads():
    m = re.search(r"^MANIFEST_KEYS=\((.*?)\)$", SCRIPT.read_text(encoding="utf-8"), re.M | re.S)
    assert m, "MANIFEST_KEYS not found in provision.sh"
    assert set(m.group(1).split()) == set(_manifest())


def test_manifest_pins_are_well_formed():
    m = _manifest()
    assert m["manifest_version"] == "1"
    assert re.fullmatch(r"[0-9A-F]{40}", m["docker_key_fpr"])
    assert re.fullmatch(r"[0-9a-f]{40}", m["whisper_revision"])
    for key, value in m.items():
        if key.endswith("_digest") or key.endswith("_sha256"):
            assert re.fullmatch(r"[0-9a-f]{64}", value), key
        if key.endswith("_model") and key.startswith("tier_"):
            # An exact tag, never a bare name Ollama can retarget.
            assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*:[A-Za-z0-9._-]+", value), key
            assert value not in ("qwen3:4b", "qwen3:8b", "llama3.2:3b"), key


def test_nothing_the_repo_pins_is_copied_into_the_manifest():
    """The manifest holds only what has no home in the repo: no Ollama
    release, image digest, Piper pin or lock hash."""
    text = MANIFEST.read_text(encoding="utf-8")
    doc = LINUX_HOST.read_text(encoding="utf-8")
    ollama_sha = re.search(r"^OLLAMA_SHA256=([0-9a-f]{64})$", doc, re.M).group(1)
    assert ollama_sha not in text
    compose = (REPO_ROOT / "domovoi" / "docker-compose.yml").read_text(encoding="utf-8")
    for digest in re.findall(r"@sha256:([0-9a-f]{64})", compose):
        assert digest not in text
    assert not re.search(r"^(ollama|postgres|flyway|searxng|piper|torch)_", text, re.M)


def test_the_doc_pins_exactly_one_ollama_release():
    """provision.sh reads OLLAMA_VERSION= and OLLAMA_SHA256= from the doc
    and stops unless there is exactly one of each."""
    doc = LINUX_HOST.read_text(encoding="utf-8")
    assert len(re.findall(r"^OLLAMA_VERSION=[0-9][0-9.]*$", doc, re.M)) == 1
    assert len(re.findall(r"^OLLAMA_SHA256=[0-9a-f]{64}$", doc, re.M)) == 1


def test_ollama_unit_is_the_docs_unit():
    script = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^ollama_unit_text\(\) \{\n  cat <<'EOF'\n(.*?)\nEOF\n\}$", script, re.M | re.S)
    assert m, "ollama_unit_text() not found in provision.sh"
    doc = LINUX_HOST.read_text(encoding="utf-8")
    d = re.search(
        r"\*\*`/etc/systemd/system/ollama\.service`\*\*.*?\n\n```ini\n(.*?)\n```", doc, re.S
    )
    assert d, "the ollama.service block is gone from LINUX_HOST.md"
    assert m.group(1) == d.group(1)


def test_units_come_from_the_shipped_files():
    """No second copy of the domovoi units: provision.sh installs
    scripts/linux/units/*.service, rewriting only paths and the user."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert '"$CHECKOUT/scripts/linux/units/$1"' in script
    assert "ExecStart=/opt/domovoi/.venv/bin/python" not in script
    for unit in ("domovoi-db.service", "domovoi-core.service", "domovoi-web.service"):
        assert (REPO_ROOT / "scripts" / "linux" / "units" / unit).is_file()


def test_restart_grant_is_the_docs_rule():
    script = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r'^restart_grant\(\) \{\n  echo "(.*?)"\n\}$', script, re.M)
    assert m, "restart_grant() not found"
    rule = m.group(1).replace("$SVC_USER", "domovoi").replace("$SYSTEMCTL_PATH", "/usr/bin/systemctl")
    assert rule in LINUX_HOST.read_text(encoding="utf-8")
