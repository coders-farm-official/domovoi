"""What goes on a room stick (domovoi/usb_stick/image.py, repack.py).

Old computers (USB stick), 2026-10-16. The pin of Debian's netinst, the
installer's answers and our initrd (a newc cpio the kernel unpacks after
Debian's own), the boot menus, stick.json, the tar repack.py puts into the
image, and the repack step itself with a stand-in for xorriso. No network,
no xorriso, no Debian image: the real repack runs in the QEMU kit
(functional-testing/usb-stick/, docs/SPARE_DEVICE_SATELLITES.md).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path

import pytest

from domovoi.usb_stick import image, repack
from satellite import enrolment

FP = "SHA256:" + base64.b64encode(hashlib.sha256(b"a test server").digest()).decode().rstrip("=")
CORE = "http://192.168.0.117:6370"
EPOCH = 1_760_000_000
BUNDLE = b"\x1f\x8b" + b"satellite bundle bytes" * 100


def payload(room: str = "Kid's Room") -> str:
    return enrolment.build(CORE, FP, room)


def wav(tag: bytes) -> bytes:
    body = b"\0" * 64 + tag
    return b"RIFF" + (36 + len(body)).to_bytes(4, "little") + b"WAVEfmt " + b"\0" * 24 + body


CLIPS = {name: wav(name.encode()) for name in image.CLIP_NAMES}


def parse_newc(data: bytes) -> dict[str, tuple[int, int, int, int, bytes]]:
    """{name: (mode, uid, gid, mtime, data)} of a newc cpio archive."""
    out, i = {}, 0
    while True:
        assert data[i:i + 6] == b"070701", f"bad magic at {i}"
        f = [int(data[i + 6 + 8 * k:i + 14 + 8 * k], 16) for k in range(13)]
        size, namesize = f[6], f[11]
        name = data[i + 110:i + 110 + namesize - 1].decode("utf-8")
        i += 110 + namesize
        i += -i % 4
        body = data[i:i + size]
        i += size
        i += -i % 4
        if name == "TRAILER!!!":
            assert i == len(data)
            return out
        out[name] = (f[1], f[2], f[3], f[5], body)


def tar_members(data: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        return {m.name: m for m in t.getmembers()}


def tar_file(data: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        return t.extractfile(name).read()


# ─── the pin ──────────────────────────────────────────────────────────────

def test_the_committed_pin_is_debian_13_7_netinst_on_debian_s_host():
    pin = image.load_pin()
    assert pin["name"] == "debian-13.7.0-amd64-netinst.iso" and pin["version"] == "13.7.0"
    assert pin["size"] == 792_723_456
    assert pin["sha256"] == "a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355"
    assert all(u.startswith("https://cdimage.debian.org/") for u in pin["urls"])
    assert "Microsoft UEFI CA 2023" in pin["shim_signatures"], "a 2011-only shim would not start on new PCs"


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(pin_version=2),
    lambda d: d.update(name="debian-13.7.0-amd64-DVD-1.iso"),
    lambda d: d.update(version="13.8.0"),
    lambda d: d.update(urls=[]),
    lambda d: d.update(urls=["https://example.com/debian-13.7.0-amd64-netinst.iso"]),
    lambda d: d.update(urls=["http://cdimage.debian.org/debian-cd/13.7.0/amd64/iso-cd/debian-13.7.0-amd64-netinst.iso"]),
    lambda d: d.update(urls=["https://cdimage.debian.org/debian-cd/13.7.0/amd64/iso-cd/other.iso"]),
    lambda d: d.update(urls=["https://cdimage.debian.org/debian-cd/../x/debian-13.7.0-amd64-netinst.iso"]),
    lambda d: d.update(size=12),
    lambda d: d.update(size=True),
    lambda d: d.update(sha256="AB" * 32),
], ids=["version", "name", "mismatch", "no-url", "host", "http", "file", "dotdot", "size", "bool", "sha"])
def test_a_malformed_pin_is_refused(tmp_path, mutate):
    doc = json.loads(image.PIN_PATH.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / "pin.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(image.PinError):
        image.load_pin(path)


def test_an_image_matches_only_at_the_pinned_size_and_digest(tmp_path):
    body = b"x" * 1000
    pin = {"size": 1000, "sha256": hashlib.sha256(body).hexdigest()}
    f = tmp_path / "d.iso"
    f.write_bytes(body)
    assert image.iso_matches(f, pin)
    f.write_bytes(b"y" * 1000)
    assert not image.iso_matches(f, pin), "rewritten: checked again"
    f.write_bytes(body + b"z")
    assert not image.iso_matches(f, pin)
    assert not image.iso_matches(tmp_path / "missing.iso", pin)


# ─── the installer's answers, the menus, the initrd ───────────────────────

def test_options_that_could_break_the_answers_file_are_refused():
    image.Options(timezone="America/Argentina/Buenos_Aires", locale="de_DE.UTF-8", keymap="de", country="DE").check()
    for bad in (image.Options(timezone="Europe/Paris\nd-i x"), image.Options(locale="en_US"),
                image.Options(keymap="us; rm"), image.Options(country="usa"), image.Options(timezone="../x")):
        with pytest.raises(image.StickError):
            bad.check()


def test_the_answers_are_filled_and_ask_for_nothing_but_erase():
    text = image.preseed(image.Options(timezone="America/Chicago"), "domovoi-kids-room").decode()
    assert not re.search(r"@[A-Z_]+@", text), "every value filled in"
    assert "d-i time/zone string America/Chicago" in text
    assert "d-i netcfg/get_hostname string domovoi-kids-room" in text
    assert "d-i partman/early_command string sh /domovoi-stick/erase-confirm.sh" in text
    assert "d-i preseed/late_command string sh /domovoi-stick/late.sh" in text
    assert "d-i passwd/make-user boolean false" in text
    assert "systemd-timesyncd" in text and "fwupd" not in text.split("pkgsel/include", 1)[1].split("\n")[0]
    assert "early_command string sh /domovoi-stick/debug-serial.sh" not in text
    debug = image.preseed(image.Options(debug=True), "h").decode()
    assert debug.endswith("d-i preseed/early_command string sh /domovoi-stick/debug-serial.sh\n")


@pytest.mark.parametrize("name, shown", [
    ("Kid's Room", "Kids Room"), ('Den "$(reboot)"', "Den (reboot)"), ("Küche", "Kuche"),
    ("A\\B {x}; menuentry", "AB x menuentry"), ("!!!", "new room"), ("x" * 60, "x" * 40),
])
def test_the_menus_carry_only_characters_a_menu_cannot_misread(name, shown):
    assert image.menu_room(name) == shown
    grub, isolinux = image.menus(name)
    assert f'Domovoi room "{shown}"' in grub.decode()
    assert f"menu title Domovoi room stick: {shown}" in isolinux.decode()
    # Debian's own kernel and initrd first, ours second, on both menus.
    assert "initrd   /install.amd/gtk/initrd.gz /domovoi/preseed.gz" in grub.decode()
    assert "initrd=/install.amd/gtk/initrd.gz,/domovoi/preseed.gz" in isolinux.decode()
    assert b"@" not in grub and b"@" not in isolinux


def test_the_initrd_holds_the_answers_and_our_two_hooks_owned_by_root():
    raw = image.initrd(room="Kid's Room", core=CORE, hostname="domovoi-kids-room",
                       options=image.Options(), epoch=EPOCH)
    assert raw[:2] == b"\x1f\x8b" and raw[4:8] == b"\0\0\0\0", "gzip with no time"
    files = parse_newc(gzip.decompress(raw))
    assert set(files) == {
        "preseed.cfg", "domovoi-stick", "domovoi-stick/room-name", "domovoi-stick/core-url",
        "domovoi-stick/wifi", "domovoi-stick/erase-confirm.sh", "domovoi-stick/late.sh",
        "domovoi-stick/lib.sh", "domovoi-stick/templates",
        "etc", "etc/modprobe.d", "etc/modprobe.d/domovoi-stick-no-wifi.conf",
    }
    assert list(files) == sorted(files), "parents before children"
    for name, (mode, uid, gid, mtime, _) in files.items():
        assert (uid, gid, mtime) == (0, 0, EPOCH), name
    assert files["domovoi-stick"][0] == 0o040755
    assert files["domovoi-stick/erase-confirm.sh"][0] == 0o100755
    assert files["domovoi-stick/templates"][0] == 0o100644
    assert files["domovoi-stick/room-name"][4] == "Kid's Room\n".encode()
    assert files["domovoi-stick/core-url"][4] == (CORE + "\n").encode()
    assert files["domovoi-stick/wifi"][4] == b"off\n"
    assert b"install cfg80211 /bin/false" in files["etc/modprobe.d/domovoi-stick-no-wifi.conf"][4]
    assert all(b"\r" not in v[4] for v in files.values())


def test_wifi_on_leaves_the_drivers_alone_and_debug_adds_the_serial_copier():
    files = parse_newc(gzip.decompress(image.initrd(room="Den", core=CORE, hostname="h",
                                                    options=image.Options(wifi=True, debug=True), epoch=EPOCH)))
    assert "etc/modprobe.d/domovoi-stick-no-wifi.conf" not in files and "etc" not in files
    assert files["domovoi-stick/wifi"][4] == b"on\n"
    assert files["domovoi-stick/debug-serial.sh"][0] == 0o100755


def test_the_same_inputs_give_the_same_initrd():
    a = image.initrd(room="Den", core=CORE, hostname="h", options=image.Options(), epoch=EPOCH)
    b = image.initrd(room="Den", core=CORE, hostname="h", options=image.Options(), epoch=EPOCH)
    c = image.initrd(room="Den", core=CORE, hostname="h", options=image.Options(), epoch=EPOCH + 1)
    assert a == b and a != c


# ─── the files a stick adds ───────────────────────────────────────────────

def test_the_stick_s_files_are_the_room_code_the_bundle_and_our_hooks():
    tar, info = image.stick_files(pin=image.load_pin(), payload=payload(), bundle=BUNDLE, epoch=EPOCH,
                                  options=image.Options(timezone="America/Chicago"), clips=CLIPS)
    m = tar_members(tar)
    assert {n.split("/")[0] for n in m} == {"boot", "domovoi", "isolinux"}, "only what repack maps"
    for n in ("domovoi/stick.json", "domovoi/satellite.tar.gz", "domovoi/preseed.gz",
              "domovoi/room/firstboot.sh", "domovoi/room/console.py", "domovoi/room/boot-check.sh",
              "domovoi/room/domovoi-status", "domovoi/room/52domovoi-stick",
              "domovoi/room/units/domovoi-stick-firstboot.service",
              "domovoi/room/units/domovoi-stick-console.service",
              "domovoi/room/units/domovoi-stick-boot-check.service",
              "domovoi/sounds/your_code_is.wav", "domovoi/sounds/digit_7.wav",
              "boot/grub/grub.cfg", "isolinux/isolinux.cfg"):
        assert n in m, n
    assert "domovoi/test-only" not in m, "no test hooks on a household's stick"
    for mem in m.values():
        assert (mem.uid, mem.gid, mem.mtime) == (0, 0, EPOCH) and (mem.isfile() or mem.isdir())
    assert m["domovoi/room/firstboot.sh"].mode == 0o755 and m["domovoi/room/console.py"].mode == 0o644
    assert tar_file(tar, "domovoi/satellite.tar.gz") == BUNDLE
    stick = json.loads(tar_file(tar, "domovoi/stick.json"))
    assert stick == {
        "version": 1, "room": "Kid's Room", "room_id": "kids_room", "core": CORE, "fp": FP,
        "payload": payload(), "bundle_sha256": hashlib.sha256(BUNDLE).hexdigest(),
        "debian": "13.7.0", "wifi": False, "made": EPOCH,
    }
    assert info["room_id"] == "kids_room" and info["hostname"] == "domovoi-kids-room"
    assert info["spoken_code"] is True and info["bundle_size"] == len(BUNDLE)


def test_the_same_inputs_give_the_same_tar_and_a_new_time_a_new_one():
    args = dict(pin=image.load_pin(), payload=payload(), bundle=BUNDLE, options=image.Options(), clips=CLIPS)
    a, _ = image.stick_files(epoch=EPOCH, **args)
    b, _ = image.stick_files(epoch=EPOCH, **args)
    c, _ = image.stick_files(epoch=EPOCH + 60, **args)
    assert a == b and a != c


def test_the_code_is_said_only_with_every_clip_and_only_real_wavs():
    pin = image.load_pin()
    for clips in ({}, {k: v for k, v in CLIPS.items() if k != "digit_3.wav"},
                  dict(CLIPS, **{"digit_3.wav": b"not a wav at all, just some bytes" * 3})):
        tar, info = image.stick_files(pin=pin, payload=payload(), bundle=BUNDLE, epoch=EPOCH, clips=clips)
        assert info["spoken_code"] is False
        assert not [n for n in tar_members(tar) if n.startswith("domovoi/sounds")]
    tar, info = image.stick_files(pin=pin, payload=payload(), bundle=BUNDLE, epoch=EPOCH,
                                  clips=dict(CLIPS, **{"evil.sh": wav(b"x")}))
    assert "domovoi/sounds/evil.sh" not in tar_members(tar)


def test_a_debug_stick_carries_the_qemu_kit_s_markers():
    tar, _ = image.stick_files(pin=image.load_pin(), payload=payload(), bundle=BUNDLE, epoch=EPOCH,
                               options=image.Options(debug=True))
    assert "domovoi/test-only/domovoi-stick-debug.service" in tar_members(tar)


@pytest.mark.parametrize("bad, reason", [
    ("domovoi-room:1?core=http://8.8.8.8:6370&fp=" + FP + "&room=Den", "not_home_network"),
    ("domovoi-room:9?core=http://10.0.0.5:6370", None),
    ("not a code", None),
])
def test_a_code_the_room_could_not_use_is_refused(bad, reason):
    with pytest.raises(image.StickError) as e:
        image.stick_files(pin=image.load_pin(), payload=bad, bundle=BUNDLE, epoch=EPOCH)
    if reason:
        assert e.value.reason == reason


def test_no_bundle_or_no_time_is_refused():
    pin = image.load_pin()
    with pytest.raises(image.StickError):
        image.stick_files(pin=pin, payload=payload(), bundle=b"", epoch=EPOCH)
    with pytest.raises(image.StickError):
        image.stick_files(pin=pin, payload=payload(), bundle=BUNDLE, epoch=0)


def test_a_crlf_checkout_still_gives_lf_scripts(monkeypatch, tmp_path):
    """A Windows checkout with autocrlf: the scripts still reach the
    installer with LF line ends."""
    src = image.STICK_DIR
    fake = tmp_path / "stick"
    for f in src.rglob("*"):
        if f.is_file():
            dest = fake / f.relative_to(src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    monkeypatch.setattr(image, "STICK_DIR", fake)
    tar, _ = image.stick_files(pin=image.load_pin(), payload=payload(), bundle=BUNDLE, epoch=EPOCH)
    assert b"\r" not in tar_file(tar, "domovoi/room/firstboot.sh")
    files = parse_newc(gzip.decompress(tar_file(tar, "domovoi/preseed.gz")))
    assert b"\r" not in files["domovoi-stick/erase-confirm.sh"][4]


# ─── repack ───────────────────────────────────────────────────────────────

FAKE_XORRISO = r'''
import sys, os, json
args = sys.argv[1:]
log = os.environ.get("FAKE_XORRISO_LOG")
if log:
    open(log, "w").write(json.dumps({"args": args, "epoch": os.environ.get("SOURCE_DATE_EPOCH")}))
if os.environ.get("FAKE_XORRISO_FAIL"):
    print("xorriso : FAILURE : fake", file=sys.stderr); sys.exit(5)
src = args[args.index("-indev") + 1]; out = args[args.index("-outdev") + 1]
extra = b"" if os.environ.get("FAKE_XORRISO_SHORT") else b"OURS" * 10
open(out, "wb").write(open(src, "rb").read() + extra)
'''


@pytest.fixture
def fake_xorriso(tmp_path):
    script = tmp_path / "fake_xorriso.py"
    script.write_text(FAKE_XORRISO, encoding="utf-8")
    if sys.platform == "win32":
        launcher = tmp_path / "xorriso.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "xorriso"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return str(launcher)


def _files_tar(tmp_path: Path) -> Path:
    tar, _ = image.stick_files(pin=image.load_pin(), payload=payload(), bundle=BUNDLE, epoch=EPOCH)
    p = tmp_path / "files.tar"
    p.write_bytes(tar)
    return p


def test_repack_maps_our_three_paths_and_replays_debian_s_boot_records(tmp_path, fake_xorriso, monkeypatch):
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"DEBIAN" * 1000)
    out = tmp_path / "room.img"
    log = tmp_path / "args.json"
    monkeypatch.setenv("FAKE_XORRISO_LOG", str(log))
    repack.repack(iso, _files_tar(tmp_path), out, EPOCH, xorriso=fake_xorriso)
    assert out.read_bytes().startswith(b"DEBIAN") and out.stat().st_size > iso.stat().st_size
    seen = json.loads(log.read_text())
    args = seen["args"]
    assert seen["epoch"] == str(EPOCH), "SOURCE_DATE_EPOCH for reproducible bytes"
    assert args[-3:] == ["-boot_image", "any", "replay"]
    maps = [(args[i + 1], args[i + 2]) for i, a in enumerate(args) if a == "-map"]
    assert [t for _, t in maps] == ["/domovoi", "/boot/grub/grub.cfg", "/isolinux/isolinux.cfg"]
    assert all(Path(src).name in ("domovoi", "grub.cfg", "isolinux.cfg") for src, _ in maps)
    assert args[args.index("-chown_r") + 1] == "0" and args[args.index("-chgrp_r") + 1] == "0"
    assert not list(tmp_path.glob(".room.img.part")), "nothing half-written left"


@pytest.mark.parametrize("env, msg", [("FAKE_XORRISO_FAIL", "exit 5"), ("FAKE_XORRISO_SHORT", "no larger")])
def test_a_failed_repack_leaves_nothing(tmp_path, fake_xorriso, monkeypatch, env, msg):
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"DEBIAN" * 1000)
    out = tmp_path / "room.img"
    monkeypatch.setenv(env, "1")
    with pytest.raises(repack.RepackError) as e:
        repack.repack(iso, _files_tar(tmp_path), out, EPOCH, xorriso=fake_xorriso)
    assert msg in str(e.value)
    assert not out.exists() and not list(tmp_path.glob(".room.img*"))


def test_no_xorriso_is_said(tmp_path, monkeypatch):
    monkeypatch.setattr(repack, "find_xorriso", lambda: None)
    with pytest.raises(repack.RepackError, match="xorriso is not installed"):
        repack.repack(tmp_path / "a.iso", tmp_path / "f.tar", tmp_path / "o.img", EPOCH)


@pytest.mark.parametrize("member", ["etc/passwd", "../x", "domovoi/link"])
def test_unpack_takes_only_plain_files_under_our_three_names(tmp_path, member):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo(member)
        if member.endswith("link"):
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/shadow"
            t.addfile(info)
        else:
            info.size = 1
            t.addfile(info, io.BytesIO(b"x"))
    f = tmp_path / "bad.tar"
    f.write_bytes(buf.getvalue())
    with pytest.raises(repack.RepackError):
        repack.unpack(f, tmp_path / "out")
    assert not (tmp_path / "x").exists()
