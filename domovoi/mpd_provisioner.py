"""Lazy MPD-per-room provisioning.

The voice domovoi runs one MPD daemon per voice-satellite room so that
playback queues / current track / volume are independent across rooms (a
shared MPD has one queue and one HTTP output, so cross-room play commands
stomp on each other). Rather than declaring rooms ahead of time, this
module spins up an MPD container the first time a satellite connects for a
new ``room_id`` and persists the room → (ports, container) mapping in the
``mpd_rooms`` table so it survives server restart.

No teardown on disconnect — Pis drop WiFi, reconnect with backoff, and the
user's expectation is that music keeps going across blips. Idle MPDs cost
~20 MB RAM each so leaving them running is cheap. Containers are created
with ``--restart unless-stopped`` so they come back after host reboot.

Docker is driven via subprocess invocations of the ``docker`` CLI rather
than the SDK so we don't take a new dependency. The core already
manages postgres + flyway via ``docker compose`` so docker is assumed
available in PATH.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from sqlalchemy import text

from domovoi.config import settings

log = logging.getLogger(__name__)

# Resolve once — the package layout has Dockerfile.mpd + mpd.conf alongside
# this module's parent directory (domovoi/). Absolute paths so
# `docker run` mount specs work from any cwd.
_PKG_DIR = Path(__file__).resolve().parent
_DOCKERFILE = _PKG_DIR / "Dockerfile.mpd"
_MPD_CONF = _PKG_DIR / "mpd.conf"
_BUILD_CONTEXT = _PKG_DIR

# pg_advisory_xact_lock id — serializes concurrent allocations across all
# domovoi instances pointing at the same DB. Released at transaction
# commit, so the lock window is just the SELECT MAX → INSERT pair.
_ALLOC_LOCK_ID = 0x4D504452_4F4F4D53  # "MPDRROOMS" in hex

# Docker container names must be `[a-zA-Z0-9][a-zA-Z0-9_.-]*`. Replace
# anything else with `-` so `room_id = "kid's bedroom"` doesn't break
# the create call.
_DOCKER_NAME_RE = re.compile(r"[^a-zA-Z0-9_.\-]")


def _safe_segment(room_id: str) -> str:
    cleaned = _DOCKER_NAME_RE.sub("-", room_id).strip("-_.")
    return cleaned or "unknown"


def _container_name(room_id: str) -> str:
    return f"{settings.mpd_container_prefix}{_safe_segment(room_id)}"


def _volume_name(room_id: str) -> str:
    return f"{settings.mpd_volume_prefix}{_safe_segment(room_id)}-data"


# ─── Docker subprocess helpers ────────────────────────────────────────────

async def _run_docker(
    *args: str,
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    """Run ``docker <args>``. Returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "docker", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, (stdout or b"").decode(errors="replace"), (stderr or b"").decode(errors="replace")


async def _image_exists(tag: str) -> bool:
    rc, _, _ = await _run_docker("image", "inspect", tag, timeout=10.0)
    return rc == 0


async def _build_image(tag: str) -> None:
    log.info("building MPD image %s (first run; cached on subsequent starts)", tag)
    rc, _, err = await _run_docker(
        "build",
        "-t", tag,
        "-f", str(_DOCKERFILE),
        str(_BUILD_CONTEXT),
        timeout=600.0,
    )
    if rc != 0:
        raise RuntimeError(f"docker build failed: {err.strip()[:500]}")


async def _container_state(name: str) -> str | None:
    """Return the container's State.Status, or None if it doesn't exist."""
    rc, out, _ = await _run_docker(
        "inspect", "-f", "{{.State.Status}}", name, timeout=10.0
    )
    if rc != 0:
        return None
    return out.strip() or None


async def _start_container(name: str) -> None:
    rc, _, err = await _run_docker("start", name, timeout=30.0)
    if rc != 0:
        raise RuntimeError(f"docker start {name}: {err.strip()[:200]}")


async def _pin_startup_volume(control_port: int) -> None:
    """Pin a freshly (re)started MPD daemon's mixer to the configured
    startup volume (``settings.mpd_startup_volume``, default 100%).

    The satellite's hardware mixer is the SINGLE volume control for both
    TTS and music, so MusicHandler keeps MPD at 100% to avoid double
    attenuation. MPD persists its software-mixer volume in the per-room
    state file, so a room whose data volume was wiped — or one created
    before the provisioner pinned volume — can boot at a stale/low level
    and silently attenuate music with no obvious cause. Re-asserting the
    level on every container start closes that trap without waiting for the
    user to issue a volume command.

    Best-effort by design: a slow/unreachable control port or a setvol
    failure is logged and swallowed so it can never wedge provisioning
    (non-music handlers must keep working even if MPD is unhappy)."""
    # Imported lazily to avoid a module-load cycle (clients.mpd imports
    # nothing from here, but keep the provisioner import-light).
    from domovoi.clients.mpd import RealMPDClient

    target = settings.mpd_startup_volume
    # setvol races the daemon's control-port bind on a cold start; wait for
    # the port before trying (short cap — if it's not up quickly the pin is
    # skipped and the next play/volume command re-pins anyway).
    if not await _wait_for_tcp(settings.mpd_host, control_port, timeout=15.0):
        log.warning(
            "MPD control port %d not up; skipping startup volume pin",
            control_port,
        )
        return
    try:
        await RealMPDClient(settings.mpd_host, control_port).set_volume(target)
        log.info("pinned MPD volume to %d%% (control port %d)", target, control_port)
    except Exception as e:
        log.warning("MPD startup volume pin on port %d failed: %s", control_port, e)


