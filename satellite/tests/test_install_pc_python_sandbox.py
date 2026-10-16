"""satellite/scripts/install-pc.sh with the satellite's own Python, run for
real, as root, in a throwaway container (2026-10-15): the pinned file from
the server's cache becomes root's own copy, root-owned and unwritable by
anyone else, without the Berkeley DB module; other bytes from the server
are refused and nothing is installed; a reinstall replaces it; the
computer's own Python takes its place again; the uninstall removes it.

Root only, inside a container (``/.dockerenv`` or
``DOMOVOI_INSTALLER_SANDBOX=1``), like test_install_pc_sandbox.py, and only
with the real pinned build at hand: ``DOMOVOI_TEST_PBS_DIR`` names a folder
holding the file the tree's lock pins for this machine (the runner, in
the maintainers' functional test harness, kept outside this repo, copies it in). No
network: GitHub is unreachable here, so a server that serves anything else
leaves no source.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
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

ROOT = Path(__file__).resolve().parents[2]
SANDBOX = (
    sys.platform.startswith("linux") and os.geteuid() == 0
    and (Path("/.dockerenv").exists() or os.environ.get("DOMOVOI_INSTALLER_SANDBOX") == "1")
)


def _pin() -> dict | None:
    for line in (ROOT / "satellite" / "locks" / "linux-python.txt").read_text(encoding="utf-8").splitlines():
        f = line.split()
        if len(f) == 7 and f[0] == "python" and f[1] == platform.machine():
            return {"version": f[2], "file": f[3], "size": int(f[4]), "sha256": f[5]}
    return None


PIN = _pin()
PBS_DIR = Path(os.environ.get("DOMOVOI_TEST_PBS_DIR", "/nonexistent"))
HAVE_BUILD = bool(PIN) and (PBS_DIR / PIN["file"]).is_file()
pytestmark = [
    pytest.mark.skipif(not SANDBOX, reason="changes /usr/local, /var/lib and accounts: root in a throwaway container only"),
    pytest.mark.skipif(not HAVE_BUILD, reason="needs the pinned build in DOMOVOI_TEST_PBS_DIR (the runner copies it in)"),
]

from satellite import _ed25519, enrolment, server_identity  # noqa: E402

SEED = bytes([11]) * 32
PUBLIC = _ed25519.public_key(SEED)
FP = server_identity.fingerprint_for(PUBLIC)
SAT = "domovoi-sat"
SAT_HOME = Path("/var/lib/domovoi-sat")
ROOT_LIB = Path("/usr/local/lib/domovoi-satellite")
PYTHON_HOME = ROOT_LIB / "python"
UNINSTALLER = ROOT_LIB / "install-pc.sh"
RECORD = Path("/var/lib/domovoi-satellite/install.json")
CORE_NAME = "core.home.arpa"
ENV = {"DOMOVOI_TEST_SYSTEMD_RUNTIME": "/nonexistent", "PATH": "/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"}
FLAGS = "--yes --permissions automatic --skip-packages --power no --wait 0 --mode headless"


def sh(cmd: str, *, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env={**os.environ, **ENV},
                       cwd="/", timeout=timeout, stdin=subprocess.DEVNULL)
    if check:
        assert r.returncode == 0, (cmd, r.returncode, r.stdout[-1500:], r.stderr[-1500:])
    return r


def _clean() -> None:
    try:
        pwd.getpwnam(SAT)
        subprocess.run(["userdel", "-r", SAT], capture_output=True)
    except KeyError:
        pass
    for p in (ROOT_LIB, RECORD.parent, SAT_HOME, Path("/etc/pip.conf")):
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def _stray_stages() -> list[str]:
    """Staging folders left behind: in /tmp, or in an account's home."""
    found = [p for p in Path("/tmp").glob("tmp.*") if (p / "x").exists() or any(p.glob("cpython-*"))]
    found += list(Path("/tmp").glob("domovoi-python.*"))
    for home in (Path("/root"), *Path("/home").glob("*")):
        found += list(home.glob(".domovoi-python.*"))
    return [str(p) for p in found]


