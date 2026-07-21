"""The plugin install pipeline (design §3.2–§3.6, §7.4).

Two-phase zip install (stage → preview → confirm) so the §7.5 trust
warning is unskippable; GitHub-URL install funnels into the same
pipeline; enable/disable, uninstall keep/purge (with the bundled-plugin
tombstone rule), and upgrade (semver-monotonic, ledger-guarded
downgrade barrier) live here too, plus the admin HTTP surface.

Security posture (§7.4): zip safety caps + path normalization + symlink
and reserved-name rejection; pinned+hashed requirements resolved with
``pip --dry-run --report - --require-hashes --only-binary=:all:`` in a
**throwaway subprocess** so no sdist build backend (attacker code) can
run before the trust screen; the staged tree is sha256-hashed at stage
time and re-verified at confirm (TOCTOU close); staged ids are 128-bit
random. Every mutating endpoint depends on
:func:`domovoi.auth.require_admin`.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from domovoi.auth import require_admin
from domovoi.plugins_runtime import registry as reg
from domovoi.plugins_runtime.contracts import ContractError
from domovoi.plugins_runtime.loader import LOADER, installed_root
from domovoi.plugins_runtime.manifest import (
    ManifestError,
    PluginManifest,
    check_web_import_hygiene,
    parse_manifest,
    validate_plugin_dir,
)
from domovoi.plugins_runtime.migrations import (
    PluginMigrationRunner,
    SqlLintError,
    discover_migrations,
    sql_lint,
)

log = logging.getLogger(__name__)

# §7.4 caps — module-level so tests can tighten them.
MAX_ZIP_BYTES = 100 * 1024 * 1024          # compressed upload / download
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024    # sum of uncompressed sizes
MAX_ZIP_ENTRIES = 10_000

_RESERVED_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

TRUST_STATEMENT = (
    "This plugin runs with full access to your Domovoi server. It can read "
    "and modify your library, database, configuration, and anything else "
    "this machine can reach. Only install plugins from publishers you trust."
)

_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:@([A-Za-z0-9_./-]+))?/?$"
)


class InstallError(ValueError):
    """A validation-phase rejection (HTTP 422)."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


def staging_root() -> Path:
    return Path.home() / ".domovoi" / "plugins" / ".staging"


def previous_root() -> Path:
    return Path.home() / ".domovoi" / "plugins" / ".previous"


def data_root() -> Path:
    return Path.home() / ".domovoi" / "plugins" / "data"


# ─── zip safety (§7.4 / §3.2 step 2) ────────────────────────────────────────

