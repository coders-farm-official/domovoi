"""The satellite's own Python in satellite/scripts/install-pc.sh (2026-10-15):
the lock line it reads with bash, where it looks for the file, the checks
that hold the file to its pin (as the installing account, and again as root
on root's own copy), the glibc floor, and which Python a computer gets.

Each function runs on its own in bash (Git Bash on Windows, bash on Linux)
with the script's own text, so a test fails if the script's check changes.
The real install, as root in a container, is test_install_pc_sandbox.py;
the end-to-end on Ubuntu 22.04, Debian 11, Mint 21 and Debian 12 is run
by the maintainers' functional test harness, kept outside this repo.
"""

from __future__ import annotations

import hashlib
import json
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
    # On stdin: a long script outgrows Windows' command line.
    return subprocess.run([BASH, "-s"], input=prelude + script, capture_output=True, text=True,
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


# ─── payload_core: the address step 0 may ask, exactly step 1's ─────────────

FIXTURE = ROOT / "docs" / "uml" / "spare-device-enrol-fixture.json"
# What step 0 leaves to step 1: a room name's letters, control characters
# and UTF-8 (they name no address). Every other refusal is step 0's too.
ROOM_ONLY = {"bad_room"}
ROOM_UTF8 = "room=K%C3%28che"


def _payload_cores(payloads: list[str]) -> list[str]:
    """payload_core for each payload, in one bash run."""
    lines = [_function(f) for f in ("ipv4_ok", "ipv6_first", "home_host", "core_normal", "payload_core")]
    for p in payloads:
        lines.append(f"PAYLOAD={_sh_quote(p)}; printf '[%s]\\n' \"$(payload_core)\"")
    got = run("\n".join(lines))
    out = re.findall(r"^\[(.*)\]$", got.stdout, re.M)
    assert len(out) == len(payloads), (got.stdout, got.stderr)
    return out


def _join_link(payload: str) -> bool:
    """The QR code's join link (<core>/join#<payload>, 2026-10-15): step 1
    takes one, but install-pc.sh's own argument is only ever a
    ``domovoi-room:`` payload (anything else is "unknown argument"), so
    payload_core never sees a link, and gives no address for one."""
    return payload.strip(" \t\r\n").lower().startswith(("http://", "https://"))


def test_payload_core_agrees_with_the_shared_fixture():
    """Every payload step 1 takes gives its own core address; every one it
    refuses gives none, but for the room-name rules left to step 1."""
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    valid, invalid = doc["valid"], doc["invalid"]
    got = _payload_cores([c["payload"] for c in valid] + [c["payload"] for c in invalid])
    assert any(_join_link(c["payload"]) for c in valid), "the fixture has join links"
    for case, core in zip(valid, got[:len(valid)]):
        if _join_link(case["payload"]):
            assert core == "", (case["name"], core)
            continue
        assert core == case["expect"]["core"], (case["name"], core)
    for case, core in zip(invalid, got[len(valid):]):
        if case["reason"] in ROOM_ONLY or ROOM_UTF8 in case["payload"]:
            continue
        assert core == "", (case["reason"], case["payload"][:120], core)


CORES = [
    "http://192.168.1.20:6370", "HTTP://Core.LOCAL:06370/", "https://domovoi.local", "http://10.1.2.3",
    "http://172.16.0.1:1", "http://172.31.255.255:65535", "http://172.32.0.1", "http://192.169.0.1",
    "http://192.168.1.01", "http://300.1.1.1.local", "http://1.2.3", "http://127.0.0.1:6370",
    "http://100.64.0.1", "http://8.8.8.8", "http://core.home.arpa", "http://core.lan:0", "http://core.lan:65536",
    "http://core.internal:", "http://.local", "http://-x.local", "http://x-.local", "http://a..b.local",
    "http://[fd00::17]:6370", "http://[FD00::17]", "http://[fe80::1]", "http://[febf::1]", "http://[fec0::1]",
    "http://[fc00::]", "http://[::ffff:192.168.1.1]", "http://[fd00::192.168.1.1]", "http://[fd00:::1]",
    "http://[fd00::1::2]", "http://[fd00:1:2:3:4:5:6:7]", "http://[fd00:1:2:3:4:5:6:7:8]",
    "http://[fd00:1:2:3:4:5:6::7]", "http://[fd00::1%eth0]", "http://[fd00::1", "http://[fd00::12345]",
    "http://[:fd00::1]", "http://[fd00::1:]", "http://[1:2:3:4:5:6:7::]", "http://[::]", "http://[fd00::1]x",
    "http://192.168.1.20:6370/x", "http://a@192.168.1.20", "http://192.168.1.20?x", "http://192.168.1.20#x",
    "http://192.168.1.20//", "file:///etc/passwd", "ftp://192.168.1.20", "http:/192.168.1.20",
    "http://192.168.1.20 ", " http://192.168.1.20", "http://192.168.1.20\n", "http://Kü.local",
    "http://x.local:6370:1", "http://x.local:+80", "1http://x.local", "http://x_y.local",
    "http://" + "a" * 240 + ".local", "http://" + "a" * 250 + ".local",
]


def test_core_normal_is_normalize_core():
    """The address rule itself, held to satellite/enrolment.py's own."""
    from satellite import enrolment

    payloads, want = [], []
    for core in CORES:
        payloads.append("domovoi-room:1?core=" + urllib.parse.quote(core, safe="")
                        + "&fp=SHA256%3A4gq%2BpNW%2FlnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA&room=Den")
        try:
            want.append(enrolment.parse(payloads[-1]).core)
        except enrolment.EnrolmentError:
            want.append("")
    assert dict(zip(CORES, _payload_cores(payloads))) == dict(zip(CORES, want))


def test_payload_core_ignores_a_raw_value_with_odd_characters():
    assert _payload_cores(["domovoi-room:1?core=http://x;id&fp=a&room=R",
                           "domovoi-room:1?core=http%3A%2F%2F192.168.1.9%00&fp=SHA256%3A4gq%2BpNW%2FlnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA&room=R",
                           "domovoi-room:1?core=http%3A%2F%2F192.168.1.9&fp=SHA256%3A4gq%2BpNW%2FlnGijI3MZ4vGYcz%00KK6r6YgmCylej3GSSoOA&room=R",
                           "domovoi-room:1?core=http%3A%2F%2F192.168.1.9%0A&fp=SHA256%3A4gq%2BpNW%2FlnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA&room=R",
                           ]) == ["", "", "", ""]


def test_step_0_asks_the_server_only_at_that_address_and_runs_python_isolated():
    stage = _function("stage_pinned_python")
    assert 'core=$(payload_core)' in stage
    assert 'fetch_to "$core/v1/satellite-linux/files/$PIN_FILE" "$f" 0' in stage   # no redirects from it
    assert 'fetch_to "$PIN_URL" "$f" 5' in stage
    text = _text()
    # Every Python the script runs, the staged one included, runs with -I.
    runs = [ln for ln in text.splitlines() if re.search(r"(\$PY|/usr/bin/python3) -", ln)]
    assert len(runs) >= 12, runs
    for line in runs:
        assert re.search(r"(\$PY|/usr/bin/python3) -I ", line), line
    assert "-P -s" not in text and "PYTHONPATH=" not in text


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
    assert "--proto '=http,https' --proto-redir '=https' --max-redirs \"$hops\"" in body
    assert "--max-redirect=0" in body and "[ \"$hops\" -gt 0 ] && [[ $loc =~ ^https://" in body


class _Server:
    """http://127.0.0.1:PORT: /file (the pinned bytes), /endless (never
    ends), /to-http and /to-https (redirects); what was asked, in order."""

    def __init__(self, body: bytes) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        seen = self.seen = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                seen.append(self.path)
                if self.path.startswith("/to-"):
                    target = ("https://127.0.0.1:9/file" if self.path == "/to-https"
                              else f"http://127.0.0.1:{self.server.server_address[1]}/file")
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                try:
                    if self.path == "/endless":
                        while True:
                            self.wfile.write(b"x" * 65536)
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="the script names /usr/bin/curl and /usr/bin/wget")
@pytest.mark.parametrize("tool", ["curl", "wget"])
def test_fetch_to_caps_the_bytes_and_follows_no_redirect_it_should_not(tmp_path, tool):
    if not Path(f"/usr/bin/{tool}").exists():
        pytest.skip(f"no {tool} here")
    body = _function("fetch_to")
    if tool == "wget":
        body = body.replace("if [ -x /usr/bin/curl ]; then", "if false; then")
    srv = _Server(BODY)
    try:
        def fetch(path: str, hops: int) -> bytes:
            out = tmp_path / "out"
            got = run(body + f"\nPIN_SIZE={len(BODY)}\nfetch_to {srv.url}{path} {out} {hops}; echo done")
            assert "done" in got.stdout, got.stderr
            return out.read_bytes() if out.exists() else b""

        assert fetch("/file", 0) == BODY
        assert len(fetch("/endless", 0)) == len(BODY) + 1          # cut one byte past the pin
        srv.seen.clear()
        assert fetch("/to-http", 5) == b"" and srv.seen == ["/to-http"]    # never to plain http
        srv.seen.clear()
        assert fetch("/to-https", 0) == b"" and srv.seen == ["/to-https"]  # the server: no redirect at all
        assert not list(tmp_path.glob("*.headers"))
    finally:
        srv.close()


