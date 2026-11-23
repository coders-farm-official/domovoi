"""Decode-only plugin containers (core 1.5.0, domovoi/plugins_runtime/isolated.py).

The manifest rule (a friend_houses plugin must declare an ``[isolated]``
decoder), the core-built image, the container's hardening flags, the IPC
under a hostile decoder, the lifecycle and the container guard. Docker is
faked throughout; a run against the real Docker is a manual check (see the
module docstring of isolated.py and the PR notes).
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import sys
import tarfile
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from domovoi import bootstrap
from domovoi.container_guard import expected as gx
from domovoi.container_guard.detect import detect
from domovoi.container_guard.model import (
    DECODER_CONTAINER_PREFIX,
    RESERVED_CONTAINER_PREFIXES,
    STOPPED,
    TAMPER,
    DockerView,
    Listed,
    Observed,
)
from domovoi.friend_houses.tor_runtime import DockerFailed
from domovoi.plugins_runtime import isolated as iso
from domovoi.plugins_runtime.manifest import (
    IsolatedDecl,
    ManifestError,
    parse_manifest,
    validate_plugin_dir,
)

REPO = Path(__file__).resolve().parents[2]


# ─── a plugin with a decoder ─────────────────────────────────────────────

def _toml(slug: str, *, perms: str = "", isolated: str | None = None, extra: str = "") -> str:
    iso_table = (
        f'[isolated]\ndecoder = "domovoi_plugin_{slug}.link.decode:main"\n'
        if isolated is None else isolated
    )
    return textwrap.dedent(f"""
        [plugin]
        slug = "{slug}"
        name = "{slug}"
        version = "1.0.0"
        publisher = "tests"
        license = "MIT"
        description = "decode-only container tests"
        domovoi_api = ">=1.0,<2.0"

        [entry_points]
        core = "domovoi_plugin_{slug}.core"

        [permissions]
        """) + perms + "\n\n" + iso_table + "\n" + extra


DECODER_OK = """\
import json
import zlib

import domovoi_isolated


def main(payload, meta):
    if meta.get("img"):
        return domovoi_isolated.Image(payload, meta["img"], lossless=meta.get("lossless", True))
    if meta.get("raw"):
        return payload
    data = json.loads(zlib.decompress(payload, 0) if meta.get("z") else payload)
    if data == "boom":
        raise ValueError("no booms")
    if data == "print":
        print("noise on stdout")
        return {"printed": True}
    return {"got": data, "house": meta.get("house_id")}
