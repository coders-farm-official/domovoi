"""A support session's shell on this satellite (1.5.0).

The core's side, and why it is safe, is domovoi/support_sessions.py and
docs/SECURITY_PRIVACY.md, "Support sessions". This side:

* **Who declares it.** Only a Raspberry Pi on a prepared card (stage 1 made
  ``/var/lib/domovoi``) that holds the root-owned server pin, whose service
  account can NOT use sudo (``sudo -n true`` fails at start), that has no
  known way from this account to root (no Domovoi root unit runs code this
  account can write; no sudo rule hands the XVF3800 tool free arguments),
  and whose unit lets this process feed a systemd watchdog. A hand-built Pi
  whose user is in sudo never declares it: there the shell would be root.
  Spare PCs and phones never declare it (:func:`should_declare`).
* **Only the server can open one.** ``support_allow`` (the per-satellite
  switch, sent on every connect and on every change), ``support_open`` and
  every ``support_input`` carry the server's Ed25519 signature under their
  own context (``domovoi-support-v1``) over this satellite's id, this
  connection's nonce (sent in the hello, new on every connect) and the
  issue time. The key must be the pinned one. An unsigned, replayed, stale,
  wrong-satellite or wrong-connection frame is refused, and so is an open
  before a signed ``support_allow`` on this connection. Only the signed
  document's values are used.
* **The device signs what it sends.** Every ``support_*`` frame this
  device sends (opened, refused, output, echo, closed) carries its own
  Ed25519 signature, under the same context with a direction label
  (``satellite-to-server``), over this satellite's id, the connection's
  nonce, the session id and a sequence number that rises on every frame of
  the connection. The key is the device's support key
  (``~/.domovoi/support_key``, made once); the server pins its public half
  when an admin approves this device's pairing (the approval exchange
  binds it: satellite/pairing_proof.py, version 3), and every server frame
  names the key it pinned: a frame naming another key is refused here, so
  a key swapped in the hello never reaches the shell.
* **The shell.** A PTY running the account's login shell as this process's
  own user (not root, on a device that declares it), inside a sandbox of
  its own (satellite/support_sandbox.py): user, mount, PID and IPC
  namespaces, every mount read-only (the home folder, ``~/.domovoi`` and
  ``~/domovoi`` too), a private in-memory ``/tmp`` holding the session's
  working directory, ``HOME`` and ``TMPDIR``, and its own ``/proc``.
  Nothing it writes outlives the session, whatever way it ends. A device
  that can't build the sandbox doesn't declare the ability. No new
  sudoers line, no setuid helper, no new port: the bytes ride the
  satellite's existing WebSocket as base64 in JSON text frames.
* **Its own limits**, kept whatever the server says: it refuses a
  ``max_sec`` over 60 minutes, ends at ``max_sec`` and after ``idle_sec``
  without input, reads at most 16 KB a frame and 64 KB a second (over that
  it stops reading the PTY, so the shell slows rather than floods), takes
  at most 4 KB of input a frame, and ends on any socket drop (never
  resumes). One session at a time.
* **The end can't be held off from inside the shell.** Input reaches the
  PTY through a bounded queue and a worker thread, so a program that stops
  reading can't block the client's event loop (a full queue ends the
  session). While a session is open the event loop feeds a systemd watchdog
  (``WATCHDOG_USEC`` set at open): a client stopped or frozen from inside
  the shell is killed with its whole unit. At open the process is made
  non-dumpable, so the shell (the same account) can't attach a debugger to
  it or write its memory.
* **The end.** The PTY's process group is hung up (then killed), and the
  client restarts its own service so nothing started with ``nohup`` or
  ``setsid`` lives on: the unit's cgroup kill covers every child. If the
  restart can't be asked for, the client exits non-zero and systemd does it.

Linux only. The module imports anywhere; :class:`PtyBackend` needs
``pty``-era modules (``fcntl``, ``termios``) and is never built on Windows.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import queue
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

log = logging.getLogger("satellite.support")

MAX_SEC_CAP = 60 * 60
OUTPUT_FRAME_MAX = 16 * 1024
OUTPUT_RATE_MAX = 64 * 1024
INPUT_FRAME_MAX = 4 * 1024
COLS_RANGE = (20, 400)
ROWS_RANGE = (5, 200)
READ_POLL_SEC = 0.2
#: Input frames waiting for the PTY. A shell that stops reading fills it,
#: and the session ends ("input_stalled") instead of the client blocking.
INPUT_QUEUE_MAX = 64
#: How long one write to the PTY waits for room before it checks again
#: whether the session is still open.
WRITE_POLL_SEC = 0.5
#: The systemd watchdog armed for a session: the event loop feeds it every
#: WATCHDOG_FEED_SEC; WATCHDOG_SEC without a feed and systemd kills the
#: unit (every process in it, the shell's too) and starts it again.
WATCHDOG_SEC = 30
WATCHDOG_FEED_SEC = 5.0
#: The lowest LED brightness a remote set_config may set while this device
#: offers support sessions (the light is the room's sign of a session).
MIN_LED_BRIGHTNESS = 4

DEVICE_MODEL = Path("/proc/device-tree/model")
#: Root's state directory, made by stage 1 of a prepared card.
PREPARED_STATE_DIR = Path("/var/lib/domovoi")
#: Where root's units live: no Domovoi unit there may run as root from a
#: path this account can write.
SYSTEMD_UNIT_DIRS = (Path("/etc/systemd/system"), Path("/lib/systemd/system"),
                     Path("/usr/lib/systemd/system"))
ROOT_UNIT_GLOB = "domovoi*.service"
_UNIT_PATH_KEYS = frozenset({
    "ExecStart", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost",
    "ExecReload", "WorkingDirectory", "Environment", "EnvironmentFile",
})
_ABS_PATH_RE = re.compile(r"(?<![\w@])/[^\s\"';:]+")
_NONCE_RE = re.compile(r"^[0-9a-f]{32}\Z")
#: The device's support key: a 32-byte Ed25519 seed, hex, made once.
SUPPORT_KEY_PATH = Path("~/.domovoi/support_key").expanduser()
#: The sandbox helper, run by path with ``python -I`` (stdlib only).
SANDBOX_SCRIPT = Path(__file__).resolve().parent / "support_sandbox.py"
SANDBOX_PROBE_TIMEOUT_SEC = 20.0


# ─── the device's key ─────────────────────────────────────────────────────


class DeviceKey:
    """This device's support key (Ed25519). The seed lives in
    :data:`SUPPORT_KEY_PATH` (0600, this account's); the shell's sandbox
    hides the file, so a session can't sign as the device."""

    def __init__(self, seed: bytes) -> None:
        if len(seed) != 32:
            raise ValueError("an ed25519 seed is 32 bytes")
        self._seed = seed
        self._priv: Any = None
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ed25519

            self._priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
            self.public = self._priv.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        except Exception:  # noqa: BLE001 - no wheel on this image: the vendored fallback
            from satellite import _ed25519

            self._priv = None
            self.public = _ed25519.public_key(seed)
        self.public_b64 = base64.b64encode(self.public).decode("ascii")

    def sign(self, message: bytes) -> bytes:
        if self._priv is not None:
            return self._priv.sign(message)
        from satellite import _ed25519

        return _ed25519.sign(self._seed, message)

    @classmethod
    def load_or_create(cls, path: Path | None = None) -> "DeviceKey":
        """The key in ``path`` (:data:`SUPPORT_KEY_PATH`), made (0600) when
        there is none. OSError or ValueError when it can't be read or
        written."""
        path = SUPPORT_KEY_PATH if path is None else path
        try:
            raw = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            raw = ""
        if raw:
            return cls(bytes.fromhex(raw))
        seed = secrets.token_bytes(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(seed.hex() + "\n")
        os.replace(tmp, path)
        return cls(seed)


def load_device_key() -> DeviceKey | None:
    try:
        return DeviceKey.load_or_create()
    except (OSError, ValueError) as e:
        log.warning("support sessions not offered: no support key (%s)", e)
        return None


def sandbox_available(
    run: Callable[..., Any] = subprocess.run,
    home: str | None = None,
) -> bool:
    """Whether this device can build the shell's sandbox: the helper's
    probe builds it, checks the home folder, ``~/.domovoi`` and
    ``~/domovoi`` can't be written from inside, and exits 0."""
    home = home or str(Path.home())
    try:
        rc = run([sys.executable, "-I", str(SANDBOX_SCRIPT), "--probe", "--home", home],
                 capture_output=True, timeout=SANDBOX_PROBE_TIMEOUT_SEC,
                 stdin=subprocess.DEVNULL).returncode
    except (OSError, subprocess.SubprocessError):
        return False
    return rc == 0


# ─── who may declare it ───────────────────────────────────────────────────


def _pin_is_root_owned() -> bool:
    """Whether this device holds the root-owned server pin first boot
    installed (satellite/server_identity.ROOT_PIN): the one pin this account
    can't rewrite, and the key every support frame is checked against."""
    from satellite import server_identity

    fingerprint, source = server_identity.pinned_fingerprint(None)
    return bool(fingerprint) and source == "image"


def root_pinned_fingerprint() -> str | None:
    """The root-owned pin, or None (a config or recorded pin never counts)."""
    from satellite import server_identity

    fingerprint, source = server_identity.pinned_fingerprint(None)
    return fingerprint if source == "image" else None


def _writable_by_me(path: Path) -> bool:
    """Whether this account could change what ``path`` holds: the path or
    any directory above it is this account's or writable to it."""
    uid = os.getuid() if hasattr(os, "getuid") else -1
    p = path
    for _ in range(64):
        try:
            st = p.stat()
        except OSError:
            st = None
        if st is not None and (st.st_uid == uid or os.access(p, os.W_OK)):
            return True
        if p.parent == p:
            return False
        p = p.parent
    return True


def root_unit_runs_my_code(
    unit_dirs: tuple[Path, ...] = SYSTEMD_UNIT_DIRS,
    writable: Callable[[Path], bool] = _writable_by_me,
) -> str | None:
    """The first Domovoi unit that runs as root from a path this account
    can write (the provisioning unit started ``~/domovoi`` code with the
    account's venv), or None. One such unit means a shell as this account
    reaches root at the next boot, so the shell is not offered."""
    for directory in unit_dirs:
        try:
            units = sorted(directory.glob(ROOT_UNIT_GLOB))
        except OSError:
            continue
        for unit in units:
            try:
                text = unit.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return str(unit)    # can't read it: can't vouch for it
            user = "root"
            paths: list[str] = []
            for raw in text.splitlines():
                line = raw.strip()
                if "=" not in line or line.startswith(("#", ";")):
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key == "User":
                    user = value.strip() or "root"
                elif key in _UNIT_PATH_KEYS:
                    paths.extend(_ABS_PATH_RE.findall(value))
            if user not in ("root", "0"):
                continue
            for found in paths:
                if writable(Path(found)):
                    return f"{unit.name} ({found})"
    return None


def sudo_rule_takes_free_args(listing: str) -> str | None:
    """A ``sudo -n -l`` rule that lets this account run ``ALL`` or hand the
    XVF3800 tool any arguments (it has options that load a library and that
    write the chip's saved settings); None when there is none."""
    for raw in listing.splitlines():
        line = raw.strip()
        if "NOPASSWD:" in line:
            line = line.split("NOPASSWD:", 1)[1]
        elif line.startswith("(") and ")" in line:
            line = line.split(")", 1)[1]
        else:
            continue
        for cmd in (c.strip() for c in line.split(",")):
            if not cmd:
                continue
            if cmd == "ALL" or cmd.endswith(" ALL"):
                return cmd
            argv = cmd.split()
            if argv and argv[0].endswith("/xvf_host") and (len(argv) == 1 or "*" in cmd):
                return cmd
    return None


def notify_socket() -> str | None:
    """systemd's notify socket for this unit (``NotifyAccess=main``), or
    None: then there is no watchdog to arm."""
    return os.environ.get("NOTIFY_SOCKET") or None


def sd_notify(message: str, path: str | None = None) -> bool:
    """Send one ``sd_notify`` datagram (``WATCHDOG=1``, ``WATCHDOG_USEC=…``).
    False when there is no socket or the send failed."""
    path = path or notify_socket()
    if not path or not hasattr(socket, "AF_UNIX"):
        return False
    addr = "\0" + path[1:] if path.startswith("@") else path
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode("ascii"))
        return True
    except OSError:
        return False


def make_undumpable() -> bool:
    """``PR_SET_DUMPABLE`` 0 for this process: another process of the same
    account (the shell) can no longer ptrace it or open its memory."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(4, 0, 0, 0, 0) == 0     # PR_SET_DUMPABLE
    except Exception:  # noqa: BLE001
        return False


def screen_led_changes(changes: dict[str, Any], declared: bool) -> tuple[dict[str, Any], list[str]]:
    """While this device offers support sessions, a remote ``set_config``
    may not turn the status light off or down to nothing: the light is the
    room's sign that a session is open. ``(kept, refused keys)``."""
    if not declared:
        return changes, []
    kept: dict[str, Any] = {}
    refused: list[str] = []
    for key, value in changes.items():
        if key == "leds.enabled" and value is not True:
            refused.append(key)
        elif key == "leds.brightness" and (
                isinstance(value, bool) or not isinstance(value, int) or value < MIN_LED_BRIGHTNESS):
            refused.append(key)
        elif key == "leds.num_leds" and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1):
            refused.append(key)
        else:
            kept[key] = value
    return kept, refused


