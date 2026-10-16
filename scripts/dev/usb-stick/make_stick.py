"""Make a room stick image for the QEMU kit, with the product's own code.

Room stick kit (domovoi/usb_stick/, 2026-10-16). Two ways:

* ``--from offline``: no server. The room code carries a made-up
  fingerprint (a stick whose server can never prove itself: the room's
  "not ready yet" screen), the satellite bundle is built from this checkout
  the way the core builds it, ``image.stick_files`` makes our files, and
  ``repack_in_docker.py`` runs ``repack.py`` (xorriso) in the kit's
  container. ``--debug`` (the default) adds the serial markers.
* ``--from ft``: a throwaway stack (functional-testing/ft.py up
  --with-core, its core started with DOMOVOI_USB_STICK_REPACK pointing at
  repack_in_docker.py and DOMOVOI_USB_STICK_DEBUG=1): the dashboard's own
  route makes the stick (POST /api/satellites/usb-stick, as the admin, with
  the core address the test PC dials: QEMU's user network reaches the
  host's 127.0.0.1 as 10.0.2.2), the status is polled until the job ends,
  and the image is downloaded from the core's public route and checked
  against the SHA-256 in its Raspberry Pi Imager list.

  python make_stick.py --from offline --room Kitchen --out kitchen.img
  python make_stick.py --from ft --room Kitchen --out kitchen-ft.img --ft-dir <functional-testing>
  python make_stick.py api <functional-testing> POST /api/satellites/approvals/kitchen/approve '{"code": "807662"}'

Writes into $USB_STICK_KIT_DIR/out/.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
GUEST_CORE = "http://10.0.2.2:6394"


def kit_dir() -> Path:
    if not os.environ.get("USB_STICK_KIT_DIR"):
        raise SystemExit("set USB_STICK_KIT_DIR to the kit's folder")
    return Path(os.environ["USB_STICK_KIT_DIR"])


def _allowed(p: Path) -> bool:
    """domovoi/main.py _is_allowed_satellite_code, as the kit can't start the core."""
    allow = {".py", ".toml", ".txt", ".md", ".service", ".sh", ".ps1", ".json", ".example"}
    n = p.name
    return (p.suffix in allow and "__pycache__" not in p.parts and bool(n)
            and not n.endswith((".bak", ".pyc")) and not n.startswith(".env"))


def offline(a) -> int:
    sys.path.insert(0, str(REPO))
    from domovoi import satellite_bundle
    from domovoi.usb_stick import image
    from satellite import enrolment

    if a.mutant == "poweroff-returns":
        # A power-off that returns (firmware that ignores ACPI S5): the rest
        # of stop() runs as written. The matrix's poweroff-returns case.
        orig = image._text

        def patched(rel: str) -> bytes:
            data = orig(rel)
            if rel == "installer/erase-confirm.sh":
                assert b"\tpoweroff -f\n" in data
                data = data.replace(b"\tpoweroff -f\n", b"\tmark POWEROFF-MUTANT; : poweroff -f\n")
            return data

        image._text = patched
    out = kit_dir() / "out" / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    fp = a.fp or "SHA256:" + base64.b64encode(hashlib.sha256(b"no such server").digest()).decode().rstrip("=")
    payload = enrolment.build(a.core, fp, a.room)
    bundle = satellite_bundle.current(REPO / "satellite", _allowed).data
    epoch = a.epoch or int(time.time())
    files, info = image.stick_files(pin=image.load_pin(), payload=payload, bundle=bundle, epoch=epoch,
                                    options=image.Options(timezone=a.timezone, wifi=a.wifi, debug=a.debug))
    iso = kit_dir() / "dl" / "debian-13.7.0" / "debian-13.7.0-amd64-netinst.iso"
    with tempfile.TemporaryDirectory() as tmp:
        tar = Path(tmp) / "files.tar"
        tar.write_bytes(files)
        t0 = time.time()
        r = subprocess.run([sys.executable, str(HERE / "repack_in_docker.py"), "--iso", str(iso),
                            "--files", str(tar), "--out", str(out), "--epoch", str(epoch)])
    print(json.dumps({"out": str(out), "payload": payload, "info": info, "seconds": round(time.time() - t0, 1),
                      "sha256": hashlib.sha256(out.read_bytes()).hexdigest() if out.exists() else None},
                     indent=2))
    return r.returncode