# ─── root's own copy is checked again, by root ────────────────────────────

def test_root_checks_its_own_copy_before_unpacking_it():
    body = _function("install_root_s_python")
    copy = body.index('$SUDO /usr/bin/install -o root -g root -m 0600 "$PYSTAGE/$PIN_FILE" "$PY_PART"')
    check = body.index('if ! pinned_ok "$PY_PART"; then')
    unpack = body.index('$SUDO /usr/bin/tar -xzf "$PY_PART" -C "$PY_NEW" --no-same-owner')
    assert copy < body.index("SUDO_CHECK=$SUDO") < check < unpack
    assert '$PYSTAGE' not in body[check:]          # nothing after the check reads the account's copy
    assert "-path '*/lib-dynload/_dbm.*.so' -type f -delete" in body
    # Readable, root's alone, whatever the umask made of tar's folders.
    assert '$SUDO /usr/bin/chmod -R u=rwX,go=rX "$PY_NEW"' in body and "go-w" not in body
    # The landmark goes in before the first run, and the run proves it held.
    landmark = body.index('landmark_zip | $SUDO /usr/bin/tee "$PY_NEW/python/lib/$(landmark_name)"')
    switch = body.index("\n  PY=$PINNED_PY\n")
    assert unpack < landmark < switch < body.index('python_stays_home "$PYTHON_HOME"')
    assert body.index("python_is_root_s \"$PY\"") > switch


