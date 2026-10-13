"""The satellite engine's platform seams: audio out, volume, service control.

Spare-device satellites (S1, 2026-10-13). The engine (``satellite/client.py``)
is the same program on a Pi, a Linux laptop and — next — Windows. What
differs between them sits behind four small interfaces, each with the Pi's
behaviour as one implementation, byte for byte what it was:

* :class:`AudioOut` — the child process that plays music, a greeting or
  the canned clip, and the chime. Pi and Linux PC: ``mpg123``/``aplay`` on
  an ALSA device (:class:`AlsaAudioOut`). The engine starts, watches and
  stops it as a process (its own process group); a platform without
  mpg123 supplies its own player program here.
* :class:`VolumeBackend` — the one output gain voice and music ride
  through. Pi: an ALSA mixer control (:class:`AmixerVolume`). Laptop: the
  sound server's default sink, ``wpctl`` (PipeWire) or ``pactl``
  (PulseAudio), found by :class:`AutoVolume`.
* :class:`ServiceControl` — how the engine restarts itself after a config
  change or an upgrade. Pi: ``sudo -n systemctl --no-block restart``
  (:class:`SudoSystemctl`, the sudoers line in PROVISIONING.md). Laptop:
  no privilege at all — the engine exits with :data:`EXIT_RESTART` and its
  unit's ``RestartForceExitStatus=75`` starts it again
  (:class:`ExitToRestart`); a Windows service wrapper can do the same.
* The control channel's transport — :mod:`satellite.control`
  (``ControlTransport``; a Unix socket here).

:func:`for_profile` picks the set for a config. Stdlib only.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from typing import Any, Callable, Protocol

from satellite import _volume

log = logging.getLogger("satellite.platform")

#: The engine asks for a restart by exiting with this (EX_TEMPFAIL). The
#: laptop units carry ``RestartForceExitStatus=75`` and
#: ``SuccessExitStatus=75``: started again, not counted as a failure.
EXIT_RESTART = 75
#: An enrolled laptop that finds no pinned server identity exits with this
#: (EX_CONFIG); its units carry ``RestartPreventExitStatus=78`` so it stays
#: down, saying why, instead of looping.
EXIT_UNPINNED = 78

#: ``[audio] volume_backend`` values.
VOLUME_BACKENDS = ("amixer", "wpctl", "pactl", "auto", "none")

Runner = Callable[..., Any]


# ─── audio out ────────────────────────────────────────────────────────────

class AudioOut(Protocol):
    """The argv of the player child process for each kind of sound.
    ``device`` is the config's output device (``[music] alsa_device`` on
    Linux), passed per call so a config change needs no new object."""

    def stream_argv(self, device: str, url: str) -> list[str]: ...
    def mp3_argv(self, device: str, path: str, gain: float) -> list[str]: ...
    def wav_argv(self, device: str, path: str) -> list[str]: ...


class AlsaAudioOut:
    """mpg123 and aplay on an ALSA device. On a desktop the ALSA ``default``
    device is the sound server's, which is where a laptop's speakers are."""

    def stream_argv(self, device: str, url: str) -> list[str]:
        # -b 1024 / --devbuffer 1.0: see Satellite._spawn_music.
        return ["mpg123", "-q", "-b", "1024", "--devbuffer", "1.0",
                "-o", "alsa", "-a", device, url]

    def mp3_argv(self, device: str, path: str, gain: float) -> list[str]:
        return ["mpg123", "-q", *_volume.mpg123_scale_args(gain),
                "-o", "alsa", "-a", device, path]

    def wav_argv(self, device: str, path: str) -> list[str]:
        return ["aplay", "-q", "-D", device, path]


# ─── volume ───────────────────────────────────────────────────────────────

class VolumeBackend(Protocol):
    name: str

    def available(self) -> bool: ...
    def get(self) -> int | None: ...
    def set(self, level: int) -> bool: ...


def _runner(run: Runner | None) -> Runner:
    """``subprocess.run`` looked up when called, so a test that replaces it
    on the module is honoured (a default argument would have kept the
    original)."""
    return run if run is not None else subprocess.run


def _run(run: Runner | None, argv: list[str], what: str) -> subprocess.CompletedProcess | None:
    try:
        r = _runner(run)(argv, capture_output=True, text=True, timeout=5.0)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log.warning("%s failed: %s", what, e)
        return None
    if r.returncode != 0:
        log.warning("%s failed: %s", what, (r.stderr or "").strip()[:160])
        return None
    return r


class NoVolume:
    name = "none"

    def available(self) -> bool:
        return False

    def get(self) -> int | None:
        return None

    def set(self, level: int) -> bool:
        return False


class AmixerVolume:
    """An ALSA simple mixer control — the Pi's path, unchanged."""

    name = "amixer"

    def __init__(self, card: str, control: str | None, *, run: Runner | None = None) -> None:
        self.card, self.control, self._run = str(card), control, run

    def available(self) -> bool:
        return bool(self.control)

    def get(self) -> int | None:
        if not self.control:
            return None
        r = _run(self._run, _volume.get_command(self.card, self.control),
                 f"amixer get {self.control!r} on card {self.card}")
        return _volume.parse_volume_percent(r.stdout) if r else None

    def set(self, level: int) -> bool:
        if not self.control:
            return False
        return _run(self._run, _volume.set_command(self.card, self.control, level),
                    f"amixer set {self.control!r} on card {self.card}") is not None


