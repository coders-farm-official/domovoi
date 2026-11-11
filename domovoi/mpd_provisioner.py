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

Port exposure: each container publishes two ports. The MPD *control* port
(container 6600) is published on ``config.MPD_CONTROL_BIND`` — IPv4
loopback — only, because its clients are the core and the web backend and
both run on this host. ``settings.mpd_host``, the address those clients
dial, is normalised to the SAME constant: an IPv4-only bind behind the
dual-stack name ``localhost`` is unreachable via ``::1`` (F-046). The HTTP
*stream* port (container 8001) stays published on every interface because
the satellites pull the audio stream from it over the LAN. A container
that predates this split (control port published on every interface) is
recreated on its next ``_ensure_container`` pass; its data volume, and so
its library DB and playlists, carries over.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi import egress
from domovoi.config import MPD_CONTROL_BIND, settings

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

# Host address the MPD control port is published on. Loopback: the only
# clients are the core and the web backend, and both run on this host.
# The HTTP stream port is deliberately NOT bound this way (satellites
# stream from it over the LAN).
#
# It lives in domovoi.config next to `mpd_host`, which is normalised to
# it, so the address we bind and the address we dial are one value. When
# they were two, this said 127.0.0.1 while mpd_host still said
# "localhost" — which resolves to ::1 first — and the dashboard's
# now-playing read timed out against a room that was playing (F-046).
_CONTROL_BIND = MPD_CONTROL_BIND

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


async def _image_id(tag: str) -> str | None:
    """The local image ID ``tag`` points at (``sha256:…``), or None."""
    rc, out, _ = await _run_docker("image", "inspect", "-f", "{{.Id}}", tag, timeout=10.0)
    return (out.strip() or None) if rc == 0 else None


async def _note_image_for_guard(*, built: bool) -> None:
    """Tell the container guard which music player image the rooms run
    (docs/CONTAINER_GUARD.md): the one the core just built, or the one it
    found. Best effort — never stops provisioning."""
    try:
        from domovoi.container_guard.engine import note_mpd_image

        outcome = await note_mpd_image(await _image_id(settings.mpd_image_tag), built=built)
        log.info("music player image recorded for the container guard: %s", outcome)
    except Exception as e:  # noqa: BLE001
        log.warning("music player image not recorded for the container guard: %s", e)


def _room_hold(room_id: str, what: str):
    """Hold this room's music player for the container guard while it is
    being started, warmed or retired, and for a minute after."""
    from domovoi.container_guard import holds

    return holds.holding(
        f"room:{room_id}", f"the music player for {room_id} is {what}", settle_sec=60.0,
    )


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


async def _control_port_host_ip(name: str) -> str | None:
    """The host address the container publishes MPD's control port on.

    ``""`` means "every interface" (a container created before the loopback
    bind existed). ``None`` means the binding could not be read — the
    container is missing, docker is unhappy, or the port isn't published —
    and callers must NOT treat that as a reason to recreate anything.
    """
    rc, out, _ = await _run_docker(
        "inspect", "-f", "{{json .HostConfig.PortBindings}}", name, timeout=10.0
    )
    if rc != 0:
        return None
    try:
        bindings = json.loads(out.strip() or "null") or {}
        entries = bindings.get("6600/tcp") or []
        if not entries:
            return None
        return str(entries[0].get("HostIp") or "")
    except (ValueError, AttributeError, TypeError):
        return None


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
    # MPD's greeting before trying — not a bare connect, which Docker's port
    # proxy accepts before the daemon listens (short cap — if it's not up
    # quickly the pin is skipped and the next play/volume command re-pins
    # anyway).
    if not await _wait_for_mpd(settings.mpd_host, control_port, timeout=15.0):
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


def _nested_mountpoints(music_dir: str) -> list[tuple[Path, str]]:
    """The spoken-audio folders mounted NESTED under /music, as
    ``(host path, subfolder)``, each with its placeholder folder made in
    ``music_dir`` (see below). One whose host path is missing, or whose
    placeholder can't be made, is left out (logged).

    /music is itself a read-only bind mount, and Docker cannot make a
    mountpoint inside a read-only parent, so ``music_dir/podcasts`` and
    ``music_dir/audiobooks`` must exist in the /music SOURCE: empty folders,
    the real content comes from the overlaid mount. Without them ``docker
    run`` and every later ``docker start`` fail with exit code 128::

        make mountpoint "/music/audiobooks": mkdirat ...: read-only file system

    Called before every create AND every start: a placeholder removed while
    the room ran (it looks like an empty folder in the music library, and
    the Files page deletes folders) keeps every later start failing
    otherwise. On 2026-11-11 a house's ``Music/audiobooks`` was gone; the
    rooms ran on until an update restarted the VM, then none would start
    again for 14 hours, until a recreate made the folder. A music folder
    that isn't mounted yet is root's empty mount point, so the folder
    can't be made there (the service user can't write it): the start then
    fails as before, and a later retry, once the folder is mounted, works.
    """
    found: list[tuple[Path, str]] = []
    music_root = Path(music_dir)
    for host_dir, container_sub in (
        (settings.podcasts_dir, "podcasts"),
        (settings.audiobooks_dir, "audiobooks"),
    ):
        host_path = Path(host_dir).expanduser()
        placeholder = music_root / container_sub
        try:
            # Docker Desktop on Windows refuses a bind mount whose host path
            # doesn't exist; the lifespan creates these lazily, but guard
            # here too so a stale config can't wedge MPD provisioning.
            if not host_path.exists():
                continue
            if not placeholder.is_dir():
                # No parents=True: a music folder that isn't there (not
                # mounted, a drive unplugged) is never made here.
                placeholder.mkdir(exist_ok=True)
                log.info("made %s: the mountpoint the rooms' music players mount %s on",
                         placeholder, container_sub)
        except OSError as e:
            _warn_hourly(
                f"mountpoint:{placeholder}",
                "could not create mountpoint %s for nested MPD mount; skipping %s: %s",
                placeholder, container_sub, e,
            )
            continue
        found.append((host_path, container_sub))
    return found


