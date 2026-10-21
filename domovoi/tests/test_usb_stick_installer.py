"""The room stick's own scripts (domovoi/usb_stick/stick/), away from the
installer: which disk the ERASE page names, the refusals before it, the
per-boot Secure Boot check, the spoken code, the room's screen, and what
the satellite and the core make of the Secure Boot facts.

Old computers (USB stick), 2026-10-16. The shell pieces run under a real
bash (Git Bash on Windows, never WSL's launcher) against made-up /sys and
/proc trees; symlinks, which sysfs is made of, are made with ln -s (MSYS
"lnk" links on Windows, which its readlink follows). The installer itself,
partman and a real disk are the QEMU kit's (scripts/dev/usb-stick/).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from domovoi.usb_stick import image

STICK = image.STICK_DIR
REPO = Path(__file__).resolve().parents[2]


def _find_bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        for up in (Path(git).parent.parent, Path(git).parent.parent.parent):
            candidates.append(up / "bin" / "bash.exe")
    candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file() and "system32" not in str(c).lower():
            return str(c)
    return None


BASH = _find_bash()
requires_bash = pytest.mark.skipif(BASH is None, reason="no usable bash (Git Bash on Windows)")


def _posix(p: Path) -> str:
    s = str(p)
    if sys.platform == "win32" and len(s) > 1 and s[1] == ":":
        return "/" + s[0].lower() + s[2:].replace("\\", "/")
    return s


def bash(script: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", script, "bash", *args], capture_output=True, text=True,
                          env={**os.environ, "MSYS": "winsymlinks:lnk", **(env or {})}, timeout=60)


# ─── every script: LF, and it parses ──────────────────────────────────────

SCRIPTS = sorted(p for p in STICK.rglob("*") if p.is_file() and (
    p.suffix in (".sh", ".script") or p.name == "domovoi-status"))


def test_every_stick_file_is_lf_only():
    for p in STICK.rglob("*"):
        if p.is_file():
            assert b"\r" not in p.read_bytes(), p


@requires_bash
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_script_parses(script):
    first = script.read_text(encoding="utf-8").splitlines()[0]
    shell = ["-n"] if "bash" in first else ["--posix", "-n"]
    r = subprocess.run([BASH, *shell, _posix(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_installer_scripts_are_posix_sh_with_no_bashisms():
    """busybox ash runs them inside Debian's installer."""
    for name in ("early.sh", "erase-confirm.sh", "late.sh", "lib.sh", "udhcpc-default.script"):
        text = (STICK / "installer" / name).read_text(encoding="utf-8")
        for bashism in ("[[", "local ", "function ", "${!", "<<<", "$((++", "declare "):
            assert bashism not in text, f"{name}: {bashism}"


# ─── which disk: lib.sh ───────────────────────────────────────────────────

TREE = r'''
set -eu
R=$1
mkdir -p "$R/sys/block" "$R/proc" "$R/sys/bus/pci/devices"
disk() {  # disk NAME DEVPATH GB ROTATIONAL REMOVABLE RO MODEL VENDOR [TYPE] [PART...]
  local name=$1 path=$2 gb=$3 rot=$4 rem=$5 ro=$6 model=$7 vendor=$8 type=${9:-}
  shift 9 2>/dev/null || shift $#
  local d="$R/devices/$path/block/$name"
  mkdir -p "$d/queue" "$d/device"
  echo $((gb * 1000 * 1000 * 2)) >"$d/size"
  echo "$rot" >"$d/queue/rotational"; echo "$rem" >"$d/removable"; echo "$ro" >"$d/ro"
  printf '%s\n' "$model" >"$d/device/model"; printf '%s\n' "$vendor" >"$d/device/vendor"
  [ -z "$type" ] || echo "$type" >"$d/device/type"
  local p; for p in "$@"; do mkdir -p "$d/$p"; done
  ln -s "../../devices/$path/block/$name" "$R/sys/block/$name"
}
'''


def run_lib(root: Path, setup: str, command: str) -> str:
    script = TREE + setup + f'\n. "{_posix(STICK / "installer" / "lib.sh")}"\nDS_ROOT=$R\n' + command
    r = bash(script, _posix(root))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


