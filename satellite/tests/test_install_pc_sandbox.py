"""satellite/scripts/install-pc.sh run for real, as root, in a throwaway
container: the uninstall the security review of wf/sat-pc broke (B3), the
headless install as root (S3), the accounts it must refuse (S2, N2).

These change /etc, /usr/local, /var/lib and the account database, so they
run only as root inside a container (``/.dockerenv``, or
``DOMOVOI_INSTALLER_SANDBOX=1`` for another runtime) — never on a
developer's own machine. functional-testing/sat-pc/run-linux-tests.sh
--root runs them; nothing here needs the network (a fake core on
127.0.0.1 answers the identity challenge, pip is pointed at nothing).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pwd = pytest.importorskip("pwd")
grp = pytest.importorskip("grp")

SANDBOX = (
    sys.platform.startswith("linux") and os.geteuid() == 0
    and (Path("/.dockerenv").exists() or os.environ.get("DOMOVOI_INSTALLER_SANDBOX") == "1")
)
pytestmark = pytest.mark.skipif(
    not SANDBOX, reason="changes /etc, /usr/local and accounts: root in a throwaway container only")

from satellite import _ed25519, enrolment, server_identity  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SEED = bytes([7]) * 32
PUBLIC = _ed25519.public_key(SEED)
FP = server_identity.fingerprint_for(PUBLIC)
SAT = "domovoi-sat"
SAT_HOME = Path("/var/lib/domovoi-sat")
UNINSTALLER = Path("/usr/local/lib/domovoi-satellite/install-pc.sh")
RECORD = Path("/var/lib/domovoi-satellite/install.json")
DROPIN = Path("/etc/systemd/logind.conf.d/90-domovoi-satellite.conf")
CORE_NAME = "core.home.arpa"


def sh(cmd: str, *, user: str | None = None, env: dict | None = None, check: bool = True,
       timeout: int = 300) -> subprocess.CompletedProcess:
    argv = ["bash", "-c", cmd] if user is None else ["runuser", "-u", user, "--", "bash", "-c", cmd]
    full = {**os.environ, **(env or {})}
    if user is not None:
        full["HOME"] = pwd.getpwnam(user).pw_dir
    r = subprocess.run(argv, capture_output=True, text=True, env=full, cwd="/",
                       timeout=timeout, stdin=subprocess.DEVNULL)
    if check:
        assert r.returncode == 0, (cmd, r.returncode, r.stdout[-1500:], r.stderr[-1500:])
    return r


def _account_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def _clean() -> None:
    for name in (SAT, "kid"):
        if _account_exists(name):
            subprocess.run(["userdel", "-r", name], capture_output=True)
    for p in (UNINSTALLER.parent, RECORD.parent, SAT_HOME):
        shutil.rmtree(p, ignore_errors=True)
    for p in (DROPIN, Path("/etc/root-canary"), Path("/etc/sudoers.d/90-keep-me"), Path("/tmp/ran-as-root"),
              Path("/tmp/systemctl.log"), Path("/usr/local/sbin/systemctl"), Path("/etc/pip.conf"),
              Path("/etc/sudoers.d/kid"), *[Path("/tmp") / n for n in TICKET_PROBES]):
        p.unlink(missing_ok=True)
    # sudo's tickets, every account's: a test's terminal never lends one to the next.
    for ts in (Path("/run/sudo/ts"), Path("/var/run/sudo/ts"), Path("/var/lib/sudo/ts")):
        shutil.rmtree(ts, ignore_errors=True)


@pytest.fixture
def sandbox():
    _clean()
    hosts = Path("/etc/hosts")
    saved = hosts.read_text(encoding="utf-8")
    hosts.write_text(saved + f"\n127.0.0.1 {CORE_NAME}\n", encoding="utf-8")
    # pip, if it is reached, fails at once instead of looking for the internet.
    Path("/etc/pip.conf").write_text("[global]\nindex-url = http://127.0.0.1:9/simple\nretries = 0\ntimeout = 2\n",
                                     encoding="utf-8")
    # Root's systemctl, as far as these tests go: a log of what it was asked.
    fake = Path("/usr/local/sbin/systemctl")
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text('#!/bin/sh\necho "$*" >>/tmp/systemctl.log\n', encoding="utf-8")
    fake.chmod(0o755)
    # A copy of the tree every account can read (root's home is 0700).
    tree = Path(tempfile.mkdtemp(dir="/tmp"))
    os.chmod(tree, 0o755)
    shutil.copytree(ROOT / "satellite", tree / "satellite",
                    ignore=shutil.ignore_patterns("__pycache__", "tests"))
    sh(f"chmod -R a+rX {tree}")
    try:
        yield tree
    finally:
        hosts.write_text(saved, encoding="utf-8")
        _clean()
        shutil.rmtree(tree, ignore_errors=True)


@pytest.fixture
def core():
    """A core that proves FP for whatever address it is asked about."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
            if not self.path.startswith("/v1/health"):
                self.send_response(404)
                self.end_headers()
                return
            challenge, addr = q.get("challenge", ""), q.get("addr")
            doc = {"status": "ok", "identity": {
                "algorithm": "ed25519", "fingerprint": FP, "challenge": challenge, "addr": addr,
                "public_key": base64.b64encode(PUBLIC).decode("ascii"),
                "signature": base64.b64encode(
                    _ed25519.sign(SEED, server_identity.health_message(challenge, addr))).decode("ascii"),
            }}
            body = json.dumps(doc).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield enrolment.build(f"http://{CORE_NAME}:{server.server_address[1]}", FP, "Cupboard")
    finally:
        server.shutdown()