# key -> loop-free monotonic time of the last warning logged under it.
_WARNED_AT: dict[str, float] = {}
_WARN_EVERY_SEC = 3600.0


def _warn_hourly(key: str, msg: str, *args: object) -> None:
    """A warning that repeats (a room that won't start, polled by a
    dashboard; a placeholder that can't be made) is logged once an hour per
    key, and at debug level in between."""
    import time

    now = time.monotonic()
    last = _WARNED_AT.get(key)
    if last is None or now - last >= _WARN_EVERY_SEC:
        _WARNED_AT[key] = now
        log.warning(msg, *args)
    else:
        log.debug(msg, *args)


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
    for host_path, container_sub in await asyncio.to_thread(_nested_mountpoints, music_dir):
        extra_mounts += ["-v", f"{host_path.as_posix()}:/music/{container_sub}:ro"]

    rc, _, err = await _run_docker(
        "run", "-d",
        # The image is Domovoi's own local build: never fetch a same-named
        # one from a registry instead.
        "--pull", "never",
        "--name", name,
        "--restart", "unless-stopped",
        # Control port: loopback only (see module docstring). Stream port:
        # every interface — the Pis fetch the audio from it over the LAN.
        "-p", f"{_CONTROL_BIND}:{control_port}:6600",
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
    check_music_mount: bool = False,
    room_id: str | None = None,
) -> None:
    """Make sure container ``name`` exists and is running. Idempotent.
    ``room_id``: the room it plays for, so a start also takes internet
    streams out of its queue under ``INTERNET_ACCESS=never``
    (:func:`_after_start`)."""
    state = await _container_state(name)
    if state is not None:
        host_ip = await _control_port_host_ip(name)
        if host_ip is not None and host_ip != _CONTROL_BIND:
            # Created by an older provisioner that published the control
            # port on every interface. Port bindings can't be edited in
            # place, so recreate it; the named data volume survives, so the
            # room's library DB, playlists and stored queue come back.
            # One-time: the replacement publishes on loopback and never
            # trips this branch again.
            log.warning(
                "MPD container %s publishes its control port on %r; "
                "recreating it bound to %s (data volume kept)",
                name, host_ip or "0.0.0.0", _CONTROL_BIND,
            )
            rc, _, err = await _run_docker("rm", "-f", name, timeout=30.0)
            if rc != 0:
                raise RuntimeError(f"docker rm -f {name}: {err.strip()[:200]}")
            state = None
    if state is not None and check_music_mount:
        # The music folder is bound when the container is CREATED: one made
        # on another music_dir (Settings changed it, then a restart) would
        # go on playing the old folder. Recreate it on today's (volume kept).
        # Only at the core's start (warm_known_rooms), never on the
        # on-demand or healing paths, which leave a running player alone.
        source = await _music_mount_source(name)
        why = None
        if source is not None and not _same_dir(source, music_dir):
            why = f"plays {source}, but the music folder is now {music_dir}"
        else:
            # The same for the nested spoken-audio mounts: on the Windows
            # install the podcasts folder moved from inside the distro to
            # /srv/media/podcasts (2026-11-11), and a player made before
            # would go on serving the old one at /music/podcasts.
            nested = await _nested_mount_sources(name)
            for host_path, sub in await asyncio.to_thread(_nested_mountpoints, music_dir):
                had = nested.get(f"/music/{sub}")
                if had is not None and not _same_dir(had, host_path.as_posix()):
                    why = f"has {had} at /music/{sub}, but that folder is now {host_path.as_posix()}"
                    break
        if why is not None:
            log.warning("MPD container %s %s; recreating it (data volume kept)", name, why)
            rc, _, err = await _run_docker("rm", "-f", name, timeout=30.0)
            if rc != 0:
                raise RuntimeError(f"docker rm -f {name}: {err.strip()[:200]}")
            state = None
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
        # The nested mounts' placeholders first: one removed since the
        # container was made fails the start (_nested_mountpoints).
        await asyncio.to_thread(_nested_mountpoints, music_dir)
        await _start_container(name)
    # The container was just created or (re)started — re-assert the mixer
    # volume so a room booting from wiped/stale MPD state isn't silently
    # attenuated. Only runs when we actually start something (the early
    # return above skips already-running daemons, leaving their live volume
    # — possibly a user's deliberate setvol — untouched).
    await _after_start(room_id, control_port)


