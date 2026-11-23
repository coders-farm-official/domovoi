"""Check a plugin's decode-only containers against the real Docker.

The unit tests (domovoi/tests/test_plugin_isolated.py) fake Docker. This
builds the real image for a throwaway sample plugin through the core's own
code (domovoi/plugins_runtime/isolated.py), runs real containers with the
core's run flags, and checks from inside them that the wall holds:

* a round trip decodes, comes back re-serialized by the core and passes the
  plugin's schema; an answer outside the schema is refused;
* the decoder runs as the fixed unprivileged user, can't write its root and
  has no writable /tmp (no tmpfs), has no network (no interface but
  loopback, a connect fails), sees no host files, no environment from the
  core and no request naming the friend, and has the file and process caps;
* one container per friend: two friends get two containers (two slot
  names, two hostnames); idle ones are stopped;
* the image build runs on the classic builder with ``--memory 1g
  --memory-swap 1g`` and a low CPU weight (BuildKit ignores those flags; the
  core refuses to build when the classic builder is gone);
* images: the container answers raw pixels and the core writes the PNG (a
  JPEG with EXIF and GPS comes back as a PNG of only IHDR, IDAT and IEND,
  scaled to 768 a side); a decompression bomb, a truncated file and a GIF
  are refused; an animation gives its first frame;
* a hostile decoder (a hang, a crash, a forged frame, a byte written after
  its answer, a memory bomb, a fork bomb) is a fault that kills only that
  friend's container; the next good item works after the backoff, and
  repeated faults pause that friend while another friend carries on.

It needs Docker and the internet (the image installs Python and Pillow from
Debian). It creates exactly one image, ``domovoi-plugin-dec-decprobe:latest``,
and containers named ``domovoi-plugin-dec-decprobe-<n>``; each run is
capped (``--memory 128m --memory-swap 128m --pids-limit 16``) and the build
too (``--memory 1g``). It removes those and nothing else. It never prunes.
Run it from the repo root:

    python scripts/dev/check_plugin_decoder_docker.py
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from domovoi.plugins_runtime import isolated as iso  # noqa: E402
from domovoi.plugins_runtime.manifest import parse_manifest, validate_plugin_dir  # noqa: E402

SLUG = "decprobe"
SCHEMA = {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "array"},
                    {"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"}]}

DECODER = '''\
import json
import os
import resource
import socket
import threading
import time


def _facts(meta):
    facts = {"uid": os.getuid(), "gid": os.getgid(), "env": dict(os.environ), "meta": sorted(meta),
             "hostname": socket.gethostname()}
    for path, key in (("/written", "root_writable"), ("/tmp/written", "tmp_writable")):
        try:
            with open(path, "w") as f:
                f.write("x")
            facts[key] = True
        except OSError:
            facts[key] = False
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
    with open("/proc/mounts") as f:
        facts["tmpfs"] = [ln.split()[1] for ln in f if ln.split()[2] == "tmpfs"
                          and ln.split()[1] not in ("/dev", "/dev/shm", "/sys/fs/cgroup")]
    facts["nofile"] = list(resource.getrlimit(resource.RLIMIT_NOFILE))
    return facts


def _reply_fds():
    import stat
    out = []
    for fd in range(3, 64):
        try:
            if stat.S_ISFIFO(os.fstat(fd).st_mode):
                out.append(fd)
        except OSError:
            pass
    return out


def main(payload, meta):
    if payload == b"facts":
        return _facts(meta)
    if payload == b"hang":
        while True:
            pass
    if payload == b"crash":
        os._exit(3)
    if payload == b"garbage":
        # A hostile decoder finds the runner's private reply pipe and
        # writes a forged frame into it.
        for fd in _reply_fds():
            os.write(fd, b"\\x00\\x00\\x00\\x09garbage!!")
        return 1
    if payload == b"late":
        # Answers, then writes one more byte later, unasked.
        def later():
            time.sleep(0.5)
            for fd in _reply_fds():
                os.write(fd, b"\\x00")
        threading.Thread(target=later, daemon=True).start()
        return "answered"
    if payload == b"membomb":
        x = bytearray(512 * 1024 * 1024)
        return len(x)
    if payload == b"forkbomb":
        pids = []
        for _ in range(200):
            try:
                pid = os.fork()
            except OSError:
                break
            if pid == 0:
                time.sleep(5)
                os._exit(0)
            pids.append(pid)
        return {"forked": len(pids)}
    return {"echo": json.loads(payload)}
'''


def _plugin(parent: Path) -> Path:
    root = parent / SLUG
    pkg = root / f"domovoi_plugin_{SLUG}"
    dec = root / f"domovoi_isolated_{SLUG}"
    pkg.mkdir(parents=True)
    dec.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
    (dec / "__init__.py").write_text("", encoding="utf-8")
    (dec / "decode.py").write_text(DECODER, encoding="utf-8")
    (root / "domovoi-plugin.toml").write_text(textwrap.dedent(f"""
        [plugin]
        slug = "{SLUG}"
        name = "Decoder probe"
        version = "0.0.1"
        publisher = "tests"
        license = "MIT"
        description = "Real-Docker check of the decode-only containers."
        domovoi_api = ">=1.0,<2.0"

        [entry_points]
        core = "domovoi_plugin_{SLUG}.core"

        [permissions]
        friend_houses = true
        warnings = ["A test plugin."]

        [isolated]
        decoder = "domovoi_isolated_{SLUG}.decode:main"
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
    mine = {v for k, v in os.environ.items() if len(v) >= 8 and k not in ("PATH",)}
    out += [f"{name} (a value from the core's environment)" for name, value in env.items()
            if str(value) in mine and not (name == "LC_CTYPE" and value == "C.UTF-8")]
    return out