#: Files the poisoned account code and the checks write: who they ran as.
TICKET_PROBES = ("account-code-ran-as", "pth-ran-as", "after-install", "after-uninstall", "ticket-control")

ENV = {"DOMOVOI_TEST_SYSTEMD_RUNTIME": "/nonexistent", "PATH": "/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"}
FLAGS = "--yes --permissions automatic --skip-apt --power no --wait 0"


def _owner(p: Path) -> str:
    return pwd.getpwuid(p.lstat().st_uid).pw_name


# ─── B3: root's uninstall never acts on what the satellite wrote ──────────

def test_B3_the_uninstall_acts_only_on_root_s_record_and_fixed_paths(sandbox):
    script = sandbox / "satellite" / "scripts" / "install-pc.sh"
    sh(f"useradd --system --create-home --home-dir {SAT_HOME} --shell /usr/sbin/nologin {SAT}")
    prefix = SAT_HOME / "app"
    # The layout an install leaves: the account's code, root's copy, root's record.
    sh(f"mkdir -p {prefix} && cp -a {sandbox}/satellite {prefix}/satellite", user=SAT)
    sh(f"install -d -o root -g root -m 0755 {UNINSTALLER.parent} {RECORD.parent} "
       f"&& install -o root -g root -m 0755 {script} {UNINSTALLER}")
    RECORD.write_text(json.dumps({"mode": "headless", "account": SAT, "logind_dropin": True,
                                  "masked_targets": ["sleep.target", "ssh.service"]}), encoding="utf-8")
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    DROPIN.write_text("[Login]\n", encoding="utf-8")
    # What root must not lose.
    Path("/etc/sudoers.d").mkdir(exist_ok=True)
    Path("/etc/sudoers.d/90-keep-me").write_text("# keep\n", encoding="utf-8")
    Path("/etc/root-canary").write_text("canary\n", encoding="utf-8")
    # A: the account writes the record the old uninstaller read.
    sh(f"mkdir -p {SAT_HOME}/.domovoi && printf '%s' "
       "'{\"logind_dropin\": \"/etc/sudoers.d/90-keep-me\", \"charge_rule\": \"/etc/root-canary\", "
       "\"masked_targets\": [\"ssh.service\"], \"prefix\": \"/etc\"}' "
       f"> {SAT_HOME}/.domovoi/install-pc.json", user=SAT)
    # B: the account rewrites the uninstaller in its own tree.
    sh(f"sed -i '2i id > /tmp/ran-as-root' {prefix}/satellite/scripts/install-pc.sh", user=SAT)
    # C: links out of its home.
    sh(f"ln -s /etc {prefix}/etc-link && ln -s /etc {SAT_HOME}/.domovoi/sounds", user=SAT)

    # Any copy but root's is refused (an honest mistake: B's copy is the attacker's own file).
    r = sh(f"bash {sandbox}/satellite/scripts/install-pc.sh --uninstall", env=ENV, check=False)
    assert r.returncode == 2 and str(UNINSTALLER) in r.stderr

    r = sh(f"bash {UNINSTALLER} --uninstall --purge", env=ENV, check=False)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert Path("/etc/sudoers.d/90-keep-me").read_text(encoding="utf-8") == "# keep\n"
    assert Path("/etc/root-canary").read_text(encoding="utf-8") == "canary\n"
    assert Path("/etc/passwd").is_file() and Path("/etc/hosts").is_file()
    assert not Path("/tmp/ran-as-root").exists()
    log = Path("/tmp/systemctl.log").read_text(encoding="utf-8") if Path("/tmp/systemctl.log").exists() else ""
    assert "unmask sleep.target" in log and "ssh.service" not in log, log
    assert not DROPIN.exists()
    assert not _account_exists(SAT) and not SAT_HOME.exists()
    assert not RECORD.exists() and not UNINSTALLER.exists()