async def _create_container(
    *,
    name: str,
    image: str,
    control_port: int,
    http_port: int,
    music_dir: str,
    volume_name: str,
) -> None:
    # Spoken-audio dirs are mounted as NESTED subdirs of /music (not
    # separate roots) because MPD indexes exactly one music_directory
    # (/music, per mpd.conf). Nesting podcasts/ and audiobooks/ under it
    # lets MPD index+stream those files with zero config change, so the
    # per-room satellite playback path (prepare_filename → resume/seek)
    # works for spoken audio identically to music. Read-only like /music —
    # the web/worker processes own writes into these host dirs.
    extra_mounts: list[str] = []
    music_root = Path(music_dir)
    for host_dir, container_sub in (
        (settings.podcasts_dir, "podcasts"),
        (settings.audiobooks_dir, "audiobooks"),
    ):
        host_path = Path(host_dir).expanduser()
        # Docker Desktop on Windows refuses a bind mount whose host path
        # doesn't exist; the lifespan creates these lazily, but guard here
        # too so a stale config can't wedge MPD provisioning entirely.
        if host_path.exists():
            # These are NESTED bind mounts under /music, which is itself a
            # read-only bind mount. Docker cannot mkdir the mountpoint inside
            # a read-only parent, so the target dir must already exist in the
            # /music SOURCE (music_dir on the host). Create an empty placeholder
            # there — the real content comes from the overlaid mount. Without
            # this, `docker run`/`docker start` fails with:
            #   make mountpoint "/music/podcasts": mkdirat ...: read-only file system
            try:
                (music_root / container_sub).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log.warning(
                    "could not create mountpoint %s for nested MPD mount; "
                    "skipping %s: %s", music_root / container_sub, container_sub, e
                )
                continue
            extra_mounts += ["-v", f"{host_path.as_posix()}:/music/{container_sub}:ro"]

    rc, _, err = await _run_docker(
        "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "-p", f"{control_port}:6600",
        "-p", f"{http_port}:8001",
        "-v", f"{volume_name}:/var/lib/mpd",
        # Mount music dir read-only — host path comes straight from settings,
        # forward-slashed on Windows to match docker-compose behavior.
        "-v", f"{music_dir}:/music:ro",
        *extra_mounts,
        "-v", f"{_MPD_CONF.as_posix()}:/etc/mpd.conf:ro",
        image,
        timeout=60.0,
    )
    if rc != 0:
        raise RuntimeError(f"docker run {name}: {err.strip()[:500]}")


async def _ensure_container(
    *,
    name: str,
    image: str,
    control_port: int,
    http_port: int,
    music_dir: str,
    volume_name: str,
) -> None:
    """Make sure container ``name`` exists and is running. Idempotent."""
    state = await _container_state(name)
    if state == "running":
        return
    if state is None:
        await _create_container(
            name=name,
            image=image,
            control_port=control_port,
            http_port=http_port,
            music_dir=music_dir,
            volume_name=volume_name,
        )
    else:
        # exited / paused / created — bring it back up. Configuration changes
        # (volume / port) would require a recreate, but the only field that
        # ever changes between starts is the image tag, which we keep stable.
        await _start_container(name)
    # The container was just created or (re)started — re-assert the mixer
    # volume so a room booting from wiped/stale MPD state isn't silently
    # attenuated. Only runs when we actually start something (the early
    # return above skips already-running daemons, leaving their live volume
    # — possibly a user's deliberate setvol — untouched).
    await _pin_startup_volume(control_port)


