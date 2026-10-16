"""The room stick's QEMU matrix (Windows host, WHPX), for a stick made with
make_stick.py --debug (the serial markers).

Room stick kit (domovoi/usb_stick/, 2026-10-16). Each case is one or two
QEMU runs (qemu_drive.py); the result is a table and
$USB_STICK_KIT_DIR/logs/matrix-<time>.json.

  install-<firmware>  the stick as a USB disk on an empty 20 GB disk: the
                      ERASE page, ERASE typed, the install to its restart;
                      then the disk alone: it boots, the per-boot Secure
                      Boot check, the held boot loader (UEFI), first boot
                      starts (an offline stick ends at "can't reach the
                      server", exit 3). Firmware: bios, uefi, uefi-sb
                      (Microsoft's 2011 keys), uefi-sb2023 (2023 keys only).
  refuse-erase        three answers that are not ERASE: "Nothing was
                      changed", powered off, the disk never written.
  refuse-network      no internet (QEMU's restricted network): the cable
                      page, nothing written; the Wi-Fi switch's marker.
  refuse-cpu          QEMU's default CPU (x86-64-v1, Core 2-class): the
                      processor page, nothing written.
  again               the stick started again on install-uefi's disk: the
                      "already the room" page, three wrong answers, the
                      disk unchanged.
  wifi                a stick made with --wifi on a restricted network:
                      Wi-Fi drivers load (cfg80211 rc 0), the Wi-Fi page.

  python matrix.py --stick kitchen.img [--wifi-stick kids-wifi.img] [--cases a,b] [--parallel 2]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if not os.environ.get("USB_STICK_KIT_DIR"):
    raise SystemExit("set USB_STICK_KIT_DIR to the kit's folder")
BASE = Path(os.environ["USB_STICK_KIT_DIR"])
QEMU_IMG = BASE / "tools" / "qemu" / "qemu-img.exe"
CPU = "Nehalem"  # the oldest named QEMU model with x86-64-v2 (2008)


def drive(*args: str) -> dict:
    out = BASE / "logs" / f"summary-{os.getpid()}-{time.time_ns()}.json"
    subprocess.run([sys.executable, str(HERE / "qemu_drive.py"), *args, "--summary", str(out)])
    try:
        return json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"result": "no summary", "events": []}


def markers(summary: dict) -> list[str]:
    return [e["line"].split("DOMOVOI-STICK: ", 1)[-1] for e in summary.get("events", []) if e.get("what") == "marker"]


def has(summary: dict, text: str) -> bool:
    return any(text in m for m in markers(summary))


def new_disk(name: str) -> None:
    subprocess.run([sys.executable, str(HERE / "qemu_drive.py"), "disk", name], check=True)


def written(name: str) -> bool:
    """Whether a guest wrote anything to this disk (any allocated data)."""
    r = subprocess.run([str(QEMU_IMG), "map", "--output=json", str(BASE / "vm" / f"{name}.qcow2")],
                       capture_output=True, text=True, check=True)
    return any(e.get("data") for e in json.loads(r.stdout))


def disk_digest(name: str) -> str:
    return hashlib.sha256((BASE / "vm" / f"{name}.qcow2").read_bytes()).hexdigest()


def install(firmware: str, stick: str) -> dict:
    name = f"m-{firmware}"
    new_disk(name)
    t0 = time.time()
    inst = drive("run", name, "--mode", firmware, "--stick", stick, "--cpu", CPU, "--tag", "install",
                 "--type-on", "ERASE-PROMPT=ERASE", "--no-reboot", "--timeout", "1800", "--shot-every", "30")
    t_install = round(time.time() - t0)
    ok_install = inst["result"] == "exited" and has(inst, "ERASE-CONFIRMED") and has(inst, "LATE-DONE")
    boot = drive("run", name, "--mode", firmware, "--cpu", CPU, "--tag", "boot",
                 "--until", "FIRSTBOOT-RC", "--until", "FIRSTBOOT-DONE", "--timeout", "900", "--shot-every", "20")
    boot_json = next((m.split("BOOT-JSON ", 1)[1] for m in markers(boot) if m.startswith("BOOT-JSON ")), None)
    held = next((m.split("HELD", 1)[1].strip() for m in markers(boot)
                 if "HELD" in m and "BOOT-JSON" not in m and "BOOTLOADER" not in m), None)
    ok_boot = boot["result"] == "until" and boot_json is not None
    facts = json.loads(boot_json) if boot_json else {}
    if firmware.startswith("uefi"):
        ok_boot = ok_boot and facts.get("firmware") == "uefi" and bool(held) and "shim-signed" in held
    if firmware.startswith("uefi-sb"):
        ok_boot = ok_boot and facts.get("secure_boot") is True
    return {"case": f"install-{firmware}", "ok": ok_install and ok_boot, "install_s": t_install,
            "boot": boot.get("result"), "boot_s": boot.get("seconds"), "facts": facts, "held": held,
            "markers": markers(inst)[-6:] + markers(boot)[-4:]}


def refuse(case: str, stick: str, *extra: str, expect: str, mode: str = "uefi") -> dict:
    name = f"m-{case}"
    new_disk(name)
    # The refusal page waits for Continue (the person reads it): pressed
    # when its marker shows.
    s = drive("run", name, "--mode", mode, "--stick", stick, "--tag", case, "--until", "STOPPED",
              "--type-on", "REFUSAL=", "--timeout", "900", "--shot-every", "20", *extra)
    # The refusal powers the PC off: QEMU exits, or the STOPPED marker is
    # seen first.
    ok = s["result"] in ("until", "exited") and has(s, f"STOPPED {expect}") and not written(name)
    return {"case": case, "ok": ok, "seconds": s.get("seconds"), "disk_written": written(name),
            "markers": markers(s)}


def again(stick: str) -> dict:
    name = "m-uefi"
    if not (BASE / "vm" / f"{name}.qcow2").exists():
        return {"case": "again", "ok": False, "why": "needs install-uefi's disk"}
    before = disk_digest(name)
    s = drive("run", name, "--mode", "uefi", "--stick", stick, "--cpu", CPU, "--tag", "again",
              "--type-on", "ERASE-PROMPT=NOPE", "--type-on", "NOT-ERASE 1=NOPE", "--type-on", "NOT-ERASE 2=NOPE",
              "--type-on", "REFUSAL=", "--until", "STOPPED", "--timeout", "900", "--shot-every", "20")
    after = disk_digest(name)
    ok = s["result"] in ("until", "exited") and has(s, "ALREADY-A-ROOM") and has(s, "STOPPED domovoi-stick/stopped")
    return {"case": "again", "ok": ok and before == after, "disk_unchanged": before == after,
            "seconds": s.get("seconds"), "markers": markers(s)}


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--stick", required=True, help="a debug stick under $USB_STICK_KIT_DIR/out/")
    p.add_argument("--wifi-stick", help="a debug stick made with --wifi")
    p.add_argument("--cases", default="install-bios,install-uefi,install-uefi-sb,install-uefi-sb2023,"
                                       "refuse-erase,refuse-network,refuse-cpu,wifi,again")
    p.add_argument("--parallel", type=int, default=2)
    a = p.parse_args(argv)
    stick = str(BASE / "out" / a.stick)
    wifi = str(BASE / "out" / a.wifi_stick) if a.wifi_stick else None
    jobs = {
        "install-bios": lambda: install("bios", stick),
        "install-uefi": lambda: install("uefi", stick),
        "install-uefi-sb": lambda: install("uefi-sb", stick),
        "install-uefi-sb2023": lambda: install("uefi-sb2023", stick),
        "refuse-erase": lambda: refuse("refuse-erase", stick, "--cpu", CPU, "--type-on", "ERASE-PROMPT=NOPE",
                                       "--type-on", "NOT-ERASE 1=nope", "--type-on", "NOT-ERASE 2=ERASED",
                                       expect="domovoi-stick/stopped"),
        "refuse-network": lambda: refuse("refuse-network", stick, "--cpu", CPU, "--net-restrict",
                                         expect="domovoi-stick/no-network"),
        "refuse-cpu": lambda: refuse("refuse-cpu", stick, expect="domovoi-stick/cpu-too-old"),
        "wifi": lambda: refuse("wifi", wifi, "--cpu", CPU, "--net-restrict", expect="domovoi-stick/no-network-wifi"),
    }
    wanted = [c for c in a.cases.split(",") if c]
    results = []
    first = [c for c in wanted if c != "again"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.parallel) as pool:
        for r in pool.map(lambda c: jobs[c](), first):
            results.append(r)
            print(json.dumps(r), flush=True)
    if "again" in wanted:
        r = again(stick)
        results.append(r)
        print(json.dumps(r), flush=True)
    out = BASE / "logs" / f"matrix-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print()
    for r in results:
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['case']}")
    print(f"-> {out}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
