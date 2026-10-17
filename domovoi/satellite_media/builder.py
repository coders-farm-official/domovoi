"""Media-prep build orchestration.

One build = resolve → refresh caches (only what's missing) → assemble the
offline payload → render the overlay → write it to the target (a mounted
boot partition, or a downloadable zip). Long-running (minutes on a cold
cache), so it runs as a background job in the web process (the
``satellite_media_jobs`` table + realtime channel own progress reporting;
this module only calls back).

Cache misses do not hard-fail a build: the affected piece degrades to the
device's online stage-2 with a warning, and ``offline`` flips to False in
build-info.json so nobody is lied to about what the card can do without
internet. The one exception is the wake-word models: an offline card is
built only with every pinned model in the cache, verified (SAT-11).
"""

from __future__ import annotations

import logging
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from domovoi import git_version, host_time, server_identity
from domovoi.config import settings
from domovoi.host_kind import is_wsl
from domovoi.satellite_media import cache, fetchers, overlay, payload
from domovoi.satellite_media.boards import BOARDS, MIC_PROFILES

log = logging.getLogger(__name__)

# The account stage 1 creates on the device.
SAT_USER = "domovoi"

BUILDS_ROOT = Path("~/.domovoi/satellite_media/builds").expanduser()

Progress = Callable[[str, int, str], Awaitable[None]]  # (phase, pct, text)


async def _noop_progress(phase: str, pct: int, text: str) -> None:
    return None


def zip_readme(ap: dict | None, *, windows_host: bool) -> str:
    """README.txt of an overlay zip.

    ``windows_host``: this server runs inside WSL on Windows (the Windows
    installer's), which can't see SD cards, so the zip is the only way to
    build a card there and the tray icon's card helper puts it on the card
    (windows/tray, CardWriter.cs). That README leads with the helper and
    keeps the steps by hand below it, worded for a household: the two
    files are added to, never replaced (a replaced cmdline.txt doesn't
    boot), and ap.json is saved as "All files" so Notepad doesn't make it
    ap.json.txt. Other hosts keep the short README they always had."""
    if windows_host:
        return _windows_readme(ap)
    readme = [
        "Domovoi satellite overlay",
        "1. Flash stock OS for your board with any imaging tool.",
        "2. Copy the domovoi/ folder to the card's boot partition, and",
        "   apply the config.txt/cmdline.txt additions from this zip's",
        "   copies (each addition is marked with a domovoi comment).",
    ]
    if ap is not None:
        # A portal card never goes near the server's USB port.
        readme += [
            "3. Create domovoi/ap.json (below), then boot the device. It",
            "   raises its own Wi-Fi setup network: set it up from a phone,",
            "   then approve it in the dashboard's Settings -> Satellites.",
        ]
    else:
        readme += [
            "3. Boot the device, then plug it into the Domovoi server's USB",
            "   port and adopt it from the dashboard's Settings -> Satellites.",
        ]
    readme += [
        "",
        "This zip deliberately contains no passwords. The dashboard shows",
        f"the console login for {SAT_USER} once, when the build finishes —",
        "write it down then. The card itself only carries the hash.",
    ]
    if ap is not None:
        readme += [
            "",
            "This is a Wi-Fi setup (portal) card. Stage 1 reads its setup",
            "network's key from domovoi/ap.json, which is not in this zip.",
            "Create it on the boot partition with the password the",
            "dashboard showed you:",
            "",
            '  {"ssid": "' + str(ap["ssid"]) + '", "psk": "<the password>"}',
            "",
            "Without that file the unit falls back to USB adoption.",
        ]
    return "\n".join(readme) + "\n"