HDD = 'disk sda pci0000:00/0000:00:17.0/ata1/host0 1000 1 0 0 "WDC WD10EZEX" ATA "" sda1 sda2\n'
SSD = 'disk nvme0n1 pci0000:00/0000:00:1d.0/0000:3d:00.0/nvme/nvme0 256 0 0 0 "Samsung SSD 970" "" "" nvme0n1p1\n'
STICKDISK = 'disk sdb pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host6/target6 32 1 1 0 "Cruzer Blade" SanDisk "" sdb1\n'
USB_SSD = 'disk sdc pci0000:00/0000:00:14.0/usb3/3-2/3-2:1.0/host7/target7 2000 0 0 0 "Portable SSD" Samsung ""\n'


@requires_bash
def test_a_solid_state_disk_wins_over_a_bigger_spinning_one(tmp_path):
    assert run_lib(tmp_path / "t", HDD + SSD, "ds_pick '' sda nvme0n1") == "nvme0n1"


@requires_bash
def test_the_largest_wins_between_two_of_a_kind(tmp_path):
    two = HDD + 'disk sdd pci0000:00/0000:00:17.0/ata2/host1 2000 1 0 0 "ST2000DM" ATA ""\n'
    assert run_lib(tmp_path / "t", two, "ds_pick '' sda sdd") == "sdd"


@requires_bash
def test_the_stick_and_every_usb_or_removable_disk_are_never_picked(tmp_path):
    """The stick itself, a USB backup SSD bigger than the internal disk, a
    removable one: the room goes on the laptop's own disk."""
    trees = HDD + STICKDISK + USB_SSD + 'disk sde pci0000:00/0000:00:17.0/ata3/host2 500 0 1 0 Hotswap ATA ""\n'
    out = run_lib(tmp_path / "t", trees, 'ds_log() { echo "LOG $*" >&2; }; ds_pick sdb sda sdb sdc sde')
    assert out == "sda"
    assert run_lib(tmp_path / "u", STICKDISK + USB_SSD, "ds_pick sdb sdb sdc") == "", "nothing else: no disk"


@requires_bash
def test_read_only_small_and_memory_card_disks_are_never_picked_but_emmc_is(tmp_path):
    trees = (
        'disk sda pci0000:00/0000:00:17.0/ata1/host0 500 0 0 1 RO ATA ""\n'
        'disk sdf pci0000:00/0000:00:17.0/ata4/host3 4 0 0 0 Tiny ATA ""\n'
        'disk mmcblk0 platform/80860F14:01/mmc_host/mmc0/mmc0:0001 64 0 0 0 SDCARD "" SD mmcblk0p1\n'
        'disk mmcblk1 platform/80860F14:00/mmc_host/mmc1/mmc1:0001 32 0 0 0 eMMC "" MMC mmcblk1p1\n'
    )
    assert run_lib(tmp_path / "t", trees, "ds_pick '' sda sdf mmcblk0 mmcblk1") == "mmcblk1"


@requires_bash
def test_the_stick_is_found_from_what_cdrom_is_mounted_from(tmp_path):
    setup = HDD + STICKDISK + SSD + 'printf "/dev/sdb1 /cdrom iso9660 ro 0 0\\n" >"$R/proc/mounts"\n'
    assert run_lib(tmp_path / "t", setup, "ds_stick_disk") == "sdb"
    assert run_lib(tmp_path / "u", setup, "ds_disk_of /dev/nvme0n1p1; ds_disk_of /dev/sda; ds_disk_of sda2").split() == [
        "nvme0n1", "sda", "sda"]
    assert run_lib(tmp_path / "v", HDD, "ds_stick_disk") == "", "no /cdrom: nothing"


@requires_bash
def test_the_erase_page_names_the_disk_as_a_person_knows_it(tmp_path):
    trees = HDD + SSD + 'disk sdz pci0000:00/0000:00:17.0/ata9/host9 20 1 0 0 "QEMU HARDDISK" ATA ""\n'
    out = run_lib(tmp_path / "t", trees, "ds_disk_desc sda; ds_disk_desc nvme0n1; ds_disk_desc sdz")
    assert out.splitlines() == ["ATA WDC WD10EZEX, 1000 GB (/dev/sda)", "Samsung SSD 970, 256 GB (/dev/nvme0n1)",
                                "ATA QEMU HARDDISK, 20 GB (/dev/sdz)"]


NEHALEM = "fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse sse2 ss " \
          "syscall nx rdtscp lm constant_tsc rep_good nopl xtopology cpuid pni ssse3 cx16 sse4_1 sse4_2 popcnt " \
          "hypervisor lahf_lm"