"""


def make_plugin(parent: Path, slug: str, *, decoder: str = DECODER_OK, toml: str | None = None) -> Path:
    root = parent / slug
    pkg = root / f"domovoi_plugin_{slug}"
    (pkg / "link").mkdir(parents=True)
    # In-process code the image must never get.
    (pkg / "__init__.py").write_text("SECRET_INIT = 1\n", encoding="utf-8")
    (pkg / "core.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
    (pkg / "link" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "link" / "decode.py").write_text(decoder, encoding="utf-8")
    (root / "domovoi-plugin.toml").write_text(toml or _toml(slug), encoding="utf-8")
    return root


def manifest_of(root: Path):
    return parse_manifest((root / "domovoi-plugin.toml").read_text(encoding="utf-8"))


# ─── the manifest rule ───────────────────────────────────────────────────

def test_isolated_parses_and_is_generic(tmp_path) -> None:
    m = parse_manifest(_toml("gen", isolated='[isolated]\ndecoder = "domovoi_plugin_gen.link.decode:main"\n'
                                              'debian = ["python3-pil"]\n'))
    assert m.isolated == IsolatedDecl("domovoi_plugin_gen.link.decode:main", ("python3-pil",))
    assert (m.isolated.module, m.isolated.function, m.isolated.subpackage) == (
        "domovoi_plugin_gen.link.decode", "main", "link")
    assert m.permissions["friend_houses"] is False  # generic: no permission needed


def test_a_friend_houses_plugin_without_a_decoder_is_refused() -> None:
    with pytest.raises(ManifestError, match=r"friend_houses requires an \[isolated\] decoder"):
        parse_manifest(_toml("fh", perms='friend_houses = true\nwarnings = ["Plays cards with friends"]',
                             isolated=""))


def test_a_friend_houses_plugin_needs_a_warning() -> None:
    with pytest.raises(ManifestError, match="friend_houses requires at least one"):
        parse_manifest(_toml("fh", perms="friend_houses = true"))


def test_a_friend_houses_plugin_with_a_decoder_and_warning_parses() -> None:
    m = parse_manifest(_toml("fh", perms='friend_houses = true\nwarnings = ["Plays cards with friends"]'))
    assert m.permissions["friend_houses"] is True and m.isolated is not None


@pytest.mark.parametrize("decoder", [
    "domovoi_plugin_other.link.decode:main",   # another plugin's package
    "domovoi_plugin_fh.decode:main",           # not inside a subpackage
    "domovoi_plugin_fh.link.decode",           # no function
    "os.system:main",
    "domovoi_plugin_fh.link.decode:main; rm",
    "domovoi_plugin_fh..link:main",
])
def test_a_bad_decoder_reference_is_refused(decoder) -> None:
    with pytest.raises(ManifestError, match="isolated.decoder"):
        parse_manifest(_toml("fh", isolated=f'[isolated]\ndecoder = "{decoder}"\n'))


@pytest.mark.parametrize("table, match", [
    ('[isolated]\ndecoder = "domovoi_plugin_fh.link.decode:main"\ndebian = ["curl"]\n', "may add only"),
    ('[isolated]\ndecoder = "domovoi_plugin_fh.link.decode:main"\ndebian = ["python3-pil", "python3-pil"]\n',
     "more than once"),
    ('[isolated]\ndecoder = "domovoi_plugin_fh.link.decode:main"\npip = ["x==1"]\n', "unknown keys"),
    ('[isolated]\ndecoder = 3\n', "isolated.decoder"),
], ids=["apt", "twice", "pip", "not-a-string"])
def test_isolated_rejects_anything_but_the_allowlist(table, match) -> None:
    with pytest.raises(ManifestError, match=match):
        parse_manifest(_toml("fh", isolated=table))


def test_a_plugin_may_not_declare_a_decoder_container_name() -> None:
    assert DECODER_CONTAINER_PREFIX in RESERVED_CONTAINER_PREFIXES
    extra = textwrap.dedent(f"""
        [[containers]]
        id = "x"
        name = "{DECODER_CONTAINER_PREFIX}fh"
        image = "busybox:1"
        """)
    with pytest.raises(ManifestError, match="core's own"):
        parse_manifest(_toml("fh", extra=extra))


def test_the_decoder_directory_validates(tmp_path) -> None:
    root = make_plugin(tmp_path, "okdec")
    assert validate_plugin_dir(root, manifest_of(root)) == []


@pytest.mark.parametrize("line", [
    "import domovoi.sdk",
    "from domovoi_plugin_imp import core",
    "import requests",
    "from .. import core",
    "import PIL",                         # not declared in isolated.debian
])
def test_the_decoder_may_import_only_stdlib_its_subpackage_and_debian(tmp_path, line) -> None:
    root = make_plugin(tmp_path, "imp", decoder=f"{line}\n\ndef main(payload, meta):\n    return 1\n")
    errors = validate_plugin_dir(root, manifest_of(root))
    assert any("isolated" in e and "import" in e for e in errors), errors


def test_declared_debian_modules_and_relative_imports_are_fine(tmp_path) -> None:
    slug = "deb"
    toml = _toml(slug, isolated=f'[isolated]\ndecoder = "domovoi_plugin_{slug}.link.decode:main"\n'
                                'debian = ["python3-pil"]\n')
    root = make_plugin(tmp_path, slug, toml=toml,
                       decoder="import PIL.Image\nfrom . import helper\n\ndef main(p, m):\n    return 1\n")
    (root / f"domovoi_plugin_{slug}" / "link" / "helper.py").write_text("X = 1\n", encoding="utf-8")
    assert validate_plugin_dir(root, manifest_of(root)) == []


def test_a_missing_decoder_module_is_a_problem(tmp_path) -> None:
    root = make_plugin(tmp_path, "gone")
    (root / "domovoi_plugin_gone" / "link" / "decode.py").unlink()
    assert any("missing" in e for e in validate_plugin_dir(root, manifest_of(root)))


# ─── the image: core-built, pinned ───────────────────────────────────────

def _names(context: bytes) -> list[str]:
    return tarfile.open(fileobj=io.BytesIO(context)).getnames()


def _member(context: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(context)) as tar:
        return tar.extractfile(name).read()


def test_the_context_is_the_core_dockerfile_runner_and_decoder_files_only(tmp_path) -> None:
    root = make_plugin(tmp_path, "ctx")
    (root / "domovoi_plugin_ctx" / "link" / "notes.txt").write_text("not code", encoding="utf-8")
    ctx = iso.context_tar(root, manifest_of(root))
    assert _names(ctx) == [
        "Dockerfile", "app/domovoi_isolated_runner.py", "app/domovoi_isolated.py", "app/decoder.json",
        "app/domovoi_plugin_ctx/__init__.py",
        "app/domovoi_plugin_ctx/link/__init__.py", "app/domovoi_plugin_ctx/link/decode.py",
    ]
    # The top __init__ is the core's empty one; core.py never goes in.
    assert _member(ctx, "app/domovoi_plugin_ctx/__init__.py") == b""
    assert json.loads(_member(ctx, "app/decoder.json")) == {
        "module": "domovoi_plugin_ctx.link.decode", "function": "main"}
    assert _member(ctx, "app/domovoi_isolated_runner.py") == iso.RUNNER_SOURCE.read_bytes()
    assert _member(ctx, "app/domovoi_isolated.py") == iso.HELPER_SOURCE.read_bytes()


def test_the_context_is_reproducible_and_its_hash_follows_the_files(tmp_path) -> None:
    root = make_plugin(tmp_path, "rep")
    m = manifest_of(root)
    a = iso.context_tar(root, m)
    assert a == iso.context_tar(root, m)
    (root / "domovoi_plugin_rep" / "link" / "decode.py").write_text(DECODER_OK + "\n# v2\n", encoding="utf-8")
    assert iso.build_sha(iso.context_tar(root, m)) != iso.build_sha(a)  # an upgrade rebuilds


def test_the_dockerfile_is_the_core_s_on_the_pinned_base(tmp_path) -> None:
    root = make_plugin(tmp_path, "dfile")
    text = iso.dockerfile(manifest_of(root))
    froms = [ln for ln in text.splitlines() if ln.startswith("FROM ")]
    assert froms == [f"FROM {iso.BASE_IMAGE}"] and "@sha256:" in iso.BASE_IMAGE
    for other in ("Dockerfile.fh-listener", "Dockerfile.tor"):
        theirs = [ln for ln in (REPO / "domovoi" / other).read_text(encoding="utf-8").splitlines()
                  if ln.startswith("FROM ")]
        assert theirs == froms, other
    assert "pip" not in text.lower()
    assert "USER 65534:65534" in text
    assert "install -y --no-install-recommends python3 \\" in text


def test_only_allowlisted_debian_packages_reach_the_dockerfile(tmp_path) -> None:
    root = make_plugin(tmp_path, "allow")
    m = manifest_of(root)
    # Even a manifest object that skipped validation can't add a package.
    forged = SimpleNamespace(isolated=IsolatedDecl(m.isolated.decoder, ("python3-pil", "curl; rm -rf /")))
    text = iso.dockerfile(forged)
    assert "python3 python3-pil \\" in text and "curl" not in text


def test_build_args_pin_the_build_and_rerun_apt() -> None:
    args = iso.build_args("fh", "abc", "1.2.3")
    assert args[0] == "build" and "--no-cache" in args and "--pull" in args and args[-1] == "-"
    assert f"{iso.BUILD_LABEL}=abc" in args and "org.domovoi.plugin=fh" in args
    assert args[args.index("-t") + 1] == f"{DECODER_CONTAINER_PREFIX}fh:latest"


def test_the_image_is_rebuilt_when_the_files_change_or_it_is_old() -> None:
    now = datetime.now(timezone.utc)
    assert iso.ImageInfo("sha256:x", "a", now - timedelta(days=1)).stale("a", now=now) is None
    assert iso.ImageInfo("sha256:x", "b", now).stale("a", now=now) == "the plugin's decoder changed"
    assert "days old" in iso.ImageInfo("sha256:x", "a", now - timedelta(days=31)).stale("a", now=now)


# ─── the container's flags ───────────────────────────────────────────────

def _pairs(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_the_container_has_the_listener_s_wall() -> None:
    args = iso.run_args("fh", "sha256:img")
    assert args[:3] == ["run", "-i", "--rm"]
    assert _pairs(args, "--name") == [f"{DECODER_CONTAINER_PREFIX}fh"]
    assert _pairs(args, "--network") == ["none"]
    assert "--read-only" in args
    assert _pairs(args, "--cap-drop") == ["ALL"]
    assert _pairs(args, "--security-opt") == ["no-new-privileges"]
    user = _pairs(args, "--user")
    assert user == ["65534:65534"] and not user[0].startswith("0")
    assert _pairs(args, "--memory") == ["256m"] and _pairs(args, "--memory-swap") == ["256m"]
    assert _pairs(args, "--pids-limit") == ["32"]
    assert _pairs(args, "--cpus") == ["0.5"]
    assert _pairs(args, "--ipc") == ["none"]
    tmpfs = _pairs(args, "--tmpfs")
    assert len(tmpfs) == 1 and tmpfs[0].startswith("/tmp:") and {"noexec", "nosuid", "nodev"} <= set(
        tmpfs[0].split(":", 1)[1].split(","))
    assert _pairs(args, "--log-driver") == ["none"]
    # Nothing from the host and nothing of the core's environment.
    assert not {"-v", "--volume", "--mount", "-e", "--env", "--env-file", "--privileged", "--cap-add",
                "--device", "--pid", "-p", "--publish", "--add-host"} & set(args)
    assert args[-1] == "sha256:img"  # the exact image the core built, last: no command override


# ─── fake Docker ─────────────────────────────────────────────────────────

class FakeDocker:
    """``docker`` as the code calls it: images by tag, builds, removals."""

    def __init__(self) -> None:
        self.images: dict[str, tuple[str, str, str]] = {}   # tag -> (id, sha, created)
        self.calls: list[tuple[str, ...]] = []
        self.envs: dict[str, dict | None] = {}
        self.fail_build = False
        self.classic_builder = True
        self._n = 0

    async def __call__(self, *args: str, timeout: float = 60.0, stdin: bytes | None = None,
                       env: dict | None = None):
        self.calls.append(args)
        if args[:2] == ("build", "--help"):
            self.envs["help"] = env
            if self.classic_builder and (env or {}).get("DOCKER_BUILDKIT") == "0":
                return 0, "Usage:  docker build [OPTIONS] PATH | URL | -\n  -m, --memory bytes\n      --memory-swap bytes\n", ""
            return 0, "Usage:  docker buildx build [OPTIONS] PATH | URL | -\n", ""
        if args[:2] == ("image", "inspect"):
            img = self.images.get(args[-1])
            return (0, "|".join(img) + "\n", "") if img else (1, "", "No such image")
        if args[0] == "build":
            self.envs["build"] = env
            if self.fail_build:
                return 1, "", "step 3 failed\nE: Unable to locate package"
            assert stdin is not None and tarfile.open(fileobj=io.BytesIO(stdin)).getnames()[0] == "Dockerfile"
            tag = args[args.index("-t") + 1]
            sha = next(a.split("=", 1)[1] for a in args if a.startswith(iso.BUILD_LABEL + "="))
            self._n += 1
            self.images[tag] = (f"sha256:{self._n:064d}", sha,
                               datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
            return 0, "", ""
        if args[:2] == ("image", "rm"):
            for tag, img in list(self.images.items()):
                if args[2] in (tag, img[0]):
                    del self.images[tag]
            return 0, "", ""
        if args[:2] == ("rm", "-f"):
            return 1, "", "Error: No such container: " + args[2]
        raise AssertionError(f"unexpected docker call {args}")


@pytest.fixture
def docker(monkeypatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(iso, "_docker", fake)
    noted: list[tuple[str, str]] = []

    async def note(slug: str, image_id: str) -> None:
        noted.append((slug, image_id))

    from domovoi.container_guard import engine

    monkeypatch.setattr(engine, "note_decoder_image", note)
    fake.noted = noted
    return fake


async def test_ensure_image_builds_once_and_tells_the_guard(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "img")
    m = manifest_of(root)
    first = await iso.ensure_image("img", root, m)
    assert docker.noted == [("img", first)]
    assert await iso.ensure_image("img", root, m) == first   # current: no rebuild
    assert sum(1 for c in docker.calls if c[0] == "build" and c[1] != "--help") == 1


async def test_an_upgrade_rebuilds_and_drops_the_old_image(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "upg")
    m = manifest_of(root)
    first = await iso.ensure_image("upg", root, m)
    (root / "domovoi_plugin_upg" / "link" / "decode.py").write_text(DECODER_OK + "# 1.1\n", encoding="utf-8")
    second = await iso.ensure_image("upg", root, m)
    assert second != first
    assert ("image", "rm", first) in docker.calls
    assert docker.noted[-1] == ("upg", second)


async def test_a_failed_build_says_so(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "bad")
    docker.fail_build = True
    with pytest.raises(DockerFailed, match="Unable to locate package"):
        await iso.ensure_image("bad", root, manifest_of(root))


def test_the_build_is_memory_capped_and_low_priority() -> None:
    args = iso.build_args("fh", "abc", "1.2.3")
    assert _pairs(args, "--memory") == ["1g"] and _pairs(args, "--memory-swap") == ["1g"]
    assert _pairs(args, "--cpu-shares") == ["256"]   # a quarter of the default weight
    assert _pairs(args, "--cpu-quota") == ["100000"] and _pairs(args, "--cpu-period") == ["100000"]
    # BuildKit accepts and ignores those flags: the build runs on the classic builder.
    assert iso.build_env()["DOCKER_BUILDKIT"] == "0"


async def test_the_build_runs_capped_on_the_classic_builder(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "capped")
    await iso.ensure_image("capped", root, manifest_of(root))
    assert docker.envs["build"]["DOCKER_BUILDKIT"] == "0"
    assert docker.envs["help"]["DOCKER_BUILDKIT"] == "0"
    build = next(c for c in docker.calls if c[0] == "build" and c[1] != "--help")
    assert "--memory" in build and "--memory-swap" in build


async def test_no_uncapped_build_when_the_classic_builder_is_gone(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "nocap")
    docker.classic_builder = False
    with pytest.raises(DockerFailed, match="can't cap the memory"):
        await iso.ensure_image("nocap", root, manifest_of(root))
    assert not any(c[0] == "build" and c[1] != "--help" for c in docker.calls)


# ─── the IPC under a hostile decoder ─────────────────────────────────────

def _frame(rid: int, status: int, data: bytes) -> bytes:
    body = struct.pack(">IB", rid, status) + data
    return struct.pack(">I", len(body)) + body


class FakeProc:
    """A decoder process. ``answer(rid, meta, payload)`` returns the raw
    bytes it writes back, ``None`` to hang, or ``"crash"``."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.stdout = asyncio.StreamReader()
        self.stderr = None
        self.returncode = None
        self.killed = False
        self.stdin = self
        self.closed = False

    # stdin
    def write(self, data: bytes) -> None:
        (size,) = struct.unpack(">I", data[:4])
        body = data[4:]
        assert len(body) == size
        rid, mlen = struct.unpack(">IH", body[:6])
        meta = json.loads(body[6:6 + mlen])
        out = self.answer(rid, meta, body[6 + mlen:])
        if out == "crash":
            self.returncode = 1
            self.stdout.feed_eof()
        elif out is not None:
            self.stdout.feed_data(out)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def _decoder(answer, *, docker: FakeDocker, timeout: float = 0.5, clock=None):
    procs: list[FakeProc] = []

    async def spawn(argv):
        assert argv == iso.run_args("hostile", "sha256:img")
        p = FakeProc(answer)
        procs.append(p)
        return p

    async def prepare() -> str:
        return "sha256:img"

    dec = iso.IsolatedDecoder("hostile", install_dir=Path("."), manifest=None, spawn=spawn,
                              prepare=prepare, timeout=timeout, clock=clock or Clock())
    await dec.start()
    return dec, procs


