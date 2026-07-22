"""Assemble the offline satellite payload (payload.tar.gz).

Everything Domovoi-specific comes from the LOCAL machine: the satellite
source tree (same allowlist as the /v1/satellite-code channel), enabled
plugins' [satellite] payloads, rendered sounds + trained wake models, the
wheel/deb/model caches, systemd units, and the bootstrap scripts. The
result is a tar.gz + sha256 that stage 1 verifies and unpacks fully
offline.

Layout (see the plan's §1.2):

    payload/
      manifest.json          {schema, files: {rel: sha256}}
      satellite/ …           code snapshot
      pyproject.toml
      wheels/ debs/ dtbo/ oww_models/ sounds/ wake_models/
      plugins/<slug>/ …      + <slug>/.meta.json
      system/  …             units + sudoers + apply-payload
      bootstrap/stage2.sh
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tarfile
from pathlib import Path
from typing import Any, Callable

from domovoi.config import settings
from domovoi.satellite_media import cache, overlay
from domovoi.satellite_payload import enabled_satellite_plugins, payload_files

log = logging.getLogger(__name__)

# Keep in sync with domovoi/main.py::_SAT_CODE_EXT_ALLOW (duplicated here
# so assembling a payload never imports the core app module).
_CODE_EXT_ALLOW = frozenset({".py", ".toml", ".txt", ".md", ".service", ".sh", ".json"})


def _allowed_code_file(p: Path) -> bool:
    if p.suffix not in _CODE_EXT_ALLOW:
        return False
    if "__pycache__" in p.parts or "tests" in p.parts:
        return False
    name = p.name
    if not name or name.endswith((".bak", ".pyc")) or name.startswith(".env"):
        return False
    return True


def _copy_tree(src: Path, dest: Path, keep: Callable[[Path], bool]) -> int:
    n = 0
    for f in sorted(src.rglob("*")):
        if f.is_symlink() or not f.is_file() or not keep(f):
            continue
        rel = f.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        n += 1
    return n


async def assemble(
    workspace: Path,
    *,
    repo_root: Path,
    python_version: str,
    os_release: str,
    progress: Callable[[str], None] = lambda msg: None,
) -> dict[str, Any]:
    """Build ``payload/`` under ``workspace``, then tar it. Returns
    ``{"tar": Path, "sha256": str, "bytes": int, "plugins": [...],
    "warnings": [...]}``."""
    pay = workspace / "payload"
    if pay.exists():
        shutil.rmtree(pay)
    pay.mkdir(parents=True)
    warnings: list[str] = []

    progress("collecting satellite code")
    n = _copy_tree(repo_root / "satellite", pay / "satellite", _allowed_code_file)
    if n == 0:
        raise RuntimeError(f"no satellite code found under {repo_root}")
    if (repo_root / "pyproject.toml").is_file():
        shutil.copy2(repo_root / "pyproject.toml", pay / "pyproject.toml")

    progress("collecting caches")
    for bucket_name, sub in (
        ("wheels", python_version),
        ("debs", os_release),
        ("dtbo", None),
        ("oww_models", None),
    ):
        src = cache.CACHE_ROOT / bucket_name / (sub or "")
        src = Path(str(src).rstrip("/\\"))
        if src.is_dir() and any(src.iterdir()):
            _copy_tree(src, pay / bucket_name, lambda p: True)
        else:
            warnings.append(f"cache bucket {bucket_name!r} is empty")

    progress("collecting sounds + wake models")
    sounds_dir = Path(getattr(settings, "sounds_dir", "")) if getattr(settings, "sounds_dir", "") else None
    if sounds_dir and sounds_dir.is_dir():
        _copy_tree(sounds_dir, pay / "sounds", lambda p: True)
    wm = Path(settings.wake_models_dir) if getattr(settings, "wake_models_dir", "") else None
    if wm and wm.is_dir():
        _copy_tree(wm, pay / "wake_models", lambda p: True)

    progress("collecting plugin payloads")
    plugins_meta: list[dict[str, str]] = []
    for entry in await enabled_satellite_plugins():
        slug, decl = entry["slug"], entry["decl"]
        files = payload_files(entry["root"], decl)
        pdir = pay / "plugins" / slug
        for rel, src_path in files.items():
            target = pdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, target)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / ".meta.json").write_text(
            json.dumps({
                "version": entry["version"],
                "apt_packages": decl["apt_packages"],
                "pip_requirements": decl["pip_requirements"],
                "post_install": decl["post_install"],
            }, indent=2),
            encoding="utf-8",
        )
        plugins_meta.append({"slug": slug, "version": entry["version"]})

    progress("collecting system files")
    system = pay / "system"
    system.mkdir()
    sat_dir = repo_root / "satellite"
    shutil.copy2(sat_dir / "domovoi-satellite.service", system / "domovoi-satellite.service")
    shutil.copy2(
        sat_dir / "scripts" / "domovoi-provisioning.service",
        system / "domovoi-provisioning.service",
    )
    shutil.copy2(
        overlay.TEMPLATES_DIR / "domovoi-bootstrap.service",
        system / "domovoi-bootstrap.service",
    )
    shutil.copy2(
        sat_dir / "scripts" / "domovoi-apply-payload",
        system / "domovoi-apply-payload",
    )
    (system / "sudoers").write_text(
        overlay.render_template("sudoers.tmpl", {}), encoding="utf-8", newline="\n"
    )

    # manifest.json over everything assembled so far (+ bootstrap below).
    progress("hashing payload")
    boot = pay / "bootstrap"
    boot.mkdir()
    # stage2 is rendered per-build by the builder (needs the user); a
    # placeholder keeps the layout stable if the builder skips rendering.
    manifest: dict[str, str] = {}
    for f in sorted(pay.rglob("*")):
        if f.is_file():
            manifest[f.relative_to(pay).as_posix()] = hashlib.sha256(
                f.read_bytes()
            ).hexdigest()
    (pay / "manifest.json").write_text(
        json.dumps({"schema": 1, "files": manifest}, indent=2), encoding="utf-8"
    )

    return {"dir": pay, "plugins": plugins_meta, "warnings": warnings}


def finalize(workspace: Path, pay: Path, stage2: str) -> dict[str, Any]:
    """Drop the rendered stage-2 script in, tar the payload, and hash it."""
    (pay / "bootstrap" / "stage2.sh").write_text(
        stage2, encoding="utf-8", newline="\n"
    )
    tar_path = workspace / "payload.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(pay, arcname="payload")
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    return {"tar": tar_path, "sha256": digest, "bytes": tar_path.stat().st_size}