CORE2 = "fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse sse2 ss " \
        "syscall nx lm constant_tsc pni ssse3 cx16 sse4_1 lahf_lm"
QEMU64 = "fpu de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse sse2 syscall nx lm pni"


@requires_bash
@pytest.mark.parametrize("flags, missing", [(NEHALEM, ""), (CORE2, "sse4_2"), (QEMU64, "ssse3")],
                         ids=["nehalem-2008", "core2-2007", "qemu64"])
def test_a_processor_older_than_x86_64_v2_is_refused(tmp_path, flags, missing):
    setup = f'printf "processor\\t: 0\\nflags\\t\\t: {flags}\\n" >"$R/proc/cpuinfo"\n'
    assert run_lib(tmp_path / "t", setup, "ds_cpu_missing") == missing


@requires_bash
@pytest.mark.parametrize("device, t2", [("0x1802", True), ("0x1801", True), ("0x1600", False)])
def test_a_t2_mac_is_recognised(tmp_path, device, t2):
    setup = (f'mkdir -p "$R/sys/bus/pci/devices/0000:02:00.1" "$R/sys/bus/pci/devices/0000:00:02.0"\n'
             f'echo 0x106b >"$R/sys/bus/pci/devices/0000:02:00.1/vendor"; echo {device} >"$R/sys/bus/pci/devices/0000:02:00.1/device"\n'
             'echo 0x8086 >"$R/sys/bus/pci/devices/0000:00:02.0/vendor"; echo 0x1802 >"$R/sys/bus/pci/devices/0000:00:02.0/device"\n')
    assert run_lib(tmp_path / "t", setup, "if ds_t2_mac; then echo yes; else echo no; fi") == ("yes" if t2 else "no")


@requires_bash
def test_an_installed_room_s_name_is_read_as_plain_text(tmp_path):
    setup = ('mkdir -p "$R/mnt/usr/local/lib/domovoi-stick"\n'
             'printf "Kid\\x27s Room\\x1b[2J\\nsecond line\\n" >"$R/mnt/usr/local/lib/domovoi-stick/room-name"\n')
    assert run_lib(tmp_path / "t", setup, 'ds_room_name_in "$R/mnt"') == "Kid's Room[2J"
    assert run_lib(tmp_path / "u", "", 'ds_room_name_in "$R/none"') == ""


def test_erase_confirm_asks_before_partman_and_never_erases_on_anything_but_erase():
    text = (STICK / "installer" / "erase-confirm.sh").read_text(encoding="utf-8")
    preseed = (STICK / "preseed.cfg.in").read_text(encoding="utf-8")
    assert "d-i partman/early_command string sh /domovoi-stick/erase-confirm.sh" in preseed
    # Each refusal comes before the question, the question before the disk is set.
    order = ["stop domovoi-stick/no-disk", "stop domovoi-stick/cpu-too-old", "stop domovoi-stick/t2-mac",
             "stop domovoi-stick/no-network", 'db_input critical "$q"', 'db_set partman-auto/disk']
    pos = [text.index(s) for s in order]
    assert pos == sorted(pos), order
    assert 'if [ "$ans" = ERASE ]' in text and text.count("db_set partman-auto/disk") == 1
    assert text.rstrip().endswith("stop domovoi-stick/stopped"), "three wrong answers: nothing changed"
    assert "mount -t ext4 -o ro,noload" in text, "the already-a-room check writes nothing"
    late = (STICK / "installer" / "late.sh").read_text(encoding="utf-8")
    assert "apt-mark hold" in late and "fwupd" in late and "passwd -l root" in late


# ─── the per-boot Secure Boot check ──────────────────────────────────────

GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
SECDB = "d719b2cb-3d3a-4596-a3bc-dad00e67656f"


def boot_check(root: Path) -> dict:
    out = root / "run" / "boot.json"
    r = bash(f'bash "{_posix(STICK / "room" / "boot-check.sh")}"',
             env={"BC_ROOT": _posix(root), "BC_OUT": _posix(out)})
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def efi(root: Path, secure_boot: int | None, certs: list[str] | None) -> None:
    v = root / "sys" / "firmware" / "efi" / "efivars"
    v.mkdir(parents=True)
    if secure_boot is not None:
        (v / f"SecureBoot-{GLOBAL}").write_bytes(b"\x06\x00\x00\x00" + bytes([secure_boot]))
    if certs is not None:
        (v / f"db-{SECDB}").write_bytes(b"\x07\x00\x00\x00" + b"\x30\x82".join(
            b"\x0c\x16" + c.encode() + b"\x00\x01" for c in certs))


