"""The satellite engine's platform seams: audio out, volume, service control.

Spare-device satellites (S1, 2026-10-13). The engine (``satellite/client.py``)
is the same program on a Pi, a Linux laptop and — next — Windows. What
differs between them sits behind four small interfaces, each with the Pi's
behaviour as one implementation, byte for byte what it was:

* :class:`AudioOut` — the child process that plays music, a greeting or
  the canned clip, and the chime. Pi and Linux PC: ``mpg123``/``aplay`` on
  an ALSA device (:class:`AlsaAudioOut`). The engine starts, watches and
  stops it as a process (its own process group); a platform without
  mpg123 supplies its own player program here — Windows:
  :class:`WindowsAudioOut`, ``python -m satellite.win_player``.
* :class:`VolumeBackend` — the one output gain voice and music ride
  through. Pi: an ALSA mixer control (:class:`AmixerVolume`). Laptop: the
  sound server's default sink, ``wpctl`` (PipeWire) or ``pactl``
  (PulseAudio), found by :class:`AutoVolume`. Windows: Core Audio's
  default speakers (:class:`WindowsVolume`).
* :class:`ServiceControl` — how the engine restarts itself after a config
  change or an upgrade. Pi: ``sudo -n systemctl --no-block restart``
  (:class:`SudoSystemctl`, the sudoers line in PROVISIONING.md). Laptop:
  no privilege at all — the engine exits with :data:`EXIT_RESTART` and its
  unit's ``RestartForceExitStatus=75`` starts it again
  (:class:`ExitToRestart`); on Windows the supervisor
  (satellite/win_service.py) does the same.
* The control channel's transport — :mod:`satellite.control`
  (``ControlTransport``; a Unix socket on Linux, a named pipe on Windows).

:func:`for_profile` picks the set for a config. Stdlib only.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from typing import Any, Callable, Protocol

from satellite import _volume, alsa_names

log = logging.getLogger("satellite.platform")

#: The engine asks for a restart by exiting with this (EX_TEMPFAIL). The
#: laptop units carry ``RestartForceExitStatus=75`` and
#: ``SuccessExitStatus=75``: started again, not counted as a failure.
EXIT_RESTART = 75
#: An enrolled laptop that finds no pinned server identity exits with this
#: (EX_CONFIG); its units carry ``RestartPreventExitStatus=78`` so it stays
#: down, saying why, instead of looping.
EXIT_UNPINNED = 78
#: The same code for a Windows satellite whose config.toml holds a value it
#: won't run on (a device name that is not "default", a number or a plain
#: name, say): its supervisor leaves it stopped and the tray says why,
#: rather than restarting it every minute into the same refusal.
EXIT_CONFIG = 78

#: ``[audio] volume_backend`` values ("windows": Core Audio, windows_pc's).
VOLUME_BACKENDS = ("amixer", "wpctl", "pactl", "auto", "windows", "none")

Runner = Callable[..., Any]


# ─── audio out ────────────────────────────────────────────────────────────

class AudioOut(Protocol):
    """The argv of the player child process for each kind of sound.
    ``device`` is the config's output device (``[music] alsa_device`` on
    Linux), passed per call so a config change needs no new object."""

    def stream_argv(self, device: str, url: str) -> list[str]: ...
    def mp3_argv(self, device: str, path: str, gain: float) -> list[str]: ...
    def wav_argv(self, device: str, path: str) -> list[str]: ...


def _pcm(device: str) -> str:
    """``device``, or ``default`` when it is not a plain device name: an
    ALSA name can open a pipe to a shell command (satellite/alsa_names.py).
    The config loader refuses such a name already; this is the last door."""
    if alsa_names.safe_pcm(device):
        return device
    log.error("refusing ALSA device %r (not a plain device name); playing on 'default'", device)
    return "default"


class AlsaAudioOut:
    """mpg123 and aplay on an ALSA device. On a desktop the ALSA ``default``
    device is the sound server's, which is where a laptop's speakers are.
    Every device name passes :func:`_pcm` first."""

    def stream_argv(self, device: str, url: str) -> list[str]:
        device = _pcm(device)
        # -b 1024 / --devbuffer 1.0: see Satellite._spawn_music.
        return ["mpg123", "-q", "-b", "1024", "--devbuffer", "1.0",
                "-o", "alsa", "-a", device, url]

    def mp3_argv(self, device: str, path: str, gain: float) -> list[str]:
        device = _pcm(device)
        return ["mpg123", "-q", *_volume.mpg123_scale_args(gain),
                "-o", "alsa", "-a", device, path]

    def wav_argv(self, device: str, path: str) -> list[str]:
        return ["aplay", "-q", "-D", _pcm(device), path]


class WindowsAudioOut:
    """Windows: ``python -m satellite.win_player`` as the player process
    (miniaudio's MP3 decoder and PortAudio; no mpg123, nothing LGPL). The
    device is ``[music] alsa_device``'s Windows meaning: ``default`` (the
    speakers Windows has chosen), a device number, or part of a device
    name. The wake acknowledgement plays inside the engine instead
    (:meth:`spawn_ack`): a new process is too slow for it."""

    def __init__(self, python: str | None = None) -> None:
        import sys

        self.python = python or sys.executable

    def _argv(self, *rest: str) -> list[str]:
        return [self.python, "-m", "satellite.win_player", *rest]

    @staticmethod
    def _device(device: str) -> str:
        """Only a name devices.valid_windows_device accepts reaches the
        player's command line; anything else plays on the default."""
        from satellite import devices

        if devices.valid_windows_device(device) and device is not None:
            return str(device)
        log.warning("not a Windows audio device name: %r; using the default", str(device)[:40])
        return "default"

    def stream_argv(self, device: str, url: str) -> list[str]:
        return self._argv("stream", url, "--device", self._device(device))

    def mp3_argv(self, device: str, path: str, gain: float) -> list[str]:
        args = self._argv("mp3", path, "--device", self._device(device))
        if gain != 1.0:
            args += ["--gain", f"{float(gain):g}"]
        return args

    def wav_argv(self, device: str, path: str) -> list[str]:
        return self._argv("wav", path, "--device", self._device(device))

    def spawn_ack(self, argv: list[str]) -> Any:
        """The acknowledgement for one of this class's argvs, played on a
        thread of the engine (satellite.win_player.InProcessPlay)."""
        from satellite import win_player

        rest = argv[3:]
        kind, path = rest[0], rest[1]
        opts = dict(zip(rest[2::2], rest[3::2]))
        return win_player.InProcessPlay(
            kind, path, gain=float(opts.get("--gain", 1.0)),
            device=win_player._device(opts.get("--device")),
        )


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


