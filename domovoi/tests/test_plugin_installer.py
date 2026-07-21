"""Install pipeline (design §3.2–§3.6, §7.4) against the compliments
fixture plugin: zip safety, two-phase stage/confirm, TOCTOU re-hash,
rollback, enable/disable, uninstall keep/purge, the bundled tombstone,
upgrade monotonicity, and the require_admin structural gate."""

from __future__ import annotations

import hashlib
import io
import secrets
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi import bootstrap
from domovoi.db.session import engine
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.plugins_runtime import installer
from domovoi.plugins_runtime import registry as reg
from domovoi.plugins_runtime.installer import (
    InstallError,
    confirm_install,
    confirm_upgrade,
    disable_plugin,
    enable_plugin,
    stage_zip,
    uninstall_plugin,
    validate_lockfile,
    validate_zip_safety,
)
from domovoi.plugins_runtime.loader import LOADER
from domovoi.tests.conftest import requires_db

pytestmark = [pytest.mark.asyncio, requires_db]

FIXTURE = Path(__file__).parent / "fixtures" / "compliments"
SLUG = "compliments"
SCHEMA = f"plugin_{SLUG}"


def build_fixture_zip(
    *, version: str | None = None, prefix: str = "", slug: str = SLUG
) -> bytes:
    """Zip the fixture plugin (manifest at zip root, §2.1 layout).
    ``version=`` overrides the manifest version (upgrade tests);
    ``prefix=`` nests everything in one top dir (GitHub archive shape)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(FIXTURE.rglob("*")):
            if f.is_dir() or "__pycache__" in f.parts:
                continue
            rel = f.relative_to(FIXTURE).as_posix()
            data = f.read_bytes()
            if rel == "domovoi-plugin.toml" and version is not None:
                data = data.replace(
                    b'version = "1.0.0"', f'version = "{version}"'.encode()
                )
            zf.writestr(prefix + rel, data)
    return buf.getvalue()


async def _schema_exists() -> bool:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = :s"
                ),
                {"s": SCHEMA},
            )
        ).first()
    return row is not None


@pytest_asyncio.fixture(autouse=True)
async def _plugin_env(tmp_path: Path, monkeypatch):
    """Sandbox every install-pipeline path under tmp + full cleanup."""
    bootstrap.register_nvidia_dlls()
    from domovoi.plugins_runtime import loader as loader_mod

    staging = tmp_path / "staging"
    installed = tmp_path / "installed"
    data = tmp_path / "data"
    previous = tmp_path / "previous"
    bundled = tmp_path / "bundled-empty"
    for d in (staging, installed, data, previous, bundled):
        d.mkdir()
    monkeypatch.setattr(installer, "staging_root", lambda: staging)
    monkeypatch.setattr(installer, "installed_root", lambda: installed)
    monkeypatch.setattr(installer, "data_root", lambda: data)
    monkeypatch.setattr(installer, "previous_root", lambda: previous)
    monkeypatch.setattr(loader_mod, "installed_root", lambda: installed)
    monkeypatch.setattr(loader_mod, "bundled_root", lambda: bundled)

    yield

    installer._STAGED.clear()
    # Unload EVERYTHING these tests loaded — the tombstone test runs
    # discover_and_load_all(), which also hot-loads any real bundled
    # plugin registered in the test DB (radio ships bundled + enabled by
    # default, locked 14). Leaving it registered would leak its handler
    # into the registry-shape tests that run later.
    await LOADER.shutdown()
    await reg.delete_plugin(SLUG)
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))


# ─── zip safety (§7.4) ──────────────────────────────────────────────────────

def _zip_of(entries: dict[str, bytes], *, symlink: str | None = None) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = 0o120777 << 16    # lrwxrwxrwx
            zf.writestr(info, "target")
    buf.seek(0)
    return zipfile.ZipFile(buf)


@pytest.mark.parametrize(
    "entries,symlink,code",
    [
        ({"../evil.txt": b"x"}, None, "zip_bad_path"),
        ({"/abs.txt": b"x"}, None, "zip_bad_path"),
        ({"a/../../evil.txt": b"x"}, None, "zip_bad_path"),
        ({"nul.txt": b"x"}, None, "zip_reserved_name"),
        ({"sub/CON": b"x"}, None, "zip_reserved_name"),
        ({"sub/com1.log": b"x"}, None, "zip_reserved_name"),
        ({"A.txt": b"x", "a.txt": b"y"}, None, "zip_case_collision"),
        # NOTE: no backslash-path case here — zipfile.writestr normalizes
        # os.sep to "/" on Windows, so a hostile backslash entry can't be
        # produced with the stdlib writer; the validate_zip_safety branch
        # stays for archives written by other tools.
        ({"ok.txt": b"x"}, "link.txt", "zip_symlink"),
    ],
)
def test_zip_safety_rejections(entries, symlink, code) -> None:
    with pytest.raises(InstallError) as exc:
        validate_zip_safety(_zip_of(entries, symlink=symlink))
    assert exc.value.code == code


def test_zip_entry_count_cap(monkeypatch) -> None:
    monkeypatch.setattr(installer, "MAX_ZIP_ENTRIES", 3)
    zf = _zip_of({f"f{i}.txt": b"x" for i in range(5)})
    with pytest.raises(InstallError) as exc:
        validate_zip_safety(zf)
    assert exc.value.code == "zip_too_many_entries"


def test_zip_extracted_size_cap(monkeypatch) -> None:
    monkeypatch.setattr(installer, "MAX_EXTRACTED_BYTES", 10)
    zf = _zip_of({"big.txt": b"x" * 100})
    with pytest.raises(InstallError) as exc:
        validate_zip_safety(zf)
    assert exc.value.code == "zip_too_large"


async def test_upload_size_cap(monkeypatch) -> None:
    monkeypatch.setattr(installer, "MAX_ZIP_BYTES", 10)
    with pytest.raises(InstallError) as exc:
        await stage_zip(b"x" * 100)
    assert exc.value.code == "zip_too_large"


async def test_bad_layout_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("one/a.txt", "x")
        zf.writestr("two/b.txt", "y")
    with pytest.raises(InstallError) as exc:
        await stage_zip(buf.getvalue())
    assert exc.value.code == "zip_bad_layout"


# ─── Phase A: stage & preview ───────────────────────────────────────────────

async def test_stage_returns_preview() -> None:
    staged = await stage_zip(build_fixture_zip(), source_ref="compliments.zip")
    assert len(staged.staged_id) == 32          # 128-bit hex
    p = staged.preview
    assert p["name"] == "Compliments"
    assert p["version"] == "1.0.0"
    assert p["publisher"] == "Coders Farm"
    assert p["permissions"]["warnings"] == [
        "Keeps a log of every compliment it pays."
    ]
    assert p["handlers"] == [
        {"name": "compliments", "band": 400, "label": "Compliments",
         "corpus": ["say something nice", "say something nice about my hair"]}
    ]
    assert p["migration_count"] == 1
    assert "full access" in p["trust_statement"]
    assert (staged.root / "domovoi-plugin.toml").is_file()
    installer._drop_staged(staged)


async def test_stage_accepts_github_archive_shape() -> None:
    staged = await stage_zip(build_fixture_zip(prefix="repo-main/"))
    assert staged.manifest.slug == SLUG
    installer._drop_staged(staged)


async def test_stage_rejects_installed_slug() -> None:
    staged = await stage_zip(build_fixture_zip())
    await confirm_install(staged.staged_id)
    with pytest.raises(InstallError) as exc:
        await stage_zip(build_fixture_zip())
    assert exc.value.code == "slug_exists"


async def test_stage_flags_orphan_schema() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
    with pytest.raises(InstallError) as exc:
        await stage_zip(build_fixture_zip())
    assert exc.value.code == "orphan_schema"


async def test_stage_lints_migrations() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(build_fixture_zip())) as src, \
         zipfile.ZipFile(buf, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename.startswith("migrations/"):
                data = b"CREATE TABLE public.hijack (id INT);"
            dst.writestr(info.filename, data)
    with pytest.raises(InstallError) as exc:
        await stage_zip(buf.getvalue())
    assert exc.value.code == "migration_lint"


# ─── Phase B: confirm ───────────────────────────────────────────────────────

async def test_full_install_pipeline() -> None:
    staged = await stage_zip(build_fixture_zip(), source_ref="compliments.zip")
    result = await confirm_install(staged.staged_id)
    assert result["installed"] and result["loaded"]
    assert result["status"] == "ok"

    # Installed dir moved into place; staging gone.
    assert (installer.installed_root() / SLUG / "domovoi-plugin.toml").is_file()
    assert not staged.root.exists()

    # Registry row: enabled, manifest JSONB intact (the web process reads it).
    row = await reg.get_plugin(SLUG)
    assert row is not None and row.enabled and row.status == "ok"
    assert row.install_source == "zip" and row.source_ref == "compliments.zip"
    assert row.manifest["plugin"]["slug"] == SLUG
    assert row.manifest["handlers"][0]["band"] == 400

    # Migrations applied with ledger.
    assert await _schema_exists()
    async with engine.connect() as conn:
        versions = (
            await conn.execute(
                text(f'SELECT version FROM "{SCHEMA}".schema_history')
            )
        ).scalars().all()
    assert versions == [1]

    # Hot-loaded: handler live in the merged registry, workers registered.
    assert "compliments" in HANDLER_BY_NAME
    from domovoi.plugins_runtime.workers import WORKERS

    assert WORKERS.worker_names(SLUG) == {"compliment_ticker": "poll"}


async def test_confirm_rehash_closes_toctou_window() -> None:
    staged = await stage_zip(build_fixture_zip())
    # Tamper with the staged tree between preview and confirm.
    core = staged.root / "domovoi_plugin_compliments" / "core.py"
    core.write_text(
        core.read_text(encoding="utf-8") + "\nEVIL = True\n", encoding="utf-8"
    )
    with pytest.raises(InstallError) as exc:
        await confirm_install(staged.staged_id)
    assert exc.value.code == "staging_modified"
    assert not staged.root.exists()             # staging dropped
    assert await reg.get_plugin(SLUG) is None   # nothing installed


async def test_unknown_staged_id() -> None:
    with pytest.raises(InstallError) as exc:
        await confirm_install(secrets.token_hex(16))
    assert exc.value.code == "staged_id_unknown"


# ─── enable / disable (§3.4) ────────────────────────────────────────────────

async def test_disable_enable_roundtrip() -> None:
    staged = await stage_zip(build_fixture_zip())
    await confirm_install(staged.staged_id)
    assert "compliments" in HANDLER_BY_NAME

    out = await disable_plugin(SLUG)
    assert out == {"enabled": False, "slug": SLUG}
    assert "compliments" not in HANDLER_BY_NAME
    row = await reg.get_plugin(SLUG)
    assert row is not None and not row.enabled
    # Data is ALWAYS kept on disable (locked 4).
    assert await _schema_exists()

    out = await enable_plugin(SLUG)
    assert out == {"enabled": True, "slug": SLUG}
    assert "compliments" in HANDLER_BY_NAME
    row = await reg.get_plugin(SLUG)
    assert row is not None and row.enabled


# ─── uninstall keep / purge (§3.5) ──────────────────────────────────────────

async def test_uninstall_keep_preserves_schema() -> None:
    staged = await stage_zip(build_fixture_zip())
    await confirm_install(staged.staged_id)
    out = await uninstall_plugin(SLUG, data="keep")
    assert out["uninstalled"] and out["data"] == "keep"
    assert "compliments" not in HANDLER_BY_NAME
    assert await reg.get_plugin(SLUG) is None            # non-bundled: row gone
    assert not (installer.installed_root() / SLUG).exists()
    assert await _schema_exists()                        # data kept


async def test_uninstall_purge_drops_schema_and_data_dir() -> None:
    staged = await stage_zip(build_fixture_zip())
    await confirm_install(staged.staged_id)
    plugin_data = installer.data_root() / SLUG
    plugin_data.mkdir(parents=True, exist_ok=True)
    (plugin_data / "cache.bin").write_bytes(b"x")
    out = await uninstall_plugin(SLUG, data="purge")
    assert out["uninstalled"] and out["data"] == "purge"
    assert not await _schema_exists()
    assert not plugin_data.exists()
    assert await reg.get_plugin(SLUG) is None


async def test_reinstall_after_keep_runs_catchup_not_v001() -> None:
    staged = await stage_zip(build_fixture_zip())
    await confirm_install(staged.staged_id)
    # Leave a row in the plugin's table, uninstall keeping data.
    async with engine.begin() as conn:
        await conn.execute(
            text(f'INSERT INTO "{SCHEMA}".compliments_log (topic) VALUES (\'hair\')')
        )
    await uninstall_plugin(SLUG, data="keep")
    assert await _schema_exists()

    # Reinstall: catch-up sees V001 already applied; user data survives.
    staged = await stage_zip(build_fixture_zip())
    await confirm_install(staged.staged_id)
    async with engine.connect() as conn:
        topics = (
            await conn.execute(
                text(f'SELECT topic FROM "{SCHEMA}".compliments_log')
            )
        ).scalars().all()
    assert topics == ["hair"]


# ─── bundled tombstone (§3.5/§3.7) ─────────────────────────────────────────

async def test_bundled_uninstall_tombstones_and_never_resurrects() -> None:
    await reg.insert_plugin(
        slug=SLUG, name="Compliments", version="1.0.0",
        publisher="Coders Farm", license="MIT", domovoi_api=">=1.0,<2.0",
        enabled=True, bundled=True, install_source="bundled",
        source_ref=None, install_dir=str(FIXTURE),
        manifest={"plugin": {"slug": SLUG}, "handlers": []},
    )
    out = await uninstall_plugin(SLUG, data="keep")
    assert out["bundled"] is True
    # Tombstone: the row survives, uninstalled + disabled; the bundled dir
    # is never deleted.
    row = await reg.get_plugin(SLUG)
    assert row is not None
    assert row.status == "uninstalled" and not row.enabled
    assert FIXTURE.is_dir()

    # §3.7 discovery respects the tombstone: no auto-re-registration even
    # with the plugin present in the bundled dir.
    from domovoi.plugins_runtime import loader as loader_mod

    bundled_dir = loader_mod.bundled_root()
    await LOADER.discover_and_load_all()
    assert SLUG not in LOADER.loaded
    row = await reg.get_plugin(SLUG)
    assert row is not None and row.status == "uninstalled"


# ─── upgrade (§3.6) ─────────────────────────────────────────────────────────

async def test_upgrade_requires_strictly_greater_version() -> None:
    staged = await stage_zip(build_fixture_zip())
    await confirm_install(staged.staged_id)

    with pytest.raises(InstallError) as exc:
        await stage_zip(build_fixture_zip(), upgrade_of=SLUG)
    assert exc.value.code == "upgrade_same_version"

    with pytest.raises(InstallError) as exc:
        await stage_zip(build_fixture_zip(version="0.9.0"), upgrade_of=SLUG)
    assert exc.value.code == "downgrade_requires_force"


async def test_upgrade_happy_path() -> None:
    staged = await stage_zip(build_fixture_zip())
    await confirm_install(staged.staged_id)

    staged2 = await stage_zip(build_fixture_zip(version="1.1.0"), upgrade_of=SLUG)
    result = await confirm_upgrade(staged2.staged_id)
    assert result["loaded"] and result["upgraded_from"] == "1.0.0"
    row = await reg.get_plugin(SLUG)
    assert row is not None and row.version == "1.1.0" and row.enabled
    assert "compliments" in HANDLER_BY_NAME
    # The .previous copy is cleaned up on success.
    assert list(installer.previous_root().iterdir()) == []


# ─── the require_admin structural gate ─────────────────────────────────────

async def test_mutating_endpoints_are_structurally_gated(db_session) -> None:
    """§7.3/task rule: with auth unconfigured every plugin-management
    mutation 501s; with an admin_auth row a bad token 401s and a valid
    Bearer session passes the gate."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from domovoi.plugins_runtime.installer import plugins_admin_router

    app = FastAPI()
    app.include_router(plugins_admin_router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No admin_auth row (db_session truncated it) → 501 fail-closed.
        r = await client.post("/v1/plugins/install", json={})
        assert r.status_code == 501
        assert "auth not configured" in r.text

        # Create the admin row + a live session token.
        token = secrets.token_hex(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        await db_session.execute(
            text(
                "INSERT INTO admin_auth (id, password_hash) VALUES (1, 'x')"
            )
        )
        await db_session.execute(
            text(
                "INSERT INTO admin_sessions (token_hash, expires_at) "
                "VALUES (:h, NOW() + INTERVAL '1 day')"
            ),
            {"h": token_hash},
        )
        await db_session.commit()

        r = await client.post("/v1/plugins/install", json={})
        assert r.status_code == 401

        r = await client.post(
            "/v1/plugins/install",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        # Past the gate; rejected for the empty body, not for auth.
        assert r.status_code == 422
        assert r.json()["detail"]["error"]["code"] == "bad_request"


# ─── §7.4 poisoned-lockfile pre-confirm code execution (BLOCKER) ────────────

EVILPKG_SRC = Path(__file__).parent / "fixtures" / "evilpkg"
COMPLIMENTS_MANIFEST = (FIXTURE / "domovoi-plugin.toml").read_text(encoding="utf-8")


def _build_evilpkg_sdist(dest_dir: Path) -> tuple[Path, str]:
    """Tar the hostile fixture into a real sdist (``evilpkg-0.0.0.tar.gz``)
    whose build backend writes a marker file when pip builds it. Returns
    (path, sha256) — the digest goes in the lockfile's --hash line so the
    poisoned lockfile passes the manifest/lockfile shape checks and the
    rejection is provably the §7.4 option-line guard, not a shape error."""
    tar_path = dest_dir / "evilpkg-0.0.0.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for name in ("setup.py", "evilpkg.py", "PKG-INFO"):
            tar.add(EVILPKG_SRC / name, arcname=f"evilpkg-0.0.0/{name}")
    return tar_path, hashlib.sha256(tar_path.read_bytes()).hexdigest()


def build_zip_with_lockfile(
    lockfile_text: str, *, requirement: str = "evilpkg==0.0.0"
) -> bytes:
    """The compliments fixture, but declaring a python requirement + a
    caller-supplied lockfile (so we can stage a poisoned lockfile)."""
    manifest = COMPLIMENTS_MANIFEST + (
        f'\n[requirements]\npython = ["{requirement}"]\n'
        'lockfile = "requirements.lock"\n'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(FIXTURE.rglob("*")):
            if f.is_dir() or "__pycache__" in f.parts:
                continue
            rel = f.relative_to(FIXTURE).as_posix()
            if rel == "domovoi-plugin.toml":
                zf.writestr(rel, manifest)
            else:
                zf.writestr(rel, f.read_bytes())
        zf.writestr("requirements.lock", lockfile_text)
    return buf.getvalue()


def test_validate_lockfile_accepts_clean_hashed_pins(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        "# a comment\n"
        "certifi==2024.2.2 \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        "idna==3.6 \\\n"
        "    --hash=sha256:1111111111111111111111111111111111111111111111111111111111111111\n",
        encoding="utf-8",
    )
    validate_lockfile(lock)  # no raise


@pytest.mark.parametrize(
    "bad_line",
    [
        "--no-binary :all:",
        "--no-binary=:all:",
        "--index-url https://evil.example/simple",
        "--extra-index-url https://evil.example/simple",
        "--find-links ./wheelhouse",
        "--no-index",
        "-e .",
        "--editable .",
        "-r other.txt",
        "--requirement other.txt",
        "--pre",
    ],
)
def test_validate_lockfile_rejects_global_options(
    tmp_path: Path, bad_line: str
) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        f"{bad_line}\n"
        "evilpkg==0.0.0 \\\n"
        "    --hash=sha256:2222222222222222222222222222222222222222222222222222222222222222\n",
        encoding="utf-8",
    )
    with pytest.raises(InstallError) as exc:
        validate_lockfile(lock)
    assert exc.value.code == "lockfile_option"


async def test_poisoned_lockfile_rejected_before_pip_runs_build_backend(
    tmp_path: Path, monkeypatch
) -> None:
    """§3.2 step 7 regression: a lockfile that smuggles ``--no-binary
    :all:`` (which would cancel ``--only-binary=:all:`` and make pip build
    the sdist, running attacker code) must be REJECTED at stage validation,
    before pip is ever invoked — so the sdist build backend never runs and
    its marker file is never written."""
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    _sdist, digest = _build_evilpkg_sdist(wheelhouse)
    marker = tmp_path / "PWNED.marker"
    monkeypatch.setenv("EVILPKG_MARKER", str(marker))

    poisoned = (
        "--no-binary :all:\n"
        f"--find-links {wheelhouse.as_posix()}\n"
        "evilpkg==0.0.0 \\\n"
        f"    --hash=sha256:{digest}\n"
    )
    with pytest.raises(InstallError) as exc:
        await stage_zip(build_zip_with_lockfile(poisoned))
    assert exc.value.code == "lockfile_option"

    # Proof the build backend never executed: no marker, nothing staged.
    assert not marker.exists()
    assert list(installer.staging_root().iterdir()) == []
    assert await reg.get_plugin(SLUG) is None


# ─── §3.5 zip-over-bundled-tombstone identity desync (MAJOR) ─────────────────

async def test_zip_over_bundled_tombstone_becomes_non_bundled() -> None:
    """Installing a zip whose slug matches a bundled plugin's 'uninstalled'
    tombstone must produce a NORMAL non-bundled row: bundled=False,
    install_dir at the installed copy. Otherwise the loader's bundled-dir
    heal repoints install_dir back into the repo tree and core runs the
    bundled code while the manifest describes the zip."""
    from domovoi.plugins_runtime import loader as loader_mod

    # A bundled tombstone: uninstalled + disabled, bundled=True, pointing
    # into the repo tree.
    await reg.insert_plugin(
        slug=SLUG, name="Compliments", version="1.0.0",
        publisher="Coders Farm", license="MIT", domovoi_api=">=1.0,<2.0",
        enabled=False, bundled=True, install_source="bundled",
        source_ref=None, install_dir=str(FIXTURE),
        manifest={"plugin": {"slug": SLUG}, "handlers": []},
        status="uninstalled",
    )
    # A real bundled copy at bundled_root/<slug> so the loader's heal WOULD
    # repoint install_dir into it if bundled stayed True.
    bundled_copy = loader_mod.bundled_root() / SLUG
    shutil.copytree(FIXTURE, bundled_copy)

    staged = await stage_zip(build_fixture_zip(), source_ref="compliments.zip")
    result = await confirm_install(staged.staged_id)
    assert result["installed"] and result["loaded"]

    installed_dir = installer.installed_root() / SLUG
    row = await reg.get_plugin(SLUG)
    assert row is not None
    assert row.bundled is False                       # revival cleared bundled
    assert row.status == "ok" and row.enabled
    assert row.install_source == "zip" and row.source_ref == "compliments.zip"
    assert Path(row.install_dir) == installed_dir     # installed copy, not repo
    assert Path(row.install_dir).resolve() != bundled_copy.resolve()

    # The bundled-dir heal must NOT fire now (it is guarded by row.bundled);
    # install_dir stays at the installed copy across a discovery pass.
    await LOADER.discover_and_load_all()
    row = await reg.get_plugin(SLUG)
    assert row is not None and row.bundled is False
    assert Path(row.install_dir) == installed_dir