def _ok(value) -> callable:
    return lambda rid, meta, payload: _frame(rid, 0, json.dumps(value).encode())


async def test_a_round_trip_is_reserialized_by_the_core(docker) -> None:
    dec, procs = await _decoder(lambda rid, meta, payload: _frame(
        rid, 0, b'{ "b" : [1, 2.5, "x"],\n "a": {"house": "' + meta["house_id"].encode() + b'"} }'),
        docker=docker)
    r = await dec.decode(b"\x00raw", {"house_id": "h1"})
    assert r.ok and r.value == {"a": {"house": "h1"}, "b": [1, 2.5, "x"]}
    assert r.canonical == b'{"a":{"house":"h1"},"b":[1,2.5,"x"]}'
    assert dec.state == "ready" and not procs[0].killed


async def test_the_decoder_s_own_refusal_is_a_refused_item_not_a_restart(docker) -> None:
    dec, procs = await _decoder(lambda rid, m, p: _frame(rid, 1, b"ValueError: bad move"), docker=docker)
    r = await dec.decode(b"x")
    assert not r.ok and "bad move" in r.reason
    assert dec.state == "ready" and len(procs) == 1


@pytest.mark.parametrize("data, why", [
    (b'{"a": NaN}', "NaN"),
    (b'{"a": Infinity}', "Infinity"),
    (b'{"a": 1e999}', "out of range"),
    (b'{"a": 123456789012345678901234567890}', "out of range"),
    (b"[" * 17 + b"]" * 17, "deeper"),
    (b"[" * 5000 + b"]" * 5000, "deeper"),
    (b'{"a": 1, "a": 2}', "duplicate"),
    (b"\xff\xfe not utf-8", "utf-8"),
    (b'"\\ud800"', "surrogate"),
    (b"garbage", "Expecting value"),
    (b'{"a": 1} trailing', "Extra data"),
    # Within the frame cap, but past it once the core re-serializes it.
    (b"[" + b"1e5," * 13000 + b"1e5]", "too large"),
], ids=["nan", "inf", "1e999", "bigint", "depth17", "depth5000", "dupkey", "utf8", "surrogate",
        "garbage", "trailing", "grows"])