def _run(run: Runner | None, argv: list[str], what: str, *, quiet: bool = False) -> subprocess.CompletedProcess | None:
    level = logging.DEBUG if quiet else logging.WARNING
    try:
        r = _runner(run)(argv, capture_output=True, text=True, timeout=5.0)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log.log(level, "%s failed: %s", what, e)
        return None
    if r.returncode != 0:
        log.log(level, "%s failed: %s", what, (r.stderr or "").strip()[:160])
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

    def __init__(self, card: str, control: str | None, *, run: Runner | None = None,
                 quiet: bool = False) -> None:
        self.card, self.control, self._run = str(card), control, run
        self.quiet = quiet

    def available(self) -> bool:
        return bool(self.control) and alsa_names.safe_card(self.card) and \
            alsa_names.safe_control(self.control)

    def get(self) -> int | None:
        if not self.available():
            return None
        r = _run(self._run, _volume.get_command(self.card, self.control),
                 f"amixer get {self.control!r} on card {self.card}", quiet=self.quiet)
        return _volume.parse_volume_percent(r.stdout) if r else None

    def set(self, level: int) -> bool:
        if not self.available():
            return False
        return _run(self._run, _volume.set_command(self.card, self.control, level),
                    f"amixer set {self.control!r} on card {self.card}") is not None


