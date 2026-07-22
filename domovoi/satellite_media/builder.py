"""Media-prep build orchestration.

One build = resolve → refresh caches (only what's missing) → assemble the
offline payload → render the overlay → write it to the target (a mounted
boot partition, or a downloadable zip). Long-running (minutes on a cold
cache), so it runs as a background job in the web process (the
``satellite_media_jobs`` table + realtime channel own progress reporting;
this module only calls back).

Cache misses NEVER hard-fail a build: the affected piece degrades to the
device's online stage-2 with a warning, and ``offline`` flips to False in
build-info.json so nobody is lied to about what the card can do without
internet.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from domovoi import git_version
from domovoi.config import settings
from domovoi.satellite_media import cache, fetchers, overlay, payload
from domovoi.satellite_media.boards import BOARDS, MIC_PROFILES

log = logging.getLogger(__name__)

# The account stage 1 creates on the device.
SAT_USER = "domovoi"

BUILDS_ROOT = Path("~/.domovoi/satellite_media/builds").expanduser()

Progress = Callable[[str, int, str], Awaitable[None]]  # (phase, pct, text)


async def _noop_progress(phase: str, pct: int, text: str) -> None:
    return None


async def build(
    *,
    board_id: str,
    mic_profile: str,
    target_kind: str,               # "drive" | "zip"
    target_mount: Path | None,
    job_id: str,
    offline: bool = True,
    sat_type: str = "voice",
    progress: Progress = _noop_progress,
) -> dict[str, Any]:
    """Run one build. Returns {ok, warnings, artifact_path?, written?,
    offline}. Raises on unbuildable inputs (unsupported board, bad target);
    degradable problems become warnings instead."""
    board = BOARDS.get(board_id)
    if board is None or not board.supported:
        raise ValueError(f"board {board_id!r} is not supported for prepared media")
    if mic_profile not in MIC_PROFILES:
        raise ValueError(f"unknown mic profile {mic_profile!r}")
    if target_kind == "drive":
        if target_mount is None or not target_mount.is_dir():
            raise ValueError("target drive is not mounted")
        if not overlay.looks_like_pi_boot(target_mount, board.boot_marker):
            raise ValueError(
                "target doesn't look like a flashed boot partition for this "
                "board (flash stock OS first, then re-insert)"
            )

    repo_root = Path(settings.repo_dir)
    warnings: list[str] = []

    # ── Phase 1: caches ───────────────────────────────────────────────
    await progress("fetch", 5, "checking artifact caches")
    st = cache.status(board.python_version, board.os_release)
    if offline:
        if not st["wheels"]["ok"]:
            await progress("fetch", 10, "downloading aarch64 wheels (first run is slow)")
            ok, msg = fetchers.fetch_wheels(
                repo_root, board.python_version, board.manylinux_platforms
            )
            if not ok:
                warnings.append(msg)
        if not st["debs"]["ok"]:
            await progress("fetch", 30, "fetching arm64 packages")
            extra: list[str] = []
            for entry in await payload.enabled_satellite_plugins():
                extra += entry["decl"]["apt_packages"]
            ok, msg = fetchers.fetch_debs(board.os_release, tuple(extra))
            if not ok:
                warnings.append(msg)
        if not st["oww_models"]["ok"]:
            await progress("fetch", 40, "caching wake-word base models")
            ok, msg = fetchers.fetch_oww_models()
            if not ok:
                warnings.append(msg)

    # ── Phase 2: assemble ─────────────────────────────────────────────
    await progress("assemble", 50, "assembling offline payload")
    workspace = BUILDS_ROOT / job_id
    workspace.mkdir(parents=True, exist_ok=True)
    asm = await payload.assemble(
        workspace,
        repo_root=repo_root,
        python_version=board.python_version,
        os_release=board.os_release,
        progress=lambda m: None,
    )
    warnings += asm["warnings"]
    effective_offline = offline and not any(
        "wheels" in w or "cache bucket 'wheels'" in w for w in warnings
    )

    await progress("assemble", 65, "rendering bootstrap scripts")
    stage2 = overlay.render_stage2(SAT_USER)
    fin = payload.finalize(workspace, asm["dir"], stage2)
    firstrun = overlay.render_firstrun(SAT_USER, mic_profile, sat_type)
    core_sha = await git_version.current_sha()
    info = overlay.build_info(
        board=board.id,
        mic_profile=mic_profile,
        sat_type=sat_type,
        core_sha=core_sha,
        python_version=board.python_version,
        os_release=board.os_release,
        plugins=asm["plugins"],
        offline=effective_offline,
    )
    device_info = overlay.initial_device_info(sat_type)

    # ── Phase 3: write ────────────────────────────────────────────────
    if target_kind == "drive":
        assert target_mount is not None
        await progress("write", 75, f"checking free space on {target_mount}")
        free = shutil.disk_usage(target_mount).free
        needed = fin["bytes"] + 8 * 1024 * 1024
        if free < needed:
            raise ValueError(
                f"boot partition has {free // (1024 * 1024)} MiB free but the "
                f"payload needs {needed // (1024 * 1024)} MiB — trim plugin "
                f"payloads or build with offline=false"
            )
        await progress("write", 80, "writing overlay to the card")
        written = overlay.write_overlay(
            target_mount,
            payload_tar=fin["tar"],
            payload_sha256=fin["sha256"],
            firstrun=firstrun,
            info=info,
            device_info=device_info,
        )
        await progress("done", 100, "card ready — eject, boot, then plug into this machine to adopt")
        result: dict[str, Any] = {"ok": True, "written": written}
    else:
        await progress("write", 80, "building overlay zip")
        staging = workspace / "overlay"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        # The zip carries only the domovoi/ dir + a README; config/cmdline
        # edits can't be applied to a not-present card, so the README
        # instructs, and firstrun re-checks.
        overlay.write_overlay(
            staging,
            payload_tar=fin["tar"],
            payload_sha256=fin["sha256"],
            firstrun=firstrun,
            info=info,
            device_info=device_info,
        )
        (staging / "README.txt").write_text(
            "Domovoi satellite overlay\n"
            "1. Flash stock OS for your board with any imaging tool.\n"
            "2. Copy the domovoi/ folder to the card's boot partition, and\n"
            "   apply the config.txt/cmdline.txt additions from this zip's\n"
            "   copies (each addition is marked with a domovoi comment).\n"
            "3. Boot the device, then plug it into the Domovoi server's USB\n"
            "   port and adopt it from the dashboard's Satellites page.\n",
            encoding="utf-8",
        )
        artifact = workspace / "domovoi-satellite-overlay.zip"
        with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(staging.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(staging))
        await progress("done", 100, "overlay zip ready to download")
        result = {"ok": True, "artifact_path": str(artifact)}

    # Workspace hygiene: the payload dir is large; keep only the artifacts.
    try:
        shutil.rmtree(asm["dir"])
        if target_kind == "drive":
            fin["tar"].unlink(missing_ok=True)
    except OSError:
        pass

    result["warnings"] = warnings
    result["offline"] = effective_offline
    return result