def test_a_record_the_satellite_could_have_written_is_not_believed(sandbox):
    """record_get: a record (or its folder) root does not own counts as none."""
    script = sandbox / "satellite" / "scripts" / "install-pc.sh"
    sh(f"useradd --system --create-home --home-dir {SAT_HOME} --shell /usr/sbin/nologin {SAT}")
    sh(f"install -d -o root -g root -m 0755 {UNINSTALLER.parent} && install -m 0755 {script} {UNINSTALLER}")
    sh(f"install -d -o {SAT} -m 0755 {RECORD.parent}")
    sh(f"printf '%s' '{{\"mode\": \"headless\", \"masked_targets\": [\"sleep.target\"]}}' > {RECORD}", user=SAT)
    r = sh(f"bash {UNINSTALLER} --uninstall --mode headless", env=ENV, check=False)
    assert r.returncode == 0, r.stderr
    log = Path("/tmp/systemctl.log").read_text(encoding="utf-8") if Path("/tmp/systemctl.log").exists() else ""
    assert "unmask" not in log


# ─── S3: a headless install run as root ───────────────────────────────────

def test_S3_root_installs_a_headless_satellite_as_its_own_account(sandbox, core):
    r = sh(f"bash {sandbox}/satellite/scripts/install-pc.sh {FLAGS} --mode headless '{core}'",
           env=ENV, check=False, timeout=600)
    out = r.stdout + r.stderr
    assert f"proved it is {FP}" in r.stdout, out[-2000:]
    assert f"created the {SAT} account" in r.stdout, out[-2000:]
    assert "Setting up Python" in r.stdout, out[-2000:]
    app = SAT_HOME / "app"
    assert _owner(app) == SAT and _owner(app / "satellite" / "client.py") == SAT
    assert (app / "venv" / "bin" / "python").exists() and _owner(app / "venv") == SAT
    assert stat.S_IMODE(SAT_HOME.stat().st_mode) == 0o750
    assert _owner(UNINSTALLER) == "root" and stat.S_IMODE(UNINSTALLER.stat().st_mode) == 0o755
    assert _owner(RECORD) == "root"
    assert json.loads(RECORD.read_text(encoding="utf-8"))["mode"] == "headless"
    groups = {g.gr_name for g in grp.getgrall() if SAT in g.gr_mem}
    assert groups <= {"audio", SAT}
    # pip found no index (no network here): the install stopped there, and
    # root's uninstaller still removes what it had done.
    r = sh(f"bash {UNINSTALLER} --uninstall --purge", env=ENV, check=False)
    assert r.returncode == 0, r.stderr
    assert not _account_exists(SAT) and not UNINSTALLER.exists()


# ─── N2 / S2: accounts it must not use ────────────────────────────────────