async def test_bad_json_is_refused_and_the_stream_stays_up(docker, data, why) -> None:
    dec, procs = await _decoder(lambda rid, m, p: _frame(rid, 0, data), docker=docker)
    r = await dec.decode(b"x")
    assert not r.ok and r.reason.startswith("bad output"), r
    assert why.lower() in r.reason.lower(), r.reason
    assert dec.state == "ready"


async def test_sixteen_levels_are_fine() -> None:
    value, _ = iso.canonical(b"[" * 16 + b"]" * 16)
    assert value == [[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]
    # A bracket inside a string isn't nesting.
    assert iso.canonical(b'"' + b"[" * 100 + b'"')[0] == "[" * 100


@pytest.mark.parametrize("answer, why", [
    (lambda rid, m, p: None, "timed out"),                                     # hangs
    (lambda rid, m, p: "crash", "stopped"),                                    # crashes
    (lambda rid, m, p: struct.pack(">I", iso.FRAME_MAX + 1) + b"x" * 10, "bad size"),    # oversize frame
    (lambda rid, m, p: struct.pack(">I", 2) + b"xx", "bad size"),              # undersize frame
    (lambda rid, m, p: _frame(rid + 7, 0, b"1"), "another request"),           # wrong id
    (lambda rid, m, p: _frame(rid, 9, b"1"), "bad status"),
    (lambda rid, m, p: b"\x00\x00", "timed out"),                              # a torn header
], ids=["hang", "crash", "oversize-frame", "undersize-frame", "wrong-id", "bad-status", "torn"])
async def test_transport_faults_refuse_the_item_and_restart_with_backoff(docker, answer, why) -> None:
    clock = Clock()
    dec, procs = await _decoder(answer, docker=docker, timeout=0.2, clock=clock)
    r = await dec.decode(b"x")
    assert not r.ok and why in r.reason, r.reason
    assert dec.state == "backoff" and procs[0].closed and procs[0].returncode is not None
    # The container is removed too (killing the docker client doesn't stop it).
    assert ("rm", "-f", f"{DECODER_CONTAINER_PREFIX}hostile") in docker.calls
    # In backoff: refused at once, no new container.
    r2 = await dec.decode(b"x")
    assert not r2.ok and "isn't running" in r2.reason and len(procs) == 1
    # After the backoff: a new container, and a good answer resets it.
    procs_answer = _ok({"fine": True})
    clock.t += iso.BACKOFF_FIRST_SEC + 0.01

    async def spawn(argv):
        p = FakeProc(procs_answer)
        procs.append(p)
        return p

    dec._spawn = spawn
    r3 = await dec.decode(b"x")
    assert r3.ok and r3.value == {"fine": True} and len(procs) == 2
    assert dec._backoff == iso.BACKOFF_FIRST_SEC and dec.restarts == 1


async def test_backoff_doubles_up_to_its_cap(docker) -> None:
    clock = Clock()
    dec, procs = await _decoder(lambda rid, m, p: "crash", docker=docker, clock=clock)
    waits = []
    for _ in range(9):
        await dec.decode(b"x")
        waits.append(dec._retry_at - clock.t)
        clock.t = dec._retry_at
    assert waits[:4] == [1.0, 2.0, 4.0, 8.0] and waits[-1] == iso.BACKOFF_MAX_SEC


async def test_requests_past_the_caps_never_reach_the_decoder(docker) -> None:
    seen = []
    dec, _ = await _decoder(lambda rid, m, p: seen.append(p) or _frame(rid, 0, b"1"), docker=docker)
    assert not (await dec.decode(b"x" * (iso.PAYLOAD_MAX + 1))).ok
    assert not (await dec.decode(b"x", {"k": "v" * iso.META_MAX})).ok
    assert not (await dec.decode(b"x", {"k": float("nan")})).ok
    assert not (await dec.decode("not bytes")).ok
    assert seen == []


async def test_a_decoder_that_could_not_be_built_refuses_everything(tmp_path, docker) -> None:
    async def prepare() -> str:
        raise DockerFailed("building the x decoder failed")

    dec = iso.IsolatedDecoder("x", install_dir=tmp_path, manifest=None, prepare=prepare)
    await dec.start()
    assert dec.state == "failed" and "failed" in dec.problem
    r = await dec.decode(b"x")
    assert not r.ok and "failed" in r.reason


async def test_the_real_runner_round_trip_in_a_subprocess(tmp_path, docker) -> None:
    """The context's own runner, decoder.json and subpackage, unpacked and
    run as a process (what the container runs, minus Docker)."""
    root = make_plugin(tmp_path, "real")
    app = tmp_path / "unpacked"
    with tarfile.open(fileobj=io.BytesIO(iso.context_tar(root, manifest_of(root)))) as tar:
        for member in tar.getmembers():
            target = app / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())

    async def spawn(argv):
        return await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", str(app / "app" / iso.RUNNER_NAME),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=tmp_path,
        )

    async def prepare() -> str:
        return "sha256:img"

    dec = iso.IsolatedDecoder("real", install_dir=root, manifest=manifest_of(root), spawn=spawn,
                              prepare=prepare, timeout=20.0)
    await dec.start()
    try:
        import zlib

        r = await dec.decode(json.dumps({"card": "Q♠"}).encode(), {"house_id": "h9"})
        assert r.ok and r.value == {"got": {"card": "Q♠"}, "house": "h9"}, r
        r = await dec.decode(zlib.compress(b'"hi"'), {"z": True})
        assert r.ok and r.value == {"got": "hi", "house": None}
        r = await dec.decode(b'"boom"')
        assert not r.ok and "no booms" in r.reason
        r = await dec.decode(b'"print"')   # a print() goes to stderr, not into the replies
        assert r.ok and r.value == {"printed": True}
        r = await dec.decode(b"not json")
        assert not r.ok and "JSONDecodeError" in r.reason
        assert dec.state == "ready"
        # The in-process package was never copied: its __init__ is the core's empty one.
        assert (app / "app" / "domovoi_plugin_real" / "__init__.py").read_bytes() == b""
        assert not (app / "app" / "domovoi_plugin_real" / "core.py").exists()
    finally:
        await dec.stop()
    assert dec.state == "stopped"