def test_the_staged_copy_gets_the_landmark_and_loses_dbm_before_it_runs():
    body = _function("stage_pinned_python")
    unpack = body.index('/usr/bin/tar -xzf "$f" -C "$PYSTAGE/x" --no-same-owner')
    dbm = body.index("-path '*/lib-dynload/_dbm.*.so' -type f -delete")
    landmark = body.index('landmark_zip >"$zip"')
    first_run = body.index("$PY -I -c")
    assert unpack < dbm < landmark < first_run < body.index('python_stays_home "$PYSTAGE/x/python"')
    assert 'zip=$PYSTAGE/x/python/lib/$(landmark_name)' in body
    # A private folder when the account has one, all the way up.
    assert "\n  make_stage\n" in body and "mktemp" not in body
    stage = _function("make_stage")
    # Created and used by the home's resolved path only, and checked again once it exists.
    assert 'real=$(cd -P -- "$home" 2>/dev/null && pwd -P)' in stage and 'private_dir "$real"' in stage
    assert '/usr/bin/mktemp -d "$real/.domovoi-python.XXXXXXXX"' in stage and '"$home/' not in stage
    assert '[ "$(cd -P -- "$PYSTAGE" 2>/dev/null && pwd -P)" = "$PYSTAGE" ]' in stage


def _fake_python(tmp_path: Path, prefix: str, os_file: str) -> Path:
    py = tmp_path / "fakepy"
    py.write_bytes(f"#!/bin/sh\nprintf '%s\\n%s\\n' {_sh_quote(prefix)} {_sh_quote(os_file)}\n".encode("ascii"))
    py.chmod(0o755)
    return py