class WpctlVolume:
    """PipeWire's default sink, through WirePlumber's ``wpctl``."""

    name = "wpctl"

    def __init__(self, *, run: Runner | None = None, quiet: bool = False) -> None:
        self._run = run
        self.quiet = quiet

    def available(self) -> bool:
        return True

    def get(self) -> int | None:
        r = _run(self._run, _volume.wpctl_get_command(), "wpctl get-volume", quiet=self.quiet)
        return _volume.parse_wpctl_volume(r.stdout) if r else None

    def set(self, level: int) -> bool:
        return all(_run(self._run, argv, "wpctl") is not None
                   for argv in _volume.wpctl_set_commands(level))


class PactlVolume:
    """PulseAudio's default sink (and PipeWire's, through pipewire-pulse)."""

    name = "pactl"

    def __init__(self, *, run: Runner | None = None, quiet: bool = False) -> None:
        self._run = run
        self.quiet = quiet

    def available(self) -> bool:
        return True

    def get(self) -> int | None:
        r = _run(self._run, _volume.pactl_get_command(), "pactl get-sink-volume", quiet=self.quiet)
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
        # Probed quietly: until one answers, a missing sound server is the
        # normal state of a box that is still booting, or of one with none.
        if which("wpctl"):
            self._candidates.append(WpctlVolume(run=run, quiet=True))
        if which("pactl"):
            self._candidates.append(PactlVolume(run=run, quiet=True))
        if control and which("amixer"):
            self._candidates.append(AmixerVolume(card, control, run=run, quiet=True))
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
                    backend.quiet = False
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


class WindowsVolume:
    """Windows: the default speakers' master volume through Core Audio
    (``IAudioEndpointVolume``, satellite/win32.py) — the taskbar slider.
    Setting it also unmutes, as amixer's ``unmute`` does on a Pi."""

    name = "windows"

    def __init__(self, endpoint: Any = None) -> None:
        self._endpoint = endpoint

    def _ep(self) -> Any:
        if self._endpoint is None:
            from satellite import win32

            self._endpoint = win32.EndpointVolume()
        return self._endpoint

    def available(self) -> bool:
        import sys

        return self._endpoint is not None or sys.platform == "win32"

    def get(self) -> int | None:
        try:
            return _volume.clamp(round(self._ep().get_scalar() * 100))
        except OSError as e:
            log.warning("Windows volume read failed: %s", e)
            return None

    def set(self, level: int) -> bool:
        try:
            self._ep().set_scalar(_volume.clamp(level) / 100.0, unmute=True)
        except OSError as e:
            log.warning("Windows volume change failed: %s", e)
            return False
        return True


def volume_backend(kind: str, *, card: str, control: str | None,
                   run: Runner | None = None,
                   which: Callable[[str], str | None] = shutil.which) -> VolumeBackend:
    if kind == "windows":
        return WindowsVolume()
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
        return f"exit {EXIT_RESTART} (its unit or supervisor starts it again)"

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


#: Microsoft's C++ runtime, which onnxruntime's Windows wheel (the wake word)
#: needs and does not carry: the embeddable Python brings vcruntime140*.dll,
#: Windows itself brings neither of these. 14.44 is the toolset onnxruntime
#: 1.31 is built with, and Microsoft's rule is a runtime at least as new as
#: the toolset. The installer ships both beside the satellite's python.exe
#: (windows-runtime.json ``vc_runtime``); this check is the fallback for an
#: install whose copies have gone (a test holds the names and the minimum
#: equal to the lock's).
VC_RUNTIME_DLLS = ("msvcp140.dll", "msvcp140_1.dll")
VC_RUNTIME_MIN = (14, 44)
VC_REDIST_URL = "https://aka.ms/vc14/vc_redist.x64.exe"