def test_an_existing_domovoi_sat_that_can_log_in_is_refused(sandbox, core):
    sh(f"useradd --create-home --home-dir {SAT_HOME} --shell /bin/bash {SAT}")
    r = sh(f"bash {sandbox}/satellite/scripts/install-pc.sh {FLAGS} --mode headless '{core}'",
           env=ENV, check=False)
    assert r.returncode == 2 and "is not one this installer makes" in r.stderr, r.stderr
    assert not UNINSTALLER.exists() and not RECORD.exists()


def test_S2_an_account_that_runs_a_domovoi_server_is_refused(sandbox, core):
    sh("useradd --create-home --shell /bin/bash kid")
    sh("mkdir -p ~/.domovoi && echo '{}' > ~/.domovoi/server-identity.json", user="kid")
    r = sh(f"cd /home/kid && bash {sandbox}/satellite/scripts/install-pc.sh {FLAGS} --mode desktop '{core}'",
           user="kid", env=ENV, check=False)
    assert r.returncode == 2 and "runs a Domovoi server" in r.stderr, (r.stdout, r.stderr)
    assert not UNINSTALLER.exists() and not Path("/home/kid/.local/share/domovoi-satellite").exists()
    assert Path("/home/kid/.domovoi/server-identity.json").exists()


# ─── round 2: a desktop's sudo ticket never meets the account's code ──────

needs_sudo = pytest.mark.skipif(shutil.which("sudo") is None or shutil.which("script") is None,
                                reason="needs sudo and script in the image (Dockerfile.pytest)")
KID_HOME = Path("/home/kid")
KID_PREFIX = KID_HOME / ".local/share/domovoi-satellite"


def _kid_with_a_sudo_password() -> None:
    """The guide's desktop: the account types its password for sudo."""
    sh("useradd -m -s /bin/bash kid && echo 'kid:pw' | chpasswd")
    Path("/etc/sudoers.d").mkdir(exist_ok=True)
    rule = Path("/etc/sudoers.d/kid")
    rule.write_text("kid ALL=(ALL) ALL\n", encoding="utf-8")
    rule.chmod(0o440)