@requires_bash
def test_secure_boot_on_without_the_2023_certificate_is_reported(tmp_path):
    efi(tmp_path, 1, ["Microsoft Windows Production PCA 2011", "Microsoft Corporation UEFI CA 2011"])
    held = tmp_path / "var" / "lib" / "domovoi-stick"
    held.mkdir(parents=True)
    (held / "held-packages").write_text("shim-signed\ngrub-efi-amd64-signed\n", encoding="utf-8")
    assert boot_check(tmp_path) == {"firmware": "uefi", "secure_boot": True, "ca_2023": False, "ca_2011": True,
                                    "loader_held": True, "stick": True}


@requires_bash
def test_secure_boot_off_with_both_certificates_and_a_bios_pc(tmp_path):
    efi(tmp_path / "a", 0, ["Microsoft Corporation UEFI CA 2011", "Microsoft UEFI CA 2023"])
    assert boot_check(tmp_path / "a") == {"firmware": "uefi", "secure_boot": False, "ca_2023": True,
                                          "ca_2011": True, "loader_held": False, "stick": True}
    (tmp_path / "b").mkdir()
    assert boot_check(tmp_path / "b") == {"firmware": "bios", "secure_boot": None, "ca_2023": None,
                                          "ca_2011": None, "loader_held": False, "stick": True}
    efi(tmp_path / "c", None, None)
    assert boot_check(tmp_path / "c")["secure_boot"] is None, "unreadable: unknown, not off"


# ─── the spoken code ─────────────────────────────────────────────────────

@pytest.fixture
def speaker(tmp_path):
    clips = tmp_path / "sounds"
    clips.mkdir()
    for name in image.CLIP_NAMES:
        (clips / name).write_bytes(b"RIFF")
    home = tmp_path / "home"
    (home / ".domovoi").mkdir(parents=True)
    played = tmp_path / "played.txt"
    aplay = tmp_path / "aplay"
    aplay.write_text(f'#!/bin/bash\necho "${{@: -1}}" >>"{_posix(played)}"\n', encoding="utf-8", newline="\n")
    aplay.chmod(0o755)

    def say(*args: str) -> list[str]:
        played.unlink(missing_ok=True)
        r = bash(f'bash "{_posix(STICK / "room" / "domovoi-status")}" "$@"', *args,
                 env={"DS_CLIPS": _posix(clips), "DS_APLAY": _posix(aplay), "HOME": _posix(home)})
        assert r.returncode == 0, r.stderr
        return [Path(x).name for x in played.read_text().split()] if played.exists() else []

    return say


@requires_bash
def test_the_code_is_said_digit_by_digit_then_not_again_for_two_minutes(speaker):
    assert speaker("say-code", "807662") == ["your_code_is.wav", "digit_8.wav", "digit_0.wav", "digit_7.wav",
                                             "digit_6.wav", "digit_6.wav", "digit_2.wav"]
    assert speaker("say-code", "807662") == [], "the satellite asks at every reconnect"
    assert speaker("say-code", "123456")[:2] == ["your_code_is.wav", "digit_1.wav"], "a new code is said at once"


@requires_bash
@pytest.mark.parametrize("args", [("say-code",), ("say-code", "12a456"), ("say-code", "$(reboot)"),
                                  ("awaiting-approval",), ("no-server",), ("say-code", "1" * 13)])
def test_nothing_else_is_said(speaker, args):
    assert speaker(*args) == []


# ─── the room's screen ───────────────────────────────────────────────────

