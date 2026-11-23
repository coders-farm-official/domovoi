"""The support shell's sandbox (satellite/support_sandbox.py, M3).

Anywhere: the mount plan (every reachable writable mount outside /proc,
/sys and /dev is remounted read-only with its own locked flags kept) and
how the shell is started (through the helper, isolated, hiding the device's
support key). On Linux, where unprivileged user namespaces can be built
(the helper's own probe; CI runs it where the runner allows them): a real
session whose writes to the home folder fail, whose /tmp and HOME are a
private tmpfs that leaves nothing on the host, which can't read the support
key, and whose every process dies when the helper is killed (a client
crash).
"""

from __future__ import annotations

import os
import secrets
import shutil
import sys
import time
from pathlib import Path

import pytest

from satellite import support_sandbox as sb
from satellite import support_shell as sh

MOUNTINFO = """\
22 1 179:2 / / ro,relatime shared:1 - ext4 /dev/root rw
23 22 0:5 / /dev rw,nosuid,relatime shared:2 - devtmpfs udev rw
24 23 0:20 / /dev/shm rw,nosuid,nodev shared:3 - tmpfs tmpfs rw
25 22 0:21 / /proc rw,nosuid,nodev,noexec,relatime shared:4 - proc proc rw
26 22 0:22 / /sys rw,nosuid,nodev,noexec,relatime shared:5 - sysfs sysfs rw
27 22 179:2 /home/alex/.domovoi /home/alex/.domovoi rw,relatime shared:1 - ext4 /dev/root rw
28 22 179:2 /home/alex/domovoi /home/alex/domovoi rw,nosuid,nodev,noatime shared:1 - ext4 /dev/root rw
29 22 0:30 / /tmp rw,nosuid,nodev shared:6 - tmpfs tmpfs rw
30 22 179:1 / /boot/firm\\040ware rw,relatime shared:7 - vfat /dev/mmcblk0p1 rw
31 22 0:31 / /run ro,nosuid,nodev,noexec shared:8 - tmpfs tmpfs rw
32 22 0:32 / /mnt/usb rw,relatime - vfat /dev/sda1 rw
33 22 0:33 / /mnt/usb ro,relatime - tmpfs over rw
"""


def test_every_writable_mount_outside_the_kernels_own_is_remounted_read_only() -> None:
    mounts = sb.parse_mountinfo(MOUNTINFO)
    points = sorted(m.point for m in sb.writable_mounts(mounts))
    # Not /proc, /sys, /dev or under it; not one already read-only; the
    # top of a stacked point decides (/mnt/usb is read-only on top).
    assert points == ["/boot/firm ware", "/home/alex/.domovoi", "/home/alex/domovoi", "/tmp"]
    # The sandbox's own tmpfs mounts and hidden files may stay writable.
    assert "/tmp" not in [m.point for m in sb.writable_mounts(mounts, ("/tmp",))]


def test_a_remount_keeps_the_mounts_locked_flags() -> None:
    f = sb.remount_flags(frozenset({"rw", "nosuid", "nodev", "noatime"}))
    assert f & sb.MS_RDONLY and f & sb.MS_BIND and f & sb.MS_REMOUNT
    assert f & sb.MS_NOSUID and f & sb.MS_NODEV and f & sb.MS_NOATIME
    assert not f & sb.MS_NOEXEC and not f & sb.MS_RELATIME
    f = sb.remount_flags(frozenset({"rw", "relatime", "noexec"}))
    assert f & sb.MS_RELATIME and f & sb.MS_NOEXEC
    assert sb.remount_flags(frozenset({"rw"})) & sb.MS_STRICTATIME   # the atime mode is kept too


def test_the_shell_starts_through_the_helper_hiding_the_key() -> None:
    argv = sh.PtyBackend().argv("/bin/bash", "/home/alex")
    assert argv[:3] == [sys.executable, "-I", str(sh.SANDBOX_SCRIPT)]
    assert argv[3:] == ["--home", "/home/alex", "--hide", str(sh.SUPPORT_KEY_PATH), "/bin/bash"]
    assert sh.SANDBOX_SCRIPT.name == "support_sandbox.py" and sh.SANDBOX_SCRIPT.is_file()
    # The helper imports nothing from the satellite package: it runs with -I.
    src = Path(sb.__file__).read_text(encoding="utf-8")
    assert "from satellite" not in src and "import satellite" not in src
    assert sh.PtyBackend(sandboxed=False).argv("/bin/sh", "/h")[2] == "-c"