@pytest.mark.parametrize("prefix,os_file,ok", [
    ("{tree}", "{tree}/lib/python3.14/os.py", True),
    ("/tmp", "/tmp/lib/python3.14/os.py", False),                 # a library planted above the tree
    ("{tree}", "/tmp/lib/python3.14/os.py", False),               # its prefix, someone else's library
    ("{tree}/..", "{tree}/../lib/python3.14/os.py", False),
    ("{tree}x", "{tree}x/lib/python3.14/os.py", False),           # a sibling folder is not the tree
    ("{real}", "{real}/lib/python3.14/os.py", True),              # the tree by its resolved path
])
def test_python_stays_home_refuses_a_library_from_elsewhere(tmp_path, prefix, os_file, ok):
    """The check that proves the landmark held (review mutant N3: forced to
    succeed, it would let a planted library through unnoticed)."""
    real = tmp_path / "real" / "python"
    real.mkdir(parents=True)
    tree = real
    if sys.platform != "win32":
        (tmp_path / "link").symlink_to(tmp_path / "real")
        tree = tmp_path / "link" / "python"
    elif "{real}" in prefix:
        pytest.skip("no symlinks here")
    t, r = _posix(tree), _posix(real)
    py = _fake_python(tmp_path, prefix.format(tree=t, real=r), os_file.format(tree=t, real=r))
    got = run(_function("python_stays_home") + f"\nPY={_sh_quote(_posix(py))}\npython_stays_home {_sh_quote(t)}; echo rc=$?")
    assert got.stdout.strip() == ("rc=0" if ok else "rc=1"), (got.stdout, got.stderr)


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() != 0, reason="root (the sandbox) only: makes accounts")
def test_the_stage_is_made_by_the_resolved_home_and_a_swapped_link_does_not_move_it(tmp_path):
    """The review's home-link-check.sh: kid's home is reached through a
    link in a folder anyone may write to (not sticky). The stage goes under
    the resolved home and stays there when the link is swapped; a home
    that is itself in such a folder falls back to /tmp."""
    import pwd as _pwd

    # Somewhere every account can reach (pytest's own folders are root's, 0700).
    base = Path("/srv/domovoi-stage-test")
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    os.chmod(base, 0o755)
    shared = base / "shared"
    shared.mkdir()
    os.chmod(shared, 0o777)
    subprocess.run(["useradd", "-m", "-s", "/bin/bash", "kidlink"], check=True)
    try:
        real_home = Path(_pwd.getpwnam("kidlink").pw_dir)
        (shared / "kidlink").symlink_to(real_home)
        subprocess.run(["usermod", "-d", str(shared / "kidlink"), "kidlink"], check=True)
        script = "\n".join([_function("private_dir"), _function("make_stage"), "make_stage", 'echo "stage=$PYSTAGE"',
                            "/usr/bin/sleep 2", '[ -d "$PYSTAGE" ] && echo "still=$(cd -P "$PYSTAGE" && pwd -P)"',
                            '/usr/bin/rm -rf "$PYSTAGE"'])
        import time as _time

        evil = base / "evil"
        evil.mkdir()
        os.chmod(evil, 0o777)
        proc = subprocess.Popen(["runuser", "-u", "kidlink", "--", BASH, "-s"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd="/")
        proc.stdin.write('die() { echo "die: $*" >&2; exit 1; }\n' + script + "\n")
        proc.stdin.close()
        # While it holds its stage, another account swaps the link for one
        # into a folder of its own.
        _time.sleep(1)
        (shared / "kidlink").unlink()
        (shared / "kidlink").symlink_to(evil)
        proc.wait(timeout=60)
        out, err = proc.stdout.read(), proc.stderr.read()
        stage = next((ln.split("=", 1)[1] for ln in out.splitlines() if ln.startswith("stage=")), "")
        assert stage.startswith(str(real_home) + "/.domovoi-python."), (out, err)
        assert f"still={stage}" in out and not list(evil.iterdir()), (out, err)
        # A home that is itself in a folder others can write: /tmp, 0700.
        subprocess.run(["usermod", "-d", str(shared / "realhome"), "kidlink"], check=True)
        (shared / "realhome").mkdir()
        shutil.chown(shared / "realhome", "kidlink")
        got = subprocess.run(["runuser", "-u", "kidlink", "--", BASH, "-s"], input=(
            'die() { echo "die: $*" >&2; exit 1; }\n' + _function("private_dir") + "\n" + _function("make_stage")
            + '\nmake_stage; echo "stage=$PYSTAGE"; /usr/bin/stat -c %a "$PYSTAGE"; /usr/bin/rm -rf "$PYSTAGE"'),
            capture_output=True, text=True, cwd="/", timeout=60)
        assert "stage=/tmp/domovoi-python." in got.stdout and "\n700\n" in got.stdout + "\n", (got.stdout, got.stderr)
    finally:
        subprocess.run(["userdel", "-r", "kidlink"], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


def test_the_landmark_is_an_empty_zip_named_for_the_pin(tmp_path):
    import zipfile

    out = tmp_path / "landmark.zip"
    got = run("\n".join([_function("landmark_zip"), _function("landmark_name"),
                         "PIN_VERSION=3.14.8; landmark_name; echo", "PIN_VERSION=3.13.16; landmark_name; echo",
                         f"landmark_zip > {_sh_quote(_posix(out))}"]))
    assert got.stdout.split() == ["python314.zip", "python313.zip"], got.stderr
    assert zipfile.is_zipfile(out) and zipfile.ZipFile(out).namelist() == [] and out.stat().st_size == 22


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners and modes")
def test_private_dir_takes_only_folders_no_other_account_can_change(tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir(mode=0o700)
    script = "\n".join([_function("private_dir"), f"private_dir {mine}; echo tmp=$?", "private_dir /; echo root=$?",
                        f"private_dir {tmp_path}/missing; echo missing=$?"])
    got = run(script).stdout
    # pytest's tmp_path is under /tmp (1777: anyone may write there).
    assert "tmp=1" in got and "root=0" in got and "missing=1" in got, got


@pytest.mark.skipif(sys.platform == "win32", reason="runs the staged files")
def test_a_staged_python_that_does_not_run_as_pinned_stops_the_install(tmp_path):
    """The staged copy's own check (review mutant R8): a file that is the
    pin but whose python3 is not that version installs nothing."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data, mode in (("python/bin/python3", b"#!/bin/sh\necho 3.0.0\n", 0o755),
                                 ("python/lib/python3.14/os.py", b"", 0o644)):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            t.addfile(info, io.BytesIO(data))
    fake = tmp_path / "fake.tar.gz"
    fake.write_bytes(buf.getvalue())
    script = "\n".join([
        *(_function(f) for f in ("pinned_ok", "private_dir", "make_stage", "landmark_zip", "landmark_name",
                                 "python_stays_home", "ipv4_ok", "ipv6_first", "home_host", "core_normal",
                                 "payload_core", "stage_pinned_python")),
        f'fetch_to() {{ /usr/bin/cp {fake} "$2"; }}',
        'SUDO_CHECK="" PAYLOAD=""',
        f"PIN_VERSION=3.14.8 PIN_FILE=fake.tar.gz PIN_SIZE={len(buf.getvalue())} "
        f"PIN_SHA={hashlib.sha256(buf.getvalue()).hexdigest()} PIN_URL=https://example.invalid/fake.tar.gz",
        "trap '[ -z \"$PYSTAGE\" ] || /usr/bin/rm -rf \"$PYSTAGE\"' EXIT",
        "stage_pinned_python; echo survived"])
    got = run(script)
    assert got.returncode == 1 and "does not run on this computer" in got.stderr, (got.stdout, got.stderr)
    assert "survived" not in got.stdout


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
        (lib / "python").chmod(0o755)
        assert "pinned=0" in run(script).stdout
        # A link anywhere on the way, even to a folder root owns and nobody
        # else may write (review mutant R1): refused.
        for linked in (lib / "python" / "bin", lib / "python", lib):
            real = linked.with_name(linked.name + ".real")
            linked.rename(real)
            linked.symlink_to(real)
            try:
                assert "pinned=1" in run(script).stdout, linked
            finally:
                linked.unlink()
                real.rename(linked)


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


# ─── the processor: x86-64-v2 for numpy 2.4+ (2026-10-15) ───────────────────

# /proc/cpuinfo as a kernel writes it (trimmed to the line that matters).
MODERN = ("processor\t: 0\nvendor_id\t: GenuineIntel\nflags\t\t: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr "
          "pge mca cmov pat pse36 clflush mmx fxsr sse sse2 ht syscall nx lm constant_tsc pni ssse3 cx16 sse4_1 "
          "sse4_2 popcnt aes xsave avx\nbogomips\t: 5000\n\n")
CORE2 = ("processor\t: 0\nmodel name\t: Intel(R) Core(TM)2 Duo CPU     E8400  @ 3.00GHz\nflags\t\t: fpu vme de pse "
         "tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush dts acpi mmx fxsr sse sse2 ss ht tm pbe "
         "syscall nx lm constant_tsc pni dtes64 monitor ds_cpl vmx est tm2 ssse3 cx16 xtpr pdcm sse4_1 xsave\n\n")
ATHLON64 = "processor\t: 0\nflags\t\t: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov sse sse2 lm 3dnow\n"


@pytest.mark.parametrize("machine,cpuinfo,ok", [
    ("x86_64", MODERN, True),
    ("x86_64", CORE2, False),                      # SSSE3 and CX16, but no SSE4.2 or POPCNT
    ("x86_64", ATHLON64, False),
    ("x86_64", MODERN.replace(" cx16", ""), False),
    ("x86_64", MODERN.replace(" sse4_2", " sse4_2x"), False),   # whole words only
    ("x86_64", "processor\t: 0\n", True),          # the kernel says nothing: nothing is refused
    ("x86_64", None, True),                        # no /proc/cpuinfo at all
    ("aarch64", ATHLON64, True),                   # 64-bit ARM: no x86 flags to need
])
def test_cpu_ok_wants_x86_64_v2(tmp_path, machine, cpuinfo, ok):
    info = tmp_path / "cpuinfo"
    if cpuinfo is not None:
        info.write_bytes(cpuinfo.encode("ascii"))
    got = run(_function("cpu_ok") + f"\nMACHINE={machine} CPUINFO={_sh_quote(_posix(info))}\ncpu_ok; echo rc=$?")
    assert got.stdout.strip() == ("rc=0" if ok else "rc=1"), (got.stdout, got.stderr)


def test_the_processor_is_checked_before_anything_is_downloaded():
    text = _text()
    assert "\nCPUINFO=/proc/cpuinfo\n" in text
    check = text.index("cpu_ok || die \"this computer's processor is too old for Domovoi (needs roughly 2009 or newer)")
    assert check < text.index("[ \"$USE_PY\" = system ] || stage_pinned_python") < text.index("# ─── 1. the payload")
    assert check < text.index('need_sudo\n$SUDO /usr/bin/install -d -o root -g root -m 0755 "$ROOT_LIB"')
    for flag in ("ssse3", "sse4_2", "popcnt", "cx16"):
        assert flag in _function("cpu_ok")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and links")
@pytest.mark.parametrize("made", ["link", "open", "fine"])
def test_the_stage_is_checked_again_once_it_exists(tmp_path, made):
    """make_stage's second look (review round 2): a folder that turns out to
    be reached through a link, or not 0700, is refused before anything goes
    in it. mktemp stands in for a swap between the making and the using."""
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    os.chmod(real, 0o700)
    if made == "link":
        (tmp_path / "link").symlink_to(real)
        given = tmp_path / "link"
    elif made == "open":
        os.chmod(real, 0o755)
        given = real
    else:
        given = real
    stub = f'/usr/bin/mktemp() {{ echo {_sh_quote(str(given))}; }}\n/usr/bin/getent() {{ :; }}\n'
    got = run(stub + _function("private_dir") + "\n" + _function("make_stage") + '\nmake_stage; echo "ok=$PYSTAGE"')
    if made == "fine":
        assert got.returncode == 0 and f"ok={real}" in got.stdout, (got.stdout, got.stderr)
    else:
        assert got.returncode == 1 and "is not this account's alone" in got.stderr, (got.stdout, got.stderr)