class Core:
    """A core that proves FP for any address, and serves the Linux Python
    as ``mode`` says: the real file, other bytes of its size, too many
    bytes, or 404."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.requests: list[str] = []
        real = (PBS_DIR / PIN["file"]).read_bytes()
        self.body = {"real": real, "other": b"\0" * len(real), "long": real + b"\0" * 1000}.get(mode)
        core = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                core.requests.append(self.path)
                if self.path.startswith("/v1/satellite-linux/files/"):
                    name = urllib.parse.unquote(self.path.rsplit("/", 1)[-1])
                    if core.body is None or name != PIN["file"]:
                        self.send_response(404)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(core.body)))
                    self.end_headers()
                    self.wfile.write(core.body)
                    return
                if not self.path.startswith("/v1/health"):
                    self.send_response(404)
                    self.end_headers()
                    return
                q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
                challenge, addr = q.get("challenge", ""), q.get("addr")
                doc = {"status": "ok", "identity": {
                    "algorithm": "ed25519", "fingerprint": FP, "challenge": challenge, "addr": addr,
                    "public_key": base64.b64encode(PUBLIC).decode("ascii"),
                    "signature": base64.b64encode(
                        _ed25519.sign(SEED, server_identity.health_message(challenge, addr))).decode("ascii")}}
                body = json.dumps(doc).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.payload = enrolment.build(f"http://{CORE_NAME}:{self.server.server_address[1]}", FP, "Cupboard")

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def tree():
    _clean()
    hosts = Path("/etc/hosts")
    saved = hosts.read_text(encoding="utf-8")
    hosts.write_text(saved + f"\n127.0.0.1 {CORE_NAME}\n", encoding="utf-8")
    Path("/etc/pip.conf").write_text("[global]\nindex-url = http://127.0.0.1:9/simple\nretries = 0\ntimeout = 2\n",
                                     encoding="utf-8")
    t = Path(tempfile.mkdtemp(dir="/tmp", prefix="bundle-"))
    os.chmod(t, 0o755)
    shutil.copytree(ROOT / "satellite", t / "satellite", ignore=shutil.ignore_patterns("__pycache__", "tests"))
    sh(f"chmod -R a+rX {t}")
    try:
        yield t
    finally:
        hosts.write_text(saved, encoding="utf-8")
        _clean()
        shutil.rmtree(t, ignore_errors=True)


@pytest.fixture
def core(request):
    c = Core(getattr(request, "param", "real"))
    yield c
    c.close()


def _install(tree: Path, core: Core, extra: str = "--python pinned") -> subprocess.CompletedProcess:
    return sh(f"bash {tree}/satellite/scripts/install-pc.sh {FLAGS} {extra} '{core.payload}'", check=False)


def _modes_under(root: Path) -> list[str]:
    """Anything under ``root`` that is not root's, or that group or others can write."""
    bad = []
    for p in [root, *root.rglob("*")]:
        st = p.lstat()
        if st.st_uid != 0 or (not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o022):
            bad.append(f"{p} uid={st.st_uid} mode={oct(st.st_mode)}")
    return bad


def test_the_pinned_python_from_the_server_becomes_root_s_own_copy(tree, core):
    r = _install(tree, core)
    out = r.stdout + r.stderr
    assert f"satellite's own Python {PIN['version']}" in r.stdout and "from the Domovoi server" in r.stdout, out[-2000:]
    assert f"proved it is {FP}" in r.stdout, out[-2000:]
    assert any(PIN["file"] in q for q in core.requests)
    # The install went on to the venv (pip has no index here, so it stopped there).
    assert "Setting up Python" in r.stdout, out[-2000:]
    assert (PYTHON_HOME / "bin" / "python3").exists()
    assert _modes_under(PYTHON_HOME) == []
    assert list(PYTHON_HOME.rglob("_dbm*.so")) == [], "the Berkeley DB module is never installed"
    assert not list(ROOT_LIB.glob(".python*")), "root's working names are gone"
    cfg = (SAT_HOME / "app" / "venv" / "pyvenv.cfg").read_text(encoding="utf-8")
    assert f"home = {PYTHON_HOME}/bin" in cfg
    got = subprocess.run([str(SAT_HOME / "app" / "venv" / "bin" / "python"), "-I", "-c",
                          "import sys, ssl, sqlite3, ctypes, lzma, tkinter; print(sys.version.split()[0], sys.base_prefix)"],
                         capture_output=True, text=True)
    assert got.stdout.split() == [PIN["version"], str(PYTHON_HOME)], (got.stdout, got.stderr)
    rec = json.loads(RECORD.read_text(encoding="utf-8"))
    assert rec["python"] == "pinned" and rec["python_version"] == PIN["version"]
    assert _stray_stages() == [], "the temporary copy is gone"
    # The uninstall takes it away, and with it root's folder.
    u = sh(f"bash {UNINSTALLER} --uninstall --purge", check=False)
    assert u.returncode == 0, (u.stdout, u.stderr)
    assert not PYTHON_HOME.exists() and not ROOT_LIB.exists() and not RECORD.exists()


@pytest.mark.parametrize("core", ["other", "long", "404"], indirect=True)
def test_a_server_that_serves_anything_else_installs_nothing(tree, core):
    r = _install(tree, core)
    assert r.returncode == 1, (r.returncode, r.stdout[-1500:], r.stderr[-1500:])
    assert "neither the Domovoi server nor GitHub gave the pinned Python" in r.stderr
    assert "nothing was installed" in r.stderr
    assert "Checking the Domovoi server" not in r.stdout, "it stops before step 1"
    assert not ROOT_LIB.exists() and not RECORD.exists()
    with pytest.raises(KeyError):
        pwd.getpwnam(SAT)
    assert _stray_stages() == []