def test_bad_arguments_run_nothing() -> None:
    assert sb.main([]) == sb.EXIT_USAGE
    assert sb.main(["--home"]) == sb.EXIT_USAGE
    assert sb.main(["--home", "/h"]) == sb.EXIT_USAGE          # no shell
    assert sb.main(["--bogus", "--home", "/h", "/bin/sh"]) == sb.EXIT_USAGE


# ─── the real thing (Linux, where the namespaces can be built) ────────────

_CAN_BUILD = sys.platform.startswith("linux") and sh.sandbox_available()
needs_sandbox = pytest.mark.skipif(
    not _CAN_BUILD, reason="needs Linux with unprivileged user namespaces (the helper's probe)")


def _drain(pty, out: bytearray, want: bytes, timeout: float = 10.0) -> bytes:
    deadline = time.monotonic() + timeout
    while want not in out and time.monotonic() < deadline:
        chunk = pty.read(4096, 0.2)
        if chunk is None:
            break
        out += chunk
    return bytes(out)


def _alive_with(marker: str) -> list[str]:
    found = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        if marker.encode() in cmd:
            found.append(pid)
    return found


@needs_sandbox
def test_a_real_session_leaves_nothing_behind() -> None:
    import pwd

    tok = secrets.token_hex(6)
    home = pwd.getpwuid(os.getuid()).pw_dir
    # The key file lives outside /tmp (the sandbox's /tmp is its own).
    keydir = Path(sh.__file__).resolve().parents[1] / f".pytest-sandbox-{tok}"
    keydir.mkdir()
    key = keydir / "support_key"
    key.write_text("00" * 32 + "\n")
    os.chmod(key, 0o600)
    pty = sh.PtyBackend(hide=(key,))
    pty.spawn(80, 24)
    out = bytearray()
    try:
        pty.write((
            f"echo H=$HOME P=$(pwd) T=$TMPDIR\n"
            f"touch {home}/.leak-{tok} 2>/dev/null && echo HOME$((1))W\n"
            f"touch {keydir}/leak 2>/dev/null && echo CODE$((1))W\n"
            f"echo x > /tmp/leak-{tok} && echo TMP$((1))W\n"
            f"echo kept > $HOME/f && cat $HOME/f\n"
            f"echo KEY=$(wc -c < {key})\n"
            f"echo PIDS=$(ls /proc | grep -c '^[0-9]')\n"
            f"echo END$((7)){tok}\n").encode())
        text = _drain(pty, out, f"END7{tok}".encode()).decode(errors="replace")
        assert "H=/tmp/session P=/tmp/session T=/tmp" in text
        assert "HOME1W" not in text and "CODE1W" not in text      # read-only
        assert "TMP1W" in text and "kept" in text                 # the private tmpfs
        assert "KEY=0" in text                                     # hidden
        pids = int(text.split("PIDS=")[1].split()[0])
        assert pids < 10                                           # its own /proc
        assert not Path(f"/tmp/leak-{tok}").exists()               # nothing on the host
        assert not Path(home, f".leak-{tok}").exists()
        assert not (keydir / "leak").exists()
        # A background process, then the client "crashes" (the helper is
        # killed outright): everything in the namespace goes with it.
        marker = f"marker-{tok}"
        pty.write(f"sh -c 'sleep 600' {marker} &\necho BG$((2)){tok}\n".encode())
        _drain(pty, out, f"BG2{tok}".encode())
        deadline = time.monotonic() + 5
        while not _alive_with(marker) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _alive_with(marker)
        assert pty._proc is not None
        pty._proc.kill()
        deadline = time.monotonic() + 5
        while _alive_with(marker) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _alive_with(marker) == []
    finally:
        pty.terminate()
        shutil.rmtree(keydir, ignore_errors=True)


@needs_sandbox
def test_a_normal_end_removes_the_session_directory() -> None:
    """The working directory is in the namespace's own tmpfs: after the
    session it exists nowhere, and the next session starts empty."""
    for round_ in range(2):
        pty = sh.PtyBackend(hide=())
        pty.spawn(80, 24)
        out = bytearray()
        try:
            pty.write(b"ls -A $HOME | wc -l | sed 's/^/N=/'; touch $HOME/left; echo D$((3))ONE\n")
            text = _drain(pty, out, b"D3ONE").decode(errors="replace")
            assert "N=0" in text, (round_, text)
        finally:
            assert pty.terminate() is not None
        assert not Path("/tmp/session/left").exists()