# ─── lifecycle ───────────────────────────────────────────────────────────

@pytest.fixture
def spawned(monkeypatch) -> list[tuple[list[str], FakeProc]]:
    out: list[tuple[list[str], FakeProc]] = []

    async def spawn(argv):
        p = FakeProc(_ok({"ok": 1}))
        out.append((argv, p))
        return p

    monkeypatch.setattr(iso, "_spawn_docker", spawn)
    yield out
    iso.DECODERS.clear()


async def _settle(dec) -> None:
    if dec._bring_up is not None:
        await dec._bring_up


async def test_start_stop_forget(tmp_path, docker, spawned) -> None:
    root = make_plugin(tmp_path, "life")
    m = manifest_of(root)
    dec = iso.start_for("life", root, m)
    assert dec is iso.DECODERS["life"] and dec.state == "starting"
    await _settle(dec)
    assert dec.state == "ready"
    image_id = docker.images[iso.image_tag("life")][0]
    assert spawned[0][0] == iso.run_args("life", image_id)
    assert (await iso.decode("life", b"x")).value == {"ok": 1}
    # Disable: stopped and its container removed.
    await iso.stop_for("life")
    assert "life" not in iso.DECODERS and spawned[0][1].killed and spawned[0][1].closed
    assert ("rm", "-f", f"{DECODER_CONTAINER_PREFIX}life") in docker.calls
    assert not (await iso.decode("life", b"x")).ok
    # Uninstall: the image goes too.
    await iso.forget("life")
    assert iso.image_tag("life") not in docker.images