def _entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def validate_zip_safety(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise InstallError(
            "zip_too_many_entries",
            f"archive has {len(infos)} entries (cap {MAX_ZIP_ENTRIES})",
        )
    total = 0
    seen_ci: set[str] = set()
    for info in infos:
        name = info.filename
        if _entry_is_symlink(info):
            raise InstallError("zip_symlink", f"symlink entry rejected: {name!r}")
        if "\\" in name:
            raise InstallError(
                "zip_bad_path", f"backslash in entry path: {name!r}"
            )
        p = PurePosixPath(name)
        if p.is_absolute() or (len(name) >= 2 and name[1] == ":"):
            raise InstallError("zip_bad_path", f"absolute entry path: {name!r}")
        if ".." in p.parts:
            raise InstallError("zip_bad_path", f"'..' in entry path: {name!r}")
        for part in p.parts:
            stem = part.split(".")[0].lower()
            if stem in _RESERVED_DEVICE_NAMES:
                raise InstallError(
                    "zip_reserved_name",
                    f"Windows reserved device name in path: {name!r}",
                )
        ci = name.lower().rstrip("/")
        if ci and ci in seen_ci:
            raise InstallError(
                "zip_case_collision",
                f"entries differing only by case collide on Windows: {name!r}",
            )
        seen_ci.add(ci)
        total += info.file_size
        if total > MAX_EXTRACTED_BYTES:
            raise InstallError(
                "zip_too_large",
                f"extracted size exceeds {MAX_EXTRACTED_BYTES} bytes",
            )


def _zip_root_prefix(zf: zipfile.ZipFile) -> str:
    """'' when the manifest is at the zip root; ``<dir>/`` when everything
    lives inside a single top-level directory (GitHub archive shape).
    Anything else → reject (§3.2 step 3)."""
    names = [i.filename for i in zf.infolist()]
    if "domovoi-plugin.toml" in names:
        return ""
    top_levels = {n.split("/", 1)[0] for n in names if n.strip("/")}
    if len(top_levels) == 1:
        prefix = next(iter(top_levels)) + "/"
        if f"{prefix}domovoi-plugin.toml" in names:
            return prefix
    raise InstallError(
        "zip_bad_layout",
        "domovoi-plugin.toml must be at the zip root or inside a single "
        "top-level directory",
    )


def hash_tree(root: Path) -> str:
    """Deterministic sha256 over the staged tree (paths + contents)."""
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            h.update(f"D:{rel}\n".encode())
        else:
            h.update(f"F:{rel}:{path.stat().st_size}\n".encode())
            h.update(path.read_bytes())
    return h.hexdigest()


# ─── pip (steps 7 + 9) ──────────────────────────────────────────────────────

# Only ``--hash=…`` continuations are allowed to start with a dash inside a
# plugin lockfile; every other option line is a global-option smuggle.
_ALLOWED_LOCKFILE_OPTION_RE = re.compile(r"^--hash(?:=|\s)", re.I)


def validate_lockfile(lockfile: Path) -> None:
    """§7.4 — a plugin lockfile is a pinned+hashed requirements file and
    NOTHING else.

    pip honors *requirement-file-level* global options (``--no-binary``,
    ``--index-url``, ``--extra-index-url``, ``--find-links``,
    ``--no-index``, ``-e``/``--editable``, ``-r``/``--requirement``, …).
    Any of them overrides the installer's CLI safety flags — a lockfile
    line like ``--no-binary :all:`` cancels ``--only-binary=:all:`` and
    makes pip run an sdist build backend (attacker code) BEFORE the user
    ever sees the §7.5 trust screen. Reject every option line except
    ``--hash=`` continuations so a poisoned lockfile fails validation and
    never reaches pip.
    """
    try:
        content = lockfile.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise InstallError(
            "lockfile_unreadable", f"cannot read lockfile {lockfile.name!r}: {e}"
        )
    for lineno, raw in enumerate(content.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line or line == "\\":            # blank / bare continuation
            continue
        if line.startswith("-"):
            if _ALLOWED_LOCKFILE_OPTION_RE.match(line):
                continue
            option = re.split(r"[=\s]", line, maxsplit=1)[0]
            raise InstallError(
                "lockfile_option",
                f"lockfile line {lineno} carries the pip option {option!r} — "
                f"plugin lockfiles may contain only pinned, hashed "
                f"requirements plus --hash= continuations; global pip options "
                f"are forbidden because they override the installer's "
                f"--only-binary safety flag and can execute a build backend "
                f"pre-confirm (design §7.4)",
                {"line": lineno, "option": option},
            )


def _pip_run_env() -> dict[str, str]:
    """Force UTF-8 for the pip subprocess so non-ASCII package metadata
    (author names, descriptions) can't crash the Windows trust-preview.
    On Windows the child's stdio defaults to the ANSI code page (cp1252);
    a UnicodeDecodeError there would abort the dry-run before the user
    ever sees the §7.5 trust screen. Paired with ``encoding="utf-8"`` on
    the ``subprocess.run`` call itself so our side decodes UTF-8 too."""
    import os

    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _pip_base_args(lockfile: Path) -> list[str]:
    # Defense-in-depth: no pip invocation may be reached with an
    # unvalidated lockfile (validated already at stage time; re-checked
    # right here so the real install can't be tricked either).
    validate_lockfile(lockfile)
    args = [
        sys.executable, "-m", "pip", "install",
        # --isolated: ignore pip.conf and PIP_* env so neither a config
        # file nor the environment can redirect the index or relax the
        # binary policy behind our back. The index below is passed on the
        # CLI (read by *us*, trusted), so it survives --isolated.
        "--isolated",
        "--require-hashes", "--only-binary=:all:",
        "-r", str(lockfile),
    ]
    # Pin the index (no implicit fallback — dependency-confusion door).
    import os

    index = os.environ.get("PIP_INDEX_URL") or "https://pypi.org/simple"
    args += ["--index-url", index, "--no-input"]
    return args


def pip_dry_run(lockfile: Path) -> dict[str, Any]:
    """§3.2 step 7 — resolve the lockfile INERTLY in a throwaway
    subprocess. ``--only-binary=:all:`` is security-load-bearing: without
    it pip runs sdist build backends (arbitrary code) just to read
    metadata, before the trust screen."""
    args = _pip_base_args(lockfile)
    args.insert(args.index("install") + 1, "--dry-run")
    args += ["--report", "-", "--quiet"]
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8",
        env=_pip_run_env(), timeout=600
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout or "").strip()
        if "sdist" in stderr.lower() or "--only-binary" in stderr:
            raise InstallError(
                "requirements_sdist",
                "a dependency resolves only as an sdist — publish wheels or "
                "vendor it (sdists never install; design §3.2 step 7)",
                {"pip": stderr[-2000:]},
            )
        raise InstallError(
            "requirements_unresolvable",
            f"pip dry-run failed: {stderr[-2000:]}",
        )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = {"install": []}

    resolved = []
    conflicts = []
    from importlib import metadata as importlib_metadata

    for item in report.get("install", []):
        meta = item.get("metadata", {})
        name, version = meta.get("name"), meta.get("version")
        hashed = bool(
            (item.get("download_info") or {}).get("archive_info", {}).get("hashes")
        )
        resolved.append({"name": name, "version": version, "hashed": hashed})
        if not name:
            continue
        try:
            installed_version = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            continue
        if installed_version != version:
            conflicts.append(
                {
                    "package": name,
                    "installed": installed_version,
                    "requested": version,
                    "required_by": lockfile.name,
                }
            )
    if conflicts:
        raise InstallError(
            "requirements_conflict",
            "install would change the version of already-installed "
            "distribution(s)",
            {"conflicts": conflicts},
        )
    return {"resolved": resolved}


def pip_install(lockfile: Path) -> dict[str, Any]:
    """§3.2 step 9 — the real install. Returns the pip_report persisted to
    the registry row: which dists were newly installed vs pre-existing."""
    from importlib import metadata as importlib_metadata

    before = {
        d.metadata["Name"].lower(): d.version
        for d in importlib_metadata.distributions()
        if d.metadata["Name"]
    }
    proc = subprocess.run(
        _pip_base_args(lockfile), capture_output=True, text=True,
        encoding="utf-8", env=_pip_run_env(), timeout=1800
    )
    if proc.returncode != 0:
        raise InstallError(
            "pip_install_failed",
            f"pip install failed: {(proc.stderr or '').strip()[-2000:]}",
        )
    # distributions() re-scans sys.path, so this sees the fresh installs.
    after = {}
    for d in importlib_metadata.distributions():
        name = d.metadata["Name"]
        if name:
            after[name.lower()] = d.version
    newly = sorted(set(after) - set(before))
    return {
        "newly_installed": [{"name": n, "version": after[n]} for n in newly],
        "preexisting": sorted(set(before) & set(after)),
    }


def pip_uninstall(dist_names: list[str]) -> None:
    if not dist_names:
        return
    subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", *dist_names],
        capture_output=True, text=True, timeout=600,
    )