def _windows_readme(ap: dict | None) -> str:
    portal = ap is not None
    readme = [
        "On Windows, use Domovoi's tray icon → Prepare a satellite card.",
        "",
        "It puts these files on the card for you, checks them, and asks for",
        "the setup network password the dashboard showed when it built them.",
        "The icon is beside the clock (the amber cat; it may be under the ^",
        "arrow).",
        "",
        "",
        "Doing it by hand instead",
        "------------------------",
        "",
        "1. Flash the card with Raspberry Pi OS Lite (64-bit), using",
        "   Raspberry Pi Imager. When Imager offers to apply OS customisation",
        "   settings, choose No.",
        "2. Put the card back in this PC. Its boot partition opens as a drive",
        "   called \"bootfs\".",
        "3. Copy this zip's domovoi folder and userconf.txt onto that drive.",
        "4. config.txt: open the card's own config.txt in Notepad, go to the",
        "   very end, and paste all the lines of this zip's config.txt there,",
        "   below everything else. Save it.",
        "5. cmdline.txt: open the card's own cmdline.txt in Notepad. It is",
        "   one long line. Click on it and press End (the end of that line,",
        "   not below it), type a space, and paste the line from this zip's",
        "   cmdline.txt there, so everything stays on that one line. Save",
        "   it. Never replace the card's cmdline.txt with this zip's: a Pi",
        "   doesn't start without the card's own line.",
    ]
    if portal:
        readme += [
            "6. ap.json: in Notepad, start a new file and type exactly this one",
            "   line, with the setup network password the dashboard showed you",
            "   in place of <the password>:",
            "",
            '     {"ssid": "' + str(ap["ssid"]) + '", "psk": "<the password>"}',
            "",
            "   Save it in the card's domovoi folder (domovoi/ap.json) with the",
            "   name ap.json, and under \"Save as type\" choose \"All files\", so",
            "   Notepad doesn't call it ap.json.txt.",
            "7. Eject the card, put it in the Pi and switch it on. It raises its",
            "   own Wi-Fi setup network: set it up from a phone, then approve it",
            "   in the dashboard's Settings -> Satellites.",
            "",
            "Without ap.json the satellite never raises its setup network.",
        ]
    else:
        readme += [
            "6. Eject the card, put it in the Pi and switch it on,",
            "   then plug it into the Domovoi server's USB port and adopt it",
            "   from the dashboard's Settings -> Satellites.",
        ]
    readme += [
        "",
        "This zip deliberately contains no passwords. The dashboard shows",
        f"the console login for {SAT_USER} once, when the build finishes —",
        "write it down then. The card itself only carries its hash.",
    ]
    return "\n".join(readme) + "\n"