async def _music_mount_source(name: str) -> str | None:
    """The host folder container ``name`` has at /music, or None when
    docker can't say (then nothing is recreated on its word)."""
    rc, out, _ = await _run_docker(
        "inspect", "-f", '{{range .Mounts}}{{if eq .Destination "/music"}}{{.Source}}{{end}}{{end}}', name,
        timeout=10.0,
    )
    return (out.strip() or None) if rc == 0 else None


async def _nested_mount_sources(name: str) -> dict[str, str]:
    """The host folder container ``name`` has at each mount below /music
    (``/music/podcasts``, ``/music/audiobooks``), by destination; {} when
    docker can't say (then nothing is recreated on its word). A mount it
    lacks is not "another folder" either: only a different one is."""
    rc, out, _ = await _run_docker(
        "inspect", "-f",
        '{{range .Mounts}}{{if ne .Destination "/music"}}{{.Destination}}\t{{.Source}}\n{{end}}{{end}}', name,
        timeout=10.0,
    )
    if rc != 0:
        return {}
    found: dict[str, str] = {}
    for line in out.splitlines():
        dest, sep, src = line.partition("\t")
        if sep and dest.startswith("/music/") and src.strip():
            found[dest.strip()] = src.strip()
    return found


#: How Docker Desktop names a Windows drive's folder on the host side of a
#: bind mount (``docker inspect`` .Source): /run/desktop/mnt/host/c/...,
#: older releases /host_mnt/c/....
_DESKTOP_SOURCE = re.compile(r"^/(?:run/desktop/mnt/host|host_mnt)/([A-Za-z])(/.*)?$")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:(/.*)?$")


def _mount_key(path: str) -> str | None:
    """A form two names of one folder share: a Windows folder as ``c:/...``
    (lower case: Windows doesn't tell case apart), a Linux path normalized.
    None for a Docker Desktop source of a shape this doesn't know, which
    then never counts as a different folder."""
    p = path.strip().replace("\\", "/")
    m = _DESKTOP_SOURCE.match(p)
    if m:
        p = f"{m.group(1)}:{m.group(2) or '/'}"
    elif p.startswith(("/run/desktop/", "/host_mnt/")):
        return None
    if _DRIVE_PATH.match(p):
        p = p.lower().rstrip("/") or p.lower()
        return p if len(p) > 2 else p + "/"
    return os.path.normpath(p)


def _same_dir(source: str, music_dir: str) -> bool:
    """Whether a container's /music source is ``music_dir``. Unsure counts
    as the same: a player is recreated only when the folder is surely
    another, never on every start because Docker spells it its own way."""
    a, b = _mount_key(source), _mount_key(music_dir)
    if a is None or b is None or a == b:
        return True
    try:
        return os.path.realpath(source) == os.path.realpath(music_dir)
    except OSError:
        return False


async def recreate_rooms(why: str) -> list[dict]:
    """Every room's player made again from its ``mpd_rooms`` row (same data
    volume: playlists and queue survive), so its mounts are taken afresh:
    after the media folders moved on a Windows install, where the Linux
    path stays /srv/media/music but the folder behind it changed
    (domovoi/media_folders.py). Each room is held for the container guard
    while it is replaced. A room that fails is named in the answer."""
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        rows = (await s.execute(text(
            "SELECT room_id, control_port, http_port, container_name FROM mpd_rooms ORDER BY created_at"
        ))).all()
    out: list[dict] = []
    for room_id, ctrl, http, container in rows:
        entry = {"room": room_id, "error": None}
        try:
            with _room_hold(room_id, why):
                rc, _, err = await _run_docker("rm", "-f", container, timeout=30.0)
                if rc != 0 and "No such container" not in err:
                    raise RuntimeError(f"docker rm -f {container}: {err.strip()[:200]}")
                await _ensure_container(
                    name=container, image=settings.mpd_image_tag, control_port=int(ctrl), http_port=int(http),
                    music_dir=settings.music_dir, volume_name=_volume_name(room_id),
                    room_id=room_id,
                )
        except Exception as e:  # noqa: BLE001 - the other rooms still move
            log.warning("music player for room=%s not recreated (%s): %s", room_id, why, e)
            entry["error"] = str(e)[:300]
        out.append(entry)
    if rows:
        log.info("recreated %d MPD room(s): %s", len(rows), why)
    return out


# What MPD says first on every control connection: "OK MPD <version>".
_MPD_GREETING = b"OK MPD "
# The most one connect, or one wait for the greeting, may take.
_MPD_ATTEMPT_SEC = 2.0