def windows_runtime_problem(python_dir: Any, system_dir: Any,
                            version_of: Callable[[str], Any] | None = None,
                            minimum: tuple[int, int] = VC_RUNTIME_MIN) -> str | None:
    """Why the wake word can't load on this PC, in words a person can act
    on, or None. Each DLL is the one Windows would load: beside the
    satellite's Python first (an app-local copy), else in System32."""
    import os

    if version_of is None:
        from satellite import win32

        version_of = win32.file_version
    missing, old = [], []
    for name in VC_RUNTIME_DLLS:
        found = next((os.path.join(str(d), name) for d in (python_dir, system_dir)
                      if os.path.isfile(os.path.join(str(d), name))), None)
        if found is None:
            missing.append(name)
            continue
        version = version_of(found)
        if not version or tuple(version[:2]) < tuple(minimum):
            have = ".".join(str(p) for p in version[:2]) if version else "unknown"
            old.append(f"{name} {have}")
    if not missing and not old:
        return None
    want = ".".join(str(p) for p in minimum)
    what = (f"Microsoft's Visual C++ runtime is not on this PC ({', '.join(missing)} missing)" if missing
            else f"Microsoft's Visual C++ runtime on this PC is older than {want} ({', '.join(old)})")
    return (f"{what}: the copies the room software installs beside its Python are gone, and the wake word "
            "can't load without them. Paste the install command from the dashboard again (it puts them "
            f"back), or install \"Microsoft Visual C++ Redistributable (x64)\" from {VC_REDIST_URL} (it "
            "asks for an administrator once), then start the room again.")


PC_PROFILES = frozenset({"generic_pc", "windows_pc"})
#: The spare-computer profile for Windows (satellite/devices.py).
WINDOWS_PROFILE = "windows_pc"


def is_pc(cfg: Any) -> bool:
    """Whether ``cfg`` is a spare computer rather than a Pi board."""
    return getattr(getattr(cfg, "device", None), "name", "") in PC_PROFILES


def is_windows_pc(cfg: Any) -> bool:
    return getattr(getattr(cfg, "device", None), "name", "") == WINDOWS_PROFILE


def for_profile(cfg: Any, *, request_exit: Callable[[int], None]) -> Platform:
    """The Pi profiles keep exactly what they had (amixer on the profile's
    mixer, sudo systemctl, no control socket). ``generic_pc`` gets the
    sound server's volume, exit-to-restart and the control socket;
    ``windows_pc`` the Windows player and Core Audio volume, and the same
    exit-to-restart (its supervisor, satellite/win_service.py, starts the
    engine again) and control channel (a named pipe)."""
    pc = is_pc(cfg)
    if is_windows_pc(cfg):
        kind = getattr(cfg, "volume_backend", "") or "windows"
        return Platform(
            audio_out=WindowsAudioOut(),
            volume=volume_backend(kind, card="0", control=None),
            service=ExitToRestart(request_exit),
            control_socket=_control_socket(cfg, True),
        )
    kind = getattr(cfg, "volume_backend", "") or ("auto" if pc else "amixer")
    return Platform(
        audio_out=AlsaAudioOut(),
        volume=volume_backend(
            kind,
            card=str(getattr(cfg, "output_mixer_card", "0")),
            control=getattr(cfg, "output_mixer_control", None),
        ),
        service=ExitToRestart(request_exit) if pc else SudoSystemctl(),
        control_socket=_control_socket(cfg, pc),
    )


def _control_socket(cfg: Any, pc: bool) -> bool:
    """``[control] enabled`` when the config says; else the profile's (on
    for a spare computer, off on a Pi)."""
    configured = getattr(cfg, "control_enabled", None)
    return pc if configured is None else bool(configured)
