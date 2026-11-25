"""The support shell's sandbox (1.5.0; satellite/support_shell.py).

A support session's shell runs as the satellite's service account, which
can write its config dir (``~/.domovoi``), its code tree (``~/domovoi``)
and ``/tmp``. Anything the shell left there would outlive the session as
that account: a changed file in the code tree runs at the next start. So
the shell runs in a sandbox where nothing it writes can outlive it:

* **Its own user, mount, PID and IPC namespaces**, made by this helper as
  the service account (unprivileged user namespaces; no root, no sudo, no
  setuid helper). The account's own id is mapped to itself, so the shell
  is the same user with the same file access, and after ``execve`` it
  holds no capabilities, so it can't undo a mount.
* **Every mount read-only** except ``/proc``, ``/sys`` and ``/dev`` (the
  terminal lives there; nothing persistent can be created in them by this
  account), including the home folder, ``~/.domovoi`` and ``~/domovoi``.
* **A fresh, private tmpfs** (size-capped, in memory) on ``/tmp``,
  ``/dev/shm`` and ``/dev/mqueue``: the session's working directory,
  ``HOME`` and ``TMPDIR`` (``/tmp/session``) are in it. It exists only in
  this mount namespace and is freed when the last process in it exits, so
  every end (``exit``, the session's limits, End, a client crash, the
  service restart, a power cut) removes it, and nothing is ever on disk to
  clean up at the next start.
* **Its own ``/proc``** for the new PID namespace: the shell sees only its
  own processes, so it can't reach another process of the account through
  ``/proc/<pid>/root`` or send it a signal.
* **The device's support key is hidden** (``/dev/null`` bound over it), so
  the shell can't sign frames as the device.
* **The shell is PID 1 of its namespace**: when it ends, the kernel kills
  everything left in it. This helper (outside the namespace) kills it when
  it is hung up or terminated, and dies with the client
  (``PR_SET_PDEATHSIG``), which takes the namespace with it.

Run as ``python -I support_sandbox.py [--probe] --home H [--hide P]... SHELL``.
``--probe`` builds the same sandbox, checks that the home folder can't be
written, and exits 0, so a device that can't build it never offers support
sessions. Exit codes: the shell's, or 70+ for a sandbox that couldn't be
built (nothing ran).

Stdlib only, no imports from the satellite package: it runs with ``-I``.
Linux only; the pure parts (mountinfo parsing, flags) are tested anywhere.
"""

from __future__ import annotations

import os
import signal
import sys
from typing import NamedTuple

CLONE_NEWNS = 0x00020000
CLONE_NEWIPC = 0x08000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000

MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_REMOUNT = 32
MS_NOSYMFOLLOW = 256
MS_NOATIME = 1024
MS_NODIRATIME = 2048
MS_BIND = 4096
MS_REC = 16384
MS_PRIVATE = 1 << 18
MS_RELATIME = 1 << 21
MS_STRICTATIME = 1 << 24

PR_SET_PDEATHSIG = 1
PR_SET_NO_NEW_PRIVS = 38

#: Mount trees left as they are: the kernel's own file systems and /dev
#: (the PTY). The account can create nothing lasting in them.
KEEP_WRITABLE = ("/proc", "/sys", "/dev")
#: The private tmpfs mounts: (where, size).
TMPFS = (("/tmp", "64m"), ("/dev/shm", "16m"), ("/dev/mqueue", "1m"))
SESSION_DIR = "/tmp/session"

EXIT_SETUP = 70        # the namespaces or a mount couldn't be made
EXIT_STILL_WRITABLE = 71
EXIT_PROBE_WROTE = 72
EXIT_USAGE = 64


class Mount(NamedTuple):
    point: str
    options: frozenset[str]


