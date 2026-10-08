"""Staging a plugin cannot exhaust or stall the core before the trust
screen (A7-04).

The zip caps (100 MB compressed, 500 MB extracted, 10k entries) held, but
what the validators did with the extracted bytes did not: the manifest and
lockfile parsers, the SQL lint (a list element per character) and the
web-import AST check ran in the core process on whatever size the archive
expanded to, and they, pip's dry run and the tree hash ran ON the event
loop, so a slow index or a big tree froze every satellite socket. Now each
file the validators parse is held to a per-file and a total ceiling,
checked from the zip's own sizes before anything is written, and the
blocking steps run in worker threads.

DB-free: ``stage_zip`` reaches the registry only at step 6, faked here as
in test_plugin_install_preview.py.
"""

from __future__ import annotations

import asyncio
import io
import time
import zipfile
from pathlib import Path

import pytest

from domovoi.plugins_runtime import installer
from domovoi.plugins_runtime import registry as reg
from domovoi.plugins_runtime.installer import InstallError, stage_zip

FIXTURE = Path(__file__).parent / "fixtures" / "compliments"
# Read with a default so the A/B against the old installer fails on the
# behaviour, not on a missing name.
CAP = getattr(installer, "MAX_PARSED_FILE_BYTES", 2 * 1024 * 1024)
BIG = max(CAP, 2 * 1024 * 1024) + 1024


