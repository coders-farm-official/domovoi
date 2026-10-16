"""The room stick's scripts run whole, against stand-ins for the installer
and the room (security review of wf/usb-stick, round 1).

* erase-confirm.sh (partman/early_command) with a stand-in debconf that
  behaves like the installer's (a question already seen is not shown; Go
  Back; Continue on a pre-filled field), stand-ins for list-devices, wget,
  mount, blkid and poweroff, over a made-up /sys (DS_ROOT): the ERASE gate
  and every refusal, that it fails closed (a power-off that returns never
  goes on), and that the partitioner's confirmations exist only after
  ERASE.
* early.sh: network preseeding switched off, udhcpc's boot file dropped.
* late.sh against a made-up /target (in-target, chroot, install stand-ins).
* firstboot.sh against a made-up room (the bundle's install-pc.sh a
  stand-in): the bundle's SHA-256, the exact install command, retries, the
  clock.
* What the update blacklist and the tar filter actually do.

Under a real bash in POSIX mode (Git Bash on Windows, never WSL's
launcher); the installer's own busybox runs the same scripts in the QEMU
matrix (scripts/dev/usb-stick/matrix.py).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from domovoi.tests.test_usb_stick_installer import BASH, TREE, _posix, requires_bash
from domovoi.usb_stick import image, repack
from satellite import enrolment

STICK = image.STICK_DIR
pytestmark = requires_bash
# Git Bash's bin/bash.exe is a launcher that puts its own /usr/bin first on
# PATH, ahead of the stand-ins: run MSYS's bash itself.
SH = BASH
if BASH and sys.platform == "win32":
    _real = Path(BASH).parent.parent / "usr" / "bin" / "bash.exe"
    if _real.is_file():
        SH = str(_real)

CONFMODULE = r'''
DC=$SB/debconf
mkdir -p "$DC"
_k() { echo "$1" | tr '/' '_'; }
db_get() { RET=$(cat "$DC/$(_k "$1").v" 2>/dev/null); return 0; }
db_set() { printf '%s' "$2" >"$DC/$(_k "$1").v"; echo "set $1=$2" >>"$SB/calls"; return 0; }
db_fset() { [ "$2" = seen ] && printf '%s' "$3" >"$DC/$(_k "$1").seen"; echo "fset $1 $2 $3" >>"$SB/calls"; return 0; }
db_subst() { echo "subst $1 $2 $3" >>"$SB/calls"; return 0; }
db_input() {
	if [ "$(cat "$DC/$(_k "$2").seen" 2>/dev/null)" = true ]; then echo "skipped $2" >>"$SB/calls"; return 30; fi
	echo "$2" >"$SB/shown"; echo "shown $2" >>"$SB/calls"; return 0
}
db_go() {
	[ -s "$SB/shown" ] || return 0
	q=$(cat "$SB/shown"); : >"$SB/shown"
	a=$(head -n 1 "$SB/answers" 2>/dev/null); sed -i '1d' "$SB/answers" 2>/dev/null
	echo "answer $q [$a]" >>"$SB/calls"
	case $a in
		"<back>") return 30 ;;
		"<enter>") ;;
		*) printf '%s' "$a" >"$DC/$(_k "$q").v" ;;
	esac
	printf true >"$DC/$(_k "$q").seen"
	return 0
}
'''

STUBS = {
    "list-devices": 'cat "$SB/devices"\n',
    "wget": 'case "$*" in *deb.debian.org*) [ -e "$SB/internet" ] ;; *) [ -e "$SB/server" ] ;; esac\n',
    "mount": ('echo "mount $*" >>"$SB/calls"\nsrc=$5 dir=$6\nf="$SB/ext4-${src##*/}"\n[ -f "$f" ] || exit 32\n'
              'mkdir -p "$dir/usr/local/lib/domovoi-stick"\ncp "$f" "$dir/usr/local/lib/domovoi-stick/room-name"\n'),
    "umount": 'for a; do d=$a; done\necho "umount $*" >>"$SB/calls"\nrm -rf "$d/usr"\n',
    "poweroff": ('echo poweroff >>"$SB/calls"\ntouch "$SB/poweroff"\n'
                 '[ -e "$SB/poweroff-returns" ] && exit 0\nkill -9 $PPID\n'),
    "sleep": ('[ -e "$SB/poweroff" ] || exit 0\necho sleep >>"$SB/calls"\n'
              'n=$(grep -c "^sleep" "$SB/calls")\nif [ "$n" -ge 3 ]; then touch "$SB/hung"; kill -9 $PPID; fi\n'),
    "debconf-loadtemplate": "exit 0\n",
    "modprobe": "exit 0\n",
    "blkid": 'for a; do d=$a; done\nf="$SB/blkid-${d##*/}"\n[ -f "$f" ] && cat "$f"\nexit 0\n',
}

NEHALEM = ("fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse sse2 "
           "ss syscall nx rdtscp lm pni ssse3 cx16 sse4_1 sse4_2 popcnt hypervisor lahf_lm")
HDD = 'disk sda pci0000:00/0000:00:17.0/ata1/host0 1000 1 0 0 "WDC WD10EZEX" ATA "" sda1 sda2\n'
STICKDISK = 'disk sdb pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host6/target6 32 1 1 0 "Cruzer" SanDisk "" sdb1\n'
HDD2 = 'disk sdd pci0000:00/0000:00:17.0/ata2/host1 500 1 0 0 "ST500" ATA "" sdd1\n'
USBDISK = 'disk sde pci0000:00/0000:00:14.0/usb2/2-2/2-2:1.0/host7/target7 2000 1 0 0 "Elements" WD "" sde1\n'


def _stubs(bindir: Path) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    for name, body in STUBS.items():
        p = bindir / name
        p.write_bytes(("#!/bin/sh\n" + body).encode())
        p.chmod(0o755)


class Box:
    """One erase-confirm.sh run: the made-up computer, the stick, debconf."""

    def __init__(self, tmp: Path, *, tree: str = HDD + STICKDISK, cpu: str = NEHALEM, internet: bool = True,
                 server: bool = True, uefi: bool = False, wifi: bool = False, answers: list[str] | None = None):
        self.sb = tmp / "sb"
        self.root = tmp / "root"
        self.stick = tmp / "stick"
        for d in (self.sb, self.stick):
            d.mkdir(parents=True)
        setup = tree + 'printf "/dev/sdb1 /cdrom iso9660 ro 0 0\\n" >"$R/proc/mounts"\n'
        setup += f'printf "processor\\t: 0\\nflags\\t\\t: {cpu}\\n" >"$R/proc/cpuinfo"\n'
        if uefi:
            setup += 'mkdir -p "$R/sys/firmware/efi"\n'
        r = subprocess.run([BASH, "-c", TREE + setup, "bash", _posix(self.root)], capture_output=True, text=True,
                           env={**os.environ, "MSYS": "winsymlinks:lnk"})
        assert r.returncode == 0, r.stderr
        for name in ("lib.sh", "templates"):
            shutil.copy(STICK / "installer" / name, self.stick / name)
        (self.stick / "room-name").write_text(newline="\n", data="Kitchen\n", encoding="utf-8")
        (self.stick / "core-url").write_text(newline="\n", data="http://192.168.0.117:6370\n", encoding="utf-8")
        (self.stick / "wifi").write_text(newline="\n", data="on\n" if wifi else "off\n", encoding="utf-8")
        (self.sb / "confmodule").write_bytes(CONFMODULE.encode())
        _stubs(self.sb / "bin")
        (self.sb / "devices").write_text(newline="\n", data="/dev/sda\n/dev/sdb\n/dev/sdd\n", encoding="utf-8")
        if internet:
            (self.sb / "internet").touch()
        if server:
            (self.sb / "server").touch()
        (self.sb / "answers").write_text(newline="\n", data="".join(a + "\n" for a in (answers or [])), encoding="utf-8")
        self.set("pkgsel/include", "python3 alsa-utils")

    def set(self, q: str, value: str, seen: bool | None = None) -> None:
        d = self.sb / "debconf"
        d.mkdir(exist_ok=True)
        (d / (q.replace("/", "_") + ".v")).write_text(newline="\n", data=value, encoding="utf-8")
        if seen is not None:
            (d / (q.replace("/", "_") + ".seen")).write_text(newline="\n", data="true" if seen else "false", encoding="utf-8")

    def get(self, q: str) -> str | None:
        p = self.sb / "debconf" / (q.replace("/", "_") + ".v")
        return p.read_text(encoding="utf-8") if p.exists() else None

    def run(self) -> subprocess.CompletedProcess:
        env = {**os.environ, "SB": _posix(self.sb), "DS_ROOT": _posix(self.root), "DS_STICK_DIR": _posix(self.stick),
               "DS_CONFMODULE": _posix(self.sb / "confmodule"), "DS_LOG": _posix(self.sb / "log"),
               "DS_LOOK": _posix(self.sb / "look"), "MSYS": "winsymlinks:lnk"}
        env["PATH"] = _posix(self.sb / "bin") + os.pathsep + env.get("PATH", "")
        if sys.platform == "win32":
            env["PATH"] = _posix(self.sb / "bin") + ":/usr/bin:/bin"
        self.result = subprocess.run([SH, "--posix", _posix(STICK / "installer" / "erase-confirm.sh")],
                                     capture_output=True, text=True, env=env, timeout=120)
        self.calls = (self.sb / "calls").read_text(encoding="utf-8").splitlines() if (self.sb / "calls").exists() else []
        return self.result

    def shown(self) -> list[str]:
        return [c.split(" ", 1)[1] for c in self.calls if c.startswith("shown ")]

    @property
    def powered_off(self) -> bool:
        return "poweroff" in self.calls

    @property
    def erased(self) -> bool:
        """Anything the partitioner would act on without asking."""
        return any(c.startswith(("set partman", "set grub-installer")) for c in self.calls)


CONFIRMS = ("partman-auto/purge_lvm_from_device", "partman-lvm/device_remove_lvm", "partman-md/device_remove_md",
            "partman-lvm/confirm", "partman-lvm/confirm_nooverwrite", "partman-partitioning/confirm_write_new_label",
            "partman/choose_partition", "partman/confirm", "partman/confirm_nooverwrite")


# ─── ERASE ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("typed", ["ERASE", "erase", "  Erase  "])
def test_erase_names_the_disk_and_only_then_answers_the_partitioner(tmp_path, typed):
    b = Box(tmp_path, answers=[typed])
    assert b.run().returncode == 0, b.result.stderr
    assert b.shown() == ["domovoi-stick/erase"]
    assert "subst domovoi-stick/erase DISK ATA WDC WD10EZEX, 1000 GB (/dev/sda)" in b.calls
    assert b.get("partman-auto/disk") == "/dev/sda" and b.get("grub-installer/bootdev") == "/dev/sda"
    for q in CONFIRMS:
        assert b.get(q) in ("true", "finish"), q
        assert f"fset {q} seen true" in b.calls, q
    erase_answer = b.calls.index("answer domovoi-stick/erase [" + typed + "]")
    assert all(b.calls.index(f"set {q}={b.get(q)}") > erase_answer for q in CONFIRMS)
    assert "shim-signed" not in b.get("pkgsel/include"), "BIOS: no shim"
    assert not b.powered_off


def test_on_uefi_the_signed_shim_and_grub_are_installed(tmp_path):
    b = Box(tmp_path, uefi=True, answers=["ERASE"])
    assert b.run().returncode == 0
    assert b.get("pkgsel/include") == "python3 alsa-utils grub-efi-amd64-signed shim-signed"


@pytest.mark.parametrize("answers", [["nope", "ERASED", "erase it"], ["", "", ""], ["E RASE", "ERAS", "ERASEERASE"]],
                         ids=["wrong", "empty", "near-misses"])
def test_three_wrong_answers_change_nothing_and_power_off(tmp_path, answers):
    b = Box(tmp_path, answers=answers)
    b.run()
    assert b.shown() == ["domovoi-stick/erase", "domovoi-stick/erase-again", "domovoi-stick/erase-again",
                         "domovoi-stick/stopped"]
    assert b.powered_off and not b.erased


def test_go_back_stops(tmp_path):
    b = Box(tmp_path, answers=["<back>"])
    b.run()
    assert b.shown() == ["domovoi-stick/erase", "domovoi-stick/stopped"]
    assert b.powered_off and not b.erased


def test_a_pre_filled_erase_is_not_an_answer(tmp_path):
    """Something put ERASE in the field already (an earlier try, a preseed):
    Continue without typing must not count."""
    b = Box(tmp_path, answers=["<enter>", "<enter>", "<enter>"])
    b.set("domovoi-stick/erase", "ERASE", seen=False)
    b.set("domovoi-stick/erase-again", "ERASE", seen=False)
    b.run()
    assert b.powered_off and not b.erased


def test_an_erase_already_marked_seen_is_still_asked(tmp_path):
    """A preseeded answer (value + seen) would skip the question: it is
    reset and asked."""
    b = Box(tmp_path, answers=["nope", "nope", "nope"])
    b.set("domovoi-stick/erase", "ERASE", seen=True)
    b.run()
    assert "skipped domovoi-stick/erase" not in b.calls and b.shown()[0] == "domovoi-stick/erase"
    assert b.powered_off and not b.erased


def test_a_power_off_that_returns_never_goes_on(tmp_path):
    """Firmware that ignores the power-off: stop() waits for ever, never
    returns to the partitioner."""
    b = Box(tmp_path, answers=["nope", "nope", "nope"])
    (b.sb / "poweroff-returns").touch()
    b.run()
    assert b.powered_off and (b.sb / "hung").exists(), "it waits after the power-off"
    assert not b.erased
    assert b.result.returncode != 0


@pytest.mark.parametrize("box, page", [
    (dict(internet=False), "domovoi-stick/no-network"),
    (dict(internet=False, wifi=True), "domovoi-stick/no-network-wifi"),
    (dict(cpu="fpu sse sse2 pni ssse3 cx16 sse4_1 lahf_lm"), "domovoi-stick/cpu-too-old"),
    (dict(tree=STICKDISK), "domovoi-stick/no-disk"),
], ids=["no-internet", "no-internet-wifi", "old-cpu", "no-disk"])
def test_each_refusal_comes_before_the_question(tmp_path, box, page):
    b = Box(tmp_path, answers=["ERASE"], **box)
    b.run()
    assert b.shown() == [page] and b.powered_off and not b.erased


def test_answers_from_the_network_are_refused(tmp_path):
    b = Box(tmp_path, answers=["ERASE"])
    b.set("preseed/url", "http://10.0.2.2/evil.cfg")
    b.run()
    assert b.shown() == ["domovoi-stick/tampered"] and b.powered_off and not b.erased
    c = Box(tmp_path / "c", answers=["ERASE"])
    (c.root / "var" / "lib" / "dpkg" / "info").mkdir(parents=True)
    (c.root / "var" / "lib" / "dpkg" / "info" / "network-preseed.postinst").write_text(newline="\n", data="x")
    c.run()
    assert c.shown() == ["domovoi-stick/tampered"] and not c.erased


def test_disks_joined_by_lvm_are_refused_naming_every_one(tmp_path):
    b = Box(tmp_path, tree=HDD + STICKDISK + HDD2, answers=["ERASE"])
    (b.sb / "blkid-sda2").write_text(newline="\n", data="LVM2_member\n")
    (b.sb / "blkid-sdd1").write_text(newline="\n", data="LVM2_member\n")
    b.run()
    assert b.shown() == ["domovoi-stick/joined"] and b.powered_off and not b.erased
    subst = next(c for c in b.calls if c.startswith("subst domovoi-stick/joined DISKS"))
    assert "(/dev/sda)" in subst and "(/dev/sdd)" in subst
    # LVM on the chosen disk alone is that disk's own business.
    c = Box(tmp_path / "c", tree=HDD + STICKDISK + HDD2, answers=["ERASE"])
    (c.sb / "blkid-sda2").write_text(newline="\n", data="LVM2_member\n")
    c.run()
    assert c.shown() == ["domovoi-stick/erase"] and c.get("partman-auto/disk") == "/dev/sda"


@pytest.mark.parametrize("kind", ["linux_raid_member", "isw_raid_member", "ddf_raid_member"])
def test_disks_joined_by_raid_are_refused_naming_every_one(tmp_path, kind):
    b = Box(tmp_path, tree=HDD + STICKDISK + HDD2, answers=["ERASE"])
    (b.sb / "blkid-sda1").write_text(newline="\n", data=kind + "\n")
    (b.sb / "blkid-sdd").write_text(newline="\n", data=kind + "\n")
    b.run()
    assert b.shown() == ["domovoi-stick/joined"] and b.powered_off and not b.erased
    subst = next(c for c in b.calls if c.startswith("subst domovoi-stick/joined DISKS"))
    assert "(/dev/sda)" in subst and "(/dev/sdd)" in subst


def test_a_volume_group_reaching_onto_a_usb_disk_is_refused_naming_it(tmp_path):
    """The USB disk is never the one erased, but a volume group spanning it
    and the chosen disk would be broken (or cleaned up) all the same."""
    b = Box(tmp_path, tree=HDD + STICKDISK + USBDISK, answers=["ERASE"])
    (b.sb / "devices").write_text(newline="\n", data="/dev/sda\n/dev/sdb\n/dev/sde\n")
    (b.sb / "blkid-sda2").write_text(newline="\n", data="LVM2_member\n")
    (b.sb / "blkid-sde1").write_text(newline="\n", data="LVM2_member\n")
    b.run()
    assert b.shown() == ["domovoi-stick/joined"] and b.powered_off and not b.erased
    subst = next(c for c in b.calls if c.startswith("subst domovoi-stick/joined DISKS"))
    assert "(/dev/sda)" in subst and "WD Elements, 2000 GB (/dev/sde)" in subst
    assert "/dev/sdb" not in subst, "the stick is never one of them"
    # Unplugged, the chosen disk's LVM is its own business again.
    c = Box(tmp_path / "c", tree=HDD + STICKDISK + USBDISK, answers=["ERASE"])
    (c.sb / "devices").write_text(newline="\n", data="/dev/sda\n/dev/sdb\n")
    (c.sb / "blkid-sda2").write_text(newline="\n", data="LVM2_member\n")
    c.run()
    assert c.shown() == ["domovoi-stick/erase"] and c.get("partman-auto/disk") == "/dev/sda"


def test_a_computer_that_is_already_a_room_says_so(tmp_path):
    b = Box(tmp_path, answers=["nope", "nope", "nope"])
    (b.sb / "ext4-sda2").write_text(newline="\n", data="Kitchen\n")
    b.run()
    assert b.shown()[0] == "domovoi-stick/erase-room"
    assert "subst domovoi-stick/erase-room OLDROOM Kitchen" in b.calls
    assert any(c.startswith("mount -t ext4 -o ro,noload /dev/sda2") for c in b.calls)
    assert any(c.startswith("umount") for c in b.calls)


def test_the_answers_file_confirms_nothing_by_itself():
    text = (STICK / "preseed.cfg.in").read_text(encoding="utf-8")
    for q in CONFIRMS + ("partman-auto/disk", "grub-installer/bootdev"):
        assert not re.search(rf"^d-i {re.escape(q)} ", text, re.M), q
    assert re.search(r"^d-i preseed/early_command string sh /domovoi-stick/early.sh$", text, re.M)


# ─── early.sh: only the stick's answers ──────────────────────────────────

def test_early_switches_network_preseeding_off_and_drops_the_boot_file(tmp_path):
    root, stick = tmp_path / "root", tmp_path / "stick"
    (root / "var" / "lib" / "dpkg").mkdir(parents=True)
    (root / "etc" / "udhcpc").mkdir(parents=True)
    stick.mkdir()
    (root / "var" / "lib" / "dpkg" / "status").write_text(newline="\n", data="Package: cdebconf-udeb\nStatus: install ok installed\n"
                                                          "Version: 0.276\n", encoding="utf-8")
    seen = tmp_path / "seen"
    (root / "etc" / "udhcpc" / "default.script").write_bytes(
        f'#!/bin/sh\necho "file=[$boot_file] $1" >"{_posix(seen)}"\n'.encode())
    shutil.copy(STICK / "installer" / "udhcpc-default.script", stick / "udhcpc-default.script")
    env = {**os.environ, "DS_ROOT": _posix(root), "DS_STICK_DIR": _posix(stick)}
    for _ in range(2):  # twice: nothing doubles
        r = subprocess.run([SH, "--posix", _posix(STICK / "installer" / "early.sh")], capture_output=True,
                           text=True, env=env)
        assert r.returncode == 0, r.stderr
    status = (root / "var" / "lib" / "dpkg" / "status").read_text(encoding="utf-8")
    assert status.count("Package: network-preseed\nStatus: install ok installed\nVersion: 99:0+domovoi") == 1
    assert status.startswith("Package: cdebconf-udeb"), "Debian's entries kept"
    # udhcpc's script now drops the boot file before Debian's runs. (Run with
    # the wrapper's exec target pointed at the made-up root.)
    wrapper = (root / "etc" / "udhcpc" / "default.script").read_text(encoding="utf-8")
    assert "boot_file=" in wrapper and (root / "etc" / "udhcpc" / "default.script.debian").exists()
    test_wrapper = tmp_path / "w.sh"
    test_wrapper.write_text(newline="\n", data=wrapper.replace("/etc/udhcpc/default.script.debian",
                                            _posix(root / "etc" / "udhcpc" / "default.script.debian")))
    subprocess.run([SH, "--posix", _posix(test_wrapper), "bound"], env={**os.environ,
                   "boot_file": "http://10.0.2.2:8000/evil.cfg"}, check=True)
    assert seen.read_text().strip() == "file=[] bound"


# ─── late.sh: the new system ─────────────────────────────────────────────

LATE_STUBS = {
    "in-target": 'echo "in-target $*" >>"$SB/calls"\nexit 0\n',
    "chroot": ('shift\nif [ "$1" = dpkg-query ]; then for a; do p=$a; done\n'
               '  grep -qx "$p" "$SB/installed" && echo "install ok installed"; exit 0; fi\nexit 1\n'),
    "install": ('echo "install $*" >>"$SB/calls"\nwhile [ $# -gt 2 ]; do case $1 in -m) m=$2; shift 2;; -o|-g) shift 2;; '
                '*) shift;; esac; done\ncp "$1" "$2" && chmod "$m" "$2"\n'),
}


def _late(tmp_path: Path, *, uefi: bool, debug: bool = False) -> tuple[Path, list[str]]:
    sb, t, src, stick, root = (tmp_path / n for n in ("sb", "target", "src", "stick", "root"))
    for d in (sb, stick, root, t / "etc" / "systemd" / "system", t / "etc" / "apt" / "apt.conf.d",
              t / "usr" / "local" / "sbin", t / "etc" / "default"):
        d.mkdir(parents=True, exist_ok=True)
    if uefi:
        (root / "sys" / "firmware" / "efi").mkdir(parents=True)
    (t / "etc" / "default" / "console-setup").write_text(newline="\n", data='FONTFACE="Fixed"\nFONTSIZE="8x16"\n')
    (t / "etc" / "default" / "grub").write_text(newline="\n", data='GRUB_TIMEOUT=5\nGRUB_CMDLINE_LINUX_DEFAULT="quiet"\n')
    tar, _ = image.stick_files(pin=image.load_pin(), payload=enrolment.build("http://10.0.0.5:6370", "SHA256:" + "A" * 43,
                               "Kitchen"), bundle=b"\x1f\x8bbundle", epoch=1_760_000_000,
                               options=image.Options(debug=debug))
    with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
        tf.extractall(src, filter="data")
    (stick / "room-name").write_text(newline="\n", data="Kitchen\n")
    if debug:
        (stick / "debug").touch()
    (sb / "installed").write_text(newline="\n", data="shim-signed\nshim-helpers-amd64-signed\ngrub-efi-amd64-signed\ngrub-efi-amd64-bin\n"
                                  "grub-common\ngrub2-common\nlinux-image-amd64\n")
    for name, body in LATE_STUBS.items():
        (sb / name).write_bytes(("#!/bin/sh\n" + body).encode())
    env = {**os.environ, "SB": _posix(sb), "DS_ROOT": _posix(root), "DS_STICK_DIR": _posix(stick),
           "DS_SRC": _posix(src / "domovoi"), "DS_TARGET": _posix(t), "DS_LOG": _posix(sb / "log")}
    env["PATH"] = _posix(sb) + (":/usr/bin:/bin" if sys.platform == "win32" else os.pathsep + env["PATH"])
    r = subprocess.run([SH, "--posix", _posix(STICK / "installer" / "late.sh")], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return t, (sb / "calls").read_text().splitlines()


def test_late_sets_up_the_room_and_freezes_the_boot_loader_on_uefi(tmp_path):
    t, calls = _late(tmp_path, uefi=True)
    lib = t / "usr" / "local" / "lib" / "domovoi-stick"
    for f in ("stick.json", "satellite.tar.gz", "firstboot.sh", "console.py", "boot-check.sh", "room-name"):
        assert (lib / f).is_file(), f
    for u in ("domovoi-stick-firstboot.service", "domovoi-stick-console.service", "domovoi-stick-boot-check.service"):
        assert (t / "etc" / "systemd" / "system" / u).is_file() and f"in-target systemctl enable {u}" in calls
    assert "in-target systemctl mask getty@tty1.service" in calls, "no login prompt over the room's screen"
    assert "in-target passwd -l root" in calls
    hold = next(c for c in calls if c.startswith("in-target apt-mark hold"))
    assert set(hold.split()[3:]) == {"shim-signed", "shim-helpers-amd64-signed", "grub-efi-amd64-signed",
                                     "grub-efi-amd64-bin", "grub-common", "grub2-common"}
    held = (t / "var" / "lib" / "domovoi-stick" / "held-packages").read_text().split()
    assert "shim-signed" in held and "linux-image-amd64" not in held
    assert (t / "etc" / "apt" / "apt.conf.d" / "52domovoi-stick").is_file()
    assert (t / "usr" / "local" / "sbin" / "domovoi-status").is_file()
    assert 'FONTSIZE="16x32"' in (t / "etc" / "default" / "console-setup").read_text()
    assert "GRUB_TIMEOUT=1" in (t / "etc" / "default" / "grub").read_text()
    assert not (t / "etc" / "systemd" / "system" / "domovoi-stick-debug.service").exists()


def test_late_holds_nothing_on_bios_and_adds_the_serial_unit_only_on_a_test_build(tmp_path):
    t, calls = _late(tmp_path / "bios", uefi=False)
    assert not any(c.startswith("in-target apt-mark") for c in calls)
    t2, calls2 = _late(tmp_path / "debug", uefi=False, debug=True)
    assert (t2 / "etc" / "systemd" / "system" / "domovoi-stick-debug.service").is_file()


# ─── firstboot.sh: the room's first start ────────────────────────────────

FB_STUBS = {
    "ip": 'echo "default via 192.168.0.1 dev eth0"\n',
    "sleep": "exit 0\n",
    "date": 'if [ "$1" = -s ]; then echo "date -s $2" >>"$SB/calls"; exit 0; fi\nexec /usr/bin/date "$@"\n',
}


def _firstboot(tmp_path: Path, *, rcs: list[int], made: int = 1_760_000_000, tamper: bool = False):
    sb, lib, state = tmp_path / "sb", tmp_path / "lib", tmp_path / "state"
    for d in (sb, lib, state):
        d.mkdir(parents=True)
    (sb / "rcs").write_text(newline="\n", data="".join(f"{r}\n" for r in rcs))
    script = ('#!/bin/sh\necho "$*" >>"$SB/install-args"\nrc=$(head -n 1 "$SB/rcs")\nsed -i 1d "$SB/rcs"\n'
              'exit "${rc:-1}"\n').encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("satellite/scripts/install-pc.sh")
        info.size, info.mode = len(script), 0o755
        tf.addfile(info, io.BytesIO(script))
    bundle = buf.getvalue()
    (lib / "satellite.tar.gz").write_bytes(bundle + (b"x" if tamper else b""))
    payload = enrolment.build("http://10.0.0.5:6370", "SHA256:" + "A" * 43, "Kitchen")
    enrol = enrolment.parse(payload)
    (lib / "stick.json").write_bytes(image.stick_json(pin=image.load_pin(), enrol=enrol, payload=payload,
                                                      bundle_sha256=hashlib.sha256(bundle).hexdigest(),
                                                      options=image.Options(), epoch=made))
    (sb / "synced").touch()
    for name, body in FB_STUBS.items():
        (sb / name).write_bytes(("#!/bin/sh\n" + body).encode())
    env = {**os.environ, "SB": _posix(sb), "FB_LIB": _posix(lib), "FB_STATE": _posix(state),
           "FB_LOG": _posix(sb / "log"), "FB_PY": _posix(Path(sys.executable)), "FB_SYNCED": _posix(sb / "synced"),
           "FB_PATH": _posix(sb) + ":/usr/bin:/bin" + ("" if sys.platform == "win32" else ":" + os.environ["PATH"])}
    r = subprocess.run([SH, _posix(STICK / "room" / "firstboot.sh")], capture_output=True, text=True, env=env,
                       timeout=120)
    args = (sb / "install-args").read_text().splitlines() if (sb / "install-args").exists() else []
    calls = (sb / "calls").read_text().splitlines() if (sb / "calls").exists() else []
    return r, args, calls, state, payload


def test_firstboot_installs_headless_automatic_with_the_stick_s_room_code(tmp_path):
    r, args, calls, state, payload = _firstboot(tmp_path, rcs=[0])
    assert r.returncode == 0, r.stdout + r.stderr
    assert args == [f"--mode headless --yes --permissions automatic --power yes --wait 0 {payload}"]
    assert (state / "installed").exists() and not (state / "bundle").exists()
    assert not any(c.startswith("date -s") for c in calls), "the clock was not behind"


def test_firstboot_keeps_trying_until_the_server_lets_it_in(tmp_path):
    r, args, _, state, _ = _firstboot(tmp_path, rcs=[3, 1, 0])
    assert r.returncode == 0 and len(args) == 3 and (state / "installed").exists()


def test_firstboot_runs_nothing_from_a_bundle_that_is_not_the_stick_s(tmp_path):
    r, args, _, state, _ = _firstboot(tmp_path, rcs=[0], tamper=True)
    assert r.returncode == 1 and args == [] and not (state / "installed").exists()
    assert "damaged" in r.stdout


def test_firstboot_sets_a_stale_clock_forward_to_the_day_the_stick_was_made(tmp_path):
    future = 4_102_444_800  # 2100: "now" is behind the stick
    _, _, calls, _, _ = _firstboot(tmp_path, rcs=[0], made=future)
    assert f"date -s @{future}" in calls


# ─── what the update policy and the tar filter do ────────────────────────

def test_the_unattended_upgrades_blacklist_keeps_the_boot_loader_and_fwupd_out():
    conf = (STICK / "room" / "52domovoi-stick").read_text(encoding="utf-8")
    block = conf[conf.index("Package-Blacklist {"):]
    patterns = re.findall(r'"([^"]+)";', block[:block.index("};")])
    blocked = lambda pkg: any(re.match(p, pkg) for p in patterns)  # noqa: E731 (u-u: re.match per entry)
    for pkg in ("shim-signed", "shim-signed-common", "shim-helpers-amd64-signed", "grub-efi-amd64-signed",
                "grub-efi-amd64-bin", "grub-common", "grub2-common", "fwupd"):
        assert blocked(pkg), pkg
    for pkg in ("linux-image-amd64", "openssl", "libc6", "python3"):
        assert not blocked(pkg), pkg
    assert re.search(r'^Unattended-Upgrade::Automatic-Reboot "true";', conf, re.M)


def test_unpack_never_writes_outside_its_folder(tmp_path):
    """The folder exists first, so without the ``data`` filter the ``..``
    really would land outside (on Windows too): only the filter stops it."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        folder = tarfile.TarInfo("domovoi")
        folder.type, folder.mode = tarfile.DIRTYPE, 0o755
        t.addfile(folder)
        info = tarfile.TarInfo("domovoi/../../escaped.txt")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    f = tmp_path / "bad.tar"
    f.write_bytes(buf.getvalue())
    with pytest.raises(tarfile.OutsideDestinationError):
        repack.unpack(f, tmp_path / "a" / "out")
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path / "a" / "escaped.txt").exists()