async def _wait_for_mpd(host: str, port: int, timeout: float, interval: float = 0.3) -> bool:
    """Poll until MPD itself answers on host:port with its ``OK MPD``
    greeting. Returns True on success, False once ``timeout`` has run out.

    A TCP connect is not enough. The control port is published through
    Docker's port proxy, which accepts a loopback connection even while
    nothing in the container listens yet (a daemon still starting, still
    scanning the library), then closes it with no bytes: a connect-only wait
    returned at once and the caller went on to a daemon that was not there.
    MPD sends its greeting as soon as it accepts a client, so reading one
    line is the whole check. Every connect and every read is bounded by
    what is left of ``timeout``, so neither a refusing port nor a frozen
    daemon (accepts, never speaks) holds the caller past it.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=min(_MPD_ATTEMPT_SEC, remaining),
            )
            line = await asyncio.wait_for(
                reader.readline(),
                timeout=min(_MPD_ATTEMPT_SEC, max(0.05, deadline - loop.time())),
            )
            if line.startswith(_MPD_GREETING):
                return True
        except (OSError, asyncio.TimeoutError, ValueError):
            pass
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
        await asyncio.sleep(max(0.0, min(interval, deadline - loop.time())))


# ─── Public API ───────────────────────────────────────────────────────────

async def ensure_image() -> None:
    """Build the MPD image if it isn't already present.

    Idempotent. The build itself relies on the same ``Dockerfile.mpd`` that
    docker-compose used to consume directly. First run takes ~10–30 s; cached
    layers on every subsequent boot.

    Under ``INTERNET_ACCESS=never`` a missing image is not built: the build
    pulls ``debian:bookworm-slim`` and runs apt. It raises the way a failed
    build does, so the caller's handling is unchanged.
    """
    if await _image_exists(settings.mpd_image_tag):
        await _note_image_for_guard(built=False)
        return
    if egress.internet_turned_off():
        msg = (
            "the music container image is missing and internet access is "
            "turned off; build it while online (docs/INTERNET.md)"
        )
        log.warning(msg)
        raise RuntimeError(msg)
    from domovoi.container_guard import holds

    with holds.holding("mpd-image", "the music player image is being built", settle_sec=60.0):
        await _build_image(settings.mpd_image_tag)
    log.info("MPD image build complete")
    await _note_image_for_guard(built=True)


def rebuild_unavailable() -> str | None:
    """Why the admin's "rebuild the music player image" can't run here, in
    plain words, or None when it can. The build pulls Debian's base image
    and runs apt, so not with internet access turned off."""
    if not settings.mpd_image_tag:
        return "no music player image is configured (MPD_IMAGE_TAG)"
    if egress.internet_turned_off():
        return "internet access is turned off, and the build downloads Debian's packages"
    return None


_REBUILD_LOCK: asyncio.Lock | None = None


class RebuildBusy(RuntimeError):
    """A rebuild is already running."""


async def _container_image(name: str) -> str | None:
    """The image ID container ``name`` runs, or None (gone, docker unhappy)."""
    rc, out, _ = await _run_docker("inspect", "-f", "{{.Image}}", name, timeout=10.0)
    return (out.strip() or None) if rc == 0 else None


async def rebuild_image() -> dict:
    """The admin's "rebuild the music player image now" (Containers page).

    Builds the image exactly as the core does when it is missing
    (:func:`_build_image`), records it as the container guard's baseline
    (``built=True``: the image Domovoi built), then puts every room's
    player on it the way an update does: each room container on another
    image is removed and created again from its ``mpd_rooms`` row, with the
    same data volume (playlists and queue survive; what a room is playing
    stops). Every room, and the tag, is held for the guard from before the
    build until the last room is done (and the usual minute after): once
    the baseline moves, a room still waiting its turn runs the old image,
    which the guard would otherwise call a different image. A room that
    could not be moved is named in the answer. A tamper incident about the
    image stays open: clearing it is the admin's call. Raises
    :class:`RebuildBusy` when one is running or an update or restart is
    under way, RuntimeError when it can't run here or the build fails."""
    global _REBUILD_LOCK
    why = rebuild_unavailable()
    if why:
        raise RuntimeError(why)
    from domovoi import self_restart

    if await asyncio.to_thread(self_restart.underway):
        raise RebuildBusy(
            "an update or restart is under way; rebuild the music player image once it has finished"
        )
    # No await between this check and taking the lock below: two clicks at
    # once give one rebuild and one "already being rebuilt".
    if _REBUILD_LOCK is None:
        _REBUILD_LOCK = asyncio.Lock()
    if _REBUILD_LOCK.locked():
        raise RebuildBusy("the music player image is already being rebuilt")
    from contextlib import ExitStack

    from domovoi.container_guard import holds
    from domovoi.container_guard.engine import note_mpd_image
    from domovoi.db.session import session_scope

    async with _REBUILD_LOCK:
        tag = settings.mpd_image_tag
        async with session_scope() as s:
            rows = (await s.execute(text(
                "SELECT room_id, control_port, http_port, container_name "
                "FROM mpd_rooms ORDER BY created_at"
            ))).all()
        rooms: list[dict] = []
        with ExitStack() as stack:
            stack.enter_context(holds.holding(
                "mpd-image", "the music player image is being rebuilt", settle_sec=60.0))
            for room_id, *_ in rows:
                stack.enter_context(_room_hold(room_id, "restarting on the rebuilt image"))
            await _build_image(tag)
            image_id = await _image_id(tag)
            if not image_id:
                raise RuntimeError(f"the build finished but {tag} names no image")
            baseline = await note_mpd_image(image_id, built=True)
            log.warning("music player image rebuilt by the admin: %s (%s)", image_id[:19], baseline)
            for room_id, ctrl, http, container in rows:
                entry = {"room": room_id, "recreated": False, "error": None}
                try:
                    running = await _container_image(container)
                    if running is not None and running != image_id:
                        rc, _, err = await _run_docker("rm", "-f", container, timeout=30.0)
                        if rc != 0:
                            raise RuntimeError(f"docker rm -f {container}: {err.strip()[:200]}")
                        entry["recreated"] = True
                    await _ensure_container(
                        name=container, image=tag, control_port=int(ctrl), http_port=int(http),
                        music_dir=settings.music_dir, volume_name=_volume_name(room_id),
                        room_id=room_id,
                    )
                except Exception as e:  # noqa: BLE001 — the other rooms still move
                    log.warning("music player for room=%s not moved to the rebuilt image: %s", room_id, e)
                    entry["error"] = str(e)[:300]
                rooms.append(entry)
    return {"image_id": image_id, "baseline": baseline, "rooms": rooms}


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

    failed: list[tuple[str, int, int, str]] = []
    for room_id, ctrl, http, container in rows:
        mpd_module._room_ports[room_id] = (int(ctrl), int(http))
        try:
            with _room_hold(room_id, "starting"):
                await _ensure_container(
                    name=container,
                    image=settings.mpd_image_tag,
                    control_port=int(ctrl),
                    http_port=int(http),
                    music_dir=settings.music_dir,
                    volume_name=_volume_name(room_id),
                    check_music_mount=True,
                    room_id=room_id,
                )
            rooms_warmed += 1
        except Exception as e:
            log.warning("failed to warm MPD for room=%s: %s", room_id, e)
            failed.append((str(room_id), int(ctrl), int(http), str(container)))
    if rows:
        log.info("warmed %d/%d MPD rooms", rooms_warmed, len(rows))
    if failed:
        schedule_warm_retry(failed)
    return rooms_warmed