async def build(
    *,
    board_id: str,
    mic_profile: str,
    setup_transport: str = "usb",
    wifi_country: str = "US",
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
    overlay.validate_wifi_country(wifi_country)   # fail before any work
    if setup_transport not in overlay.SETUP_TRANSPORTS:
        raise ValueError(f"unknown setup transport {setup_transport!r}")
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
    # Only pinned, verified wake-word models ever reach a payload: anything
    # else in that bucket is removed first, and a short bucket is refilled.
    oww_verified, _removed = fetchers.verify_oww_models()
    if offline:
        if not st["wheels"]["ok"]:
            await progress("fetch", 10, "downloading aarch64 wheels (first run is slow)")
            ok, msg = fetchers.fetch_wheels(
                repo_root, board.python_version, board.manylinux_platforms
            )
            if not ok:
                warnings.append(msg)
        # A cache with SOME debs was "ready" before 2026-10-17, so one that
        # lacked a base package (mpg123 on the office card) was never
        # fetched again and the build said nothing. Refetch when any base
        # package is missing, and say so loudly when it still is.
        if not st["debs"]["ok"] or fetchers.base_debs_missing(board.os_release):
            await progress("fetch", 30, "fetching arm64 packages")
            extra: list[str] = []
            for entry in await payload.enabled_satellite_plugins():
                extra += entry["decl"]["apt_packages"]
            ok, msg = fetchers.fetch_debs(board.os_release, tuple(extra))
            # A partial fetch is ok=True with the missing names in the
            # message, which used to be dropped here.
            if not ok or "could not fetch" in msg:
                warnings.append(msg)
        gone = fetchers.base_debs_missing(board.os_release)
        if gone:
            log.warning("satellite media: %s", fetchers.base_debs_warning(gone))
            warnings.append(fetchers.base_debs_warning(gone))
        oww_problem = ""
        if oww_verified < len(fetchers.OWW_MODEL_FILES):
            await progress("fetch", 40, "caching wake-word base models")
            ok, msg = fetchers.fetch_oww_models()
            if not ok:
                oww_problem = msg
        # Unlike a missing wheel, a missing wake-word model does not degrade
        # to "the device fetches it": the only fallback on the device was
        # openWakeWord's own download, from the same release this pin
        # protects against. An offline card ships every pinned model,
        # verified, or is not built (SAT-11, 2026-10 review REV-17).
        missing = fetchers.missing_oww_models()
        if missing:
            raise ValueError(
                f"the wake-word model cache is incomplete: {len(missing)} of "
                f"{len(fetchers.OWW_MODEL_FILES)} pinned models are missing or "
                f"failed their SHA-256 check ({', '.join(missing)})"
                + (f" - {oww_problem}" if oww_problem else "")
                + ". Retry when the download works, or prepare the card with "
                "offline off: the satellite then downloads them and keeps "
                "only files that match the same pins."
            )
        if not st["xvf_host"]["ok"]:
            await progress("fetch", 45, "fetching the XVF3800 LED tool")
            ok, msg = fetchers.fetch_xvf_host()
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
    fin = payload.finalize(
        workspace, asm["dir"], stage2,
        overlay.render_status_helper(mic_profile),
    )
    # The zone and the clock, as this host knows them right now: stage 1
    # applies the zone offline and refuses to boot with a clock earlier
    # than this moment.
    firstrun = overlay.render_firstrun(
        SAT_USER, mic_profile, sat_type, setup_transport, wifi_country,
        tz=host_time.local_timezone_name(), prep_epoch=time.time(),
    )
    core_sha = await git_version.current_sha()
    # This card's server. Baked now, readable on the card, installed
    # root-owned by first boot and compared on every connect for the rest
    # of the device's life: it is what tells this household's core from
    # anything else that answers on 6370.
    identity = server_identity.load_or_create().public_document()
    info = overlay.build_info(
        board=board.id,
        mic_profile=mic_profile,
        sat_type=sat_type,
        core_sha=core_sha,
        python_version=board.python_version,
        os_release=board.os_release,
        plugins=asm["plugins"],
        offline=effective_offline,
        server_identity=identity,
    )
    # Portal units get per-device AP credentials baked now — the card has
    # never booted, so there is no MAC to derive an identity from later.
    ap = overlay.generate_ap_credentials() if setup_transport == "portal" else None
    # Answers Pi OS's first-boot user wizard unattended. Without it a unit
    # sits on "enter a new username" at the console forever — invisible on a
    # voice satellite, front and centre on a video one.
    console = overlay.generate_console_credentials(SAT_USER)
    device_info = overlay.initial_device_info(
        sat_type,
        setup_transport=setup_transport,
        ap_ssid=ap["ssid"] if ap else None,
    )

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
            ap=ap,
            console=console,
            usb_gadget=(setup_transport == "usb"),
            usb_host=(
                setup_transport != "usb"
                and mic_profile in overlay.USB_MIC_PROFILES
            ),
            server_identity=identity,
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
        # No plaintext passwords in an artifact that sits on the server
        # and is fetched over HTTP (WEB-1). userconf.txt still carries the
        # console password's HASH, so first boot is still unattended; the
        # dashboard shows both passwords once, and the README says how to
        # add the setup-AP file by hand for a portal card built this way.
        overlay.write_overlay(
            staging,
            payload_tar=fin["tar"],
            payload_sha256=fin["sha256"],
            firstrun=firstrun,
            info=info,
            device_info=device_info,
            ap=ap,
            console=console,
            usb_gadget=(setup_transport == "usb"),
            usb_host=(
                setup_transport != "usb"
                and mic_profile in overlay.USB_MIC_PROFILES
            ),
            include_credential_files=False,
            server_identity=identity,
        )
        (staging / "README.txt").write_text(
            zip_readme(ap, windows_host=is_wsl()), encoding="utf-8",
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
    # Handed back so the caller can show them ONCE, in process memory only:
    # persisting them would put a secret that should die with the card into
    # a database backup. For a DRIVE build they are also on the card, where
    # stage 1 needs them and whoever holds the card can read them anyway.
    # For a ZIP build this is the ONLY place they appear (WEB-1).
    result["credentials"] = {"ap": ap, "console": console}
    return result
