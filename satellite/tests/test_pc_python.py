"""The satellite's own Python in satellite/scripts/install-pc.sh (2026-10-15):
the lock line it reads with bash, where it looks for the file, the checks
that hold the file to its pin (as the installing account, and again as root
on root's own copy), the glibc floor, and which Python a computer gets.

Each function runs on its own in bash (Git Bash on Windows, bash on Linux)
with the script's own text, so a test fails if the script's check changes.
The real install, as root in a container, is test_install_pc_sandbox.py;
the end-to-end on Ubuntu 22.04, Debian 11, Mint 21 and Debian 12 is
functional-testing/wf-sat-pc-python/.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "satellite" / "scripts" / "install-pc.sh"
LOCK = ROOT / "satellite" / "locks" / "linux-python.txt"
PBS = "https://github.com/astral-sh/python-build-standalone/releases/download/"


def _bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    git = shutil.which("git")
    for c in ([Path(git).parent.parent / "bin" / "bash.exe"] if git else []) + [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"]:
        if c.is_file():
            return str(c)
    return None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _function(name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{.*?^\}}$", _text(), re.S | re.M)
    assert m, name
    return m.group(0)


def _assignment(name: str) -> str:
    m = re.search(rf"^{name}=.*$", _text(), re.M)
    assert m, name
    return m.group(0)


def _posix(p: Path) -> str:
    """A path as Git Bash names it (C:\\x -> /c/x); unchanged on Linux."""
    s = str(p)
    if sys.platform == "win32" and re.match(r"^[A-Za-z]:\\", s):
        s = "/" + s[0].lower() + s[2:].replace("\\", "/")
    return s


def run(script: str, *, env: dict | None = None) -> subprocess.CompletedProcess:
    prelude = ('die() { echo "die: $*" >&2; exit "${2:-1}"; }\nsay() { printf \'%s\\n\' "$*"; }\n'
               + "PIN_VERSION='' PIN_FILE='' PIN_SIZE='' PIN_SHA='' PIN_URL=''\n")
    return subprocess.run([BASH, "-c", prelude + script], capture_output=True, text=True,
                          env={**os.environ, **(env or {}), "MSYS_NO_PATHCONV": "1"}, timeout=120)


# ─── read_pin: the lock line for this machine ─────────────────────────────

def _read_pin_with(lock_text: str, machine: str, tmp_path: Path) -> subprocess.CompletedProcess:
    (tmp_path / "satellite" / "locks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "satellite" / "locks" / "linux-python.txt").write_bytes(lock_text.encode("utf-8"))
    return run("\n".join([_assignment("PY_LOCK_REL"), _assignment("PBS_RELEASES"), _function("read_pin"),
                          f"SRC={_posix(tmp_path)} MACHINE={machine}",
                          'if read_pin; then echo "PIN $PIN_VERSION $PIN_FILE $PIN_SIZE $PIN_SHA $PIN_URL"; '
                          'else echo none; fi']))


def test_read_pin_reads_the_tree_s_lock_as_the_core_does(tmp_path):
    rows = [ln.split() for ln in LOCK.read_text(encoding="utf-8").splitlines() if ln.startswith("python ")]
    assert {r[1] for r in rows} == {"x86_64", "aarch64"}
    for r in rows:
        got = _read_pin_with(LOCK.read_text(encoding="utf-8"), r[1], tmp_path)
        assert got.stdout.split() == ["PIN", r[2], r[3], r[4], r[5], r[6]], (got.stdout, got.stderr)
    assert _read_pin_with(LOCK.read_text(encoding="utf-8"), "riscv64", tmp_path).stdout.strip() == "none"


GOOD_FILE = "cpython-3.14.8+20261009-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
GOOD_URL = PBS + "20261009/" + urllib.parse.quote(GOOD_FILE)
GOOD_SHA = "a" * 64


@pytest.mark.parametrize("line", [
    f"python x86_64 3.14.8 {GOOD_FILE} 100 {GOOD_SHA} https://evil.example/{GOOD_FILE}",
    f"python x86_64 3.14.8 {GOOD_FILE} 100 {GOOD_SHA} {PBS}20261009/{GOOD_FILE}",      # + not quoted
    f"python x86_64 3.14.8 {GOOD_FILE} 100 {GOOD_SHA} {PBS}20261010/" + urllib.parse.quote(GOOD_FILE),
    f"python x86_64 3.14.7 {GOOD_FILE} 100 {GOOD_SHA} {GOOD_URL}",
    f"python x86_64 3.14.8 {GOOD_FILE} 1e3 {GOOD_SHA} {GOOD_URL}",
    f"python x86_64 3.14.8 {GOOD_FILE} 100 {'A' * 64} {GOOD_URL}",
    f"python x86_64 3.14.8 {GOOD_FILE} 100 {GOOD_SHA} {GOOD_URL} extra",
    f"python x86_64 3.14.8 ../{GOOD_FILE} 100 {GOOD_SHA} {GOOD_URL}",
    f"python x86_64 3.14.8 {GOOD_FILE.replace('_stripped', '')} 100 {GOOD_SHA} {GOOD_URL}",
    f"python x86_64 3.14.8 {GOOD_FILE};id 100 {GOOD_SHA} {GOOD_URL}",
])
def test_read_pin_refuses_a_bad_line(tmp_path, line):
    got = _read_pin_with(line + "\n", "x86_64", tmp_path)
    assert got.returncode == 1 and "die:" in got.stderr and "PIN" not in got.stdout, (got.stdout, got.stderr)


def test_read_pin_takes_a_good_line(tmp_path):
    got = _read_pin_with(f"# x\npython x86_64 3.14.8 {GOOD_FILE} 100 {GOOD_SHA} {GOOD_URL}\n", "x86_64", tmp_path)
    assert got.stdout.split() == ["PIN", "3.14.8", GOOD_FILE, "100", GOOD_SHA, GOOD_URL], got.stderr


# ─── payload_core: where to look for the cached file first ────────────────

@pytest.mark.parametrize("core,want", [
    ("http://192.168.0.117:6370", "http://192.168.0.117:6370"),
    ("http://core.home.arpa:6370", "http://core.home.arpa:6370"),
    ("https://domovoi.local", "https://domovoi.local"),
    ("http://[fd00::17]:6370", "http://[fd00::17]:6370"),
    ("http://192.168.0.117:6370/x", ""),             # a path
    ("http://a@192.168.0.117:6370", ""),             # userinfo
    ("file:///etc/passwd", ""),
    ("http://192.168.0.117:6370\nhttp://evil", ""),
    ("http://$(id)", ""),
    ("ftp://192.168.0.117", ""),
])
def test_payload_core_is_a_plain_address_or_nothing(core, want):
    payload = "domovoi-room:1?core=" + urllib.parse.quote(core, safe="") + "&fp=SHA256%3Ax&room=R"
    got = run(_function("payload_core") + f"\nPAYLOAD={_sh_quote(payload)}\nprintf '[%s]' \"$(payload_core)\"")
    assert got.stdout == f"[{want}]", (got.stdout, got.stderr)


def test_payload_core_ignores_a_raw_value_with_odd_characters():
    got = run(_function("payload_core") + "\nPAYLOAD='domovoi-room:1?core=http://x;id&fp=a&room=R'\n"
              "printf '[%s]' \"$(payload_core)\"")
    assert got.stdout == "[]"


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


# ─── pinned_ok: size, then SHA-256, never a link ──────────────────────────

BODY = b"python-build-standalone" * 50


def _pinned_ok(path: Path, size: int, sha: str) -> int:
    got = run("\n".join([_function("pinned_ok"), 'SUDO_CHECK=""',
                         f"PIN_SIZE={size} PIN_SHA={sha}", f"pinned_ok {_sh_quote(_posix(path))}; echo rc=$?"]))
    m = re.search(r"rc=(\d+)", got.stdout)
    assert m, (got.stdout, got.stderr)
    return int(m.group(1))


def test_pinned_ok_holds_the_file_to_its_size_and_hash(tmp_path):
    f = tmp_path / "py.tar.gz"
    f.write_bytes(BODY)
    sha = hashlib.sha256(BODY).hexdigest()
    assert _pinned_ok(f, len(BODY), sha) == 0
    assert _pinned_ok(f, len(BODY) + 1, sha) != 0
    assert _pinned_ok(f, len(BODY), "b" * 64) != 0
    f.write_bytes(BODY[:-1] + b"X")                 # same size, other bytes
    assert _pinned_ok(f, len(BODY), sha) != 0
    f.write_bytes(BODY + b"X")                      # one byte too many (fetch_to's cap)
    assert _pinned_ok(f, len(BODY), sha) != 0
    assert _pinned_ok(tmp_path / "missing", len(BODY), sha) != 0


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_pinned_ok_refuses_a_link(tmp_path):
    """Even one whose own size (its target's length) is the pinned size and
    whose target is the pinned file: only the link check stops it."""
    real = tmp_path / "real"
    body = b"p" * len(str(real))
    real.write_bytes(body)
    (tmp_path / "link").symlink_to(real)
    assert (tmp_path / "link").lstat().st_size == len(body)
    assert _pinned_ok(real, len(body), hashlib.sha256(body).hexdigest()) == 0
    assert _pinned_ok(tmp_path / "link", len(body), hashlib.sha256(body).hexdigest()) != 0


def test_fetch_to_reads_at_most_one_byte_past_the_pin():
    body = _function("fetch_to")
    assert body.count('/usr/bin/head -c "$((PIN_SIZE + 1))"') == 2
    assert "--proto '=http,https' --proto-redir '=https'" in body and "--max-redirs 5" in body


# ─── root's own copy is checked again, by root ────────────────────────────

def test_root_checks_its_own_copy_before_unpacking_it():
    body = _function("install_root_s_python")
    copy = body.index('$SUDO /usr/bin/install -o root -g root -m 0600 "$PYSTAGE/$PIN_FILE" "$PY_PART"')
    check = body.index('if ! pinned_ok "$PY_PART"; then')
    unpack = body.index('$SUDO /usr/bin/tar -xzf "$PY_PART" -C "$PY_NEW" --no-same-owner')
    assert copy < body.index("SUDO_CHECK=$SUDO") < check < unpack
    assert '$PYSTAGE' not in body[check:]          # nothing after the check reads the account's copy
    assert "-path '*/lib-dynload/_dbm.*.so' -type f -delete" in body
    assert '$SUDO /usr/bin/chmod -R go-w "$PY_NEW"' in body
    assert body.index("python_is_root_s \"$PY\"") > body.index("PY=$PINNED_PY")


@pytest.mark.skipif(sys.platform == "win32", reason="runs install -o root")
@pytest.mark.skipif(sys.platform != "win32" and os.geteuid() != 0, reason="root (the sandbox) only")
def test_a_staged_file_that_changed_is_refused_by_root(tmp_path):
    lib = tmp_path / "lib"
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "py.tar.gz").write_bytes(BODY[:-1] + b"X")
    script = "\n".join([
        f"ROOT_LIB={lib}", 'PYTHON_HOME=$ROOT_LIB/python', 'PINNED_PY=$PYTHON_HOME/bin/python3',
        'PY_PART=$ROOT_LIB/.python.tar.gz', 'PY_NEW=$ROOT_LIB/.python.new', 'PY_OLD=$ROOT_LIB/.python.old',
        'SUDO="" SUDO_CHECK=""', f"PYSTAGE={stage} PIN_FILE=py.tar.gz PIN_VERSION=3.14.8",
        f"PIN_SIZE={len(BODY)} PIN_SHA={hashlib.sha256(BODY).hexdigest()}",
        f"mkdir -p {lib}/python/bin && echo old > {lib}/python/bin/python3",
        _function("pinned_ok"), _function("python_is_root_s"), _function("install_root_s_python"),
        "install_root_s_python; echo survived"])
    got = run(script)
    assert got.returncode == 1 and "changed after it was checked" in got.stderr, (got.stdout, got.stderr)
    assert "survived" not in got.stdout
    assert (lib / "python" / "bin" / "python3").read_text() == "old\n"
    assert not (lib / ".python.tar.gz").exists() and not (lib / ".python.new").exists()


# ─── the glibc floor ──────────────────────────────────────────────────────

@pytest.mark.parametrize("answer,ok", [
    ("glibc 2.28", True), ("glibc 2.31", True), ("glibc 2.35", True), ("glibc 3.0", True),
    ("glibc 2.27", False), ("glibc 2.17", False), ("", False), ("musl 1.2", False), ("glibc x.y", False),
])
def test_glibc_ok(answer, ok):
    stub = f"/usr/bin/getconf() {{ [ -n {_sh_quote(answer)} ] && echo {_sh_quote(answer)}; }}\n"
    got = run(stub + _assignment("MIN_GLIBC_MINOR") + "\n" + _function("glibc_ok") + "\nglibc_ok; echo rc=$?")
    assert got.stdout.strip() == ("rc=0" if ok else "rc=1"), (got.stdout, got.stderr)


# ─── root runs only a root-owned Python ───────────────────────────────────

def test_root_runs_only_the_system_s_python_or_its_own_copy():
    body = _function("python_is_root_s")
    assert '[ "$1" = /usr/bin/python3 ] && return 0' in body
    assert '[ "$1" = "$PINNED_PY" ]' in body and '"0 "*[0145][0145]) ;;' in body
    for p in ('"$ROOT_LIB"', '"$PYTHON_HOME"', '"$PYTHON_HOME/bin"', '"$PINNED_PY"'):
        assert p in body
    assert 'python_is_root_s "$PY" || die' in _function("record_set")
    assert 'python_is_root_s "$PY" || return 0' in _function("record_get")
    assert 'python_is_root_s "$PY"; then' in _function("purge_satellite_files")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners")
def test_python_is_root_s_refuses_one_the_account_could_change(tmp_path):
    lib = tmp_path / "lib"
    (lib / "python" / "bin").mkdir(parents=True)
    py = lib / "python" / "bin" / "python3"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    for d in (lib, lib / "python", lib / "python" / "bin"):
        d.chmod(0o755)
    script = "\n".join([f"ROOT_LIB={lib}", "PYTHON_HOME=$ROOT_LIB/python", "PINNED_PY=$PYTHON_HOME/bin/python3",
                        _function("python_is_root_s"), 'python_is_root_s "$PINNED_PY"; echo pinned=$?',
                        f'python_is_root_s {tmp_path}/other; echo other=$?',
                        'python_is_root_s /usr/bin/python3; echo system=$?'])
    got = run(script)
    owned_by_root = os.geteuid() == 0
    assert f"pinned={0 if owned_by_root else 1}" in got.stdout, got.stdout
    assert "other=1" in got.stdout and "system=0" in got.stdout
    if owned_by_root:
        (lib / "python").chmod(0o775)               # group-writable: no longer root's alone
        assert "pinned=1" in run(script).stdout


# ─── which Python a computer gets ─────────────────────────────────────────

def test_only_a_debian_family_computer_uses_its_own_python():
    text = _text()
    step0 = text[text.index("# ─── 0. the system, and which Python"):text.index("# ─── 1. the payload")]
    assert 'if [ "$FAMILY" != debian ]; then\n  SYSTEM_WHY=' in step0
    assert "sys.version_info >= (3, 11)" in step0
    assert 'pc-$(/usr/bin/python3 -I -c' in step0          # a list for its version, or it is not used
    assert "*) if [ -z \"$SYSTEM_WHY\" ]; then USE_PY=system; else USE_PY=pinned; fi ;;" in step0
    assert '[ "$USE_PY" = system ] || stage_pinned_python' in step0
    # Nothing is written before step 1 but the temporary folder, which goes at exit.
    assert "$SUDO" not in step0 and "record_set" not in step0
    assert "trap '[ -z \"$PYSTAGE\" ] || /usr/bin/rm -rf \"$PYSTAGE\"' EXIT" in text
    stage = _function("stage_pinned_python")
    assert stage.index("pinned_ok \"$f\"; then") < stage.index("/usr/bin/tar -xzf")
    assert stage.count("pinned_ok") == 2 and "$SUDO" not in stage


def test_the_lock_names_a_python_with_a_package_list_for_each_machine():
    for row in (ln.split() for ln in LOCK.read_text(encoding="utf-8").splitlines() if ln.startswith("python ")):
        tag = "cp" + "".join(row[2].split(".")[:2])
        assert (ROOT / "satellite" / "locks" / f"pc-{tag}-{row[1]}.txt").is_file(), row


def test_the_tool_check_comes_before_any_other_tool_runs():
    """A Debian with /bin unmerged has no /usr/bin/uname: the check must
    name the cause (and the fix) rather than fail on the first tool."""
    text = _text()
    check = text.index('for _t in $TOOLS; do [ -x "$_t" ]')
    head = text[text.index("# ─── arguments"):check]
    used = set(re.findall(r"/usr/s?bin/[a-z0-9_-]+", "\n".join(
        ln for ln in head.splitlines() if not ln.lstrip().startswith("#") and "usage()" not in ln)))
    assert used <= {"/usr/bin/id", "/usr/bin/uname", "/usr/sbin/runuser"} | {
        t for t in re.findall(r"/usr/s?bin/[a-z0-9_-]+", text[text.index('TOOLS="'):check])}, used
    assert 'if [ -x /usr/bin/uname ] && [ "$(/usr/bin/uname -s)" != Linux ]; then' in head
    assert "sudo apt install usrmerge" in text[check:check + 800]


def test_every_full_path_the_script_runs_is_checked_or_optional():
    text = _text()
    m = re.search(r'^TOOLS="(.*?)"$', text, re.S | re.M)
    assert m
    checked = set(m.group(1).split()) | {"/usr/sbin/runuser"}
    optional = {"/usr/bin/sudo", "/usr/bin/loginctl", "/usr/bin/systemctl", "/usr/bin/pkill", "/usr/bin/python3",
                "/usr/bin/dpkg-query", "/usr/bin/rpm", "/usr/bin/dnf", "/usr/bin/zypper", "/usr/bin/pacman",
                "/usr/bin/curl", "/usr/bin/wget", "/usr/bin/getconf", "/usr/sbin/nologin"}
    used = set(re.findall(r"/usr/s?bin/[a-z0-9_-]+", text))
    assert used - checked - optional == set(), sorted(used - checked - optional)