# ─── self-heal: rooms whose player stopped and stayed stopped ─────────────
#
# ``--restart unless-stopped`` does not cover a start that FAILS: when the
# Docker daemon restores its containers at boot and one won't start (a
# nested mount's placeholder folder gone from the music folder, see
# _nested_mountpoints; a music folder not mounted yet), Docker records exit
# code 128 and never tries that container again (2026-11-11: every room of
# a house stayed down for 14 hours after an update restarted its WSL VM,
# its Music/audiobooks placeholder gone). Each start below remakes the
# placeholders first. Three places start such a room again, all through
# start_stopped_room: the core's start (a bounded retry, in the background,
# of the rooms its warm couldn't start), a music command that can't reach
# its room's player (clients/mpd.py, once, then the command again) and the
# container guard (engine.py, one start with backoff before it reports the
# room). Every one of them:
#
# * starts only a STOPPED container: never recreates one that exists (a
#   running one is left as it is, whatever it mounts);
# * starts only a container that is a room player Domovoi made: Domovoi's
#   music player image (the guard's recorded baseline), no privileged mode,
#   host namespaces or Docker socket, the provisioner's restart policy and
#   mounts (container_guard.detect.room_player_shape); started by its ID,
#   so the container judged is the one started;
# * refuses while a tamper incident freezes risky operations, and when the
#   freeze can't be read (fails closed);
# * leaves a room alone while another operation holds it (a warm, a retire,
#   the image rebuild).

# When the start's background retry looks again at the rooms its warm could
# not start: about ten minutes in all, then the guard takes over.
WARM_RETRY_DELAYS: tuple[float, ...] = (10.0, 20.0, 40.0, 80.0, 160.0, 300.0)
# How long after one self-heal attempt for a room a music command may try
# another: a dashboard polling a dead room doesn't run `docker start` every
# few seconds.
HEAL_COOLDOWN_SEC = 60.0
# After a start, the internet-off purge asks the room this many times, this
# far apart, while its daemon comes up.
PURGE_TRIES = 3
PURGE_RETRY_SEC = 2.0

_warm_retry_task: asyncio.Task | None = None
_heal_last: dict[str, float] = {}
_heal_tasks: dict[str, asyncio.Future] = {}
# Post-start work running in the background: referenced here so the event
# loop's weak reference isn't the only one (a task can vanish mid-run).
_BACKGROUND: set[asyncio.Task] = set()