def test_a_plugin_without_isolated_gets_no_decoder(tmp_path) -> None:
    m = parse_manifest(_toml("plain", isolated=""))
    assert iso.start_for("plain", tmp_path, m) is None and "plain" not in iso.DECODERS


async def test_the_loader_starts_on_load_and_stops_on_unload_and_upgrade_rebuilds(
    tmp_path, docker, spawned
) -> None:
    from domovoi.plugins_runtime.loader import LOADER

    bootstrap.register_nvidia_dlls()
    slug = "isolife"
    root = make_plugin(tmp_path, slug)

    async def load():
        await LOADER.load_plugin(slug=slug, install_dir=root, manifest=manifest_of(root),
                                 foreign_corpus=[], foreign_web_routes=[], update_registry_status=False)
        await _settle(iso.DECODERS[slug])

    await load()
    try:
        assert iso.DECODERS[slug].state == "ready"
        first = iso.DECODERS[slug].image_id
        await LOADER.unload_plugin(slug)
        assert slug not in iso.DECODERS and spawned[-1][1].killed
        # An upgrade swaps the files; the next load rebuilds the image.
        (root / f"domovoi_plugin_{slug}" / "link" / "decode.py").write_text(
            DECODER_OK + "# 1.1\n", encoding="utf-8")
        await load()
        assert iso.DECODERS[slug].image_id != first
        assert ("image", "rm", first) in docker.calls
    finally:
        await LOADER.unload_plugin(slug)


# ─── the container guard ─────────────────────────────────────────────────

def _row(slug: str, *, isolated: bool = True, status: str = "ok"):
    manifest = {"plugin": {"slug": slug}}
    if isolated:
        manifest["isolated"] = {"decoder": f"domovoi_plugin_{slug}.link.decode:main"}
    return SimpleNamespace(slug=slug, name=slug.title(), status=status, enabled=True, manifest=manifest)


def test_the_guard_expects_each_decoder_on_the_image_the_core_built() -> None:
    rows = [_row("games"), _row("plain", isolated=False), _row("gone", status="uninstalled")]
    out = gx.decoder_expectations(rows, {"games": "sha256:built"})
    assert len(out) == 1
    e = out[0]
    assert (e.name, e.owner, e.role, e.state, e.image_id, e.required) == (
        f"{DECODER_CONTAINER_PREFIX}games", "core", "plugin-decoder", STOPPED, "sha256:built", False)
    assert e.name == iso.container_name("games") and e.image_ref == iso.image_tag("games")


def test_the_guard_reports_a_decoder_running_another_image() -> None:
    e = gx.decoder_expectations([_row("games")], {"games": "sha256:" + "1" * 64})[0]
    exp = gx.ExpectedSet()
    exp.add([e])

    def view(image_id: str) -> DockerView:
        o = Observed(id="c" * 64, name=e.name, status="running", running=True,
                     config_image="sha256:" + "1" * 64, image_id=image_id)
        v = DockerView(ok=True)
        v.listed[o.name] = Listed(id=o.id, name=o.name, compose_project="", compose_service="")
        v.containers[o.name] = o
        return v

    now = 1_800_000_000.0
    assert detect(exp, view("sha256:" + "1" * 64), now_wall=now, boot_wall=now - 3600).findings == []
    swapped = detect(exp, view("sha256:" + "2" * 64), now_wall=now, boot_wall=now - 3600)
    assert [(f.cls, f.kind, f.container) for f in swapped.findings] == [(TAMPER, "image", e.name)]