def test_a_reinstall_replaces_root_s_copy_and_the_computer_s_own_python_retires_it(tree, core):
    assert "from the Domovoi server" in _install(tree, core).stdout
    (PYTHON_HOME / "planted-by-an-old-install").write_text("x", encoding="utf-8")
    r = _install(tree, core)
    assert "from the Domovoi server" in r.stdout, r.stderr[-1500:]
    assert (PYTHON_HOME / "bin" / "python3").exists() and not (PYTHON_HOME / "planted-by-an-old-install").exists()
    assert not list(ROOT_LIB.glob(".python*"))
    # This Debian 12 has Python 3.11 and a list for it: its own Python, and ours goes.
    r = _install(tree, core, "--python system")
    assert "satellite's own Python" not in r.stdout and "Setting up Python" in r.stdout, r.stderr[-1500:]
    assert not PYTHON_HOME.exists()
    assert json.loads(RECORD.read_text(encoding="utf-8"))["python"] == "system"
    assert "home = /usr/bin" in (SAT_HOME / "app" / "venv" / "pyvenv.cfg").read_text(encoding="utf-8")


def test_auto_on_a_debian_with_python_3_11_keeps_the_computer_s_own(tree, core):
    r = _install(tree, core, "")
    assert "satellite's own Python" not in r.stdout and "Setting up Python" in r.stdout, r.stderr[-1500:]
    assert not any("satellite-linux" in q for q in core.requests)
    assert not PYTHON_HOME.exists()


def test_root_will_not_run_a_python_the_account_could_have_changed(tree, core):
    """A root folder that is group-writable is not root's alone: the
    uninstall then uses the system's python3, never that one."""
    assert "from the Domovoi server" in _install(tree, core).stdout
    marker = Path("/tmp/ran-a-planted-python")
    marker.unlink(missing_ok=True)
    (PYTHON_HOME / "bin").chmod(0o777)
    real = PYTHON_HOME / "bin" / "python3"
    target = real.resolve()
    real.unlink()
    real.write_text(f"#!/bin/sh\ntouch {marker}\nexec {target} \"$@\"\n", encoding="utf-8")
    real.chmod(0o755)
    u = sh(f"bash {UNINSTALLER} --uninstall --purge", check=False)
    assert u.returncode == 0, (u.stdout, u.stderr)
    assert not marker.exists(), "root ran a Python from a folder others can write"
    assert not ROOT_LIB.exists()


# ─── review fix round: a library planted above the tree, the umask, cwd ───

MALLORY = "mallory"
PLANT = Path("/tmp/lib")
MARKER_GLOB = "pwned-*"


def _sh_as(user: str, cmd: str, *, cwd: str = "/", check: bool = False, timeout: int = 600):
    return subprocess.run(["runuser", "-u", user, "--", "bash", "-c", cmd], capture_output=True, text=True,
                          env={**os.environ, **ENV, "HOME": pwd.getpwnam(user).pw_dir}, cwd=cwd,
                          timeout=timeout, stdin=subprocess.DEVNULL)


def _markers() -> list[str]:
    return sorted(str(p) for p in Path("/tmp").glob(MARKER_GLOB))


@pytest.fixture
def plant():
    """Another local account plants lib/python3XY.zip and the pinned
    version's standard library, with a sitecustomize, in /tmp: what the
    review's tmp-zip-attack.sh did."""
    for name in (MALLORY, "kid"):
        try:
            pwd.getpwnam(name)
        except KeyError:
            subprocess.run(["useradd", "-m", "-s", "/bin/bash", name], check=True)
    minor = ".".join(PIN["version"].split(".")[:2])
    tag = minor.replace(".", "")
    stage = f"/tmp/mallory-py-{os.getpid()}"
    r = _sh_as(MALLORY, f"""set -e
        mkdir -p {stage} && tar -xzf {PBS_DIR / PIN['file']} -C {stage}
        mkdir -p {PLANT} && cp -r {stage}/python/lib/python{minor} {PLANT}/
        printf 'PK\\005\\006' > {PLANT}/python{tag}.zip; head -c 18 /dev/zero >> {PLANT}/python{tag}.zip
        printf 'import os\\nopen("/tmp/pwned-%%d" %% os.getuid(), "a").write("ran\\\\n")\\n' > {PLANT}/python{minor}/sitecustomize.py
        chmod -R a+rX {PLANT}; rm -rf {stage}""")
    assert r.returncode == 0, r.stderr
    for m in _markers():
        Path(m).unlink()
    yield
    shutil.rmtree(PLANT, ignore_errors=True)
    for m in _markers():
        Path(m).unlink()
    for name in (MALLORY, "kid"):
        subprocess.run(["userdel", "-r", name], capture_output=True)