def reset_self_heal() -> None:
    """Forget cooldowns and in-flight heals (tests)."""
    global _warm_retry_task
    _heal_last.clear()
    _heal_tasks.clear()
    _warm_retry_task = None
    _WARNED_AT.clear()


async def frozen_by() -> str | None:
    """Why the self-heal must not start anything right now, in words, or
    None: an open tamper incident (``"incident #12"``), or a freeze state
    that can't be read. Fails CLOSED, unlike the HTTP freeze
    (container_guard/freeze.py), which fails open because every frozen
    operation needs the database anyway: a start needs only Docker."""
    try:
        from domovoi.container_guard import store
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            rows = await store.frozen_by(s)
    except Exception as e:  # noqa: BLE001 — fail closed
        return f"the freeze state can't be read ({str(e)[:120]})"
    return f"incident #{rows[0].get('id')}" if rows else None


def room_busy(room_id: str) -> str | None:
    """Another operation is working on ``room_id``'s player right now (a
    warm, a retire, a start, the image rebuild): its reason, or None."""
    from domovoi.container_guard import holds

    if _REBUILD_LOCK is not None and _REBUILD_LOCK.locked():
        return "the music player image is being rebuilt"
    running = holds._RUNNING.get(f"room:{room_id}")
    if running is not None and running[0] > 0:
        return str(running[2])
    return None


async def _inspect_room(target: str) -> Any | None:
    """``docker inspect`` of one container (by name or ID) as the guard
    reads it (container_guard.model.Observed), or None when it isn't
    there."""
    from domovoi.container_guard.docker_cli import parse_inspect

    rc, out, _ = await _run_docker("inspect", target, timeout=10.0)
    if rc != 0:
        return None
    found = parse_inspect(out)
    return found[0] if found else None


async def _trusted_image_ids() -> set[str]:
    """The music player images a room may run: the guard's recorded
    baseline and the build before it, or, before the guard recorded any,
    what the tag names now. Raises when it can't be read (the caller then
    starts nothing)."""
    from domovoi.container_guard import store
    from domovoi.container_guard.engine import MPD_BASELINE_KEY, MPD_PREVIOUS_KEY
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        ids = {
            (row or {}).get("value")
            for row in (await store.get_baseline(s, MPD_BASELINE_KEY),
                        await store.get_baseline(s, MPD_PREVIOUS_KEY))
        }
    ids.discard(None)
    if not ids:
        tag_id = await _image_id(settings.mpd_image_tag)
        if tag_id:
            ids.add(tag_id)
    return {str(i) for i in ids}


def room_player_problem(obs: Any, image_ids: set[str]) -> str | None:
    """Why ``obs`` must not be started as a room's music player, in words,
    or None when it is one Domovoi made (see the section comment)."""
    from domovoi.container_guard.detect import room_player_shape

    if not image_ids or obs.image_id not in image_ids:
        return "it runs another image than Domovoi's music player"
    risky = obs.security()
    if risky:
        return f"it runs with {', '.join(risky)}"
    return room_player_shape(obs)


async def start_stopped_room(
    room_id: str,
    container: str | None = None,
    *,
    why: str,
    container_id: str | None = None,
) -> str:
    """``docker start`` a provisioned room's music player that exists but
    isn't running, by its ID, held for the container guard while it starts.
    Never creates or recreates anything. Returns what it found and did:
    ``"started"``, ``"running"`` (nothing to do), ``"missing"``,
    ``"frozen"``, ``"busy"`` (another operation holds the room),
    ``"refused"`` (not a room player Domovoi made: logged), or the state it
    leaves alone (``"paused"``, ``"restarting"``, ``"removing"``). Raises
    RuntimeError when Docker refuses the start (its words: a bind mount
    whose source is missing, a port another program holds). The caller runs
    :func:`_after_start` (or schedules it)."""
    target = container_id or container or _container_name(room_id)
    busy = room_busy(room_id)
    if busy is not None:
        log.info("music player for room=%s not started: %s", room_id, busy)
        return "busy"
    incident = await frozen_by()
    if incident is not None:
        _warn_hourly(f"frozen:{room_id}",
                     "music player for room=%s not started: %s (risky operations are frozen "
                     "until an admin clears it)", room_id, incident)
        return "frozen"
    obs = await _inspect_room(target)
    if obs is None:
        return "missing"
    if obs.running or obs.paused or obs.restarting or obs.status == "removing":
        return "paused" if obs.paused else ("restarting" if obs.restarting else
                                            "running" if obs.running else obs.status)
    try:
        image_ids = await _trusted_image_ids()
    except Exception as e:  # noqa: BLE001 — no proof it's ours: don't start it
        _warn_hourly(f"refused:{room_id}",
                     "music player for room=%s not started: its image can't be checked (%s)",
                     room_id, e)
        return "refused"
    problem = room_player_problem(obs, image_ids)
    if problem is not None:
        _warn_hourly(f"refused:{room_id}",
                     "container %s (%s) for room=%s not started: %s",
                     obs.name, obs.id[:12], room_id, problem)
        return "refused"
    with _room_hold(room_id, "starting again"):
        await asyncio.to_thread(_nested_mountpoints, settings.music_dir)
        await _start_container(obs.id)
    log.warning("music player for room=%s was %s; started it again (%s)",
                room_id, obs.status, why)
    return "started"


