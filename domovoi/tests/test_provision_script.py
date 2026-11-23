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

import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi.tests.test_apply_update_script import BASH, requires_bash

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "linux" / "provision.sh"
MANIFEST = REPO_ROOT / "scripts" / "linux" / "provision-manifest.conf"
HARNESS = REPO_ROOT / "scripts" / "linux" / "tests" / "test-provision.sh"
OS_JOBS = REPO_ROOT / "scripts" / "linux" / "os-jobs.sh"
GPU_RUNTIME = REPO_ROOT / "scripts" / "linux" / "gpu-runtime.sh"
APPLY_UPDATE = REPO_ROOT / "scripts" / "linux" / "apply-update.sh"
PY_RUNTIME = REPO_ROOT / "scripts" / "linux" / "python-runtime.sh"
INSTALL = REPO_ROOT / "scripts" / "linux" / "install.sh"
INSTALL_VM = REPO_ROOT / "scripts" / "dev" / "linux-install-vm.sh"
LINUX_HOST = REPO_ROOT / "docs" / "LINUX_HOST.md"


def test_files_are_lf_only():
    """A CRLF in a bash script is a syntax error on the Linux host."""
    for path in (SCRIPT, MANIFEST, HARNESS, OS_JOBS, GPU_RUNTIME, PY_RUNTIME, INSTALL_VM):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} has CRLF line endings"