async def _wait_for_tcp(host: str, port: int, timeout: float, interval: float = 0.3) -> bool:
    """Poll until host:port accepts TCP. Returns True on success, False on timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=2.0
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(interval)
    return False


# ─── Public API ───────────────────────────────────────────────────────────

async def ensure_image() -> None:
    """Build the MPD image if it isn't already present.

    Idempotent. The build itself relies on the same ``Dockerfile.mpd`` that
    docker-compose used to consume directly. First run takes ~10–30 s; cached
    layers on every subsequent boot.
    """
    if await _image_exists(settings.mpd_image_tag):
        return
    await _build_image(settings.mpd_image_tag)
    log.info("MPD image build complete")


async def warm_known_rooms() -> int:
    """Restart every previously-provisioned MPD container at startup.

    Without this, a Pi reconnecting after server restart would have
    to wait for `ensure_room` to re-spawn its container before the first
    music command works. Restarting up-front is cheap and keeps user-facing
    latency low.

    Also rebuilds the in-memory port cache in `clients.mpd._room_ports` so
    `get_mpd_client_for` and `mpd_stream_url_for` work without a DB hit on
    the request path.
    """
    from domovoi.clients import mpd as mpd_module
    from domovoi.db.session import session_scope

    rooms_warmed = 0
    async with session_scope() as session:
        result = await session.execute(
            text(
                "SELECT room_id, control_port, http_port, container_name "
                "FROM mpd_rooms ORDER BY created_at"
            )
        )
        rows = result.all()

    for room_id, ctrl, http, container in rows:
        mpd_module._room_ports[room_id] = (int(ctrl), int(http))
        try:
            await _ensure_container(
                name=container,
                image=settings.mpd_image_tag,
                control_port=int(ctrl),
                http_port=int(http),
                music_dir=settings.music_dir,
                volume_name=_volume_name(room_id),
            )
            rooms_warmed += 1
        except Exception as e:
            log.warning("failed to warm MPD for room=%s: %s", room_id, e)
    if rows:
        log.info("warmed %d/%d MPD rooms", rooms_warmed, len(rows))
    return rooms_warmed


async def ensure_room(room_id: str) -> tuple[int, int]:
    """Provision (or reuse) the MPD daemon for ``room_id``.

    Returns ``(control_port, http_port)``. Idempotent — concurrent calls for
    the same room collapse via the advisory lock + UNIQUE constraint. After
    the function returns, the control port is verified reachable (or a
    warning is logged if the wait timed out — handlers will surface a
    "music player unreachable" message in that case).

    Container start happens AFTER the DB transaction commits so we don't
    hold the row lock across a slow `docker run` (image pull / volume
    create can take seconds on first call).
    """
    from domovoi.clients import mpd as mpd_module
    from domovoi.db.session import session_scope

    cached = mpd_module._room_ports.get(room_id)
    container = _container_name(room_id)
    music_dir = settings.music_dir
    volume = _volume_name(room_id)

    if cached is not None:
        ctrl, http = cached
        try:
            await _ensure_container(
                name=container,
                image=settings.mpd_image_tag,
                control_port=ctrl,
                http_port=http,
                music_dir=music_dir,
                volume_name=volume,
            )
        except Exception as e:
            log.warning("ensure_container for room=%s failed: %s", room_id, e)
        return ctrl, http

    # Allocate atomically. Any racing connect for the same room hits the
    # advisory lock, then sees the existing row and exits the IF branch.
    async with session_scope() as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _ALLOC_LOCK_ID},
        )
        existing = (await session.execute(
            text(
                "SELECT control_port, http_port "
                "FROM mpd_rooms WHERE room_id = :r"
            ),
            {"r": room_id},
        )).first()
        if existing is not None:
            ctrl, http = int(existing[0]), int(existing[1])
            await session.execute(
                text(
                    "UPDATE mpd_rooms "
                    "SET last_connected_at = NOW() "
                    "WHERE room_id = :r"
                ),
                {"r": room_id},
            )
        else:
            ports_row = (await session.execute(
                text(
                    "SELECT "
                    "  COALESCE(MAX(control_port), :base_ctrl - 1) + 1, "
                    "  COALESCE(MAX(http_port),    :base_http - 1) + 1 "
                    "FROM mpd_rooms"
                ),
                {
                    "base_ctrl": settings.mpd_port_base_control,
                    "base_http": settings.mpd_port_base_http,
                },
            )).first()
            ctrl, http = int(ports_row[0]), int(ports_row[1])
            await session.execute(
                text(
                    "INSERT INTO mpd_rooms "
                    "  (room_id, control_port, http_port, container_name) "
                    "VALUES (:r, :ctrl, :http, :name)"
                ),
                {"r": room_id, "ctrl": ctrl, "http": http, "name": container},
            )
            log.info(
                "provisioned MPD room=%s control=%d http=%d",
                room_id, ctrl, http,
            )

    # Container start is outside the transaction — slow operation, no DB
    # state involved. ensure_container is idempotent so a concurrent
    # provisioning that won the row race already started the container;
    # this call no-ops in that case.
    await _ensure_container(
        name=container,
        image=settings.mpd_image_tag,
        control_port=ctrl,
        http_port=http,
        music_dir=music_dir,
        volume_name=volume,
    )
    if not await _wait_for_tcp(
        settings.mpd_host, ctrl, timeout=settings.mpd_provision_timeout_sec
    ):
        log.warning(
            "MPD control port %d for room=%s didn't answer within %.1fs",
            ctrl, room_id, settings.mpd_provision_timeout_sec,
        )

    mpd_module._room_ports[room_id] = (ctrl, http)
    return ctrl, http