async def _after_start(room_id: str | None, control_port: int) -> None:
    """What every start of a room's player is followed by, wherever it
    came from: the mixer pinned to the startup volume (which waits for
    MPD's greeting), then, under ``INTERNET_ACCESS=never``, internet
    streams taken out of the room's queue (MPD resumes whatever its state
    file holds; mpd_internet.purge_room), asked a few times while the
    daemon comes up. Never raises."""
    await _pin_startup_volume(control_port)
    if room_id is None or not egress.internet_turned_off():
        return
    from domovoi import mpd_internet
    from domovoi.clients.mpd import RealMPDClient

    client = RealMPDClient(settings.mpd_host, control_port)
    for attempt in range(1, PURGE_TRIES + 1):
        try:
            await mpd_internet.purge_room(room_id, client)
            return
        except Exception as e:  # noqa: BLE001 — a daemon still starting
            if attempt == PURGE_TRIES:
                log.warning("internet off: couldn't check room=%s's music queue after "
                            "its start: %s", room_id, e)
                return
            await asyncio.sleep(PURGE_RETRY_SEC)


def schedule_after_start(room_id: str, control_port: int) -> asyncio.Task:
    """:func:`_after_start` in the background (the guard's check doesn't
    wait for MPD's greeting), kept referenced until it ends."""
    task = asyncio.get_running_loop().create_task(_after_start(room_id, control_port))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


async def _reconcile_room(room_id: str, ctrl: int, http: int, container: str) -> str:
    """One more go at a room the start's warm couldn't start, or one a
    music command found down. A MISSING container is created as the warm
    does; a stopped one is started by :func:`start_stopped_room` (all its
    checks); a running one is left exactly as it is: never recreated here,
    whatever it mounts or binds (that is the warm's call, at the core's
    next start). Returns ``"created"``, ``"started"``, ``"running"`` or
    what :func:`start_stopped_room` said (``"frozen"``, ``"busy"``,
    ``"refused"``, ...); raises when Docker refuses."""
    busy = room_busy(room_id)
    if busy is not None:
        return "busy"
    incident = await frozen_by()
    if incident is not None:
        return "frozen"
    obs = await _inspect_room(container)
    if obs is None:
        with _room_hold(room_id, "being created again"):
            if await _container_state(container) is not None:
                return "running"          # appeared meanwhile: someone else's start
            await _create_container(
                name=container, image=settings.mpd_image_tag, control_port=ctrl,
                http_port=http, music_dir=settings.music_dir,
                volume_name=_volume_name(room_id),
            )
        await _after_start(room_id, ctrl)
        return "created"
    if obs.running:
        return "running"
    outcome = await start_stopped_room(
        room_id, container, why="it hadn't started with the core", container_id=obs.id)
    if outcome == "started":
        await _after_start(room_id, ctrl)
    return outcome


async def retry_warm(
    rooms: list[tuple[str, int, int, str]],
    delays: tuple[float, ...] = WARM_RETRY_DELAYS,
) -> list[str]:
    """Look again, after each of ``delays``, at the rooms the start's warm
    couldn't start, until each runs or the delays run out. Logs every
    outcome; returns the rooms still down at the end (the container guard
    reports those). A room it may not start (frozen, busy, not Domovoi's)
    is asked again at the next delay."""
    pending = list(rooms)
    for attempt, delay in enumerate(delays, start=1):
        if not pending:
            break
        await asyncio.sleep(delay)
        still: list[tuple[str, int, int, str]] = []
        for room in pending:
            room_id, ctrl, http, container = room
            try:
                outcome = await _reconcile_room(room_id, ctrl, http, container)
            except Exception as e:  # noqa: BLE001 — try again after the next delay
                log.warning(
                    "music player for room=%s still won't start (try %d of %d): %s",
                    room_id, attempt, len(delays), e,
                )
                still.append(room)
                continue
            if outcome in ("started", "created"):
                log.warning("music player for room=%s %s on try %d after the core's start",
                            room_id, outcome, attempt)
            elif outcome != "running":
                log.warning("music player for room=%s not started (%s; try %d of %d)",
                            room_id, outcome, attempt, len(delays))
                still.append(room)
        pending = still
    if pending:
        log.warning(
            "music players still down after the start's retries: %s (the container guard reports them)",
            ", ".join(r[0] for r in pending),
        )
    return [r[0] for r in pending]