def _running_slots() -> list[str]:
    """Names of this probe's running containers (only ours, by name)."""
    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name=^{iso.DECODER_CONTAINER_PREFIX}{SLUG}-"],
        capture_output=True, text=True, timeout=30,
    ).stdout
    return sorted(n for n in out.split() if n)


async def _no_gate(slug, manifest):
    # The build gate (Friend Houses on, a friend's apps toggle, the internet
    # answer) is unit-tested; here the build is what is being checked.
    return None


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
        expect(errors == [], f"the probe plugin validates {errors}")
        args = iso.run_args(SLUG, 0, "sha256:x")
        expect(args[args.index("--memory") + 1] == iso.RUN_MEMORY and "--pids-limit" in args
               and "--tmpfs" not in args, "the run flags carry the memory and pids caps and no tmpfs")
        bargs = iso.build_args(SLUG, "0" * 64, "0.0.1")
        expect(bargs[bargs.index("--memory") + 1] == iso.BUILD_MEMORY and "--memory-swap" in bargs
               and "--cpu-shares" in bargs, "the build flags carry the memory cap and a low CPU weight")
        expect(await iso.builder_caps_honoured(),
               "this Docker's classic builder takes --memory (else the core refuses to build)")
        # No database here: the container guard's record of the built image
        # is kept in memory instead.
        from domovoi.container_guard import engine

        noted: dict[str, str] = {}

        async def note(slug: str, image_id: str) -> None:
            noted[slug] = image_id

        async def record(slug: str):
            return noted.get(slug)

        engine.note_decoder_image = note
        engine.decoder_image_record = record
        pool = iso.DecoderPool(SLUG, install_dir=root, manifest=manifest, gate=_no_gate,
                               timeout=10.0, image_timeout=20.0)
        await pool.prepare()
        expect(pool.image_state == "ready" and noted.get(SLUG) == pool.image_id,
               f"the image builds and is recorded before any run ({pool.problem})")
        if pool.image_state != "ready":
            return problems
        try:
            r = await pool.decode("friend-a", b'{"b": 1, "a": [2]}')
            expect(r.outcome == "refused" and "no schema" in r.reason, "without a schema nothing is accepted")
            pool.set_schema(SCHEMA)
            r = await pool.decode("friend-a", b'{"b": 1, "a": [2]}', {"kind": "move"})
            expect(r.ok and r.canonical == b'{"echo":{"a":[2],"b":1}}', f"round trip {r}")
            pool.set_schema({"type": "object", "properties": {"echo": {"type": "integer"}}})
            r = await pool.decode("friend-a", b'"text"')
            expect(r.outcome == "refused" and "not an integer" in r.reason, f"the schema refuses ({r.reason})")
            pool.set_schema(SCHEMA)

            r = await pool.decode("friend-a", b"facts", {"kind": "probe"})
            f = r.value or {}
            expect(r.ok, f"facts {r.reason}")
            expect(f.get("uid") == 65534 and f.get("gid") == 65534, f"runs as 65534 {f.get('uid')}")
            expect(f.get("root_writable") is False, "the root is read-only")
            expect(f.get("tmp_writable") is False and "/tmp" not in (f.get("tmpfs") or []),
                   f"no writable /tmp, no tmpfs {f.get('tmpfs')}")
            expect(f.get("network") is False and f.get("interfaces") == ["lo"],
                   f"no network {f.get('interfaces')}")
            expect(f.get("host_files") == [], f"no host files {f.get('host_files')}")
            expect(f.get("nofile") == [64, 64], f"64 open files at most {f.get('nofile')}")
            expect(f.get("meta") == ["kind"], "the request carries no friend")
            leaks = _env_problems(f.get("env") or {})
            expect(not leaks, "no core environment in the container" + (f": {leaks}" if leaks else ""))

            r_b = await pool.decode("friend-b", b"facts")
            slots = _running_slots()
            expect(r_b.ok and (r_b.value or {}).get("hostname") != f.get("hostname") and len(slots) == 2,
                   f"each friend has its own container {slots}")

            for bad in (b"hang", b"crash", b"garbage", b"late", b"membomb"):
                r = await pool.decode("friend-a", bad)
                if bad == b"late":
                    expect(r.ok, "an answer before the late byte is still an answer")
                    await asyncio.sleep(1.5)
                    expect("friend-a" not in pool.instances, "a byte written unasked kills the container")
                else:
                    expect(r.outcome == "fault", f"{bad.decode()} is a fault ({r.reason})")
                pool.resume("friend-a")       # skip the backoff and the pause here
                r = await pool.decode("friend-a", b"1")
                expect(r.ok and r.value == {"echo": 1}, f"a good item works after {bad.decode()}")
            expect("friend-b" in pool.instances, "the other friend's container was never touched")

            await _images(pool, expect)

            r = await pool.decode("friend-a", b"forkbomb")
            expect((not r.ok) or (r.value or {}).get("forked", 999) < int(iso.RUN_PIDS),
                   f"the pids cap holds ({r.value or r.reason})")

            pool.resume("friend-c")
            for _ in range(iso.BREAKER_FAULTS):
                st = pool.friends.get("friend-c")
                if st is not None:
                    st.retry_at = 0.0
                await pool.decode("friend-c", b"crash")
            expect(pool.paused("friend-c"), "repeated faults pause that friend")
            r = await pool.decode("friend-b", b"2")
            expect(r.ok, "another friend carries on")

            iso.IDLE_SEC = 0.0
            await pool.reap_idle()
            expect(_running_slots() == [] and not pool.instances, "idle containers are stopped")
        finally:
            await pool.stop()
            await iso.forget(SLUG)
            expect(_running_slots() == [], "nothing of the probe is left running")
    return problems