def _zip_of(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    return buf.getvalue()


def _compliments() -> dict[str, bytes]:
    return {
        f.relative_to(FIXTURE).as_posix(): f.read_bytes()
        for f in sorted(FIXTURE.rglob("*"))
        if f.is_file() and "__pycache__" not in f.parts
    }


@pytest.fixture
def staging(tmp_path: Path, monkeypatch):
    root = tmp_path / "staging"
    root.mkdir()
    monkeypatch.setattr(installer, "staging_root", lambda: root)

    async def _no_row(slug: str):
        return None

    async def _no_schema(slug: str):
        return False

    monkeypatch.setattr(reg, "get_plugin", _no_row)
    monkeypatch.setattr(reg, "plugin_schema_exists", _no_schema)
    yield root
    installer._STAGED.clear()


def _never(name: str):
    def boom(*a, **kw):  # pragma: no cover — reaching it IS the failure
        raise AssertionError(f"{name} ran on an oversized archive")

    return boom


# ─── size ceilings ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "domovoi_plugin_compliments/web.py",
        "domovoi_plugin_compliments/big.py",
        "migrations/V002__huge.sql",
        "requirements.lock",
    ],
)
async def test_an_oversized_source_file_is_refused_before_extraction(
    staging, monkeypatch, path
) -> None:
    """``x=1`` lines compress to almost nothing; the file they expand to
    would be parsed whole. Refused from the zip's own sizes: nothing is
    written, nothing is parsed."""
    for fn in ("check_web_import_hygiene", "sql_lint", "parse_manifest"):
        monkeypatch.setattr(installer, fn, _never(fn))
    files = _compliments()
    files[path] = b"x=1\n" * (BIG // 4)
    data = _zip_of(files)
    assert len(data) < BIG // 20                  # it really is a tiny upload

    with pytest.raises(InstallError) as exc:
        await stage_zip(data)

    assert exc.value.code == "plugin_file_too_large"
    assert exc.value.details["file"] == path
    assert list(staging.iterdir()) == []          # no staging dir was made


async def test_a_lockfile_under_any_name_is_held_to_the_same_ceiling(
    staging, monkeypatch
) -> None:
    monkeypatch.setattr(installer, "validate_lockfile", _never("validate_lockfile"))
    files = _compliments()
    manifest = files["domovoi-plugin.toml"].decode("utf-8")
    manifest += '\n[requirements]\npython = ["httpx==0.28.1"]\nlockfile = "deps.txt"\n'
    files["domovoi-plugin.toml"] = manifest.encode("utf-8")
    files["deps.txt"] = b"# x\n" * (BIG // 4)

    with pytest.raises(InstallError) as exc:
        await stage_zip(_zip_of(files))

    assert exc.value.code == "plugin_file_too_large"
    assert exc.value.details["file"] == "deps.txt"


async def test_many_files_under_the_per_file_ceiling_still_add_up(
    staging, monkeypatch
) -> None:
    monkeypatch.setattr(installer, "MAX_PARSED_TOTAL_BYTES", 64 * 1024, raising=False)
    files = _compliments()
    for i in range(10):
        files[f"domovoi_plugin_compliments/m{i}.py"] = b"x=1\n" * 2048   # 8 KiB each
    with pytest.raises(InstallError) as exc:
        await stage_zip(_zip_of(files))
    assert exc.value.code == "plugin_file_too_large"


async def test_a_real_plugin_is_nowhere_near_the_ceilings(staging) -> None:
    staged = await stage_zip(_zip_of(_compliments()))
    assert staged.manifest.slug == "compliments"


# ─── off the event loop ──────────────────────────────────────────────────


async def _ticks_while(coro, *, interval: float = 0.01) -> tuple[int, object]:
    """Run ``coro`` beside a ticker; how often the loop got to tick."""
    ticks = 0
    done = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await asyncio.sleep(interval)

    t = asyncio.create_task(ticker())
    try:
        result = await coro
    finally:
        done.set()
        await t
    return ticks, result


def _slow(seconds: float, ret=None):
    def run(*a, **kw):
        time.sleep(seconds)
        return ret if not callable(ret) else ret(*a, **kw)

    return run


async def test_the_validators_do_not_stall_the_event_loop(staging, monkeypatch) -> None:
    """A slow parse used to freeze the core (every satellite socket with
    it) for its whole duration. The loop now keeps ticking."""
    monkeypatch.setattr(installer, "check_web_import_hygiene", _slow(0.4, []))
    ticks, staged = await _ticks_while(stage_zip(_zip_of(_compliments())))
    assert staged.manifest.slug == "compliments"
    assert ticks >= 10, f"the loop ticked {ticks} times in a 0.4 s validator"


async def test_the_pip_dry_run_does_not_stall_the_event_loop(staging, monkeypatch) -> None:
    files = _compliments()
    manifest = files["domovoi-plugin.toml"].decode("utf-8")
    manifest += '\n[requirements]\npython = ["httpx==0.28.1"]\n'
    files["domovoi-plugin.toml"] = manifest.encode("utf-8")
    files["requirements.lock"] = (
        "httpx==0.28.1 \\\n    --hash=sha256:" + "0" * 64 + "\n"
    ).encode("utf-8")
    monkeypatch.setattr(installer, "pip_dry_run", _slow(0.4, {"resolved": []}))

    ticks, staged = await _ticks_while(stage_zip(_zip_of(files)))

    assert staged.preview["requirements"]["direct"] == ["httpx==0.28.1"]
    assert ticks >= 10, f"the loop ticked {ticks} times during a 0.4 s pip run"


async def test_the_tree_hash_reads_files_in_blocks(tmp_path) -> None:
    """Same digest as hashing each file whole, without holding it whole."""
    import hashlib

    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "big.bin").write_bytes(b"\x01" * (3 * 1024 * 1024 + 7))
    (tmp_path / "b.txt").write_bytes(b"hello")
    h = hashlib.sha256()
    for path in sorted(tmp_path.rglob("*")):
        rel = path.relative_to(tmp_path).as_posix()
        if path.is_dir():
            h.update(f"D:{rel}\n".encode())
        else:
            h.update(f"F:{rel}:{path.stat().st_size}\n".encode())
            h.update(path.read_bytes())
    assert installer.hash_tree(tmp_path) == h.hexdigest()
    assert installer.hash_tree_excluding(tmp_path, set()) == h.hexdigest()