def test_a_library_planted_in_tmp_never_runs_as_root(tree, core, plant):
    r = _install(tree, core)
    out = r.stdout + r.stderr
    assert _markers() == [], ("the planted sitecustomize ran", _markers(), out[-1500:])
    assert f"proved it is {FP}" in r.stdout and "Setting up Python" in r.stdout, out[-2000:]
    got = subprocess.run([str(PYTHON_HOME / "bin" / "python3"), "-I", "-c", "import sys, os; print(sys.prefix, os.__file__)"],
                         capture_output=True, text=True, cwd="/tmp")
    assert got.stdout.split()[0] == str(PYTHON_HOME) and got.stdout.split()[1].startswith(f"{PYTHON_HOME}/lib/")
    landmark = PYTHON_HOME / "lib" / f"python{PIN['version'].split('.')[0]}{PIN['version'].split('.')[1]}.zip"
    assert landmark.stat().st_size == 22 and landmark.stat().st_uid == 0


def test_a_library_planted_in_tmp_never_runs_for_an_account_whose_home_others_can_write(tree, core, plant):
    """kid's home is group-writable, so the staging falls back to /tmp,
    right under the plant: the landmark alone keeps the search inside."""
    os.chmod("/home/kid", 0o777)
    sh(f"chmod -R a+rX {tree}")
    r = _sh_as("kid", f"cd ~ && bash {tree}/satellite/scripts/install-pc.sh {FLAGS.replace('--mode headless', '--mode desktop')} "
                      f"--python pinned '{core.payload}'")
    out = r.stdout + r.stderr
    assert "satellite's own Python" in r.stdout and f"proved it is {FP}" in r.stdout, out[-2000:]
    assert _markers() == [], ("the planted sitecustomize ran", _markers(), out[-1500:])
    assert not list(Path("/tmp").glob("domovoi-python.*")), "the staged copy is gone"


def test_a_planted_library_with_no_stdlib_does_not_break_the_install(tree, core, plant):
    shutil.rmtree(PLANT / f"python{'.'.join(PIN['version'].split('.')[:2])}")
    r = _install(tree, core)
    assert f"proved it is {FP}" in r.stdout and "Setting up Python" in r.stdout, (r.stdout + r.stderr)[-2000:]


def test_root_runs_nothing_from_the_folder_it_was_started_in(tree, core, plant):
    """Every Python the script runs, root's included, runs with -I: a
    json.py or platform.py in the current folder (one another account can
    write to) is never imported (review mutant R4)."""
    evil = Path("/tmp/evilcwd")
    evil.mkdir(mode=0o777, exist_ok=True)
    os.chmod(evil, 0o777)
    for mod in ("json", "platform", "runpy"):
        (evil / f"{mod}.py").write_text(
            'import os\nopen("/tmp/pwned-cwd-%d" % os.getuid(), "a").write("' + mod + '\\n")\n'
            'raise ImportError("planted")\n', encoding="utf-8")
    try:
        r = sh(f"cd {evil} && bash {tree}/satellite/scripts/install-pc.sh {FLAGS} --python pinned '{core.payload}'",
               check=False)
        assert f"proved it is {FP}" in r.stdout and "Setting up Python" in r.stdout, (r.stdout + r.stderr)[-2000:]
        assert _markers() == [], [Path(m).read_text() for m in _markers()]
        assert json.loads(RECORD.read_text(encoding="utf-8"))["python"] == "pinned"
    finally:
        shutil.rmtree(evil, ignore_errors=True)


@pytest.mark.parametrize("mask", ["077", "027"])
def test_a_strict_umask_still_gives_a_python_the_satellite_can_run(tree, core, mask):
    r = sh(f"umask {mask} && bash {tree}/satellite/scripts/install-pc.sh {FLAGS} --python pinned '{core.payload}'",
           check=False)
    assert "Setting up Python" in r.stdout, (r.stdout + r.stderr)[-2000:]
    minor = ".".join(PIN["version"].split(".")[:2])
    for d in (PYTHON_HOME, PYTHON_HOME / "bin", PYTHON_HOME / "lib", PYTHON_HOME / "lib" / f"python{minor}",
              PYTHON_HOME / "lib" / f"python{minor}" / "lib-dynload"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o755, (d, oct(d.stat().st_mode))
    assert _modes_under(PYTHON_HOME) == []
    got = subprocess.run(["runuser", "-u", SAT, "--", str(PYTHON_HOME / "bin" / "python3"), "-I", "-c",
                          "import ssl, json; print('runs')"], capture_output=True, text=True, cwd="/")
    assert got.stdout.strip() == "runs", got.stderr
    assert (SAT_HOME / "app" / "venv" / "bin" / "python").exists()