def test_an_installer_hold_holds_the_decoder() -> None:
    from domovoi.container_guard import holds

    holds.hold("plugin:games", 60, "upgrading")
    try:
        assert gx.decoder_expectations([_row("games")], {})[0].hold == "upgrading"
    finally:
        holds.clear()


# ─── slice 2: images re-encoded in the container ─────────────────────────

PIL = pytest.importorskip("PIL")
from PIL import Image as PILImage  # noqa: E402
from PIL import PngImagePlugin  # noqa: E402

from domovoi.plugins_runtime import isolated_helper as helper  # noqa: E402


def _img(fmt: str, size=(64, 48), mode="RGB", **save) -> bytes:
    color = {"RGB": (200, 30, 40), "RGBA": (200, 30, 40, 120), "L": 90}[mode]
    im = PILImage.new(mode, size, color)
    for x in range(0, size[0], 4):  # some detail, so a re-encode isn't trivially equal
        im.putpixel((x, x % size[1]), (0, 0, 0) if mode == "RGB" else ((0, 0, 0, 255) if mode == "RGBA" else 0))
    buf = io.BytesIO()
    im.save(buf, fmt, **save)
    return buf.getvalue()


def _gps_exif() -> bytes:
    exif = PILImage.Exif()
    exif[0x010F] = "SecretCam"            # Make
    exif[0x0131] = "SecretSoftware"       # Software
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (52.0, 31.0, 12.5)
    gps[3], gps[4] = "E", (13.0, 24.0, 1.5)
    return exif.tobytes()


def _png_chunk(ctype: bytes, body: bytes) -> bytes:
    import zlib

    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def _bomb_png(width: int, height: int) -> bytes:
    """A small file that declares a huge image (the pixels are never read)."""
    import zlib

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 4096, 9)
    return iso.PNG_MAGIC + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _open(data: bytes):
    return PILImage.open(io.BytesIO(data))


# The helper (it runs inside the container; here, in process).

@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_exif_and_gps_are_gone_and_the_bytes_are_new(fmt) -> None:
    src = _img(fmt, exif=_gps_exif())
    assert b"SecretCam" in src
    for out_fmt in ("png", "webp"):
        _, out = helper.reencode(helper.Image(src, out_fmt))
        assert out != src and b"SecretCam" not in out and b"SecretSoftware" not in out
        assert b"Exif" not in out and b"GPS" not in out
        with _open(out) as im:
            assert dict(im.getexif()) == {} and "exif" not in im.info and "icc_profile" not in im.info
        iso.check_image(out)


def test_png_text_and_icc_are_stripped() -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "SecretText")
    info.add_itxt("XML:com.adobe.xmp", "<x:xmpmeta>SecretXmp</x:xmpmeta>")
    src = _img("PNG", pnginfo=info, icc_profile=b"SecretIcc" * 20)
    _, out = helper.reencode(helper.Image(src))
    assert src.count(b"SecretText") and src.count(b"iCCP")
    for marker in (b"SecretText", b"SecretXmp", b"SecretIcc", b"tEXt", b"iTXt", b"zTXt", b"iCCP"):
        assert marker not in out, marker
    with _open(out) as im:
        assert "Comment" not in im.info and "icc_profile" not in im.info


def test_the_orientation_is_applied_before_the_metadata_goes() -> None:
    exif = PILImage.Exif()
    exif[0x0112] = 6                     # rotate 90 on display
    src = _img("JPEG", size=(40, 20), exif=exif.tobytes())
    _, out = helper.reencode(helper.Image(src))
    with _open(out) as im:
        assert im.size == (20, 40) and dict(im.getexif()) == {}


def test_a_big_image_is_scaled_to_1024_a_side() -> None:
    _, out = helper.reencode(helper.Image(_img("PNG", size=(3000, 1200))))
    assert iso.check_image(out)[1:] == (1024, 410)