class WpctlVolume:
    """PipeWire's default sink, through WirePlumber's ``wpctl``."""

    name = "wpctl"

    def __init__(self, *, run: Runner | None = None) -> None:
        self._run = run

    def available(self) -> bool:
        return True

    def get(self) -> int | None:
        r = _run(self._run, _volume.wpctl_get_command(), "wpctl get-volume")
        return _volume.parse_wpctl_volume(r.stdout) if r else None

    def set(self, level: int) -> bool:
        return all(_run(self._run, argv, "wpctl") is not None
                   for argv in _volume.wpctl_set_commands(level))


class PactlVolume:
    """PulseAudio's default sink (and PipeWire's, through pipewire-pulse)."""

    name = "pactl"

    def __init__(self, *, run: Runner | None = None) -> None:
        self._run = run

    def available(self) -> bool:
        return True

    def get(self) -> int | None:
        r = _run(self._run, _volume.pactl_get_command(), "pactl get-sink-volume")
        return _volume.parse_pactl_volume(r.stdout) if r else None

    def set(self, level: int) -> bool:
        return all(_run(self._run, argv, "pactl") is not None
                   for argv in _volume.pactl_set_commands(level))


class AutoVolume:
    """The first of wpctl, pactl, amixer that answers a read. Asked again
    until one has: at boot the user's sound server may not be up yet, and
    a laptop that settles on "no volume" for the rest of the process would
    ignore every "turn it up". Once one answers it is kept."""

    name = "auto"

    def __init__(self, card: str, control: str | None, *, run: Runner | None = None,
                 which: Callable[[str], str | None] = shutil.which) -> None:
        self._candidates: list[VolumeBackend] = []
        if which("wpctl"):
            self._candidates.append(WpctlVolume(run=run))
        if which("pactl"):
            self._candidates.append(PactlVolume(run=run))
        if control and which("amixer"):
            self._candidates.append(AmixerVolume(card, control, run=run))
        self._chosen: VolumeBackend | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(self._candidates)

    def chosen(self) -> VolumeBackend | None:
        return self._chosen

    def _pick(self) -> tuple[VolumeBackend | None, int | None]:
        with self._lock:
            if self._chosen is not None:
                return self._chosen, None
            for backend in self._candidates:
                level = backend.get()
                if level is not None:
                    self._chosen = backend
                    log.info("volume: using %s (now %d%%)", backend.name, level)
                    return backend, level
        return None, None

    def get(self) -> int | None:
        backend, level = self._pick()
        if backend is None:
            return None
        return level if level is not None else backend.get()

    def set(self, level: int) -> bool:
        backend, _ = self._pick()
        return backend.set(level) if backend is not None else False