def _unescape(field: str) -> str:
    """mountinfo escapes space, tab, newline and backslash as ``\\ooo``."""
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and field[i + 1:i + 4].isdigit() and len(field[i + 1:i + 4]) == 3:
            out.append(chr(int(field[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def parse_mountinfo(text: str) -> list[Mount]:
    """``/proc/self/mountinfo`` as (mount point, per-mount options), in
    order. Where one point is mounted over another, the last one listed is
    the one a path reaches."""
    mounts: list[Mount] = []
    for line in text.splitlines():
        fields = line.split(" ")
        if len(fields) < 6:
            continue
        mounts.append(Mount(_unescape(fields[4]), frozenset(fields[5].split(","))))
    return mounts


def _under(point: str, tops: tuple[str, ...]) -> bool:
    return any(point == t or point.startswith(t + "/") for t in tops)


def reachable(mounts: list[Mount]) -> list[Mount]:
    """One entry per mount point: the top one."""
    top: dict[str, Mount] = {}
    for m in mounts:
        top.pop(m.point, None)
        top[m.point] = m
    return list(top.values())


def writable_mounts(mounts: list[Mount], allowed: tuple[str, ...] = ()) -> list[Mount]:
    """Mounts a path still reaches writable, outside the trees left alone
    and the sandbox's own tmpfs mounts (``allowed``)."""
    return [m for m in reachable(mounts)
            if "rw" in m.options and not _under(m.point, KEEP_WRITABLE)
            and m.point not in allowed]


def remount_flags(options: frozenset[str]) -> int:
    """The flags for a read-only bind remount that keeps the mount's own
    flags: in a user namespace the kernel refuses a remount that would
    clear a locked one (nosuid, nodev, noexec, the atime mode)."""
    flags = MS_REMOUNT | MS_BIND | MS_RDONLY
    for name, bit in (("nosuid", MS_NOSUID), ("nodev", MS_NODEV), ("noexec", MS_NOEXEC),
                      ("nodiratime", MS_NODIRATIME), ("nosymfollow", MS_NOSYMFOLLOW)):
        if name in options:
            flags |= bit
    if "noatime" in options:
        flags |= MS_NOATIME
    elif "relatime" in options:
        flags |= MS_RELATIME
    else:
        flags |= MS_STRICTATIME
    return flags


# ─── the Linux part ───────────────────────────────────────────────────────


class SandboxError(Exception):
    pass


def _libc():  # pragma: no cover - Linux only
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                           ctypes.c_ulong, ctypes.c_void_p]
    libc.unshare.argtypes = [ctypes.c_int]
    return libc


def _check(rc: int, what: str) -> None:  # pragma: no cover - Linux only
    if rc != 0:
        import ctypes

        err = ctypes.get_errno()
        raise SandboxError(f"{what}: {os.strerror(err)}")


def _mount(libc, source: str | None, target: str, fstype: str | None, flags: int,
           data: str | None = None) -> None:  # pragma: no cover - Linux only
    enc = lambda s: s.encode() if s is not None else None  # noqa: E731
    _check(libc.mount(enc(source), enc(target), enc(fstype), flags, enc(data)), f"mount {target}")


def _write(path: str, text: str) -> None:  # pragma: no cover - Linux only
    with open(path, "w", encoding="ascii") as f:
        f.write(text)


def build(libc, hide: list[str]) -> None:  # pragma: no cover - Linux only
    """In this (single-threaded) process: the namespaces, then the mounts.
    Raises :class:`SandboxError`."""
    uid, gid = os.getuid(), os.getgid()
    _check(libc.unshare(CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWIPC), "unshare")
    try:
        _write("/proc/self/setgroups", "deny")
        _write("/proc/self/uid_map", f"{uid} {uid} 1")
        _write("/proc/self/gid_map", f"{gid} {gid} 1")
    except OSError as e:
        raise SandboxError(f"id maps: {e}") from None
    _mount(libc, None, "/", None, MS_REC | MS_PRIVATE)
    with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
        mounts = parse_mountinfo(f.read())
    for m in writable_mounts(mounts):
        _mount(libc, None, m.point, None, remount_flags(m.options))
    allowed = []
    for where, size in TMPFS:
        if os.path.isdir(where):
            _mount(libc, "tmpfs", where, "tmpfs", MS_NOSUID | MS_NODEV,
                   f"size={size},mode=1777")
            allowed.append(where)
    for path in hide:
        if os.path.isfile(path):
            _mount(libc, "/dev/null", path, None, MS_BIND)
            allowed.append(path)      # /dev/null: writing it keeps nothing
    with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
        left = writable_mounts(parse_mountinfo(f.read()), tuple(allowed))
    if left:
        raise SandboxError("still writable: " + ", ".join(m.point for m in left[:5]))


def _child(libc, shell: str, home: str, probe: bool) -> None:  # pragma: no cover - Linux only
    """PID 1 of the new namespace: its own /proc, the session directory,
    then the shell (or, probing, the write check)."""
    try:
        _mount(libc, "proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC)
        os.makedirs(SESSION_DIR, mode=0o700, exist_ok=False)
        os.chdir(SESSION_DIR)
        _check(libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "no_new_privs")
    except (OSError, SandboxError) as e:
        sys.stderr.write(f"support sandbox: {e}\n")
        os._exit(EXIT_SETUP)
    if probe:
        for target in (os.path.join(home, ".domovoi-sandbox-probe"),
                       os.path.join(home, ".domovoi", ".sandbox-probe"),
                       os.path.join(home, "domovoi", ".sandbox-probe")):
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except OSError:
                continue
            os.close(fd)
            try:
                os.unlink(target)
            except OSError:
                pass
            os._exit(EXIT_PROBE_WROTE)
        os._exit(0)
    import fcntl
    import termios

    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    env = dict(os.environ)
    env.update({"HOME": SESSION_DIR, "TMPDIR": "/tmp", "PWD": SESSION_DIR})
    os.execve(shell, ["-" + os.path.basename(shell)], env)


def main(argv: list[str]) -> int:  # pragma: no cover - Linux only
    probe = False
    home = ""
    hide: list[str] = []
    args = list(argv)
    shell = ""
    while args:
        a = args.pop(0)
        if a == "--probe":
            probe = True
        elif a == "--home" and args:
            home = args.pop(0)
        elif a == "--hide" and args:
            hide.append(args.pop(0))
        elif not a.startswith("--") and not args:
            shell = a
        else:
            return EXIT_USAGE
    if not home or (not probe and not shell):
        return EXIT_USAGE
    libc = _libc()
    parent = os.getppid()
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    if os.getppid() != parent:
        return EXIT_SETUP          # the client is already gone
    try:
        build(libc, hide)
    except (OSError, SandboxError) as e:
        sys.stderr.write(f"support sandbox: {e}\n")
        return EXIT_SETUP
    pid = os.fork()
    if pid == 0:
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        _child(libc, shell, home, probe)
        os._exit(EXIT_SETUP)

    def _kill(_signum, _frame) -> None:
        try:
            os.kill(pid, signal.SIGKILL)     # PID 1: the whole namespace goes
        except OSError:
            pass

    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _kill)
    while True:
        try:
            _, status = os.waitpid(pid, 0)
        except InterruptedError:
            continue
        except ChildProcessError:
            return EXIT_SETUP
        return os.waitstatus_to_exitcode(status) & 0xFF


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