def schedule_warm_retry(rooms: list[tuple[str, int, int, str]]) -> asyncio.Task | None:
    """Run :func:`retry_warm` in the background (never awaited by the
    start). One at a time: a second warm while one runs leaves it be."""
    global _warm_retry_task
    if _warm_retry_task is not None and not _warm_retry_task.done():
        return _warm_retry_task
    try:
        _warm_retry_task = asyncio.get_running_loop().create_task(retry_warm(rooms))
    except RuntimeError:  # no loop: nothing to schedule on
        return None
    log.info("music players %s didn't start with the core; trying again in the background",
             ", ".join(r[0] for r in rooms))
    return _warm_retry_task


async def heal_room(room_id: str) -> bool:
    """A music command couldn't reach ``room_id``'s player: start it again
    if its container is stopped (or create it when it is missing), then
    wait for MPD's greeting. True when it did and the player now answers,
    so the caller tries its command once more. At most one attempt per room
    per :data:`HEAL_COOLDOWN_SEC`; commands arriving while one runs share
    it. Never raises."""
    task = _heal_tasks.get(room_id)
    if task is None or task.done():
        loop = asyncio.get_running_loop()
        last = _heal_last.get(room_id)
        if last is not None and loop.time() - last < HEAL_COOLDOWN_SEC:
            return False
        _heal_last[room_id] = loop.time()
        task = loop.create_task(_heal(room_id))
        _heal_tasks[room_id] = task
    try:
        return bool(await asyncio.shield(task))
    except Exception:  # noqa: BLE001 — _heal never raises; belt and braces
        return False


async def _heal(room_id: str) -> bool:
    from domovoi.clients import mpd as mpd_module

    ports = mpd_module._room_ports.get(room_id)
    if ports is None:
        return False
    ctrl, http = ports
    try:
        outcome = await _reconcile_room(room_id, int(ctrl), int(http), _container_name(room_id))
    except Exception as e:  # noqa: BLE001
        _warn_hourly(f"heal:{room_id}",
                     "music player for room=%s could not be started for a music command: %s",
                     room_id, e)
        return False
    if outcome not in ("started", "created"):
        return False
    log.warning("music player for room=%s was down; %s it for a music command", room_id, outcome)
    # _after_start pinned the volume, which waited for the greeting; wait
    # once more, briefly, in case that wait gave up early.
    return await _wait_for_mpd(settings.mpd_host, int(ctrl), timeout=5.0)


async def remove_room(room_id: str) -> dict[str, bool]:
    """Retire a room's MPD instance: stop and remove its container, drop its
    ``mpd_rooms`` row, and release the ports back to the pool.

    The VOLUME is deliberately kept. It holds that ROOM's music state -
    playlists, stored queue - which belongs to the kitchen rather than to
    whichever Pi happened to be sitting in it, and a replacement satellite
    for the same room should inherit it. Deleting it is irreversible and
    nobody has asked for it.

    Best-effort on the docker half: a container that is already gone, or a
    docker daemon that is not running, must not stop the row from being
    removed - otherwise a room name stays claimed forever by a container
    that no longer exists.
    """
    from domovoi.db.session import session_scope

    removed_container = False
    name = _container_name(room_id)
    with _room_hold(room_id, "being removed"):
        try:
            state = await _container_state(name)
            if state is not None:
                await _run_docker("rm", "-f", name)
                removed_container = True
        except Exception as e:  # noqa: BLE001 - see docstring
            log.warning("mpd: could not remove container for room %s: %s", room_id, e)

        async with session_scope() as s:
            result = await s.execute(
                text("DELETE FROM mpd_rooms WHERE room_id = :r"), {"r": room_id}
            )
            removed_row = bool(result.rowcount)
        # And this process's own memory of it. Left behind, the next connect
        # of a room with the same id took the cached ports, started a
        # container and wrote no mpd_rooms row - so the next retire found no
        # row, left that container running, and the ports looked free to
        # the allocator (2026-10-13, found by the Windows satellite's e2e).
        from domovoi.clients import mpd as mpd_module

        mpd_module._room_ports.pop(room_id, None)
        mpd_module._clients.pop(room_id, None)
    log.info(
        "mpd: retired room=%s (container=%s row=%s)",
        room_id, removed_container, removed_row,
    )
    return {"container": removed_container, "row": removed_row}


async def ensure_room(room_id: str) -> tuple[int, int]:
    """Provision (or reuse) the MPD daemon for ``room_id`` — see
    :func:`_ensure_room`; held for the container guard while it runs."""
    with _room_hold(room_id, "starting"):
        return await _ensure_room(room_id)


async def _ensure_room(room_id: str) -> tuple[int, int]:
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
                room_id=room_id,
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
        room_id=room_id,
    )
    if not await _wait_for_mpd(
        settings.mpd_host, ctrl, timeout=settings.mpd_provision_timeout_sec
    ):
        log.warning(
            "MPD control port %d for room=%s didn't answer within %.1fs",
            ctrl, room_id, settings.mpd_provision_timeout_sec,
        )

    mpd_module._room_ports[room_id] = (ctrl, http)
    return ctrl, http