@pytest.mark.parametrize("data, why", [
    (_bomb_png(20000, 20000), "too large"),          # a decompression bomb: refused before decoding
    (_bomb_png(4000, 3001), "too large"),            # just over MAX_PIXELS
    (b"GIF89a" + b"\x00" * 40, "not a PNG, JPEG or WebP"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "not a PNG, JPEG or WebP"),
    (b"", "not a PNG, JPEG or WebP"),
    (iso.PNG_MAGIC + b"\xff\xd8\xff" + b"\x00" * 40, "doesn't decode"),
], ids=["bomb", "over-max-pixels", "gif", "svg", "empty", "png-magic-garbage"])
def test_hostile_images_are_refused(data, why) -> None:
    with pytest.raises(helper.ImageRefused, match=why):
        helper.reencode(helper.Image(data))


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_truncated_images_are_refused(fmt) -> None:
    data = _img(fmt, size=(300, 200))
    with pytest.raises(helper.ImageRefused):
        helper.reencode(helper.Image(data[: len(data) // 2]))


def test_an_animation_is_refused() -> None:
    frames = [PILImage.new("RGB", (16, 16), c) for c in ((255, 0, 0), (0, 255, 0))]
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:])
    with pytest.raises(helper.ImageRefused, match="animated"):
        helper.reencode(helper.Image(buf.getvalue()))


def test_the_helper_s_caps_match_the_core_s() -> None:
    assert helper.MAX_SIDE == iso.IMAGE_MAX_SIDE and helper.OUTPUT_MAX == iso.IMAGE_MAX
    import domovoi.plugins_runtime.isolated_runner as runner

    assert runner.REQUEST_MAX == iso.PAYLOAD_MAX + iso.META_MAX + 6 and runner.REPLY_MAX == iso.REPLY_MAX


# The core's re-check (what a decoder that skips the helper would meet).

def test_the_core_accepts_fresh_encodes() -> None:
    for fmt, save in (("PNG", {}), ("WEBP", {"lossless": True}), ("WEBP", {"quality": 80})):
        for mode in ("RGB", "RGBA"):
            got = iso.check_image(_img(fmt, mode=mode, **save))
            assert got == (fmt.lower(), 64, 48)


def test_the_core_refuses_metadata_and_anything_but_pixels() -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "x")
    clean_png = _img("PNG")
    frames = [PILImage.new("RGB", (16, 16), c) for c in ((255, 0, 0), (0, 255, 0))]
    anim = io.BytesIO()
    frames[0].save(anim, "WEBP", save_all=True, append_images=frames[1:])
    bad = {
        "png text": _img("PNG", pnginfo=info),
        "png exif": _img("PNG", exif=_gps_exif()),
        "png icc": _img("PNG", icc_profile=b"x" * 64),
        "webp exif": _img("WEBP", exif=_gps_exif()),
        "webp icc": _img("WEBP", icc_profile=b"x" * 64),
        "webp animated": anim.getvalue(),
        "jpeg": _img("JPEG"),
        "too wide": _img("PNG", size=(1025, 10)),
        "trailing bytes": clean_png + b"MORE",
        "bad crc": clean_png[:-5] + bytes([clean_png[-5] ^ 1]) + clean_png[-4:],
        "truncated png": clean_png[:-12],
        "truncated webp": _img("WEBP")[:-7],
        "bomb header": _bomb_png(20000, 20000),
        "too big": iso.PNG_MAGIC + b"\x00" * iso.IMAGE_MAX,
        "garbage": b"RIFF\x00\x00\x00\x00WEBPjunk",
    }
    for name, data in bad.items():
        with pytest.raises(ValueError):
            iso.check_image(data)
            pytest.fail(name)


# Through the IPC (fake container) and the real runner (subprocess).

async def test_an_image_reply_reaches_the_caller_as_checked_bytes(docker) -> None:
    png = _img("PNG")
    dec, _ = await _decoder(lambda rid, m, p: _frame(rid, 2, png), docker=docker)
    r = await dec.decode(b"x")
    assert r.ok and r.image == png and r.media_type == "image/png"
    assert r.value == {"image": {"format": "png", "width": 64, "height": 48, "bytes": len(png)}}


async def test_an_image_reply_with_metadata_is_refused_and_the_stream_stays_up(docker) -> None:
    dirty = _img("JPEG", exif=_gps_exif())
    dec, procs = await _decoder(lambda rid, m, p: _frame(rid, 2, dirty), docker=docker)
    r = await dec.decode(b"x")
    assert not r.ok and r.reason.startswith("bad image") and r.image is None
    assert dec.state == "ready" and len(procs) == 1


async def test_frame_caps_follow_the_reply_kind(docker) -> None:
    # A JSON reply may not use the image allowance.
    dec, _ = await _decoder(lambda rid, m, p: _frame(rid, 0, b" " * (iso.REPLY_MAX + 10)), docker=docker)
    r = await dec.decode(b"x")
    assert not r.ok and dec.state == "backoff"
    # An image past the frame cap breaks the stream too.
    dec, _ = await _decoder(lambda rid, m, p: struct.pack(">I", iso.FRAME_MAX + 1) + b"x", docker=docker)
    r = await dec.decode(b"x")
    assert not r.ok and "bad size" in r.reason


async def test_the_real_runner_re_encodes_images(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "realimg")
    assert validate_plugin_dir(root, manifest_of(root)) == []
    app = tmp_path / "unpacked"
    with tarfile.open(fileobj=io.BytesIO(iso.context_tar(root, manifest_of(root)))) as tar:
        for member in tar.getmembers():
            target = app / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())

    async def spawn(argv):
        return await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", str(app / "app" / iso.RUNNER_NAME),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=tmp_path,
        )

    async def prepare() -> str:
        return "sha256:img"

    dec = iso.IsolatedDecoder("realimg", install_dir=root, manifest=manifest_of(root), spawn=spawn,
                              prepare=prepare, timeout=30.0)
    await dec.start()
    try:
        src = _img("JPEG", size=(2000, 1500), exif=_gps_exif())
        r = await dec.decode(src, {"img": "webp", "lossless": False})
        assert r.ok and r.media_type == "image/webp", r
        assert r.value["image"]["width"] == 1024 and r.value["image"]["height"] == 768
        assert b"SecretCam" not in r.image and r.image != src
        r = await dec.decode(_img("PNG"), {"raw": True})          # plain bytes: PNG out
        assert r.ok and r.media_type == "image/png"
        r = await dec.decode(_bomb_png(20000, 20000), {"img": "png"})
        assert not r.ok and "too large" in r.reason
        r = await dec.decode(b"GIF89a....", {"raw": True})
        assert not r.ok and "not a PNG" in r.reason
        r = await dec.decode(b'{"still": "json"}')
        assert r.ok and r.value == {"got": {"still": "json"}, "house": None}
        assert dec.state == "ready"
    finally:
        await dec.stop()