# ─── Phase A — stage & validate ─────────────────────────────────────────────

@dataclass
class StagedInstall:
    staged_id: str
    root: Path                       # staging dir containing the plugin tree
    manifest: PluginManifest
    tree_hash: str
    source_ref: str | None
    install_source: str              # 'zip' | 'github'
    preview: dict[str, Any] = field(default_factory=dict)
    upgrade_of: str | None = None    # slug when this staging is an upgrade


_STAGED: dict[str, StagedInstall] = {}


async def stage_zip(
    data: bytes,
    *,
    source_ref: str | None = None,
    install_source: str = "zip",
    upgrade_of: str | None = None,
    force_downgrade: bool = False,
) -> StagedInstall:
    """§3.2 Phase A, steps 2–8. Raises InstallError (→ 422) on rejection."""
    if len(data) > MAX_ZIP_BYTES:
        raise InstallError(
            "zip_too_large", f"upload exceeds {MAX_ZIP_BYTES} bytes"
        )
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise InstallError("zip_invalid", f"not a valid zip archive: {e}")

    validate_zip_safety(zf)                       # step 2
    prefix = _zip_root_prefix(zf)                 # step 3

    staged_id = secrets.token_hex(16)             # 128-bit random (step 8)
    stage_dir = staging_root() / staged_id
    stage_dir.mkdir(parents=True, exist_ok=False)
    try:
        for info in zf.infolist():
            name = info.filename
            if prefix and not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            target = stage_dir / PurePosixPath(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

        # Step 4 — manifest parse + validation + layout.
        manifest_path = stage_dir / "domovoi-plugin.toml"
        if not manifest_path.is_file():
            raise InstallError("manifest_missing", "no domovoi-plugin.toml")
        try:
            manifest = parse_manifest(manifest_path.read_text(encoding="utf-8"))
        except ManifestError as e:
            raise InstallError("manifest_invalid", str(e))
        dir_errors = validate_plugin_dir(stage_dir, manifest)
        if dir_errors:
            raise InstallError("layout_invalid", "; ".join(dir_errors))

        # Install-time SQL lint over every migration file (§6.2).
        for mf in discover_migrations(stage_dir / manifest.migrations_dir):
            violations = sql_lint(mf.sql, manifest.slug)
            if violations:
                raise InstallError(
                    "migration_lint",
                    f"{mf.filename}: " + "; ".join(violations),
                )

        # Step 5 — web-entry import hygiene (AST tripwire).
        hygiene = check_web_import_hygiene(stage_dir, manifest)
        if hygiene:
            raise InstallError("web_import_hygiene", "; ".join(hygiene))

        # Step 6 — slug collision / orphan schema.
        existing = await reg.get_plugin(manifest.slug)
        if upgrade_of is None:
            if existing is not None and existing.status != "uninstalled":
                raise InstallError(
                    "slug_exists",
                    f"plugin {manifest.slug!r} is already installed — "
                    f"upgrades go through POST /v1/plugins/{manifest.slug}/upgrade",
                    # The dashboard uses this to transparently re-stage the
                    # same upload as an upgrade of that slug.
                    details={"slug": manifest.slug},
                )
            if existing is None and await reg.plugin_schema_exists(manifest.slug):
                # Two ways a schema can exist without a registry row:
                # (a) uninstall-with-keep (§3.5) — the ledger is intact and
                #     reinstall legitimately runs migration CATCH-UP; allowed.
                # (b) a purge crash left debris without a ledger — surfaced
                #     to the admin with a "purge leftovers" action; rejected.
                probe = PluginMigrationRunner(
                    manifest.slug, stage_dir / manifest.migrations_dir
                )
                if await probe.ledger_max_version() == 0:
                    raise InstallError(
                        "orphan_schema",
                        f"Postgres schema plugin_{manifest.slug} exists with no "
                        f"migration ledger and no registry row (leftover from a "
                        f"purge crash) — purge the leftovers first",
                    )
        else:
            if manifest.slug != upgrade_of:
                raise InstallError(
                    "upgrade_slug_mismatch",
                    f"archive is plugin {manifest.slug!r}, not {upgrade_of!r}",
                )
            if existing is None:
                raise InstallError(
                    "upgrade_missing", f"plugin {upgrade_of!r} is not installed"
                )
            cmp = _semver_cmp(manifest.version, existing.version)
            if cmp == 0:
                raise InstallError(
                    "upgrade_same_version",
                    f"version {manifest.version} is already installed",
                )
            if cmp < 0:
                # Downgrade: correctness/UX guard, forceable — EXCEPT across
                # an applied migration (hard ledger barrier, §3.6).
                runner = PluginMigrationRunner(
                    manifest.slug, stage_dir / manifest.migrations_dir
                )
                new_max = len(discover_migrations(stage_dir / manifest.migrations_dir))
                ledger_max = await runner.ledger_max_version()
                if ledger_max > new_max:
                    raise InstallError(
                        "downgrade_across_migrations",
                        f"applied migrations reach V{ledger_max:03d} but this "
                        f"version ships only V{new_max:03d} — migrations never "
                        f"run backwards; downgrade refused even with force",
                    )
                if not force_downgrade:
                    raise InstallError(
                        "downgrade_requires_force",
                        f"{manifest.version} < installed {existing.version} — "
                        f"downgrading may reintroduce vulnerabilities fixed in "
                        f"the newer version; repeat with force=true to proceed",
                    )

        # Step 7 — inert pip dry-run (only when python requirements exist).
        # Validate the lockfile FIRST: a poisoned lockfile (global pip
        # options) must fail here, before pip is ever invoked (§7.4).
        transitive: list[dict[str, Any]] = []
        if manifest.python_requirements:
            lock = stage_dir / (manifest.lockfile or "requirements.lock")
            validate_lockfile(lock)
            transitive = pip_dry_run(lock)["resolved"]

        tree_hash = hash_tree(stage_dir)          # step 8

        open_mutations = _collect_open_endpoints(manifest)
        preview = {
            "name": manifest.name,
            "version": manifest.version,
            "publisher": manifest.publisher,
            "license": manifest.license,
            "description": manifest.description,
            "permissions": {**manifest.permissions, "warnings": list(manifest.warnings)},
            "requirements": {
                "direct": list(manifest.python_requirements),
                "transitive": transitive,
            },
            "handlers": [
                # corpus = the manifest's canonical example utterances —
                # the trust screen shows THOSE (what can I say?), not
                # routing internals.
                {"name": h.name, "band": h.band, "label": h.label,
                 "corpus": list(h.corpus)}
                for h in manifest.handlers
            ],
            "migration_count": len(
                discover_migrations(stage_dir / manifest.migrations_dir)
            ),
            "capabilities": list(manifest.provides),
            "open_endpoints": open_mutations,
            "trust_statement": TRUST_STATEMENT,
        }
        staged = StagedInstall(
            staged_id=staged_id,
            root=stage_dir,
            manifest=manifest,
            tree_hash=tree_hash,
            source_ref=source_ref,
            install_source=install_source,
            preview=preview,
            upgrade_of=upgrade_of,
        )
        (stage_dir / ".domovoi-staging.json").write_text(
            json.dumps(
                {
                    "staged_id": staged_id,
                    "tree_hash": tree_hash,
                    "source_ref": source_ref,
                    "install_source": install_source,
                    "upgrade_of": upgrade_of,
                    "created_at": time.time(),
                }
            ),
            encoding="utf-8",
        )
        _STAGED[staged_id] = staged
        return staged
    except BaseException:
        shutil.rmtree(stage_dir, ignore_errors=True)   # rollback: steps 1–7
        raise


def _collect_open_endpoints(manifest: PluginManifest) -> list[str]:
    """The install preview lists opted-out mutating routes; static best
    effort — the authoritative audit runs at load (§13.2 check 6)."""
    return []  # populated at load time; kept in the preview shape for §4.11


def _semver_cmp(a: str, b: str) -> int:
    ta = tuple(int(x) for x in a.split("."))
    tb = tuple(int(x) for x in b.split("."))
    return (ta > tb) - (ta < tb)


# ─── GitHub install (§3.3) ─────────────────────────────────────────────────

async def download_github_zip(github_url: str) -> tuple[bytes, str]:
    """Resolve + download a repo archive with a streaming size cap.
    Returns (zip bytes, 'url@ref')."""
    import httpx

    m = _GITHUB_URL_RE.match(github_url.strip())
    if not m:
        raise InstallError(
            "github_url_invalid",
            "expected https://github.com/<owner>/<repo>[@ref]",
        )
    owner, repo, ref = m.group(1), m.group(2), m.group(3)
    headers = {
        "User-Agent": "domovoi/1.0.0 (+github.com/coders-farm-official/domovoi)"
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        if not ref:
            api = f"https://api.github.com/repos/{owner}/{repo}"
            r = await client.get(api, headers=headers)
            if r.status_code != 200:
                raise InstallError(
                    "github_repo_unavailable",
                    f"could not resolve default branch ({r.status_code})",
                )
            ref = r.json().get("default_branch") or "main"
        url = f"https://codeload.github.com/{owner}/{repo}/zip/{ref}"
        buf = bytearray()
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                raise InstallError(
                    "github_download_failed",
                    f"codeload returned {resp.status_code} for {url}",
                )
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_ZIP_BYTES:
                    raise InstallError(
                        "zip_too_large",
                        f"download exceeded {MAX_ZIP_BYTES} bytes — aborted",
                    )
    return bytes(buf), f"{github_url}@{ref}"


# ─── Phase B — confirm (§3.2 steps 9–15 + rollback matrix) ─────────────────

async def confirm_install(staged_id: str) -> dict[str, Any]:
    staged = _STAGED.get(staged_id)
    if staged is None:
        raise InstallError("staged_id_unknown", "unknown or expired staged_id")
    manifest = staged.manifest
    slug = manifest.slug

    # Step 9a — TOCTOU close: re-hash the staged tree.
    meta_file = staged.root / ".domovoi-staging.json"
    tree_now = hash_tree_excluding(staged.root, {meta_file.name})
    if tree_now != staged.tree_hash:
        _drop_staged(staged)
        raise InstallError(
            "staging_modified",
            "staged tree changed between preview and confirm — install aborted",
        )

    pip_report: dict[str, Any] | None = None
    moved_to: Path | None = None
    registry_inserted = False
    runner = PluginMigrationRunner(slug, staged.root / manifest.migrations_dir)
    # A schema that pre-exists (reinstall-after-keep, §3.5) carries user
    # data — rollback must NEVER drop it; only a brand-new schema is
    # dropped on failure (§3.2 matrix row 10).
    schema_was_fresh = not await runner.schema_exists()

    try:
        # Step 9b — real pip install.
        if manifest.python_requirements:
            lock = staged.root / (manifest.lockfile or "requirements.lock")
            pip_report = pip_install(lock)

        # Step 10 — migrations, both DBs (the runner owns fresh-install
        # both-or-neither internally).
        await runner.apply_all()

        # Step 11 — move staging → installed/<slug>/.
        meta_file.unlink(missing_ok=True)
        dest = installed_root() / slug
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise InstallError(
                "install_dir_exists", f"{dest} already exists"
            )
        shutil.move(str(staged.root), str(dest))
        moved_to = dest
        # Migrations must now run from the moved dir on any later catch-up.
        runner = PluginMigrationRunner(slug, dest / manifest.migrations_dir)

        # Step 12 — registry insert (or tombstone revival for bundled).
        existing = await reg.get_plugin(slug)
        if existing is not None and existing.status == "uninstalled":
            # Revival over a tombstone. If the tombstone was a BUNDLED
            # plugin's (§3.5), we must clear bundled=True — otherwise the
            # loader's bundled-dir heal (§3.7) repoints install_dir back
            # into the repo tree and core silently runs the bundled code
            # while this manifest describes the zip/github source. A
            # zip/github install always becomes a normal non-bundled row.
            await reg.update_plugin(
                slug,
                status="ok", enabled=True, version=manifest.version,
                install_dir=str(dest), manifest=manifest.raw,
                pip_report=pip_report, install_source=staged.install_source,
                source_ref=staged.source_ref, bundled=False, last_error=None,
            )
        else:
            await reg.insert_plugin(
                slug=slug, name=manifest.name, version=manifest.version,
                publisher=manifest.publisher, license=manifest.license,
                domovoi_api=manifest.domovoi_api, enabled=True, bundled=False,
                install_source=staged.install_source,
                source_ref=staged.source_ref, install_dir=str(dest),
                manifest=manifest.raw, pip_report=pip_report,
            )
        registry_inserted = True

        # Step 13 — hot load + contract checks. Failure here is NOT rolled
        # back like earlier steps: status='load_error', enabled=false, files
        # + schema kept for inspection (§3.2 matrix row 13).
        try:
            await LOADER.load_plugin(
                slug=slug, install_dir=dest, manifest=manifest
            )
        except (ContractError, Exception) as e:
            _STAGED.pop(staged_id, None)
            return {
                "installed": True,
                "loaded": False,
                "slug": slug,
                "status": "load_error",
                "error": str(e),
            }

        # Step 14 — Letta/chat resync (non-fatal).
        resync_warning = None
        try:
            from domovoi.plugins_runtime.letta_resync import resync_tools

            await resync_tools()
        except Exception as e:  # noqa: BLE001 — §3.2 matrix rows 14–15
            resync_warning = f"chat tool resync failed: {e}"
            log.warning("%s", resync_warning)

        # Step 15 (web pickup) rides the plugins_changed NOTIFY from step 12.
        _STAGED.pop(staged_id, None)
        result: dict[str, Any] = {
            "installed": True, "loaded": True, "slug": slug,
            "version": manifest.version, "status": "ok",
        }
        if resync_warning:
            result["warning"] = resync_warning
        return result

    except InstallError:
        await _rollback(
            staged, pip_report, schema_was_fresh, moved_to,
            registry_inserted, runner,
        )
        raise
    except Exception as e:
        await _rollback(
            staged, pip_report, schema_was_fresh, moved_to,
            registry_inserted, runner,
        )
        raise InstallError("install_failed", f"install failed: {e}") from e


def hash_tree_excluding(root: Path, exclude_names: set[str]) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.name in exclude_names:
            continue
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            h.update(f"D:{rel}\n".encode())
        else:
            h.update(f"F:{rel}:{path.stat().st_size}\n".encode())
            h.update(path.read_bytes())
    return h.hexdigest()


async def _rollback(
    staged: StagedInstall,
    pip_report: dict[str, Any] | None,
    schema_was_fresh: bool,
    moved_to: Path | None,
    registry_inserted: bool,
    runner: PluginMigrationRunner,
) -> None:
    """§3.2 failure matrix, rows 9–12 (later rows don't roll back). The
    schema is dropped only when it was BRAND NEW this install — a kept
    schema from an earlier uninstall (§3.5 reinstall-after-keep) carries
    user data and survives."""
    slug = staged.manifest.slug
    if registry_inserted:
        try:
            await reg.delete_plugin(slug)
        except Exception:  # pragma: no cover
            log.exception("rollback: registry delete failed")
    if moved_to is not None:
        shutil.rmtree(moved_to, ignore_errors=True)
    if schema_was_fresh:
        try:
            await runner.drop_schema()
        except Exception:  # pragma: no cover
            log.exception("rollback: schema drop failed")
    if pip_report:
        pip_uninstall(
            [d["name"] for d in pip_report.get("newly_installed", [])]
        )
    _drop_staged(staged)


def _drop_staged(staged: StagedInstall) -> None:
    _STAGED.pop(staged.staged_id, None)
    shutil.rmtree(staged.root, ignore_errors=True)


# ─── enable / disable (§3.4) ────────────────────────────────────────────────

async def enable_plugin(slug: str) -> dict[str, Any]:
    row = await reg.get_plugin(slug)
    if row is None or row.status == "uninstalled":
        raise InstallError("not_installed", f"plugin {slug!r} is not installed")
    install_dir = Path(row.install_dir)
    manifest = parse_manifest(
        (install_dir / "domovoi-plugin.toml").read_text(encoding="utf-8")
    )
    # Migration catch-up on BOTH DBs (a newer version may have been copied
    # in while disabled), then load + contract checks + resync.
    runner = PluginMigrationRunner(slug, install_dir / manifest.migrations_dir)
    await runner.apply_all()
    await reg.set_enabled(slug, True)
    try:
        await LOADER.load_plugin(slug=slug, install_dir=install_dir, manifest=manifest)
    except Exception as e:
        return {"enabled": False, "slug": slug, "status": "load_error", "error": str(e)}
    await _best_effort_resync()
    return {"enabled": True, "slug": slug}


async def disable_plugin(slug: str) -> dict[str, Any]:
    row = await reg.get_plugin(slug)
    if row is None:
        raise InstallError("not_installed", f"plugin {slug!r} is not installed")
    await LOADER.unload_plugin(slug)
    await reg.set_enabled(slug, False)
    await _best_effort_resync()
    return {"enabled": False, "slug": slug}


async def _best_effort_resync() -> None:
    try:
        from domovoi.plugins_runtime.letta_resync import resync_tools

        await resync_tools()
    except Exception as e:  # noqa: BLE001 — non-fatal by design
        log.warning("chat tool resync failed: %s", e)


# ─── uninstall keep / purge (§3.5) ──────────────────────────────────────────

async def uninstall_plugin(slug: str, *, data: str = "keep") -> dict[str, Any]:
    if data not in ("keep", "purge"):
        raise InstallError("bad_request", "data must be 'keep' or 'purge'")
    row = await reg.get_plugin(slug)
    if row is None:
        raise InstallError("not_installed", f"plugin {slug!r} is not installed")

    # 1. Full disable teardown.
    await LOADER.unload_plugin(slug)

    manifest = row.manifest or {}
    migrations_dir = (manifest.get("assets") or {}).get("migrations_dir", "migrations")
    runner = PluginMigrationRunner(slug, Path(row.install_dir) / migrations_dir)

    # 2. Data policy.
    if data == "purge":
        await runner.drop_schema()
        shutil.rmtree(data_root() / slug, ignore_errors=True)

    # 3. pip refcount: uninstall this plugin's pinned dists only when no
    #    other non-uninstalled plugin declares the same distribution and the
    #    dist was newly installed by THIS plugin (pip_report, §3.2 step 9).
    newly = {
        d["name"].lower()
        for d in (row.pip_report or {}).get("newly_installed", [])
    }
    if newly:
        others: set[str] = set()
        for other in await reg.list_plugins():
            if other.slug == slug or other.status == "uninstalled":
                continue
            for req in ((other.manifest.get("requirements") or {}).get("python") or []):
                others.add(req.split("==")[0].split("[")[0].lower())
        pip_uninstall(sorted(newly - others))

    # 4/5. Bundled → tombstone (never delete dir or row); else remove both.
    if row.bundled:
        await reg.tombstone_plugin(slug)
    else:
        shutil.rmtree(Path(row.install_dir), ignore_errors=True)
        await reg.delete_plugin(slug)

    await _best_effort_resync()
    return {"uninstalled": True, "slug": slug, "data": data, "bundled": row.bundled}


# ─── upgrade (§3.6) ─────────────────────────────────────────────────────────

async def confirm_upgrade(staged_id: str) -> dict[str, Any]:
    """Confirm a staged UPGRADE: teardown old → move old dir to .previous →
    run the normal confirm pipeline → best-effort restore on failure."""
    staged = _STAGED.get(staged_id)
    if staged is None:
        raise InstallError("staged_id_unknown", "unknown or expired staged_id")
    slug = staged.upgrade_of
    if not slug:
        raise InstallError("bad_request", "staged id is not an upgrade")
    row = await reg.get_plugin(slug)
    if row is None:
        raise InstallError("upgrade_missing", f"plugin {slug!r} is not installed")

    old_dir = Path(row.install_dir)
    prev_dir = previous_root() / f"{slug}-{row.version}"
    prev_dir.parent.mkdir(parents=True, exist_ok=True)
    manifest = staged.manifest
    mig_runner = PluginMigrationRunner(
        slug, staged.root / manifest.migrations_dir
    )
    pre_upgrade_ledger = await mig_runner.ledger_max_version()

    await LOADER.unload_plugin(slug)
    old_row = row
    if old_dir.is_dir():
        if prev_dir.exists():
            shutil.rmtree(prev_dir, ignore_errors=True)
        shutil.move(str(old_dir), str(prev_dir))
    # The registry row must vanish so confirm_install's insert works; we
    # restore it on failure below.
    await reg.delete_plugin(slug)

    try:
        result = await confirm_install(staged_id)
        shutil.rmtree(prev_dir, ignore_errors=True)
        result["upgraded_from"] = old_row.version
        return result
    except BaseException as e:
        # Roll back: previous dir returns, old row returns, old version
        # re-enables. Migrations that already applied STAY applied — warn
        # loudly naming them (§3.6, DB rule 5).
        new_ledger = await mig_runner.ledger_max_version()
        if new_ledger > pre_upgrade_ledger:
            log.error(
                "upgrade of %s failed AFTER migrations V%03d..V%03d applied — "
                "they stay applied; the previous version must be "
                "one-version backward compatible (design §6.2 rule 5)",
                slug, pre_upgrade_ledger + 1, new_ledger,
            )
        if prev_dir.is_dir() and not old_dir.exists():
            old_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(prev_dir), str(old_dir))
        try:
            await reg.insert_plugin(
                slug=slug, name=old_row.name, version=old_row.version,
                publisher=old_row.publisher, license=old_row.license,
                domovoi_api=old_row.domovoi_api, enabled=old_row.enabled,
                bundled=old_row.bundled, install_source=old_row.install_source,
                source_ref=old_row.source_ref, install_dir=old_row.install_dir,
                manifest=old_row.manifest, pip_report=old_row.pip_report,
            )
            if old_row.enabled:
                old_manifest = parse_manifest(
                    (old_dir / "domovoi-plugin.toml").read_text(encoding="utf-8")
                )
                await LOADER.load_plugin(
                    slug=slug, install_dir=old_dir, manifest=old_manifest
                )
        except Exception:  # pragma: no cover — double fault
            log.exception("upgrade rollback of %s failed", slug)
        if isinstance(e, InstallError):
            raise
        raise InstallError("upgrade_failed", f"upgrade failed: {e}") from e


# ─── the admin HTTP surface (§3.2/§7.3 gated list) ──────────────────────────

plugins_admin_router = APIRouter(tags=["plugins-admin"])


def _install_error_response(e: InstallError) -> HTTPException:
    # Log the rejection reason at the source — the access log's bare 422
    # says nothing, and operators read consoles before dashboards.
    log.warning("plugin install rejected: %s — %s", e.code, e)
    return HTTPException(
        status_code=422,
        detail={"error": {"code": e.code, "message": str(e), "details": e.details}},
    )


async def _read_install_body(
    request: Request,
) -> tuple[bytes | None, str | None, str | None, bool]:
    """(zip bytes, source_ref, github_url, force) from either a multipart
    form (file field ``file``) or a JSON body ({github_url, force}).
    Content-type sniffing avoids FastAPI's exclusive body-model binding."""
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise InstallError(
                "bad_request", "multipart form must carry a zip in field 'file'"
            )
        return await upload.read(), upload.filename, None, bool(form.get("force"))
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    return None, None, body.get("github_url"), bool(body.get("force"))


@plugins_admin_router.post(
    "/v1/plugins/install", dependencies=[Depends(require_admin)]
)
async def api_install(request: Request) -> dict:
    """Phase A — stage & validate a zip upload (multipart field ``file``)
    or a GitHub URL (JSON body ``{"github_url": ...}``)."""
    try:
        data, source_ref, github_url, _force = await _read_install_body(request)
        if data is not None:
            staged = await stage_zip(data, source_ref=source_ref)
        else:
            if not github_url:
                raise InstallError(
                    "bad_request",
                    "send a multipart zip in field 'file' or JSON "
                    "{'github_url': ...}",
                )
            data, source_ref = await download_github_zip(github_url)
            staged = await stage_zip(
                data, source_ref=source_ref, install_source="github"
            )
    except InstallError as e:
        raise _install_error_response(e)
    return {"staged_id": staged.staged_id, "preview": staged.preview}


@plugins_admin_router.post(
    "/v1/plugins/install/{staged_id}/confirm",
    dependencies=[Depends(require_admin)],
)
async def api_confirm(staged_id: str) -> dict:
    try:
        staged = _STAGED.get(staged_id)
        if staged is not None and staged.upgrade_of:
            return await confirm_upgrade(staged_id)
        return await confirm_install(staged_id)
    except InstallError as e:
        raise _install_error_response(e)


@plugins_admin_router.post(
    "/v1/plugins/{slug}/enable", dependencies=[Depends(require_admin)]
)
async def api_enable(slug: str) -> dict:
    try:
        return await enable_plugin(slug)
    except InstallError as e:
        raise _install_error_response(e)


@plugins_admin_router.post(
    "/v1/plugins/{slug}/disable", dependencies=[Depends(require_admin)]
)
async def api_disable(slug: str) -> dict:
    try:
        return await disable_plugin(slug)
    except InstallError as e:
        raise _install_error_response(e)


@plugins_admin_router.post(
    "/v1/plugins/{slug}/uninstall", dependencies=[Depends(require_admin)]
)
async def api_uninstall(slug: str, body: dict | None = None) -> dict:
    try:
        return await uninstall_plugin(slug, data=(body or {}).get("data", "keep"))
    except InstallError as e:
        raise _install_error_response(e)


@plugins_admin_router.post(
    "/v1/plugins/{slug}/upgrade", dependencies=[Depends(require_admin)]
)
async def api_upgrade(request: Request, slug: str) -> dict:
    """Stage an upgrade (same two-phase flow: returns staged_id + preview;
    confirm via the shared confirm endpoint)."""
    try:
        row = await reg.get_plugin(slug)
        if row is None:
            raise InstallError("not_installed", f"plugin {slug!r} is not installed")
        if row.install_source == "dev":
            raise InstallError(
                "dev_mode", "dev-mode plugins refuse upgrade — just restart"
            )
        data, source_ref, github_url, force = await _read_install_body(request)
        install_source = "zip"
        if data is None:
            if not github_url:
                raise InstallError(
                    "bad_request",
                    "send a multipart zip in field 'file' or JSON "
                    "{'github_url': ...}",
                )
            data, source_ref = await download_github_zip(github_url)
            install_source = "github"
        staged = await stage_zip(
            data, source_ref=source_ref, upgrade_of=slug, force_downgrade=force,
            install_source=install_source,
        )
    except InstallError as e:
        raise _install_error_response(e)
    return {"staged_id": staged.staged_id, "preview": staged.preview}
