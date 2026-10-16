"""Drive a QEMU test PC for the room stick (Windows host, WHPX or TCG).

Room stick kit (domovoi/usb_stick/, 2026-10-16; from the wf/usb-stick-spike
prototype). QEMU is a test tool here, never shipped, run from the kit's own
folder ($USB_STICK_KIT_DIR/tools/qemu, unpacked, never installed).

One run = one QEMU process: a "PC" with a SATA disk, an
Intel e1000e NIC on QEMU's user network (the host is 10.0.2.2), an HDA
sound card with a microphone (silent), and optionally the room stick
plugged in as a USB mass-storage device (the hybrid image as a raw disk,
exactly as on a real stick, not as a CD).

Firmware modes:
  bios      SeaBIOS (QEMU's own)
  uefi      Debian's OVMF, no Secure Boot
  uefi-sb   OVMF with Secure Boot enforced and Microsoft's 2011 keys
            (Debian's OVMF_VARS_4M.ms.fd). Under WHPX the code file is
            Debian's source built without the SMM requirement
            (build-ovmf-sb-nosmm.sh: WHPX has no SMM); under --accel tcg it
            is Debian's own OVMF_CODE_4M.secboot.fd with smm=on.
  uefi-sb2023  the same, but the firmware trusts only Microsoft's 2023 CAs
            (made by make-vars.sh): a PC whose 2011 CA was revoked.
  uefi-sbboth  both the 2011 and 2023 CAs: a PC that got the 2023 update.
  uefi-sb-no3p Windows' own CAs only, no third-party UEFI CA: a
            "Secured-core" PC with "Allow Microsoft 3rd party UEFI CA" off.
  uefi32    32-bit UEFI (Debian's ovmf-ia32): Bay Trail-era tablets and
            netbooks with a 64-bit CPU and 32-bit firmware.

While it runs: every few seconds a screenshot (PNG) through QMP; the serial
port goes to a log (test builds of the stick write markers and the
installer's/first boot's logs there). Markers drive the run:
  --type-on MARKER=TEXT   type TEXT (then Enter) when MARKER shows on serial
  --until MARKER          stop the run (power off) when MARKER shows
The process runs at Idle priority on the E-cores (logical CPUs 16-31).

  python qemu_drive.py disk NAME [--size 20G]
  python qemu_drive.py run NAME --mode uefi [--stick ISO] [--install] ...
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

if not os.environ.get("USB_STICK_KIT_DIR"):
    raise SystemExit("set USB_STICK_KIT_DIR to the kit's folder (tools/qemu, tools/ovmf, vm/, shots/, logs/)")
BASE = Path(os.environ["USB_STICK_KIT_DIR"])
QEMU = BASE / "tools" / "qemu" / "qemu-system-x86_64.exe"
QEMU_IMG = BASE / "tools" / "qemu" / "qemu-img.exe"
OVMF = BASE / "tools" / "ovmf"
VM = BASE / "vm"
SHOTS = BASE / "shots"
LOGS = BASE / "logs"
E_CORES = 0xFFFF0000  # logical CPUs 16-31
IDLE_PRIORITY_CLASS = 0x00000040

FIRMWARE = {
    # mode: (code file, vars template or None)
    "uefi": ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"),
    "uefi-sb": ("OVMF_CODE_4M.secboot-nosmm.fd", "OVMF_VARS_4M.ms.fd"),
    "uefi-sb-tcg": ("OVMF_CODE_4M.secboot.fd", "OVMF_VARS_4M.ms.fd"),
    "uefi-sb2023": ("OVMF_CODE_4M.secboot-nosmm.fd", "OVMF_VARS_4M.ms2023.fd"),
    "uefi-sbboth": ("OVMF_CODE_4M.secboot-nosmm.fd", "OVMF_VARS_4M.msboth.fd"),
    "uefi-sb-no3p": ("OVMF_CODE_4M.secboot-nosmm.fd", "OVMF_VARS_4M.no3p.fd"),
    "uefi32": ("OVMF32_CODE_4M.fd", "OVMF32_VARS_4M.fd"),
}


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class QMP:
    def __init__(self, port: int) -> None:
        deadline = time.time() + 30
        while True:
            try:
                # 60 s: with several VMs starting at once on a busy box
                # QEMU's greeting took longer than 10 s (run2, 2026-10-16).
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=60)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)
        self.f = self.sock.makefile("rwb")
        self._read()  # greeting
        self.cmd("qmp_capabilities")

    def _read(self) -> dict:
        while True:
            line = self.f.readline()
            if not line:
                raise ConnectionError("QMP closed")
            msg = json.loads(line)
            if "event" in msg:
                continue
            return msg

    def cmd(self, execute: str, **args) -> dict:
        self.f.write((json.dumps({"execute": execute, "arguments": args} if args else {"execute": execute}) + "\n").encode())
        self.f.flush()
        return self._read()

    def keys(self, text: str) -> None:
        names = {" ": "spc", "\n": "ret", "-": "minus", ".": "dot", "/": "slash", ":": "shift-semicolon"}
        for ch in text:
            if ch in names:
                k = names[ch]
            elif ch.isupper():
                k = "shift-" + ch.lower()
            else:
                k = ch
            if k.startswith("shift-"):
                keys = [{"type": "qcode", "data": "shift"}, {"type": "qcode", "data": k.split("-", 1)[1]}]
            else:
                keys = [{"type": "qcode", "data": k}]
            self.cmd("send-key", keys=keys)
            time.sleep(0.08)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def idle_on_e_cores(pid: int) -> None:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x0200 | 0x0400, False, pid)  # SET_INFORMATION | QUERY_INFORMATION
    if h:
        k32.SetPriorityClass(h, IDLE_PRIORITY_CLASS)
        k32.SetProcessAffinityMask(h, ctypes.c_size_t(E_CORES))
        k32.CloseHandle(h)


def cmd_disk(a) -> int:
    VM.mkdir(parents=True, exist_ok=True)
    path = VM / f"{a.name}.qcow2"
    if path.exists():
        path.unlink()
    subprocess.run([str(QEMU_IMG), "create", "-q", "-f", "qcow2", str(path), a.size], check=True)
    for old in VM.glob(f"{a.name}.*.vars.fd"):
        old.unlink()
    say(f"disk {path} ({a.size})")
    return 0


def qemu_args(a, qmp_port: int, serial: Path) -> list[str]:
    accel = a.accel
    machine = "q35"
    if a.mode == "uefi-sb-tcg":
        accel = "tcg"
        machine += ",smm=on"
    if accel == "whpx":
        machine += ",accel=whpx,kernel-irqchip=off"
    else:
        machine += ",accel=tcg"
    args = [str(QEMU), "-name", a.name, "-machine", machine, "-m", str(a.mem), "-smp", str(a.cpus)]
    if a.cpu:
        # QEMU's default (qemu64) is an x86-64-v1 CPU: no SSSE3/SSE4.x/POPCNT,
        # older than NumPy 2.4's wheels accept. "Nehalem" (2008, the first
        # Core i7) is the oldest named model that has x86-64-v2.
        args += ["-cpu", a.cpu]
    if accel == "tcg":
        args += ["-accel", "tcg,thread=multi"]
    if a.mode in FIRMWARE:
        code, tmpl = FIRMWARE[a.mode]
        vars_ = VM / f"{a.name}.{a.mode}.vars.fd"
        if not vars_.exists():
            shutil.copyfile(OVMF / tmpl, vars_)
        if a.mode == "uefi-sb-tcg":
            args += ["-global", "driver=cfi.pflash01,property=secure,value=on"]
        args += ["-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={OVMF / code}",
                 "-drive", f"if=pflash,format=raw,unit=1,file={vars_}"]
    args += [
        "-drive", f"file={VM / (a.name + '.qcow2')},if=none,id=hd0,format=qcow2,cache=unsafe",
        "-device", "ide-hd,drive=hd0,bus=ide.0,bootindex=2",
        "-device", "qemu-xhci,id=xhci",
        "-netdev", "user,id=n0" + (",restrict=on" if a.net_restrict else "")
        + (f",bootfile={a.bootfile}" if a.bootfile else ""),
        "-device", f"e1000e,netdev=n0,mac={a.mac}",
        "-audiodev", "none,id=a0",
        "-device", "intel-hda", "-device", "hda-micro,audiodev=a0",
        "-vga", "std", "-display", "none",
        "-qmp", f"tcp:127.0.0.1:{qmp_port},server=on,wait=off",
        "-serial", f"file:{serial}",
        "-device", "usb-kbd,bus=xhci.0",
    ]
    if a.stick:
        args += ["-drive", f"file={a.stick},if=none,id=stick,format=raw,readonly=on",
                 "-device", "usb-storage,bus=xhci.0,drive=stick,removable=on,bootindex=1"]
    if a.no_reboot:
        args += ["-no-reboot"]
    return args


def ppm_to_png(src: Path, dst: Path) -> None:
    """QEMU's PPM screendump to PNG (stdlib zlib), for builds without PNG."""
    import struct
    import zlib

    data = src.read_bytes()
    parts = data.split(maxsplit=4)
    w, h = int(parts[1]), int(parts[2])
    pix = data[len(data) - w * h * 3:]
    raw = b"".join(b"\x00" + pix[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    dst.write_bytes(png)


def cmd_run(a) -> int:
    for d in (VM, SHOTS, LOGS):
        d.mkdir(parents=True, exist_ok=True)
    run_id = f"{a.name}-{a.tag or a.mode}-{time.strftime('%Y%m%d-%H%M%S')}"
    shots = SHOTS / run_id
    shots.mkdir(parents=True, exist_ok=True)
    serial = LOGS / f"{run_id}.serial.log"
    qmp_port = free_port()
    args = qemu_args(a, qmp_port, serial)
    (LOGS / f"{run_id}.cmd.txt").write_text(subprocess.list2cmdline(args), encoding="utf-8")
    say(f"run {run_id}: {a.mode}/{a.accel} stick={a.stick or '-'}")
    errlog = open(LOGS / f"{run_id}.qemu.log", "wb")
    proc = subprocess.Popen(args, stdout=errlog, stderr=subprocess.STDOUT, creationflags=IDLE_PRIORITY_CLASS)
    idle_on_e_cores(proc.pid)
    # Only ever stopped by this PID (`qemu_drive.py stop NAME`): other
    # agents' Android emulators are qemu-system-x86_64.exe processes too.
    (VM / f"{a.name}.pid").write_text(str(proc.pid), encoding="ascii")
    t0 = time.time()
    events: list[dict] = []

    def event(what: str, **kw) -> None:
        e = {"t": round(time.time() - t0, 1), "what": what, **kw}
        events.append(e)
        say(f"+{e['t']:7.1f}s {what} {kw if kw else ''}")

    try:
        qmp = QMP(qmp_port)
    except OSError:
        # Never leave a QEMU behind (the spike once did, still writing to a
        # disk the next run opened too).
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                say(f"QEMU {proc.pid} did not stop; stop it with: qemu_drive.py stop {a.name}")
        print((LOGS / f"{run_id}.qemu.log").read_text(errors="replace"))
        return 1
    event("started", pid=proc.pid)
    type_on = [tuple(x.split("=", 1)) for x in (a.type_on or [])]
    done_markers: set[str] = set()
    last_shot = 0.0
    n = 0
    result = "timeout"
    seen = 0
    while True:
        if proc.poll() is not None:
            # The last lines before a power-off (a refusal's STOPPED) land
            # after the previous read: read them before saying it ended.
            try:
                tail = serial.read_text(errors="replace")[seen:] if serial.exists() else ""
            except OSError:
                tail = ""
            for line in tail.splitlines():
                if "DOMOVOI-STICK:" in line:
                    event("marker", line=line.strip()[-400:])
            result = "exited"
            event("qemu exited", code=proc.returncode)
            break
        now = time.time()
        if now - t0 > a.timeout:
            event("timeout")
            break
        if now - last_shot >= a.shot_every:
            last_shot = now
            n += 1
            ppm = shots / "last.ppm"
            try:
                r = qmp.cmd("screendump", filename=str(ppm))
                if "error" not in r and ppm.exists():
                    ppm_to_png(ppm, shots / f"{n:04d}-{int(now - t0):05d}s.png")
                    ppm.unlink()
            except (OSError, ConnectionError, ValueError) as e:
                say(f"screendump failed: {e}")
        try:
            text = serial.read_text(errors="replace") if serial.exists() else ""
        except OSError:
            text = ""
        if len(text) > seen:
            new = text[seen:]
            seen = len(text)
            for line in new.splitlines():
                if "DOMOVOI-STICK:" in line:
                    event("marker", line=line.strip()[-400:])
        for marker, keys in type_on:
            if marker not in done_markers and marker in text:
                done_markers.add(marker)
                time.sleep(a.type_delay)
                try:
                    qmp.cmd("screendump", filename=str(shots / "before-type.ppm"))
                    ppm_to_png(shots / "before-type.ppm", shots / f"typed-{re.sub(r'[^A-Za-z0-9]+', '_', marker)}.png")
                    (shots / "before-type.ppm").unlink()
                except Exception:  # noqa: BLE001
                    pass
                qmp.keys(keys.replace("\\n", "\n") + "\n")
                event("typed", marker=marker, keys=keys)
        if a.until and any(u in text for u in a.until):
            hit = next(u for u in a.until if u in text)
            event("until", marker=hit)
            time.sleep(a.linger)
            try:
                qmp.cmd("screendump", filename=str(shots / "final.ppm"))
                ppm_to_png(shots / "final.ppm", shots / "final.png")
                (shots / "final.ppm").unlink()
            except Exception:  # noqa: BLE001
                pass
            result = "until"
            break
        time.sleep(1.0)
    if proc.poll() is None:
        try:
            qmp.cmd("quit")
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    summary = {"run": run_id, "mode": a.mode, "accel": a.accel, "result": result,
               "seconds": round(time.time() - t0, 1), "events": events,
               "shots": str(shots), "serial": str(serial)}
    (LOGS / f"{run_id}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if a.summary:
        Path(a.summary).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    say(f"done: {result} in {summary['seconds']} s -> {LOGS / (run_id + '.json')}")
    return 0 if result in ("until", "exited") else 1


def cmd_stop(a) -> int:
    pidfile = VM / f"{a.name}.pid"
    if not pidfile.exists():
        say("no pid file")
        return 1
    pid = int(pidfile.read_text())
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    if "qemu-system-x86_64" not in out:
        say(f"pid {pid} is not this run's QEMU any more")
        return 1
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    say(f"stopped {pid}")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("disk")
    d.add_argument("name")
    d.add_argument("--size", default="20G")
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--mode", choices=["bios", *FIRMWARE], default="uefi")
    r.add_argument("--accel", choices=["whpx", "tcg"], default="whpx")
    r.add_argument("--stick")
    r.add_argument("--tag")
    r.add_argument("--cpu", default=None, help="QEMU CPU model (default: QEMU's qemu64, x86-64-v1)")
    r.add_argument("--mem", type=int, default=2048)
    r.add_argument("--cpus", type=int, default=2)
    r.add_argument("--mac", default="52:54:00:12:34:56")
    r.add_argument("--timeout", type=float, default=3600)
    r.add_argument("--shot-every", type=float, default=15)
    r.add_argument("--type-on", action="append")
    r.add_argument("--type-delay", type=float, default=3.0)
    r.add_argument("--until", action="append")
    r.add_argument("--linger", type=float, default=5.0)
    r.add_argument("--summary", help="also write the run's summary (events, markers) here")
    r.add_argument("--no-reboot", action="store_true")
    r.add_argument("--bootfile", default=None,
                   help="the DHCP server's boot-file name (QEMU's user network): a rogue DHCP server's preseed URL")
    r.add_argument("--net-restrict", action="store_true",
                   help="no internet: QEMU's user network with restrict=on (DHCP still answers)")
    s = sub.add_parser("stop")
    s.add_argument("name")
    a = p.parse_args(argv)
    return {"disk": cmd_disk, "run": cmd_run, "stop": cmd_stop}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