def _as_kid_on_a_terminal(cmd: str, *, timeout: float = 240) -> subprocess.CompletedProcess:
    """``cmd`` as kid on a terminal of its own (script(1)), typing kid's
    password each time sudo asks for it (and only then, like a person): a
    sudo ticket for that terminal is live from the first answer on, as on
    a real desktop."""
    import select
    import time as _time

    inner = "cd /home/kid && " + cmd
    proc = subprocess.Popen(
        ["runuser", "-u", "kid", "--", "script", "-qec", inner, "/dev/null"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd="/",
        env={**os.environ, **ENV, "HOME": str(KID_HOME)},
    )
    end = _time.monotonic() + timeout
    out = b""
    answered = 0
    while _time.monotonic() < end:
        ready, _, _ = select.select([proc.stdout], [], [], 0.5)
        if ready:
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            out += chunk
        asked = out.count(b"password for kid")
        if asked > answered:
            _time.sleep(0.3)
            proc.stdin.write(b"pw\n")
            proc.stdin.flush()
            answered = asked
    if proc.poll() is None and _time.monotonic() >= end:
        proc.kill()
    rc = proc.wait(timeout=30)
    text = out.decode("utf-8", "replace").replace("\r", "")
    return subprocess.CompletedProcess(proc.args, rc, text + f"\n[sudo asked {answered} time(s)]", "")


def _probe(name: str) -> str:
    f = Path("/tmp") / name
    return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""


@needs_sudo
def test_round2_the_sandbox_s_sudo_ticket_is_real(sandbox):
    """The control: in this sandbox, once sudo has had the password, the
    same terminal gets root without it — what the tests below rule out."""
    _kid_with_a_sudo_password()
    _as_kid_on_a_terminal("sudo true; sudo -n id > /tmp/ticket-control 2>&1", timeout=60)
    assert "uid=0" in _probe("ticket-control")


POISON = ('import subprocess as _s; _s.run("sudo -n id > /tmp/account-code-ran-as 2>&1", shell=True)')
PTH = 'import subprocess; subprocess.run("sudo -n id > /tmp/pth-ran-as 2>&1", shell=True)'


def _kid_s_poisoned_install(sandbox: Path) -> None:
    """A desktop install whose account has had its way with everything it
    owns: its code tree and its venv (a .pth runs at Python's start)."""
    KID_PREFIX.mkdir(parents=True)
    shutil.copytree(sandbox / "satellite", KID_PREFIX / "satellite")
    for py in ("pc_install.py", "client.py", "__init__.py"):
        f = KID_PREFIX / "satellite" / py
        f.write_text(POISON + "\n" + f.read_text(encoding="utf-8").replace(
            "from __future__ import annotations\n", ""), encoding="utf-8")
    sh(f"python3 -m venv {KID_PREFIX}/venv")
    for site in (KID_PREFIX / "venv" / "lib").glob("python3*/site-packages"):
        (site / "zz-evil.pth").write_text(PTH + "\n", encoding="utf-8")
    (KID_HOME / ".domovoi").mkdir(exist_ok=True)
    (KID_HOME / ".domovoi" / "config.toml").write_text("x\n", encoding="utf-8")
    sh(f"chown -R kid:kid {KID_HOME}")


@needs_sudo
def test_round2_a_desktop_uninstall_purge_runs_none_of_the_account_s_code(sandbox):
    """The reviewer's proof: root's copy used sudo (linger), then ran the
    account's own pc_install.py for --purge in the same terminal, and that
    code got uid=0 from the cached ticket. Now nothing of the account's
    runs at all, and sudo forgets the password at the end."""
    _kid_with_a_sudo_password()
    _kid_s_poisoned_install(sandbox)
    script = sandbox / "satellite" / "scripts"
    sh(f"install -d -o root -g root -m 0755 {UNINSTALLER.parent} {RECORD.parent} "
       f"&& install -m 0755 {script}/install-pc.sh {UNINSTALLER} "
       f"&& install -m 0644 {script}/purge-satellite-files.py {UNINSTALLER.parent}/purge-satellite-files.py")
    RECORD.write_text(json.dumps({"mode": "desktop", "account": "kid", "linger_enabled_by_installer": True,
                                  "logind_dropin": True}), encoding="utf-8")
    r = _as_kid_on_a_terminal(f"bash {UNINSTALLER} --uninstall --purge; sudo -n id > /tmp/after-uninstall 2>&1")
    assert "Done." in r.stdout, r.stdout[-1500:]
    assert _probe("account-code-ran-as") == "" and _probe("pth-ran-as") == "", "the account's code ran"
    assert "uid=0" not in _probe("after-uninstall"), "the terminal kept a sudo ticket"
    assert not (KID_HOME / ".domovoi" / "config.toml").exists() and not KID_PREFIX.exists()
    assert not RECORD.exists() and not UNINSTALLER.parent.exists()


@needs_sudo
def test_round2_a_desktop_reinstall_never_runs_the_old_venv_and_drops_the_ticket(sandbox, core):
    """Re-installing over a poisoned install: every sudo step first, then
    `sudo -k`, then a fresh venv (the old one, .pth and all, is removed
    without being run). pip has no index here, so the run stops there."""
    _kid_with_a_sudo_password()
    _kid_s_poisoned_install(sandbox)
    r = _as_kid_on_a_terminal(
        f"bash {sandbox}/satellite/scripts/install-pc.sh {FLAGS} --mode desktop '{core}'; "
        "sudo -n id > /tmp/after-install 2>&1")
    assert f"proved it is {FP}" in r.stdout and "Setting up Python" in r.stdout, r.stdout[-2000:]
    assert _probe("pth-ran-as") == "", "the old venv ran"
    assert "uid=0" not in _probe("account-code-ran-as"), _probe("account-code-ran-as")
    assert "uid=0" not in _probe("after-install"), "the terminal kept a sudo ticket"
    assert not (KID_PREFIX / "venv" / "lib").exists() or not list(
        (KID_PREFIX / "venv" / "lib").glob("python3*/site-packages/zz-evil.pth"))