def _api(ft: Path, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    token = json.loads((ft / "runs" / "current.json").read_text(encoding="utf-8"))["admin_token"]
    req = urllib.request.Request("http://127.0.0.1:6390" + path, method=method,
                                 data=None if body is None else json.dumps(body).encode())
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-Requested-With", "fetch")  # the dashboard's CSRF header
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode("utf-8", "replace")


def from_ft(a) -> int:
    ft = Path(a.ft_dir)
    out = kit_dir() / "out" / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    st, doc = _api(ft, "POST", "/api/satellites/usb-stick", {"room": a.room, "core": a.core})
    print("prepare:", st, json.dumps({k: doc.get(k) for k in ("started", "room_id", "conflict", "refusal")}
                                    if isinstance(doc, dict) else doc))
    if st != 200:
        return 1
    while True:
        time.sleep(3)
        st, doc = _api(ft, "GET", "/api/satellites/usb-stick")
        job = doc.get("job") or {}
        print(f"  +{time.time() - t0:5.0f}s {job.get('stage')} {job.get('bytes_done')}/{job.get('bytes_todo')}",
              flush=True)
        if not job.get("running"):
            break
    print("job:", json.dumps(job))
    if not job.get("ok"):
        return 1
    b = next(x for x in doc["builds"] if x["id"] == job["build"])
    local = "http://127.0.0.1:6394/v1/usb-stick/" + b["id"] + "/"
    with urllib.request.urlopen(local + "os-list.json", timeout=30) as r:
        lst = json.load(r)
    entry = lst["os_list"][0]
    h = hashlib.sha256()
    with urllib.request.urlopen(local + "domovoi-room-stick.img", timeout=600) as r, open(out, "wb") as f:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    ok = h.hexdigest() == entry["extract_sha256"] == b["sha256"] and out.stat().st_size == entry["extract_size"]
    print(json.dumps({"out": str(out), "build": b, "imager_entry": entry, "sha256_matches": ok,
                      "seconds": round(time.time() - t0, 1)}, indent=2))
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if argv[:1] == ["api"]:  # make_stick.py api FT_DIR METHOD PATH [JSON]: the ft dashboard as its admin
        st, doc = _api(Path(argv[1]), argv[2], argv[3], json.loads(argv[4]) if len(argv) > 4 else None)
        print(st)
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0 if st < 400 else 1
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="src", choices=["ft", "offline"], required=True)
    p.add_argument("--room", default="Kitchen")
    p.add_argument("--out", required=True, help="file name under $USB_STICK_KIT_DIR/out/")
    p.add_argument("--core", default=GUEST_CORE, help="the core address the test PC dials")
    p.add_argument("--fp", default=None, help="offline: the fingerprint in the room code (default: made up)")
    p.add_argument("--timezone", default="America/Chicago")
    p.add_argument("--wifi", action="store_true", help="offline: a stick with Wi-Fi on")
    p.add_argument("--no-debug", dest="debug", action="store_false", help="offline: no serial markers")
    p.add_argument("--mutant", choices=["poweroff-returns"], default=None,
                   help="offline: a stick whose power-off returns (a test of the ERASE gate failing closed)")
    p.add_argument("--epoch", type=int, default=None)
    p.add_argument("--ft-dir", default=None, help="ft: the functional-testing folder (runs/current.json)")
    a = p.parse_args(argv)
    return offline(a) if a.src == "offline" else from_ft(a)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