def should_declare(
    *,
    is_pc: bool,
    platform: str = sys.platform,
    model_path: Path = DEVICE_MODEL,
    state_dir: Path = PREPARED_STATE_DIR,
    run: Callable[..., Any] = subprocess.run,
    root_pin: Callable[[], bool] = _pin_is_root_owned,
    root_path: Callable[[], str | None] = root_unit_runs_my_code,
    watchdog: Callable[[], str | None] = notify_socket,
    sandbox: Callable[[], bool] = sandbox_available,
) -> bool:
    """Whether this device's hello may declare ``support_shell``: a Pi on a
    prepared card with the root-owned server pin, a watchdog, no Domovoi
    root unit running code this account can write, no sudo, and no sudo
    rule with free arguments. Never a spare PC or a phone (1.5.0, owner
    decision 22 Nov)."""
    if is_pc or not platform.startswith("linux"):
        return False
    try:
        if "raspberry pi" not in model_path.read_text(errors="replace").lower():
            return False
    except OSError:
        return False
    if not state_dir.is_dir():
        return False
    if not root_pin():
        log.info("support sessions not offered: no root-owned server pin to check them against")
        return False
    if not watchdog():
        log.info("support sessions not offered: the service has no watchdog "
                 "(NotifyAccess=main in domovoi-satellite.service)")
        return False
    found = root_path()
    if found:
        log.info("support sessions not offered: a root unit runs code this account can "
                 "write (%s), so a shell would reach root", found)
        return False
    try:
        rc = run(["sudo", "-n", "true"], capture_output=True, timeout=5.0,
                 stdin=subprocess.DEVNULL).returncode
    except FileNotFoundError:
        return _sandbox_ok(sandbox)     # no sudo at all: nothing to escalate with
    except (OSError, subprocess.SubprocessError):
        return False    # couldn't tell: don't offer it
    if rc == 0:
        log.info("support sessions not offered: this account can use sudo (the shell would be root)")
        return False
    try:
        listed = run(["sudo", "-n", "-l"], capture_output=True, timeout=5.0, text=True,
                     stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    if listed.returncode != 0:
        return False    # couldn't list the rules: don't offer it
    loose = sudo_rule_takes_free_args(str(listed.stdout or ""))
    if loose:
        log.info("support sessions not offered: a sudo rule takes free arguments (%s)", loose)
        return False
    return _sandbox_ok(sandbox)


def _sandbox_ok(sandbox: Callable[[], bool]) -> bool:
    if sandbox():
        return True
    log.info("support sessions not offered: the shell's sandbox can't be built here "
             "(unprivileged user namespaces; satellite/support_sandbox.py)")
    return False


# ─── the PTY ──────────────────────────────────────────────────────────────


class Backend(Protocol):
    def spawn(self, cols: int, rows: int) -> None: ...
    def read(self, max_bytes: int, timeout: float) -> bytes | None: ...  # None: the shell ended
    def write(self, data: bytes, still_open: Callable[[], bool] = ...) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def echo_on(self) -> bool: ...
    def terminate(self) -> int | None: ...


_CTTY_HELPER = (
    "import fcntl,os,sys,termios;"
    "fcntl.ioctl(0,termios.TIOCSCTTY,0);"
    "s=sys.argv[1];os.execve(s,['-'+os.path.basename(s)],os.environ)"
)


class PtyBackend:
    """The real PTY (Linux). The shell is started through the sandbox
    helper (satellite/support_sandbox.py), which builds the session's
    namespaces, makes the PTY the shell's controlling terminal and becomes
    the login shell, so no Python runs in a forked child of this threaded
    process. ``sandboxed=False`` (tests of the PTY alone) uses a two-line
    helper that only does the terminal. The master is non-blocking: a write
    waits for room with a timeout and gives up once the session is over."""

    def __init__(self, *, sandboxed: bool = True,
                 hide: tuple[Path, ...] | None = None) -> None:
        self._master: int | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._sandboxed = sandboxed
        self._hide = (SUPPORT_KEY_PATH,) if hide is None else hide

    def spawn(self, cols: int, rows: int) -> None:
        import pwd

        master, slave = os.openpty()
        self._master = master
        os.set_blocking(master, False)
        self._winsize(slave, cols, rows)
        pw = pwd.getpwuid(os.getuid())
        shell = pw.pw_shell if pw.pw_shell and os.access(pw.pw_shell, os.X_OK) else "/bin/sh"
        home = pw.pw_dir or "/"
        env = {
            "HOME": home, "USER": pw.pw_name, "LOGNAME": pw.pw_name, "SHELL": shell,
            "PATH": "/usr/local/bin:/usr/bin:/bin", "TERM": "xterm-256color",
            "LANG": "C.UTF-8",
        }
        try:
            self._proc = subprocess.Popen(
                self.argv(shell, home), stdin=slave, stdout=slave, stderr=slave, cwd="/", env=env,
                start_new_session=True, close_fds=True,
            )
        finally:
            os.close(slave)

    def argv(self, shell: str, home: str) -> list[str]:
        """How the shell is started: through the sandbox helper (hiding
        the device's support key), or the terminal-only helper."""
        if not self._sandboxed:
            return [sys.executable, "-I", "-c", _CTTY_HELPER, shell]
        argv = [sys.executable, "-I", str(SANDBOX_SCRIPT), "--home", home]
        for path in self._hide:
            argv += ["--hide", str(path)]
        return argv + [shell]

    @staticmethod
    def _winsize(fd: int, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def read(self, max_bytes: int, timeout: float) -> bytes | None:
        import select

        master = self._master
        if master is None:
            return None
        ready, _, _ = select.select([master], [], [], timeout)
        if not ready:
            return b""
        try:
            data = os.read(master, max_bytes)
        except BlockingIOError:
            return b""
        except OSError:      # EIO: every writer of the PTY is gone
            return None
        return data or None

    def write(self, data: bytes, still_open: Callable[[], bool] = lambda: True) -> None:
        import select

        view = memoryview(data)
        while view:
            master = self._master
            if master is None or not still_open():
                raise OSError("the session is over")
            _, ready, _ = select.select([], [master], [], WRITE_POLL_SEC)
            if not ready:
                continue
            try:
                n = os.write(master, view)
            except BlockingIOError:
                continue
            view = view[n:]

    def resize(self, cols: int, rows: int) -> None:
        if self._master is not None:
            self._winsize(self._master, cols, rows)

    def echo_on(self) -> bool:
        """False only while a program is reading a hidden line: ECHO off
        with the line discipline still canonical, the way getpass, sudo
        and passwd ask. A shell's line editor (readline) and full-screen
        programs turn ECHO off too, but in raw mode (ICANON off), and they
        draw what is typed themselves, so that input is not hidden."""
        import termios

        if self._master is None:
            return True
        try:
            lflag = termios.tcgetattr(self._master)[3]
        except termios.error:
            return True
        return bool(lflag & termios.ECHO) or not bool(lflag & termios.ICANON)

    def terminate(self) -> int | None:
        import signal

        proc = self._proc
        code: int | None = None
        if proc is not None:
            for sig, wait in ((signal.SIGHUP, 2.0), (signal.SIGKILL, 2.0)):
                if proc.poll() is not None:
                    break
                try:
                    os.killpg(proc.pid, sig)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=wait)
                except subprocess.TimeoutExpired:
                    continue
            code = proc.poll()
        master, self._master = self._master, None
        if master is not None:
            try:
                os.close(master)
            except OSError:
                pass
        return code


def _clamp(value: Any, bounds: tuple[int, int]) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = bounds[0]
    return max(bounds[0], min(bounds[1], v))


def _verifier_for(satellite_id: Callable[[], str], pinned: Callable[[], str | None],
                  wall: Callable[[], float],
                  device_key: Callable[[], str | None] = lambda: None,
                  ) -> Callable[[Any, str, str | None], dict[str, Any]]:
    def verify(envelope: Any, kind: str, nonce: str | None) -> dict[str, Any]:
        from satellite import server_identity

        return server_identity.verify_support(
            envelope, kind=kind, expected_fingerprint=pinned(),
            satellite_id=satellite_id(), nonce=nonce, now=wall(),
            satellite_key=device_key(),
        )
    return verify


class SupportShell:
    """One support session at a time on this device.

    ``emit(frame)`` sends a text frame to the core; ``on_open()`` runs when
    a session opens (the client lights the overlay and arms the watchdog);
    ``on_end(reason, by_server)`` runs once when a session that OPENED ends
    (the client turns the light off and restarts its service); ``busy()``
    names why a session can't open now (a drop-in call, a wake-word
    recording) or returns None. ``satellite_id()`` is this device's id as
    the core knows it and ``pinned()`` the root-owned server fingerprint:
    the signed frames are checked against both."""

    def __init__(
        self,
        *,
        declared: bool,
        emit: Callable[[dict[str, Any]], Any],
        on_open: Callable[[], Any] = lambda: None,
        on_end: Callable[[str, bool], Any] = lambda reason, by_server: None,
        busy: Callable[[], str | None] = lambda: None,
        backend_factory: Callable[[], Backend] = PtyBackend,
        clock: Callable[[], float] = time.monotonic,
        satellite_id: Callable[[], str] = lambda: "",
        pinned: Callable[[], str | None] = root_pinned_fingerprint,
        wall: Callable[[], float] = time.time,
        verify: Callable[[Any, str, str | None], dict[str, Any]] | None = None,
        key: Callable[[], DeviceKey | None] = load_device_key,
    ) -> None:
        # No key the server can pin: nothing this device sends could be
        # checked, so it doesn't offer sessions at all.
        self.key = key() if declared else None
        self.declared = declared and self.key is not None
        self._emit = emit
        self._on_open = on_open
        self._on_end = on_end
        self._busy = busy
        self._factory = backend_factory
        self._clock = clock
        self._satellite_id = satellite_id
        self._verify = verify or _verifier_for(
            satellite_id, pinned, wall, lambda: self.key.public_b64 if self.key else None)
        self._lock = threading.Lock()
        self.session_id: str | None = None
        self._backend: Backend | None = None
        self._thread: threading.Thread | None = None
        self._writer: threading.Thread | None = None
        self._inbox: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=INPUT_QUEUE_MAX)
        self._stop = threading.Event()
        self.deadline = 0.0
        self.idle_sec = 0.0
        self.last_input = 0.0
        self._echo = True
        self._tokens = float(OUTPUT_RATE_MAX)
        self._refill_at = 0.0
        # This connection: its nonce, whether the server armed it, the
        # session ids it has seen, the last input sequence number.
        self.nonce: str | None = None
        self.allowed = False
        self._seen_ids: set[str] = set()
        self._in_seq = 0
        # What this device sends on this connection: one rising number
        # across every signed frame, sent in order (the lock covers the
        # number, the signature and the queueing).
        self._out_lock = threading.Lock()
        self._out_seq = 0

    @property
    def support_key(self) -> str | None:
        """The public half of this device's support key, for the hello."""
        return self.key.public_b64 if self.declared and self.key else None

    @property
    def active(self) -> bool:
        return self.session_id is not None

    def new_connection(self) -> str | None:
        """A new socket to the core: a fresh nonce for the hello, and the
        device disarmed until the server's signed ``support_allow`` for THIS
        connection arrives. None when the device doesn't offer sessions."""
        self.allowed = False
        self._seen_ids = set()
        with self._out_lock:
            self._out_seq = 0
            self.nonce = secrets.token_hex(16) if self.declared else None
        return self.nonce

    def _send(self, kind: str, sid: Any, **fields: Any) -> None:
        """Send a frame about a session, signed with this device's key over
        its kind, this satellite, this connection's nonce, the session id,
        the next sequence number and its fields (which travel only inside
        the signed document). Unsigned (and so ignored by the server) only
        on a device with no key, which never offers sessions."""
        from satellite import server_identity

        with self._out_lock:
            if self.key is None or not self.nonce:
                self._emit({"type": kind, "session_id": sid, **fields})
                return
            self._out_seq += 1
            doc = {"kind": kind, "satellite_id": self._satellite_id(), "nonce": self.nonce,
                   "session_id": sid, "seq": self._out_seq, **fields}
            signature = self.key.sign(server_identity.support_reply_message(doc))
            self._emit({"type": kind, "session_id": sid, "signed": {
                "algorithm": server_identity.ALGORITHM, "doc": doc,
                "signature": base64.b64encode(signature).decode("ascii")}})

    # ── frames from the core ─────────────────────────────────────────

    def handle(self, frame: dict[str, Any]) -> None:
        kind = frame.get("type")
        if kind == "support_allow":
            self.allow(frame)
        elif kind == "support_open":
            self.open(frame)
        elif kind == "support_input":
            self.input(frame)
        elif kind == "support_resize":
            self.resize(frame)
        elif kind == "support_close":
            if frame.get("session_id") == self.session_id and self.active:
                self.close(str(frame.get("reason") or "closed"), by_server=True)

    def _signed(self, frame: dict[str, Any], kind: str) -> dict[str, Any] | None:
        from satellite import server_identity

        if not self.declared or not self.nonce or not _NONCE_RE.match(self.nonce):
            return None
        try:
            return self._verify(frame.get("signed"), kind, self.nonce)
        except server_identity.IdentityError as e:
            log.warning("support: refused a %s frame: %s", kind, e)
            return None

    def allow(self, frame: dict[str, Any]) -> None:
        """The server's signed switch for this connection."""
        doc = self._signed(frame, "support_allow")
        if doc is None:
            return
        self.allowed = doc.get("allowed") is True
        log.info("support sessions %s by the server", "allowed" if self.allowed else "not allowed")
        if not self.allowed and self.active:
            self.close("switch_off", by_server=True)

    def _refuse(self, sid: Any, reason: str) -> None:
        log.info("support session refused: %s", reason)
        self._send("support_refused", sid, reason=reason)

    def open(self, frame: dict[str, Any]) -> None:
        raw_sid = frame.get("session_id")
        if not isinstance(raw_sid, str) or not raw_sid or len(raw_sid) > 64:
            return
        if not self.declared:
            self._refuse(raw_sid, "support sessions are not offered on this device")
            return
        doc = self._signed(frame, "support_open")
        if doc is None:
            self._refuse(raw_sid, "the open was not signed by this device's server")
            return
        sid = doc.get("session_id")
        if not isinstance(sid, str) or sid != raw_sid:
            self._refuse(raw_sid, "the open was not signed by this device's server")
            return
        if sid in self._seen_ids:
            self._refuse(sid, "that open was already used")
            return
        self._seen_ids.add(sid)
        if not self.allowed:
            self._refuse(sid, "support sessions are off for this satellite")
            return
        busy = self._busy()
        if busy:
            self._refuse(sid, busy)
            return
        try:
            max_sec = int(doc.get("max_sec"))
            idle_sec = int(doc.get("idle_sec"))
        except (TypeError, ValueError):
            self._refuse(sid, "bad limits")
            return
        if not 0 < max_sec <= MAX_SEC_CAP or not 0 < idle_sec <= max_sec:
            self._refuse(sid, f"limits out of range (at most {MAX_SEC_CAP // 60} minutes)")
            return
        with self._lock:
            if self.session_id is not None:
                self._refuse(sid, "a support session is already open")
                return
            backend = self._factory()
            try:
                backend.spawn(_clamp(doc.get("cols"), COLS_RANGE), _clamp(doc.get("rows"), ROWS_RANGE))
            except Exception as e:  # noqa: BLE001
                self._refuse(sid, f"couldn't start a shell: {type(e).__name__}")
                return
            now = self._clock()
            self.session_id = sid
            self._backend = backend
            self.deadline = now + max_sec
            self.idle_sec = float(idle_sec)
            self.last_input = now
            self._in_seq = 0
            self._echo = True
            self._tokens = float(OUTPUT_RATE_MAX)
            self._refill_at = now
            self._stop = threading.Event()
            self._inbox = queue.Queue(maxsize=INPUT_QUEUE_MAX)
        log.info("support session %s opened (%d min, idle %d min)", sid[:8], max_sec // 60, idle_sec // 60)
        self._send("support_opened", sid)
        try:
            self._on_open()
        except Exception:  # noqa: BLE001
            log.debug("support on_open failed", exc_info=True)
        self._thread = threading.Thread(target=self._reader, args=(sid,), daemon=True,
                                        name="support-shell")
        self._thread.start()
        self._writer = threading.Thread(target=self._write_loop, args=(sid,), daemon=True,
                                        name="support-input")
        self._writer.start()

    def input(self, frame: dict[str, Any]) -> None:
        """An input frame for the open session, queued for the writer
        thread, which checks its signature and writes it. Never blocks the
        caller (the event loop): a full queue ends the session."""
        if self.session_id is None or frame.get("session_id") != self.session_id:
            return
        try:
            self._inbox.put_nowait(frame)
        except queue.Full:
            threading.Thread(target=self.close, args=("input_stalled",), daemon=True,
                             name="support-close").start()

    def _input_bytes(self, frame: dict[str, Any]) -> bytes | None:
        """The bytes of a queued input frame, if the server signed it for
        this session and connection with a sequence number past the last."""
        doc = self._signed(frame, "support_input")
        if doc is None or doc.get("session_id") != self.session_id:
            return None
        seq = doc.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq <= self._in_seq:
            log.warning("support: refused an input frame out of sequence")
            return None
        raw = doc.get("data")
        if not isinstance(raw, str) or len(raw) > (INPUT_FRAME_MAX * 4) // 3 + 8:
            return None
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            return None
        if not data or len(data) > INPUT_FRAME_MAX:
            return None
        self._in_seq = seq
        # Only a key press or a paste counts for the idle limit (the server
        # signs which it was): a terminal's automatic answers don't.
        if doc.get("active") is True:
            self.last_input = self._clock()
        return data

    def pump_input(self, sid: str, *, block: bool = False) -> int:
        """Check and write queued input to the PTY (the writer thread's
        work; a test calls it directly). Returns how many frames were
        written."""
        written = 0
        while self.session_id == sid:
            try:
                frame = self._inbox.get(timeout=READ_POLL_SEC) if block else self._inbox.get_nowait()
            except queue.Empty:
                if block:
                    continue
                return written
            if frame is None:
                return written
            data = self._input_bytes(frame)
            if data is None:
                continue
            backend = self._backend
            if backend is None:
                return written
            try:
                backend.write(data, lambda: self.session_id == sid)
            except OSError:
                if self.session_id == sid:
                    self.close("exit")
                return written
            written += 1
            self._check_echo()
            if block:
                continue
        return written

    def _write_loop(self, sid: str) -> None:
        try:
            self.pump_input(sid, block=True)
        except Exception:  # noqa: BLE001
            log.exception("support shell writer failed")
            if self.session_id == sid:
                self.close("exit")

    def resize(self, frame: dict[str, Any]) -> None:
        if frame.get("session_id") != self.session_id or self._backend is None:
            return
        try:
            self._backend.resize(_clamp(frame.get("cols"), COLS_RANGE), _clamp(frame.get("rows"), ROWS_RANGE))
        except OSError:
            pass

    def connection_lost(self) -> None:
        """The socket dropped: the session ends here and never resumes, and
        the device stays disarmed until the next connection's allow."""
        self.allowed = False
        if self.active:
            self.close("connection_lost", by_server=True)

    # ── the PTY ──────────────────────────────────────────────────────

    def _check_echo(self) -> None:
        backend = self._backend
        if backend is None:
            return
        on = backend.echo_on()
        if on != self._echo:
            self._echo = on
            self._send("support_echo", self.session_id, on=on)

    def step(self) -> bool:
        """One pass of the reader: the clocks, then at most one frame of
        output within the rate. False once the session is over."""
        sid, backend = self.session_id, self._backend
        if sid is None or backend is None or self._stop.is_set():
            return False
        now = self._clock()
        if now >= self.deadline:
            self.close("time_limit")
            return False
        if now - self.last_input >= self.idle_sec:
            self.close("idle")
            return False
        self._tokens = min(float(OUTPUT_RATE_MAX), self._tokens + (now - self._refill_at) * OUTPUT_RATE_MAX)
        self._refill_at = now
        budget = int(min(OUTPUT_FRAME_MAX, self._tokens))
        if budget < 1:
            # Over the rate: stop reading the PTY until the bucket refills.
            # The shell blocks on a full PTY, so it slows rather than floods.
            time.sleep(0.05)
            return True
        chunk = backend.read(budget, READ_POLL_SEC)
        if chunk is None:
            self.close("exit")
            return False
        if chunk:
            self._tokens -= len(chunk)
            self._send("support_output", sid, data=base64.b64encode(chunk).decode("ascii"))
        self._check_echo()
        return True

    def _reader(self, sid: str) -> None:
        try:
            while self.session_id == sid and self.step():
                pass
        except Exception:  # noqa: BLE001
            log.exception("support shell reader failed")
            if self.session_id == sid:
                self.close("exit")

    def close(self, reason: str, *, by_server: bool = False) -> None:
        """End the session: hang up the shell's process group, tell the core
        (unless it is the one ending it, or is gone), then ``on_end``."""
        with self._lock:
            sid, backend = self.session_id, self._backend
            if sid is None:
                return
            self.session_id = None
            self._backend = None
            self._stop.set()
        try:
            self._inbox.put_nowait(None)     # wake the writer
        except queue.Full:
            pass
        code = None
        if backend is not None:
            try:
                code = backend.terminate()
            except Exception:  # noqa: BLE001
                log.debug("support terminate failed", exc_info=True)
        log.info("support session %s ended: %s", sid[:8], reason)
        if not by_server:
            extra: dict[str, Any] = {} if code is None else {"exit_code": code}
            self._send("support_closed", sid, reason=reason, **extra)
        try:
            self._on_end(reason, by_server)
        except Exception:  # noqa: BLE001
            log.debug("support on_end failed", exc_info=True)
