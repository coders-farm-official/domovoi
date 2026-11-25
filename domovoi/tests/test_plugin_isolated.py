"""Decode-only plugin containers (core 1.5.0, domovoi/plugins_runtime/isolated.py).

The manifest rule (a friend_houses plugin must declare an ``[isolated]``
decoder, in its own top-level package), the core-built image and when it
may be built, the containers' hardening flags, one container per friend,
the IPC under a hostile decoder, faults and the per-friend pause, the
reply and schema checks, images (raw pixels out of the container, the PNG
written by the core), the lifecycle and the container guard. Docker is
faked throughout; ``scripts/dev/check_plugin_decoder_docker.py`` is the
real-Docker check. The findings named (D1...) are the 2026-11-23 design
review's.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import struct
import sys
import tarfile
import textwrap
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from domovoi import bootstrap
from domovoi.container_guard import expected as gx
from domovoi.container_guard.detect import detect
from domovoi.container_guard.model import (
    DECODER_CONTAINER_PREFIX,
    DECODER_SLOTS,
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
# Anything the reply checks allow (a plugin's real schema is strict).
SCHEMA_ANY = {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "array"},
                        {"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "null"}]}


# ─── a plugin with a decoder ─────────────────────────────────────────────

def _toml(slug: str, *, perms: str = "", isolated: str | None = None, extra: str = "") -> str:
    iso_table = (
        f'[isolated]\ndecoder = "domovoi_isolated_{slug}.decode:main"\n'
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


def main(payload, meta):
    data = json.loads(zlib.decompress(payload, 0) if meta.get("z") else payload)
    if data == "boom":
        raise ValueError("no booms <script>")
    if data == "print":
        print("noise on stdout")
        return {"printed": True}
    return {"got": data, "meta": sorted(meta)}
"""


def make_plugin(parent: Path, slug: str, *, decoder: str = DECODER_OK, toml: str | None = None) -> Path:
    root = parent / slug
    pkg = root / f"domovoi_plugin_{slug}"
    dec = root / f"domovoi_isolated_{slug}"
    pkg.mkdir(parents=True)
    dec.mkdir()
    # In-process code the image must never get.
    (pkg / "__init__.py").write_text("SECRET_INIT = 1\n", encoding="utf-8")
    (pkg / "core.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
    (dec / "__init__.py").write_text("", encoding="utf-8")
    (dec / "decode.py").write_text(decoder, encoding="utf-8")
    (root / "domovoi-plugin.toml").write_text(toml or _toml(slug), encoding="utf-8")
    return root


def manifest_of(root: Path):
    return parse_manifest((root / "domovoi-plugin.toml").read_text(encoding="utf-8"))


# ─── the manifest rule (D11: its own top-level, Python-only package) ─────

def test_isolated_parses_and_is_generic() -> None:
    m = parse_manifest(_toml("gen", isolated='[isolated]\ndecoder = "domovoi_isolated_gen.decode:main"\n'
                                              'debian = ["python3-pil"]\n'))
    assert m.isolated == IsolatedDecl("domovoi_isolated_gen.decode:main", ("python3-pil",))
    assert (m.isolated.module, m.isolated.function, m.isolated.package) == (
        "domovoi_isolated_gen.decode", "main", "domovoi_isolated_gen")
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
    "domovoi_plugin_fh.link.decode:main",      # inside the in-process package: its __init__ would run
    "domovoi_isolated_other.decode:main",      # another plugin's
    "domovoi_isolated_fh:main",                # no module
    "domovoi_isolated_fh.decode",              # no function
    "os.system:main",
    "domovoi_isolated_fh.decode:main; rm",
    "domovoi_isolated_fh..decode:main",
])
def test_a_bad_decoder_reference_is_refused(decoder) -> None:
    with pytest.raises(ManifestError, match="isolated.decoder"):
        parse_manifest(_toml("fh", isolated=f'[isolated]\ndecoder = "{decoder}"\n'))