def _console():
    spec = importlib.util.spec_from_file_location("stick_console", STICK / "room" / "console.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_code_is_drawn_in_block_digits_that_fit_the_screen():
    c = _console()
    rows = c.big("807662", 80)
    assert len(rows) == 7
    plain = [r.replace(c.ON, "").replace(c.OFF, "") for r in rows]
    assert max(len(r) for r in plain) <= 76
    assert c.big("807662", 200) != rows, "wider screens get bigger digits"
    assert c.big("", 80) == [] and c.big("abc", 80) == []


def test_the_screen_says_what_to_do_in_every_state():
    c = _console()
    info = {"room": "Kitchen", "core": "http://192.168.1.20:6370", "fp": "SHA256:" + "A" * 43}
    lines, done = c.screen_for({"connection": "awaiting_approval"}, "807662", info, 120)
    text = "\n".join(lines)
    assert not done and "Room: Kitchen" in text and "(the code is 807 662)" in text
    assert "Settings -> Rooms -> Waiting for approval" in text
    lines, done = c.screen_for({"connection": "connected"}, None, info, 120)
    assert done and "now the room \"Kitchen\"" in "\n".join(lines)
    for state, words in (("offline", "Can't reach"), ("unpinned", "did not prove"), ("refused", "turned this computer away")):
        lines, done = c.screen_for({"connection": state, "reason": "x"}, None, info, 120)
        assert not done and words in "\n".join(lines), state
    lines, _ = c.screen_for(None, None, info, 120)
    assert "Starting" in "\n".join(lines)


# ─── the core's reading of the satellite's report ────────────────────────
# (the satellite's half: satellite/tests/test_stick_boot_facts.py)

def test_the_core_keeps_only_the_boot_shape_it_knows():
    from domovoi import satellite_health

    s = satellite_health.validate_sample({"seq": 1, "rss_kb": 1, "boot": {
        "firmware": "uefi", "secure_boot": True, "ca_2023": False, "evil": "<script>", "loader_held": 1}})
    assert s["boot"] == {"firmware": "uefi", "secure_boot": True, "ca_2023": False}
    s = satellite_health.validate_sample({"seq": 1, "rss_kb": 1, "boot": {"firmware": "efi"}})
    assert s["boot"] is None


@requires_bash
def test_the_8_gb_floor_holds_even_against_a_solid_state_disk(tmp_path):
    trees = ('disk sda pci0000:00/0000:00:17.0/ata1/host0 8 1 0 0 HDD8 ATA ""\n'
             'disk sdc pci0000:00/0000:00:17.0/ata3/host2 7 0 0 0 SSD7 ATA ""\n')
    assert run_lib(tmp_path / "t", trees, "ds_pick '' sda sdc") == "sda"
    assert run_lib(tmp_path / "u", trees.splitlines()[1] + "\n", "ds_pick '' sdc") == ""


@requires_bash
def test_the_stick_is_never_picked_even_on_an_internal_port(tmp_path):
    """A stick that looks internal (an SD-to-SATA adapter, a USB stick a
    firmware presents as SATA): known as the stick by /cdrom alone."""
    tree = 'disk sde pci0000:00/0000:00:17.0/ata5/host4 640 0 0 0 Big ATA ""\n' + HDD
    assert run_lib(tmp_path / "t", tree, "ds_pick sde sda sde") == "sda"


@requires_bash
def test_a_thunderbolt_or_usb4_disk_is_never_picked(tmp_path):
    """No "usb" in its path: the PCI port it sits behind says removable."""
    tb = "pci0000:00/0000:00:07.0/0000:20:00.0/0000:21:01.0/0000:22:00.0/nvme/nvme1"
    tree = (HDD + f'disk nvme1n1 {tb} 2000 0 0 0 TBEnclosure "" ""\n'
            'echo removable >"$R/devices/pci0000:00/0000:00:07.0/0000:20:00.0/removable"\n'
            'echo fixed >"$R/devices/pci0000:00/0000:00:17.0/removable"\n')
    assert run_lib(tmp_path / "t", tree, "ds_pick '' sda nvme1n1") == "sda"
    assert run_lib(tmp_path / "u", tree, "ds_external nvme1n1 && echo ext; ds_external sda || echo own") == "ext\nown"


@requires_bash
def test_disks_joined_by_lvm_or_raid_are_named(tmp_path):
    setup = HDD + HDD.replace("sda", "sdd").replace("ata1", "ata2") + SSD
    stub = ('blkid() { for a; do d=$a; done; case ${d##*/} in sda2) echo LVM2_member ;; '
            'sdd) echo linux_raid_member ;; nvme0n1p1) echo ext4 ;; esac; }\n')
    assert run_lib(tmp_path / "t", setup, stub + "ds_joined sda sdd nvme0n1").split() == ["sda", "sdd"]
    assert run_lib(tmp_path / "o", setup, "ds_others sdb /dev/sda /dev/sdb /dev/sde").split() == ["sda", "sde"]
    assert run_lib(tmp_path / "u", setup, stub + "ds_internal sdb sda sdb sdd nvme0n1").split() == [
        "sda", "sdd", "nvme0n1"]
