"""What the engine does differently on a spare computer (``generic_pc``).

Spare-device satellites (S1, 2026-10-13). A Linux laptop or mini PC runs
the same client as a Pi; this module holds what only such a device adds,
so ``client.py`` gains hooks rather than a second program:

* **The hello says what it is.** ``board: "linux_pc"`` and a ``supports``
  list naming only what a laptop can do (:func:`hello_fields`): no Wi-Fi,
  LEDs, mic gain, root plugin payloads or time sync — there are no root
  helpers on a laptop — so the core and the dashboard never offer them.
* **Pinned, always.** An enrolled laptop refuses to run without a pinned
  server identity (:func:`require_pin`): it never falls back to trusting
  the first server that answers.
* **The approval code on screen, never in a log**
  (:func:`show_code_on_tty`, the control channel's ``code`` verb, the
  tray), instead of a Pi's root helper speaking it.
* **Mute.** Closes the microphone (the capture stream stops, so the
  desktop's own mic indicator goes out too) and keeps it closed across
  restarts until unmuted (:data:`MUTED_SIDECAR`).
* **The "ask me" permission.** With ``[pc] permissions = "ask"`` an update,
  a restart, a settings push or a wake-word push from the core waits for
  the person's OK in the tray (or ``--approve``) instead of running on
  arrival (:meth:`SpareDeviceEngine._held`). "automatic" runs them at once,
  like a Pi. Either way nothing runs as root.
* **The control channel** (:mod:`satellite.control`) and its CLI
  (:func:`cli`): ``--status``, ``--approval-code``, ``--mute``,
  ``--unmute``, ``--pending``, ``--approve``, ``--decline``.
* **Windows** (``windows_pc``, 2026-10-13): the same, with board
  ``windows_pc``, the control channel on a named pipe, the log in a file
  the supervisor keeps (satellite/win_service.py), a stop that the tray or
  the uninstaller asks for through a named event, and no subnet sweep when
  the core moves (:func:`server_search`).

A Pi (any other profile) never reaches any of it: every hook in the client
asks :func:`satellite.engine_platform.is_pc` first.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from satellite import control, engine_platform

log = logging.getLogger("satellite.pc")

CONFIG_DIR = Path.home() / ".domovoi"
#: Present while the person has muted this device; read at every start.
MUTED_SIDECAR = CONFIG_DIR / "muted"
#: The web dashboard's port on the core's host ("Open dashboard").
DASHBOARD_PORT = 6369
BOARD = "linux_pc"
#: How a spare computer looks for its core when the enrolled address stops
#: answering (``[pc] server_search``): "sweep" — its name, the address, then
#: the /24 sweep (a Pi's order, and generic_pc's); "names" — its name and the
#: address, no sweep (windows_pc's default: a Windows laptop probing every
#: address on the home network looks like a port scan to its security
#: software, and the enrolment gave the exact address); "address" — only
#: the address.
SERVER_SEARCH = ("sweep", "names", "address")

#: What a laptop can do, as the hello's ``supports`` names it. Restart and
#: code sync are there in both permissions: with "ask me" they are held for
#: the person's OK, not refused. Not listed, so never sent or offered:
#: plugin_payloads (they run as root on a Pi), wifi, mic_gain, leds,
#: display, time_sync (a laptop keeps its own clock), chat_mode and
#: full_duplex (unless [device] full_duplex says a speakerphone cancels
#: its own echo).
PC_SUPPORTS: tuple[str, ...] = (
    "restart", "code_sync", "config", "logs", "wake_clips", "wake_models",
    "sounds", "voice",
)
#: Settings the core may never change on a laptop, whatever it sends: the
#: identity it is pinned to, the permission the person chose, and the
#: [device] section — the profile (which decides whether it restarts itself
#: with sudo) and full_duplex (whether a call may open the mic while the
#: speaker plays) are the owner's to say.
PROTECTED_KEYS = frozenset({"satellite.server_fingerprint"})
PROTECTED_SECTIONS = ("pc.", "device.")
#: The ``error`` reasons that mean the core turned this device away.
REFUSALS = ("pairing_rejected", "approval_conflict", "approval_rejected", "room_taken")


def is_pc(cfg: Any) -> bool:
    return engine_platform.is_pc(cfg)


def board_for(cfg: Any) -> str:
    """The hello's ``board``: the profile's (``linux_pc``, ``windows_pc``)."""
    return getattr(getattr(cfg, "device", None), "board", "") or BOARD


def server_search(cfg: Any) -> str:
    """``[pc] server_search``, or the profile's default (see
    :data:`SERVER_SEARCH`). A Pi always sweeps."""
    if not is_pc(cfg):
        return "sweep"
    if os.environ.get("DOMOVOI_DEBUG_SERVER_SEARCH") == "address":
        # A test hook that can only narrow the search: an end-to-end run
        # against a throwaway core, where the house's own server answers to
        # domovoi.local and must never be asked. On Windows it reaches the
        # engine only through its supervisor's --test-audio-hooks.
        log.warning("TEST HOOK: looking for the server at its address only (DOMOVOI_DEBUG_SERVER_SEARCH)")
        return "address"
    chosen = getattr(cfg, "server_search", "") or ""
    if chosen in SERVER_SEARCH:
        return chosen
    return "names" if engine_platform.is_windows_pc(cfg) else "sweep"


def full_duplex(cfg: Any) -> bool:
    """Echo cancellation: the profile's, or ``[device] full_duplex`` on a
    laptop with a USB speakerphone that cancels its own echo."""
    override = getattr(cfg, "full_duplex_override", None)
    if is_pc(cfg) and isinstance(override, bool):
        return override
    return bool(getattr(getattr(cfg, "device", None), "supports_full_duplex", False))


def hello_fields(cfg: Any) -> dict[str, Any]:
    """What a laptop's hello adds to the Pi's: its board and its list."""
    supports = list(PC_SUPPORTS)
    fd = full_duplex(cfg)
    if fd:
        supports.append("chat_mode")
    return {"board": board_for(cfg), "supports": supports, "supports_full_duplex": fd}


def filter_config_changes(changes: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """``(kept, refused)`` for a spare computer: satellite/remote_config.py's
    screen, which holds every board to the dashboard's own settings and a
    laptop also off the sections it never declared. The protected keys
    above are never among what it keeps."""
    from satellite import remote_config

    kept, refused = remote_config.screen(changes, pc=True)
    for key in [k for k in kept if k in PROTECTED_KEYS or k.startswith(PROTECTED_SECTIONS)]:
        del kept[key]          # never reached: the screen keeps none of these
        refused.append(repr(key)[:80])
    return kept, refused


def require_pin(cfg: Any) -> tuple[bool, str]:
    """Whether this laptop may run: it must hold a pinned server identity
    (``[satellite] server_fingerprint``, written by the installer from the
    enrolment payload). ``(ok, where it came from or why not)``."""
    from satellite import server_identity

    fp, source = server_identity.pinned_fingerprint(getattr(cfg, "server_fingerprint", ""))
    if fp:
        return True, f"{fp} ({source})"
    return False, (
        "this room device has no pinned Domovoi server identity, and a spare "
        "computer never trusts the first server that answers. Enrol it again "
        "from the dashboard (Settings → Rooms → Add a spare device), or "
        "set [satellite] server_fingerprint to the SHA256:… shown under "
        "Settings → About."
    )


#: Files only a Domovoi *server* keeps in ~/.domovoi: its private key, its
#: household device token, its setup code.
CORE_MARKERS = ("server-identity.json", "device-token.txt", "setup-code.txt")


def core_account(config_dir: Path) -> str | None:
    """Why ``config_dir`` belongs to a Domovoi server, or None. A spare
    computer's satellite must not share one: the two keep different things
    under the same names (``sounds/``, ``wake_models/``), and the server's
    private key sits there (security review of wf/sat-pc, S2)."""
    for name in CORE_MARKERS:
        if (Path(config_dir) / name).exists():
            return (f"{Path(config_dir) / name} belongs to a Domovoi server: install the "
                    "room under another account (a server and a room device can't share ~/.domovoi)")
    return None


def read_muted() -> bool:
    return MUTED_SIDECAR.exists()


def write_muted(muted: bool) -> None:
    try:
        if muted:
            MUTED_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
            MUTED_SIDECAR.write_text("muted\n", encoding="utf-8")
        else:
            MUTED_SIDECAR.unlink(missing_ok=True)
    except OSError as e:
        log.warning("could not record the mute (%s); it lasts until this process ends", e)


def show_code_on_tty(code: str | None, room: str, *, stream=None) -> bool:
    """Put the approval code on the terminal this engine runs in — only a
    real terminal: under systemd stderr is the journal, and the code never
    goes to a log. Returns whether it was shown."""
    out = stream if stream is not None else sys.stderr
    try:
        if not code or not out.isatty():
            return False
        out.write(
            f"\n  Waiting for approval: code {code}\n"
            f"  Type it in the dashboard, Settings → Rooms, beside \"{room}\".\n\n"
        )
        out.flush()
        return True
    except (OSError, ValueError, AttributeError):
        return False


def home_network_url(url: str) -> bool:
    """Whether a ws/http URL's host is on the home network
    (satellite/enrolment.py's rule, the one the enrolment code obeys)."""
    import urllib.parse

    from satellite import enrolment

    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return bool(host) and enrolment.is_home_network_host(host)


def dashboard_url(ws_url: str) -> str | None:
    """The dashboard beside the core this device is pinned to."""
    import urllib.parse

    try:
        parts = urllib.parse.urlsplit(ws_url)
    except ValueError:
        return None
    host = parts.hostname
    if not host or parts.scheme not in ("ws", "wss", "http", "https"):
        return None
    if ":" in host:
        host = f"[{host}]"
    scheme = "https" if parts.scheme in ("wss", "https") else "http"
    return f"{scheme}://{host}:{DASHBOARD_PORT}"


class SpareDeviceEngine:
    """The engine's spare-computer half, mixed into ``Satellite``.

    Every attribute is read with a default, because the tests build a
    Satellite without ``__init__``; on a Pi none of it is ever reached."""

    # ── state ──────────────────────────────────────────────────────────

    def _pc_init(self) -> None:
        self._muted = is_pc(self.cfg) and read_muted()
        self._conn_state = "connecting"
        self._conn_reason: str | None = None
        self._approval_code: str | None = None
        self._pending = control.PendingActions(on_change=self._control_notify)
        self._control_server: control.ControlServer | None = None
        self._battery_cache: tuple[float, dict[str, Any]] | None = None
        leds = getattr(self, "_leds", None)
        if is_pc(self.cfg) and hasattr(leds, "add_listener"):
            leds.add_listener(self._control_notify)

    def _control_notify(self) -> None:
        server = getattr(self, "_control_server", None)
        if server is not None:
            server.notify()

    def _set_conn(self, state: str, reason: str | None = None) -> None:
        if not is_pc(self.cfg):
            return
        reason = reason[:200] if reason else None
        current = getattr(self, "_conn_state", None)
        # Parked devices redial every few seconds; each attempt is not news.
        # The person is reading the code off the tray: it stays amber (and
        # the code stays shown) until the core accepts, refuses, or can't
        # be reached.
        if state == "connecting" and current == "awaiting_approval":
            return
        if (current, getattr(self, "_conn_reason", None)) == (state, reason):
            return
        self._conn_state, self._conn_reason = state, reason
        self._control_notify()

    def _pending_actions(self) -> control.PendingActions:
        pending = getattr(self, "_pending", None)
        if pending is None:
            pending = self._pending = control.PendingActions(on_change=self._control_notify)
        return pending

    # ── "ask me" ───────────────────────────────────────────────────────

    def _held(self, kind: str, summary: str, run: Callable[[], None], *,
              details: list[str] | tuple[str, ...] = ()) -> bool:
        """With the "ask me" permission, keep ``run`` for the person's OK
        and return True (the caller does nothing more). Otherwise False:
        the caller runs it now, as a Pi would. Something that can't be
        shown whole is refused (and True: nothing runs)."""
        if not is_pc(self.cfg) or getattr(self.cfg, "permissions", "automatic") != "ask":
            return False
        try:
            self._pending_actions().offer(kind, summary, run, details=details)
        except ValueError as e:
            log.warning("ask me: refusing a %s the person could not be shown in full (%s)", kind, e)
        return True

    # ── mute ───────────────────────────────────────────────────────────

    def _set_muted(self, muted: bool) -> None:
        """Close or reopen the microphone. Muting stops the capture stream
        (nothing is read from the mic, and the desktop's own mic indicator
        goes out); the mic thread idles on an empty queue, as it does
        behind the approval gate. Kept across restarts."""
        with self._voice_input_lock:
            already = bool(getattr(self, "_muted", False))
            self._muted = muted
            if muted and not already:
                # A capture in progress ends with what it has; a call or a
                # chat goes on without this side's audio.
                end = getattr(self, "_end_capture", None)
                if end is not None:
                    end.set()
                self._stop_mic()
            elif not muted and already:
                if (self._voice_input_started and not self._voice_gate_closed
                        and self._input_stream is None):
                    try:
                        self._start_mic()
                    except Exception as e:  # noqa: BLE001
                        log.error("unmute: the microphone would not open: %s", e)
        write_muted(muted)
        if muted != already:
            log.info("microphone %s from this device", "muted" if muted else "unmuted")
        self._control_notify()

    # ── the control channel (satellite/control.py EngineApi) ──────────

    def _mic_state(self) -> str:
        if getattr(self, "_muted", False):
            return "muted"
        if not getattr(self.cfg, "mic_enabled", True) or not getattr(self, "_voice_input_started", False):
            return "off"
        if getattr(self, "_voice_gate_closed", False) or getattr(self, "_input_stream", None) is None:
            return "off"
        led = getattr(getattr(self, "_leds", None), "state", "idle")
        busy = (
            led in ("listening", "dropin")
            or getattr(getattr(self, "wake_recording", None), "is_set", lambda: False)()
            or getattr(getattr(self, "chat_active", None), "is_set", lambda: False)()
            or getattr(getattr(self, "dropin_active", None), "is_set", lambda: False)()
        )
        return "capturing" if busy else "listening"

    def _output_state(self) -> str:
        led = getattr(getattr(self, "_leds", None), "state", "idle")
        playing = getattr(getattr(self, "playback_active", None), "is_set", lambda: False)()
        if led == "speaking" or playing:
            return "speaking"
        proc = getattr(self, "_music_proc", None)
        if led == "music" or (proc is not None and proc.poll() is None):
            return "music"
        return "idle"

    def _battery_status(self) -> dict[str, Any] | None:
        from satellite import health

        now = time.monotonic()
        cache = getattr(self, "_battery_cache", None)
        if cache is None or now - cache[0] > 60.0:
            cache = (now, health.read_battery_here())
            self._battery_cache = cache
        b = cache[1].get("battery")
        return dict(b) if isinstance(b, dict) else None

    def control_status(self) -> dict[str, Any]:
        from satellite import client, sound_sync  # noqa: F401  (client: constants)

        fp, source = client._pinned_fingerprint(self.cfg)
        url = getattr(self.cfg, "domovoi_url", "") or ""
        try:
            host = sound_sync.http_base_from_ws(url).split("://", 1)[-1].rsplit(":", 1)[0].strip("[]")
        except Exception:  # noqa: BLE001
            host = None
        return {
            "protocol": control.PROTOCOL,
            "board": board_for(self.cfg),
            "room": {"id": self.cfg.room_id, "name": getattr(self.cfg, "room_name", "") or self.cfg.room_id},
            "server": {"url": url, "host": host or None, "fingerprint": fp, "pinned": bool(fp)},
            "dashboard": dashboard_url(url),
            "connection": getattr(self, "_conn_state", "connecting"),
            "reason": getattr(self, "_conn_reason", None),
            "mic": self._mic_state(),
            "output": self._output_state(),
            "volume": getattr(self, "_last_volume", None),
            "permissions": getattr(self.cfg, "permissions", "automatic"),
            "pending": self._pending_actions().list(),
            "battery": self._battery_status(),
            "version": client._read_synced_sha(),
        }

    def control_set_muted(self, muted: bool) -> None:
        self._set_muted(muted)

    def control_volume_available(self) -> bool:
        return self._platform.volume.available()

    def control_volume(self, level: int | None) -> int | None:
        if level is None:
            got = self._read_output_volume()
            if got is not None:
                self._last_volume = got
        else:
            # Sets, re-reads and reports it (_emit_volume_status keeps
            # _last_volume and tells the core, as a dashboard change does).
            self._apply_output_volume(level)
            got = getattr(self, "_last_volume", None)
        self._control_notify()
        return got

    def control_approval_code(self) -> tuple[bool, str | None]:
        from satellite import client

        waiting = bool(getattr(self, "_approval_pending", False)) or (
            getattr(self, "_conn_state", None) == "awaiting_approval")
        code = getattr(self, "_approval_code", None) or client._effective_approval_code()
        return waiting, code

    def control_logs_path(self) -> dict[str, Any]:
        scope = os.environ.get("DOMOVOI_SERVICE_SCOPE", "")
        if scope == "windows":
            folder = os.environ.get("DOMOVOI_LOG_DIR") or str(CONFIG_DIR)
            return {"kind": "file", "path": folder,
                    "hint": f"satellite.log in {folder} (the newest lines at the end)"}
        if scope == "user":
            hint = "journalctl --user -u domovoi-satellite -e"
        elif scope == "system":
            hint = "journalctl -u domovoi-satellite -e"
        else:
            hint = "the engine runs in a terminal: its log is that terminal"
        return {"kind": "journal" if scope else "terminal", "path": str(CONFIG_DIR), "hint": hint}

    def control_identify(self) -> bool:
        from satellite import chime, client

        try:
            path = chime.ensure(client.SOUNDS_CACHE_DIR / client.CHIME_FILE, self.cfg.tts_playback_gain)
            argv = self._platform.audio_out.wav_argv(self.cfg.music_alsa_device, str(path))
            import subprocess

            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except (OSError, ValueError) as e:
            log.warning("identify: could not play the chime: %s", e)
            return False
        log.info("identify: played the chime for room %s", self.cfg.room_id)
        return True

    def control_pending(self) -> control.PendingActions:
        return self._pending_actions()

    def _watch_windows_stop(self) -> None:
        """Windows: a stop asked for by the tray ("Stop satellite") or the
        uninstaller arrives as a named event (satellite/win32.py
        STOP_EVENT), the counterpart of systemd's SIGTERM. Watched on a
        daemon thread; setting it shuts the engine down as a signal
        would."""
        if not engine_platform.is_windows_pc(self.cfg) or sys.platform != "win32":
            return
        import threading

        from satellite import win32

        try:
            event = win32.NamedEvent(win32.object_name(win32.STOP_EVENT))
        except OSError as e:
            log.warning("the stop event could not be opened (%s); stop the satellite from the tray "
                        "or Task Manager", e)
            return

        def wait() -> None:
            while not self.shutdown_event.is_set():
                if event.wait(1.0):
                    log.info("stop asked for (the tray or the uninstaller)")
                    self._request_exit(0)
                    return

        threading.Thread(target=wait, name="windows-stop", daemon=True).start()

    async def _start_control(self) -> None:
        self._watch_windows_stop()
        if not self._platform.control_socket:
            return
        from satellite import client

        server = control.ControlServer(self, control.default_transport(client.CONFIG_DIR))
        try:
            await server.start()
        except (OSError, NotImplementedError) as e:
            if engine_platform.is_windows_pc(getattr(self, "cfg", None)):
                # Someone holds the pipe's name (another account, or a
                # low-integrity process of this one): a satellite listening
                # with a tray that can't see or mute it, or one fed by the
                # squatter, is worse than one that stops and says why. Exit
                # 78 before the microphone opens; the supervisor keeps this
                # line as the reason the tray shows.
                from satellite import not_started

                log.error("control channel not started (%s); not running without it", e)
                why = ("another process holds this room's control pipe, so its tray could not see "
                       "or mute it. Sign out and in again (or restart the PC), then choose Start the room.")
                print(f"domovoi-satellite: not starting: {why}", file=sys.stderr, flush=True)
                not_started.note(why)
                self._request_exit(engine_platform.EXIT_CONFIG)
                return
            log.warning("control channel not started (%s): the tray and the CLI can't reach this engine", e)
            return
        self._control_server = server

    async def _stop_control(self) -> None:
        server = getattr(self, "_control_server", None)
        self._control_server = None
        if server is not None:
            await server.close()


# ─── the command line (no engine started) ─────────────────────────────────

CLI_FLAGS = ("status", "approval_code", "mute", "unmute", "pending", "approve", "decline")


def add_cli_arguments(parser) -> None:
    group = parser.add_argument_group("talk to the running room (spare computers)")
    group.add_argument("--status", action="store_true", help="Print the running room's status and exit.")
    group.add_argument("--json", action="store_true", help="With --status: print it as JSON.")
    group.add_argument("--approval-code", action="store_true",
                       help="Print the approval code while it waits for approval.")
    group.add_argument("--mute", action="store_true", help="Close the microphone until --unmute.")
    group.add_argument("--unmute", action="store_true", help="Open the microphone again.")
    group.add_argument("--pending", action="store_true",
                       help="List updates and restarts waiting for your OK (\"ask me\").")
    group.add_argument("--approve", metavar="ID", help="Run a waiting action (its id, or all).")
    group.add_argument("--decline", metavar="ID", help="Drop a waiting action (its id, or all).")
    group.add_argument("--control-socket", default=None,
                       help="The engine's control socket, or on Windows its pipe "
                            f"(default: {CONFIG_DIR / control.SOCKET_NAME}, or "
                            "\\\\.\\pipe\\domovoi-satellite-<your SID>).")


def wants_cli(args: Any) -> bool:
    return any(getattr(args, flag, None) for flag in CLI_FLAGS)


def _status_text(st: dict[str, Any]) -> str:
    room = st.get("room") or {}
    server = st.get("server") or {}
    lines = [
        f"{room.get('name') or room.get('id')} ({room.get('id')}) · {st.get('connection')}"
        + (f": {st['reason']}" if st.get("reason") else ""),
        f"server   {server.get('url')}  pinned {server.get('fingerprint') or 'NOTHING'}",
        f"mic      {st.get('mic')}   speaker {st.get('output')}   volume "
        + ("—" if st.get("volume") is None else f"{st['volume']}%"),
        f"updates  {st.get('permissions')}",
    ]
    battery = st.get("battery")
    if battery:
        lines.append(f"battery  {battery.get('percent')}%"
                     + (" charging" if battery.get("charging")
                        else " on the charger" if battery.get("on_power") else " on battery"))
    for p in st.get("pending") or []:
        lines.append(f"waiting  [{p['id']}] {p['kind']}: {p['summary']}")
    if st.get("dashboard"):
        lines.append(f"dashboard {st['dashboard']}")
    return "\n".join(lines)


def _printable_streams() -> None:
    """A Windows console or pipe that can't show a character (the arrows
    and dots in a status) shows "?" instead of failing the command."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def cli(args: Any) -> int:
    """Answer one CLI flag through the control channel. Exit codes: 0 done,
    1 the engine refused, 2 nothing to say (no code: not waiting), 3 the
    engine isn't running."""
    _printable_streams()
    path = getattr(args, "control_socket", None) or control.default_address(CONFIG_DIR)
    client_ = control.ControlClient(path)
    try:
        if args.status:
            st = client_.call("status")
            print(json.dumps(st, indent=2) if getattr(args, "json", False) else _status_text(st))
            return 0 if st.get("ok") else 1
        if args.approval_code:
            got = client_.call("code")
            if got.get("waiting") and got.get("code"):
                print(got["code"])
                return 0
            print("not waiting for approval" if got.get("ok") else got.get("message"), file=sys.stderr)
            return 2
        if args.mute or args.unmute:
            got = client_.call("mute" if args.mute else "unmute")
            print(f"microphone: {got.get('mic')}" if got.get("ok") else got.get("message"))
            return 0 if got.get("ok") else 1
        if args.pending:
            st = client_.call("status")
            pending = st.get("pending") or []
            for p in pending:
                print(f"{p['id']}  {p['kind']}: {p['summary']}")
            if not pending:
                print("nothing is waiting for your OK")
            return 0
        verb, action = ("approve", args.approve) if args.approve else ("decline", args.decline)
        got = client_.call(verb, action=action)
        if not got.get("ok"):
            print(got.get("message"), file=sys.stderr)
            return 1
        for d in got.get("done") or []:
            print(f"{verb}d: {d['kind']}: {d['summary']}")
        return 0
    except ConnectionError as e:
        from satellite import not_started

        why = not_started.read()
        if why:
            print(f"The room is not running: {why}", file=sys.stderr)
        else:
            hint = ("start it from the Start menu: Domovoi Satellite" if sys.platform == "win32"
                    else "systemctl --user status domovoi-satellite")
            print(f"{e}. Is the room running? ({hint})", file=sys.stderr)
        return 3


def wait_for_approval(path: Path | str, *, timeout: float, out=None, poll: float = 1.0,
                      clock: Callable[[], float] = time.monotonic,
                      sleep: Callable[[float], None] = time.sleep, show_code: bool = True,
                      image_dir: str | None = None) -> str:
    """The installer's last step: watch the engine until it is connected,
    printing the approval code while it waits — only when ``out`` is a
    terminal: an installer piped into a file or a CI log says where to find
    the code instead (the code never goes to a log). ``show_code`` False
    says the same even on a terminal (the installer passes it when its own
    output is not one: sudo can hand the waiter a pty of its own). Returns
    the final connection state, or "timeout" / "no_engine"."""
    if out is None:
        _printable_streams()
        out = sys.stdout
    try:
        on_terminal = bool(show_code) and bool(out.isatty())
    except (OSError, ValueError, AttributeError):
        on_terminal = False
    # image_dir (Windows): the folder the engine's Python runs from, when
    # this waiter runs another one (the installer's staged Python).
    client_ = control.ControlClient(path, timeout=5.0, **({"image_dir": image_dir} if image_dir else {}))
    deadline = clock() + timeout
    shown: str | None = None
    last_state = None
    while clock() < deadline:
        try:
            st = client_.call("status")
        except ConnectionError:
            sleep(poll)
            continue
        state = st.get("connection")
        if state == "connected":
            out.write(f"Approved: this device is now the {st['room'].get('name') or st['room'].get('id')}.\n")
            out.flush()
            return state
        if state == "awaiting_approval" and not on_terminal:
            if last_state != state:
                out.write(
                    "\nWaiting for approval. The code is not printed here (this is not a\n"
                    "terminal); it is in the tray, or run: python -m satellite.client --approval-code\n"
                )
                out.flush()
        elif state == "awaiting_approval":
            try:
                code = client_.call("code").get("code")
            except ConnectionError:
                code = None
            if code and code != shown:
                out.write(
                    f"\nWaiting for approval: code {code}\n"
                    "Type it in the dashboard: Settings → Rooms.\n"
                )
                out.flush()
                shown = code
        elif state in ("unpinned", "refused") and state != last_state:
            out.write(f"The core says: {state}" + (f" ({st.get('reason')})" if st.get("reason") else "") + "\n")
            out.flush()
        last_state = state
        sleep(poll)
    return "timeout" if last_state is not None else "no_engine"


# Re-exported for the client's hooks.
__all__ = [
    "BOARD", "CONFIG_DIR", "MUTED_SIDECAR", "PC_SUPPORTS", "REFUSALS", "SERVER_SEARCH",
    "SpareDeviceEngine", "add_cli_arguments", "board_for", "cli", "dashboard_url",
    "filter_config_changes", "full_duplex", "hello_fields", "is_pc",
    "read_muted", "require_pin", "server_search", "show_code_on_tty", "wait_for_approval",
    "wants_cli", "write_muted",
]