@pytest.mark.parametrize("table, match", [
    ('[isolated]\ndecoder = "domovoi_isolated_fh.decode:main"\ndebian = ["curl"]\n', "may add only"),
    ('[isolated]\ndecoder = "domovoi_isolated_fh.decode:main"\ndebian = ["python3-pil", "python3-pil"]\n',
     "more than once"),
    ('[isolated]\ndecoder = "domovoi_isolated_fh.decode:main"\npip = ["x==1"]\n', "unknown keys"),
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
        name = "{DECODER_CONTAINER_PREFIX}fh-0"
        image = "busybox:1"
        """)
    with pytest.raises(ManifestError, match="core's own"):
        parse_manifest(_toml("fh", extra=extra))


def test_the_decoder_directory_validates(tmp_path) -> None:
    root = make_plugin(tmp_path, "okdec")
    assert validate_plugin_dir(root, manifest_of(root)) == []


def test_only_the_decoder_package_may_sit_beside_the_plugin_package(tmp_path) -> None:
    root = make_plugin(tmp_path, "stray")
    (root / "other_pkg").mkdir()
    (root / "other_pkg" / "__init__.py").write_text("", encoding="utf-8")
    errors = validate_plugin_dir(root, manifest_of(root))
    assert len(errors) == 1 and "other_pkg" in errors[0]


@pytest.mark.parametrize("line", [
    "import domovoi.sdk",
    "from domovoi_plugin_imp import core",   # the in-process package
    "import domovoi_isolated",               # the core's helper is not the decoder's
    "import requests",
    "from .. import core",
    "import PIL",                            # not declared in isolated.debian
])
def test_the_decoder_may_import_only_stdlib_its_package_and_debian(tmp_path, line) -> None:
    root = make_plugin(tmp_path, "imp", decoder=f"{line}\n\ndef main(payload, meta):\n    return 1\n")
    errors = validate_plugin_dir(root, manifest_of(root))
    assert any("isolated" in e and "import" in e for e in errors), errors


def test_declared_debian_modules_and_relative_imports_are_fine(tmp_path) -> None:
    slug = "deb"
    toml = _toml(slug, isolated=f'[isolated]\ndecoder = "domovoi_isolated_{slug}.decode:main"\n'
                                'debian = ["python3-pil"]\n')
    root = make_plugin(tmp_path, slug, toml=toml,
                       decoder="import PIL.Image\nfrom . import helper\n\ndef main(p, m):\n    return 1\n")
    (root / f"domovoi_isolated_{slug}" / "helper.py").write_text("X = 1\n", encoding="utf-8")
    assert validate_plugin_dir(root, manifest_of(root)) == []


def test_the_decoder_package_is_python_only(tmp_path) -> None:
    root = make_plugin(tmp_path, "pyonly")
    (root / "domovoi_isolated_pyonly" / "native.so").write_bytes(b"\x7fELF")
    (root / "domovoi_isolated_pyonly" / "__pycache__").mkdir()
    (root / "domovoi_isolated_pyonly" / "__pycache__" / "decode.cpython-312.pyc").write_bytes(b"x")
    errors = validate_plugin_dir(root, manifest_of(root))
    assert errors == ["isolated: domovoi_isolated_pyonly/native.so is not a .py file; "
                      "the decoder's package is Python only"]


def test_a_missing_decoder_module_is_a_problem(tmp_path) -> None:
    root = make_plugin(tmp_path, "gone")
    (root / "domovoi_isolated_gone" / "decode.py").unlink()
    assert any("missing" in e for e in validate_plugin_dir(root, manifest_of(root)))


# ─── the image: core-built, pinned (D11) ─────────────────────────────────

def _names(context: bytes) -> list[str]:
    return tarfile.open(fileobj=io.BytesIO(context)).getnames()


def _member(context: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(context)) as tar:
        return tar.extractfile(name).read()


def test_the_context_is_the_core_s_files_and_the_decoder_package_only(tmp_path) -> None:
    root = make_plugin(tmp_path, "ctx")
    ctx = iso.context_tar(root, manifest_of(root))
    assert _names(ctx) == [
        "Dockerfile", "app/domovoi_isolated_runner.py", "app/domovoi_isolated.py",
        "app/domovoi_image_safety.py", "app/decoder.json",
        "app/domovoi_isolated_ctx/__init__.py", "app/domovoi_isolated_ctx/decode.py",
    ]
    assert not any("domovoi_plugin_ctx" in n for n in _names(ctx))   # no in-process code
    assert json.loads(_member(ctx, "app/decoder.json")) == {
        "module": "domovoi_isolated_ctx.decode", "function": "main"}
    assert _member(ctx, "app/domovoi_isolated_runner.py") == iso.RUNNER_SOURCE.read_bytes()
    assert _member(ctx, "app/domovoi_isolated.py") == iso.HELPER_SOURCE.read_bytes()
    assert _member(ctx, "app/domovoi_image_safety.py") == (REPO / "domovoi" / "image_safety.py").read_bytes()


def test_the_context_is_reproducible_and_its_hash_follows_the_files(tmp_path) -> None:
    root = make_plugin(tmp_path, "rep")
    m = manifest_of(root)
    a = iso.context_tar(root, m)
    assert a == iso.context_tar(root, m)
    (root / "domovoi_isolated_rep" / "decode.py").write_text(DECODER_OK + "\n# v2\n", encoding="utf-8")
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
    # apt runs before the plugin's files are copied; they are only compiled.
    runs = [ln for ln in text.splitlines() if ln.startswith(("RUN", "COPY"))]
    assert runs[0].startswith("RUN apt-get") and runs[1].startswith("COPY app/")
    assert runs[2] == f"RUN chmod -R a+rX,go-w {iso.APP_DIR} && python3 -m compileall -q {iso.APP_DIR}"


def test_only_allowlisted_debian_packages_reach_the_dockerfile(tmp_path) -> None:
    root = make_plugin(tmp_path, "allow")
    m = manifest_of(root)
    # Even a manifest object that skipped validation can't add a package.
    forged = SimpleNamespace(isolated=IsolatedDecl(m.isolated.decoder, ("python3-pil", "curl; rm -rf /")))
    text = iso.dockerfile(forged)
    assert "python3 python3-pil \\" in text and "curl" not in text


def test_build_args_pin_the_build_and_rerun_apt() -> None:
    sha = "a" * 64
    args = iso.build_args("fh", sha, "1.2.3")
    assert args[0] == "build" and "--no-cache" in args and "--pull" in args and args[-1] == "-"
    assert f"{iso.BUILD_LABEL}={sha}" in args and "org.domovoi.plugin=fh" in args
    assert "org.domovoi.plugin-version=1.2.3" in args
    assert args[args.index("-t") + 1] == f"{DECODER_CONTAINER_PREFIX}fh:latest"
    # Manifest strings go in only as validated values.
    assert "org.domovoi.plugin-version=unknown" in iso.build_args("fh", sha, "1 --network=host")
    with pytest.raises(iso.IsolatedBuildError):
        iso.build_args("fh --pull", sha, "1.0.0")
    with pytest.raises(iso.IsolatedBuildError):
        iso.build_args("fh", "abc", "1.0.0")


def test_the_image_is_rebuilt_when_the_files_change_or_it_is_old() -> None:
    now = datetime.now(timezone.utc)
    assert iso.ImageInfo("sha256:x", "a", now - timedelta(days=1)).stale("a", now=now) is None
    assert iso.ImageInfo("sha256:x", "b", now).stale("a", now=now) == "the plugin's decoder changed"
    assert "days old" in iso.ImageInfo("sha256:x", "a", now - timedelta(days=31)).stale("a", now=now)


# ─── the containers' flags (D11, D2) ─────────────────────────────────────

def _pairs(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_the_container_has_the_wall() -> None:
    args = iso.run_args("fh", 2, "sha256:img")
    assert args[:3] == ["run", "-i", "--rm"]
    assert _pairs(args, "--name") == [f"{DECODER_CONTAINER_PREFIX}fh-2"]
    assert _pairs(args, "--network") == ["none"]
    assert "--read-only" in args
    assert _pairs(args, "--cap-drop") == ["ALL"]
    assert _pairs(args, "--security-opt") == ["no-new-privileges"]
    assert _pairs(args, "--user") == ["65534:65534"]
    # Swap limited to the memory limit; per-container caps that add up.
    assert _pairs(args, "--memory") == ["128m"] and _pairs(args, "--memory-swap") == ["128m"]
    assert _pairs(args, "--pids-limit") == ["16"]
    assert _pairs(args, "--cpus") == ["0.25"]
    assert _pairs(args, "--ulimit") == ["nofile=64:64", "core=0"]
    assert _pairs(args, "--ipc") == ["none"]
    assert _pairs(args, "--log-driver") == ["none"]
    # No tmpfs, no mounts, nothing of the host or the core's environment.
    assert not {"--tmpfs", "-v", "--volume", "--mount", "-e", "--env", "--env-file", "--privileged",
                "--cap-add", "--device", "--pid", "-p", "--publish", "--add-host"} & set(args)
    assert args[-1] == "sha256:img"  # the exact image the core built, last: no command override
    assert iso.MAX_LIVE_TOTAL * 128 <= 1024   # all of them together stay within 1 GB


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

    def builds(self) -> int:
        return sum(1 for c in self.calls if c[0] == "build" and c[1] != "--help")

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
    fake.noted = {}
    fake.note_fails = False

    async def note(slug: str, image_id: str) -> None:
        if fake.note_fails:
            raise ConnectionError("no database")
        fake.noted[slug] = image_id

    async def record(slug: str):
        return fake.noted.get(slug)

    from domovoi.container_guard import engine

    monkeypatch.setattr(engine, "note_decoder_image", note)
    monkeypatch.setattr(engine, "decoder_image_record", record)
    yield fake
    iso._LIVE.clear()


async def test_ensure_image_builds_once_and_tells_the_guard_first(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "img")
    m = manifest_of(root)
    first = await iso.ensure_image("img", root, m)
    assert docker.noted == {"img": first}
    assert await iso.ensure_image("img", root, m) == first   # current and recorded: no rebuild
    assert docker.builds() == 1


async def test_an_image_the_guard_has_no_record_of_is_rebuilt_not_run(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "norec")
    m = manifest_of(root)
    first = await iso.ensure_image("norec", root, m)
    docker.noted.clear()                       # e.g. a record lost, or an image put there by hand
    second = await iso.ensure_image("norec", root, m)
    assert second != first and docker.noted == {"norec": second} and docker.builds() == 2


async def test_an_image_that_can_t_be_recorded_is_not_run(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "nonote")
    docker.note_fails = True
    with pytest.raises(DockerFailed, match="isn't run"):
        await iso.ensure_image("nonote", root, manifest_of(root))


async def test_an_upgrade_rebuilds_and_drops_the_old_image(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "upg")
    m = manifest_of(root)
    first = await iso.ensure_image("upg", root, m)
    (root / "domovoi_isolated_upg" / "decode.py").write_text(DECODER_OK + "# 1.1\n", encoding="utf-8")
    second = await iso.ensure_image("upg", root, m)
    assert second != first
    assert ("image", "rm", first) in docker.calls
    assert docker.noted["upg"] == second


async def test_a_failed_build_says_so(tmp_path, docker) -> None:
    root = make_plugin(tmp_path, "bad")
    docker.fail_build = True
    with pytest.raises(DockerFailed, match="Unable to locate package"):
        await iso.ensure_image("bad", root, manifest_of(root))


def test_the_build_is_memory_capped_and_low_priority() -> None:
    args = iso.build_args("fh", "a" * 64, "1.2.3")
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
    assert docker.builds() == 0


# ─── when the image may be built (D11) ───────────────────────────────────

@pytest.fixture
def gate_world(monkeypatch):
    from domovoi import egress
    from domovoi.config import settings

    world = SimpleNamespace(policy="ask", fh=True, apps=True)
    monkeypatch.setattr(egress, "policy", lambda: world.policy)
    monkeypatch.setattr(settings, "friend_houses_enabled", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")

    async def apps(slug):
        return world.apps

    monkeypatch.setattr(iso, "FRIEND_APPS_CHECK", apps)

    def set_fh(on: bool) -> None:
        monkeypatch.setattr(settings, "friend_houses_enabled", on, raising=False)

    world.set_fh = set_fh
    return world


async def test_the_gate(gate_world, monkeypatch) -> None:
    fh = parse_manifest(_toml("fh", perms='friend_houses = true\nwarnings = ["Cards"]'))
    generic = parse_manifest(_toml("gen"))
    assert await iso.default_gate("fh", fh) is None
    gate_world.apps = False
    assert "Apps with this friend" in await iso.default_gate("fh", fh)
    assert await iso.default_gate("gen", generic) is None       # no friend toggle for a generic decoder
    monkeypatch.setattr(iso, "FRIEND_APPS_CHECK", None)          # before the mail service: never
    assert "Apps with this friend" in await iso.default_gate("fh", fh)
    gate_world.set_fh(False)
    assert await iso.default_gate("fh", fh) == "Friend Houses is off"
    gate_world.policy = "never"
    assert "never" in await iso.default_gate("gen", generic)


async def test_nothing_is_built_while_the_gate_says_no(tmp_path, docker, gate_world) -> None:
    root = make_plugin(tmp_path, "gated", toml=_toml("gated", perms='friend_houses = true\nwarnings = ["Cards"]'))
    gate_world.apps = False
    pool = iso.DecoderPool("gated", install_dir=root, manifest=manifest_of(root))
    r = await pool.decode("friend-a", b"1")
    assert r.outcome == "unavailable" and r.retry
    await pool._build_task
    assert pool.image_state == "waiting" and "Apps with this friend" in pool.problem
    assert docker.builds() == 0 and not docker.calls
    gate_world.apps = True
    pool._build_retry_at = 0.0
    await pool.decode("friend-a", b"1")
    await pool._build_task
    assert pool.image_state == "ready" and docker.builds() == 1
    await pool.stop()


# ─── the IPC under a hostile decoder ─────────────────────────────────────

def _frame(rid: int, status: int, data: bytes) -> bytes:
    body = struct.pack(">IB", rid, status) + data
    return struct.pack(">I", len(body)) + body


class FakeProc:
    """A decoder process. ``answer(rid, kind, meta, payload)`` returns the
    raw bytes it writes back, ``None`` to hang, or ``"crash"``."""

    def __init__(self, answer, argv=None) -> None:
        self.answer = answer
        self.argv = argv
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(iso.READY_BYTE)   # the runner says it is up (M3)
        self.returncode = None
        self.killed = False
        self.stdin = self
        self.closed = False
        self.received: list[bytes] = []

    # stdin
    def write(self, data: bytes) -> None:
        self.received.append(data)
        (size,) = struct.unpack(">I", data[:4])
        body = data[4:]
        assert len(body) == size
        rid, kind, mlen = struct.unpack(">IBH", body[:7])
        meta = json.loads(body[7:7 + mlen]) if mlen else {}
        out = self.answer(rid, kind, meta, body[7 + mlen:])
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


async def _no_gate(slug, manifest):
    return None


async def _pool(answer, *, docker: FakeDocker, timeout: float = 0.5, clock=None, slug="hostile",
                schema=SCHEMA_ANY):
    procs: list[FakeProc] = []

    async def spawn(argv):
        p = FakeProc(answer, argv)
        procs.append(p)
        return p

    async def prepare() -> str:
        return "sha256:img"

    pool = iso.DecoderPool(slug, install_dir=Path("."), manifest=None, spawn=spawn, prepare=prepare,
                           gate=_no_gate, timeout=timeout, image_timeout=timeout, clock=clock or Clock(),
                           wall=clock or Clock(), host_event_sec=0.01)
    await pool.prepare()
    if schema is not None:
        pool.set_schema(schema)
    return pool, procs


def _ok(value) -> callable:
    return lambda rid, kind, meta, payload: _frame(rid, 0, json.dumps(value).encode())


async def test_a_round_trip_is_reserialized_by_the_core(docker) -> None:
    pool, procs = await _pool(lambda rid, k, meta, payload: _frame(
        rid, 0, b'{ "b" : [1, 2.5, "x"],\n "a": {"n": ' + payload + b'} }'), docker=docker)
    r = await pool.decode("friend-a", b"7", {"kind": "move"})
    assert r.ok and r.value == {"a": {"n": 7}, "b": [1, 2.5, "x"]} and r.outcome == "ok"
    assert r.canonical == b'{"a":{"n":7},"b":[1,2.5,"x"]}'
    assert len(procs) == 1 and not procs[0].killed
    await pool.stop()


async def test_the_friend_never_enters_the_container(docker) -> None:
    pool, procs = await _pool(_ok(1), docker=docker)
    assert (await pool.decode("friend-secret-id", b"1")).ok
    assert all(b"friend-secret-id" not in d for d in procs[0].received)
    assert not any("friend-secret-id" in a for a in procs[0].argv)
    for key in ("house_id", "friend"):
        r = await pool.decode("friend-secret-id", b"1", {key: "x"})
        assert r.outcome == "refused" and "friend" in r.reason
    await pool.stop()


async def test_the_decoder_s_own_refusal_is_a_refused_item_not_a_fault(docker) -> None:
    pool, procs = await _pool(lambda rid, k, m, p: _frame(rid, 1, b"ValueError: bad move \x1b[31m<b>"),
                              docker=docker)
    r = await pool.decode("f", b"x")
    assert r.outcome == "refused" and "bad move" in r.reason
    assert "\x1b" not in r.reason and "<" not in r.reason          # cleaned (D9)
    assert len(procs) == 1 and not procs[0].killed
    await pool.stop()


@pytest.mark.parametrize("data, why", [
    (b'{"a": NaN}', "NaN"),
    (b'{"a": Infinity}', "Infinity"),
    (b'{"a": 1e999}', "out of range"),
    (b'{"a": 123456789012345678901234567890}', "out of range"),
    (b'{"a": 9007199254740992}', "out of range"),                      # 2**53
    (b"[" * 17 + b"]" * 17, "deeper"),
    (b"[" * 30000 + b"]" * 30000, "deeper"),
    (b'{"a": 1, "a": 2}', "duplicate"),
    (b"\xff\xfe not utf-8", "utf-8"),
    (b'"\\ud800"', "surrogate"),
    (b'"a\\u0000b"', "NUL"),
    (b'{"k\\u0007": 1}', "control"),
    (b'"\\u009b"', "control"),
    (b'"' + b"x" * 2049 + b'"', "longer than"),
    (b'{"' + b"k" * 129 + b'": 1}', "longer than"),
    (b"{" + b",".join(b'"k%d":1' % i for i in range(257)) + b"}", "more than 256 keys"),
    (b"[" + b",".join([b"[1,2,3,4]"] * 900) + b"]", "values"),
    (b"garbage", "Expecting value"),
    (b'{"a": 1} trailing', "Extra data"),
    # Within the frame cap, but past the node cap long before the parser's recursion.
    (b"[" + b"1e5," * 13000 + b"1e5]", "values"),
], ids=["nan", "inf", "1e999", "bigint", "2pow53", "depth17", "depth30000", "dupkey",
        "utf8", "surrogate", "nul", "ctrl-key", "c1", "longstring", "longkey", "manykeys", "nodes",
        "garbage", "trailing", "grows"])
async def test_bad_json_is_refused_and_the_stream_stays_up(docker, data, why) -> None:
    pool, procs = await _pool(lambda rid, k, m, p: _frame(rid, 0, data), docker=docker)
    r = await pool.decode("f", b"x")
    assert r.outcome == "refused" and r.reason.startswith("bad output"), r
    assert why.lower() in r.reason.lower(), r.reason
    assert len(procs) == 1 and not procs[0].killed
    await pool.stop()


def test_sixteen_levels_and_ordinary_text_are_fine() -> None:
    value, _ = iso.canonical(b"[" * 16 + b"]" * 16)
    assert value == [[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]
    # A bracket inside a string isn't nesting; tabs, newlines and non-ASCII are text.
    assert iso.canonical(b'"' + b"[" * 100 + b'"')[0] == "[" * 100
    assert iso.canonical('"Q♠\\n\\tok"'.encode())[0] == "Q♠\n\tok"


async def test_the_schema_is_checked_in_process(docker) -> None:
    schema = {"type": "object", "required": ["card"],
              "properties": {"card": {"type": "string", "maxLength": 3},
                             "seat": {"type": "integer", "minimum": 0, "maximum": 3}}}
    answers = iter([b'{"card": "Q"}', b'{"card": "Q", "seat": 9}', b'{"card": "Q", "x": 1}', b'{"seat": 1}',
                    b'{"card": "Q", "seat": true}'])
    pool, _ = await _pool(lambda rid, k, m, p: _frame(rid, 0, next(answers)), docker=docker, schema=schema)
    assert (await pool.decode("f", b"x")).value == {"card": "Q"}
    for why in ("maximum", "doesn't name", "lacks", "not an integer"):
        r = await pool.decode("f", b"x")
        assert r.outcome == "refused" and why in r.reason, r.reason
    await pool.stop()


async def test_without_a_schema_nothing_is_accepted(docker) -> None:
    # M4: no schema registered is not the friend's fault: the item waits
    # ("unavailable", weight 0), it is never refused and deleted, and no
    # container is started for it.
    pool, procs = await _pool(_ok({"a": 1}), docker=docker, schema=None)
    r = await pool.decode("f", b"x")
    assert r.outcome == "unavailable" and r.retry and "no schema" in r.reason
    assert procs == [] and not pool.friends.get("f", iso._Friend()).breaker.events
    await pool.stop()


@pytest.mark.parametrize("schema", [
    {"type": "string", "pattern": "^a"},        # a keyword the core doesn't enforce
    {"type": "thing"},
    {"properties": {}},                          # no type
    {"type": "array", "maxItems": -1},
    {"type": "object", "additionalProperties": "yes"},
    "not a schema",
])
def test_a_schema_the_core_can_t_enforce_is_refused(schema) -> None:
    with pytest.raises(iso.SchemaError):
        iso.compile_schema(schema)


@pytest.mark.parametrize("answer, why", [
    (lambda rid, k, m, p: None, "timed out"),                                     # hangs
    (lambda rid, k, m, p: "crash", "stopped"),                                    # crashes
    (lambda rid, k, m, p: struct.pack(">I", iso.REPLY_FRAME_MAX + 1) + b"x" * 10, "bad size"),
    (lambda rid, k, m, p: struct.pack(">I", 2) + b"xx", "bad size"),              # undersize frame
    (lambda rid, k, m, p: _frame(rid + 7, 0, b"1"), "another request"),           # wrong id
    (lambda rid, k, m, p: _frame(rid, 9, b"1"), "bad status"),
    (lambda rid, k, m, p: _frame(rid, 2, b"\x00\x01\x00\x01\x03abc"), "bad status"),   # pixels, unasked
    (lambda rid, k, m, p: b"\x00\x00", "timed out"),                              # a torn header
], ids=["hang", "crash", "oversize-frame", "undersize-frame", "wrong-id", "bad-status", "pixels-for-json",
        "torn"])
async def test_transport_faults_kill_that_friend_s_container_and_back_off(docker, answer, why) -> None:
    clock = Clock()
    pool, procs = await _pool(answer, docker=docker, timeout=0.2, clock=clock)
    r = await pool.decode("f", b"x")
    assert r.outcome == "fault" and r.retry and why in r.reason, r.reason
    assert procs[0].closed and procs[0].returncode is not None and "f" not in pool.instances
    # The container is removed too (killing the docker client doesn't stop it).
    assert ("rm", "-f", f"{DECODER_CONTAINER_PREFIX}hostile-0") in docker.calls
    # In backoff: not tried, no new container.
    r2 = await pool.decode("f", b"x")
    assert r2.outcome == "unavailable" and "restarts in" in r2.reason and len(procs) == 1
    # After the backoff: a new container, and a good answer resets it.
    for p in procs:
        p.answer = _ok({"fine": True})
    pool._spawn = _respawn(procs, _ok({"fine": True}))
    clock.t += iso.BACKOFF_FIRST_SEC + 0.01
    r3 = await pool.decode("f", b"x")
    assert r3.ok and r3.value == {"fine": True} and len(procs) == 2
    assert pool.friends["f"].backoff == iso.BACKOFF_FIRST_SEC and pool.restarts == 1
    await pool.stop()


def _respawn(procs, answer):
    async def spawn(argv):
        p = FakeProc(answer, argv)
        procs.append(p)
        return p
    return spawn


async def test_a_byte_written_unasked_kills_the_container(docker) -> None:
    pool, procs = await _pool(_ok(1), docker=docker)
    assert (await pool.decode("f", b"x")).ok
    procs[0].stdout.feed_data(b"\x00")          # between requests
    for _ in range(100):
        if procs[0].killed and "f" not in pool.instances:
            break
        await asyncio.sleep(0)
    assert procs[0].killed and "f" not in pool.instances and pool.restarts == 1
    await pool.stop()


async def test_backoff_doubles_up_to_its_cap_for_that_friend_only(docker) -> None:
    clock = Clock()
    pool, procs = await _pool(lambda rid, k, m, p: "crash" if p == b"bad" else _frame(rid, 0, b"1"),
                              docker=docker, clock=clock)
    waits = []
    for _ in range(9):
        st = pool.friends.get("a")
        if st is not None:
            st.breaker.resume()                  # the breaker has its own test
        await pool.decode("a", b"bad")
        waits.append(pool.friends["a"].retry_at - clock.t)
        clock.t = pool.friends["a"].retry_at
    assert waits[:4] == [1.0, 2.0, 4.0, 8.0] and waits[-1] == iso.BACKOFF_MAX_SEC
    # Another friend never waited.
    assert (await pool.decode("b", b"good")).ok
    await pool.stop()


async def test_requests_past_the_caps_never_reach_the_decoder(docker) -> None:
    seen = []
    pool, _ = await _pool(lambda rid, k, m, p: seen.append(p) or _frame(rid, 0, b"1"), docker=docker)
    assert (await pool.decode("f", b"x" * (iso.PAYLOAD_MAX + 1))).outcome == "refused"
    assert (await pool.decode("f", b"x", {"k": "v" * iso.META_MAX})).outcome == "refused"
    assert (await pool.decode("f", b"x", {"k": float("nan")})).outcome == "refused"
    assert (await pool.decode("f", "not bytes")).outcome == "refused"
    assert (await pool.decode("", b"x")).outcome == "refused"
    assert (await pool.decode_image("f", b"")).outcome == "refused"
    assert seen == []
    await pool.stop()


async def test_a_decoder_that_could_not_be_built_refuses_everything(tmp_path, docker) -> None:
    async def prepare() -> str:
        raise DockerFailed("building the x decoder failed")

    pool = iso.DecoderPool("x", install_dir=tmp_path, manifest=None, prepare=prepare, gate=_no_gate)
    await pool.prepare()
    assert pool.image_state == "failed" and "failed" in pool.problem
    r = await pool.decode("f", b"x")
    assert r.outcome == "unavailable" and "failed" in r.reason
    await pool.stop()


# ─── one container per friend (D2) ───────────────────────────────────────

async def test_each_friend_gets_its_own_container(docker) -> None:
    pool, procs = await _pool(lambda rid, k, m, p: _frame(rid, 0, p), docker=docker)
    assert (await pool.decode("a", b"1")).value == 1
    assert (await pool.decode("b", b"2")).value == 2
    assert (await pool.decode("a", b"3")).value == 3
    assert len(procs) == 2
    assert [p.argv[p.argv.index("--name") + 1] for p in procs] == [
        f"{DECODER_CONTAINER_PREFIX}hostile-0", f"{DECODER_CONTAINER_PREFIX}hostile-1"]
    # A's bytes went only to A's container, B's only to B's.
    assert [d[-1:] for d in procs[0].received] == [b"1", b"3"]
    assert [d[-1:] for d in procs[1].received] == [b"2"]
    await pool.stop()


async def test_a_compromised_friend_container_never_sees_another_friend(docker) -> None:
    # Friend A's item "exploits" its container, which from then on answers
    # anything it likes. B's items never pass through it.
    pool, procs = await _pool(_ok("forged"), docker=docker)
    assert (await pool.decode("a", b"exploit")).value == "forged"
    pool._spawn = _respawn(procs, _ok("honest"))
    assert (await pool.decode("b", b"b-move")).value == "honest"
    assert (await pool.decode("a", b"again")).value == "forged"
    assert len(procs[0].received) == 2 and not any(b"b-move" in d for d in procs[0].received)
    assert procs[1].received[0].endswith(b"b-move")
    await pool.stop()


async def test_the_live_caps_evict_the_least_recently_used_idle_container(docker) -> None:
    clock = Clock()
    pool, procs = await _pool(_ok(1), docker=docker, clock=clock)
    for i in range(iso.MAX_LIVE_PER_PLUGIN):
        clock.t += 1
        assert (await pool.decode(f"f{i}", b"x")).ok
    assert len(pool.instances) == iso.MAX_LIVE_PER_PLUGIN
    clock.t += 1
    await pool.decode("f0", b"x")                 # f1 is now the least recently used
    clock.t += 1
    assert (await pool.decode("new", b"x")).ok
    assert "f1" not in pool.instances and "new" in pool.instances and procs[1].killed
    assert len(pool.instances) == iso.MAX_LIVE_PER_PLUGIN
    # The evicted friend's next item just starts a container again: no fault, no backoff.
    assert pool.restarts == 0 and pool.friends["f1"].retry_at == 0.0
    await pool.stop()


async def test_the_total_cap_spans_plugins(docker, monkeypatch) -> None:
    monkeypatch.setattr(iso, "MAX_LIVE_TOTAL", 3)
    clock = Clock()
    one, procs_one = await _pool(_ok(1), docker=docker, clock=clock, slug="one")
    two, _ = await _pool(_ok(2), docker=docker, clock=clock, slug="two")
    for f in ("a", "b"):
        clock.t += 1
        await one.decode(f, b"x")
    clock.t += 1
    await two.decode("c", b"x")
    clock.t += 1
    assert (await two.decode("d", b"x")).ok
    assert len(iso._LIVE) == 3 and "a" not in one.instances and procs_one[0].killed
    await one.stop()
    await two.stop()
    assert iso._LIVE == []


async def test_when_every_container_is_busy_the_item_waits(docker, monkeypatch) -> None:
    monkeypatch.setattr(iso, "MAX_LIVE_PER_PLUGIN", 1)
    pool, _ = await _pool(lambda rid, k, m, p: None if p == b"slow" else _frame(rid, 0, b"1"),
                          docker=docker, timeout=0.3)
    slow = asyncio.ensure_future(pool.decode("a", b"slow"))
    await asyncio.sleep(0.05)
    r = await pool.decode("b", b"x")
    assert r.outcome == "unavailable" and "busy" in r.reason
    await slow
    await pool.stop()


async def test_idle_containers_are_stopped(docker) -> None:
    clock = Clock()
    pool, procs = await _pool(_ok(1), docker=docker, clock=clock)
    await pool.decode("a", b"x")
    clock.t += 60
    await pool.decode("b", b"x")
    clock.t += iso.IDLE_SEC - 59
    assert await pool.reap_idle() == 1
    assert "a" not in pool.instances and "b" in pool.instances and procs[0].killed and not procs[1].killed
    await pool.stop()
    assert procs[1].killed and not pool.instances


# ─── faults: per item and per friend (D3) ────────────────────────────────

async def test_an_item_that_keeps_faulting_is_dropped(docker) -> None:
    clock = Clock()
    pool, _ = await _pool(lambda rid, k, m, p: "crash", docker=docker, clock=clock)
    outcomes = []
    for _ in range(3):
        outcomes.append((await pool.decode("f", b"bomb", item_key="mail-7")).outcome)
        clock.t += iso.BACKOFF_MAX_SEC
    assert outcomes == ["fault", "fault", "dropped"]
    await pool.stop()


async def test_a_friend_that_keeps_faulting_is_paused_and_others_carry_on(docker) -> None:
    clock = Clock()
    told = []
    iso.PAUSE_LISTENERS.append(lambda *a: told.append(a))
    try:
        pool, _ = await _pool(lambda rid, k, m, p: None if p == b"bomb" else _frame(rid, 0, b"1"),
                              docker=docker, clock=clock, timeout=0.05)
        # Weight 100 each (M4): three timeouts in flight on a ready container.
        for i in range(3):
            assert (await pool.decode("bad", b"bomb", item_key=f"m{i}")).outcome == "fault"
            clock.t += iso.BACKOFF_MAX_SEC
        assert pool.paused("bad") and told == [("hostile", "bad", 3600, "faults", False)]
        r = await pool.decode("bad", b"fine")
        assert r.outcome == "unavailable" and "paused" in r.reason
        assert (await pool.decode("good", b"fine")).ok          # every other friend carries on
        clock.t += iso.BREAKER_PAUSE_SEC + 1
        assert not pool.paused("bad") and (await pool.decode("bad", b"fine")).ok
        # A repeat pauses for twice as long; an admin's Resume lifts it and
        # starts the length over (L11).
        for i in range(3):
            await pool.decode("bad", b"bomb", item_key=f"n{i}")
            clock.t += iso.BACKOFF_MAX_SEC
        assert told[-1][2] == 7200
        assert pool.friends["bad"].breaker.paused_until - clock.t > iso.BREAKER_PAUSE_SEC
        pool.resume("bad")
        assert pool.friends["bad"].breaker.next_pause == iso.BREAKER_PAUSE_SEC
        assert (await pool.decode("bad", b"fine")).ok
        await pool.stop()
    finally:
        iso.PAUSE_LISTENERS.clear()


# ─── the real runner, in a subprocess ────────────────────────────────────

def _unpack(root: Path, dest: Path) -> Path:
    with tarfile.open(fileobj=io.BytesIO(iso.context_tar(root, manifest_of(root)))) as tar:
        for member in tar.getmembers():
            target = dest / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
    return dest / "app"


async def _real_pool(tmp_path, slug: str, timeout: float = 20.0):
    """The context's own runner, helpers, decoder.json and package,
    unpacked and run as a process (what the container runs, minus Docker)."""
    root = make_plugin(tmp_path, slug)
    app = _unpack(root, tmp_path / "unpacked")

    async def spawn(argv):
        return await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-B", str(app / iso.RUNNER_NAME),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=tmp_path,
        )

    async def prepare() -> str:
        return "sha256:img"

    pool = iso.DecoderPool(slug, install_dir=root, manifest=manifest_of(root), spawn=spawn,
                           prepare=prepare, gate=_no_gate, timeout=timeout, image_timeout=timeout)
    await pool.prepare()
    pool.set_schema(SCHEMA_ANY)
    return pool, app


async def test_the_real_runner_round_trip_in_a_subprocess(tmp_path, docker) -> None:
    pool, app = await _real_pool(tmp_path, "real")
    try:
        r = await pool.decode("a", json.dumps({"card": "Q♠"}).encode(), {"kind": "move"})
        assert r.ok and r.value == {"got": {"card": "Q♠"}, "meta": ["kind"]}, r
        r = await pool.decode("a", zlib.compress(b'"hi"'), {"z": True})
        assert r.ok and r.value == {"got": "hi", "meta": ["z"]}
        r = await pool.decode("a", b'"boom"')
        assert r.outcome == "refused" and "no booms" in r.reason and "<" not in r.reason
        r = await pool.decode("a", b'"print"')   # a print() goes to stderr, not into the replies
        assert r.ok and r.value == {"printed": True}
        r = await pool.decode("a", b"not json")
        assert r.outcome == "refused" and "JSONDecodeError" in r.reason
        assert pool.restarts == 0
        # The in-process package was never copied.
        assert not (app / "domovoi_plugin_real").exists()
    finally:
        await pool.stop()


# ─── lifecycle ───────────────────────────────────────────────────────────

@pytest.fixture
def spawned(monkeypatch) -> list[FakeProc]:
    out: list[FakeProc] = []

    async def spawn(argv):
        p = FakeProc(_ok({"ok": 1}), argv)
        out.append(p)
        return p

    monkeypatch.setattr(iso, "_spawn_docker", spawn)
    monkeypatch.setattr(iso, "default_gate", _no_gate)
    yield out
    iso.POOLS.clear()


async def test_start_stop_forget(tmp_path, docker, spawned, monkeypatch) -> None:
    root = make_plugin(tmp_path, "life")
    m = manifest_of(root)
    pool = iso.start_for("life", root, m)
    assert pool is iso.POOLS["life"] and pool.image_state == "none"
    assert not docker.calls and not spawned          # nothing built or run at load
    monkeypatch.setattr(pool, "_gate", _no_gate)
    r = await iso.decode("life", "a", b"x")
    assert r.outcome == "unavailable"                # the first item starts the build
    await pool._build_task
    iso.set_schema("life", SCHEMA_ANY)
    assert (await iso.decode("life", "a", b"x")).value == {"ok": 1}
    image_id = docker.images[iso.image_tag("life")][0]
    assert spawned[0].argv == iso.run_args("life", 0, image_id)
    # Disable: stopped and its container removed.
    await iso.stop_for("life")
    assert "life" not in iso.POOLS and spawned[0].killed and spawned[0].closed
    assert ("rm", "-f", f"{DECODER_CONTAINER_PREFIX}life-0") in docker.calls
    assert (await iso.decode("life", "a", b"x")).outcome == "unavailable"
    # Uninstall: every slot's container and the image go.
    await iso.forget("life")
    assert iso.image_tag("life") not in docker.images
    for name in iso.container_names("life"):
        assert ("rm", "-f", name) in docker.calls


def test_a_plugin_without_isolated_gets_no_decoder(tmp_path) -> None:
    m = parse_manifest(_toml("plain", isolated=""))
    assert iso.start_for("plain", tmp_path, m) is None and "plain" not in iso.POOLS


async def test_the_loader_registers_on_load_and_stops_on_unload(tmp_path, docker, spawned) -> None:
    from domovoi.plugins_runtime.loader import LOADER

    bootstrap.register_nvidia_dlls()
    slug = "isolife"
    root = make_plugin(tmp_path, slug)

    async def load():
        await LOADER.load_plugin(slug=slug, install_dir=root, manifest=manifest_of(root),
                                 foreign_corpus=[], foreign_web_routes=[], update_registry_status=False)

    await load()
    try:
        pool = iso.POOLS[slug]
        pool._gate = _no_gate
        await pool.prepare()
        pool.set_schema(SCHEMA_ANY)
        assert (await pool.decode("a", b"x")).ok
        first = pool.image_id
        await LOADER.unload_plugin(slug)
        assert slug not in iso.POOLS and spawned[-1].killed
        # An upgrade swaps the files; the next load's first build rebuilds the image.
        (root / f"domovoi_isolated_{slug}" / "decode.py").write_text(DECODER_OK + "# 1.1\n", encoding="utf-8")
        await load()
        pool = iso.POOLS[slug]
        pool._gate = _no_gate
        await pool.prepare()
        assert pool.image_id != first and ("image", "rm", first) in docker.calls
    finally:
        await LOADER.unload_plugin(slug)


# ─── the container guard (D11) ───────────────────────────────────────────

def _row(slug: str, *, isolated: bool = True, status: str = "ok"):
    manifest = {"plugin": {"slug": slug}}
    if isolated:
        manifest["isolated"] = {"decoder": f"domovoi_isolated_{slug}.decode:main"}
    return SimpleNamespace(slug=slug, name=slug.title(), status=status, enabled=True, manifest=manifest)


def test_the_guard_expects_each_slot_on_the_image_the_core_built() -> None:
    rows = [_row("games"), _row("plain", isolated=False), _row("gone", status="uninstalled")]
    out = gx.decoder_expectations(rows, {"games": "sha256:built"})
    assert [e.name for e in out] == iso.container_names("games") and len(out) == DECODER_SLOTS
    assert iso.MAX_LIVE_PER_PLUGIN == DECODER_SLOTS
    for e in out:
        assert (e.owner, e.role, e.state, e.image_id, e.required) == (
            "core", "plugin-decoder", STOPPED, "sha256:built", False)
        assert e.image_ref == iso.image_tag("games")


def _view(name: str, image_id: str, config_image: str = "sha256:" + "1" * 64) -> DockerView:
    o = Observed(id="c" * 64, name=name, status="running", running=True,
                 config_image=config_image, image_id=image_id)
    v = DockerView(ok=True)
    v.listed[o.name] = Listed(id=o.id, name=o.name, compose_project="", compose_service="")
    v.containers[o.name] = o
    return v


def test_the_guard_reports_a_decoder_running_another_image() -> None:
    exp = gx.ExpectedSet()
    exp.add(gx.decoder_expectations([_row("games")], {"games": "sha256:" + "1" * 64}))
    name = iso.container_name("games", 1)
    now = 1_800_000_000.0
    assert detect(exp, _view(name, "sha256:" + "1" * 64), now_wall=now, boot_wall=now - 3600).findings == []
    swapped = detect(exp, _view(name, "sha256:" + "2" * 64), now_wall=now, boot_wall=now - 3600)
    assert [(f.cls, f.kind, f.container) for f in swapped.findings] == [(TAMPER, "image", name)]


@pytest.mark.parametrize("name", [
    f"{DECODER_CONTAINER_PREFIX}games-{DECODER_SLOTS}",       # past the slots
    f"{DECODER_CONTAINER_PREFIX}games",                       # the old single name
    f"{DECODER_CONTAINER_PREFIX}notinstalled-0",
])
def test_an_unexpected_decoder_container_is_tampering(name) -> None:
    exp = gx.ExpectedSet()
    exp.add(gx.decoder_expectations([_row("games")], {"games": "sha256:" + "1" * 64}))
    now = 1_800_000_000.0
    det = detect(exp, _view(name, "sha256:" + "1" * 64), now_wall=now, boot_wall=now - 3600)
    assert [(f.cls, f.kind, f.container) for f in det.findings] == [(TAMPER, "unknown", name)]


def test_an_installer_hold_holds_the_decoder() -> None:
    from domovoi.container_guard import holds

    holds.hold("plugin:games", 60, "upgrading")
    try:
        assert {e.hold for e in gx.decoder_expectations([_row("games")], {})} == {"upgrading"}
    finally:
        holds.clear()


# ─── images: raw pixels out, the core's PNG (D1, D10) ────────────────────

PIL = pytest.importorskip("PIL")
from PIL import Image as PILImage  # noqa: E402
from PIL import PngImagePlugin  # noqa: E402

from domovoi.plugins_runtime import isolated_helper as helper  # noqa: E402


@pytest.fixture
def safety_module(monkeypatch):
    """The helper imports the core's image_safety under its in-image name."""
    from domovoi import image_safety

    monkeypatch.setitem(sys.modules, "domovoi_image_safety", image_safety)


def _img(fmt: str, size=(64, 48), mode="RGB", **save) -> bytes:
    color = {"RGB": (200, 30, 40), "RGBA": (200, 30, 40, 120), "L": 90}[mode]
    im = PILImage.new(mode, size, color)
    for x in range(0, size[0], 4):
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
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def _bomb_png(width: int, height: int) -> bytes:
    """A small file that declares a huge image (the pixels are never read)."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 4096, 9)
    return iso.PNG_MAGIC + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _chunks(png: bytes) -> list[bytes]:
    pos, out = 8, []
    while pos < len(png):
        (n,) = struct.unpack(">I", png[pos:pos + 4])
        out.append(png[pos + 4:pos + 8])
        pos += 12 + n
    return out


# The helper (it runs inside the container; here, in process).

@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_only_pixels_come_out_of_the_container(fmt, safety_module) -> None:
    extra = {"exif": _gps_exif()}
    if fmt == "PNG":
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "SecretText")
        extra.update(pnginfo=info, icc_profile=b"SecretIcc" * 20)
    src = _img(fmt, **extra)
    w, h, c, pixels = helper.decode_pixels(src)
    assert (w, h, c) == (64, 48, 3) and len(pixels) == w * h * c
    png = iso.encode_png(w, h, c, pixels)
    for marker in (b"SecretCam", b"SecretSoftware", b"SecretText", b"SecretIcc", b"Exif", b"GPS"):
        assert marker not in png
    # no iCCP, eXIf, tEXt: nothing but the pixels (and their palette when few colours)
    assert _chunks(png) in ([b"IHDR", b"IDAT", b"IEND"], [b"IHDR", b"PLTE", b"IDAT", b"IEND"])
    with PILImage.open(io.BytesIO(png)) as im:
        assert im.size == (64, 48) and "icc_profile" not in im.info and dict(im.getexif()) == {}


def test_the_core_s_png_is_the_same_picture(safety_module) -> None:
    src = _img("PNG", mode="RGBA")
    w, h, c, pixels = helper.decode_pixels(src)
    assert c == 4
    with PILImage.open(io.BytesIO(iso.encode_png(w, h, c, pixels))) as a, PILImage.open(io.BytesIO(src)) as b:
        assert a.convert("RGBA").tobytes() == b.convert("RGBA").tobytes()


def test_the_orientation_is_applied(safety_module) -> None:
    exif = PILImage.Exif()
    exif[0x0112] = 6                     # rotate 90 on display
    w, h, _, _ = helper.decode_pixels(_img("JPEG", size=(40, 20), exif=exif.tobytes()))
    assert (w, h) == (20, 40)


def test_a_big_image_is_scaled_to_the_side_cap(safety_module) -> None:
    w, h, _, _ = helper.decode_pixels(_img("PNG", size=(2000, 800)))
    assert (w, h) == (iso.IMAGE_MAX_SIDE, 307) and helper.MAX_SIDE == iso.IMAGE_MAX_SIDE


def test_an_animation_gives_its_first_frame(safety_module) -> None:
    frames = [PILImage.new("RGB", (16, 16), c) for c in ((255, 0, 0), (0, 255, 0))]
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], lossless=True)
    w, h, c, pixels = helper.decode_pixels(buf.getvalue())
    assert (w, h) == (16, 16) and pixels[:3] == b"\xff\x00\x00"


@pytest.mark.parametrize("data, why", [
    (_bomb_png(20000, 20000), "too large"),          # a decompression bomb: refused before decoding
    (_bomb_png(2001, 2000), "too large"),            # just over the 4-megapixel cap
    (b"GIF89a" + b"\x00" * 40, "not a PNG, JPEG or WebP"),
    (b"BM" + b"\x00" * 60, "not a PNG, JPEG or WebP"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "not a PNG, JPEG or WebP"),
    (b"%!PS-Adobe-3.0 EPSF-3.0\n", "not a PNG, JPEG or WebP"),
    (b"", "not a PNG, JPEG or WebP"),
], ids=["bomb", "over-4mp", "gif", "bmp", "svg", "eps", "empty"])
def test_hostile_images_are_refused(data, why, safety_module) -> None:
    with pytest.raises(helper.ImageRefused, match=why):
        helper.decode_pixels(data)


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_truncated_images_are_refused(fmt, safety_module) -> None:
    data = _img(fmt, size=(300, 200))
    with pytest.raises(helper.ImageRefused):
        helper.decode_pixels(data[: len(data) // 2])


def test_the_caps_match_across_the_wall() -> None:
    import domovoi.plugins_runtime.isolated_runner as runner

    assert helper.MAX_SIDE == iso.IMAGE_MAX_SIDE
    assert helper.MAX_PIXELS * 4 <= 32 * 1024 * 1024     # sized to the 128 MB container
    assert runner.REQUEST_MAX == iso.PAYLOAD_MAX + iso.META_MAX + 7 and runner.REPLY_MAX == iso.REPLY_MAX
    # The largest image the core encodes stays under its output cap.
    side = iso.IMAGE_MAX_SIDE
    noisy = os.urandom(side * side * 4)
    assert len(iso.encode_png(side, side, 4, noisy)) <= iso.IMAGE_OUT_MAX


# The core's checks on what comes back (a hostile container).

@pytest.mark.parametrize("data", [
    b"",
    struct.pack(">HHB", 0, 10, 3),
    struct.pack(">HHB", iso.IMAGE_MAX_SIDE + 1, 1, 3) + b"\x00" * (3 * (iso.IMAGE_MAX_SIDE + 1)),
    struct.pack(">HHB", 2, 2, 2) + b"\x00" * 8,                  # grey+alpha: not RGB or RGBA
    struct.pack(">HHB", 2, 2, 3) + b"\x00" * 11,                 # one byte short
    struct.pack(">HHB", 2, 2, 3) + b"\x00" * 13,                 # one byte over
], ids=["empty", "zero", "wide", "la", "short", "long"])
def test_the_core_refuses_anything_but_exact_pixels(data) -> None:
    with pytest.raises(ValueError):
        iso.check_pixels(data)


async def test_an_image_reply_reaches_the_caller_as_the_core_s_png(docker) -> None:
    pixels = bytes(range(2 * 3 * 3))
    pool, _ = await _pool(lambda rid, k, m, p: _frame(rid, 2, struct.pack(">HHB", 2, 3, 3) + pixels),
                          docker=docker)
    r = await pool.decode_image("f", b"friend bytes")
    assert r.ok and r.media_type == "image/png" and r.image.startswith(iso.PNG_MAGIC)
    assert r.image == iso.encode_png(2, 3, 3, pixels) and b"friend bytes" not in r.image
    assert r.value == {"image": {"format": "png", "width": 2, "height": 3, "bytes": len(r.image)}}
    await pool.stop()


async def test_an_image_reply_that_doesn_t_add_up_is_refused_and_the_stream_stays_up(docker) -> None:
    pool, procs = await _pool(lambda rid, k, m, p: _frame(rid, 2, struct.pack(">HHB", 2, 3, 3) + b"x"),
                              docker=docker)
    r = await pool.decode_image("f", b"x")
    assert r.outcome == "refused" and r.reason.startswith("bad image") and r.image is None
    assert not procs[0].killed
    await pool.stop()


async def test_frame_caps_follow_the_request_kind(docker) -> None:
    # A JSON request may not use the image allowance, and an image request
    # may not get JSON back.
    big = struct.pack(">HHB", 100, 100, 4) + b"\x00" * 40000
    pool, procs = await _pool(lambda rid, k, m, p: _frame(rid, 2 if k == 1 else 0, big if k == 1 else b"1"),
                              docker=docker)
    assert (await pool.decode_image("f", b"x")).ok
    procs[0].answer = lambda rid, k, m, p: _frame(rid, 0, b" " * (iso.REPLY_MAX + 10))
    r = await pool.decode("f", b"x")
    assert r.outcome == "fault" and "bad size" in r.reason
    pool._spawn = _respawn(procs, lambda rid, k, m, p: _frame(rid, 0, b"1"))
    pool.friends["f"].retry_at = 0.0
    r = await pool.decode_image("g", b"x")
    assert r.outcome == "fault" and "bad status" in r.reason
    await pool.stop()


async def test_the_real_runner_answers_image_requests_with_pixels(tmp_path, docker) -> None:
    pool, app = await _real_pool(tmp_path, "realimg", timeout=30.0)
    try:
        src = _img("JPEG", size=(1600, 1200), exif=_gps_exif())
        r = await pool.decode_image("a", src)
        assert r.ok and r.media_type == "image/png", r
        assert (r.value["image"]["width"], r.value["image"]["height"]) == (768, 576)
        # The core writes only these chunks; a picture with few colours adds a
        # palette (PLTE), never metadata.
        assert b"SecretCam" not in r.image
        assert _chunks(r.image) in ([b"IHDR", b"IDAT", b"IEND"], [b"IHDR", b"PLTE", b"IDAT", b"IEND"])
        r = await pool.decode_image("a", _bomb_png(20000, 20000))
        assert r.outcome == "refused" and "too large" in r.reason
        r = await pool.decode_image("a", b"GIF89a....")
        assert r.outcome == "refused" and "not a PNG" in r.reason
        r = await pool.decode("a", b'{"still": "json"}')
        assert r.ok and r.value == {"got": {"still": "json"}, "meta": []}
        assert pool.restarts == 0
    finally:
        await pool.stop()


def test_the_core_serves_images_with_a_fixed_type_and_nosniff() -> None:
    resp = iso.image_response(iso.encode_png(1, 1, 3, b"\x00\x00\x00"))
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in resp.headers["content-security-policy"]
    with pytest.raises(ValueError):
        iso.image_response(b"<svg/>")


def test_image_pieces_are_reassembled_by_the_core_within_bounds() -> None:
    clock = Clock()
    asm = iso.ImageChunks(max_bytes=10, max_parts=3, per_friend=2, total=3, ttl_sec=60, clock=clock)
    assert asm.add("games", "a", "k", 1, 2, b"cd") is None
    # L13e: keyed by plugin too: another plugin's piece never joins it.
    assert asm.add("other", "a", "k", 0, 2, b"zz") is None
    assert asm.add("games", "a", "k", 0, 2, b"ab") == b"abcd"
    asm.discard("other", "a", "k")
    with pytest.raises(ValueError):
        asm.add("games", "a", "k", 0, 4, b"x")                      # too many pieces
    with pytest.raises(ValueError):
        asm.add("games", "a", "k", 2, 2, b"x")                      # index out of range
    asm.add("games", "a", "big", 0, 2, b"123456")
    with pytest.raises(ValueError, match="too large"):
        asm.add("games", "a", "big", 1, 2, b"789012")
    asm.add("games", "a", "d", 0, 2, b"x")
    with pytest.raises(ValueError, match="disagree"):
        asm.add("games", "a", "d", 0, 2, b"y")                      # a piece sent twice, differently
    asm.add("games", "a", "p", 0, 2, b"x")
    asm.add("games", "a", "q", 0, 2, b"x")
    with pytest.raises(ValueError, match="in progress"):
        asm.add("games", "a", "r", 0, 2, b"x")                      # per friend
    asm.add("games", "b", "s", 0, 2, b"x")
    with pytest.raises(ValueError, match="in progress"):
        asm.add("games", "c", "t", 0, 2, b"x")                      # in all
    clock.t += 61
    assert asm.add("games", "c", "t", 0, 2, b"x") is None           # the old ones expired


def test_the_docker_cli_sees_none_of_the_core_s_secrets(monkeypatch) -> None:
    # The container gets no environment at all (run_args has no -e); the
    # docker CLI that starts it sees only what it needs to find Docker.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("DOMOVOI_ADMIN_TOKEN", "t")
    monkeypatch.setenv("SOME_API_KEY", "k")
    for env in (iso.docker_env(), iso.build_env()):
        assert not {"DATABASE_URL", "DOMOVOI_ADMIN_TOKEN", "SOME_API_KEY"} & set(env)
        assert set(env) <= set(iso._DOCKER_ENV) | {"LANG", "DOCKER_BUILDKIT"}
