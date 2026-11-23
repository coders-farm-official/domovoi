"""Check a plugin's decode-only container against the real Docker.

The unit tests (domovoi/tests/test_plugin_isolated.py) fake Docker. This
builds the real image for a throwaway sample plugin through the core's own
code (domovoi/plugins_runtime/isolated.py), runs the real container with
the core's run flags, and checks from inside it that the wall holds:

* a round trip decodes and comes back re-serialized by the core;
* the decoder runs as nobody, can't write its root, has no network
  (no interface but loopback, a connect fails), sees no host files and no
  environment from the core;
* a hostile decoder (a hang, a crash, garbage on stdout, a memory bomb, a
  fork bomb) refuses the item, and the next good item works after the
  restart.

It needs Docker and the internet (the image installs Python from Debian).
It creates exactly one image, ``domovoi-plugin-dec-decprobe:latest``, and
containers named ``domovoi-plugin-dec-decprobe`` (each run with
``--memory 256m --memory-swap 256m --pids-limit 32``); it removes those
and nothing else. It never prunes. Run it from the repo root:

    python scripts/dev/check_plugin_decoder_docker.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from domovoi.plugins_runtime import isolated as iso  # noqa: E402
from domovoi.plugins_runtime.manifest import parse_manifest, validate_plugin_dir  # noqa: E402

SLUG = "decprobe"

DECODER = '''\
import json
import os
import socket


def _facts():
    facts = {"uid": os.getuid(), "gid": os.getgid(), "env": sorted(os.environ)}
    try:
        with open("/written", "w") as f:
            f.write("x")
        facts["root_writable"] = True
    except OSError:
        facts["root_writable"] = False
    try:
        with open("/tmp/ok", "w") as f:
            f.write("x")
        facts["tmp_writable"] = True
    except OSError:
        facts["tmp_writable"] = False
    try:
        facts["interfaces"] = sorted(n for _, n in socket.if_nameindex())
    except OSError:
        facts["interfaces"] = []
    try:
        s = socket.create_connection(("1.1.1.1", 443), timeout=3)
        s.close()
        facts["network"] = True
    except OSError:
        facts["network"] = False
    facts["host_files"] = [p for p in ("/etc/fh", "/var/lib/fh-listener", "/home", "/root/.domovoi")
                           if os.path.exists(p) and os.listdir(p)]
    return facts


def main(payload, meta):
    if payload == b"facts":
        return _facts()
    if payload == b"hang":
        while True:
            pass
    if payload == b"crash":
        os._exit(3)
    if payload == b"garbage":
        # A hostile decoder finds the runner's private reply pipe and
        # writes a forged frame into it.
        import stat
        for fd in range(3, 32):
            try:
                if stat.S_ISFIFO(os.fstat(fd).st_mode):
                    os.write(fd, b"\\x00\\x00\\x00\\x09garbage!!")
            except OSError:
                pass
        return 1
    if payload == b"membomb":
        x = bytearray(1024 * 1024 * 1024)
        return len(x)
    if payload == b"forkbomb":
        pids = []
        for _ in range(200):
            try:
                pid = os.fork()
            except OSError:
                break
            if pid == 0:
                import time
                time.sleep(5)
                os._exit(0)
            pids.append(pid)
        return {"forked": len(pids)}
    return {"echo": json.loads(payload), "house": meta.get("house_id")}
'''


def _plugin(parent: Path) -> Path:
    root = parent / SLUG
    pkg = root / f"domovoi_plugin_{SLUG}"
    (pkg / "link").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
    (pkg / "link" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "link" / "decode.py").write_text(DECODER, encoding="utf-8")
    (root / "domovoi-plugin.toml").write_text(textwrap.dedent(f"""
        [plugin]
        slug = "{SLUG}"
        name = "Decoder probe"
        version = "0.0.1"
        publisher = "tests"
        license = "MIT"
        description = "Real-Docker check of the decode-only container."
        domovoi_api = ">=1.0,<2.0"

        [entry_points]
        core = "domovoi_plugin_{SLUG}.core"

        [permissions]
        friend_houses = true
        warnings = ["A test plugin."]

        [isolated]
        decoder = "domovoi_plugin_{SLUG}.link.decode:main"
        """), encoding="utf-8")
    return root


async def _check() -> list[str]:
    problems: list[str] = []

    def expect(cond: bool, what: str) -> None:
        print(("ok    " if cond else "FAIL  ") + what)
        if not cond:
            problems.append(what)

    with tempfile.TemporaryDirectory() as tmp:
        root = _plugin(Path(tmp))
        manifest = parse_manifest((root / "domovoi-plugin.toml").read_text(encoding="utf-8"))
        errors = validate_plugin_dir(root, manifest)
        # The probe imports socket/os/json: stdlib, so it validates.
        expect(errors == [], f"the probe plugin validates {errors}")
        args = iso.run_args(SLUG, "sha256:x")
        expect("--memory" in args and "--pids-limit" in args, "the run flags carry --memory and --pids-limit")
        dec = iso.IsolatedDecoder(SLUG, install_dir=root, manifest=manifest, timeout=10.0)
        await dec.start()
        expect(dec.state == "ready", f"the image builds and the container starts ({dec.problem})")
        if dec.state != "ready":
            return problems
        try:
            r = await dec.decode(b'{"b": 1, "a": [2]}', {"house_id": "h1"})
            expect(r.ok and r.canonical == b'{"echo":{"a":[2],"b":1},"house":"h1"}', f"round trip {r}")
            r = await dec.decode(b"facts")
            f = r.value or {}
            expect(r.ok, f"facts {r.reason}")
            expect(f.get("uid") == 65534 and f.get("gid") == 65534, f"runs as nobody {f.get('uid')}")
            expect(f.get("root_writable") is False, "the root is read-only")
            expect(f.get("tmp_writable") is True, "the tmpfs is writable")
            expect(f.get("network") is False and f.get("interfaces") == ["lo"],
                   f"no network {f.get('interfaces')}")
            expect(f.get("host_files") == [], f"no host files {f.get('host_files')}")
            expect(set(f.get("env", [])) <= {"PATH", "HOSTNAME", "HOME", "LANG", "PWD", "SHLVL", "_"},
                   f"no core environment {f.get('env')}")
            for bad in (b"hang", b"crash", b"garbage", b"membomb"):
                r = await dec.decode(bad)
                expect(not r.ok, f"{bad.decode()} is refused ({r.reason})")
                dec._retry_at = 0.0  # skip the backoff wait
                r = await dec.decode(b"1")
                expect(r.ok and r.value == {"echo": 1, "house": None}, f"a good item works after {bad.decode()}")
            r = await dec.decode(b"forkbomb")
            expect((not r.ok) or (r.value or {}).get("forked", 999) < 32,
                   f"the pids cap holds ({r.value or r.reason})")
        finally:
            await dec.stop()
            await iso.forget(SLUG)
    return problems


def main() -> int:
    problems = asyncio.run(_check())
    print("\nALL OK" if not problems else f"\n{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