@requires_bash
def test_scripts_parse():
    for path in (SCRIPT, HARNESS, OS_JOBS, GPU_RUNTIME, PY_RUNTIME, INSTALL_VM):
        proc = subprocess.run([BASH, "-n", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, f"{path.name}: {proc.stderr}"


@requires_bash
@pytest.mark.bash_harness
def test_provision_harness():
    # In the suite like test_apply_update_harness, the same way: Git Bash,
    # HARNESS_PYTHON, and a bound that is for a hang, not a measure of
    # speed. Each case is one or more whole provisioning runs against
    # shims, and every fork is slow under Git Bash: the 30 cases of
    # 2026-10-09 took 901-1024 s alone on the dev box, the review round's
    # 41 took 1722 s.
    proc = subprocess.run(
        [BASH, str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=4800,
        env={**os.environ, "HARNESS_PYTHON": sys.executable},
    )
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
    assert re.fullmatch(r"[0-9A-F]{40}", m["nvidia_key_fpr"])
    assert re.fullmatch(r"[0-9a-f]{40}", m["whisper_revision"])
    for key, value in m.items():
        if key.endswith("_digest") or key.endswith("_sha256"):
            assert re.fullmatch(r"[0-9a-f]{64}", value), key
        if key.endswith("_model") and key.startswith("tier_"):
            # An exact tag, never a bare name Ollama can retarget.
            assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*:[A-Za-z0-9._-]+", value), key
            assert value not in ("qwen3:4b", "qwen3:8b", "llama3.2:3b"), key
            # The bare qwen3-vl tags are the thinking builds (2026-10-12).
            assert value not in ("qwen3-vl:2b", "qwen3-vl:4b"), key
            if key.endswith("_vision_model"):
                assert "-instruct" in value, key


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


def _docs_ollama_unit() -> str:
    doc = LINUX_HOST.read_text(encoding="utf-8")
    d = re.search(
        r"\*\*`/etc/systemd/system/ollama\.service`\*\*.*?\n\n```ini\n(.*?)\n```", doc, re.S
    )
    assert d, "the ollama.service block is gone from LINUX_HOST.md"
    return d.group(1) + "\n"


def test_ollama_unit_is_the_docs_unit():
    script = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^ollama_unit_text\(\) \{\n(.*?)\n\}$", script, re.M | re.S)
    assert m, "ollama_unit_text() not found in provision.sh"
    # Its two heredocs, joined, are the doc's unit (one request at a time);
    # the doc names the line that goes between them.
    parts = re.findall(r"cat <<'EOF'\n(.*?)\nEOF", m.group(1), re.S)
    assert len(parts) == 2
    assert "\n".join(parts) + "\n" == _docs_ollama_unit()
    assert "Environment=OLLAMA_NUM_PARALLEL=" in LINUX_HOST.read_text(encoding="utf-8")


def _unit_for(tmp_path: Path, *, mem_mib: int, tier: str, journal: str = "",
              env: dict[str, str] | None = None, had: int | None = None) -> str:
    """ollama_unit_text() as provision.sh writes it on a box with
    ``mem_mib`` of MemTotal, the given tier, Ollama start-up lines in the
    journal, .env context windows and the unit it already has (the
    script's own functions; what they call is stubbed)."""
    script = SCRIPT.read_text(encoding="utf-8")
    start = script.index("OLLAMA_PARALLEL_3_MIN_MEM_MIB=")
    end = script.index("ollama_served_version() {")
    unit_dir = tmp_path / "units"
    unit_dir.mkdir(exist_ok=True)
    (unit_dir / "ollama.service").write_text(
        "" if had is None else f"Environment=OLLAMA_NUM_PARALLEL={had}\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / "journal").write_text(journal, encoding="utf-8", newline="\n")
    env_lines = "".join(f"  {k}) echo '{v}' ;;\n" for k, v in (env or {}).items())
    prog = (
        "set -u\n"
        "declare -A M=([tier_standard_min_mem_mib]=22528)\n"
        f"MEM_MIB={mem_mib}; TIER_EFF={tier}; UNIT_DIR='{unit_dir.as_posix()}'\n"
        f"journalctl() {{ cat '{(tmp_path / 'journal').as_posix()}'; }}\n"
        f"dotenv_get() {{ case $1 in\n{env_lines}  *) ;;\n  esac; }}\n"
        + script[start:end]
        + "\nollama_unit_text\n"
    )
    (tmp_path / "prog.sh").write_text(prog, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, str(tmp_path / "prog.sh")], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _parallel(unit: str) -> int:
    m = re.search(r"^Environment=OLLAMA_NUM_PARALLEL=(\d+)$", unit, re.M)
    return int(m.group(1)) if m else 1


_START = 'time=x level=INFO source=routes.go:1 msg="server config" env=map[]\n'
_CPU = 'time=x level=INFO msg="inference compute" id=cpu library=cpu compute="" name=cpu description=cpu total="62.5 GiB"\n'


def _gpu(free_gib: float) -> str:
    return (f'time=x level=INFO msg="inference compute" id=0 library=CUDA compute=8.9 name=CUDA0 '
            f'description="NVIDIA GeForce RTX 4070 Ti SUPER" type=discrete total="16.0 GiB" '
            f'available="{free_gib} GiB"\n')


@requires_bash
def test_ollama_answers_at_once_by_tier(tmp_path):
    """OLLAMA_NUM_PARALLEL (S3-7): 1 below the standard tier, 2 on it, 3
    from 44 GiB; a box steps down while the extra context windows wouldn't
    leave the tier its memory."""
    floor = _unit_for(tmp_path, mem_mib=15800, tier="floor")
    assert _parallel(floor) == 1
    assert floor == _docs_ollama_unit()  # the doc's unit, unchanged
    assert _parallel(_unit_for(tmp_path, mem_mib=23800, tier="standard")) == 2
    assert _parallel(_unit_for(tmp_path, mem_mib=64000, tier="standard")) == 3
    # --tier standard forced on a small box: a second window doesn't fit.
    assert _parallel(_unit_for(tmp_path, mem_mib=16000, tier="standard")) == 1
    # A 24 GB box with an 8192 routing window: 22528 + 1728 > 23800.
    assert _parallel(_unit_for(tmp_path, mem_mib=23800, tier="standard",
                               env={"OLLAMA_TOOL_NUM_CTX": "8192"})) == 1
    # 48 GB with huge windows (64k + 32k, 13824 MiB a slot): three don't fit, two do.
    assert _parallel(_unit_for(tmp_path, mem_mib=47000, tier="standard",
                               env={"OLLAMA_NUM_CTX": "65536", "OLLAMA_TOOL_NUM_CTX": "32768"})) == 2
    unit = _unit_for(tmp_path, mem_mib=23800, tier="standard")
    assert unit.index("ExecStart=") < unit.index("Environment=OLLAMA_NUM_PARALLEL=2\n") < unit.index("User=ollama")


@requires_bash
def test_ollama_answers_four_at_once_with_room_on_the_gpus(tmp_path):
    two_cards = _START + _CPU + _gpu(14.7) + _gpu(14.7)
    assert _parallel(_unit_for(tmp_path, mem_mib=64000, tier="standard", journal=two_cards)) == 4
    # The windows live on the cards, so the box's own memory doesn't matter.
    assert _parallel(_unit_for(tmp_path, mem_mib=15800, tier="floor", journal=_START + _CPU + _gpu(14.7))) == 4
    # A card without room for the models plus four windows: the memory tier.
    assert _parallel(_unit_for(tmp_path, mem_mib=23800, tier="standard", journal=_START + _CPU + _gpu(10.9))) == 2
    assert _parallel(_unit_for(tmp_path, mem_mib=23800, tier="standard", journal=_START + _CPU)) == 2
    # Only Ollama's last start counts.
    assert _parallel(_unit_for(tmp_path, mem_mib=23800, tier="standard",
                               journal=_START + _CPU + _gpu(14.7) + _START + _CPU)) == 2


@requires_bash
def test_an_unknown_gpu_keeps_the_units_four(tmp_path):
    """No Ollama start in this boot's journal yet: a 4 the unit has stays,
    so a re-run doesn't rewrite the unit and restart Ollama."""
    assert _parallel(_unit_for(tmp_path, mem_mib=23800, tier="standard", had=4)) == 4
    assert _parallel(_unit_for(tmp_path, mem_mib=23800, tier="standard", had=3)) == 2
    # Known again, and without room on a GPU: the memory tier.
    assert _parallel(_unit_for(tmp_path, mem_mib=23800, tier="standard", had=4, journal=_START + _CPU)) == 2


def test_units_come_from_the_shipped_files():
    """No second copy of the domovoi units: provision.sh installs
    scripts/linux/units/*.service, rewriting only paths and the user, from
    the verified copy of the commit (never the working tree; delta security
    review 2026-10-16, S4)."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert '"$(src_file "scripts/linux/units/$1")"' in script
    assert "ExecStart=/opt/domovoi/.venv/bin/python" not in script
    for unit in ("domovoi-db.service", "domovoi-core.service", "domovoi-web.service"):
        assert (REPO_ROOT / "scripts" / "linux" / "units" / unit).is_file()


def _lock_closure(roots: set[str]) -> set[str]:
    """ROOTS and everything they need, through pip-compile's "# via" lines
    of the Linux lock: what provision.sh's lock_stanzas installs as the
    build tools."""
    lock = (REPO_ROOT / "requirements-linux-py314.lock").read_text(encoding="utf-8")
    vias: dict[str, set[str]] = {}
    cur: str | None = None
    in_via = False
    for line in lock.splitlines():
        if re.match(r"[A-Za-z0-9]", line):
            cur = re.split(r"[=<>!~;\[ ]", line, maxsplit=1)[0].lower()
            vias[cur] = set()
            in_via = False
            continue
        if cur is None:
            continue
        m = re.match(r"\s+# via\s*(\S*)", line)
        if m:
            in_via = True
            if m.group(1):
                vias[cur].add(m.group(1).lower())
            continue
        m = re.match(r"\s+#\s+(\S+)", line)
        if in_via and m:
            vias[cur].add(m.group(1).lower())
            continue
        if line.startswith("#") or not line.strip():
            cur = None
    selected = set(roots)
    changed = True
    while changed:
        changed = False
        for name, needed_by in vias.items():
            if name not in selected and needed_by & selected:
                selected.add(name)
                changed = True
    return selected


def test_the_docs_build_tools_are_the_locks():
    """LINUX_HOST.md's manual build-tools install names setuptools, wheel
    and what they need in the lock, the set provision.sh installs: a name
    missing there would stop pip with "Hashes are required"."""
    doc = LINUX_HOST.read_text(encoding="utf-8")
    m = re.search(
        r"^awk '/\^\(([a-z|-]+)\)==/ .*' requirements-linux-py314\.lock >/tmp/build-tools\.txt$", doc, re.M
    )
    assert m, "the build-tools awk line is gone from LINUX_HOST.md"
    assert set(m.group(1).split("|")) == _lock_closure({"setuptools", "wheel"})


def _lock_closure_awk(path: Path) -> str:
    m = re.search(r"^LOCK_CLOSURE_AWK='(.*?)'$", path.read_text(encoding="utf-8"), re.M | re.S)
    assert m, f"LOCK_CLOSURE_AWK not found in {path.name}"
    return m.group(1)


def test_install_and_update_install_the_build_tools_alike():
    """provision.sh installs the build tools from the lock's own stanzas
    (pip 25.1 takes no hashes from a constraints file), and so does the
    update unit's locked re-sync (owner decision 2026-10-11). The program
    that picks those stanzas lives in both scripts, never in a file of its
    own: a rollback re-syncs against the previous checkout, which may not
    have that file. So the two copies must be the same, character for
    character, and neither script may go back to "-c <lock> setuptools
    wheel"."""
    assert _lock_closure_awk(SCRIPT) == _lock_closure_awk(APPLY_UPDATE)
    for path in (SCRIPT, APPLY_UPDATE):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"pip\S* install --require-hashes -c ", text), path.name
        assert 'roots="setuptools wheel"' in text or "lock_stanzas setuptools wheel" in text, path.name


def test_provisioned_boxes_update_from_the_lock():
    """A box provision.sh installs from the lock re-syncs from it on every
    update too: DOMOVOI_USE_LOCK=1 in /etc/default/domovoi-update. The
    update unit's own default stays off (the Beelink is not provisioned)."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert "UPDATE_DEFAULTS=$ROOT/etc/default/domovoi-update" in script
    assert re.search(r'^    "DOMOVOI_USE_LOCK=1"$', script, re.M)
    assert "USE_LOCK=${DOMOVOI_USE_LOCK:-0}" in APPLY_UPDATE.read_text(encoding="utf-8")


def _os_jobs_never_units() -> list[str]:
    m = re.search(r"^NEVER_UNITS=\((.*?)\)$", OS_JOBS.read_text(encoding="utf-8"), re.M | re.S)
    assert m, "NEVER_UNITS not found in os-jobs.sh"
    return m.group(1).split()


def test_os_jobs_masks_what_the_owner_named():
    """Owner decision 2026-10-11, under "No": apt-daily, apt-daily-upgrade
    (and unattended-upgrades), motd-news, ua-timer, apport's auto-report
    units and snapd.snap-repair. The doc names every one."""
    units = _os_jobs_never_units()
    for unit in ("apt-daily.timer", "apt-daily-upgrade.timer", "unattended-upgrades.service",
                 "motd-news.timer", "ua-timer.timer", "snapd.snap-repair.timer"):
        assert unit in units, unit
    assert any(u.startswith("apport-autoreport.") for u in units)
    doc = LINUX_HOST.read_text(encoding="utf-8")
    for unit in units:
        assert unit in doc, f"LINUX_HOST.md does not name {unit}"


def test_both_scripts_run_the_same_helper():
    """The jobs follow the answer wherever the box re-applies its config:
    provision.sh's os-jobs step and the update unit both run os-jobs.sh."""
    assert "scripts/linux/os-jobs.sh" in SCRIPT.read_text(encoding="utf-8")
    assert "OS_JOBS=$SCRIPT_DIR/os-jobs.sh" in APPLY_UPDATE.read_text(encoding="utf-8")


def test_gpu_runtime_reads_the_manifests_nvidia_keys():
    """scripts/linux/gpu-runtime.sh reads its own keys from the manifest
    that provision.sh validates (and refuses unknown keys in): each is a key
    provision.sh knows, and each package it installs has its pin."""
    helper = GPU_RUNTIME.read_text(encoding="utf-8")
    m = re.search(r"^MANIFEST_KEYS=\((.*?)\)$", helper, re.M | re.S)
    assert m, "MANIFEST_KEYS not found in gpu-runtime.sh"
    keys = set(m.group(1).split())
    p = re.search(r"^MANIFEST_KEYS=\((.*?)\)$", SCRIPT.read_text(encoding="utf-8"), re.M | re.S)
    assert keys <= set(p.group(1).split())
    manifest = _manifest()
    pkgs = re.search(r"^PKGS=\((.*?)\)$", helper, re.M).group(1).split()
    assert pkgs == ["nvidia-container-toolkit", "nvidia-container-toolkit-base",
                    "libnvidia-container-tools", "libnvidia-container1"]
    for pkg in pkgs:
        key = pkg.replace("-", "_")
        assert key in keys, pkg
        assert re.fullmatch(r"[0-9][A-Za-z0-9.+~-]*", manifest[key]), pkg


def test_gpu_runtime_never_installs_a_driver():
    """Never a Linux NVIDIA driver (inside WSL the driver is Windows'): the
    packages the brief names are refused after apt's simulation, none of
    the toolkit's own is, and nothing recommended comes along."""
    helper = GPU_RUNTIME.read_text(encoding="utf-8")
    m = re.search(r"^FORBIDDEN=\((.*?)\)$", helper, re.M | re.S)
    assert m, "FORBIDDEN not found in gpu-runtime.sh"
    globs = [g.strip("'") for g in m.group(1).split()]
    for g in ("nvidia-driver*", "nvidia-dkms*", "nvidia-kernel*", "linux-modules-nvidia*",
              "libnvidia-compute*", "nvidia-utils*", "nvidia-compute-utils*"):
        assert g in globs, g
    for pkg in ("nvidia-container-toolkit", "nvidia-container-toolkit-base",
                "libnvidia-container-tools", "libnvidia-container1"):
        assert not any(fnmatch.fnmatchcase(pkg, g) for g in globs), pkg
    assert "install -y --no-install-recommends" in helper


def test_both_scripts_run_the_same_gpu_helper():
    """Docker's GPU runtime is set up wherever the box re-applies its
    config: provision.sh's gpu-runtime step and the update unit both run
    gpu-runtime.sh."""
    assert "scripts/linux/gpu-runtime.sh" in SCRIPT.read_text(encoding="utf-8")
    assert "GPU_RUNTIME=$SCRIPT_DIR/gpu-runtime.sh" in APPLY_UPDATE.read_text(encoding="utf-8")


def test_restart_grant_is_the_docs_rule():
    script = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r'^restart_grant\(\) \{\n  echo "(.*?)"\n\}$', script, re.M)
    assert m, "restart_grant() not found"
    rule = m.group(1).replace("$SVC_USER", "domovoi").replace("$SYSTEMCTL_PATH", "/usr/bin/systemctl")
    assert rule in LINUX_HOST.read_text(encoding="utf-8")


# ─── Other systems and the venv's own CPython (owner decision 2026-11-15) ──


def _bash_array(path: Path, name: str) -> list[str]:
    m = re.search(rf"^{name}=\((.*?)\)$", path.read_text(encoding="utf-8"), re.M | re.S)
    assert m, f"{name} not found in {path.name}"
    return m.group(1).split()


def _bash_string(path: Path, name: str) -> str:
    m = re.search(rf'^{name}="([^"]*)"$', path.read_text(encoding="utf-8"), re.M)
    assert m, f"{name} not found in {path.name}"
    return m.group(1)


def test_the_system_packages_are_one_list():
    """The Windows installer's distro (Ubuntu 26.04) installs exactly what
    it did before Debian and the derivatives came in: one list, the same
    names on every system (each is a package of that name in Debian 13 and
    Ubuntu 24.04 too), so nothing is mapped and nothing changes there."""
    assert _bash_array(SCRIPT, "APT_PACKAGES") == [
        "python3", "python3-venv", "python3-dev", "build-essential", "pkg-config", "libasound2-dev",
        "git", "ffmpeg", "mpg123", "libchromaprint-tools", "xorriso", "tesseract-ocr", "tesseract-ocr-eng",
        "zstd", "ca-certificates", "curl", "gnupg", "tzdata", "sudo",
    ]


def test_docker_is_pinned_for_every_suite_the_script_installs_on():
    """Ubuntu 26.04 (resolute) keeps its keys as they were; Debian 13
    (trixie) and Ubuntu 24.04 (noble, the base of Linux Mint 22 and Pop!_OS
    24.04) each pin the same upstream releases, built for that suite."""
    m = _manifest()
    extra = _bash_string(SCRIPT, "DOCKER_EXTRA_SUITES").split()
    assert extra == ["noble", "trixie"]
    pinned = _bash_string(SCRIPT, "PINNED_SUITES").split()
    assert sorted(pinned) == sorted([m["docker_suite"], *extra])
    legacy = {"ce": "docker_ce", "ce_cli": "docker_ce_cli", "containerd_io": "containerd_io",
              "buildx_plugin": "docker_buildx_plugin", "compose_plugin": "docker_compose_plugin"}
    tags = {"resolute": "~ubuntu.26.04~resolute", "noble": "~ubuntu.24.04~noble", "trixie": "~debian.13~trixie"}
    assert m["docker_suite"] == "resolute"
    for short, key in legacy.items():
        upstream = m[key].split("-1~")[0]
        assert m[key].endswith(tags["resolute"]), key
        for suite in extra:
            v = m[f"docker_{suite}_{short}"]
            assert v.endswith(tags[suite]), (suite, short, v)
            assert v.split("-1~")[0] == upstream, f"{suite} {short}: {v} is not the release resolute pins ({upstream})"
    assert m["docker_debian_key_url"] == "https://download.docker.com/linux/debian/gpg"
    assert m["docker_debian_repo_url"] == "https://download.docker.com/linux/debian"
    assert m["docker_key_url"] == "https://download.docker.com/linux/ubuntu/gpg"


def test_the_own_cpython_is_the_locks_python_and_pinned_twice():
    """Where the system's python3 is not the lock's, the venv runs on the
    CPython the manifest pins: the lock's minor version, by version and
    SHA-256, and uv by version and SHA-256 too."""
    m = _manifest()
    lock = (REPO_ROOT / "requirements-linux-py314.lock").read_text(encoding="utf-8")
    want = re.search(r"autogenerated by pip-compile with Python (\d+\.\d+)", lock).group(1)
    assert m["python_version"].rsplit(".", 1)[0] == want
    assert re.fullmatch(r"3\.\d+\.\d+", m["python_version"])
    assert re.fullmatch(r"\d{8}", m["python_build"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["uv_version"])
    for key in ("python_sha256", "uv_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", m[key]), key
    # The helper reads only keys provision.sh validates.
    assert set(_bash_array(PY_RUNTIME, "MANIFEST_KEYS")) <= set(_bash_array(SCRIPT, "MANIFEST_KEYS"))


def test_the_own_cpython_downloads_are_checked_before_they_run():
    helper = PY_RUNTIME.read_text(encoding="utf-8")
    # Both downloads: https only, checked against the manifest before use.
    assert "curl -fsSL --proto '=https'" in helper
    assert 'fetch "$UV_URL" "$UV_TGZ" "${M[uv_sha256]}"' in helper
    assert 'fetch "$PBS_URL" "$PBS_TGZ" "${M[python_sha256]}"' in helper
    assert helper.index('fetch "$PBS_URL"') < helper.index('tar -xzf "$UV_TGZ"')
    # uv installs from the checked file (a file:// mirror), with no
    # configuration of anyone's.
    assert 'UV_PYTHON_INSTALL_MIRROR="file://$WORK/mirror"' in helper
    assert "UV_NO_CONFIG=1" in helper and "env -i" in helper
    # Never over an interpreter a service may still run on: a folder a build.
    assert "DEST=$PYTHON_DIR/$BUILD" in helper
    assert "UV_URL_BASE=https://github.com/astral-sh/uv/releases/download" in helper
    assert "PBS_URL_BASE=https://github.com/astral-sh/python-build-standalone/releases/download" in helper


def test_ubuntu_26_04_keeps_its_own_python():
    """On a box whose python3 is the lock's (Ubuntu 26.04, the Windows
    installer's distro) the venv is made from it as before, and its marker
    inputs stay as they were: the own CPython's lines only come in where it
    is used (the provision harness checks the marker hashes themselves)."""
    script = SCRIPT.read_text(encoding="utf-8")
    venv = re.search(r"^do_venv\(\) \{\n(.*?)^\}$", script, re.M | re.S).group(1)
    assert "py=python3" in venv
    assert 'if [ "$want" != "$have" ]; then' in venv
    assert '(cd "$CHECKOUT" && as_svc "$py" -m venv .venv)' in venv
    inputs = re.search(r"^inputs_venv\(\) \{\n(.*?)^\}$", script, re.M | re.S).group(1)
    assert "  if needs_own_python; then" in inputs


def test_both_scripts_run_the_same_python_helper():
    """The own CPython is installed by one helper, from the verified copy,
    wherever the venv may move: provision.sh's venv step and the update
    unit's step 4b."""
    assert "src_file scripts/linux/python-runtime.sh" in SCRIPT.read_text(encoding="utf-8")
    text = APPLY_UPDATE.read_text(encoding="utf-8")
    assert "PY_RUNTIME=$SCRIPT_DIR/python-runtime.sh" in text
    assert "PY_MANIFEST=$SCRIPT_DIR/provision-manifest.conf" in text
    # Only a venv on Domovoi's own CPython is ever moved.
    assert 'case $home in "$OWN_PY_DIR"/?*) ;; *) return 0 ;; esac' in text


def test_old_cpython_builds_go_only_after_a_good_update():
    """The update unit removes the builds no venv needs only at the end of
    a successful update: one call, between the last soft step and
    `finish ok`, never in the rollback."""
    text = APPLY_UPDATE.read_text(encoding="utf-8")
    calls = re.findall(r"^\s+prune_python$", text, re.M)
    assert len(calls) == 1
    assert "    reconcile_wake_trainer || true\n    prune_python\n    finish ok\n" in text
    rollback = re.search(r"^rollback\(\) \{\n(.*?)^\}$", text, re.M | re.S).group(1)
    assert "prune_python" not in rollback


def test_install_and_provision_accept_the_same_systems():
    """install.sh's gate lets through what provision.sh's preflight
    installs: Ubuntu, Debian 13, and Ubuntu derivatives on the suites the
    manifest pins Docker for."""
    install = INSTALL.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    pinned = sorted(_bash_string(SCRIPT, "PINNED_SUITES").split())
    m = re.search(r"^        (noble\|resolute)\) ;;$", install, re.M)
    assert m and sorted(m.group(1).split("|")) == [s for s in pinned if s != "trixie"]
    assert '[ "$ver" = 13 ] || die' in script and 'if [ "$ver" != 13 ]; then' in install
    for text in (install, script):
        assert "Fedora, Arch and others are not supported yet" in text
        assert "/issues" in text


def _os_jobs_dry_run(root: Path, internet: str) -> subprocess.CompletedProcess:
    # The root as bash sees it (Git Bash: /c/...), which the helper wants.
    return subprocess.run(
        [BASH, "-c", 'export DOMOVOI_PROVISION_ROOT=$(cd "$0" && pwd) DOMOVOI_PROVISION_TEST_HARNESS=1; '
                     'exec bash "$1" --internet "$2" --wsl no --dry-run',
         root.as_posix(), OS_JOBS.as_posix(), internet],
        capture_output=True, text=True, timeout=60,
    )


@requires_bash
def test_os_jobs_on_debian(tmp_path):
    """Debian 13 has apt's timers and unattended-upgrades (what never
    masks there) and none of Ubuntu's own; popularity-contest is a cron job
    the administrator opted into, left alone under never and said so."""
    vendor = tmp_path / "usr" / "lib" / "systemd" / "system"
    vendor.mkdir(parents=True)
    for unit in ("apt-daily.timer", "apt-daily-upgrade.timer", "unattended-upgrades.service", "man-db.timer"):
        (vendor / unit).write_text("", encoding="utf-8")
    (tmp_path / "etc").mkdir()
    conf = tmp_path / "etc" / "popularity-contest.conf"
    conf.write_text('MY_HOSTID="0123"\nPARTICIPATE="yes"\nUSEHTTP="yes"\n', encoding="utf-8")
    proc = _os_jobs_dry_run(tmp_path, "never")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for unit in ("apt-daily.timer", "apt-daily-upgrade.timer", "unattended-upgrades.service"):
        assert f"would mask {unit} (internet: never)" in proc.stdout
    for absent in ("motd-news", "ua-timer", "apport", "snapd", "man-db"):
        assert absent not in proc.stdout.split("os-jobs:")[0], absent
    assert 'popularity-contest reports daily (PARTICIPATE="yes"' in proc.stdout
    # Not opted in, or another answer: nothing to say about it.
    assert "popularity-contest" not in _os_jobs_dry_run(tmp_path, "always").stdout
    conf.write_text('PARTICIPATE="no"\n', encoding="utf-8")
    assert "popularity-contest" not in _os_jobs_dry_run(tmp_path, "never").stdout


def test_the_fresh_install_test_is_smallest_and_pinned():
    """The CI's fresh install (linux-install.yml) downloads no AI models
    and boots cloud images pinned by checksum."""
    vm = INSTALL_VM.read_text(encoding="utf-8")
    for flag in ("--skip-models", "--no-ai-models", "--no-vision-model", "--tier floor"):
        assert flag in vm, flag
    assert re.search(r"^    IMAGE_SUM=sha256:[0-9a-f]{64}$", vm, re.M)
    assert re.search(r"^    IMAGE_SUM=sha512:[0-9a-f]{128}$", vm, re.M)
    assert "/current/" not in vm and "/latest/" not in vm
    wf = (REPO_ROOT / ".github" / "workflows" / "linux-install.yml").read_text(encoding="utf-8")
    assert "system: [ubuntu-26.04, debian-13]" in wf
    assert "schedule:" in wf and "workflow_dispatch:" in wf and '"scripts/linux/**"' in wf
    # Pushes to main only; a branch runs it by hand.
    assert "  push:\n    branches: [main]\n    paths:\n" in wf
    assert "secrets." not in wf
    timeout = re.search(r"timeout-minutes: (\d+)", wf)
    assert timeout and int(timeout.group(1)) <= 50


def test_every_option_the_mac_helper_passes_is_one_provision_parses() -> None:
    """mac/Sources/DomovoiMacKit/Provision.swift builds provision.sh's command
    line inside the VM; an option the script doesn't know stops the install
    with a usage error, on a Mac, where no CI runs it."""
    swift = (REPO_ROOT / "mac" / "Sources" / "DomovoiMacKit" / "Provision.swift").read_text(encoding="utf-8")
    body = swift[swift.index("public static func build("):swift.index("/// One `@@DOMOVOI {json}`")]
    passed = set(re.findall(r'"(--[a-z][a-z-]*)"', body))
    assert {"--host-stt-url", "--host-stt-model", "--host-stt-shares-gpu", "--vm-guard-lan"} <= passed
    text = SCRIPT.read_text(encoding="utf-8")
    parsed = set()
    for m in re.finditer(r"^    ((?:--[a-z][a-z-]*\|)*--[a-z][a-z-]*)(?:\|-h\|--help)?\)$", text, re.M):
        parsed.update(m.group(1).split("|"))
    assert passed <= parsed, f"provision.sh doesn't parse {sorted(passed - parsed)}"


def test_the_shared_gpu_line_is_written_only_with_the_host_speech_service() -> None:
    """WHISPER_REMOTE_SHARES_GPU rides --host-stt-url: true with
    --host-stt-shares-gpu (the Mac), false without; a key the core may not
    know yet (ENV_NEWER_KEYS), and one the script manages."""
    text = SCRIPT.read_text(encoding="utf-8")
    block = text[text.index('  if [ -n "$HOST_STT_URL" ]; then\n    echo "WHISPER_REMOTE_URL='):]
    block = block[:block.index("\n  fi\n")]
    assert 'echo "WHISPER_REMOTE_SHARES_GPU=true"' in block and 'echo "WHISPER_REMOTE_SHARES_GPU=false"' in block
    for name in ("ENV_MANAGED_KEYS", "ENV_NEWER_KEYS"):
        m = re.search(rf"^{name}=\(([^)]*)\)", text, re.M)
        assert m and "WHISPER_REMOTE_SHARES_GPU" in m.group(1).split(), name
    assert 'usage_error "--host-stt-shares-gpu goes with --host-stt-url"' in text
