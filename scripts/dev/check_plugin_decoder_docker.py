"""Check a plugin's decode-only container against the real Docker.

The unit tests (domovoi/tests/test_plugin_isolated.py) fake Docker. This
builds the real image for a throwaway sample plugin through the core's own
code (domovoi/plugins_runtime/isolated.py), runs the real container with
the core's run flags, and checks from inside it that the wall holds:

* a round trip decodes and comes back re-serialized by the core;
* the decoder runs as nobody, can't write its root, has no network
  (no interface but loopback, a connect fails), sees no host files and no
  environment from the core;
* the image build runs on the classic builder with ``--memory 1g
  --memory-swap 1g`` and a low CPU weight (BuildKit ignores those flags; the
  core refuses to build when the classic builder is gone);
* images come back re-encoded in the container (slice 2): EXIF and GPS
  gone, scaled to 1024 a side, a decompression bomb and a truncated file
  refused;
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

import domovoi_isolated


def _facts():
    facts = {"uid": os.getuid(), "gid": os.getgid(), "env": dict(os.environ)}
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
    if meta.get("img"):
        return domovoi_isolated.Image(payload, meta["img"])
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
        debian = ["python3-pil"]
        """), encoding="utf-8")
    return root


# The only variables the container may have, and where each comes from:
# PATH from the Debian base image's config (ENV PATH), HOSTNAME set by
# Docker to the container's short ID, HOME set by Docker from the user's
# passwd entry (nobody: /nonexistent; "/" when it has none), and LC_CTYPE
# set by Python itself at start-up (PEP 538 locale coercion: the C locale
# is coerced to C.UTF-8, -I doesn't turn that off). Nothing comes from the
# core: run_args passes no -e/--env/--env-file, and the docker CLI's own
# environment (docker_env) is never forwarded into a container.
CONTAINER_ENV = {
    "PATH": lambda v: v == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOSTNAME": lambda v: len(v) == 12 and all(c in "0123456789abcdef" for c in v),
    "HOME": lambda v: v in ("/nonexistent", "/"),
    "LC_CTYPE": lambda v: v == "C.UTF-8",
}
_SECRET_WORDS = ("DATABASE", "DOMOVOI", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "KEY", "DOCKER", "PG")


def _env_problems(env: dict) -> list[str]:
    """Names only (never a value: a leaked value may be a secret)."""
    import os

    out = []
    for name, value in sorted(env.items()):
        if any(w in name.upper() for w in _SECRET_WORDS):
            out.append(f"{name} (a core-like name)")
        elif name not in CONTAINER_ENV:
            out.append(f"{name} (not a container-standard name)")
        elif not CONTAINER_ENV[name](str(value)):
            out.append(f"{name} (not its container-standard value)")
    # No value of this process's own environment (the core's stand-in)
    # turns up under any name, standard ones included.
    mine = {v for k, v in os.environ.items() if len(v) >= 8 and k not in ("PATH",)}
    out += [f"{name} (a value from the core's environment)" for name, value in env.items()
            if str(value) in mine and not (name == "LC_CTYPE" and value == "C.UTF-8")]
    return out


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
        bargs = iso.build_args(SLUG, "x", "0.0.1")
        expect(bargs[bargs.index("--memory") + 1] == iso.BUILD_MEMORY and "--memory-swap" in bargs
               and "--cpu-shares" in bargs, "the build flags carry the memory cap and a low CPU weight")
        expect(await iso.builder_caps_honoured(),
               "this Docker's classic builder takes --memory (else the core refuses to build)")
        # No database here: the container guard's record of the built image
        # is the core's job, so this run notes it in memory instead.
        from domovoi.container_guard import engine

        noted: list[str] = []

        async def note(slug: str, image_id: str) -> None:
            noted.append(image_id)

        engine.note_decoder_image = note
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
            env = f.get("env") or {}
            leaks = _env_problems(env)
            expect(not leaks, "no core environment in the container" + (f": {leaks}" if leaks else ""))
            for bad in (b"hang", b"crash", b"garbage", b"membomb"):
                r = await dec.decode(bad)
                expect(not r.ok, f"{bad.decode()} is refused ({r.reason})")
                dec._retry_at = 0.0  # skip the backoff wait
                r = await dec.decode(b"1")
                expect(r.ok and r.value == {"echo": 1, "house": None}, f"a good item works after {bad.decode()}")
            await _images(dec, expect)
            r = await dec.decode(b"forkbomb")
            expect((not r.ok) or (r.value or {}).get("forked", 999) < 32,
                   f"the pids cap holds ({r.value or r.reason})")
        finally:
            await dec.stop()
            await iso.forget(SLUG)
    return problems


async def _images(dec, expect) -> None:
    import io
    import struct
    import zlib

    from PIL import Image

    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (52.0, 31.0, 12.5)
    buf = io.BytesIO()
    Image.new("RGB", (2000, 1500), (200, 30, 40)).save(buf, "JPEG", exif=exif.tobytes())
    src = buf.getvalue()
    for fmt in ("png", "webp"):
        r = await dec.decode(src, {"img": fmt})
        expect(r.ok and r.media_type == f"image/{fmt}" and b"SecretCam" not in (r.image or b"")
               and (r.value or {}).get("image", {}).get("width") == 1024,
               f"a JPEG with EXIF/GPS comes back as clean {fmt} ({r.reason or r.value})")

    def chunk(t: bytes, b: bytes) -> bytes:
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)

    bomb = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" * 1_000_000, 9)) + chunk(b"IEND", b""))
    r = await dec.decode(bomb, {"img": "png"})
    expect(not r.ok and "too large" in r.reason, f"a decompression bomb is refused ({r.reason})")
    r = await dec.decode(src[: len(src) // 2], {"img": "png"})
    expect(not r.ok, f"a truncated JPEG is refused ({r.reason})")
    r = await dec.decode(b"1")
    expect(r.ok, "a good item works after the image refusals")


def main() -> int:
    problems = asyncio.run(_check())
    print("\nALL OK" if not problems else f"\n{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