def volume_backend(kind: str, *, card: str, control: str | None,
                   run: Runner | None = None,
                   which: Callable[[str], str | None] = shutil.which) -> VolumeBackend:
    if kind == "amixer":
        return AmixerVolume(card, control, run=run)
    if kind == "wpctl":
        return WpctlVolume(run=run)
    if kind == "pactl":
        return PactlVolume(run=run)
    if kind == "auto":
        return AutoVolume(card, control, run=run, which=which)
    return NoVolume()


# ─── service control ──────────────────────────────────────────────────────

class ServiceControl(Protocol):
    def restart(self) -> bool: ...
    def describe(self) -> str: ...


class SudoSystemctl:
    """The Pi: ``sudo -n /usr/bin/systemctl --no-block restart
    domovoi-satellite.service`` (PROVISIONING.md's self-restart sudoers
    line). ``--no-block`` is load-bearing: a synchronous restart issued
    from inside the unit shares its cgroup, so systemd can SIGTERM this
    process mid-job and leave the service stopped."""

    UNIT = "domovoi-satellite.service"

    def __init__(self, *, run: Runner | None = None) -> None:
        self._run = run

    def describe(self) -> str:
        return f"sudo systemctl restart {self.UNIT}"

    def restart(self) -> bool:
        try:
            result = _runner(self._run)(
                ["sudo", "-n", "/usr/bin/systemctl", "--no-block", "restart", self.UNIT],
                capture_output=True, text=True, timeout=10.0,
            )
        except FileNotFoundError as e:
            log.warning("self-restart: missing binary (%s)", e)
            return False
        except subprocess.TimeoutExpired:
            log.warning("self-restart: systemctl timed out after 10s")
            return False
        if result.returncode == 0:
            log.info("self-restart: restart queued — the service will bounce now")
            return True
        log.warning(
            "self-restart failed: rc=%d stdout=%r stderr=%r. Check the "
            "self-restart sudoers entry in PROVISIONING.md — `sudo -n "
            "/usr/bin/systemctl --no-block restart domovoi-satellite.service` "
            "must succeed without prompting for a password.",
            result.returncode, result.stdout.strip(), result.stderr.strip(),
        )
        return False


class ExitToRestart:
    """A laptop: exit with :data:`EXIT_RESTART` and let the unit start the
    engine again. Needs no sudo, no polkit and no D-Bus, so it works the
    same under ``systemd --user``, a system unit, or anything else that
    restarts a process on an exit code."""

    def __init__(self, request_exit: Callable[[int], None]) -> None:
        self._request_exit = request_exit

    def describe(self) -> str:
        return f"exit {EXIT_RESTART} (the unit starts it again)"

    def restart(self) -> bool:
        log.info("self-restart: exiting with %d so the service starts again", EXIT_RESTART)
        self._request_exit(EXIT_RESTART)
        return True


# ─── which set a config gets ──────────────────────────────────────────────

class Platform:
    """The seams for one engine."""

    def __init__(self, *, audio_out: AudioOut, volume: VolumeBackend,
                 service: ServiceControl, control_socket: bool) -> None:
        self.audio_out = audio_out
        self.volume = volume
        self.service = service
        self.control_socket = control_socket


PC_PROFILES = frozenset({"generic_pc"})


def is_pc(cfg: Any) -> bool:
    """Whether ``cfg`` is a spare computer rather than a Pi board."""
    return getattr(getattr(cfg, "device", None), "name", "") in PC_PROFILES


def for_profile(cfg: Any, *, request_exit: Callable[[int], None]) -> Platform:
    """The Pi profiles keep exactly what they had (amixer on the profile's
    mixer, sudo systemctl, no control socket). ``generic_pc`` gets the
    sound server's volume, exit-to-restart and the control socket."""
    pc = is_pc(cfg)
    kind = getattr(cfg, "volume_backend", "") or ("auto" if pc else "amixer")
    return Platform(
        audio_out=AlsaAudioOut(),
        volume=volume_backend(
            kind,
            card=str(getattr(cfg, "output_mixer_card", "0")),
            control=getattr(cfg, "output_mixer_control", None),
        ),
        service=ExitToRestart(request_exit) if pc else SudoSystemctl(),
        control_socket=bool(getattr(cfg, "control_enabled", pc)),
    )