async def _images(pool, expect) -> None:
    import io
    import struct
    import zlib

    from PIL import Image

    def chunks(png: bytes) -> list[bytes]:
        pos, out = 8, []
        while pos < len(png):
            (n,) = struct.unpack(">I", png[pos:pos + 4])
            out.append(png[pos + 4:pos + 8])
            pos += 12 + n
        return out

    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (52.0, 31.0, 12.5)
    buf = io.BytesIO()
    Image.new("RGB", (1600, 1200), (200, 30, 40)).save(buf, "JPEG", exif=exif.tobytes())
    src = buf.getvalue()
    r = await pool.decode_image("friend-a", src)
    expect(r.ok and r.media_type == "image/png" and b"SecretCam" not in (r.image or b"")
           and chunks(r.image or b"\x89PNG\r\n\x1a\n") == [b"IHDR", b"IDAT", b"IEND"]
           and (r.value or {}).get("image", {}).get("width") == iso.IMAGE_MAX_SIDE,
           f"a JPEG with EXIF/GPS comes back as the core's clean PNG ({r.reason or r.value})")

    def chunk(t: bytes, b: bytes) -> bytes:
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)

    bomb = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" * 1_000_000, 9)) + chunk(b"IEND", b""))
    r = await pool.decode_image("friend-a", bomb)
    expect(r.outcome == "refused" and "too large" in r.reason, f"a decompression bomb is refused ({r.reason})")
    r = await pool.decode_image("friend-a", src[: len(src) // 2])
    expect(r.outcome == "refused", f"a truncated JPEG is refused ({r.reason})")
    r = await pool.decode_image("friend-a", b"GIF89a" + b"\x00" * 40)
    expect(r.outcome == "refused", f"a GIF is refused ({r.reason})")
    frames = [Image.new("RGB", (16, 16), c) for c in ((255, 0, 0), (0, 255, 0))]
    anim = io.BytesIO()
    frames[0].save(anim, "WEBP", save_all=True, append_images=frames[1:], lossless=True)
    r = await pool.decode_image("friend-a", anim.getvalue())
    ok = False
    if r.ok:
        with Image.open(io.BytesIO(r.image)) as im:
            ok = im.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
    expect(ok, f"an animation gives its first frame ({r.reason or r.value})")
    r = await pool.decode("friend-a", b"1")
    expect(r.ok, "a good item works after the image refusals")


def main() -> int:
    problems = asyncio.run(_check())
    print("\nALL OK" if not problems else f"\n{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
